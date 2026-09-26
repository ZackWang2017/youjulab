"""每日资讯：RSS / URL / RSSHub 拉取。"""
from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from config import digest_rsshub_base

MAX_ITEMS_PER_SOURCE = 8
MAX_SUMMARY = 800
TIMEOUT = httpx.Timeout(20.0, read=40.0)


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _resolve_rsshub(route: str) -> str:
    base = digest_rsshub_base()
    path = (route or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not base:
        raise ValueError("未配置 DIGEST_RSSHUB_BASE，无法使用 RSSHub 路由。")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def fetch_rss(url: str) -> list[dict[str, Any]]:
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


def fetch_url_page(url: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
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


def fetch_source(source: dict) -> list[dict[str, Any]]:
    kind = (source.get("kind") or "rss").strip().lower()
    label = (source.get("label") or "").strip()
    if kind == "rsshub":
        url = _resolve_rsshub(source.get("route") or source.get("url") or "")
        items = fetch_rss(url)
    elif kind == "url":
        url = (source.get("url") or "").strip()
        if not url:
            raise ValueError("网址来源缺少 url。")
        items = fetch_url_page(url)
    else:
        url = (source.get("url") or "").strip()
        if not url:
            raise ValueError("RSS 来源缺少 url。")
        items = fetch_rss(url)
    for item in items:
        item["source_label"] = label or url
        item["source_kind"] = kind
    return items


def collect_topic_items(topic: dict) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in topic.get("sources") or []:
        try:
            for item in fetch_source(source):
                key = (item.get("url") or "") + "|" + (item.get("title") or "")
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
        except Exception as exc:
            errors.append(str(exc))
    if errors and not out:
        raise ValueError("；".join(errors[:3]))
    return out
