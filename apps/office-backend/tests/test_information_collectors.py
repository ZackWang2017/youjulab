"""Offline contract checks for local information collectors."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from information.collectors import feed_service, page_reader, preset_news_service, security_search_service
from information.collectors import weread_session_repository as repository
from information.collectors import weread_session_service as service


class WereadIsolationTests(unittest.TestCase):
    def setUp(self):
        service._login_sessions.clear()

    def tearDown(self):
        service._login_sessions.clear()

    def test_cookie_files_and_browser_profiles_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(repository, "user_data_root", return_value=Path(directory)):
                repository.save_cookie("wr_skey=alice", "alice")
                repository.save_cookie("wr_skey=bob", "bob")
                self.assertEqual(repository.load_cookie("alice"), "wr_skey=alice")
                self.assertEqual(repository.load_cookie("bob"), "wr_skey=bob")
                repository.save_cookie("", "alice")
                self.assertEqual(repository.load_cookie("bob"), "wr_skey=bob")
                self.assertNotEqual(repository.browser_profile_path("alice"), repository.browser_profile_path("bob"))
                self.assertTrue(repository.session_directory("../../outside").is_relative_to(Path(directory)))
                with self.assertRaises(ValueError):
                    repository.load_cookie("")

    def test_pending_sessions_are_not_shared(self):
        with patch.object(repository, "load_cookie", return_value=""), patch.object(service.threading, "Thread"):
            alice = service.start_login("alice")
            bob = service.start_login("bob")
            self.assertNotEqual(alice["session_id"], bob["session_id"])
            self.assertEqual(service.start_login("alice")["session_id"], alice["session_id"])
            self.assertEqual(service.login_status(alice["session_id"], "bob")["status"], "expired")
            self.assertEqual(service.login_status(alice["session_id"], "alice")["status"], "pending")

    def test_expired_shelf_cookie_clears_only_owner(self):
        with patch.object(repository, "load_cookie", return_value="cookie"), patch.object(
            repository, "list_official_accounts", return_value={"auth_expired": True}
        ), patch.object(repository, "save_cookie") as saved:
            self.assertTrue(service.list_shelf_articles(8, "alice")["login_required"])
            saved.assert_called_once_with("", "alice")

    def test_login_exception_finishes_session(self):
        service._login_sessions["session"] = {"id": "session", "owner_id": "alice", "status": "pending", "updated_at": time.time()}
        with patch.object(repository, "login", side_effect=RuntimeError("browser unavailable")):
            service._login("session", "alice")
        self.assertEqual(service.login_status("session", "alice")["status"], "failed")

    def test_login_uses_http_without_browser_by_default(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(repository, "_http_login", return_value=""), patch.object(repository, "_browser_login") as browser:
            self.assertEqual(repository.login(Mock(), "alice"), "")
            browser.assert_not_called()

    def test_browser_fallback_requires_explicit_setting(self):
        with patch.dict("os.environ", {"OFFICE_WEREAD_BROWSER_CHANNEL": "chrome"}, clear=True), patch.object(repository, "_http_login", return_value=""), patch.object(repository, "_browser_login", return_value="cookie") as browser:
            callback = Mock()
            self.assertEqual(repository.login(callback, "alice"), "cookie")
            browser.assert_called_once_with(callback, "alice", executable="", channel="chrome")

    def test_qr_supports_svg_and_browser_image(self):
        self.assertTrue(service._qr_data_url("https://weread.qq.com/web/confirm?uid=test").startswith("data:image/svg+xml;base64,"))
        self.assertEqual(service._qr_data_url("data:image/png;base64,abc"), "data:image/png;base64,abc")

    def test_article_cover_fallback_keeps_metadata(self):
        with patch.object(repository, "_get_json", side_effect=[
            {"payload": {}, "auth_expired": True},
            {"payload": {"reviewId": "MP_WXS_test_abc", "title": "Article", "time": 1700000000}},
        ]), patch.object(repository, "_get_html", return_value=""):
            result = repository.latest_articles("cookie", "MP_WXS_test")
        self.assertFalse(result["auth_expired"])
        self.assertEqual(result["items"][0]["title"], "Article")
        self.assertEqual(result["items"][0]["url"], "https://mp.weixin.qq.com/s/abc")


class PageReaderTests(unittest.TestCase):
    def read(self, html, url="https://example.com/article"):
        with patch.object(page_reader.web_document, "fetch_html", return_value=(html, url)):
            return page_reader.read_page(url)

    def test_sections_preserve_headings_without_script_noise(self):
        result = self.read("<title>Title</title><nav>Noise</nav><article><p>Intro</p><h2>Heading</h2><p>Body <b>text</b></p><script>secret</script></article>")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["title"], "Title")
        self.assertEqual(result["sections"], [
            {"heading": "", "level": 0, "text": "Intro"},
            {"heading": "Heading", "level": 2, "text": "Body text"},
        ])

    def test_sections_and_text_are_bounded(self):
        html = "<article>" + "".join("<h2>Heading</h2><p>" + "a" * 3000 + "</p>" for _ in range(50)) + "</article>"
        result = self.read(html)
        self.assertEqual(len(result["sections"]), 40)
        self.assertTrue(all(len(section["text"]) <= 2000 for section in result["sections"]))

    def test_fetch_validation_failure_is_structured(self):
        with patch.object(page_reader.web_document, "fetch_html", side_effect=ValueError("private address")):
            self.assertEqual(page_reader.read_page("http://127.0.0.1"), {"title": "", "sections": [], "error": "private address"})

    def test_wechat_missing_content_reports_error(self):
        result = self.read("<title>Article</title><body>Verify</body>", "https://mp.weixin.qq.com/s/test")
        self.assertTrue(result["error"])
        self.assertEqual(result["sections"], [])

    def test_wechat_hidden_article_remains_readable(self):
        result = self.read('<title>Article</title><div id="js_content" style="display:none"><p>' + "正文内容" * 20 + "</p></div>", "https://mp.weixin.qq.com/s/test")
        self.assertEqual(result["error"], "")
        self.assertIn("正文内容", result["sections"][0]["text"])


class AdapterTests(unittest.TestCase):
    def test_news_source_contracts(self):
        rows = [{"title": "News", "url": "https://example.com", "published_at": "", "summary": ""}]
        for source in preset_news_service.SOURCES:
            with self.subTest(source=source), patch.object(preset_news_service, "_" + source, return_value=rows):
                result = preset_news_service.fetch_preset_news(source, "00700.HK", "Tencent")
                self.assertEqual(result, {"source": source, "results": rows, "error": ""})

    def test_security_markets_and_exact_code_order(self):
        response = Mock()
        response.json.return_value = {"QuotationCodeTable": {"Data": [
            {"Code": "9988", "Name": "Alibaba", "JYS": "HK"},
            {"Code": "700", "Name": "Tencent", "JYS": "HK"},
            {"Code": "SPY", "Name": "SPDR ETF", "JYS": "NYSEARCA"},
            {"Code": "600519", "Name": "Moutai", "JYS": "SH"},
        ]}}
        with patch.object(security_search_service.httpx, "get", return_value=response):
            rows = security_search_service.search_securities("700.HK")
            self.assertEqual(rows[0]["ticker_code"], "00700.HK")
            self.assertEqual(len(rows), 2)
            self.assertEqual(security_search_service.search_securities("SPY.US")[0]["security_type"], "基金")

    def test_rss_parser_is_available_and_deduplicates(self):
        response = Mock(is_error=False, content=b'<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title><item><title>Story</title><link>https://example.com/a</link><description>Summary</description></item></channel></rss>')
        with patch.object(feed_service.httpx, "Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            result = feed_service.collect_sources([{"url": "https://example.com/rss"}] * 2)
        self.assertEqual(result["error"], "")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["title"], "Story")


if __name__ == "__main__":
    unittest.main()
