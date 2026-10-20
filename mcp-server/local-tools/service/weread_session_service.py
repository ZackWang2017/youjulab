"""微信读书登录态和按需发起的扫码登录会话。"""
from __future__ import annotations

import base64
import io
import threading
import time
import uuid
from typing import Any

import weread_session_repository as session_repository

_lock = threading.Lock()
_login_sessions: dict[str, dict[str, Any]] = {}
_ACTIVE_TTL_SECONDS = 5 * 60


def list_shelf_articles(num: int) -> dict:
    """重新读取书架上的全部公众号，再取每个号的最近文章。"""
    count = max(1, num)
    cookie = session_repository.load_cookie()
    if not cookie:
        return _login_required("微信读书尚未登录。")
    shelf = session_repository.list_official_accounts(cookie)
    if shelf.get("auth_expired"):
        session_repository.save_cookie("")
        return _login_required("微信读书登录已过期。")
    if shelf.get("error"):
        return {"error": shelf["error"], "accounts": []}
    books = shelf.get("accounts") or []
    if not books:
        return {"error": "书架上没有公众号。", "accounts": []}
    accounts = []
    for book in books:
        fetched = session_repository.latest_articles(cookie, book["id"])
        if fetched.get("auth_expired"):
            session_repository.save_cookie("")
            return _login_required("微信读书登录已过期。")
        items = (fetched.get("items") or [])[:count]
        accounts.append({
            "id": book["id"],
            "title": book["title"],
            "error": "" if items else (fetched.get("error") or "没有文章。"),
            "results": items,
        })
    return {"error": "", "accounts": accounts}


def list_articles(book_id: str, num: int) -> dict:
    cookie = session_repository.load_cookie()
    if not cookie:
        return _login_required("微信读书尚未登录。", results=True)
    fetched = session_repository.latest_articles(cookie, book_id)
    if fetched.get("auth_expired"):
        session_repository.save_cookie("")
        return _login_required("微信读书登录已过期。", results=True)
    items = (fetched.get("items") or [])[: max(1, num)]
    if fetched.get("error") and not items:
        return {"error": fetched["error"], "results": []}
    return {"error": "", "results": items}


def start_login() -> dict:
    """仅在用户明确请求读取且登录失效时，创建一个扫码会话。"""
    cookie = session_repository.load_cookie()
    if cookie and session_repository.session_valid(cookie):
        return {"status": "authenticated", "session_id": "", "qr_data_url": ""}
    with _lock:
        _prune_sessions()
        pending = next((item for item in _login_sessions.values() if item["status"] == "pending"), None)
        if pending:
            return _public_session(pending)
        session = {
            "id": uuid.uuid4().hex,
            "status": "pending",
            "qr_url": "",
            "error": "",
            "updated_at": time.time(),
        }
        _login_sessions[session["id"]] = session
        threading.Thread(target=_login, args=(session["id"],), name="weread-login", daemon=True).start()
        return _public_session(session)


def login_status(session_id: str) -> dict:
    with _lock:
        _prune_sessions()
        session = _login_sessions.get(session_id)
        if not session:
            return {"status": "expired", "session_id": session_id, "qr_data_url": "", "error": "扫码会话已过期，请重新获取数据。"}
        return _public_session(session)


def _login(session_id: str) -> None:
    def on_qr(url: str) -> None:
        with _lock:
            session = _login_sessions.get(session_id)
            if not session:
                return
            session["qr_url"] = url
            session["updated_at"] = time.time()

    cookie = session_repository.login(on_qr)
    with _lock:
        session = _login_sessions.get(session_id)
        if not session:
            return
        session["status"] = "authenticated" if cookie else "failed"
        session["error"] = "" if cookie else "扫码未完成或已超时，请重新获取数据。"
        session["updated_at"] = time.time()


def _login_required(message: str, *, results: bool = False) -> dict:
    payload = {"error": message, "login_required": True}
    payload["results" if results else "accounts"] = []
    return payload


def _public_session(session: dict) -> dict:
    url = str(session.get("qr_url") or "")
    return {
        "status": session["status"],
        "session_id": session["id"],
        "qr_data_url": _qr_data_url(url) if url else "",
        "error": session.get("error") or "",
    }


def _qr_data_url(url: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
        output = io.BytesIO()
        image.save(output)
    except Exception:
        return ""
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _prune_sessions() -> None:
    cutoff = time.time() - _ACTIVE_TTL_SECONDS
    for session_id, session in list(_login_sessions.items()):
        if session.get("updated_at", 0) < cutoff:
            _login_sessions.pop(session_id, None)
