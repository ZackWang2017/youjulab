"""财经 RSS 适配器的抓取。优先用各站公开接口或页面，不依赖 RSSHub。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

from ..collectors import feed_service
import httpx

MAX_ITEMS = 24
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TIMEOUT = httpx.Timeout(20.0, read=30.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*; q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_TAGS = re.compile(r"<[^>]+>")
_JIN10_TITLE = re.compile(r"^【(.+?)】")
_STATE = re.compile(r"window\.initialState\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)


def fetch_adapter(meta: dict[str, Any]) -> list[dict[str, str]]:
    adapter_id = str(meta.get("id") or "")
    if adapter_id == "cls":
        return fetch_cls()
    if adapter_id == "jin10":
        return fetch_jin10()
    if adapter_id == "caixin":
        return fetch_caixin()
    if adapter_id == "stcn":
        return fetch_stcn()
    if adapter_id == "36kr":
        return fetch_36kr()
    if meta.get("kind") == "rsshub":
        return _via_feed([{"kind": "rsshub", "route": meta.get("route") or "", "label": meta.get("name") or ""}])
    if meta.get("url"):
        return _via_feed([{"kind": "rss", "url": meta.get("url") or "", "label": meta.get("name") or ""}])
    raise ValueError(f"未知财经 RSS 适配器：{adapter_id or '（空）'}")


def fetch_cls() -> list[dict[str, str]]:
    payload = _get_json(
        "https://www.cls.cn/api/cache",
        params={"app": "CailianpressWeb", "name": "telegraph", "os": "web", "sv": "8.7.9"},
        headers={"Referer": "https://www.cls.cn/telegraph"},
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    rows = []
    if isinstance(data, dict):
        for key in ("roll_data", "telegraph", "roll", "depth_list"):
            found = data.get(key)
            if isinstance(found, list):
                rows = found
                break
    items: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("title") or row.get("brief") or row.get("content"))
        if not title:
            continue
        item_id = str(row.get("id") or "").strip()
        items.append(_item(
            title,
            f"https://www.cls.cn/detail/{item_id}" if item_id else "https://www.cls.cn/telegraph",
            _stamp(row.get("ctime")),
            _plain(row.get("brief") or row.get("content")),
        ))
        if len(items) >= MAX_ITEMS:
            break
    return items


def fetch_jin10() -> list[dict[str, str]]:
    payload = _get_json(
        "https://flash-api.jin10.com/get_flash_list",
        params={"channel": "-8200", "vip": "1"},
        headers={
            "Referer": "https://www.jin10.com/",
            "Origin": "https://www.jin10.com",
            "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0",
        },
    )
    rows = payload.get("data") if isinstance(payload, dict) else []
    items: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("type") == 1:
            continue
        body = row.get("data") if isinstance(row.get("data"), dict) else {}
        content = _plain(body.get("content") or body.get("title") or body.get("vip_title"))
        if not content:
            continue
        matched = _JIN10_TITLE.match(content)
        title = matched.group(1) if matched else content
        summary = content[matched.end():].strip() if matched else content
        url = _plain(body.get("link") or body.get("source_link")) or "https://www.jin10.com/"
        items.append(_item(title, url, _stamp(row.get("time")), summary))
        if len(items) >= MAX_ITEMS:
            break
    return items


def fetch_caixin() -> list[dict[str, str]]:
    payload = _get_json("https://gateway.caixin.com/api/dataplatform/scroll/index")
    data = payload.get("data") if isinstance(payload, dict) else {}
    rows = (data or {}).get("articleList") if isinstance(data, dict) else []
    items: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("title"))
        url = _plain(row.get("url"))
        if not title or not url or url.startswith(("https://fm.caixin.com/", "https://video.caixin.com/")):
            continue
        items.append(_item(title, url, _stamp(row.get("time") or row.get("updateTime")), _plain(row.get("summary"))))
        if len(items) >= MAX_ITEMS:
            break
    return items


def fetch_stcn() -> list[dict[str, str]]:
    html = _get_text("https://www.stcn.com/article/list/yw.html", headers={"Referer": "https://www.stcn.com/"})
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<div class="tt">\s*<a href="(/article/detail/\d+\.html)"[^>]*>\s*([^<]+?)\s*</a>',
        html,
    ):
        path = match.group(1)
        title = _plain(match.group(2))
        if not title or path in seen:
            continue
        seen.add(path)
        snippet = html[match.end():match.end() + 2500]
        summary_match = re.search(r'<div class="text[^"]*">\s*<a[^>]*>\s*([^<]+)', snippet)
        clock = re.search(r'<div class="info[^"]*">[\s\S]*?<span>(\d{1,2}:\d{2})</span>', snippet)
        items.append(_item(
            title,
            "https://www.stcn.com" + path,
            _clock_today(clock.group(1) if clock else ""),
            _plain(summary_match.group(1) if summary_match else ""),
        ))
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("证券时报要闻列表没有解析到条目。")
    return items


def fetch_36kr() -> list[dict[str, str]]:
    html = _get_text("https://www.36kr.com/newsflashes", headers={"Referer": "https://www.36kr.com/"})
    items = _parse_36kr_state(html) or _parse_36kr_html(html)
    if not items:
        raise ValueError("36氪快讯没有解析到条目。")
    return items[:MAX_ITEMS]


def _parse_36kr_state(html: str) -> list[dict[str, str]]:
    match = _STATE.search(html or "")
    if not match:
        return []
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    rows = (
        ((state.get("newsflashCatalogData") or {}).get("data") or {})
        .get("newsflashList") or {}
    ).get("data") or []
    items: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        material = row.get("templateMaterial") if isinstance(row.get("templateMaterial"), dict) else {}
        title = _plain(material.get("widgetTitle") or row.get("widgetTitle"))
        item_id = str(material.get("itemId") or row.get("itemId") or "").strip()
        if not title:
            continue
        items.append(_item(
            title,
            f"https://www.36kr.com/newsflashes/{item_id}" if item_id else "https://www.36kr.com/newsflashes",
            _stamp(material.get("publishTime")),
            _plain(material.get("widgetContent")),
        ))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _parse_36kr_html(html: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<a class="item-title"[^>]+href="(/newsflashes/\d+)"[^>]*>([^<]+)</a>',
        html or "",
    ):
        path = match.group(1)
        title = _plain(match.group(2))
        if not title or path in seen:
            continue
        seen.add(path)
        snippet = html[match.end():match.end() + 400]
        rel = re.search(r'<span class="time">([^<]+)</span>', snippet)
        items.append(_item(title, "https://www.36kr.com" + path, _relative_stamp(rel.group(1) if rel else "") or _clock_today(""), ""))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _via_feed(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    payload = feed_service.collect_sources(sources)
    if payload.get("error") and not payload.get("items"):
        raise ValueError(payload["error"])
    items: list[dict[str, str]] = []
    for row in payload.get("items") or []:
        title = _plain(row.get("title"))
        if not title:
            continue
        items.append(_item(title, _plain(row.get("url")), _plain(row.get("published_at")), _plain(row.get("summary"))))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _get_json(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = httpx.get(url, params=params, headers={**_HEADERS, **(headers or {})}, timeout=_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("返回结果不是 JSON。")
    return payload


def _get_text(url: str, headers: dict[str, str] | None = None) -> str:
    response = httpx.get(url, headers={**_HEADERS, **(headers or {})}, timeout=_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _item(title: str, url: str, published_at: str, summary: str) -> dict[str, str]:
    return {"title": title, "url": url, "published_at": published_at, "summary": summary[:800]}


def _plain(value: Any) -> str:
    return " ".join(_TAGS.sub("", unescape(str(value or ""))).split())


def _now() -> datetime:
    return datetime.now(_SHANGHAI)


def _clock_today(hhmm: str) -> str:
    clock = (hhmm or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", clock):
        clock = "00:00"
    return f"{_now().strftime('%Y-%m-%d')} {clock}"


def _relative_stamp(text: str) -> str:
    raw = (text or "").strip()
    now = _now()
    if raw in ("刚刚", "刚才"):
        return now.strftime("%Y-%m-%d %H:%M")
    matched = re.fullmatch(r"(\d+)\s*分钟前", raw)
    if matched:
        return (now - timedelta(minutes=int(matched.group(1)))).strftime("%Y-%m-%d %H:%M")
    matched = re.fullmatch(r"(\d+)\s*小时前", raw)
    if matched:
        return (now - timedelta(hours=int(matched.group(1)))).strftime("%Y-%m-%d %H:%M")
    matched = re.fullmatch(r"(\d+)\s*天前", raw)
    if matched:
        return (now - timedelta(days=int(matched.group(1)))).strftime("%Y-%m-%d %H:%M")
    if raw.startswith("昨天"):
        clock = re.search(r"(\d{1,2}:\d{2})", raw)
        return f"{(now.date() - timedelta(days=1)).isoformat()} {clock.group(1) if clock else '00:00'}"
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        return _clock_today(raw)
    return ""


def _stamp(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:16]
    try:
        stamp = float(text)
    except ValueError:
        return text
    if stamp > 10_000_000_000:
        stamp = stamp / 1000
    try:
        return datetime.fromtimestamp(stamp, _SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""

