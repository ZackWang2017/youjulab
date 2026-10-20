"""持仓管理的持久化：投资组合、持仓、流水、收盘价、汇率、每日净值。

所有表都放在 finresearch 库，和 securities 表同库，首次访问时自动建表。
owner_id 是办公工作区的用户 id，由 office-backend 转发时在请求头里带过来。
"""
from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from db.connection import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS portfolios (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    name          VARCHAR(80) NOT NULL,
    base_currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portfolios_owner ON portfolios (owner_id);

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id                    BIGSERIAL PRIMARY KEY,
    portfolio_id          BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker_code           VARCHAR(32) NOT NULL,
    name                  VARCHAR(128) NOT NULL,
    market_code           VARCHAR(10) NOT NULL,
    currency              VARCHAR(8) NOT NULL,
    shares                NUMERIC(24, 6) NOT NULL DEFAULT 0,
    avg_cost              NUMERIC(24, 6) NOT NULL DEFAULT 0,
    target_allocation_pct NUMERIC(8, 2),
    status                VARCHAR(10) NOT NULL DEFAULT 'open',
    opened_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at             TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_pf ON portfolio_holdings (portfolio_id, status);

CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id            BIGSERIAL PRIMARY KEY,
    portfolio_id  BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    holding_id    BIGINT REFERENCES portfolio_holdings(id) ON DELETE SET NULL,
    ticker_code   VARCHAR(32) NOT NULL,
    currency      VARCHAR(8) NOT NULL,
    action        VARCHAR(12) NOT NULL,
    price         NUMERIC(24, 6) NOT NULL,
    shares        NUMERIC(24, 6) NOT NULL,
    amount        NUMERIC(24, 6) NOT NULL,
    external_flow NUMERIC(24, 6) NOT NULL DEFAULT 0,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portfolio_tx_pf_time ON portfolio_transactions (portfolio_id, occurred_at);

CREATE TABLE IF NOT EXISTS security_close_prices (
    ticker_code VARCHAR(32) NOT NULL,
    trade_date  DATE NOT NULL,
    close       NUMERIC(24, 6) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker_code, trade_date)
);

CREATE TABLE IF NOT EXISTS fx_rates_daily (
    currency  VARCHAR(8) NOT NULL,
    rate_date DATE NOT NULL,
    rate_cny  NUMERIC(18, 6) NOT NULL,
    PRIMARY KEY (currency, rate_date)
);

CREATE TABLE IF NOT EXISTS portfolio_nav_daily (
    portfolio_id       BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    nav_date           DATE NOT NULL,
    total_value_cny    NUMERIC(24, 4) NOT NULL,
    net_flow_cny       NUMERIC(24, 4) NOT NULL DEFAULT 0,
    units              NUMERIC(28, 8) NOT NULL,
    nav                NUMERIC(18, 6) NOT NULL,
    daily_return       NUMERIC(14, 8),
    cum_invested_cny   NUMERIC(24, 4) NOT NULL DEFAULT 0,
    cum_withdrawn_cny  NUMERIC(24, 4) NOT NULL DEFAULT 0,
    xirr               NUMERIC(14, 8),
    computed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (portfolio_id, nav_date)
);
"""

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
        _schema_ready = True


def _row(row: dict | None) -> dict | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------

def list_portfolios(owner_id: str) -> list[dict]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolios WHERE owner_id = %s ORDER BY created_at, id",
                (owner_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_portfolio(owner_id: str, portfolio_id: int) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolios WHERE owner_id = %s AND id = %s",
                (owner_id, portfolio_id),
            )
            return _row(cur.fetchone())


def create_portfolio(owner_id: str, name: str, base_currency: str = "CNY") -> dict:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portfolios (owner_id, name, base_currency) VALUES (%s, %s, %s) RETURNING *",
                (owner_id, name, base_currency),
            )
            return dict(cur.fetchone())


def rename_portfolio(owner_id: str, portfolio_id: int, name: str) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE portfolios SET name = %s, updated_at = NOW() WHERE owner_id = %s AND id = %s RETURNING *",
                (name, owner_id, portfolio_id),
            )
            return _row(cur.fetchone())


def delete_portfolio(owner_id: str, portfolio_id: int) -> bool:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM portfolios WHERE owner_id = %s AND id = %s", (owner_id, portfolio_id))
            return cur.rowcount > 0


def all_portfolio_ids() -> list[int]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM portfolios ORDER BY id")
            return [int(r["id"]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# 持仓与流水
# ---------------------------------------------------------------------------

def list_holdings(portfolio_id: int, *, status: str | None = "open") -> list[dict]:
    ensure_schema()
    sql = "SELECT * FROM portfolio_holdings WHERE portfolio_id = %s"
    params: list[Any] = [portfolio_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY opened_at DESC, id DESC"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def get_holding(portfolio_id: int, holding_id: int) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolio_holdings WHERE portfolio_id = %s AND id = %s",
                (portfolio_id, holding_id),
            )
            return _row(cur.fetchone())


def find_open_holding(portfolio_id: int, ticker_code: str) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolio_holdings WHERE portfolio_id = %s AND ticker_code = %s AND status = 'open'",
                (portfolio_id, ticker_code),
            )
            return _row(cur.fetchone())


def insert_holding(portfolio_id: int, fields: dict) -> dict:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO portfolio_holdings
                    (portfolio_id, ticker_code, name, market_code, currency, shares, avg_cost,
                     target_allocation_pct, status, opened_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s)
                RETURNING *
                """,
                (
                    portfolio_id, fields["ticker_code"], fields["name"], fields["market_code"], fields["currency"],
                    fields["shares"], fields["avg_cost"], fields.get("target_allocation_pct"), fields["opened_at"],
                ),
            )
            return dict(cur.fetchone())


def update_holding(holding_id: int, **fields: Any) -> dict | None:
    ensure_schema()
    if not fields:
        return None
    columns = ", ".join(f"{key} = %s" for key in fields)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE portfolio_holdings SET {columns}, updated_at = NOW() WHERE id = %s RETURNING *",
                (*fields.values(), holding_id),
            )
            return _row(cur.fetchone())


def insert_transaction(portfolio_id: int, fields: dict) -> dict:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO portfolio_transactions
                    (portfolio_id, holding_id, ticker_code, currency, action, price, shares, amount,
                     external_flow, occurred_at, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    portfolio_id, fields.get("holding_id"), fields["ticker_code"], fields["currency"], fields["action"],
                    fields["price"], fields["shares"], fields["amount"], fields.get("external_flow", 0),
                    fields["occurred_at"], fields.get("note"),
                ),
            )
            return dict(cur.fetchone())


def list_transactions(portfolio_id: int, *, holding_id: int | None = None, limit: int | None = None) -> list[dict]:
    ensure_schema()
    sql = "SELECT * FROM portfolio_transactions WHERE portfolio_id = %s"
    params: list[Any] = [portfolio_id]
    if holding_id is not None:
        sql += " AND holding_id = %s"
        params.append(holding_id)
    sql += " ORDER BY occurred_at DESC, id DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def transactions_in_order(portfolio_id: int) -> list[dict]:
    """按发生时间正序，净值重算用。"""
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolio_transactions WHERE portfolio_id = %s ORDER BY occurred_at, id",
                (portfolio_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def held_tickers_everywhere() -> list[dict]:
    """所有组合里出现过的非现金证券（含已清仓，补历史收盘价要用）。"""
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker_code, market_code, MIN(opened_at)::date AS first_date
                FROM portfolio_holdings
                WHERE market_code <> 'CASH'
                GROUP BY ticker_code, market_code
                ORDER BY ticker_code
                """
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# 收盘价与汇率
# ---------------------------------------------------------------------------

def upsert_close_prices(rows: Iterable[tuple[str, date, Decimal]]) -> int:
    ensure_schema()
    payload = list(rows)
    if not payload:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO security_close_prices (ticker_code, trade_date, close)
                VALUES (%s, %s, %s)
                ON CONFLICT (ticker_code, trade_date) DO UPDATE SET close = EXCLUDED.close
                """,
                payload,
            )
    return len(payload)


def last_close_date(ticker_code: str) -> date | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(trade_date) AS d FROM security_close_prices WHERE ticker_code = %s",
                (ticker_code,),
            )
            row = cur.fetchone()
            return row["d"] if row else None


def close_prices_between(tickers: list[str], start: date, end: date) -> dict[str, list[tuple[date, Decimal]]]:
    """每只证券按日期正序的收盘价，含 start 之前最近一条，便于向前结转。"""
    ensure_schema()
    if not tickers:
        return {}
    out: dict[str, list[tuple[date, Decimal]]] = {t: [] for t in tickers}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker_code, trade_date, close FROM security_close_prices
                WHERE ticker_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s
                ORDER BY ticker_code, trade_date
                """,
                (tickers, start, end),
            )
            for r in cur.fetchall():
                out[r["ticker_code"]].append((r["trade_date"], Decimal(r["close"])))
            cur.execute(
                """
                SELECT DISTINCT ON (ticker_code) ticker_code, trade_date, close FROM security_close_prices
                WHERE ticker_code = ANY(%s::text[]) AND trade_date < %s
                ORDER BY ticker_code, trade_date DESC
                """,
                (tickers, start),
            )
            for r in cur.fetchall():
                out[r["ticker_code"]].insert(0, (r["trade_date"], Decimal(r["close"])))
    return out


def year_start_closes(tickers: list[str], year: int) -> dict[str, Decimal]:
    """每只证券在 ``year`` 之前最后一条收盘价（即上年末收盘），用于年涨跌。"""
    ensure_schema()
    if not tickers:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker_code) ticker_code, close
                FROM security_close_prices
                WHERE ticker_code = ANY(%s::text[]) AND trade_date < %s
                ORDER BY ticker_code, trade_date DESC
                """,
                (tickers, date(year, 1, 1)),
            )
            return {r["ticker_code"]: Decimal(r["close"]) for r in cur.fetchall()}


def latest_close(ticker_code: str) -> tuple[date, Decimal] | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, close FROM security_close_prices WHERE ticker_code = %s ORDER BY trade_date DESC LIMIT 1",
                (ticker_code,),
            )
            row = cur.fetchone()
            return (row["trade_date"], Decimal(row["close"])) if row else None


def upsert_fx_rates(rate_date: date, rates: dict[str, Decimal]) -> None:
    ensure_schema()
    if not rates:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO fx_rates_daily (currency, rate_date, rate_cny) VALUES (%s, %s, %s)
                ON CONFLICT (currency, rate_date) DO UPDATE SET rate_cny = EXCLUDED.rate_cny
                """,
                [(currency, rate_date, rate) for currency, rate in rates.items()],
            )


def fx_rates_between(start: date, end: date) -> dict[str, list[tuple[date, Decimal]]]:
    ensure_schema()
    out: dict[str, list[tuple[date, Decimal]]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT currency, rate_date, rate_cny FROM fx_rates_daily
                WHERE rate_date <= %s AND (rate_date >= %s OR rate_date = (
                    SELECT MAX(f2.rate_date) FROM fx_rates_daily f2 WHERE f2.currency = fx_rates_daily.currency AND f2.rate_date < %s
                ))
                ORDER BY currency, rate_date
                """,
                (end, start, start),
            )
            for r in cur.fetchall():
                out.setdefault(r["currency"], []).append((r["rate_date"], Decimal(r["rate_cny"])))
    return out


# ---------------------------------------------------------------------------
# 每日净值
# ---------------------------------------------------------------------------

def replace_nav_series(portfolio_id: int, rows: list[dict]) -> int:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM portfolio_nav_daily WHERE portfolio_id = %s", (portfolio_id,))
            if rows:
                cur.executemany(
                    """
                    INSERT INTO portfolio_nav_daily
                        (portfolio_id, nav_date, total_value_cny, net_flow_cny, units, nav, daily_return,
                         cum_invested_cny, cum_withdrawn_cny, xirr)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            portfolio_id, r["nav_date"], r["total_value_cny"], r["net_flow_cny"], r["units"], r["nav"],
                            r.get("daily_return"), r["cum_invested_cny"], r["cum_withdrawn_cny"], r.get("xirr"),
                        )
                        for r in rows
                    ],
                )
    return len(rows)


def nav_series(portfolio_id: int) -> list[dict]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM portfolio_nav_daily WHERE portfolio_id = %s ORDER BY nav_date",
                (portfolio_id,),
            )
            return [dict(r) for r in cur.fetchall()]

