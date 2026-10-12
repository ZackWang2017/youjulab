"""单篇公众号文章：只接受 mp.weixin.qq.com，并抽出账号与时间。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "local-tools" / "service"))

import wechat_article_repository as article_repo  # noqa: E402
import wechat_article_service as article_service  # noqa: E402

_HTML = """
<html><head>
<meta property="og:title" content="备用标题" />
</head><body>
<h1 id="activity-name">回购进展</h1>
<a id="js_name">腾讯</a>
<em id="publish_time">2024-09-26</em>
<div id="js_content">
<p>公司继续回购。</p>
<h2>说明</h2>
<p>金额不变。</p>
<script>secret = 1</script>
</div>
</body></html>
"""


class WechatArticleTests(unittest.TestCase):
    def test_structured_article(self) -> None:
        with patch.object(article_repo, "fetch_article", return_value={
            "url": "https://mp.weixin.qq.com/s/abc",
            "status": 200,
            "body": _HTML,
            "truncated": False,
        }):
            out = article_service.read_wechat_article("https://mp.weixin.qq.com/s/abc")
        self.assertNotIn("error", out)
        self.assertEqual(out["title"], "回购进展")
        self.assertEqual(out["account"], "腾讯")
        self.assertEqual(out["published_at"], "2024-09-26")
        self.assertEqual(out["sections"], [
            {"heading": "", "level": 0, "text": "公司继续回购。"},
            {"heading": "说明", "level": 2, "text": "金额不变。"},
        ])
        self.assertNotIn("secret", str(out))

    def test_rejects_other_hosts(self) -> None:
        out = article_service.read_wechat_article("https://example.com/s/abc")
        self.assertIn("mp.weixin.qq.com", out["error"])
        self.assertEqual(out["sections"], [])

    def test_blocked_page(self) -> None:
        with patch.object(article_repo, "fetch_article", return_value={
            "url": "https://mp.weixin.qq.com/s/abc",
            "status": 200,
            "body": "环境异常",
            "truncated": False,
        }):
            out = article_service.read_wechat_article("https://mp.weixin.qq.com/s/abc")
        self.assertIn("拦截", out["error"])


if __name__ == "__main__":
    unittest.main()
