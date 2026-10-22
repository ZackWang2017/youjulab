"""外部工具 MCP 包：搜索等可扩展能力，挂在 /mcp/external-tools。"""
from __future__ import annotations

from contextlib import asynccontextmanager

import search_service


def register_tools(mcp) -> None:
    # 配额库初始化失败不阻塞 MCP 启动（Postgres 挂起/未起时仅打日志）
    try:
        ready = search_service.ensure_ready()
        print(
            "external_tools: search quota ready "
            f"baidu_used={ready['baidu'].get('used_today')} "
            f"baike_used={ready['baike'].get('used_today')} "
            f"google_used={ready['google'].get('used_today')}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"external_tools: search quota init skipped ({exc}); "
            "百度/百科/Google 搜索在数据库可用前会返回配额错误",
            flush=True,
        )

    @mcp.tool()
    def baidu_web_search(query: str, num: int = 5, topic: str = "") -> dict:
        """百度网页搜索（千帆 AI Search）。受 SEARCH_BAIDU_ENABLED / 日限额约束。可选 topic。"""
        return search_service.baidu_web_search(query, num=num, topic=topic)

    @mcp.tool()
    def baidu_baike_search(query: str, num: int = 5) -> dict:
        """百度百科词条（lemma/get_content）。与百度网页搜索分开。受 SEARCH_BAIKE_ENABLED / 日限额约束。"""
        return search_service.baidu_baike_search(query, num=num)

    @mcp.tool()
    def google_web_search(query: str, num: int = 5, topic: str = "") -> dict:
        """Google 网页搜索（CSE）。受 SEARCH_GOOGLE_ENABLED / 日限额约束。可选 topic。"""
        return search_service.google_web_search(query, num=num, topic=topic)

    @mcp.tool()
    def search_quota_status() -> dict:
        """查看百度/百科/Google 搜索开关与今日已用次数（按 SEARCH_USAGE_TZ / DIGEST_TZ 自然日）。"""
        return search_service.quota_status()


def register_http(app, cors: dict) -> None:
    """供 office-backend 用用户 JWT 调用搜索，避免办公进程直接 import 本服务。"""
    import json

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def invoke(request: Request):
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return JSONResponse({"error": "请求体不是合法 JSON。"}, status_code=400, headers=cors)
        name = str(body.get("name") or "")
        query = str(body.get("query") or "")
        num = int(body.get("num") or 5)
        topic = str(body.get("topic") or "")
        try:
            if name == "baidu_web_search":
                payload = search_service.baidu_web_search(query, num=num, topic=topic)
            elif name == "baidu_baike_search":
                payload = search_service.baidu_baike_search(query, num=num)
            elif name == "google_web_search":
                payload = search_service.google_web_search(query, num=num, topic=topic)
            else:
                return JSONResponse({"error": "未知搜索工具。"}, status_code=400, headers=cors)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=502, headers=cors)
        return JSONResponse(payload, headers=cors)

    app.add_route("/api/external-tools/invoke", invoke, methods=["POST", "OPTIONS"])


def mount_external_mcp(host_app, external_mcp) -> None:
    """把外部工具 MCP 挂到同一进程的 /mcp/external-tools。"""
    child_app = external_mcp.streamable_http_app()
    host_app.router.routes.extend(list(child_app.routes))
    inner = host_app.router.lifespan_context
    child_life = child_app.router.lifespan_context

    @asynccontextmanager
    async def combined(scope_app):
        async with inner(scope_app):
            async with child_life(scope_app):
                yield

    host_app.router.lifespan_context = combined
