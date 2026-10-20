"""A 股财报抓取：宽表拆行、新浪解析、东方财富失败后的回退。不访问数据库和外网。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))

import a_share_statement_repository as statements  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_suffix_and_bare_code(self):
        self.assertEqual(statements.normalize_a_ticker("600519.sh"), ("600519.SH", "SH"))
        self.assertEqual(statements.normalize_a_ticker("596"), ("000596.SZ", "SZ"))
        self.assertEqual(statements.normalize_a_ticker("830001"), ("830001.BJ", "BJ"))
        self.assertEqual(statements.normalize_a_ticker("920001"), ("920001.BJ", "BJ"))
        with self.assertRaises(ValueError):
            statements.normalize_a_ticker("00700.HK")


class EastMoneyUnpivotTests(unittest.TestCase):
    def test_splits_wide_row_and_skips_yoy_and_null(self):
        row = {
            "REPORT_DATE": "2026-06-30 00:00:00",
            "REPORT_TYPE": "中报",
            "MONETARYFUNDS": "53518798979.08",
            "MONETARYFUNDS_YOY": 3.6,
            "TOTAL_ASSETS": 309050784569.31,
            "UNKNOWN_FIELD": 12,
            "ACCOUNTS_RECE": None,
        }
        records = statements.eastmoney_row_to_records(row, ticker_code="600519.SH", market_code="SH", item_category="资产负债表")
        by_name = {item.source_item_name: item for item in records}
        self.assertEqual(set(by_name), {"货币资金", "资产总计", "UNKNOWN_FIELD"})
        cash = by_name["货币资金"]
        self.assertEqual(cash.report_period, "2季报")
        self.assertEqual(cash.fiscal_year, 2026)
        self.assertEqual(cash.market_code, "SH")
        self.assertEqual(str(cash.source_value), "53518798979.08")
        self.assertEqual(cash.source_unit, "元")

    def test_unknown_period_is_dropped(self):
        row = {"REPORT_DATE": "2026-06-30", "REPORT_TYPE": "月报", "MONETARYFUNDS": 1}
        self.assertEqual(statements.eastmoney_row_to_records(row, ticker_code="600519.SH", market_code="SH", item_category="资产负债表"), [])


class SinaParseTests(unittest.TestCase):
    def test_keeps_consolidated_items_with_chinese_titles(self):
        payload = {"result": {"status": {"code": 0}, "data": {
            "report_date": [{"date_value": "20251231", "date_type": 4}],
            "report_list": {"20251231": {
                "rType": "合并期末",
                "data": [
                    {"item_title": "流动资产", "item_value": "", "item_field": ""},
                    {"item_title": "货币资金", "item_value": "100.5", "item_field": "CURFDS"},
                    {"item_title": "应收账款", "item_value": None, "item_field": "ACCOUNTS"},
                ],
            }},
        }}}
        records = statements.sina_report_to_records(payload, ticker_code="600519.SH", market_code="SH", item_category="资产负债表")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_item_name, "货币资金")
        self.assertEqual(records[0].report_period, "年报")
        self.assertEqual(records[0].fiscal_year, 2025)

    def test_parent_company_report_is_skipped(self):
        payload = {"result": {"data": {
            "report_date": [{"date_value": "20251231", "date_type": 4}],
            "report_list": {"20251231": {"rType": "母公司期末", "data": [{"item_title": "货币资金", "item_value": "1"}]}},
        }}}
        self.assertEqual(statements.sina_report_to_records(payload, ticker_code="600519.SH", market_code="SH", item_category="资产负债表"), [])


class FallbackTests(unittest.TestCase):
    def test_auto_uses_sina_when_eastmoney_fails(self):
        sina_row = statements.em.FinancialStatementRecord(
            "600519.SH", "SH", 2025, "年报", "利润表", "营业收入", "营业收入", None, None, "元", "元",
        )
        with patch.object(statements, "fetch_eastmoney_statements", side_effect=RuntimeError("down")), \
             patch.object(statements, "fetch_sina_statements", return_value=[sina_row]) as sina, \
             patch.object(statements.em, "save_financial_statements", return_value=1) as saved:
            summary = statements.sync_a_share_financial_statements("600519.SH")
        sina.assert_called_once()
        saved.assert_called_once()
        self.assertEqual(summary["data_source"], "新浪财经")
        self.assertEqual(summary["income_statement"], 1)

    def test_explicit_eastmoney_does_not_fall_back(self):
        with patch.object(statements, "fetch_eastmoney_statements", side_effect=RuntimeError("down")), \
             patch.object(statements, "fetch_sina_statements") as sina:
            with self.assertRaises(RuntimeError):
                statements.sync_a_share_financial_statements("600519", source="eastmoney")
        sina.assert_not_called()


if __name__ == "__main__":
    unittest.main()
