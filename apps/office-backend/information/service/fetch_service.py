"""按资讯组类型拉取今日资讯。去重标题，关键字过滤。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..repository import adapter_catalog as catalog
from ..repository import announcement_repository as announce
from ..collectors import preset_news_service as preset
from ..repository import rss_repository as rss_repo
from ..collectors import weread_session_service as weread

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TICKER = re.compile(r"^[A-Za-z0-9]{1,10}\.[A-Za-z]{2,4}$")
_TICKER_ONLY_ADAPTERS = {"sina", "xueqiu", "tencent"}
_BULL = ("回购", "增持", "中标", "增长", "超预期", "分红", "涨停", "签约", "获批", "利好")
_BEAR = ("减持", "亏损", "下调", "处罚", "立案", "暴跌", "商誉减值", "退市", "问询", "利空")


def today() -> date:
    return datetime.now(_SHANGHAI).date()


def split_keywords(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，;；\s]+", raw or "") if part.strip()]


def match_keywords(title: str, summary: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = f"{title} {summary}".lower()
    return any(key.lower() in text for key in keywords)


def pick_rss_rows(rows: list[dict], keywords: list[str], keep: int = 8) -> list[dict]:
    matched: list[dict] = []
    rest: list[dict] = []
    for row in rows or []:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        if match_keywords(title, str(row.get("summary") or ""), keywords):
            matched.append(row)
        else:
            rest.append(row)
    return (matched or rest)[:keep]


def lookup_securities(query: str) -> list[dict]:
    try:
        from ..collectors import security_search_service as securities
        return list(securities.search_securities(query, limit=3) or [])
    except Exception:  # noqa: BLE001
        return []


def _code_matches(query: str, ticker: str) -> bool:
    q = query.strip().upper()
    code, _, _market = ticker.strip().upper().partition(".")
    if not q or not code:
        return False
    if ticker.strip().upper() == q or ticker.strip().upper().startswith(q + "."):
        return True
    if q.isdigit() and code.isdigit() and len(q) >= 3:
        return q.lstrip("0") == code.lstrip("0") or q.zfill(len(code)) == code
    return q == code


def resolve_security(query: str) -> tuple[str, str] | None:
    text = (query or "").strip()
    if not text:
        return None
    if _TICKER.match(text):
        return text, ""
    for row in lookup_securities(text):
        ticker = str(row.get("ticker_code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not ticker:
            continue
        if text == name or text in name or name.startswith(text) or _code_matches(text, ticker):
            return ticker, name
    return None


def finance_api_queries(keywords: list[str]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    securities: list[tuple[str, str]] = []
    topics: list[str] = []
    for key in keywords:
        found = resolve_security(key)
        if found:
            ticker = found[0]
            if ticker not in seen:
                seen.add(ticker)
                securities.append(found)
        else:
            topics.append(key)
    if securities:
        return securities
    if topics:
        return [("", key) for key in topics]
    return [("", "")]


def guess_impact(title: str, summary: str = "", source_name: str = "") -> str:
    text = f"{title} {summary} {source_name}"
    bull = sum(1 for word in _BULL if word in text)
    bear = sum(1 for word in _BEAR if word in text)
    if bull and not bear:
        return "可能利好业绩或财务指标（据标题/摘要初步判断，未读全文）"
    if bear and not bull:
        return "可能利空业绩或财务指标（据标题/摘要初步判断，未读全文）"
    if bull and bear:
        return "利好利空信号并存，需结合全文判断对业绩或财务指标的影响"
    if any(word in source_name for word in ("公告", "交易所", "巨潮", "SEC", "EDGAR", "港交所")):
        return "官方披露，可能影响当期业绩或财务指标，需结合公告类型判断"
    return "从标题尚难判断对业绩或财务指标的影响"


def is_today(published_at: str, day: date | None = None) -> bool:
    day = day or today()
    text = (published_at or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10]) == day
        except ValueError:
            return False
    return False


def collect_group(kind: str, config: dict, owner_id: str) -> dict[str, Any]:
    if kind == "wechat":
        return _wechat(owner_id)
    if kind == "finance_api":
        return _finance_api(config)
    if kind == "finance_rss":
        return _finance_rss(config)
    if kind == "holdings_announce":
        return _holdings_announce(config)
    return {"items": [], "failed": [], "login_required": False}


def _wechat(owner_id: str) -> dict[str, Any]:
    fetched = weread.list_shelf_articles(8, owner_id)
    if fetched.get("login_required"):
        return {"items": [], "failed": [fetched.get("error") or "微信读书尚未登录。"], "login_required": True}
    if fetched.get("error") and not fetched.get("accounts"):
        return {"items": [], "failed": [fetched["error"]], "login_required": False}
    day = today()
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    for account in fetched.get("accounts") or []:
        name = str(account.get("title") or "公众号")
        rows = list(account.get("results") or [])
        if account.get("error") and not rows:
            failed.append(f"{name}：{account['error']}")
            continue
        today_rows = [row for row in rows if is_today(str(row.get("published_at") or ""), day)]
        picked = today_rows or rows[:1]
        for row in picked:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            items.append(_item(title, row, "wechat", name, ""))
    return {"items": items, "failed": failed, "login_required": False}


def _finance_api(config: dict) -> dict[str, Any]:
    adapters = [str(item) for item in (config.get("adapters") or []) if catalog.finance_api(str(item))]
    keywords = split_keywords(str(config.get("keywords") or ""))
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    queries = finance_api_queries(keywords)
    for adapter_id in adapters:
        meta = catalog.finance_api(adapter_id) or {}
        label = str(meta.get("name") or adapter_id)
        if adapter_id in _TICKER_ONLY_ADAPTERS:
            runnable = [(ticker, name) for ticker, name in queries if ticker and preset.news_symbol(adapter_id, ticker)]
            if not runnable:
                failed.append(f"{label}：需要带市场的股票代码（如 00700.HK、600519.SH），主题词请用东方财富或集思录。")
                continue
        else:
            runnable = queries
        for ticker, name in runnable:
            try:
                payload = preset.fetch_preset_news(adapter_id, ticker, name)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{label}：{exc}")
                continue
            if payload.get("error"):
                failed.append(f"{label}：{payload['error']}")
                continue
            for row in payload.get("results") or []:
                title = str(row.get("title") or "").strip()
                if not title or not match_keywords(title, str(row.get("summary") or ""), keywords):
                    continue
                items.append(_item(title, row, "finance_api", label, meta.get("icon") or ""))
    return {"items": items, "failed": failed, "login_required": False}


def _finance_rss(config: dict) -> dict[str, Any]:
    adapters = [str(item) for item in (config.get("adapters") or []) if catalog.finance_rss(str(item))]
    keywords = split_keywords(str(config.get("keywords") or ""))
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    for adapter_id in adapters:
        meta = catalog.finance_rss(adapter_id) or {}
        label = str(meta.get("name") or adapter_id)
        try:
            rows = rss_repo.fetch_adapter({**meta, "id": adapter_id})
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{label}：{exc}")
            continue
        for row in pick_rss_rows(rows, keywords):
            items.append(_item(str(row.get("title") or "").strip(), row, "finance_rss", label, meta.get("icon") or ""))
    return {"items": items, "failed": failed, "login_required": False}


def _holdings_announce(config: dict) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    for holding in config.get("holdings") or []:
        if not isinstance(holding, dict):
            continue
        ticker = str(holding.get("ticker_code") or "").strip()
        name = str(holding.get("name") or ticker)
        adapter_id = str(holding.get("adapter") or catalog.default_announce_adapter(str(holding.get("market_code") or "")))
        meta = catalog.announce(adapter_id)
        if not meta:
            failed.append(f"{name}：未知公告适配器 {adapter_id}")
            continue
        try:
            rows = _announce_rows(adapter_id, ticker, name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}（{meta['name']}）：{exc}")
            continue
        for row in rows:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            items.append(_item(title, row, "holdings_announce", meta["name"], meta.get("icon") or ""))
    return {"items": items, "failed": failed, "login_required": False}


def _announce_rows(adapter_id: str, ticker: str, name: str) -> list[dict[str, str]]:
    if adapter_id == "cninfo":
        return announce.fetch_cninfo(ticker, name)
    if adapter_id == "sse":
        return announce.fetch_sse(ticker, name)
    if adapter_id == "szse":
        return announce.fetch_szse(ticker, name)
    if adapter_id == "hkex":
        payload = preset.fetch_preset_news("hkex", ticker, name)
        if payload.get("error"):
            raise ValueError(payload["error"])
        return list(payload.get("results") or [])
    if adapter_id == "sec":
        return announce.fetch_sec(ticker, name)
    raise ValueError(f"未知公告适配器：{adapter_id}")


def _item(title: str, row: dict, kind: str, source_name: str, icon: str) -> dict[str, Any]:
    summary = str(row.get("summary") or "")
    return {
        "source_kind": kind,
        "source_name": source_name,
        "source_icon": icon,
        "title": title,
        "url": str(row.get("url") or ""),
        "published_at": str(row.get("published_at") or ""),
        "summary": summary,
        "impact": guess_impact(title, summary, source_name),
    }

