"""证券查询 REST。业务查询在本服务，办公后台只转发。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import security_search_service as search_service


def register_routes(app, cors_headers: dict[str, str]) -> None:
    async def search_route(request: Request):
        try:
            limit = int(request.query_params.get("limit") or "8")
        except ValueError:
            limit = 8
        try:
            items = search_service.search_securities(request.query_params.get("q") or "", limit)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400, headers=cors_headers)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500, headers=cors_headers)
        return JSONResponse({"ok": True, "data": {"items": items}}, headers=cors_headers)

    app.add_route("/api/financial/securities/search", search_route, methods=["GET", "OPTIONS"])
