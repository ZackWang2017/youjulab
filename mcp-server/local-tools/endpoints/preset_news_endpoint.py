"""预定义资讯查询入口。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import preset_news_service as news


def register_tools(mcp) -> None:
    @mcp.tool()
    def fetch_preset_news(source: str, ticker_code: str = "", name: str = "") -> dict:
        """查询预定义来源的资讯。source 为 hkex、sina、eastmoney、xueqiu、jisilu、tencent，按股票查询。"""
        return news.fetch_preset_news(source, ticker_code, name)


def register_routes(app, cors_headers: dict[str, str]) -> None:
    async def preset_route(request: Request):
        payload = news.fetch_preset_news(
            request.query_params.get("source") or "",
            request.query_params.get("ticker_code") or "",
            request.query_params.get("name") or "",
        )
        status = 400 if payload.get("error") == "未知的信息源。" else 200
        return JSONResponse(payload, status_code=status, headers=cors_headers)

    app.add_route("/api/local-tools/preset-news", preset_route, methods=["GET", "OPTIONS"])
