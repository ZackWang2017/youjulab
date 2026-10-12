"""抓取调用方给出的单个 http(s) 页面。不跟随到内网，不整站爬取。"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_MAX_BYTES = 1_000_000
_MAX_REDIRECTS = 5
# 本地代理（Clash、Surge）常用的 fake-ip 段，解析结果不是内网主机。
_PROXY_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
_USER_AGENT = "invp-page-reader/1.0"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_page(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    current = _public_http_url(url)
    request_headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    if headers:
        request_headers.update(headers)
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                response = client.get(current, headers=request_headers)
            except httpx.HTTPError as exc:
                return _failed(current, f"抓取失败：{exc}")
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location") or ""
                if not location:
                    break
                current = _public_http_url(urljoin(str(response.url), location))
                continue
            body = response.content[:_MAX_BYTES]
            encoding = response.encoding or "utf-8"
            text = body.decode(encoding, errors="replace")
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            return {
                "url": str(response.url),
                "status": response.status_code,
                "content_type": content_type,
                "truncated": len(response.content) > _MAX_BYTES,
                "body": text,
            }
    return _failed(current, "重定向次数过多。")


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv4Address) and ip in _PROXY_FAKE_IP:
        return False
    return not ip.is_global


def _failed(url: str, message: str) -> dict[str, Any]:
    return {"error": message, "url": url, "status": 0, "content_type": "", "truncated": False, "body": ""}


def _public_http_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持带域名的 http 或 https 页面地址。")
    host = parsed.hostname.strip().lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("无法解析该网址。") from exc
    for record in records:
        ip = ipaddress.ip_address(record[4][0])
        if _is_blocked(ip):
            raise ValueError("只能抓取公网页面。")
    return parsed.geturl()
