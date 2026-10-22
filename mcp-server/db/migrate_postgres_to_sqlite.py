"""一次性把 finresearch PostgreSQL 数据导入单机 SQLite。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
for layer in (
    "account-service/repository",
    "external-tools/repository",
    "portfolio-service/repository",
):
    sys.path.insert(0, str(ROOT / layer))

import account_repository  # noqa: E402
import portfolio_repository  # noqa: E402
import usage  # noqa: E402
from db.config import load_config, sqlite_path  # noqa: E402
from db.connection import get_connection  # noqa: E402
from db.bootstrap import init_schema  # noqa: E402
from scheduler.pg_run_store import PgRunStore  # noqa: E402

TABLES = (
    "accounts",
    "securities",
    "financial_item_mappings",
    "financial_statements",
    "portfolios",
    "portfolio_holdings",
    "portfolio_transactions",
    "security_close_prices",
    "fx_rates_daily",
    "portfolio_nav_daily",
    "scheduled_task_runs",
    "external_search_daily",
)
DELETE_ORDER = tuple(reversed(TABLES))


def _source_rows(conn: psycopg.Connection, table: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", (table,))
        if not cur.fetchone()["table_name"]:
            return []
        cur.execute(f"SELECT * FROM {table}")
        return [dict(row) for row in cur.fetchall()]


def _prepare_target(replace: bool) -> None:
    os.environ["FINANCE_STORE"] = "sqlite"
    init_schema()
    account_repository.ensure_schema()
    portfolio_repository.ensure_schema()
    usage.ensure_schema()
    PgRunStore()._ensure()
    if not replace:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            for table in DELETE_ORDER:
                cur.execute(f"DELETE FROM {table}")


def _copy_rows(table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    columns = tuple(rows[0])
    marks = ", ".join("%s" for _ in columns)
    statement = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({marks})"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(statement, [tuple(row.get(column) for column in columns) for row in rows])
    return len(rows)


def migrate(*, replace: bool = False) -> dict[str, int]:
    source = load_config()
    with psycopg.connect(source.dsn, row_factory=dict_row) as pg:
        source_rows = {table: _source_rows(pg, table) for table in TABLES}
    _prepare_target(replace)
    return {table: _copy_rows(table, source_rows[table]) for table in TABLES}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate finresearch PostgreSQL data into SQLite")
    parser.add_argument("--replace", action="store_true", help="先清空目标 SQLite 中已存在的迁移表")
    args = parser.parse_args()
    try:
        copied = migrate(replace=args.replace)
    except Exception as exc:  # noqa: BLE001
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"SQLite ready: {sqlite_path()}")
    for table, count in copied.items():
        print(f"{table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
