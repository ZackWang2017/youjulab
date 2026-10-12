"""单页抓取：repository 只取一个 URL，service 只输出结构化正文。"""
from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "local-tools" / "service"))
sys.path.insert(0, str(HERE / "local-tools" / "endpoints"))

import website_crawler_endpoint  # noqa: E402
import website_crawler_repository as pages  # noqa: E402
import website_crawler_service as reader  # noqa: E402

_PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
_PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))]
_FAKE_IP = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.43", 443))]

_HTML = """
<html><head>
<title>腾讯游戏</title>
<meta name="description" content="游戏业务介绍">
<script>secretToken = 1</script>
</head><body>
<nav><a href="https://example.com/nav">导航</a>菜单</nav>
<h1>游戏业务</h1>
<p>王者荣耀是主要产品。</p>
<p><a href="/news">新闻</a> <a href="https://game.qq.com/a">详情</a></p>
</body></html>
"""


class PageReaderTests(unittest.TestCase):
    def test_tool_lives_on_local_mcp(self) -> None:
        from mcp.server.fastmcp import FastMCP

        local = FastMCP("invp-local-tools", streamable_http_path="/mcp/local-tools")
        website_crawler_endpoint.register_tools(local)
        names = {tool.name for tool in local._tool_manager.list_tools()}
        self.assertEqual(names, {"read_web_page"})
        self.assertEqual(local.settings.streamable_http_path, "/mcp/local-tools")

    def test_structured_json_drops_html_and_noise(self) -> None:
        with patch.object(pages, "fetch_page", return_value={
            "url": "https://game.qq.com/about",
            "status": 200,
            "content_type": "text/html",
            "truncated": False,
            "body": _HTML,
        }):
            out = reader.read_web_page("https://game.qq.com/about")
        self.assertNotIn("error", out)
        self.assertEqual(out["title"], "腾讯游戏")
        self.assertEqual(out["description"], "游戏业务介绍")
        self.assertEqual(out["sections"], [
            {"heading": "游戏业务", "level": 1, "text": "王者荣耀是主要产品。 新闻 详情"},
        ])
        self.assertNotIn("<", str(out["sections"]))
        self.assertNotIn("secretToken", str(out))
        self.assertNotIn("菜单", str(out))
        self.assertEqual(out["links"], [
            {"text": "新闻", "href": "https://game.qq.com/news"},
            {"text": "详情", "href": "https://game.qq.com/a"},
        ])

    def test_allows_local_proxy_fake_ip(self) -> None:
        with patch.object(pages.socket, "getaddrinfo", return_value=_FAKE_IP):
            self.assertEqual(
                pages._public_http_url("https://weixin.sogou.com/weixin"),
                "https://weixin.sogou.com/weixin",
            )

    def test_rejects_private_and_non_http(self) -> None:
        with patch.object(pages.socket, "getaddrinfo", return_value=_PRIVATE):
            with self.assertRaises(ValueError):
                pages.fetch_page("http://intranet.example/")
        out = reader.read_web_page("file:///etc/passwd")
        self.assertIn("error", out)
        self.assertEqual(out["sections"], [])

    @patch("website_crawler_repository.httpx.Client")
    def test_fetch_stops_redirect_to_private_host(self, client_cls: MagicMock) -> None:
        redirected = MagicMock(status_code=302, headers={"location": "http://127.0.0.1/secret"})
        redirected.url = "https://example.com/start"
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = redirected
        client_cls.return_value = client

        def resolve(host, port, type):  # noqa: A002
            del port, type
            if host == "example.com":
                return _PUBLIC
            return _PRIVATE

        with patch.object(pages.socket, "getaddrinfo", side_effect=resolve):
            with self.assertRaises(ValueError):
                pages.fetch_page("https://example.com/start")
        client.get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
