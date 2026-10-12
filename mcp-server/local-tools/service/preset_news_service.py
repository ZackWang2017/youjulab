"""把港交所、新浪财经、东方财富、雪球、集思录、腾讯自选股的查询结果整理成同一结构。"""
from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import preset_news_repository as repository

SOURCES = ("hkex", "sina", "eastmoney", "xueqiu", "jisilu", "tencent")
_MAX_ITEMS = 5
_SINA_ITEM = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*<a[^>]+href='([^']+)'>([^<]+)</a>",
    re.I,
)
_TAGS = re.compile(r"<[^>]+>")
_JISILU_ITEM = re.compile(
    r'<h4>\s*<a[^>]+href="(https://www\.jisilu\.cn/question/\d+)"[^>]*>\s*([^<]+?)\s*</a>[\s\S]{0,1800}?(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
)


def fetch_preset_news(source: str, ticker_code: str = "", name: str = "") -> dict:
    kind = (source or "").strip().lower()
    if kind not in SOURCES:
        return {"source": kind, "results": [], "error": "未知的信息源。"}
    code, label = _ticker(ticker_code)
    title_name = (name or "").strip() or label
    try:
        if kind == "hkex":
            results = _hkex(code)
        elif kind == "sina":
            results = _sina(ticker_code)
        elif kind == "eastmoney":
            results = _eastmoney(title_name or code)
        elif kind == "xueqiu":
            results = _xueqiu(ticker_code)
        elif kind == "jisilu":
            results = _jisilu(ticker_code, title_name)
        else:
            results = _tencent(ticker_code)
    except Exception as exc:  # noqa: BLE001
        return {"source": kind, "results": [], "error": f"查询失败：{exc}"}
    cap = _MAX_ITEMS * 2 if kind == "xueqiu" else _MAX_ITEMS
    return {"source": kind, "results": results[:cap], "error": ""}


def _ticker(ticker_code: str) -> tuple[str, str]:
    raw = (ticker_code or "").strip()
    code, _, market = raw.partition(".")
    return code, market


def _hkex(code: str) -> list[dict]:
    if not code.isdigit():
        raise ValueError("港交所查询需要股票代码。")
    padded = code.zfill(5)
    found = repository.hkex_lookup(padded)
    stock_id = ""
    for item in found.get("stockInfo") or []:
        if str(item.get("code") or "") == padded:
            stock_id = str(item.get("stockId") or "")
            break
    if not stock_id and found.get("stockInfo"):
        stock_id = str(found["stockInfo"][0].get("stockId") or "")
    if not stock_id:
        raise ValueError("没有找到对应的港交所证券。")
    payload = repository.hkex_titles(int(stock_id))
    rows = json_list(payload.get("result"))
    items = []
    for row in rows:
        link = str(row.get("FILE_LINK") or "")
        if link.startswith("/"):
            link = "https://www1.hkexnews.hk" + link
        items.append({
            "title": _plain(row.get("TITLE")),
            "url": link,
            "published_at": _hkex_time(str(row.get("DATE_TIME") or "")),
            "summary": _plain(row.get("LONG_TEXT") or row.get("SHORT_TEXT") or ""),
        })
    return items


def _sina(ticker_code: str) -> list[dict]:
    symbol = _sina_symbol(ticker_code)
    if not symbol:
        raise ValueError("新浪财经查询需要带市场的股票代码。")
    page = html.unescape(repository.sina_news_html(symbol))
    items = []
    for match in _SINA_ITEM.finditer(page):
        items.append({
            "title": _plain(match.group(4)),
            "url": match.group(3),
            "published_at": f"{match.group(1)} {match.group(2)}",
            "summary": "",
        })
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _eastmoney(keyword: str) -> list[dict]:
    if not keyword:
        raise ValueError("东方财富查询需要股票名称或代码。")
    payload = repository.eastmoney_search(keyword)
    rows = ((payload.get("result") or {}).get("cmsArticleWebOld") or [])
    return [
        {
            "title": _plain(row.get("title")),
            "url": str(row.get("url") or ""),
            "published_at": _trim_seconds(str(row.get("date") or "")),
            "summary": _plain(row.get("content")),
        }
        for row in rows
        if row.get("title")
    ]


def _xueqiu(ticker_code: str) -> list[dict]:
    symbol = _xueqiu_symbol(ticker_code)
    if not symbol:
        raise ValueError("雪球查询需要带市场的股票代码。")
    payload = repository.xueqiu_news(symbol)
    if payload.get("error_code") not in (None, 0, "0"):
        raise ValueError(payload.get("error_description") or "雪球查询失败。")
    items = []
    for row in payload.get("list") or []:
        title = _plain(row.get("title"))
        summary = _plain(row.get("description"))
        if not title:
            title = summary
        target = str(row.get("target") or "")
        url = "https://xueqiu.com" + target if target.startswith("/") else target
        if not title:
            continue
        items.append({
            "title": title,
            "url": url,
            "published_at": _epoch_ms(row.get("created_at")),
            "summary": summary,
        })
        if len(items) >= _MAX_ITEMS:
            break
    try:
        items.extend(_xueqiu_talks(symbol))
    except Exception:
        return items
    return items


def _xueqiu_talks(symbol: str) -> list[dict]:
    payload = repository.xueqiu_talks(symbol)
    if payload.get("error_code") not in (None, 0, "0"):
        return []
    items = []
    for row in payload.get("list") or []:
        summary = _plain(row.get("description") or row.get("text"))
        if not summary or summary.startswith("回复"):
            continue
        title = _plain(row.get("title")) or _lead(summary)
        target = str(row.get("target") or "")
        if target.startswith("/"):
            url = "https://xueqiu.com" + target
        else:
            url = target
        if not url:
            continue
        items.append({
            "title": title,
            "url": url,
            "published_at": _epoch_ms(row.get("created_at")),
            "summary": summary,
        })
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _lead(text: str, limit: int = 36) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _tencent(ticker_code: str) -> list[dict]:
    symbol = _tencent_symbol(ticker_code)
    if not symbol:
        raise ValueError("腾讯自选股查询需要带市场的股票代码。")
    payload = repository.tencent_news(symbol)
    if payload.get("code") not in (None, 0, "0"):
        raise ValueError(payload.get("msg") or "腾讯自选股查询失败。")
    rows = ((payload.get("data") or {}).get("data") or [])
    items = []
    for row in rows:
        title = _plain(row.get("title"))
        if not title:
            continue
        items.append({
            "title": title,
            "url": str(row.get("url") or ""),
            "published_at": _trim_seconds(str(row.get("time") or "")),
            "summary": _plain(row.get("summary")),
        })
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _tencent_symbol(ticker_code: str) -> str:
    code, market = _ticker(ticker_code)
    market = market.lower()
    if market == "hk" and code.isdigit():
        return "hk" + code.zfill(5)
    if market in {"sh", "sz", "bj"} and code:
        return market + code
    return ""


def _jisilu(ticker_code: str, name: str) -> list[dict]:
    code, _ = _ticker(ticker_code)
    queries = []
    if name:
        queries.append(name)
    if code and code not in queries:
        queries.append(code)
    if not queries:
        raise ValueError("集思录查询需要股票名称或代码。")
    for query in queries:
        final_url, page = repository.jisilu_topic(query)
        if "<title>请稍候" in page:
            continue
        slug = unquote(urlparse(final_url).path.rstrip("/").rsplit("/", 1)[-1])
        if query.isdigit() and slug != query:
            continue
        items = _jisilu_items(page)
        if items:
            return items
    return []


def _jisilu_items(page: str) -> list[dict]:
    start = page.find('class="aw-question-list"')
    body = page[start:] if start >= 0 else page
    side = body.find('class="aw-side')
    if side > 0:
        body = body[:side]
    items = []
    for match in _JISILU_ITEM.finditer(body):
        title = _plain(match.group(2))
        if not title or title == "问题反馈":
            continue
        items.append({
            "title": title,
            "url": match.group(1),
            "published_at": match.group(3),
            "summary": "",
        })
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _xueqiu_symbol(ticker_code: str) -> str:
    code, market = _ticker(ticker_code)
    market = market.upper()
    if market == "HK" and code.isdigit():
        return code.zfill(5)
    if market in {"SH", "SZ", "BJ"} and code:
        return market + code
    return ""


def _sina_symbol(ticker_code: str) -> str:
    code, market = _ticker(ticker_code)
    market = market.lower()
    if market == "hk" and code.isdigit():
        return "hk" + code.zfill(5)
    if market in {"sh", "sz", "bj"} and code:
        return market + code
    return ""


def json_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        import json
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    return []


def _plain(value) -> str:
    return " ".join(_TAGS.sub("", html.unescape(str(value or ""))).split())


def _hkex_time(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = datetime.strptime(text, "%d/%m/%Y %H:%M")
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M")


def _trim_seconds(value: str) -> str:
    text = value.strip()
    if len(text) >= 16 and text[10] == " ":
        return text[:16]
    return text


def _epoch_ms(value) -> str:
    try:
        stamp = int(value) / 1000
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(stamp, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
