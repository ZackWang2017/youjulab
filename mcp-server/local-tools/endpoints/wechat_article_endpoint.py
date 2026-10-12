"""单篇公众号文章的 MCP 入口。"""
from __future__ import annotations

import wechat_article_service as article_service


def register_tools(mcp) -> None:
    @mcp.tool()
    def read_wechat_article(url: str) -> dict:
        """读取一篇 https://mp.weixin.qq.com 文章，返回标题、公众号、发布时间和分段正文。"""
        return article_service.read_wechat_article(url)
