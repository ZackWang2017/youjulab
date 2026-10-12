"""预定义资讯源的 HTTP 请求。地址和参数由代码固定，不接受调用方传入的网址。"""
from __future__ import annotations

import json
import ssl
import time
from typing import Any
from urllib.parse import quote

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
_HKEX_HEADERS = {
    **_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Referer": "https://www1.hkexnews.hk/",
}


def hkex_lookup(code: str) -> dict[str, Any]:
    response = _hkex_get(
        "https://www1.hkexnews.hk/search/prefix.do",
        {"callback": "cb", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"},
    )
    return _jsonp(response.text)


def hkex_titles(stock_id: int) -> dict[str, Any]:
    response = _hkex_get(
        "https://www1.hkexnews.hk/search/titleSearchServlet.do",
        {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": str(stock_id),
            "documentType": "-1",
            "fromDate": "19990401",
            "toDate": "20991231",
            "title": "",
            "searchType": "1",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": "5",
            "lang": "zh",
        },
    )
    return response.json()


def _hkex_get(url: str, params: dict[str, str]) -> httpx.Response:
    """港交所偶发在 TLS 读完前断开。忽略这种结束，失败后改用 TLS 1.2 再试。"""
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=_HKEX_HEADERS,
                timeout=20.0,
                verify=_hkex_ssl(tls12=attempt > 0),
            )
            response.raise_for_status()
            return response
        except httpx.TransportError as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    assert last is not None
    raise last


def _hkex_ssl(*, tls12: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
    if tls12:
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def sina_news_html(symbol: str) -> str:
    response = httpx.get(
        "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllNewsStock.php",
        params={"symbol": symbol, "Page": "1"},
        headers={**_HEADERS, "Referer": "https://finance.sina.com.cn/"},
        timeout=20.0,
    )
    response.raise_for_status()
    return response.content.decode("gb18030", errors="replace")


def eastmoney_search(keyword: str) -> dict[str, Any]:
    payload = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 5,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    response = httpx.get(
        "https://search-api-web.eastmoney.com/search/jsonp",
        params={"cb": "cb", "param": json.dumps(payload, ensure_ascii=False)},
        headers={**_HEADERS, "Referer": "https://so.eastmoney.com/"},
        timeout=20.0,
    )
    response.raise_for_status()
    return _jsonp(response.text)


def xueqiu_news(symbol: str) -> dict[str, Any]:
    headers = {**_HEADERS, "Referer": f"https://xueqiu.com/S/{symbol}"}
    with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
        client.get(
            "https://xueqiu.com/service/csrf",
            params={"api": "/statuses/stock_timeline.json"},
        )
        response = client.get(
            "https://api.xueqiu.com/statuses/stock_timeline.json",
            params={
                "symbol_id": symbol,
                "symbol": symbol,
                "source": "自选股新闻",
                "count": "5",
                "page": "1",
                "sort": "alpha",
                "comment": "0",
                "hl": "0",
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("查询结果不是 JSON。")
    return payload


def xueqiu_talks(symbol: str) -> dict[str, Any]:
    headers = {**_HEADERS, "Referer": f"https://xueqiu.com/S/{symbol}"}
    with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
        client.get("https://xueqiu.com/")
        client.get(
            "https://xueqiu.com/service/csrf",
            params={"api": "/query/v1/symbol/search/status.json"},
        )
        response = client.get(
            "https://api.xueqiu.com/query/v1/symbol/search/status.json",
            params={
                "count": "20",
                "comment": "0",
                "symbol": symbol,
                "hl": "0",
                "source": "all",
                "sort": "time",
                "page": "1",
                "q": "",
                "type": "11",
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("查询结果不是 JSON。")
    return payload


def tencent_news(symbol: str) -> dict[str, Any]:
    response = httpx.get(
        "https://web.ifzq.gtimg.cn/appstock/news/info/search",
        params={"page": "1", "symbol": symbol, "n": "5", "type": "2"},
        headers={**_HEADERS, "Referer": "https://gu.qq.com/"},
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("查询结果不是 JSON。")
    return payload


def jisilu_topic(keyword: str) -> tuple[str, str]:
    response = httpx.get(
        "https://www.jisilu.cn/topic/" + quote(keyword, safe=""),
        headers={**_HEADERS, "Referer": "https://www.jisilu.cn/"},
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return str(response.url), response.text


def _jsonp(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("查询结果不是 JSON。")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("查询结果不是 JSON。")
    return parsed
