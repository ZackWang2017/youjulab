"""RSS、网页和 RSSHub 拉取入口。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import feed_service


def register_routes(app, cors_headers: dict[str, str]) -> None:
    async def feed_route(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        sources = body.get("sources") if isinstance(body, dict) else []
        payload = feed_service.collect_sources(sources if isinstance(sources, list) else [])
        return JSONResponse(payload, headers=cors_headers)

    app.add_route("/api/local-tools/feed", feed_route, methods=["POST", "OPTIONS"])
