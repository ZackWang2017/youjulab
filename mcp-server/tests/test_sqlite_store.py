"""单机 SQLite 存储：覆盖桌面 MCP 的核心结构化数据路径。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "account-service" / "repository"))
sys.path.insert(0, str(HERE / "external-tools" / "repository"))
sys.path.insert(0, str(HERE / "portfolio-service" / "repository"))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))

import account_repository  # noqa: E402
import portfolio_repository  # noqa: E402
import security_search_repository  # noqa: E402
import usage  # noqa: E402
from db.bootstrap import init_schema  # noqa: E402
from db.connection import get_connection  # noqa: E402
from scheduler.pg_run_store import PgRunStore  # noqa: E402


class SqliteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(
            os.environ,
            {
                "FINANCE_STORE": "sqlite",
                "FINANCE_SQLITE_PATH": str(Path(self.temp.name) / "finresearch.sqlite"),
                "SEARCH_BAIDU_DAILY_LIMIT": "2",
            },
            clear=False,
        )
        self.patch.start()
        portfolio_repository._schema_ready = False
        usage._ensured = False
        init_schema()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_core_records_work_without_postgres(self) -> None:
        account_repository.ensure_schema()
        account_repository.insert("sqlite-user", "sqlite@example.com", "hash")
        self.assertEqual(account_repository.get_by_email("sqlite@example.com")["id"], "sqlite-user")

        portfolio = portfolio_repository.create_portfolio("sqlite-user", "测试组合")
        portfolio_repository.insert_holding(
            portfolio["id"],
            {
                "ticker_code": "00700.HK",
                "name": "腾讯控股",
                "market_code": "HK",
                "currency": "HKD",
                "shares": Decimal("100"),
                "avg_cost": Decimal("400"),
                "opened_at": datetime(2026, 1, 2, 9, 0),
            },
        )
        portfolio_repository.upsert_close_prices(
            [("00700.HK", date(2026, 1, 2), Decimal("400"))]
        )
        self.assertEqual(portfolio_repository.latest_close("00700.HK"), (date(2026, 1, 2), Decimal("400")))

        run_store = PgRunStore()
        run_store.mark("market.sync", date(2026, 1, 2), "success", "ok")
        self.assertEqual(run_store.get("market.sync", date(2026, 1, 2)).status, "success")

        self.assertTrue(usage.try_consume("baidu")["ok"])
        self.assertEqual(usage.try_consume("baidu")["used_today"], 2)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO securities (ticker_code, name, security_type) VALUES (%s, %s, %s)",
                    ("SPY.US", "SPDR ETF", "基金"),
                )
        self.assertEqual(security_search_repository.search_securities("SPY", limit=5)[0]["ticker_code"], "SPY.US")


if __name__ == "__main__":
    unittest.main()
