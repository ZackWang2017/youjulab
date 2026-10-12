"""把单个页面整理成模型可读的 JSON。不回传 HTML 或 DOM。"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import website_crawler_repository as page_repository

_MAX_SECTIONS = 40
_MAX_SECTION_CHARS = 2000
_MAX_LINKS = 20
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template"}
_NOISE_TAGS = {"nav", "header", "footer", "aside"}
_BLOCK_TAGS = {"p", "li", "div", "section", "article", "h1", "h2", "h3", "br", "tr"}


def read_web_page(url: str) -> dict[str, Any]:
    """抓取一个具体页面，返回标题、分段正文和少量链接。"""
    try:
        fetched = page_repository.fetch_page(url)
    except ValueError as exc:
        return {"error": str(exc), "url": (url or "").strip(), "title": "", "sections": [], "links": []}
    if fetched.get("error"):
        return {
            "error": fetched["error"],
            "url": fetched.get("url") or url,
            "title": "",
            "sections": [],
            "links": [],
        }
    if fetched["status"] >= 400:
        return {
            "error": f"页面返回 HTTP {fetched['status']}",
            "url": fetched["url"],
            "title": "",
            "sections": [],
            "links": [],
        }
    content_type = fetched.get("content_type") or ""
    if content_type and "html" not in content_type and "xml" not in content_type:
        text = _collapse(fetched.get("body") or "")[:_MAX_SECTION_CHARS]
        return {
            "url": fetched["url"],
            "title": "",
            "description": "",
            "truncated": bool(fetched.get("truncated")),
            "sections": [{"heading": "", "level": 0, "text": text}] if text else [],
            "links": [],
        }
    parsed = _PageReader(fetched["url"]).parse(fetched.get("body") or "")
    parsed["truncated"] = bool(fetched.get("truncated")) or parsed["truncated"]
    return parsed


class _PageReader(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self._skip = 0
        self._noise = 0
        self._in_title = False
        self._in_heading = False
        self._title: list[str] = []
        self._description = ""
        self._sections: list[dict[str, Any]] = [{"heading": "", "level": 0, "parts": []}]
        self._href = ""
        self._link_parts: list[str] = []
        self._capturing_link = False
        self._links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()

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
            "title": _collapse("".join(self._title)),
            "description": _collapse(self._description),
            "truncated": overflow,
            "sections": sections,
            "links": self._links[:_MAX_LINKS],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _NOISE_TAGS:
            self._noise += 1
        if self._skip or self._noise:
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta" and attr.get("name", "").lower() == "description":
            self._description = attr.get("content", "")
        if tag in {"h1", "h2", "h3"}:
            self._sections.append({"heading": "", "level": int(tag[1]), "parts": []})
            self._in_heading = True
        if tag == "a" and attr.get("href"):
            self._capturing_link = True
            self._href = urljoin(self.page_url, attr["href"])
            self._link_parts = []
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in _NOISE_TAGS and self._noise:
            self._noise -= 1
        if self._skip or self._noise:
            return
        if tag == "title":
            self._in_title = False
        if tag in {"h1", "h2", "h3"}:
            heading = _collapse("".join(self._sections[-1]["parts"]))
            self._sections[-1]["heading"] = heading
            self._sections[-1]["parts"] = []
            self._in_heading = False
        if tag == "a" and self._capturing_link:
            text = _collapse("".join(self._link_parts))
            href = self._href
            self._capturing_link = False
            if href.startswith(("http://", "https://")) and href not in self._seen_links and len(self._links) < _MAX_LINKS:
                self._seen_links.add(href)
                self._links.append({"text": text, "href": href})
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip or self._noise or not data:
            return
        if self._in_title:
            self._title.append(data)
            return
        if self._capturing_link:
            self._link_parts.append(data)
        if self._in_heading:
            self._sections[-1]["parts"].append(data)
            return
        self._append(data)

    def _append(self, text: str) -> None:
        if not self._sections:
            return
        self._sections[-1]["parts"].append(text)


def _collapse(text: str) -> str:
    return " ".join(text.split())
