"""拉取指定公众号的 RSS 列表。地址由 service 拼好。"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import website_crawler_repository as pages

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TAG = re.compile(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>|<[^>]+>")


def fetch_feed(url: str) -> dict[str, Any]:
    fetched = pages.fetch_page(url, headers=pages.BROWSER_HEADERS)
    if fetched.get("error"):
        return {"error": fetched["error"], "url": fetched.get("url") or url, "items": []}
    if fetched.get("status", 0) >= 400:
        return {"error": f"公众号列表返回 HTTP {fetched['status']}", "url": fetched.get("url") or url, "items": []}
    try:
        items = _parse_rss(fetched.get("body") or "")
    except ElementTree.ParseError as exc:
        return {"error": f"公众号列表不是 RSS：{exc}", "url": fetched.get("url") or url, "items": []}
    return {"url": fetched.get("url") or url, "items": items}


def _parse_rss(xml: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml.strip() or "<rss/>")
    items: list[dict[str, str]] = []
    for node in root.iter():
        if _local(node.tag) != "item":
            continue
        fields = {_local(child.tag): "".join(child.itertext()) for child in list(node)}
        title = _collapse(fields.get("title", ""))
        link = _collapse(fields.get("link", ""))
        summary = _plain(fields.get("description") or fields.get("encoded") or "")
        published = _format_time(fields.get("pubDate", ""))
        if not title and not link:
            continue
        items.append({"title": title, "url": link, "published_at": published, "summary": summary[:500]})
    return items


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _plain(text: str) -> str:
    return _collapse(_TAG.sub(" ", text or ""))


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def _format_time(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return text
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo("UTC"))
    return when.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")
