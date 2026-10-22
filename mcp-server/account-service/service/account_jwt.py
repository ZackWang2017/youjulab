"""用户 JWT（HS256）。桌面与远程调用用它代替静态 MCP_ACCESS_TOKEN。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from contextvars import ContextVar

_claims: ContextVar[dict | None] = ContextVar("account_claims", default=None)


def secret() -> str:
    return (os.environ.get("ACCOUNT_JWT_SECRET") or "account-dev-secret-please-change").strip() or "account-dev-secret-please-change"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def issue(sub: str, email: str, *, days: int = 7, kind: str = "session") -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "kind": kind,
        "iat": now,
        "exp": now + days * 86400,
    }
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret().encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def verify(token: str) -> dict | None:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    header, body, sig = parts
    expected = hmac.new(secret().encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    try:
        provided = _unb64(sig)
    except (ValueError, UnicodeEncodeError):
        return None
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeEncodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return payload


def verify_bearer(header: str) -> dict | None:
    if not (header or "").lower().startswith("bearer "):
        return None
    return verify(header.split(" ", 1)[1].strip())


def set_claims(claims: dict | None):
    return _claims.set(claims)


def reset_claims(token) -> None:
    _claims.reset(token)


def current_claims() -> dict | None:
    return _claims.get()


def current_sub() -> str:
    claims = current_claims()
    if isinstance(claims, dict) and isinstance(claims.get("sub"), str):
        return claims["sub"]
    return ""
