"""账号 HTTP：/api/account/*。公开接口不要求 JWT，其余由 server.py 的 guard 校验。"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse

import account_service as service
import captcha_service
from account_jwt import current_sub, verify_bearer
from account_service import AccountError


def _json(request: Request, payload: dict, status: int = 200):
    cors = getattr(request.app.state, "account_cors", {})
    return JSONResponse(payload, status_code=status, headers=cors)


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise AccountError("请求体不是合法 JSON。") from None
    if not isinstance(data, dict):
        raise AccountError("请求体必须是对象。")
    return data


def _handler(fn):
    async def route(request: Request):
        try:
            payload = await fn(request)
        except AccountError as exc:
            return _json(request, {"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001
            return _json(request, {"error": f"账号服务错误：{exc}"}, 500)
        return _json(request, payload)

    return route


def register_routes(app, cors: dict) -> None:
    app.state.account_cors = cors

    @_handler
    async def health(_request: Request):
        return {"ok": True}

    @_handler
    async def captcha(_request: Request):
        return captcha_service.issue()

    @_handler
    async def register(request: Request):
        return service.register(await _body(request))

    @_handler
    async def login(request: Request):
        return service.login(await _body(request))

    @_handler
    async def me(request: Request):
        sub = current_sub() or (verify_bearer(request.headers.get("authorization", "")) or {}).get("sub", "")
        if not sub:
            raise AccountError("请先登录。", 401)
        return service.me(str(sub))

    @_handler
    async def password(request: Request):
        sub = current_sub() or (verify_bearer(request.headers.get("authorization", "")) or {}).get("sub", "")
        if not sub:
            raise AccountError("请先登录。", 401)
        body = await _body(request)
        service.change_password(str(sub), str(body.get("current_password") or ""), str(body.get("new_password") or ""))
        return {"ok": True}

    @_handler
    async def access_token(request: Request):
        sub = current_sub() or (verify_bearer(request.headers.get("authorization", "")) or {}).get("sub", "")
        if not sub:
            raise AccountError("请先登录。", 401)
        return service.issue_agent_token(str(sub))

    app.add_route("/api/account/health", health, methods=["GET", "OPTIONS"])
    app.add_route("/api/account/captcha", captcha, methods=["GET", "OPTIONS"])
    app.add_route("/api/account/register", register, methods=["POST", "OPTIONS"])
    app.add_route("/api/account/login", login, methods=["POST", "OPTIONS"])
    app.add_route("/api/account/me", me, methods=["GET", "OPTIONS"])
    app.add_route("/api/account/password", password, methods=["PUT", "OPTIONS"])
    app.add_route("/api/account/access-token", access_token, methods=["POST", "OPTIONS"])
