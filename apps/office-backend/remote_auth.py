"""本机索引时转发给远程 mcp 的用户 JWT。"""
from __future__ import annotations

from contextvars import ContextVar

_token: ContextVar[str] = ContextVar("remote_user_jwt", default="")


def set_remote_token(token: str | None) -> None:
    _token.set((token or "").strip())


def remote_token() -> str:
    return _token.get()
