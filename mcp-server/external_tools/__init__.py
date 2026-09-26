"""外部工具 MCP 包：搜索等可扩展能力，挂在 /mcp/external-tools。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from . import search_engine, usage


def register_tools(mcp) -> None:
    # 配额库初始化失败不阻塞 MCP 启动（Postgres 挂起/未起时仅打日志）
    try:
        usage.ensure_schema()
        print(
            "external_tools: search quota ready "
            f"baidu_used={usage.get_count(usage.ENGINE_BAIDU)} "
            f"baike_used={usage.get_count(usage.ENGINE_BAIKE)} "
            f"google_used={usage.get_count(usage.ENGINE_GOOGLE)}",
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
        return search_engine.baidu_web_search(query, num=num, topic=topic)

    @mcp.tool()
    def baidu_baike_search(query: str, num: int = 5) -> dict:
        """百度百科词条（lemma/get_content）。与百度网页搜索分开。受 SEARCH_BAIKE_ENABLED / 日限额约束。"""
        return search_engine.baidu_baike_search(query, num=num)

    @mcp.tool()
    def google_web_search(query: str, num: int = 5, topic: str = "") -> dict:
        """Google 网页搜索（CSE）。受 SEARCH_GOOGLE_ENABLED / 日限额约束。可选 topic。"""
        return search_engine.google_web_search(query, num=num, topic=topic)

    @mcp.tool()
    def search_quota_status() -> dict:
        """查看百度/百科/Google 搜索开关与今日已用次数（按 SEARCH_USAGE_TZ / DIGEST_TZ 自然日）。"""
        return {
            "baidu": usage.status(usage.ENGINE_BAIDU),
            "baike": usage.status(usage.ENGINE_BAIKE),
            "google": usage.status(usage.ENGINE_GOOGLE),
        }


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
