"""抓取搜狗微信文章搜索的第一页。"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import website_crawler_repository as pages

_SEARCH_URL = "https://weixin.sogou.com/weixin?type=2&query={query}"


def fetch_search_page(query: str) -> dict[str, Any]:
    url = _SEARCH_URL.format(query=quote((query or "").strip(), safe=""))
    return pages.fetch_page(url, headers=pages.BROWSER_HEADERS)
