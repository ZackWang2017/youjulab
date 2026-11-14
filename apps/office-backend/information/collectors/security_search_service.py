"""Desktop security lookup without the remote PostgreSQL catalogue."""
from __future__ import annotations

import re

import httpx

_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
_MARKETS = {"HK", "SH", "SZ", "BJ", "US"}
_US_EXCHANGES = {"NASDAQ", "NAS", "NYSE", "NYSEARCA", "AMEX", "ARCA"}


def _security(row: dict) -> dict | None:
    code = str(row.get("Code") or "").strip()
    name = str(row.get("Name") or "").strip()
    market = str(row.get("JYS") or "").strip().upper()
    if market in _US_EXCHANGES or str(row.get("MarketType")) in {"105", "106", "107"}:
        market = "US"
    if not code or not name or market not in _MARKETS:
        return None
    if market == "HK" and code.isdigit():
        code = code.zfill(5)
    elif market in {"SH", "SZ", "BJ"} and code.isdigit():
        code = code.zfill(6)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", code):
        return None
    return {"ticker_code": f"{code.upper()}.{market}", "name": name,
            "security_type": "基金" if "ETF" in name.upper() or str(row.get("SecurityType")) in {"2", "3"} else "股票"}


def search_securities(query: str, limit: int = 8) -> list[dict]:
    text = (query or "").strip()
    if not text:
        return []
    if len(text) > 40:
        raise ValueError("查询过长。")
    size = min(max(int(limit or 8), 1), 10)
    code, separator, suffix = text.rpartition(".")
    target_market = suffix.upper() if separator and suffix.upper() in _MARKETS else ""
    search = code if target_market else text
    response = httpx.get(
        _URL,
        params={"input": search, "type": "14", "token": _TOKEN, "count": 30},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=8.0,
    )
    response.raise_for_status()
    payload = response.json()
    raw = ((payload.get("QuotationCodeTable") or {}).get("Data") or [])
    rows, seen = [], set()
    for candidate in raw:
        row = _security(candidate) if isinstance(candidate, dict) else None
        if row is None or row["ticker_code"] in seen:
            continue
        if target_market and not row["ticker_code"].endswith("." + target_market):
            continue
        seen.add(row["ticker_code"])
        rows.append(row)
    def rank(row: dict) -> tuple:
        ticker = row["ticker_code"].split(".", 1)[0]
        exact = ticker.casefold() == search.casefold()
        if ticker.isdigit() and search.isdigit():
            exact = int(ticker) == int(search)
        return (not exact, not row["name"].casefold().startswith(search.casefold()), len(row["name"]), row["ticker_code"])
    rows.sort(key=rank)
    return rows[:size]
