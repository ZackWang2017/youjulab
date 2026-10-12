"""把办公窗口的股票查询转到 mcp-server。证券匹配在 mcp-server 完成。"""
from __future__ import annotations

import httpx

from config import mcp_access_token, mcp_http_base


def lookup_securities(query: str) -> list[dict]:
    text = (query or "").strip()
    if not text:
        return []
    try:
        response = httpx.get(
            f"{mcp_http_base()}/api/financial/securities/search",
            params={"q": text, "limit": 8},
            headers={"Authorization": f"Bearer {mcp_access_token()}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"股票查询失败：{exc}") from exc
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error") or ""
        except Exception:
            detail = response.text[:120]
        raise ValueError(detail or f"股票查询失败 HTTP {response.status_code}")
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    items = (data or {}).get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("股票查询结果无法解析。")
    return [
        {"ticker_code": str(item.get("ticker_code") or ""), "name": str(item.get("name") or "")}
        for item in items
        if isinstance(item, dict) and item.get("ticker_code") and item.get("name")
    ]
