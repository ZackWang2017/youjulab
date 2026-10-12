"""指定页面读取的 MCP 入口，挂在 /mcp/local-tools。"""
from __future__ import annotations

from contextlib import asynccontextmanager

import website_crawler_service as page_service


def register_tools(mcp) -> None:
    @mcp.tool()
    def read_web_page(url: str) -> dict:
        """读取一个具体网页，返回标题、按标题分段的正文和少量链接。不要传入整站域名，传入搜索结果里的单页 URL。"""
        return page_service.read_web_page(url)


def mount_local_mcp(host_app, local_mcp) -> None:
    """把本地工具 MCP 挂到同一进程的 /mcp/local-tools。"""
    child_app = local_mcp.streamable_http_app()
    host_app.router.routes.extend(list(child_app.routes))
    inner = host_app.router.lifespan_context
    child_life = child_app.router.lifespan_context

    @asynccontextmanager
    async def combined(scope_app):
        async with inner(scope_app):
            async with child_life(scope_app):
                yield

    host_app.router.lifespan_context = combined
