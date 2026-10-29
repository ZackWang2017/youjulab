"""A/B/H/美股官方公告抓取。地址固定在代码里，不接受调用方传入网址。"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_SEC_UA = "youjulab research bot contact@youjulab.local"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TAGS = re.compile(r"<[^>]+>")
_TIMEOUT = httpx.Timeout(20.0, read=30.0)
_CNINFO_ORG: dict[str, str] = {}
_SEC_CIK: dict[str, str] = {}
_SEC_CLASS: dict[str, str] = {}
_SEC_COMPANY_LOADED = False
_SEC_MF_LOADED = False
_SEC_COMPANY_FORMS = "8-K,10-Q,10-K,6-K,20-F"
_SEC_FUND_FORMS = "N-CSR,N-CSRS,NPORT-P,N-PORT,485BPOS,497,497K,N-CEN,8-K"


def reset_caches() -> None:
    global _SEC_COMPANY_LOADED, _SEC_MF_LOADED
    _CNINFO_ORG.clear()
    _SEC_CIK.clear()
    _SEC_CLASS.clear()
    _SEC_COMPANY_LOADED = False
    _SEC_MF_LOADED = False


def fetch_cninfo(ticker_code: str, name: str = "") -> list[dict[str, str]]:
    code, market = _split(ticker_code)
    if not code:
        raise ValueError("巨潮资讯查询需要股票代码。")
    try:
        org_id = _cninfo_org_id(code)
    except ValueError:
        rows = fetch_eastmoney_fund(ticker_code, name)
        if rows:
            return rows
        raise
    column = "sse" if market == "SH" else "szse"
    end = date.today()
    start = end - timedelta(days=730)
    payload = {
        "pageNum": "1",
        "pageSize": "20",
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = httpx.post(
        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        data=payload,
        headers={**_HEADERS, "Referer": "https://www.cninfo.com.cn/", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    items = []
    for row in data.get("announcements") or []:
        title = _plain(row.get("announcementTitle"))
        if not title:
            continue
        path = str(row.get("adjunctUrl") or "")
        url = "https://static.cninfo.com.cn/" + path.lstrip("/") if path else ""
        items.append({
            "title": title,
            "url": url,
            "published_at": _epoch_ms(row.get("announcementTime")),
            "summary": _plain(name),
        })
    return items


def fetch_sse(ticker_code: str, name: str = "") -> list[dict[str, str]]:
    code, market = _split(ticker_code)
    if market != "SH" or not code:
        raise ValueError("上交所公告只支持沪市代码。")
    response = httpx.get(
        "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do",
        params={
            "isPagination": "true",
            "pageHelp.pageSize": "20",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "SECURITY_CODE": code,
        },
        headers={**_HEADERS, "Referer": "https://www.sse.com.cn/disclosure/listedinfo/announcement/"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    items = []
    for row in _sse_rows(data):
        title = _plain(row.get("TITLE") or row.get("title"))
        if not title:
            continue
        url = str(row.get("URL") or row.get("url") or "")
        if url.startswith("/"):
            url = "https://static.sse.com.cn" + url
        elif url and not url.startswith("http"):
            url = "https://static.sse.com.cn/" + url.lstrip("/")
        items.append({
            "title": title,
            "url": url,
            "published_at": _plain(row.get("SSEDATE") or row.get("BULLETIN_DATE") or row.get("date")),
            "summary": _plain(name),
        })
    return items


def fetch_szse(ticker_code: str, name: str = "") -> list[dict[str, str]]:
    code, market = _split(ticker_code)
    if market != "SZ" or not code:
        raise ValueError("深交所公告只支持深市代码。")
    response = httpx.post(
        "https://www.szse.cn/api/disc/announcement/annList",
        json={"seDate": ["", ""], "stock": [code], "channelCode": ["listedNotice_disc"], "pageSize": 20, "pageNum": 1},
        headers={**_HEADERS, "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html", "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    items = []
    for row in data.get("data") or []:
        title = _plain(row.get("title"))
        if not title:
            continue
        attach = str(row.get("attachPath") or row.get("id") or "")
        url = f"https://disc.szse.cn/download?id={attach}" if attach else ""
        items.append({
            "title": title,
            "url": url,
            "published_at": _plain(row.get("publishTime") or row.get("seDate")),
            "summary": _plain(name),
        })
    return items


def fetch_eastmoney_fund(ticker_code: str, name: str = "") -> list[dict[str, str]]:
    code, _market = _split(ticker_code)
    if not code:
        return []
    response = httpx.get(
        "https://api.fund.eastmoney.com/f10/JJGG",
        params={"fundcode": code, "pageIndex": "1", "pageSize": "20", "type": "0"},
        headers={**_HEADERS, "Referer": f"https://fundf10.eastmoney.com/jjgg_{code}.html"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ErrCode") not in (None, 0, "0"):
        return []
    items = []
    for row in payload.get("Data") or []:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("TITLE"))
        notice_id = str(row.get("ID") or "").strip()
        if not title:
            continue
        url = f"https://fund.eastmoney.com/gonggao/{code},{notice_id}.html" if notice_id else ""
        items.append({
            "title": title,
            "url": url,
            "published_at": _plain(row.get("PUBLISHDATEDesc") or row.get("PUBLISHDATE")),
            "summary": _plain(name or row.get("ShortTitle")),
        })
    return items


def fetch_sec(ticker_code: str, name: str = "") -> list[dict[str, str]]:
    code, market = _split(ticker_code)
    if market != "US" or not code:
        raise ValueError("SEC 公告只支持美股代码。")
    entity = _sec_entity(code)
    cik = entity["cik"]
    headers = {**_HEADERS, "User-Agent": _SEC_UA}
    class_id = entity.get("class_id")
    if class_id:
        params = {"q": class_id, "dateRange": "1y", "forms": _SEC_FUND_FORMS}
    else:
        params = {"ciks": cik, "dateRange": "1y", "forms": _SEC_COMPANY_FORMS}
    response = httpx.get(
        "https://efts.sec.gov/LATEST/search-index",
        params=params,
        headers=headers,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    hits = ((data.get("hits") or {}).get("hits")) or []
    items = []
    for hit in hits[:20]:
        source = hit.get("_source") or {}
        form = _plain(source.get("form"))
        filed = _plain(source.get("file_date"))
        desc = _plain(source.get("file_description"))
        title = " ".join(part for part in (name or code, form, filed) if part).strip()
        if desc and desc.lower() not in title.lower():
            title = f"{title} {desc}".strip()
        if not title:
            continue
        adsh = str(source.get("adsh") or "").replace("-", "")
        file = str((hit.get("_id") or source.get("file_name") or "").split(":")[-1])
        cik_num = str((source.get("ciks") or [cik])[0]).lstrip("0") or "0"
        url = ""
        if adsh and file:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{adsh}/{file}"
        items.append({
            "title": title,
            "url": url,
            "published_at": filed,
            "summary": form,
        })
    return items


def _sse_rows(data: dict) -> list[dict]:
    raw = ((data.get("pageHelp") or {}).get("data")) or data.get("result") or []
    rows: list[dict] = []
    for row in raw:
        if isinstance(row, list):
            rows.extend(item for item in row if isinstance(item, dict))
        elif isinstance(row, dict):
            rows.append(row)
    return rows


def _cninfo_org_id(code: str) -> str:
    if code in _CNINFO_ORG:
        return _CNINFO_ORG[code]
    response = httpx.post(
        "https://www.cninfo.com.cn/new/information/topSearch/query",
        params={"keyWord": code, "maxNum": 10},
        headers={**_HEADERS, "Referer": "https://www.cninfo.com.cn/"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    rows = response.json()
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("list") or []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("code") or "").upper() != code.upper():
            continue
        org_id = str(row.get("orgId") or "").strip()
        if org_id:
            _CNINFO_ORG[code] = org_id
            return org_id
    raise ValueError(f"巨潮资讯未找到 {code} 的机构编号。")


def _sec_entity(code: str) -> dict[str, str]:
    key = code.upper()
    if key in _SEC_CIK:
        found = {"cik": _SEC_CIK[key]}
        if key in _SEC_CLASS:
            found["class_id"] = _SEC_CLASS[key]
        return found
    _load_sec_companies()
    if key in _SEC_CIK:
        return {"cik": _SEC_CIK[key]}
    _load_sec_funds()
    if key not in _SEC_CIK:
        raise ValueError(f"SEC 未收录代码 {code}。")
    found = {"cik": _SEC_CIK[key]}
    if key in _SEC_CLASS:
        found["class_id"] = _SEC_CLASS[key]
    return found


def _load_sec_companies() -> None:
    global _SEC_COMPANY_LOADED
    if _SEC_COMPANY_LOADED:
        return
    response = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={**_HEADERS, "User-Agent": _SEC_UA},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    for row in (response.json() or {}).values():
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        cik = str(row.get("cik_str") or "").strip()
        if ticker and cik:
            _SEC_CIK[ticker] = cik.zfill(10)
    _SEC_COMPANY_LOADED = True


def _load_sec_funds() -> None:
    global _SEC_MF_LOADED
    if _SEC_MF_LOADED:
        return
    response = httpx.get(
        "https://www.sec.gov/files/company_tickers_mf.json",
        headers={**_HEADERS, "User-Agent": _SEC_UA},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json() or {}
    rows = payload.get("data") if isinstance(payload, dict) else payload
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        cik, _series, class_id, symbol = row[0], row[1], row[2], row[3]
        ticker = str(symbol or "").upper()
        if not ticker or ticker in _SEC_CIK:
            continue
        _SEC_CIK[ticker] = str(cik).zfill(10)
        if class_id:
            _SEC_CLASS[ticker] = str(class_id)
    _SEC_MF_LOADED = True


def _split(ticker_code: str) -> tuple[str, str]:
    raw = (ticker_code or "").strip().upper()
    code, _, market = raw.partition(".")
    return code, market


def _plain(value: Any) -> str:
    return " ".join(_TAGS.sub("", unescape(str(value or ""))).split())


def _epoch_ms(value: Any) -> str:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return _plain(value)
    if stamp > 10_000_000_000:
        stamp = stamp / 1000
    try:
        return datetime.fromtimestamp(stamp, _SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""
