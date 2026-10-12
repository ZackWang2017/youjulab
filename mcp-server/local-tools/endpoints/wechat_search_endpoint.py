"""搜狗微信文章搜索的 MCP 入口。"""
from __future__ import annotations

import wechat_search_service as search_service


def register_tools(mcp) -> None:
    @mcp.tool()
    def search_wechat_articles(query: str, num: int = 10) -> dict:
        """按关键词搜索微信公众号文章，只返回搜狗第一页。结果含标题、公众号、时间和链接。"""
        return search_service.search_wechat_articles(query, num=num)
