import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "local-office-tools" / "service"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "repository"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "endpoints"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

import document_service as office_docs
import document_content_service as document
from office_repository import set_client_factory
from test_invp_office import FakeOffice


class DocumentModelTests(unittest.TestCase):
    def test_markdown_roundtrip_and_format(self):
        doc = document.markdown_to_document("# 标题\n\n正文 **加粗** 与 *斜体*\n\n- 甲\n- 乙")
        self.assertEqual(document.document_to_markdown(doc), "# 标题\n\n正文 **加粗** 与 *斜体*\n\n- 甲\n- 乙")
        blocks = document.describe_blocks(doc)
        self.assertEqual([item["type"] for item in blocks], ["h1", "paragraph", "bullet"])
        self.assertIn("bold", blocks[1]["marks"])
        formatted = document.format_block(doc, 1, "h2", bold=False, italic=True)
        second = formatted["content"][1]
        self.assertEqual(second["type"], "heading")
        self.assertEqual(second["attrs"]["level"], 2)
        self.assertEqual(document._marks_in(second), {"italic"})

    def test_brief_layout_roundtrip(self):
        src = "\n".join([
            "---",
            "## 2026年09月26日 19:14:02",
            "### 港交所",
            "1. 【腾讯控股】回购（2026-09-25 17:22）",
            "继续回购。 🔗 [原文](https://example.com/a)",
            "○ 业务板块：游戏",
            "○ 财务指标：材料不足",
            "### 新浪财经",
            "2. 【腾讯控股】回购进展（发布时间未知）",
            "金额不变。",
        ])
        doc = document.markdown_to_document(src)
        kinds = [item["type"] for item in document.describe_blocks(doc)]
        self.assertEqual(kinds, ["rule", "h2", "h3", "ordered", "h3", "ordered"])
        first = doc["content"][3]
        self.assertNotIn("start", first.get("attrs") or {})
        second = doc["content"][5]
        self.assertEqual(second["attrs"]["start"], 2)
        item = first["content"][0]
        summary = item["content"][1]["content"]
        self.assertEqual(summary[0]["text"], "继续回购。 ")
        self.assertFalse(summary[0].get("marks"))
        self.assertEqual(summary[1]["text"], "🔗 ")
        self.assertTrue(any(mark["type"] == "small" for mark in summary[1]["marks"]))
        self.assertEqual(summary[2]["text"], "原文")
        self.assertTrue(any(mark["type"] == "small" for mark in summary[2]["marks"]))
        self.assertEqual(item["content"][2]["attrs"]["marker"], "circle")
        again = document.document_to_markdown(doc)
        self.assertIn("---", again)
        self.assertIn("2. 【腾讯控股】回购进展（发布时间未知）", again)
        self.assertIn("○ 业务板块：游戏", again)
        prepended = document.prepend_markdown(doc, "---\n\n## 更新")
        self.assertEqual(prepended["content"][0]["type"], "horizontalRule")
        self.assertEqual(prepended["content"][-1]["type"], "orderedList")

    def test_markdown_link_roundtrip(self):
        doc = document.markdown_to_document("- 概述 [原文](https://example.com/a)")
        paragraph = doc["content"][0]["content"][0]["content"][0]["content"]
        link = next(node for node in paragraph if any(mark.get("type") == "link" for mark in node.get("marks") or []))
        self.assertEqual(link["marks"][0]["attrs"]["href"], "https://example.com/a")
        self.assertIn("[原文](https://example.com/a)", document.document_to_markdown(doc))


class DocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_create_write_read_and_format(self):
        created = office_docs.create_document("年度预算", "纪要")
        self.assertTrue(created["created"]["document"])
        written = office_docs.write_document("年度预算", "纪要", "# 周会\n\n讨论进度", mode="replace")
        self.assertEqual(written["block_count"], 2)
        appended = office_docs.write_document("年度预算", "纪要", "- 甲\n- 乙", mode="append")
        self.assertEqual(appended["blocks"][-1]["type"], "bullet")
        prepended = office_docs.write_document("年度预算", "纪要", "---\n\n## 置顶", mode="prepend")
        self.assertEqual(prepended["blocks"][0]["type"], "rule")
        self.assertEqual(prepended["blocks"][1]["type"], "h2")
        self.assertEqual(prepended["blocks"][-1]["type"], "bullet")
        formatted = office_docs.format_document("年度预算", "纪要", 2, "h2", bold=True)
        self.assertEqual(formatted["blocks"][2]["type"], "h2")
        self.assertIn("bold", formatted["blocks"][2]["marks"])
        read = office_docs.read_document("年度预算", "纪要")
        self.assertIn("周会", read["markdown"])
        self.assertEqual(read["blocks"][2]["type"], "h2")

    def test_create_duplicate_rejected(self):
        office_docs.create_document("P", "纪要")
        with self.assertRaises(ValueError):
            office_docs.create_document("P", "纪要")

    def test_read_missing_rejected(self):
        office_docs.create_document("P", "纪要")
        with self.assertRaises(ValueError):
            office_docs.read_document("P", "没有")

    def test_document_tools_are_separate_from_finance(self):
        docs = FastMCP("invp-documents")
        from document_endpoint import register_tools
        register_tools(docs)
        names = {tool.name for tool in docs._tool_manager.list_tools()}
        self.assertEqual(names, {"create_document", "read_document", "write_document", "format_document"})


if __name__ == "__main__":
    unittest.main()
