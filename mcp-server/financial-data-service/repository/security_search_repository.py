"""按名称或代码查询 securities 表。港股在表里是英文简称，中文名用行情联想补上后再回表核对。"""
from __future__ import annotations

import re

import httpx

from db.connection import get_connection

_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
_MARKETS = {"HK", "SH", "SZ", "BJ"}
_SEARCHABLE_TYPES = ("股票", "基金")  # 美股 ETF 记为基金
_US_CODE_RE = re.compile(r"^[A-Za-z]{1,6}$")


def candidate_codes(text: str) -> list[str]:
    head = (text or "").strip().split(".", 1)[0]
    if head.isdigit():
        return list(dict.fromkeys((head, head.zfill(5), head.zfill(6))))
    if _US_CODE_RE.match(head):
        # 美股 / ETF 代码，如 SPY、spy.us（仅 ASCII 字母，中文名不算）
        return [head.upper()]
    return []


def search_securities(text: str, *, limit: int) -> list[dict]:
    codes = candidate_codes(text)
    like = f"%{_escape_like(text)}%"
    prefix = f"{_escape_like(text)}%"
    sql = """
        SELECT ticker_code, name, security_type::text AS security_type
        FROM securities
        WHERE security_type = ANY(%s::security_type_enum[])
          AND (
            split_part(ticker_code, '.', 1) = ANY(%s::text[])
            OR ticker_code ILIKE %s
            OR name ILIKE %s
          )
        ORDER BY
          CASE
            WHEN split_part(ticker_code, '.', 1) = ANY(%s::text[]) THEN 0
            WHEN name ILIKE %s THEN 1
            ELSE 2
          END,
          char_length(name),
          ticker_code
        LIMIT %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (list(_SEARCHABLE_TYPES), codes, like, like, codes, prefix, limit))
            return [_row(row) for row in cur.fetchall()]


def fetch_by_tickers(tickers: list[str]) -> list[dict]:
    if not tickers:
        return []
    sql = """
        SELECT ticker_code, name, security_type::text AS security_type
        FROM securities
        WHERE ticker_code = ANY(%s::text[])
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tickers,))
            return [_row(row) for row in cur.fetchall()]


def _row(row) -> dict:
    return {"ticker_code": row["ticker_code"], "name": row["name"], "security_type": row["security_type"]}


def fetch_name_hints(text: str) -> list[dict]:
    """行情联想给出的中文简称。失败时返回空列表，不影响代码和英文名查询。"""
    try:
        response = httpx.get(
            _SUGGEST_URL,
            params={"input": text, "type": "14", "token": _SUGGEST_TOKEN, "count": 8},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            timeout=8.0,
        )
        response.raise_for_status()
        rows = ((response.json().get("QuotationCodeTable") or {}).get("Data") or [])
    except (httpx.HTTPError, ValueError):
        return []
    hints = []
    for row in rows:
        code = str(row.get("Code") or "").strip()
        name = str(row.get("Name") or "").strip()
        market = str(row.get("JYS") or "").strip().upper()
        if code and name and market in _MARKETS:
            hints.append({"code": code, "name": name, "market": market})
    return hints


def ticker_of(code: str, market: str) -> str:
    market = (market or "").upper()
    code = (code or "").strip()
    if market == "HK":
        code = code.zfill(5)
    return f"{code}.{market}"


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
