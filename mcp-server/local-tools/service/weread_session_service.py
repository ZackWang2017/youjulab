"""微信读书扫码登录。二维码打在 mcp-server 进程的标准错误上，接口本身不等待扫码。"""
from __future__ import annotations

import sys
import threading

import weread_session_repository as session_repository

_lock = threading.Lock()
_worker: threading.Thread | None = None


def ensure_login() -> None:
    """已有可用登录态就直接返回。否则在后台输出二维码并等待扫码。"""
    cookie = session_repository.load_cookie()
    if cookie and session_repository.session_valid(cookie):
        print("微信读书登录有效。", file=sys.stderr)
        return
    _start_worker("微信读书需要扫码登录。请用微信扫描下面的二维码，并在手机上确认登录微信读书网页版。")


def list_shelf_articles(num: int) -> dict:
    """重新读取书架上的全部公众号，再取每个号的最近文章。"""
    count = max(1, num)
    _log(f"开始拉取书架，每个号最多 {count} 篇")
    cookie = session_repository.load_cookie()
    if not cookie:
        _log("本地没有登录态")
        _start_worker("微信读书尚未登录。请到 mcp-server 终端扫描二维码。")
        return {"error": "微信读书尚未登录，请到 mcp-server 终端扫码。", "accounts": []}
    shelf = session_repository.list_official_accounts(cookie)
    if shelf.get("auth_expired"):
        _clear_login("书架")
        return {"error": "微信读书登录已过期，请到 mcp-server 终端扫码。", "accounts": []}
    if shelf.get("error"):
        _log(f"书架失败：{shelf['error']}")
        return {"error": shelf["error"], "accounts": []}
    books = shelf.get("accounts") or []
    _log(f"书架上有 {len(books)} 个公众号")
    if not books:
        return {"error": "书架上没有公众号。", "accounts": []}
    accounts = []
    for book in books:
        fetched = session_repository.latest_articles(cookie, book["id"])
        if fetched.get("auth_expired"):
            _clear_login(book.get("title") or book["id"])
            if accounts:
                break
            return {"error": "微信读书登录已过期，请到 mcp-server 终端扫码。", "accounts": []}
        items = (fetched.get("items") or [])[:count]
        accounts.append({
            "id": book["id"],
            "title": book["title"],
            "error": "" if items else (fetched.get("error") or "没有文章。"),
            "results": items,
        })
    filled = sum(1 for account in accounts if account["results"])
    _log(f"完成：{filled} 个号有文章，{len(accounts) - filled} 个号为空，共 {len(books)} 个号")
    return {"error": "", "accounts": accounts}


def list_articles(book_id: str, num: int) -> dict:
    cookie = session_repository.load_cookie()
    if not cookie:
        _log("本地没有登录态")
        _start_worker("微信读书尚未登录。请到 mcp-server 终端扫描二维码。")
        return _expired("微信读书尚未登录，请到 mcp-server 终端扫码。")
    fetched = session_repository.latest_articles(cookie, book_id)
    if fetched.get("auth_expired"):
        _clear_login(book_id)
        return _expired("微信读书登录已过期，请到 mcp-server 终端扫码。")
    items = (fetched.get("items") or [])[: max(1, num)]
    if fetched.get("error") and not items:
        return {"error": fetched["error"], "results": []}
    return {"error": "", "results": items}


def _log(message: str) -> None:
    print(f"微信读书：{message}", file=sys.stderr)


def _clear_login(where: str) -> None:
    _log(f"{where} 确认登录失效，已清除本地登录态")
    session_repository.save_cookie("")
    _start_worker("微信读书登录已过期。请用微信扫描下面的新二维码。")


def _start_worker(notice: str) -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_login, args=(notice,), name="weread-login", daemon=True)
        _worker.start()


def _login(notice: str) -> None:
    print(notice, file=sys.stderr)
    cookie = session_repository.login(_print_qr)
    if cookie:
        print("微信读书登录成功。", file=sys.stderr)
        return
    print("微信读书扫码未完成。下次拉取公众号时会再输出一张二维码。", file=sys.stderr)


def _print_qr(url: str) -> None:
    print("—— 微信读书登录二维码 ——", file=sys.stderr)
    print(url, file=sys.stderr)
    try:
        import qrcode
    except ImportError:
        print("当前环境没有 qrcode 包，请打开上面的地址完成登录。", file=sys.stderr)
        return
    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(out=sys.stderr, invert=True)


def _expired(message: str) -> dict:
    return {"error": message, "results": []}
