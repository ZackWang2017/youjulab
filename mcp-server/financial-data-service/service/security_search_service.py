"""证券名称和代码查询。"""
from __future__ import annotations

import security_search_repository as repository

_MAX_QUERY = 40


def search_securities(query: str, limit: int = 8) -> list[dict]:
    text = (query or "").strip()
    if not text:
        return []
    if len(text) > _MAX_QUERY:
        raise ValueError("查询过长。")
    size = min(max(int(limit or 8), 1), 10)
    rows = repository.search_securities(text, limit=size)
    hints = repository.fetch_name_hints(text)
    hint_name = {}
    hint_order = []
    for hint in hints:
        ticker = repository.ticker_of(hint["code"], hint["market"])
        hint_name[ticker] = hint["name"]
        hint_order.append(ticker)
    known = {row["ticker_code"]: row for row in repository.fetch_by_tickers(list(hint_name))}
    for row in rows:
        chinese = hint_name.get(row["ticker_code"])
        if chinese:
            row["name"] = chinese
    seen = {row["ticker_code"] for row in rows}
    for ticker in hint_order:
        if ticker in seen or ticker not in known:
            continue
        seen.add(ticker)
        row = {"ticker_code": ticker, "name": hint_name[ticker]}
        if known[ticker].get("security_type"):
            row["security_type"] = known[ticker]["security_type"]
        rows.append(row)
    return rows[:size]
