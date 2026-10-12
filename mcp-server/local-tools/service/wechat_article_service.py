"""把一篇公众号文章整理成标题、公众号、发布时间和分段正文。"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import wechat_article_repository as article_repository

_MAX_SECTIONS = 40
_MAX_SECTION_CHARS = 2000
_BLOCKED = ("环境异常", "该内容已被发布者删除", "此内容无法查看")
_CONTENT_ID = "js_content"
_TITLE_ID = "activity-name"
_ACCOUNT_ID = "js_name"
_TIME_ID = "publish_time"


def read_wechat_article(url: str) -> dict[str, Any]:
    try:
        fetched = article_repository.fetch_article(url)
    except ValueError as exc:
        return _empty(str(exc), (url or "").strip())
    if fetched.get("error"):
        return _empty(fetched["error"], fetched.get("url") or url)
    if fetched.get("status", 0) >= 400:
        return _empty(f"文章返回 HTTP {fetched['status']}", fetched.get("url") or url)
    html = fetched.get("body") or ""
    if any(mark in html for mark in _BLOCKED):
        return _empty("微信拦截了这篇文章，没有读到正文。", fetched.get("url") or url)
    parsed = _ArticleReader(fetched.get("url") or url).parse(html)
    parsed["truncated"] = bool(fetched.get("truncated")) or parsed["truncated"]
    return parsed


class _ArticleReader(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self._skip = 0
        self._content_depth = 0
        self._capture = ""
        self._parts: list[str] = []
        self._title = ""
        self._og_title = ""
        self._account = ""
        self._published = ""
        self._in_heading = False
        self._sections: list[dict[str, Any]] = [{"heading": "", "level": 0, "parts": []}]

    def parse(self, html: str) -> dict[str, Any]:
        self.feed(html)
        self.close()
        sections = []
        overflow = False
        for section in self._sections:
            text = _collapse(" ".join(section["parts"]))
            if not text and not section["heading"]:
                continue
            if len(text) > _MAX_SECTION_CHARS:
                text = text[:_MAX_SECTION_CHARS]
                overflow = True
            sections.append({"heading": section["heading"], "level": section["level"], "text": text})
            if len(sections) >= _MAX_SECTIONS:
                overflow = True
                break
        return {
            "url": self.page_url,
            "title": self._title or self._og_title,
            "account": self._account,
            "published_at": self._published,
            "truncated": overflow,
            "sections": sections,
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "meta" and attr.get("property") == "og:title":
            self._og_title = _collapse(attr.get("content", ""))
        element_id = attr.get("id", "")
        if element_id == _CONTENT_ID:
            self._content_depth = 1
            return
        if element_id in {_TITLE_ID, _ACCOUNT_ID, _TIME_ID}:
            self._capture = element_id
            self._parts = []
        if self._content_depth:
            self._content_depth += 1
            if tag in {"h1", "h2", "h3"}:
                self._sections.append({"heading": "", "level": int(tag[1]), "parts": []})
                self._in_heading = True
            if tag in {"p", "br", "section", "li"}:
                self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if self._capture and tag in {"h1", "a", "em", "span", "div", "strong"}:
            text = _collapse("".join(self._parts))
            if self._capture == _TITLE_ID:
                self._title = text
            elif self._capture == _ACCOUNT_ID:
                self._account = text
            elif self._capture == _TIME_ID:
                self._published = text
            self._capture = ""
            self._parts = []
        if not self._content_depth:
            return
        if tag in {"h1", "h2", "h3"} and self._in_heading:
            heading = _collapse("".join(self._sections[-1]["parts"]))
            self._sections[-1]["heading"] = heading
            self._sections[-1]["parts"] = []
            self._in_heading = False
        if tag in {"p", "section", "li"}:
            self._append("\n")
        self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        if self._capture:
            self._parts.append(data)
            return
        if not self._content_depth:
            return
        if self._in_heading:
            self._sections[-1]["parts"].append(data)
            return
        self._append(data)

    def _append(self, text: str) -> None:
        self._sections[-1]["parts"].append(text)


def _empty(message: str, url: str) -> dict[str, Any]:
    return {
        "error": message,
        "url": url,
        "title": "",
        "account": "",
        "published_at": "",
        "truncated": False,
        "sections": [],
    }


def _collapse(text: str) -> str:
    return " ".join(text.split())
