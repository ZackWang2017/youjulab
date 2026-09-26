import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "office-api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

import office_docs
import office_document as document
from office_sheet_client import set_client_factory
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
        formatted = office_docs.format_document("年度预算", "纪要", 0, "h2", bold=True)
        self.assertEqual(formatted["blocks"][0]["type"], "h2")
        self.assertIn("bold", formatted["blocks"][0]["marks"])
        read = office_docs.read_document("年度预算", "纪要")
        self.assertIn("周会", read["markdown"])
        self.assertEqual(read["blocks"][0]["type"], "h2")

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
        office_docs.register_tools(docs)
        names = {tool.name for tool in docs._tool_manager.list_tools()}
        self.assertEqual(names, {"create_document", "read_document", "write_document", "format_document"})


if __name__ == "__main__":
    unittest.main()
