"""financial_statements 表读取。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.connection import get_connection  # noqa: E402


def fetch_statement_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def statement_ticker_exists(ticker_code: str) -> bool:
    rows = fetch_statement_rows(
        "SELECT 1 AS ok FROM financial_statements WHERE ticker_code = %s LIMIT 1",
        [ticker_code],
    )
    return bool(rows)


def security_exists(ticker_code: str) -> bool:
    rows = fetch_statement_rows(
        "SELECT 1 AS ok FROM securities WHERE ticker_code = %s LIMIT 1",
        [ticker_code],
    )
    return bool(rows)


def list_latest_statement_coverage(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """每只已入库证券的最新报告期，以及该期已有的报表类别。"""
    params: list[Any] = []
    ticker_filter = ""
    if tickers:
        marks = ", ".join("%s" for _ in tickers)
        ticker_filter = f"WHERE ticker_code IN ({marks})"
        params.extend(tickers)
    sql = f"""
        SELECT ticker_code, market_code, fiscal_year, report_period, item_category
        FROM financial_statements
        {ticker_filter}
        ORDER BY ticker_code, fiscal_year DESC,
                 CASE report_period
                     WHEN '1季报' THEN 1 WHEN '2季报' THEN 2 WHEN '3季报' THEN 3
                     WHEN '4季报' THEN 4 WHEN '年报' THEN 5 ELSE 0 END DESC
    """
    rank = {"1季报": 1, "2季报": 2, "3季报": 3, "4季报": 4, "年报": 5}
    latest: dict[str, dict[str, Any]] = {}
    for row in fetch_statement_rows(sql, params):
        key = row["ticker_code"]
        period_key = (int(row["fiscal_year"]), rank.get(str(row["report_period"]), 0))
        found = latest.get(key)
        if found is None or period_key > found["_period_key"]:
            latest[key] = {
                "ticker_code": key,
                "market_code": row["market_code"],
                "fiscal_year": row["fiscal_year"],
                "report_period": row["report_period"],
                "categories": {row["item_category"]},
                "_period_key": period_key,
            }
        elif period_key == found["_period_key"]:
            found["categories"].add(row["item_category"])
    return [
        {key: value for key, value in row.items() if key != "_period_key" and key != "categories"}
        | {"categories": sorted(row["categories"])}
        for row in latest.values()
    ]
