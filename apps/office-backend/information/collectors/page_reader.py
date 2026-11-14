"""Bounded local article extraction for information analysis."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

import web_document

_MAX_SECTIONS = 40
_MAX_SECTION_CHARS = 2000
_MAX_NODES = 20_000
_SKIP = "script,style,noscript,iframe,object,embed,svg,canvas,nav,footer,header,aside,form,button,template"


def read_page(url: str) -> dict:
    """Return title, sections ({heading, level, text}), and error."""
    try:
        html, final_url = web_document.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        if len(soup.find_all(True)) > _MAX_NODES:
            raise ValueError("网页节点过多，暂不支持读取。")
        title_node = soup.select_one("#activity-name") or soup.find("h1") or soup.title
        meta = soup.select_one('meta[property="og:title"]')
        title = _plain(title_node.get_text(" ", strip=True)) if title_node else ""
        title = (title or (str(meta.get("content") or "") if meta else ""))[:500]
        if re.search(r"captcha|access denied|just a moment|验证后继续|访问过于频繁|环境异常", title, re.I):
            raise ValueError("网站返回了验证或访问限制页面。")
        root = soup.select_one("#js_content")
        if urlsplit(final_url).hostname == "mp.weixin.qq.com" and (root is None or len(root.get_text(strip=True)) < 20):
            raise ValueError("未读取到公众号正文，文章可能已删除或需要验证码。")
        for node in soup.select(_SKIP):
            if node.parent:
                node.decompose()
        if root is None:
            candidates = soup.select('article,main,[itemprop="articleBody"],.article-content,.entry-content,.post-content')
            root = max(candidates, key=lambda node: len(node.get_text(" ", strip=True)), default=None)
        if root is None:
            root = soup.body or soup
        sections = []
        heading, level, parts = "", 0, []
        def flush() -> None:
            text = _plain(" ".join(parts))
            if text or heading:
                sections.append({"heading": heading, "level": level, "text": text[:_MAX_SECTION_CHARS]})
            parts.clear()
        for node in root.descendants:
            if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name or ""):
                flush()
                if len(sections) >= _MAX_SECTIONS:
                    break
                heading, level = _plain(node.get_text(" ", strip=True))[:500], int(node.name[1])
            elif isinstance(node, NavigableString) and not any(re.fullmatch(r"h[1-6]", parent.name or "") for parent in node.parents if parent is not root):
                parts.append(str(node))
        else:
            flush()
        if not any(section["text"] for section in sections):
            raise ValueError("未找到可读取的文章正文。")
        return {"title": title, "sections": sections[:_MAX_SECTIONS], "error": ""}
    except (ValueError, OSError) as exc:
        return {"title": "", "sections": [], "error": str(exc)}


def _plain(text: str) -> str:
    return " ".join(text.split())
