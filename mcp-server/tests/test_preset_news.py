"""预定义资讯查询：解析各来源的返回，不访问外网。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "local-tools" / "service"))
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))
sys.path.insert(0, str(HERE / "financial-data-service" / "service"))

import preset_news_repository as repo  # noqa: E402
import preset_news_service as news  # noqa: E402
import security_search_service as stocks  # noqa: E402
from security_search_repository import candidate_codes  # noqa: E402


class PresetNewsTests(unittest.TestCase):
    def test_hkex_retries_when_the_connection_closes_early(self):
        calls = {"n": 0}

        def fake_get(*args, **kwargs):
            del args, kwargs
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
            request = httpx.Request("GET", "https://www1.hkexnews.hk/search/prefix.do")
            return httpx.Response(200, text='callback({"stockInfo":[{"code":"00700"}]});', request=request)

        with patch("preset_news_repository.httpx.get", fake_get), patch("preset_news_repository.time.sleep"):
            found = repo.hkex_lookup("00700")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(found["stockInfo"][0]["code"], "00700")

    def test_hkex_uses_stock_id_and_keeps_publish_time(self):
        with patch("preset_news_service.repository.hkex_lookup", return_value={"stockInfo": [{"code": "00700", "stockId": 7609}]}), \
             patch("preset_news_service.repository.hkex_titles", return_value={"result": "[{\"TITLE\":\"翌日披露報表\",\"DATE_TIME\":\"25/09/2026 17:22\",\"FILE_LINK\":\"/listedco/a.pdf\",\"LONG_TEXT\":\"翌日披露\"}]"}):
            out = news.fetch_preset_news("hkex", "00700.HK", "腾讯控股")
        self.assertEqual(out["error"], "")
        self.assertEqual(out["results"][0]["published_at"], "2026-09-25 17:22")
        self.assertEqual(out["results"][0]["url"], "https://www1.hkexnews.hk/listedco/a.pdf")

    def test_sina_reads_dated_links(self):
        html = "&nbsp;2026-09-26&nbsp;16:46&nbsp;<a target='_blank' href='https://finance.sina.com.cn/a.shtml'>腾讯回购</a>"
        with patch("preset_news_service.repository.sina_news_html", return_value=html):
            out = news.fetch_preset_news("sina", "00700.HK", "腾讯控股")
        self.assertEqual(out["results"][0]["title"], "腾讯回购")
        self.assertEqual(out["results"][0]["published_at"], "2026-09-26 16:46")

    def test_xueqiu_maps_market_symbol_and_keeps_publish_time(self):
        payload = {
            "list": [{
                "title": "腾讯回购",
                "description": "继续回购。",
                "target": "/S/00700/410594927",
                "created_at": 1758872760000,
            }],
        }
        with patch("preset_news_service.repository.xueqiu_news", return_value=payload) as mocked, \
             patch("preset_news_service.repository.xueqiu_talks", return_value={"list": []}):
            hk = news.fetch_preset_news("xueqiu", "00700.HK", "腾讯控股")
            sh = news.fetch_preset_news("xueqiu", "600519.SH", "贵州茅台")
            sz = news.fetch_preset_news("xueqiu", "000001.SZ", "平安银行")
            bj = news.fetch_preset_news("xueqiu", "920799.BJ", "艾融软件")
        self.assertEqual([call.args[0] for call in mocked.call_args_list], ["00700", "SH600519", "SZ000001", "BJ920799"])
        self.assertEqual(hk["error"], "")
        self.assertEqual(hk["results"][0]["title"], "腾讯回购")
        self.assertEqual(hk["results"][0]["url"], "https://xueqiu.com/S/00700/410594927")
        self.assertEqual(hk["results"][0]["published_at"], "2025-09-26 15:46")

    def test_xueqiu_appends_talks_and_skips_replies(self):
        news_payload = {"list": [{
            "title": "腾讯回购",
            "description": "继续回购。",
            "target": "/S/00700/1",
            "created_at": 1758872760000,
        }]}
        talks = {"list": [
            {"title": "仓位需要多确定", "description": "组合里腾讯占一半。", "target": "/1/2", "created_at": 1758872760000},
            {"title": "", "description": "回复@某人: 腾讯老登股", "target": "/1/3", "created_at": 1758872760000},
            {"title": "", "description": "腾讯的基本盘是社交。", "target": "/1/4", "created_at": 1758872760000},
        ]}
        with patch("preset_news_service.repository.xueqiu_news", return_value=news_payload), \
             patch("preset_news_service.repository.xueqiu_talks", return_value=talks):
            out = news.fetch_preset_news("xueqiu", "00700.HK", "腾讯控股")
        self.assertEqual([row["title"] for row in out["results"]], ["腾讯回购", "仓位需要多确定", "腾讯的基本盘是社交。"])
        self.assertEqual(out["results"][1]["url"], "https://xueqiu.com/1/2")

    def test_tencent_maps_symbol_and_keeps_publish_time(self):
        payload = {"code": 0, "data": {"data": [{
            "title": "花旗维持买入",
            "url": "https://gu.qq.com/resources/shy/news/detail-v2/index.html#/?id=1",
            "time": "2026-09-26 18:29:29",
            "summary": "",
        }]}}
        with patch("preset_news_service.repository.tencent_news", return_value=payload) as mocked:
            hk = news.fetch_preset_news("tencent", "00700.HK", "腾讯控股")
            sh = news.fetch_preset_news("tencent", "600519.SH", "贵州茅台")
        self.assertEqual([call.args[0] for call in mocked.call_args_list], ["hk00700", "sh600519"])
        self.assertEqual(hk["results"][0]["title"], "花旗维持买入")
        self.assertEqual(hk["results"][0]["published_at"], "2026-09-26 18:29")

    def test_jisilu_reads_topic_by_name_and_skips_unrelated_code(self):
        page = """
        <h4> <a target="_blank" href="https://www.jisilu.cn/question/525506">阿里800亿增发后，腾讯会不会配股融资？</a>
        <span>回复 • 2026-09-23 13:51 • 100 次浏览</span>
        """
        with patch("preset_news_service.repository.jisilu_topic", return_value=(
            "https://www.jisilu.cn/topic/%E8%85%BE%E8%AE%AF%E6%8E%A7%E8%82%A1", page,
        )) as mocked:
            out = news.fetch_preset_news("jisilu", "00700.HK", "腾讯控股")
        self.assertEqual(mocked.call_args.args[0], "腾讯控股")
        self.assertEqual(out["results"][0]["title"], "阿里800亿增发后，腾讯会不会配股融资？")
        self.assertEqual(out["results"][0]["published_at"], "2026-09-23 13:51")
        self.assertEqual(out["results"][0]["url"], "https://www.jisilu.cn/question/525506")

        def topic(keyword):
            return "https://www.jisilu.cn/topic/122216", "<title>122216 - 集思录</title><h4><a href=\"https://www.jisilu.cn/question/48275\">问题反馈</a> 2020-01-01 00:00"

        with patch("preset_news_service.repository.jisilu_topic", side_effect=topic):
            missed = news.fetch_preset_news("jisilu", "00700.HK", "")
        self.assertEqual(missed["results"], [])

    def test_code_lookup_pads_hk_and_a_share(self):
        self.assertEqual(candidate_codes("700"), ["700", "00700", "000700"])
        self.assertEqual(candidate_codes("腾讯"), [])

    def test_chinese_name_matches_hk_stock_stored_in_english(self):
        with patch("security_search_service.repository.search_securities", return_value=[]), \
             patch("security_search_service.repository.fetch_name_hints", return_value=[
                 {"code": "00700", "name": "腾讯控股", "market": "HK"},
             ]), \
             patch("security_search_service.repository.fetch_by_tickers", return_value=[
                 {"ticker_code": "00700.HK", "name": "TENCENT"},
             ]):
            rows = stocks.search_securities("腾讯")
        self.assertEqual(rows, [{"ticker_code": "00700.HK", "name": "腾讯控股"}])


if __name__ == "__main__":
    unittest.main()
