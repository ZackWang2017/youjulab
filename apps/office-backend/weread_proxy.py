"""office-backend 到本地 MCP 的微信读书扫码会话代理。"""
from __future__ import annotations

import httpx

from config import mcp_access_token, mcp_http_base


def start_login() -> dict:
    return _request("POST", "/api/local-tools/weread-login")


def login_status(session_id: str) -> dict:
    return _request("GET", f"/api/local-tools/weread-login/{session_id}")


def _request(method: str, path: str) -> dict:
    try:
        response = httpx.request(
            method,
            f"{mcp_http_base()}{path}",
            headers={"Authorization": f"Bearer {mcp_access_token()}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"无法连接微信读书服务：{exc}") from exc
    if response.status_code >= 400:
        raise ValueError(f"微信读书服务返回 HTTP {response.status_code}。")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("微信读书服务返回了无效数据。") from exc
    if not isinstance(payload, dict):
        raise ValueError("微信读书服务返回了无效数据。")
    return payload
