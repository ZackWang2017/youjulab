"""digest job 校验与 TipTap 合并。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from unittest.mock import patch

from digest_job import _commit_digest, validate_job_body  # noqa: E402
from digest_render import markdown_to_tiptap_nodes, merge_into_document  # noqa: E402
from store import MemoryStore  # noqa: E402


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

    def test_commit_archives_when_merged_document_is_too_large(self):
        store = MemoryStore()
        user = store.create_user("digest@example.com", "secret1")
        project = store.create_project(user["id"], "项目")
        asset = store.create_asset(user["id"], project["id"], "document", "日报", {"type": "doc", "content": [{"type": "paragraph"}]})
        store.update_user(user["id"], last_assets={"document": asset["id"]})
        job = {"user_id": user["id"], "document_id": asset["id"], "enabled": True, "output_order": "newest_first", "topics": []}
        store.upsert_digest_job(user["id"], asset["id"], job)
        with patch("document_archive.MAX_DOCUMENT_BYTES", 80), patch("digest_job.MAX_DOCUMENT_BYTES", 80):
            result = _commit_digest(store, job, asset, "每日资讯", "新的一条很长很长很长很长很长很长", [])
        self.assertIn("_bak_", result["archived_name"])
        self.assertNotEqual(result["document_id"], asset["id"])
        archived = store.asset(user["id"], asset["id"])
        fresh = store.asset(user["id"], result["document_id"])
        self.assertEqual(archived["name"], result["archived_name"])
        self.assertEqual(fresh["name"], "日报")
        self.assertEqual(store.get_digest_job(user["id"], result["document_id"])["document_id"], result["document_id"])
        self.assertIsNone(store.get_digest_job(user["id"], asset["id"]))


if __name__ == "__main__":
    unittest.main()
