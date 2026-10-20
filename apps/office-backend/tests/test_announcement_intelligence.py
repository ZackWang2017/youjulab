"""Pure rule tests for the native announcement outline pipeline."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from announcement_intelligence.layout import remove_running_headers  # noqa: E402
from announcement_intelligence.rule_config import rule_pack_for  # noqa: E402
from announcement_intelligence.rules import apply_rules  # noqa: E402
from announcement_intelligence.schema import LayoutBlock, validate_rules  # noqa: E402
from announcement_intelligence.selector import selection_from_model  # noqa: E402
from office_pdf_vector import extract_layout_blocks  # noqa: E402


def block(id: str, text: str, *, page: int = 1, order: int = 0, size: float = 10, bold: bool = False, y: float = .4, x1: float = 400) -> LayoutBlock:
    return LayoutBlock(id, page, order, text, size, bold, 40, y * 800, x1, y * 800 + 14, 40, y)


class AnnouncementRuleTests(unittest.TestCase):
    def test_numbered_and_bold_headings_keep_reading_order(self):
        rows = [
            block("a", "第一章 公司概况", order=1, size=14, y=.15),
            block("b", "这是经营情况的正文，长度足够且不应被识别为标题。", order=2, size=10, y=.40),
            block("c", "核心经营数据", order=3, size=12, bold=True, y=.65, x1=180),
        ]
        _, rules = rule_pack_for("generic")
        rules["min_font_percentile"] = 90
        result = apply_rules(rows, rules)
        self.assertEqual([item["id"] for item in result], ["a", "c"])
        self.assertEqual(result[0]["kind"], "numbered")
        self.assertEqual(result[1]["kind"], "bold")

    def test_repeated_running_header_is_dropped(self):
        rows = [
            block("header-1", "某公司 2026 年年度报告", page=1, order=1, y=.02),
            block("header-2", "某公司 2026 年年度报告", page=2, order=2, y=.02),
            block("header-3", "某公司 2026 年年度报告", page=3, order=3, y=.02),
            block("body", "一、重要提示", page=1, order=4, size=14),
        ]
        self.assertEqual([item.id for item in remove_running_headers(rows)], ["body"])

    def test_rule_dsl_rejects_executable_fields(self):
        with self.assertRaises(ValueError):
            validate_rules({"python": "import os"})

    def test_model_can_only_select_configured_rule_pack(self):
        document_type, rule_pack, rules, _ = selection_from_model({
            "document_type": "annual_report",
            "rule_pack": "financial_report",
            "rules": {"number_patterns": [".*"]},
            "reason": "财务报告版式",
        })
        self.assertEqual((document_type, rule_pack), ("annual_report", "financial_report"))
        self.assertNotEqual(rules["number_patterns"], [".*"])

    def test_common_numbering_and_single_line_bold_heading_are_kept(self):
        _, rules = rule_pack_for("generic")
        rows = [
            block("chapter", "第一章 总则", order=1, size=14, y=.08),
            block("chinese", "一、经营情况讨论与分析", order=2, size=12, y=.16),
            block("paren-chinese", "（一）主营业务", order=3, size=11, y=.24),
            block("decimal", "1.2 经营成果", order=4, size=11, y=.32),
            block("deep-decimal", "1.2.3 境内业务", order=5, size=11, y=.40),
            block("paren-number", "（1）主要产品", order=6, size=11, y=.48),
            block("roman", "IV. 风险因素", order=7, size=11, y=.56),
            block("english", "Appendix A. Definitions", order=8, size=11, y=.64),
            block("bold", "核心会计数据", order=9, size=10, bold=True, y=.72, x1=180),
            block("body", "1. 本公司营业收入增长，主要来自销售规模扩大，具体情况如下。", order=10, size=10, y=.80),
            block("table", "单位：人民币万元", order=11, size=10, y=.88),
        ]
        titles = [item["title"] for item in apply_rules(rows, rules)]
        self.assertEqual(titles, [
            "第一章 总则", "一、经营情况讨论与分析", "（一）主营业务", "1.2 经营成果",
            "1.2.3 境内业务", "（1）主要产品", "IV. 风险因素", "Appendix A. Definitions", "核心会计数据",
        ])

    def test_non_bold_numbered_and_bullet_list_items_are_kept(self):
        _, rules = rule_pack_for("generic")
        rows = [
            block("numeric", "1. 非加粗数字列表项。", order=1, y=.12),
            block("decimal", "1.1. 非加粗多级编号。", order=2, y=.22),
            block("full-width", "（２）全角数字编号。", order=3, y=.32),
            block("bracket", "【3】方括号数字编号。", order=4, y=.42),
            block("bullet", "• 非加粗圆点列表项。", order=5, y=.52),
            block("arrow", "➢ 非加粗箭头列表项。", order=6, y=.62),
            block("body", "本公司认为本次事项符合相关规定，后续将持续推进。", order=7, y=.72),
        ]
        result = apply_rules(rows, rules)
        self.assertEqual([item["title"] for item in result], [
            "1. 非加粗数字列表项。", "1.1. 非加粗多级编号。", "（２）全角数字编号。",
            "【3】方括号数字编号。", "• 非加粗圆点列表项。", "➢ 非加粗箭头列表项。",
        ])
        self.assertTrue(all(item["kind"] == "numbered" for item in result))

    def test_full_width_bold_body_line_is_not_a_heading(self):
        _, rules = rule_pack_for("announcement")
        rows = [
            block("heading", "建议票据发行", order=1, size=13, bold=True, y=.10),
            LayoutBlock("body-1", 1, 2, "由于截至本公告刊发日期并无就建议票据发行订立具约束力的协议，故建议票据发", 12, True, 70, 120, 524, 134, 70, .15),
            LayoutBlock("body-2", 1, 3, "行可能未必落实。建议票据发行的完成须取决于多项因素，包括但不限于全球市场", 12, True, 70, 140, 524, 154, 70, .175),
            block("next", "释义", order=4, size=13, bold=True, y=.25),
        ]
        self.assertEqual([item["title"] for item in apply_rules(rows, rules)], ["建议票据发行", "释义"])

    def test_native_layout_exposes_style_and_reading_order(self):
        import pymupdf
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), "Chapter One", fontsize=16)
        page.insert_text((72, 112), "Body text.", fontsize=10)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as file:
            document.save(file.name)
            document.close()
            rows = extract_layout_blocks(Path(file.name))
        self.assertEqual([row["text"] for row in rows], ["Chapter One", "Body text."])
        self.assertGreater(rows[0]["font_size"], rows[1]["font_size"])


if __name__ == "__main__":
    unittest.main()
