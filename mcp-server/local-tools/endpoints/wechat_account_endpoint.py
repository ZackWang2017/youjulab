"""指定公众号文章列表的 MCP 入口。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import wechat_account_service as account_service
import weread_session_service as weread_service


def register_tools(mcp) -> None:
    @mcp.tool()
    def list_wechat_account(account: str, source: str = "rsshub", num: int = 10) -> dict:
        """拉取一个指定公众号的最近文章。source=rsshub 时 account 是微信号；source=weread 时 account 是微信读书公众号 id（MP_WXS_ 开头）。只返回标题、链接、时间和摘要。"""
        return account_service.list_wechat_account(account, source=source, num=num)

    @mcp.tool()
    def list_weread_shelf(num: int = 5) -> dict:
        """重新读取微信读书书架上的全部公众号，并返回每个号的最近文章。"""
        return weread_service.list_shelf_articles(num)


def register_routes(app, cors_headers: dict[str, str]) -> None:
    async def account_route(request: Request):
        try:
            num = int(request.query_params.get("num") or 10)
        except ValueError:
            num = 10
        payload = account_service.list_wechat_account(
            request.query_params.get("account") or "",
            request.query_params.get("source") or "rsshub",
            num,
        )
        return JSONResponse(payload, headers=cors_headers)

    async def shelf_route(request: Request):
        try:
            num = int(request.query_params.get("num") or 5)
        except ValueError:
            num = 5
        payload = weread_service.list_shelf_articles(num)
        return JSONResponse(payload, headers=cors_headers)

    async def weread_login_route(_request: Request):
        return JSONResponse(weread_service.start_login(), headers=cors_headers)

    async def weread_login_status_route(request: Request):
        return JSONResponse(weread_service.login_status(request.path_params["session_id"]), headers=cors_headers)

    app.add_route("/api/local-tools/wechat-account", account_route, methods=["GET", "OPTIONS"])
    app.add_route("/api/local-tools/weread-shelf", shelf_route, methods=["GET", "OPTIONS"])
    app.add_route("/api/local-tools/weread-login", weread_login_route, methods=["POST", "OPTIONS"])
    app.add_route("/api/local-tools/weread-login/{session_id}", weread_login_status_route, methods=["GET", "OPTIONS"])
