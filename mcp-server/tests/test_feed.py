"""RSSHub 根地址在 mcp-server 内拼接，不访问外网。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "local-tools" / "service"))

import feed_service  # noqa: E402


class FeedServiceTests(unittest.TestCase):
    def test_rsshub_route_uses_configured_base(self):
        def fake_rss(url: str):
            return [{"title": "标题", "url": "https://example.com/a", "summary": "摘要", "source_url": url}]

        with patch.dict(os.environ, {"DIGEST_RSSHUB_BASE": "https://rss.example"}, clear=False), \
             patch.object(feed_service, "_fetch_rss", side_effect=fake_rss):
            out = feed_service.collect_sources([{"kind": "rsshub", "route": "wechat/sogou/TencentNews", "label": "腾讯"}])
        self.assertEqual(out["error"], "")
        self.assertEqual(out["items"][0]["source_url"], "https://rss.example/wechat/sogou/TencentNews")
        self.assertEqual(out["items"][0]["source_label"], "腾讯")

    def test_rsshub_without_base_is_an_error(self):
        with patch.dict(os.environ, {"DIGEST_RSSHUB_BASE": ""}, clear=False):
            out = feed_service.collect_sources([{"kind": "rsshub", "route": "/wechat/sogou/TencentNews"}])
        self.assertIn("DIGEST_RSSHUB_BASE", out["error"])
        self.assertEqual(out["items"], [])


if __name__ == "__main__":
    unittest.main()
