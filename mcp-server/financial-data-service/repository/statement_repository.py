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
        ticker_filter = "WHERE ticker_code = ANY(%s)"
        params.append(tickers)
    sql = f"""
        SELECT DISTINCT ON (ticker_code)
               ticker_code,
               market_code,
               fiscal_year,
               report_period,
               categories
        FROM (
            SELECT ticker_code,
                   MAX(market_code::text) AS market_code,
                   fiscal_year,
                   report_period::text AS report_period,
                   ARRAY_AGG(DISTINCT item_category::text) AS categories
            FROM financial_statements
            {ticker_filter}
            GROUP BY ticker_code, fiscal_year, report_period
        ) grouped
        ORDER BY ticker_code,
                 fiscal_year DESC,
                 CASE report_period
                     WHEN '1季报' THEN 1
                     WHEN '2季报' THEN 2
                     WHEN '3季报' THEN 3
                     WHEN '4季报' THEN 4
                     WHEN '年报' THEN 5
                     ELSE 0
                 END DESC
    """
    return fetch_statement_rows(sql, params)
