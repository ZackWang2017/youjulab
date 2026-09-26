"""选区翻译：术语覆盖、数字校对、上下文不入库要求、勾选开关。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from pdf_glossary import load_builtin, match_terms  # noqa: E402
from pdf_translate import neighbor_context, qa_issues, translate_segment  # noqa: E402


class GlossaryTests(unittest.TestCase):
    def test_project_term_overrides_builtin(self):
        builtin = load_builtin()
        project = {
            "en": "revenue",
            "en_key": "revenue",
            "zh": "公司收入",
            "forbidden": ["销售收入"],
            "keep": False,
            "source": "project",
        }
        merged = {term["en_key"]: term for term in builtin}
        merged[project["en_key"]] = project
        hits = match_terms("Revenue grew.", list(merged.values()))
        revenue = [hit for hit in hits if hit["en_key"] == "revenue"]
        self.assertEqual(len(revenue), 1)
        self.assertEqual(revenue[0]["zh"], "公司收入")


class QaTests(unittest.TestCase):
    def test_changed_number_fails(self):
        issues = qa_issues("Revenue was $1,200.", "营业收入为 1,250 美元。", [{
            "en": "revenue", "en_key": "revenue", "zh": "营业收入", "forbidden": [], "keep": False,
        }])
        self.assertTrue(any("1200" in item for item in issues))

    def test_context_number_is_not_required(self):
        issues = qa_issues("Revenue was $1,200.", "营业收入为 1,200 美元。", [{
            "en": "revenue", "en_key": "revenue", "zh": "营业收入", "forbidden": [], "keep": False,
        }])
        self.assertEqual(issues, [])
        self.assertNotIn("9,999", "".join(issues))

    def test_neighbor_context_drops_selection_and_keeps_nearby(self):
        chunks = [
            {"page": 4, "ord": 0, "text": "Prior table total 9999."},
            {"page": 5, "ord": 1, "text": "Heading. Revenue was $1,200. Footnote."},
            {"page": 6, "ord": 2, "text": "Next year outlook."},
        ]
        text = neighbor_context(chunks, 5, "Revenue was $1,200.")
        self.assertIn("Heading", text)
        self.assertIn("Footnote", text)
        self.assertNotIn("Revenue was $1,200.", text)
        self.assertIn("9999", text)


class TranslatePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_qa_is_not_stored_and_passed_qa_is_recalled(self):
        store: list[dict] = []

        async def complete(messages):
            user = messages[-1]["content"]
            if "简要解释" in messages[0]["content"]:
                return "这是营业收入的说明。", ""
            if "上一稿未通过" in user:
                return "营业收入为 9 美元。", ""
            return "营业收入为 9 美元。", ""

        def remember(source, target, terms):
            store.append({"source": source, "target": target, "terms": terms})

        bad = await translate_segment(
            "Revenue was $1,200.",
            page=1,
            document_id="doc",
            project_id="p",
            complete=complete,
            chunks=[],
            memory=[],
            terms=[{
                "en": "revenue", "en_key": "revenue", "zh": "营业收入", "forbidden": [], "keep": False,
            }],
            remember=remember,
        )
        self.assertTrue(bad["issues"])
        self.assertEqual(store, [])
        self.assertNotIn("## 解释", bad["answer"])

        async def good_complete(messages):
            user = messages[-1]["content"]
            if "简要解释" in messages[0]["content"]:
                return "这是营业收入的说明。", ""
            return "营业收入为 1,200 美元。", ""

        good = await translate_segment(
            "Revenue was $1,200.",
            page=1,
            document_id="doc",
            project_id="p",
            complete=good_complete,
            chunks=[],
            memory=[],
            terms=[{
                "en": "revenue", "en_key": "revenue", "zh": "营业收入", "forbidden": [], "keep": False,
            }],
            remember=remember,
        )
        self.assertEqual(good["issues"], [])
        self.assertEqual(len(store), 1)
        self.assertIn("## 解释", good["answer"])

        recalled = {"hit": False}

        async def second(messages):
            user = messages[-1]["content"]
            if store and store[0]["target"] in user:
                recalled["hit"] = True
            if "简要解释" in messages[0]["content"]:
                return "说明。", ""
            return "营业收入为 1,200 美元。", ""

        await translate_segment(
            "Revenue was $1,200.",
            page=2,
            document_id="doc",
            project_id="p",
            complete=second,
            chunks=[],
            memory=[{"source": store[0]["source"], "target": store[0]["target"]}],
            terms=[{
                "en": "revenue", "en_key": "revenue", "zh": "营业收入", "forbidden": [], "keep": False,
            }],
            remember=remember,
        )
        self.assertTrue(recalled["hit"])

    def test_translate_false_does_not_enter_pipeline(self):
        source = (HERE / "server.py").read_text()
        gate = source.split("if body.translate:", 1)[1].split("async for event in iter_pdf_ask_events", 1)[0]
        self.assertIn("iter_pdf_translate_events", gate)
        self.assertIn("return", gate)
        self.assertIn("translate: bool = False", source)
