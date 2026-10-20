"""把办公窗口的持仓管理请求转到 mcp-server。

组合、持仓、行情、净值、计划任务全部在 mcp-server 的 /api/portfolio 与 /api/scheduler 实现；
这里只负责：登录用户 → X-Office-User-Id 头，静态服务令牌，原样转发请求体与查询参数。
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from config import mcp_access_token, mcp_http_base

_ALLOWED_PREFIXES = ("portfolios", "overview", "scheduler/tasks")
_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


def _target(path: str) -> str:
    clean = (path or "").strip("/")
    if not clean or not clean.startswith(_ALLOWED_PREFIXES):
        raise HTTPException(404, "持仓接口不存在。")
    if clean.startswith("scheduler/"):
        return f"{mcp_http_base()}/api/{clean}"
    return f"{mcp_http_base()}/api/portfolio/{clean}"


async def forward(method: str, path: str, *, user_id: str, query: str = "", body: bytes = b"") -> tuple[int, dict | list]:
    url = _target(path)
    if query:
        url = f"{url}?{query}"
    headers = {
        "Authorization": f"Bearer {mcp_access_token()}",
        "X-Office-User-Id": user_id,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method.upper(), url, headers=headers, content=body or None)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"持仓服务不可用：{exc}") from exc
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        raise HTTPException(502, "持仓服务返回了无法解析的响应。") from None
    if response.status_code >= 400:
        detail = payload.get("error") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or f"持仓服务错误 HTTP {response.status_code}")
    return response.status_code, payload
