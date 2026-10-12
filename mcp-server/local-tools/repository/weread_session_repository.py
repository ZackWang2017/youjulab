"""微信读书网页版登录态，以及用该登录态读取公众号最新文章。"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import httpx

_BASE = "https://weread.qq.com"
_AUTH_CODES = {-2012, -2010}
_SESSION_PATH = Path(__file__).resolve().parents[3] / "data" / "weread-session.json"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": _BASE,
    "Referer": f"{_BASE}/",
}


def load_cookie() -> str:
    try:
        payload = json.loads(_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    cookie = payload.get("cookie") if isinstance(payload, dict) else ""
    return cookie.strip() if isinstance(cookie, str) else ""


def save_cookie(cookie: str) -> None:
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_PATH.write_text(
        json.dumps({"cookie": cookie}, ensure_ascii=False),
        encoding="utf-8",
    )


def session_valid(cookie: str) -> bool:
    if not cookie.strip():
        return False
    try:
        response = httpx.get(
            f"{_BASE}/web/shelf/sync",
            params={"userVid": "", "synckey": 0},
            headers={**_HEADERS, "Cookie": cookie},
            timeout=20,
        )
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or _auth_code(payload):
        return False
    return any(key in payload for key in ("books", "bookCount", "synckey"))


def login(on_qr) -> str:
    """阻塞直到扫码成功或五分钟超时。二维码过期后会再要一张。"""
    deadline = time.time() + 300
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
        while time.time() < deadline:
            try:
                opened = client.get(f"{_BASE}/api/auth/getLoginUid")
                uid = str((opened.json() or {}).get("uid") or "")
            except (httpx.HTTPError, json.JSONDecodeError):
                return ""
            if not uid:
                return ""
            on_qr(f"{_BASE}/web/confirm?uid={uid}")
            uid_deadline = min(deadline, time.time() + 120)
            while time.time() < uid_deadline:
                try:
                    checked = client.get(
                        f"{_BASE}/api/auth/getLoginInfo",
                        params={"uid": uid, "otp": ""},
                        timeout=75,
                    )
                    data = checked.json()
                except httpx.TimeoutException:
                    continue
                except (httpx.HTTPError, json.JSONDecodeError):
                    return ""
                if not isinstance(data, dict):
                    return ""
                inner = data.get("data") if isinstance(data.get("data"), dict) else {}
                if data.get("succeed") or inner.get("succeed"):
                    cookie = _renew(_cookie_from(client, checked, data, inner))
                    if session_valid(cookie):
                        save_cookie(cookie)
                        return cookie
                    return ""
                if str(data.get("logicCode") or "") == "LOGIN_TIMEOUT":
                    break
                if str(data.get("logicCode") or "") == "NEED_OTP":
                    return ""
    return ""


def list_official_accounts(cookie: str) -> dict[str, Any]:
    """每次请求都读当前书架，只保留公众号。"""
    fetched = _get_json(
        f"{_BASE}/web/shelf/sync",
        {"userVid": "", "synckey": 0},
        {**_HEADERS, "Cookie": cookie},
    )
    if fetched.get("auth_expired") or fetched.get("error"):
        return {"accounts": [], "error": fetched.get("error") or "", "auth_expired": bool(fetched.get("auth_expired"))}
    accounts: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (fetched.get("payload") or {}).get("books") or []:
        if not isinstance(item, dict):
            continue
        book_id = str(item.get("bookId") or "")
        if not book_id.startswith("MP_WXS_") or book_id == "MP_WXS_FEATURED_ARTICLES" or book_id in seen:
            continue
        seen.add(book_id)
        title = str(item.get("title") or "").strip() or book_id
        accounts.append({"id": book_id, "title": title})
    return {"accounts": accounts, "error": "", "auth_expired": False}


def latest_articles(cookie: str, book_id: str) -> dict[str, Any]:
    headers = {**_HEADERS, "Cookie": cookie}
    listed = _get_json(f"{_BASE}/web/mp/articles", {"bookId": book_id, "offset": 0}, headers)
    items = [] if listed.get("auth_expired") else _articles_from_reviews(listed.get("payload") or {}, book_id)
    if listed.get("auth_expired"):
        _log(f"{book_id} 文章列表返回登录失效，改读封面，暂不清除登录态")
    elif items:
        _log(f"{book_id} 文章列表拿到 {len(items)} 篇")
        return {"items": items, "error": "", "auth_expired": False}
    else:
        _log(f"{book_id} 文章列表为空，改读封面")
    cover = _get_json(f"{_BASE}/api/mp/cover", {"bookId": book_id}, headers)
    if cover.get("auth_expired"):
        _log(f"{book_id} 封面也返回登录失效")
        return cover
    payload = cover.get("payload") or {}
    if cover.get("error") and not payload.get("reviewId"):
        _log(f"{book_id} 封面失败：{cover['error']}")
        return {"items": [], "error": cover["error"], "auth_expired": False}
    if not payload.get("reviewId"):
        _log(f"{book_id} 封面没有 reviewId")
    item = _article_from_page(cookie, payload, book_id)
    if not item:
        _log(f"{book_id} 封面和正文都没有可读文章")
        return {"items": [], "error": "这个公众号目前没有可读的新文章。", "auth_expired": False}
    _log(
        f"{book_id} 读到《{item['title']}》"
        f" 时间={item['published_at'] or '无'}"
        f" 摘要{len(item['summary'])}字"
    )
    return {"items": [item], "error": "", "auth_expired": False}


def _get_json(url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=20)
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return {"payload": {}, "error": f"微信读书请求失败：{exc}", "auth_expired": False}
    if not isinstance(payload, dict):
        return {"payload": {}, "error": "微信读书返回的不是文章数据。", "auth_expired": False}
    code = _raw_code(payload)
    path = url.removeprefix(_BASE)
    if code:
        _log(f"{path} errCode={code}")
    if _auth_code(payload):
        return {"payload": payload, "error": "微信读书登录已过期。", "auth_expired": True}
    return {"payload": payload, "error": "", "auth_expired": False}


def _cookie_from(client: httpx.Client, response: httpx.Response, data: dict[str, Any], inner: dict[str, Any]) -> str:
    values = {cookie.name: cookie.value for cookie in client.cookies.jar}
    for cookie in response.cookies.jar:
        if cookie.value:
            values[cookie.name] = cookie.value
    vid = str(data.get("vid") or data.get("webLoginVid") or inner.get("vid") or inner.get("webLoginVid") or "")
    access = unquote(str(data.get("accessToken") or inner.get("accessToken") or ""))
    refresh = unquote(str(data.get("refreshToken") or inner.get("refreshToken") or ""))
    if vid:
        values.setdefault("wr_vid", vid)
    if access:
        values.setdefault("wr_skey", access)
    if refresh:
        values.setdefault("wr_rt", refresh)
    return "; ".join(f"{key}={value}" for key, value in values.items() if value)


def _renew(cookie: str) -> str:
    values = _cookie_map(cookie)
    if not values.get("wr_rt"):
        return cookie
    try:
        with httpx.Client(headers=_HEADERS, timeout=20) as client:
            for key, value in values.items():
                client.cookies.set(key, value, domain="weread.qq.com", path="/")
            client.post(
                f"{_BASE}/web/login/renewal",
                content=json.dumps({"rq": "%2Fweb%2Fbook%2Fread", "ql": True}),
                headers={"Content-Type": "application/json"},
            )
            for item in client.cookies.jar:
                if item.value:
                    values[item.name] = item.value
    except httpx.HTTPError:
        return cookie
    return "; ".join(f"{key}={value}" for key, value in values.items() if value)


def _cookie_map(cookie: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _log(message: str) -> None:
    print(f"微信读书：{message}", file=sys.stderr)


def _raw_code(payload: dict[str, Any]) -> int:
    raw = payload.get("errCode", payload.get("errcode", 0))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _auth_code(payload: dict[str, Any]) -> int:
    raw = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    return code if code in _AUTH_CODES else 0


def _articles_from_reviews(payload: dict[str, Any], book_id: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for group in payload.get("reviews") or []:
        if not isinstance(group, dict):
            continue
        for sub in group.get("subReviews") or []:
            if not isinstance(sub, dict):
                continue
            review = sub.get("review") if isinstance(sub.get("review"), dict) else {}
            info = review.get("mpInfo") if isinstance(review.get("mpInfo"), dict) else {}
            review_id = str(review.get("reviewId") or sub.get("reviewId") or "")
            title = str(info.get("title") or "").strip()
            if not review_id or not title:
                continue
            original = str(info.get("originalId") or "")
            link = _mp_url(original) or _link_from_review(review_id, book_id)
            when = review.get("createTime") or info.get("time") or group.get("createTime") or 0
            items.append({
                "title": title,
                "url": link,
                "published_at": _format_time(when),
                "summary": str(info.get("content") or review.get("content") or "").strip()[:500],
            })
    return items


def _article_from_page(cookie: str, cover: dict[str, Any], book_id: str) -> dict[str, str] | None:
    """文章列表接口已废弃。封面给出最新一篇，正文页补上链接、时间和摘要。"""
    review_id = str(cover.get("reviewId") or "")
    html = ""
    if review_id:
        html = _get_html(
            f"{_BASE}/web/mp/content",
            {"reviewId": review_id},
            {**_HEADERS, "Cookie": cookie},
        )
    parsed = _article_from_html(html, cover, book_id) if html else None
    return parsed or _article_from_cover(cover, book_id)


def _get_html(url: str, params: dict[str, Any], headers: dict[str, str], limit: int = 800_000) -> str:
    try:
        with httpx.stream("GET", url, params=params, headers=headers, timeout=20, follow_redirects=True) as response:
            response.raise_for_status()
            parts: list[bytes] = []
            size = 0
            for block in response.iter_bytes():
                parts.append(block)
                size += len(block)
                if size >= limit:
                    break
    except httpx.HTTPError as exc:
        _log(f"正文请求失败：{exc}")
        return ""
    html = b"".join(parts).decode("utf-8", "ignore")
    if not html.strip():
        _log("正文是空的")
    return html


def _article_from_html(html: str, cover: dict[str, Any], book_id: str) -> dict[str, str] | None:
    review_id = str(cover.get("reviewId") or "")
    title = _meta(html, "og:title") or str(cover.get("title") or "").strip()
    if not title:
        return None
    url = _meta(html, "og:url") or _link_from_review(review_id, book_id)
    summary = _summary_from_html(html)
    published = _format_time(_first(html, r"ori_create_time:\s*'(\d+)'"))
    return {"title": title, "url": url, "published_at": published, "summary": summary}


def _meta(html: str, prop: str) -> str:
    name = re.escape(prop)
    match = re.search(rf'<meta[^>]+property="{name}"[^>]+content="([^"]*)"', html, re.I)
    if not match:
        match = re.search(rf'<meta[^>]+content="([^"]*)"[^>]+property="{name}"', html, re.I)
    return unescape(match.group(1)).strip() if match else ""


def _summary_from_html(html: str) -> str:
    start = html.find('id="js_content"')
    if start < 0:
        return ""
    opened = html.find(">", start)
    if opened < 0:
        return ""
    chunk = html[opened + 1:opened + 1 + 120_000]
    chunk = re.sub(r"<script[\s\S]*?</script>", " ", chunk, flags=re.I)
    chunk = re.sub(r"<style[\s\S]*?</style>", " ", chunk, flags=re.I)
    chunk = re.sub(r"<[^>]+>", " ", chunk)
    return unescape(re.sub(r"\s+", " ", chunk)).strip()[:500]


def _first(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    return match.group(1) if match else ""


def _article_from_cover(payload: dict[str, Any], book_id: str) -> dict[str, str] | None:
    review_id = str(payload.get("reviewId") or "")
    title = str(payload.get("title") or "").strip()
    if not review_id or not title:
        return None
    return {
        "title": title,
        "url": _link_from_review(review_id, book_id),
        "published_at": _format_time(payload.get("time") or payload.get("createTime") or 0),
        "summary": str(payload.get("content") or payload.get("abstract") or "").strip()[:500],
    }


def _mp_url(original_id: str) -> str:
    token = original_id.strip().replace("~", "_")
    if not token:
        return ""
    return f"https://mp.weixin.qq.com/s/{quote(token, safe='')}"


def _link_from_review(review_id: str, book_id: str) -> str:
    token = review_id
    prefix = f"{book_id}_" if book_id else ""
    if prefix and review_id.startswith(prefix):
        token = review_id[len(prefix):]
    elif "_" in token:
        token = token.rsplit("_", 1)[-1]
    return _mp_url(token)


def _format_time(raw: Any) -> str:
    try:
        stamp = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    if stamp > 10_000_000_000:
        stamp //= 1000
    return datetime.fromtimestamp(stamp, _SHANGHAI).strftime("%Y-%m-%d %H:%M")
