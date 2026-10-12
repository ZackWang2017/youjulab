"""抓取一篇微信公众号文章。只接受 mp.weixin.qq.com。"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import website_crawler_repository as pages


def fetch_article(url: str) -> dict[str, Any]:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host != "mp.weixin.qq.com" or not parsed.path.startswith("/s"):
        raise ValueError("只支持 https://mp.weixin.qq.com 的单篇文章。")
    return pages.fetch_page(parsed.geturl(), headers=pages.BROWSER_HEADERS)
