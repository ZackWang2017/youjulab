"""财报查询缺数补抓：已有数据不请求远程，失败结果短时缓存。不访问数据库和外网。"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "financial-data-service" / "service"))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))

import statement_ensure as ensure  # noqa: E402


class EnsureTests(unittest.TestCase):
    def setUp(self):
        ensure.reset_statement_sync_state()

    def test_ready_when_rows_already_exist(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=True), \
             patch.object(ensure, "_sync_market") as sync:
            result = ensure.ensure_statements("600519.SH")
        sync.assert_not_called()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["ticker_code"], "600519.SH")
        self.assertEqual(result["message"], "")

    def test_fetches_a_share_when_table_is_empty(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=False), \
             patch.object(ensure, "security_exists", return_value=True), \
             patch.object(ensure, "_sync_market", return_value={"saved": 12, "data_source": "东方财富"}) as sync:
            result = ensure.ensure_statements("600519.SH")
        sync.assert_called_once_with("600519.SH", "SH")
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["saved"], 12)
        self.assertIn("已从东方财富补齐", result["message"])

    def test_missing_security_does_not_fetch_and_is_cached(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=False), \
             patch.object(ensure, "security_exists", return_value=False) as exists, \
             patch.object(ensure, "_sync_market") as sync:
            first = ensure.ensure_statements("000858.SZ")
            second = ensure.ensure_statements("000858.SZ")
        sync.assert_not_called()
        exists.assert_called_once()
        self.assertEqual(first["status"], "unavailable")
        self.assertIn("不在证券库中", first["message"])
        self.assertEqual(second["message"], first["message"])

    def test_us_has_no_statement_source(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=False), \
             patch.object(ensure, "_sync_market") as sync:
            result = ensure.ensure_statements("SPY.US")
        sync.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("暂无美股财报数据源", result["message"])

    def test_hk_routes_to_hk_sync(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=False), \
             patch.object(ensure, "security_exists", return_value=True), \
             patch.object(ensure, "_sync_market", return_value={"saved": 4, "data_source": "东方财富"}) as sync:
            result = ensure.ensure_statements("700.HK")
        sync.assert_called_once_with("00700.HK", "HK")
        self.assertEqual(result["status"], "fetched")

    def test_sync_error_is_cached(self):
        with patch.object(ensure, "statement_ticker_exists", return_value=False), \
             patch.object(ensure, "security_exists", return_value=True), \
             patch.object(ensure, "_sync_market", side_effect=RuntimeError("down")) as sync:
            first = ensure.ensure_statements("600519.SH")
            second = ensure.ensure_statements("600519.SH")
        sync.assert_called_once()
        self.assertEqual(first["status"], "unavailable")
        self.assertIn("down", first["message"])
        self.assertEqual(second["status"], "unavailable")

    def test_parallel_calls_sync_once(self):
        state = {"has": False, "calls": 0}

        def exists(_ticker: str) -> bool:
            return state["has"]

        def sync(_ticker: str, _market: str) -> dict:
            state["calls"] += 1
            state["has"] = True
            return {"saved": 3, "data_source": "东方财富"}

        with patch.object(ensure, "statement_ticker_exists", side_effect=exists), \
             patch.object(ensure, "security_exists", return_value=True), \
             patch.object(ensure, "_sync_market", side_effect=sync):
            results: list[dict] = []

            def run():
                results.append(ensure.ensure_statements("601318.SH"))

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(state["calls"], 1)
        self.assertEqual(sorted(item["status"] for item in results), ["fetched", "ready"])


class QueryWithSyncTests(unittest.TestCase):
    def setUp(self):
        ensure.reset_statement_sync_state()

    def test_without_ticker_does_not_sync(self):
        with patch.object(ensure, "ensure_statements") as sync, \
             patch.object(ensure.statement_service, "query_financial_statements", return_value=[{"source_item_name": "货币资金"}]):
            result = ensure.query_statements_with_sync()
        sync.assert_not_called()
        self.assertEqual(result["sync"]["status"], "ready")
        self.assertIsNone(result["sync"]["ticker_code"])
        self.assertEqual(len(result["items"]), 1)

    def test_filter_miss_keeps_local_rows_untouched(self):
        ready = ensure._sync_payload(status="ready", ticker_code="600519.SH")
        with patch.object(ensure, "ensure_statements", return_value=ready), \
             patch.object(ensure.statement_service, "query_financial_statements", return_value=[]) as query:
            result = ensure.query_statements_with_sync(ticker_code="600519.sh", fiscal_year=1999)
        self.assertEqual(query.call_args.kwargs["query"].ticker_code, "600519.SH")
        self.assertEqual(query.call_args.kwargs["query"].fiscal_year, 1999)
        self.assertEqual(result["sync"]["status"], "ready")
        self.assertIn("没有符合本次筛选", result["sync"]["message"])

    def test_fetched_rows_keep_the_fetch_message(self):
        fetched = ensure._sync_payload(
            status="fetched",
            ticker_code="000858.SZ",
            data_source="东方财富",
            saved=8,
            message="库中没有 000858.SZ 的财报，已从东方财富补齐。",
        )
        with patch.object(ensure, "ensure_statements", return_value=fetched), \
             patch.object(ensure.statement_service, "query_financial_statements", return_value=[{"source_item_name": "营业收入"}]):
            result = ensure.query_statements_with_sync(ticker_code="000858.SZ", item_category="利润表")
        self.assertEqual(result["sync"]["message"], fetched["message"])
        self.assertEqual(result["items"][0]["source_item_name"], "营业收入")


if __name__ == "__main__":
    unittest.main()
