"""digest job 校验与 TipTap 合并。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from digest_job import validate_job_body  # noqa: E402
from digest_render import markdown_to_tiptap_nodes, merge_into_document  # noqa: E402


class DigestRenderTests(unittest.TestCase):
    def test_markdown_heading_and_list(self):
        nodes = markdown_to_tiptap_nodes("# 每日资讯\n\n## 主题\n\n- 一条 **重点**\n")
        types = [n["type"] for n in nodes]
        self.assertIn("heading", types)
        self.assertIn("bulletList", types)

    def test_merge_newest_first(self):
        doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "旧"}]}]}
        frag = [{"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "新"}]}]
        merged = merge_into_document(doc, frag, "newest_first")
        self.assertEqual(merged["content"][0]["type"], "heading")

    def test_validate_job(self):
        body = validate_job_body({
            "enabled": True,
            "hour": 9,
            "minute": 30,
            "output_order": "newest_last",
            "topics": [{
                "name": "王者荣耀",
                "sources": [{"kind": "rss", "url": "https://example.com/feed.xml", "label": "官博"}],
            }],
        })
        self.assertTrue(body["enabled"])
        self.assertEqual(body["topics"][0]["name"], "王者荣耀")


if __name__ == "__main__":
    unittest.main()
