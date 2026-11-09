"""资讯组、今日拉取、收藏与每日点评。"""
from __future__ import annotations

from datetime import date
from typing import Any

import adapter_catalog as catalog
import fetch_service as fetch
import information_repository as repo
import portfolio_repository as holdings
import weread_session_service as weread

GROUP_KINDS = ("wechat", "finance_api", "finance_rss", "holdings_announce")
MAX_GROUPS = 30
FAVORITES_NAME = "收藏夹"


class InformationError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _group(owner_id: str, group_id: int) -> dict:
    row = repo.get_group(owner_id, group_id)
    if not row:
        raise InformationError("资讯组不存在。", 404)
    return row


def ensure_favorites(owner_id: str) -> dict:
    found = repo.find_group_by_kind(owner_id, "favorites")
    if found:
        return found
    return repo.create_group(owner_id, FAVORITES_NAME, "favorites", {})


def list_groups(owner_id: str) -> list[dict]:
    ensure_favorites(owner_id)
    return [repo.public_group(row) for row in repo.list_groups(owner_id)]


def create_group(owner_id: str, body: dict) -> dict:
    ensure_favorites(owner_id)
    name = str(body.get("name") or "").strip()
    kind = str(body.get("kind") or "").strip()
    if not name:
        raise InformationError("请填写资讯组名称。")
    if len(name) > 80:
        raise InformationError("资讯组名称不能超过 80 字。")
    if kind not in GROUP_KINDS:
        raise InformationError("类型只能是公众号、财经 API、财经 RSS 或持仓公告。")
    existing = [row for row in repo.list_groups(owner_id) if row["kind"] != "favorites"]
    if len(existing) >= MAX_GROUPS:
        raise InformationError(f"最多创建 {MAX_GROUPS} 个资讯组。")
    if any(row["name"] == name for row in repo.list_groups(owner_id)):
        raise InformationError("已有同名资讯组。")
    config = _normalize_config(kind, body.get("config") if isinstance(body.get("config"), dict) else body)
    return repo.public_group(repo.create_group(owner_id, name, kind, config))


def update_group(owner_id: str, group_id: int, body: dict) -> dict:
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        raise InformationError("收藏夹不能修改。")
    name = str(body.get("name") or row["name"]).strip()
    if not name:
        raise InformationError("请填写资讯组名称。")
    if any(item["name"] == name and int(item["id"]) != group_id for item in repo.list_groups(owner_id)):
        raise InformationError("已有同名资讯组。")
    incoming = body.get("config") if isinstance(body.get("config"), dict) else body
    config = _normalize_config(row["kind"], incoming)
    updated = repo.update_group(owner_id, group_id, name, config)
    if not updated:
        raise InformationError("资讯组不存在。", 404)
    return repo.public_group(updated)


def delete_group(owner_id: str, group_id: int) -> None:
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        raise InformationError("收藏夹不能删除。")
    repo.delete_group(owner_id, group_id)


def list_items(owner_id: str, group_id: int, *, today_only: bool = False, for_review: bool = False) -> dict:
    row = _group(owner_id, group_id)
    day = fetch.today() if today_only else None
    if row["kind"] == "favorites":
        # 收藏夹列出全部收藏；只有复盘按当天收藏统计。
        rows = repo.list_items(owner_id, favorited=True, fetched_date=day if for_review else None, hidden=None if for_review else False)
    elif for_review:
        rows = repo.list_items(owner_id, group_id=group_id, fetched_date=day)
    else:
        rows = repo.list_items(owner_id, group_id=group_id, fetched_date=day, favorited=False, hidden=False)
    return {"group": repo.public_group(row), "items": [repo.public_item(item) for item in rows]}


def fetch_today(owner_id: str, group_id: int) -> dict:
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        raise InformationError("收藏夹不支持获取，请到其它资讯组获取后再收藏。")
    collected = fetch.collect_group(row["kind"], repo.public_group(row)["config"])
    if collected.get("login_required"):
        return {
            "group": repo.public_group(row),
            "inserted": [],
            "skipped": 0,
            "failed": collected.get("failed") or [],
            "login_required": True,
        }
    day = fetch.today()
    known = repo.known_titles(owner_id)
    inserted: list[dict] = []
    skipped = 0
    seen: set[str] = set()
    for item in collected.get("items") or []:
        title = item["title"]
        if title in known or title in seen or not fetch.is_today(str(item.get("published_at") or ""), day):
            skipped += 1
            continue
        seen.add(title)
        saved = repo.insert_item({
            "owner_id": owner_id,
            "group_id": group_id,
            "fetched_date": day,
            **item,
        })
        if saved:
            known.add(title)
            inserted.append(repo.public_item(saved))
        else:
            skipped += 1
    return {
        "group": repo.public_group(row),
        "inserted": inserted,
        "skipped": skipped,
        "failed": collected.get("failed") or [],
        "login_required": False,
    }


def clear_history(owner_id: str) -> dict:
    deleted = repo.clear_past_unfavorited(owner_id, fetch.today())
    return {"deleted": deleted}


def get_item(owner_id: str, item_id: int) -> dict:
    item = repo.get_item(owner_id, item_id)
    if not item:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(item)


def favorite_item(owner_id: str, item_id: int, favorited: bool = True) -> dict:
    item = repo.get_item(owner_id, item_id)
    if not item:
        raise InformationError("资讯不存在。", 404)
    group = repo.get_group(owner_id, int(item["group_id"]))
    kind = (group or {}).get("kind") or item.get("source_kind") or ""
    name = (group or {}).get("name") or item.get("source_name") or ""
    updated = repo.set_favorite(owner_id, item_id, favorited, kind, name)
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def hide_item(owner_id: str, item_id: int) -> dict:
    updated = repo.set_hidden(owner_id, item_id, True)
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def save_impact(owner_id: str, item_id: int, impact: str) -> dict:
    updated = repo.update_impact(owner_id, item_id, str(impact or "").strip())
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def save_analysis(owner_id: str, item_id: int, analysis: dict) -> dict:
    updated = repo.update_analysis(owner_id, item_id, analysis or {})
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def today_summary(owner_id: str, group_id: int) -> dict:
    row = _group(owner_id, group_id)
    day = fetch.today()
    if row["kind"] == "favorites":
        items = repo.list_items(owner_id, favorited=True, fetched_date=day)
    else:
        items = repo.list_items(owner_id, group_id=group_id, fetched_date=day)
    success = len(items)
    latest = repo.list_reviews(owner_id, group_id)
    today_review = next((repo.public_review(item) for item in latest if str(item.get("review_date"))[:10] == day.isoformat()), None)
    return {
        "group": repo.public_group(row),
        "date": day.isoformat(),
        "success_count": success,
        "fail_count": int((today_review or {}).get("fail_count") or 0),
        "sentiment": (today_review or {}).get("sentiment") or _sentiment(items),
        "item_count": success,
    }


def list_reviews(owner_id: str, group_id: int) -> dict:
    row = _group(owner_id, group_id)
    return {"group": repo.public_group(row), "reviews": [repo.public_review(item) for item in repo.list_reviews(owner_id, group_id)]}


def get_review(owner_id: str, review_id: int) -> dict:
    row = repo.get_review(owner_id, review_id)
    if not row:
        raise InformationError("点评不存在。", 404)
    return repo.public_review(row)


def save_review(owner_id: str, group_id: int, body: dict) -> dict:
    _group(owner_id, group_id)
    day = _date(body.get("review_date")) or fetch.today()
    saved = repo.upsert_review({
        "owner_id": owner_id,
        "group_id": group_id,
        "review_date": day,
        "success_count": int(body.get("success_count") or 0),
        "fail_count": int(body.get("fail_count") or 0),
        "sentiment": str(body.get("sentiment") or "中性"),
        "content": str(body.get("content") or ""),
        "item_refs": body.get("item_refs") if isinstance(body.get("item_refs"), list) else [],
    })
    return repo.public_review(saved)


def adapters(kind: str) -> dict:
    return {"adapters": catalog.list_adapters(kind)}


def list_holdings(owner_id: str) -> dict:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for portfolio in holdings.list_portfolios(owner_id):
        for row in holdings.list_holdings(int(portfolio["id"]), status="open"):
            ticker = str(row.get("ticker_code") or "")
            market = str(row.get("market_code") or "").upper()
            if not ticker or market == "CASH" or ticker in seen:
                continue
            seen.add(ticker)
            items.append({
                "ticker_code": ticker,
                "name": row.get("name") or ticker,
                "market_code": market,
                "adapter": catalog.default_announce_adapter(market),
                "adapters": catalog.adapters_for_market(market),
            })
    return {"holdings": items}


def weread_status() -> dict:
    return weread.current_status()


def weread_login() -> dict:
    return weread.start_login()


def weread_login_status(session_id: str) -> dict:
    return weread.login_status(session_id)


def _normalize_config(kind: str, raw: dict) -> dict:
    if kind == "wechat":
        return {}
    if kind in {"finance_api", "finance_rss"}:
        lookup = catalog.finance_api if kind == "finance_api" else catalog.finance_rss
        adapters = [str(item) for item in (raw.get("adapters") or []) if lookup(str(item))]
        if not adapters:
            raise InformationError("请至少选择一个适配器。")
        return {"adapters": adapters, "keywords": str(raw.get("keywords") or "").strip()}
    holdings_rows = []
    for item in raw.get("holdings") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker_code") or "").strip()
        market = str(item.get("market_code") or "").upper()
        if not ticker:
            continue
        adapter = str(item.get("adapter") or catalog.default_announce_adapter(market))
        if not catalog.announce(adapter):
            raise InformationError(f"{ticker} 的公告适配器无效。")
        holdings_rows.append({
            "ticker_code": ticker,
            "name": str(item.get("name") or ticker),
            "market_code": market,
            "adapter": adapter,
        })
    if not holdings_rows:
        raise InformationError("请至少选择一只持仓股票。")
    return {"holdings": holdings_rows}


def _sentiment(items: list[dict]) -> str:
    bull = bear = 0
    for item in items:
        text = f"{item.get('impact') or ''} {item.get('title') or ''}"
        analysis = item.get("analysis")
        if isinstance(analysis, str):
            text += analysis
        elif isinstance(analysis, dict):
            text += " ".join(str(value) for value in analysis.values())
        if "利空" in text:
            bear += 1
        elif "利好" in text:
            bull += 1
    if bull > bear:
        return "利好"
    if bear > bull:
        return "利空"
    return "中性"


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InformationError("日期格式无效。") from None
