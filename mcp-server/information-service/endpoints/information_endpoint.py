"""资讯 REST：/api/information/*。用户身份来自账号 JWT 的 sub。"""
from __future__ import annotations

import asyncio
import json

from starlette.requests import Request
from starlette.responses import JSONResponse

import information_service as service
from information_service import InformationError


def _owner(request: Request) -> str:
    from account_jwt import current_sub, verify_bearer

    owner = current_sub()
    if not owner:
        claims = verify_bearer(request.headers.get("authorization", ""))
        owner = str(claims.get("sub") or "") if claims else ""
    if not owner:
        raise InformationError("请先登录。", 401)
    return owner


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise InformationError("请求体不是合法 JSON。") from None
    if not isinstance(data, dict):
        raise InformationError("请求体必须是对象。")
    return data


def _int(value: str, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InformationError(f"{label} 无效。") from None


def _handler(fn):
    async def route(request: Request):
        cors = request.app.state.information_cors
        try:
            payload = await fn(request)
        except InformationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"资讯服务错误：{exc}"}, status_code=500, headers=cors)
        return JSONResponse(payload, headers=cors)

    return route


def register_routes(app, cors_headers: dict[str, str]) -> None:
    app.state.information_cors = cors_headers

    @_handler
    async def groups(request: Request):
        owner = _owner(request)
        if request.method == "POST":
            return service.create_group(owner, await _body(request))
        return {"groups": service.list_groups(owner)}

    @_handler
    async def group_item(request: Request):
        owner = _owner(request)
        gid = _int(request.path_params["gid"], "资讯组 id")
        if request.method == "DELETE":
            service.delete_group(owner, gid)
            return {"ok": True}
        return service.update_group(owner, gid, await _body(request))

    @_handler
    async def items(request: Request):
        owner = _owner(request)
        gid = _int(request.path_params["gid"], "资讯组 id")
        today_only = (request.query_params.get("today") or "1") != "0"
        for_review = (request.query_params.get("review") or "") in {"1", "true", "yes"}
        return service.list_items(owner, gid, today_only=today_only, for_review=for_review)

    @_handler
    async def fetch_today(request: Request):
        owner = _owner(request)
        gid = _int(request.path_params["gid"], "资讯组 id")
        return await asyncio.to_thread(service.fetch_today, owner, gid)

    @_handler
    async def clear_history(request: Request):
        return service.clear_history(_owner(request))

    @_handler
    async def item(request: Request):
        owner = _owner(request)
        iid = _int(request.path_params["iid"], "资讯 id")
        if request.method == "DELETE":
            return service.hide_item(owner, iid)
        return service.get_item(owner, iid)

    @_handler
    async def favorite(request: Request):
        owner = _owner(request)
        iid = _int(request.path_params["iid"], "资讯 id")
        body = await _body(request)
        favorited = body.get("favorited", True)
        return service.favorite_item(owner, iid, bool(favorited))

    @_handler
    async def item_impact(request: Request):
        owner = _owner(request)
        body = await _body(request)
        return service.save_impact(owner, _int(request.path_params["iid"], "资讯 id"), str(body.get("impact") or ""))

    @_handler
    async def item_analysis(request: Request):
        owner = _owner(request)
        body = await _body(request)
        analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else body
        return service.save_analysis(owner, _int(request.path_params["iid"], "资讯 id"), analysis)

    @_handler
    async def today_summary(request: Request):
        owner = _owner(request)
        return service.today_summary(owner, _int(request.path_params["gid"], "资讯组 id"))

    @_handler
    async def reviews(request: Request):
        owner = _owner(request)
        gid = _int(request.path_params["gid"], "资讯组 id")
        if request.method == "POST":
            return service.save_review(owner, gid, await _body(request))
        return service.list_reviews(owner, gid)

    @_handler
    async def review_item(request: Request):
        owner = _owner(request)
        return service.get_review(owner, _int(request.path_params["rid"], "点评 id"))

    @_handler
    async def adapters(request: Request):
        return service.adapters(request.query_params.get("kind") or "")

    @_handler
    async def holdings(request: Request):
        return service.list_holdings(_owner(request))

    @_handler
    async def weread_status(_request: Request):
        return service.weread_status()

    @_handler
    async def weread_login(_request: Request):
        return service.weread_login()

    @_handler
    async def weread_login_status(request: Request):
        return service.weread_login_status(request.path_params["session_id"])

    app.add_route("/api/information/groups", groups, methods=["GET", "POST", "OPTIONS"])
    app.add_route("/api/information/groups/{gid}", group_item, methods=["PUT", "DELETE", "OPTIONS"])
    app.add_route("/api/information/groups/{gid}/items", items, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/groups/{gid}/fetch", fetch_today, methods=["POST", "OPTIONS"])
    app.add_route("/api/information/groups/{gid}/summary", today_summary, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/groups/{gid}/reviews", reviews, methods=["GET", "POST", "OPTIONS"])
    app.add_route("/api/information/reviews/{rid}", review_item, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/items/{iid}/favorite", favorite, methods=["POST", "OPTIONS"])
    app.add_route("/api/information/items/{iid}", item, methods=["GET", "DELETE", "OPTIONS"])
    app.add_route("/api/information/items/{iid}/impact", item_impact, methods=["PUT", "OPTIONS"])
    app.add_route("/api/information/items/{iid}/analysis", item_analysis, methods=["PUT", "OPTIONS"])
    app.add_route("/api/information/clear", clear_history, methods=["POST", "OPTIONS"])
    app.add_route("/api/information/adapters", adapters, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/holdings", holdings, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/weread/status", weread_status, methods=["GET", "OPTIONS"])
    app.add_route("/api/information/weread/login", weread_login, methods=["POST", "OPTIONS"])
    app.add_route("/api/information/weread/login/{session_id}", weread_login_status, methods=["GET", "OPTIONS"])
