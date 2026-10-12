"""AI 图表生成：复用共享表结构缓存并验证模型输出。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from server import create_app  # noqa: E402
from sheet_chart import validate_chart_plan  # noqa: E402
from sheet_cache import normalize_cache_body, structure_fingerprint  # noqa: E402
from store import MemoryStore  # noqa: E402


class SheetChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        signed = self.client.post("/auth/register", json={"email": "chart@example.com", "password": "secret1"}).json()
        self.user = signed["user"]
        self.headers = {"Authorization": f"Bearer {signed['token']}"}
        self.asset_id = self.user["last_assets"]["sheet"]
        fields = normalize_cache_body({
            "sheet_id": "sheet-1",
            "sheet_name": "利润表",
            "origin": {"row": 0, "col": 0},
            "range": "A1:C3",
            "values": [["季度", "收入", "同比"], ["Q1", 100, 0.1], ["Q2", 120, 0.2]],
        }, revision=3)
        fields["profile"] = "第一行为表头，A 列季度，B 列收入，C 列同比。"
        fields["profile_fingerprint"] = structure_fingerprint(fields)
        self.store.upsert_sheet_cache(self.user["id"], self.asset_id, fields)

    @staticmethod
    def chart_plan() -> dict:
        return {
            "title": "季度收入与同比",
            "chart_type": "bar",
            "source_range": "A1:C3",
            "x_axis": {"name": "季度", "values": ["Q1", "Q2"]},
            "y_axes": [
                {"name": "收入", "format": "currency"},
                {"name": "同比", "format": "percent"},
            ],
            "series": [
                {"name": "收入", "type": "bar", "data": [100, 120], "y_axis": 0},
                {"name": "同比", "type": "line", "data": [0.1, 0.2], "y_axis": 1},
            ],
            "summary": "收入柱状图叠加同比折线。",
        }

    def test_chart_endpoint_reuses_cache_and_selection(self) -> None:
        captured = {}

        async def fake_events(messages, **kwargs):
            captured["payload"] = json.loads(messages[1]["content"])
            captured["kwargs"] = kwargs
            yield {"type": "delta", "part": "answer", "text": json.dumps(self.chart_plan(), ensure_ascii=False)}

        with patch("server.model_configured", return_value=True), \
             patch("sheet_chart.iter_chat_events", fake_events), \
             patch("sheet_chart.model_name", return_value="test-model"):
            response = self.client.post(
                f"/assets/{self.asset_id}/assistant/chart",
                headers=self.headers,
                json={
                    "sheet_id": "sheet-1",
                    "description": "收入柱状，同比折线",
                    "selection": {"address": "A1:C3", "rows": 3, "cols": 3, "values": [["季度", "收入", "同比"], ["Q1", 100, 0.1], ["Q2", 120, 0.2]]},
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["context_source"], "shared_sheet_cache")
        self.assertTrue(body["profile_reused"])
        self.assertEqual(body["model_name"], "test-model")
        self.assertEqual(body["chart"]["series"][1]["type"], "line")
        self.assertEqual(captured["payload"]["profile"], "第一行为表头，A 列季度，B 列收入，C 列同比。")
        self.assertEqual(captured["payload"]["selection"]["address"], "A1:C3")
        self.assertEqual(captured["payload"]["sheet"]["values"][2], ["Q2", 120, 0.2])
        self.assertTrue(captured["kwargs"]["json_object"])

    def test_streams_reasoning_before_chart(self) -> None:
        async def fake_stream(cache, description, selection):
            self.assertEqual(description, "分析季度趋势")
            yield {"type": "delta", "part": "reasoning", "text": "先识别季度和金额列。"}
            yield {
                "type": "done",
                "model_name": "test-model",
                "context_source": "shared_sheet_cache",
                "profile_reused": True,
                "chart": self.chart_plan(),
            }

        with patch("server.model_configured", return_value=True), \
             patch("server.iter_sheet_chart_events", fake_stream):
            response = self.client.post(
                f"/assets/{self.asset_id}/assistant/chart/stream",
                headers=self.headers,
                json={"sheet_id": "sheet-1", "description": "分析季度趋势"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"part": "reasoning"', response.text)
        self.assertIn('"type": "done"', response.text)
        self.assertLess(response.text.index('"part": "reasoning"'), response.text.index('"type": "done"'))

    def test_saves_lists_and_renames_chart_per_sheet(self) -> None:
        created = self.client.post(
            f"/assets/{self.asset_id}/sheet-charts",
            headers=self.headers,
            json={
                "sheet_id": "sheet-1",
                "name": "季度图",
                "description": "收入柱状，同比折线",
                "chart": self.chart_plan(),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        chart_id = created.json()["id"]

        listed = self.client.get(
            f"/assets/{self.asset_id}/sheet-charts?sheet_id=sheet-1",
            headers=self.headers,
        )
        self.assertEqual([item["name"] for item in listed.json()["charts"]], ["季度图"])
        other = self.client.get(
            f"/assets/{self.asset_id}/sheet-charts?sheet_id=sheet-2",
            headers=self.headers,
        )
        self.assertEqual(other.json()["charts"], [])

        renamed = self.client.put(
            f"/assets/{self.asset_id}/sheet-charts/{chart_id}",
            headers=self.headers,
            json={"name": "季度经营趋势"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "季度经营趋势")

    def test_rejects_mismatched_series_and_multi_series_pie(self) -> None:
        base = {
            "title": "图表",
            "chart_type": "bar",
            "x_axis": {"name": "季度", "values": ["Q1", "Q2"]},
            "series": [{"name": "收入", "type": "bar", "data": [100], "y_axis": 0}],
        }
        with self.assertRaisesRegex(ValueError, "长度"):
            validate_chart_plan(base)
        with self.assertRaisesRegex(ValueError, "饼图"):
            validate_chart_plan({
                **base,
                "chart_type": "pie",
                "series": [
                    {"name": "收入", "type": "pie", "data": [100, 120]},
                    {"name": "利润", "type": "pie", "data": [20, 30]},
                ],
            })

    def test_requires_owned_sheet_cache(self) -> None:
        with patch("server.model_configured", return_value=True):
            response = self.client.post(
                f"/assets/{self.asset_id}/assistant/chart",
                headers=self.headers,
                json={"sheet_id": "missing", "description": "生成图表"},
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
