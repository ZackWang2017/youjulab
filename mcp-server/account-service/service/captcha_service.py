"""登录验证码。一期放进程内存，过期即丢。"""
from __future__ import annotations

import base64
import secrets
import time
from html import escape

_TTL = 180
_store: dict[str, tuple[str, float]] = {}


def _purge(now: float) -> None:
    stale = [key for key, (_, exp) in _store.items() if exp < now]
    for key in stale:
        _store.pop(key, None)


def issue() -> dict:
    now = time.time()
    _purge(now)
    code = f"{secrets.randbelow(9000) + 1000}"
    captcha_id = secrets.token_hex(8)
    _store[captcha_id] = (code, now + _TTL)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='40'>"
        "<rect width='120' height='40' fill='#f4f7f6'/>"
        f"<text x='16' y='28' font-size='24' font-family='monospace' fill='#168274'>{escape(code)}</text>"
        "</svg>"
    )
    image = base64.b64encode(svg.encode()).decode()
    return {"captcha_id": captcha_id, "image_base64": image, "image_type": "image/svg+xml"}


def check(captcha_id: str, code: str) -> bool:
    now = time.time()
    _purge(now)
    item = _store.pop((captcha_id or "").strip(), None)
    if not item:
        return False
    expected, exp = item
    if exp < now:
        return False
    return secrets.compare_digest(expected, (code or "").strip())
