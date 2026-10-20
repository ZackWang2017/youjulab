"""已入库财报的增量检查：缺表或远程有更新才补齐。不访问数据库和外网。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "financial-data-service" / "service"))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))

import statement_refresh as refresh  # noqa: E402


class PeriodTests(unittest.TestCase):
    def test_same_year_annual_is_after_third_quarter(self):
        self.assertTrue(refresh.period_is_newer((2026, "年报"), (2026, "3季报")))
        self.assertFalse(refresh.period_is_newer((2025, "年报"), (2026, "1季报")))
        self.assertFalse(refresh.period_is_newer((2026, "2季报"), (2026, "2季报")))

    def test_eastmoney_and_sina_period_parsing(self):
        self.assertEqual(
            refresh.period_from_em_row({"REPORT_DATE": "2026-06-30 00:00:00", "REPORT_TYPE": "中报"}),
            (2026, "2季报"),
        )
        self.assertEqual(
            refresh.period_from_em_row({"REPORT_DATE": "2025-12-31", "DATE_TYPE_CODE": "001"}),
            (2025, "年报"),
        )
        payload = {"result": {"data": {"report_date": [
            {"date_value": "20250930", "date_type": 3},
            {"date_value": "20251231", "date_type": "4"},
        ]}}}
        self.assertEqual(refresh.latest_from_sina_payload(payload), (2025, "年报"))


class DecideTests(unittest.TestCase):
    def _categories(self):
        return set(refresh.REQUIRED_CATEGORIES)

    def test_missing_statement_refreshes_even_if_probe_failed(self):
        action, reason = refresh.decide_refresh(
            market="SH",
            categories={"资产负债表", "利润表"},
            local=(2026, "2季报"),
            remote=None,
            probe_error="down",
            force=False,
        )
        self.assertEqual(action, "refresh")
        self.assertIn("现金流量表", reason)

    def test_newer_remote_period_refreshes(self):
        action, reason = refresh.decide_refresh(
            market="HK",
            categories=self._categories(),
            local=(2025, "年报"),
            remote=(2026, "1季报"),
            probe_error=None,
            force=False,
        )
        self.assertEqual(action, "refresh")
        self.assertIn("2026年1季报", reason)

    def test_current_period_skips(self):
        action, _reason = refresh.decide_refresh(
            market="SZ",
            categories=self._categories(),
            local=(2026, "2季报"),
            remote=(2026, "2季报"),
            probe_error=None,
            force=False,
        )
        self.assertEqual(action, "skip")

    def test_probe_error_on_complete_local_data_fails_closed(self):
        action, reason = refresh.decide_refresh(
            market="SH",
            categories=self._categories(),
            local=(2026, "2季报"),
            remote=None,
            probe_error="检查远程财报失败",
            force=False,
        )
        self.assertEqual(action, "fail")
        self.assertIn("检查远程财报失败", reason)

    def test_us_is_skipped(self):
        action, reason = refresh.decide_refresh(
            market="US",
            categories=set(),
            local=(2026, "年报"),
            remote=None,
            probe_error=None,
            force=True,
        )
        self.assertEqual(action, "unsupported")
        self.assertIn("US", reason)


class ProbeTests(unittest.TestCase):
    def test_takes_the_newest_of_three_statements(self):
        rows = {
            "RPT_F10_FINANCE_GBALANCE": {"REPORT_DATE": "2026-03-31", "REPORT_TYPE": "一季报"},
            "RPT_F10_FINANCE_GINCOME": {"REPORT_DATE": "2026-06-30", "REPORT_TYPE": "中报"},
            "RPT_F10_FINANCE_GCASHFLOW": None,
        }

        class _Client:
            def fetch_latest_row(self, *, report_name: str, secucode: str):
                self.secucode = secucode
                return rows[report_name]

        client = _Client()
        period = refresh.probe_eastmoney_latest("600519.SH", "SH", client=client)
        self.assertEqual(period, (2026, "2季报"))
        self.assertEqual(client.secucode, "600519.SH")

    def test_all_request_failures_raise(self):
        class _Client:
            def fetch_latest_row(self, *, report_name: str, secucode: str):
                raise RuntimeError(f"down {report_name}")

        with self.assertRaises(RuntimeError):
            refresh.probe_eastmoney_latest("00700.HK", "HK", client=_Client())


class RefreshRowTests(unittest.TestCase):
    def test_up_to_date_does_not_sync(self):
        row = {
            "ticker_code": "600519.SH",
            "market_code": "SH",
            "fiscal_year": 2026,
            "report_period": "2季报",
            "categories": list(refresh.REQUIRED_CATEGORIES),
        }
        with patch.object(refresh, "_probe", return_value=((2026, "2季报"), None, None)), \
             patch.object(refresh, "_sync") as sync:
            result = refresh.refresh_coverage_row(row, dry_run=False, force=False)
        sync.assert_not_called()
        self.assertEqual(result["action"], refresh.ACTION_SKIP)

    def test_dry_run_does_not_write(self):
        row = {
            "ticker_code": "00700.HK",
            "market_code": "HK",
            "fiscal_year": 2025,
            "report_period": "年报",
            "categories": list(refresh.REQUIRED_CATEGORIES),
        }
        with patch.object(refresh, "_probe", return_value=((2026, "1季报"), None, None)), \
             patch.object(refresh, "_sync") as sync:
            result = refresh.refresh_coverage_row(row, dry_run=True, force=False)
        sync.assert_not_called()
        self.assertEqual(result["action"], refresh.ACTION_DRY)
        self.assertIn("2026年1季报", result["reason"])


if __name__ == "__main__":
    unittest.main()
