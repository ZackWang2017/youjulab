"""MCP HTTP 静态访问令牌。财务 /mcp 仍用配置的 MCP_ACCESS_TOKEN；表格入口转发用户自己的 32 位 token。"""
from __future__ import annotations

import contextvars
import os
import secrets

# 恰好 32 个字符；改 .env 里的 MCP_ACCESS_TOKEN 即可覆盖。
DEFAULT_MCP_ACCESS_TOKEN = "invpdevstatic32tokenpleasechange"


def bearer_token(header: str) -> str:
    if not (header or "").lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()


def is_sheet_path(path: str) -> bool:
    return path == "/mcp/sheets" or path.startswith("/mcp/sheets/") or path.startswith("/api/spreadsheets")


def is_document_path(path: str) -> bool:
    return path == "/mcp/documents" or path.startswith("/mcp/documents/") or path.startswith("/api/documents")


def is_user_office_path(path: str) -> bool:
    return is_sheet_path(path) or is_document_path(path)


_incoming_access_token: contextvars.ContextVar[str] = contextvars.ContextVar("incoming_access_token", default="")


def set_incoming_access_token(token: str) -> contextvars.Token:
    return _incoming_access_token.set(token)


def reset_incoming_access_token(token: contextvars.Token) -> None:
    _incoming_access_token.reset(token)


def incoming_access_token() -> str:
    return _incoming_access_token.get()


def mcp_access_token() -> str:
    for key in ("MCP_ACCESS_TOKEN", "FINANCE_MCP_TOKEN"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return DEFAULT_MCP_ACCESS_TOKEN


def bearer_matches(header: str) -> bool:
    if not header.lower().startswith("bearer "):
        return False
    provided = header.split(" ", 1)[1].strip()
    expected = mcp_access_token()
    if len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)
