"""RSS、网页和 RSSHub 路由的拉取。RSSHub 根地址只在这里读取。"""
from __future__ import annotations

import os
import re
from html import unescape
from typing import Any

import httpx

MAX_ITEMS_PER_SOURCE = 8
MAX_SUMMARY = 800
_TIMEOUT = httpx.Timeout(20.0, read=40.0)


def collect_sources(sources: list) -> dict[str, Any]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        try:
            for item in _fetch_source(source):
                key = (item.get("url") or "") + "|" + (item.get("title") or "")
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        except Exception as exc:
            errors.append(str(exc))
    if errors and not items:
        return {"items": [], "error": "；".join(errors[:3])}
    return {"items": items, "error": ""}


def _fetch_source(source: dict) -> list[dict[str, Any]]:
    kind = (source.get("kind") or "rss").strip().lower()
    label = (source.get("label") or "").strip()
    if kind == "rsshub":
        url = _resolve_rsshub(source.get("route") or source.get("url") or "")
        items = _fetch_rss(url)
    elif kind == "url":
        url = (source.get("url") or "").strip()
        if not url:
            raise ValueError("网址来源缺少 url。")
        items = _fetch_url_page(url)
    else:
        url = (source.get("url") or "").strip()
        if not url:
            raise ValueError("RSS 来源缺少 url。")
        items = _fetch_rss(url)
    for item in items:
        item["source_label"] = label or url
        item["source_kind"] = kind
    return items


def _resolve_rsshub(route: str) -> str:
    base = (os.environ.get("DIGEST_RSSHUB_BASE") or "").strip().rstrip("/")
    path = (route or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not base:
        raise ValueError("未配置 DIGEST_RSSHUB_BASE，无法使用 RSSHub 路由。")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _fetch_rss(url: str) -> list[dict[str, Any]]:
    try:
        import feedparser
    except ImportError as exc:
        raise ValueError("缺少 feedparser 依赖，请安装后重试。") from exc
    parsed = feedparser.parse(url)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError(f"无法解析 RSS：{url}")
    items: list[dict[str, Any]] = []
    for entry in (parsed.entries or [])[:MAX_ITEMS_PER_SOURCE]:
        title = (getattr(entry, "title", None) or "").strip() or "（无标题）"
        link = (getattr(entry, "link", None) or "").strip()
        summary = _strip_html(getattr(entry, "summary", None) or getattr(entry, "description", None) or "")
        items.append({
            "title": title,
            "url": link,
            "summary": summary[:MAX_SUMMARY],
            "source_url": url,
        })
    return items


def _fetch_url_page(url: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        res = client.get(url, headers={"User-Agent": "office-digest/1.0"})
        if res.is_error:
            raise ValueError(f"拉取网址失败 HTTP {res.status_code}：{url}")
        final_url = str(res.url)
        html = res.text
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = _strip_html(title_match.group(1) if title_match else "") or url
    body = ""
    for pattern in (r"(?is)<article[^>]*>(.*?)</article>", r"(?is)<main[^>]*>(.*?)</main>"):
        found = re.search(pattern, html)
        if found:
            body = _strip_html(found.group(1))
            break
    if not body:
        body = _strip_html(html)[:MAX_SUMMARY * 2]
    return [{
        "title": title[:200],
        "url": final_url,
        "summary": body[:MAX_SUMMARY],
        "source_url": url,
    }]


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()
