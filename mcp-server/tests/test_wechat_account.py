"""指定公众号：RSSHub 走 RSS，weread 走微信读书登录态。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "local-tools" / "service"))

import wechat_account_repository as account_repo  # noqa: E402
import wechat_account_service as account_service  # noqa: E402
import weread_session_repository as weread_repo  # noqa: E402
import weread_session_service as weread_service  # noqa: E402

_RSS = """<?xml version="1.0"?>
<rss><channel>
<item>
<title>回购公告</title>
<link>https://mp.weixin.qq.com/s/abc</link>
<pubDate>Thu, 26 Sep 2024 00:00:00 GMT</pubDate>
<description>&lt;p&gt;摘要正文&lt;/p&gt;</description>
</item>
</channel></rss>
"""


class WechatAccountTests(unittest.TestCase):
    def test_rsshub_url_and_items(self) -> None:
        with patch.dict(os.environ, {"DIGEST_RSSHUB_BASE": "https://rss.example/"}, clear=False), \
             patch.object(account_repo, "fetch_feed", return_value={"items": account_repo._parse_rss(_RSS)}) as fetch:
            out = account_service.list_wechat_account("TencentNews", source="rsshub", num=5)
        fetch.assert_called_once_with("https://rss.example/wechat/sogou/TencentNews")
        self.assertEqual(out["source"], "rsshub")
        self.assertEqual(out["results"][0]["title"], "回购公告")
        self.assertEqual(out["results"][0]["url"], "https://mp.weixin.qq.com/s/abc")
        self.assertEqual(out["results"][0]["published_at"], "2024-09-26 08:00")
        self.assertEqual(out["results"][0]["summary"], "摘要正文")

    def test_weread_uses_session(self) -> None:
        article = {"title": "早报", "url": "https://mp.weixin.qq.com/s/abc", "published_at": "", "summary": ""}
        with patch.object(weread_service, "list_articles", return_value={"error": "", "results": [article]}) as listed:
            out = account_service.list_wechat_account("MP_WXS_1", source="weread", num=5)
        listed.assert_called_once_with("MP_WXS_1", 5)
        self.assertEqual(out["results"][0]["title"], "早报")

    def test_weread_missing_session_requests_interactive_login(self) -> None:
        with patch.object(weread_repo, "load_cookie", return_value=""):
            out = weread_service.list_shelf_articles(5)
        self.assertTrue(out["login_required"])
        self.assertEqual(out["accounts"], [])
        self.assertIn("尚未登录", out["error"])

    def test_weread_review_link(self) -> None:
        payload = {"reviews": [{"subReviews": [{"review": {
            "reviewId": "MP_WXS_1_token~1",
            "createTime": 1727308800,
            "mpInfo": {"title": "早报", "content": "摘要"},
        }}]}]}
        items = weread_repo._articles_from_reviews(payload, "MP_WXS_1")
        self.assertEqual(items[0]["url"], "https://mp.weixin.qq.com/s/token_1")
        self.assertEqual(items[0]["published_at"], "2024-09-26 08:00")
        self.assertEqual(items[0]["summary"], "摘要")

    def test_blocked_article_list_uses_the_latest_page(self) -> None:
        cover = {"title": "早报", "reviewId": "MP_WXS_1_token"}
        html = """
        <meta property="og:title" content="早报" />
        <meta property="og:url" content="https://mp.weixin.qq.com/s?__biz=abc&amp;idx=1" />
        ori_create_time: '1727308800' * 1,
        <div id="js_content"><p>摘要正文</p></div>
        """
        pages = [
            {"payload": {"errCode": -2041, "errMsg": "-2041"}, "error": "", "auth_expired": False},
            {"payload": cover, "error": "", "auth_expired": False},
        ]
        with patch.object(weread_repo, "_get_json", side_effect=pages), \
             patch.object(weread_repo, "_get_html", return_value=html):
            out = weread_repo.latest_articles("cookie", "MP_WXS_1")
        item = out["items"][0]
        self.assertEqual(item["title"], "早报")
        self.assertEqual(item["url"], "https://mp.weixin.qq.com/s?__biz=abc&idx=1")
        self.assertEqual(item["published_at"], "2024-09-26 08:00")
        self.assertEqual(item["summary"], "摘要正文")
        self.assertEqual(out["error"], "")

    def test_article_list_auth_error_still_reads_cover(self) -> None:
        cover = {"title": "早报", "reviewId": "MP_WXS_1_token"}
        html = '<meta property="og:title" content="早报" /><div id="js_content"><p>摘要正文</p></div>'
        pages = [
            {"payload": {"errCode": -2012}, "error": "微信读书登录已过期。", "auth_expired": True},
            {"payload": cover, "error": "", "auth_expired": False},
        ]
        with patch.object(weread_repo, "_get_json", side_effect=pages), \
             patch.object(weread_repo, "_get_html", return_value=html):
            out = weread_repo.latest_articles("cookie", "MP_WXS_1")
        self.assertFalse(out["auth_expired"])
        self.assertEqual(out["items"][0]["title"], "早报")
        self.assertEqual(out["items"][0]["summary"], "摘要正文")

    def test_shelf_keeps_official_accounts(self) -> None:
        payload = {"books": [
            {"bookId": "MP_WXS_1", "title": "腾讯财经"},
            {"bookId": "CB_1", "title": "一本小说"},
            {"bookId": "MP_WXS_FEATURED_ARTICLES", "title": "精选"},
            {"bookId": "MP_WXS_1", "title": "腾讯财经"},
        ]}
        with patch.object(weread_repo, "_get_json", return_value={"payload": payload, "error": "", "auth_expired": False}):
            out = weread_repo.list_official_accounts("cookie")
        self.assertEqual(out["accounts"], [{"id": "MP_WXS_1", "title": "腾讯财经"}])

    def test_shelf_articles_reads_every_account(self) -> None:
        books = [{"id": "MP_WXS_1", "title": "腾讯财经"}, {"id": "MP_WXS_2", "title": "腾讯科技"}]
        articles = {
            "MP_WXS_1": {"items": [{"title": "早报", "url": "https://mp.weixin.qq.com/s/a", "published_at": "", "summary": ""}], "error": "", "auth_expired": False},
            "MP_WXS_2": {"items": [], "error": "没有文章。", "auth_expired": False},
        }
        with patch.object(weread_repo, "load_cookie", return_value="cookie"), \
             patch.object(weread_repo, "list_official_accounts", return_value={"accounts": books, "error": "", "auth_expired": False}), \
             patch.object(weread_repo, "latest_articles", side_effect=lambda cookie, book_id: articles[book_id]):
            out = weread_service.list_shelf_articles(5)
        self.assertEqual(out["error"], "")
        self.assertEqual(out["accounts"][0]["results"][0]["title"], "早报")
        self.assertEqual(out["accounts"][1]["error"], "没有文章。")

    def test_missing_base(self) -> None:
        with patch.dict(os.environ, {"DIGEST_RSSHUB_BASE": ""}, clear=False):
            out = account_service.list_wechat_account("TencentNews")
        self.assertIn("DIGEST_RSSHUB_BASE", out["error"])
        self.assertEqual(out["results"], [])


if __name__ == "__main__":
    unittest.main()
