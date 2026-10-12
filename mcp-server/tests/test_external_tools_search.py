"""external_tools.search_engine / usage 单元测试。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "external-tools" / "repository"))
sys.path.insert(0, str(HERE / "external-tools" / "service"))

import search_repository  # noqa: E402
import search_service  # noqa: E402
import usage  # noqa: E402


def _ok_gate(engine="baidu", used=1, limit=100):
    return {
        "ok": True,
        "engine": engine,
        "enabled": True,
        "switch_on": True,
        "auto_closed": False,
        "used_today": used,
        "daily_limit": limit,
        "day": "2026-09-18",
    }


def _status_ok(engine="baidu", used=0, limit=100):
    return {
        "engine": engine,
        "enabled": True,
        "switch_on": True,
        "auto_closed": False,
        "used_today": used,
        "daily_limit": limit,
        "day": "2026-09-18",
        "store_error": None,
        "closed_reason": None,
    }


class SearchEngineTests(unittest.TestCase):
    def test_baidu_missing_key(self):
        with patch.dict(os.environ, {"BAIDU_QIANFAN_API_KEY": ""}, clear=False), patch(
            "search_service.usage.status", return_value=_status_ok()
        ):
            out = search_service.baidu_web_search("宁德时代 毛利率")
        self.assertIn("error", out)
        self.assertEqual(out["results"], [])
        self.assertEqual(out["engine"], "baidu")

    def test_google_missing_key(self):
        with patch.dict(
            os.environ,
            {"GOOGLE_CSE_API_KEY": "", "GOOGLE_CSE_CX": ""},
            clear=False,
        ), patch("search_service.usage.status", return_value=_status_ok("google")):
            out = search_service.google_web_search("Tesla margin")
        self.assertIn("error", out)
        self.assertEqual(out["results"], [])

    def test_topic_sites_from_env(self):
        env = {
            "SEARCH_SITES_FINANCE": "a.example.com, https://B.Example.com/path ,a.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            sites = search_repository.topic_sites("finance")
        self.assertEqual(sites, ["a.example.com", "b.example.com"])

    def test_all_sites_merges_topics(self):
        env = {
            "SEARCH_SITES": "",
            "SEARCH_SITES_FINANCE": "a.com",
            "SEARCH_SITES_ACCOUNTING": "b.com,a.com",
            "SEARCH_SITES_ENCYCLOPEDIA": "c.com",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(search_repository.all_sites(), ["a.com", "b.com", "c.com"])

    def test_baidu_parses_references_and_topic_sites(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "references": [
                {
                    "type": "web",
                    "title": "市盈率",
                    "url": "https://baike.baidu.com/item/市盈率",
                    "snippet": "市盈率是…",
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.post.return_value = mock_resp

        env = {
            "BAIDU_QIANFAN_API_KEY": "test-key",
            "SEARCH_SITES_ENCYCLOPEDIA": "baike.baidu.com,wiki.mbalib.com",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "search_service.usage.status", return_value=_status_ok()
        ), patch(
            "search_service.usage.try_consume", return_value=_ok_gate()
        ), patch(
            "search_repository.httpx.Client", return_value=mock_client
        ):
            out = search_service.baidu_web_search("市盈率", num=3, topic="encyclopedia")

        self.assertNotIn("error", out)
        self.assertEqual(len(out["results"]), 1)
        body = mock_client.post.call_args.kwargs["json"]
        self.assertEqual(
            body["search_filter"]["match"]["site"],
            ["baike.baidu.com", "wiki.mbalib.com"],
        )
        self.assertEqual(out["quota"]["used_today"], 1)

    def test_baidu_baike_uses_lemma_api(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "code": "0",
            "result": {
                "lemma_title": "市盈率",
                "lemma_desc": "财务指标",
                "url": "https://baike.baidu.com/item/市盈率",
                "summary": "市盈率是每股市价与每股收益的比率。",
            },
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch.dict(os.environ, {"BAIDU_QIANFAN_API_KEY": "test-key"}, clear=False), patch(
            "search_service.usage.status", return_value=_status_ok("baike")
        ), patch(
            "search_service.usage.try_consume",
            return_value=_ok_gate("baike"),
        ), patch(
            "search_repository.httpx.Client", return_value=mock_client
        ):
            out = search_service.baidu_baike_search("市盈率", num=3)

        self.assertNotIn("error", out)
        self.assertEqual(out["engine"], "baike")
        self.assertEqual(out["results"][0]["link"], "https://baike.baidu.com/item/市盈率")
        self.assertIn("每股市价", out["results"][0]["snippet"])
        called = mock_client.get.call_args
        self.assertEqual(called.args[0], search_repository.BAIKE_CONTENT_URL)
        self.assertEqual(called.kwargs["params"]["search_type"], "lemmaTitle")
        self.assertEqual(called.kwargs["params"]["search_key"], "市盈率")
        mock_client.post.assert_not_called()

    def test_google_topic_uses_single_cx_and_site_or(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {
                    "title": "P/E",
                    "link": "https://finance.eastmoney.com/a/1.html",
                    "snippet": "…",
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_resp

        env = {
            "GOOGLE_CSE_API_KEY": "gk",
            "GOOGLE_CSE_CX": "one-cx",
            "SEARCH_SITES_FINANCE": "finance.eastmoney.com,www.cninfo.com.cn",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "search_service.usage.status", return_value=_status_ok("google")
        ), patch(
            "search_service.usage.try_consume",
            return_value=_ok_gate("google"),
        ), patch(
            "search_repository.httpx.Client", return_value=mock_client
        ):
            out = search_service.google_web_search("毛利率", num=5, topic="finance")

        params = mock_client.get.call_args.kwargs["params"]
        self.assertEqual(params["cx"], "one-cx")
        self.assertEqual(out["cx"], "one-cx")
        self.assertIn("site:finance.eastmoney.com", params["q"])

    def test_google_empty_topic_no_site_clause(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_resp

        env = {"GOOGLE_CSE_API_KEY": "gk", "GOOGLE_CSE_CX": "one-cx"}
        with patch.dict(os.environ, env, clear=False), patch(
            "search_service.usage.status", return_value=_status_ok("google")
        ), patch(
            "search_service.usage.try_consume",
            return_value=_ok_gate("google"),
        ), patch(
            "search_repository.httpx.Client", return_value=mock_client
        ):
            search_service.google_web_search("P/E", num=5, topic="")

        params = mock_client.get.call_args.kwargs["params"]
        self.assertEqual(params["q"], "P/E")
        self.assertNotIn("site:", params["q"])

    def test_invalid_topic(self):
        with patch.dict(os.environ, {"BAIDU_QIANFAN_API_KEY": "k"}, clear=False):
            out = search_service.baidu_web_search("x", topic="sports")
        self.assertIn("error", out)

    def test_switch_off_refuses(self):
        st = _status_ok()
        st["enabled"] = False
        st["switch_on"] = False
        st["closed_reason"] = "switch_off"
        with patch.dict(os.environ, {"BAIDU_QIANFAN_API_KEY": "k"}, clear=False), patch(
            "search_service.usage.status", return_value=st
        ):
            out = search_service.baidu_web_search("hello")
        self.assertIn("关闭", out["error"])
        self.assertEqual(out["results"], [])


class UsageLogicTests(unittest.TestCase):
    def test_engine_enabled_env(self):
        with patch.dict(os.environ, {"SEARCH_BAIDU_ENABLED": "0"}, clear=False):
            self.assertFalse(usage.engine_enabled("baidu"))
        with patch.dict(os.environ, {"SEARCH_BAIDU_ENABLED": "1"}, clear=False):
            self.assertTrue(usage.engine_enabled("baidu"))
        with patch.dict(os.environ, {"SEARCH_BAIKE_ENABLED": "0"}, clear=False):
            self.assertFalse(usage.engine_enabled("baike"))

    def test_daily_limit_env(self):
        with patch.dict(os.environ, {"SEARCH_GOOGLE_DAILY_LIMIT": "3"}, clear=False):
            self.assertEqual(usage.daily_limit("google"), 3)
        with patch.dict(os.environ, {"SEARCH_BAIKE_DAILY_LIMIT": "7"}, clear=False):
            self.assertEqual(usage.daily_limit("baike"), 7)


if __name__ == "__main__":
    unittest.main()
