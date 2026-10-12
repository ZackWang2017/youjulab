"""每日资讯的来源拉取：转给 mcp-server，不在办公进程里抓 RSS。"""
from __future__ import annotations

from typing import Any

import httpx

from config import mcp_access_token, mcp_http_base


def collect_topic_items(topic: dict) -> list[dict[str, Any]]:
    sources = topic.get("sources") or []
    if not sources:
        return []
    try:
        response = httpx.post(
            f"{mcp_http_base()}/api/local-tools/feed",
            json={"sources": sources},
            headers={"Authorization": f"Bearer {mcp_access_token()}"},
            timeout=45.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"拉取资讯失败：{exc}") from exc
    if response.status_code >= 400:
        raise ValueError(f"拉取资讯失败 HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise ValueError("拉取资讯结果无法解析。") from exc
    if not isinstance(payload, dict):
        raise ValueError("拉取资讯结果无法解析。")
    if payload.get("error"):
        raise ValueError(str(payload["error"]))
    items = payload.get("items") or []
    return items if isinstance(items, list) else []
