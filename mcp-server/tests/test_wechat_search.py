"""搜狗微信搜索：只解析第一页 HTML。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "local-tools" / "service"))

import wechat_search_repository as search_repo  # noqa: E402
import wechat_search_service as search_service  # noqa: E402

_HTML = """
<html><body>
<ul class="news-list">
<li>
<div class="txt-box">
<h3><a href="https://mp.weixin.qq.com/s/abc123">腾讯回购</a></h3>
<p class="txt-info">公司宣布回购计划</p>
<div class="s-p" t="1727308800">
<span class="all-time-y2">2024-9-26</span>
<a href="/account">腾讯</a>
</div>
</div>
</li>
</ul>
</body></html>
"""


class WechatSearchTests(unittest.TestCase):
    def test_parses_first_page(self) -> None:
        with patch.object(search_repo, "fetch_search_page", return_value={"status": 200, "body": _HTML}):
            out = search_service.search_wechat_articles("腾讯", num=5)
        self.assertNotIn("error", out)
        self.assertEqual(out["results"], [{
            "title": "腾讯回购",
            "account": "腾讯",
            "published_at": "2024-09-26 08:00",
            "url": "https://mp.weixin.qq.com/s/abc123",
            "url_kind": "wechat",
            "snippet": "公司宣布回购计划",
        }])

    def test_blocked_page(self) -> None:
        with patch.object(search_repo, "fetch_search_page", return_value={"status": 200, "body": "请输入验证码"}):
            out = search_service.search_wechat_articles("腾讯")
        self.assertIn("验证", out["error"])
        self.assertEqual(out["results"], [])


if __name__ == "__main__":
    unittest.main()
