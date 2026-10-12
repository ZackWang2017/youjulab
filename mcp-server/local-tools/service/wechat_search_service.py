"""把搜狗微信搜索第一页整理成文章列表。不回传 HTML。"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import wechat_search_repository as search_repository

_MAX_RESULTS = 10
_ORIGIN = "https://weixin.sogou.com"
_BLOCKED = ("请输入验证码", "antispider", "访问过于频繁")
_MP_URL = re.compile(r"https?://mp\.weixin\.qq\.com/s\S*")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def search_wechat_articles(query: str, num: int = 10) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "query": q, "results": []}
    try:
        fetched = search_repository.fetch_search_page(q)
    except ValueError as exc:
        return {"error": str(exc), "query": q, "results": []}
    if fetched.get("error"):
        return {"error": fetched["error"], "query": q, "results": []}
    if fetched.get("status", 0) >= 400:
        return {"error": f"搜狗搜索返回 HTTP {fetched['status']}", "query": q, "results": []}
    html = fetched.get("body") or ""
    if any(mark in html for mark in _BLOCKED):
        return {"error": "搜狗微信搜索要求验证，本次没有结果。", "query": q, "results": []}
    results = _SogouList().parse(html)[:_clamp(num)]
    return {"query": q, "results": results}


class _SogouList(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._list_depth = 0
        self._meta_depth = 0
        self._current: dict[str, str] | None = None
        self._capture = ""
        self._parts: list[str] = []
        self._href = ""
        self.items: list[dict[str, str]] = []

    def parse(self, html: str) -> list[dict[str, str]]:
        self.feed(html)
        self.close()
        return [item for item in self.items if item["title"] or item["url"]]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        classes = attr.get("class", "")
        if tag == "ul" and "news-list" in classes.split():
            self._list_depth += 1
        if not self._list_depth:
            return
        if tag == "li":
            self._current = {"title": "", "account": "", "published_at": "", "url": "", "url_kind": "sogou", "snippet": ""}
            self.items.append(self._current)
            self._meta_depth = 0
        if self._current is None:
            return
        if tag == "div" and "s-p" in classes.split():
            self._meta_depth = 1
            stamp = attr.get("t", "")
            if stamp.isdigit():
                when = datetime.fromtimestamp(int(stamp), _SHANGHAI)
                self._current["published_at"] = when.strftime("%Y-%m-%d %H:%M")
        elif self._meta_depth and tag == "div":
            self._meta_depth += 1
        if tag == "a":
            self._href = urljoin(_ORIGIN + "/", attr.get("href", ""))
            if self._meta_depth:
                self._begin("account")
        if tag == "h3":
            self._begin("title")
        if tag == "p" and "txt-info" in classes.split():
            self._begin("snippet")
        if tag == "span" and "all-time-y2" in classes.split() and not self._current["published_at"]:
            self._begin("published_at")

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul" and self._list_depth:
            self._list_depth -= 1
        if tag == "div" and self._meta_depth:
            self._meta_depth -= 1
        if tag in {"a", "h3", "p", "span"} and self._capture:
            text = _collapse("".join(self._parts))
            if self._capture == "title" and self._current is not None:
                self._current["title"] = text or self._current["title"]
                self._assign_url(self._href)
            elif self._capture == "account" and self._current is not None and text:
                self._current["account"] = text
            elif self._capture == "snippet" and self._current is not None:
                self._current["snippet"] = text
            elif self._capture == "published_at" and self._current is not None and text:
                self._current["published_at"] = text
            self._capture = ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture and data:
            self._parts.append(data)

    def _begin(self, field: str) -> None:
        if self._capture:
            return
        self._capture = field
        self._parts = []

    def _assign_url(self, href: str) -> None:
        if self._current is None or not href or self._current["url"]:
            return
        matched = _MP_URL.search(href)
        if matched:
            self._current["url"] = matched.group(0).rstrip(").,，")
            self._current["url_kind"] = "wechat"
            return
        self._current["url"] = href
        self._current["url_kind"] = "sogou"


def _clamp(num: int) -> int:
    try:
        count = int(num)
    except (TypeError, ValueError):
        count = _MAX_RESULTS
    return max(1, min(_MAX_RESULTS, count))


def _collapse(text: str) -> str:
    return " ".join(text.split())
