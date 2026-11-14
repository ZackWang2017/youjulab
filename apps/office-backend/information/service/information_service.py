"""资讯组、今日拉取、收藏与每日点评。"""
from __future__ import annotations

from datetime import date
from typing import Any
from contextvars import ContextVar
from threading import RLock
from store import default_information, remove_recent_asset

from ..repository import adapter_catalog as catalog
from . import fetch_service as fetch
from ..repository import information_repository as repo
import httpx

from config import mcp_remote_base
from remote_auth import remote_token
from ..collectors import weread_session_service as weread

GROUP_KINDS = ("wechat", "finance_api", "finance_rss", "holdings_announce")
MAX_GROUPS = 30
FAVORITES_NAME = "资讯收藏栏"
active_store = ContextVar("information_store", default=None)
_creation_lock = RLock()


def _public_group(row):
    result = repo.public_group(row)
    store = active_store.get()
    asset = store.asset(row['owner_id'], row.get('asset_id') or '') if store else None
    result['visibility'] = (asset or {}).get('visibility', 'user' if row['kind'] == 'favorites' else 'project')
    return result


def _visibility(body):
    visibility = body.get('visibility')
    if visibility is not None and visibility not in ('user', 'project'):
        raise InformationError('可见范围无效。')
    return visibility


def _project(owner_id: str, project_id: str | None):
    store = active_store.get()
    if store is not None and (not project_id or not store.project(owner_id, project_id)):
        raise InformationError("项目不存在。", 404)


def _visible(owner_id: str, row: dict) -> bool:
    store = active_store.get()
    if store is None:
        return True
    asset = store.asset(owner_id, row.get("asset_id") or "")
    content = (asset or {}).get("content")
    return bool(row.get("project_id") and store.project(owner_id, row["project_id"])
                and asset and asset.get("kind") == "information"
                and asset.get("project_id") == row["project_id"]
                and isinstance(content, dict) and content.get("group_id") == row["id"]
                and content.get("format") == "invp-information-group-v1")


def _create_linked(owner_id, name, kind, config, project_id):
    with _creation_lock:
        row = repo.create_group(owner_id, name, kind, config, project_id=project_id)
        store = active_store.get()
        if store is not None and not row.get("asset_id"):
            asset = store.create_asset(owner_id, project_id, "information", name,
                                       default_information(row["id"]))
            if kind == 'favorites':
                store.update_asset(owner_id, asset['id'], visibility='user', information_scope_version=1)
            try:
                repo.link_asset(owner_id, row["id"], asset["id"])
            except Exception:
                store.delete_asset(owner_id, asset["id"])
                raise
            row["asset_id"] = asset["id"]
        return row


def _scoped_items(owner_id, row, **kwargs):
    return [item for item in repo.list_items(owner_id, **kwargs)
            if (group := repo.get_group(owner_id, item["group_id"]))
            and group.get("project_id") == row.get("project_id") and _visible(owner_id, group)]


class InformationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _group(owner_id: str, group_id: int) -> dict:
    row = repo.get_group(owner_id, group_id)
    if not row or not _visible(owner_id, row):
        raise InformationError("资讯组不存在。", 404)
    return row


def ensure_favorites(owner_id: str, project_id: str | None = None) -> dict:
    _project(owner_id, project_id)
    with _creation_lock:
        found = repo.find_group_by_kind(owner_id, "favorites", project_id)
        if found:
            store = active_store.get()
            asset = store.asset(owner_id, found.get('asset_id') or '') if store else None
            if asset and not asset.get('information_scope_version'):
                store.update_asset(owner_id, asset['id'], visibility='user', information_scope_version=1)
            return found
        return _create_linked(owner_id, FAVORITES_NAME, "favorites", {}, project_id)


def list_groups(owner_id: str, project_id: str | None = None) -> list[dict]:
    ensure_favorites(owner_id, project_id)
    return [_public_group(row) for row in repo.list_groups(owner_id, project_id) if _visible(owner_id, row)]


def create_group(owner_id: str, body: dict) -> dict:
    visibility = _visibility(body)
    project_id = body.get("project_id")
    ensure_favorites(owner_id, project_id)
    name = str(body.get("name") or "").strip()
    kind = str(body.get("kind") or "").strip()
    if not name:
        raise InformationError("请填写资讯组名称。")
    if len(name) > 80:
        raise InformationError("资讯组名称不能超过 80 字。")
    if kind not in GROUP_KINDS:
        raise InformationError("类型只能是公众号、财经 API、财经 RSS 或持仓公告。")
    existing = [row for row in repo.list_groups(owner_id, project_id) if row["kind"] != "favorites"]
    if len(existing) >= MAX_GROUPS:
        raise InformationError(f"最多创建 {MAX_GROUPS} 个资讯组。")
    if any(row["name"] == name for row in repo.list_groups(owner_id, project_id)):
        raise InformationError("已有同名资讯组。")
    config = _normalize_config(kind, body.get("config") if isinstance(body.get("config"), dict) else body)
    row = _create_linked(owner_id, name, kind, config, project_id)
    if visibility and active_store.get() is not None:
        active_store.get().update_asset(owner_id, row['asset_id'], visibility=visibility)
    return _public_group(row)


def update_group(owner_id: str, group_id: int, body: dict) -> dict:
    visibility = _visibility(body)
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        if any(key in body for key in ('name', 'config')):
            raise InformationError("收藏栏名称和类型不能修改。")
        if visibility and active_store.get() is not None:
            active_store.get().update_asset(owner_id, row['asset_id'], visibility=visibility, information_scope_version=1)
        return _public_group(row)
    name = str(body.get("name") or row["name"]).strip()
    if not name:
        raise InformationError("请填写资讯组名称。")
    if any(item["name"] == name and int(item["id"]) != group_id for item in repo.list_groups(owner_id, row.get("project_id"))):
        raise InformationError("已有同名资讯组。")
    incoming = body.get("config") if isinstance(body.get("config"), dict) else body
    config = _normalize_config(row["kind"], {**repo.public_group(row)["config"], **incoming})
    updated = repo.update_group(owner_id, group_id, name, config)
    if not updated:
        raise InformationError("资讯组不存在。", 404)
    if active_store.get() is not None:
        store = active_store.get()
        asset = store.asset(owner_id, row["asset_id"])
        store.update_asset(owner_id, row["asset_id"], name=name, visibility=visibility or asset.get('visibility', 'project'), revision=asset.get("revision", 0) + 1)
    return _public_group(updated)


def delete_group(owner_id: str, group_id: int) -> None:
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        raise InformationError("收藏夹不能删除。")
    ensure_favorites(owner_id, row.get("project_id"))
    repo.delete_group(owner_id, group_id)
    if active_store.get() is not None:
        store = active_store.get()
        store.delete_asset(owner_id, row["asset_id"])
        user = store.user_by_id(owner_id)
        if user:
            last = dict(user.get("last_assets") or {})
            if last.get("information") == row["asset_id"]:
                last["information"] = None
            store.update_user(owner_id, last_assets=last,
                              recent_assets_by_project=remove_recent_asset(user, row["asset_id"]))


def list_items(owner_id: str, group_id: int, *, today_only: bool = False, for_review: bool = False) -> dict:
    row = _group(owner_id, group_id)
    day = fetch.today() if today_only else None
    if row["kind"] == "favorites":
        # 收藏夹列出全部收藏；只有复盘按当天收藏统计。
        rows = _scoped_items(owner_id, row, favorited=True, fetched_date=day if for_review else None, hidden=None if for_review else False)
    elif for_review:
        rows = repo.list_items(owner_id, group_id=group_id, fetched_date=day)
    else:
        rows = repo.list_items(owner_id, group_id=group_id, fetched_date=day, favorited=False, hidden=False)
    return {"group": repo.public_group(row), "items": [repo.public_item(item) for item in rows]}


def fetch_today(owner_id: str, group_id: int) -> dict:
    row = _group(owner_id, group_id)
    if row["kind"] == "favorites":
        raise InformationError("收藏夹不支持获取，请到其它资讯组获取后再收藏。")
    collected = fetch.collect_group(row["kind"], repo.public_group(row)["config"], owner_id)
    if collected.get("login_required"):
        return {
            "group": repo.public_group(row),
            "inserted": [],
            "skipped": 0,
            "failed": collected.get("failed") or [],
            "login_required": True,
        }
    day = fetch.today()
    known = repo.known_titles(owner_id, row.get("project_id"))
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
            **item,
            "owner_id": owner_id,
            "group_id": group_id,
            "fetched_date": day,
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


def clear_history(owner_id: str, project_id: str | None = None) -> dict:
    _project(owner_id, project_id)
    if active_store.get() is not None:
        deleted = 0
        for item in repo.list_items(owner_id, favorited=False):
            group = repo.get_group(owner_id, item["group_id"])
            if group and group.get("project_id") == project_id and _visible(owner_id, group) and str(item["fetched_date"])[:10] != fetch.today().isoformat():
                deleted += repo.delete_item(owner_id, item["id"])
        return {"deleted": deleted}
    deleted = repo.clear_past_unfavorited(owner_id, fetch.today(), project_id)
    return {"deleted": deleted}


def get_item(owner_id: str, item_id: int) -> dict:
    item = repo.get_item(owner_id, item_id)
    if not item:
        raise InformationError("资讯不存在。", 404)
    _group(owner_id, item["group_id"])
    return repo.public_item(item)


def favorite_item(owner_id: str, item_id: int, favorited: bool = True) -> dict:
    get_item(owner_id, item_id)
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
    get_item(owner_id, item_id)
    updated = repo.set_hidden(owner_id, item_id, True)
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def save_impact(owner_id: str, item_id: int, impact: str) -> dict:
    get_item(owner_id, item_id)
    updated = repo.update_impact(owner_id, item_id, str(impact or "").strip())
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def save_analysis(owner_id: str, item_id: int, analysis: dict) -> dict:
    get_item(owner_id, item_id)
    updated = repo.update_analysis(owner_id, item_id, analysis or {})
    if not updated:
        raise InformationError("资讯不存在。", 404)
    return repo.public_item(updated)


def today_summary(owner_id: str, group_id: int) -> dict:
    row = _group(owner_id, group_id)
    day = fetch.today()
    if row["kind"] == "favorites":
        items = _scoped_items(owner_id, row, favorited=True, fetched_date=day)
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
    _group(owner_id, row["group_id"])
    return repo.public_review(row)


def save_review(owner_id: str, group_id: int, body: dict) -> dict:
    group = _group(owner_id, group_id)
    for ref in body.get("item_refs") or []:
        if not isinstance(ref, dict):
            raise InformationError("资讯引用无效。")
        item = get_item(owner_id, int(ref.get("id") or 0))
        source = _group(owner_id, item["group_id"])
        if source.get("project_id") != group.get("project_id") or (group["kind"] != "favorites" and source["id"] != group_id):
            raise InformationError("资讯引用不属于当前资讯组。", 404)
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
    for portfolio in _remote_portfolios():
        for row in _remote_holdings(int(portfolio["id"])):
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


def _remote_get(path: str, params: dict | None = None) -> dict:
    token = remote_token()
    if not token:
        raise InformationError("请先登录远程账户以读取持仓。", 401)
    try:
        response = httpx.get(
            f"{mcp_remote_base().rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid portfolio response")
        return payload
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise InformationError("远程持仓读取失败。", status if status in (401, 403) else 502) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise InformationError("远程持仓读取失败。", 502) from exc


def _remote_portfolios() -> list[dict]:
    return _remote_get("/api/portfolio/portfolios").get("portfolios") or []


def _remote_holdings(portfolio_id: int) -> list[dict]:
    return _remote_get(
        f"/api/portfolio/portfolios/{portfolio_id}/holdings",
        {"refresh": "0"},
    ).get("holdings") or []


def weread_status(owner_id: str) -> dict:
    return weread.current_status(owner_id)


def weread_login(owner_id: str) -> dict:
    return weread.start_login(owner_id)


def weread_login_status(session_id: str, owner_id: str) -> dict:
    return weread.login_status(session_id, owner_id)


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

