"""资讯组、拉取去重、关键字过滤与官方公告解析。不访问数据库和外网。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "information-service" / "repository"))
sys.path.insert(0, str(HERE / "information-service" / "service"))
sys.path.insert(0, str(HERE / "local-tools" / "service"))
sys.path.insert(0, str(HERE / "local-tools" / "repository"))
sys.path.insert(0, str(HERE / "portfolio-service" / "repository"))

import adapter_catalog as catalog  # noqa: E402
import announcement_repository as announce  # noqa: E402
import fetch_service as fetch  # noqa: E402
import information_service as service  # noqa: E402
import rss_repository as rss_repo  # noqa: E402
from information_service import InformationError  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_lists_implemented_adapters(self):
        apis = {item["id"] for item in catalog.list_adapters("finance_api")}
        rss = {item["id"] for item in catalog.list_adapters("finance_rss")}
        announce_ids = {item["id"] for item in catalog.list_adapters("holdings_announce")}
        self.assertIn("sina", apis)
        self.assertNotIn("hkex", apis)
        self.assertIn("cls", rss)
        self.assertEqual(announce_ids, {"cninfo", "sse", "szse", "hkex", "sec"})

    def test_default_announce_adapter_follows_market(self):
        self.assertEqual(catalog.default_announce_adapter("SH"), "cninfo")
        self.assertEqual(catalog.default_announce_adapter("HK"), "hkex")
        self.assertEqual(catalog.default_announce_adapter("US"), "sec")
        self.assertEqual([item["id"] for item in catalog.adapters_for_market("SZ")], ["cninfo", "szse"])


class KeywordTests(unittest.TestCase):
    def test_split_and_match_keywords(self):
        keys = fetch.split_keywords("回购，分红 中报")
        self.assertEqual(keys, ["回购", "分红", "中报"])
        self.assertTrue(fetch.match_keywords("公司宣布中期分红", "", keys))
        self.assertFalse(fetch.match_keywords("日常经营公告", "", keys))
        self.assertTrue(fetch.match_keywords("任意标题", "", []))

    def test_guess_impact_from_title(self):
        self.assertIn("利好", fetch.guess_impact("控股股东增持并启动回购"))
        self.assertIn("利空", fetch.guess_impact("高管减持且计提商誉减值"))
        self.assertIn("官方披露", fetch.guess_impact("董事会决议", source_name="巨潮资讯"))

    def test_is_today_uses_date_prefix(self):
        self.assertTrue(fetch.is_today("2026-10-23 09:30", date(2026, 10, 23)))
        self.assertFalse(fetch.is_today("2026-10-22 18:00", date(2026, 10, 23)))
        self.assertFalse(fetch.is_today("", date(2026, 10, 23)))

    def test_finance_api_resolves_company_name_to_ticker(self):
        with patch.object(fetch, "lookup_securities", return_value=[{"ticker_code": "00700.HK", "name": "腾讯控股"}]):
            self.assertEqual(fetch.resolve_security("腾讯"), ("00700.HK", "腾讯控股"))
            self.assertEqual(fetch.finance_api_queries(["腾讯", "回购"]), [("00700.HK", "腾讯控股")])
        self.assertEqual(fetch.resolve_security("00700.HK"), ("00700.HK", ""))

    def test_finance_api_fetches_resolved_ticker(self):
        row = {"title": "腾讯回购", "url": "https://a", "published_at": "2026-10-23 09:00", "summary": ""}
        with patch.object(fetch, "lookup_securities", return_value=[{"ticker_code": "00700.HK", "name": "腾讯控股"}]), \
             patch.object(fetch.preset, "fetch_preset_news", return_value={"results": [row], "error": ""}) as preset:
            out = fetch.collect_group("finance_api", {"adapters": ["sina"], "keywords": "腾讯"})
        preset.assert_called_once_with("sina", "00700.HK", "腾讯控股")
        self.assertEqual(out["items"][0]["title"], "腾讯回购")
        self.assertEqual(out["failed"], [])

    def test_finance_api_resolves_bare_code(self):
        with patch.object(fetch, "lookup_securities", return_value=[{"ticker_code": "600000.SH", "name": "浦发银行"}]):
            self.assertEqual(fetch.resolve_security("600000"), ("600000.SH", "浦发银行"))
            self.assertEqual(fetch.finance_api_queries(["600000", "回购"]), [("600000.SH", "浦发银行")])

    def test_finance_api_skips_ticker_adapters_once_for_topics(self):
        row = {"title": "回购进展", "url": "https://a", "published_at": "2026-10-23 09:00", "summary": ""}
        with patch.object(fetch, "lookup_securities", return_value=[]), \
             patch.object(fetch.preset, "fetch_preset_news", return_value={"results": [row], "error": ""}) as preset:
            out = fetch.collect_group("finance_api", {
                "adapters": ["sina", "xueqiu", "tencent", "eastmoney", "jisilu"],
                "keywords": "回购 分红 中报",
            })
        called = [call.args[0] for call in preset.call_args_list]
        self.assertEqual(called.count("sina"), 0)
        self.assertEqual(called.count("xueqiu"), 0)
        self.assertEqual(called.count("tencent"), 0)
        self.assertEqual(called.count("eastmoney"), 3)
        self.assertEqual(called.count("jisilu"), 3)
        self.assertEqual(len(out["failed"]), 3)
        self.assertTrue(all("需要带市场的股票代码" in item for item in out["failed"]))
        self.assertEqual(out["items"][0]["title"], "回购进展")

    def test_finance_api_skips_sina_for_us_ticker(self):
        row = {"title": "苹果新品", "url": "https://a", "published_at": "2026-10-23 09:00", "summary": ""}
        with patch.object(fetch, "lookup_securities", return_value=[]), \
             patch.object(fetch.preset, "fetch_preset_news", return_value={"results": [row], "error": ""}) as preset:
            out = fetch.collect_group("finance_api", {
                "adapters": ["sina", "xueqiu", "tencent"],
                "keywords": "AAPL.US",
            })
        self.assertEqual([call.args for call in preset.call_args_list], [
            ("xueqiu", "AAPL.US", ""),
            ("tencent", "AAPL.US", ""),
        ])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("新浪财经", out["failed"][0])

    def test_finance_rss_uses_native_adapter_and_does_not_need_rsshub(self):
        row = {"title": "电报", "url": "https://www.cls.cn/detail/1", "published_at": "2026-10-23 09:00", "summary": "回购"}
        with patch.object(fetch.rss_repo, "fetch_adapter", return_value=[row]) as pulled:
            out = fetch.collect_group("finance_rss", {"adapters": ["cls"], "keywords": "回购"})
        pulled.assert_called_once()
        self.assertEqual(pulled.call_args.args[0]["id"], "cls")
        self.assertEqual(out["items"][0]["title"], "电报")
        self.assertEqual(out["failed"], [])

    def test_finance_rss_keeps_latest_when_keywords_miss(self):
        rows = [
            {"title": "日常经营公告", "url": "https://a", "published_at": "", "summary": ""},
            {"title": "午间点评", "url": "https://b", "published_at": "", "summary": ""},
        ]
        with patch.object(fetch.rss_repo, "fetch_adapter", return_value=rows):
            out = fetch.collect_group("finance_rss", {"adapters": ["cls"], "keywords": "腾讯"})
        self.assertEqual([item["title"] for item in out["items"]], ["日常经营公告", "午间点评"])
        self.assertEqual(out["failed"], [])

    def test_finance_rss_prefers_keyword_hits(self):
        rows = [
            {"title": "日常经营公告", "url": "https://a", "published_at": "", "summary": ""},
            {"title": "腾讯回购", "url": "https://b", "published_at": "", "summary": ""},
        ]
        with patch.object(fetch.rss_repo, "fetch_adapter", return_value=rows):
            out = fetch.collect_group("finance_rss", {"adapters": ["cls"], "keywords": "腾讯"})
        self.assertEqual([item["title"] for item in out["items"]], ["腾讯回购"])

    def test_finance_rss_records_adapter_error(self):
        with patch.object(fetch.rss_repo, "fetch_adapter", side_effect=ValueError("站点拒绝")):
            out = fetch.collect_group("finance_rss", {"adapters": ["36kr"], "keywords": ""})
        self.assertEqual(out["items"], [])
        self.assertEqual(out["failed"], ["36氪：站点拒绝"])

    def test_holdings_announce_queries_by_ticker_and_ignores_keywords(self):
        row = {"title": "日常经营公告", "url": "https://a", "published_at": "2026-10-23 09:00", "summary": ""}
        with patch.object(fetch, "_announce_rows", return_value=[row]) as pulled:
            out = fetch.collect_group("holdings_announce", {
                "holdings": [{"ticker_code": "600000.SH", "name": "浦发银行", "market_code": "SH", "adapter": "cninfo"}],
                "keywords": "回购",
            })
        pulled.assert_called_once_with("cninfo", "600000.SH", "浦发银行")
        self.assertEqual(out["items"][0]["title"], "日常经营公告")
        self.assertEqual(out["failed"], [])


class GroupServiceTests(unittest.TestCase):
    def test_create_group_requires_adapters(self):
        with patch.object(service, "ensure_favorites", return_value={"id": 1}), \
             patch.object(service.repo, "list_groups", return_value=[{"id": 1, "name": "收藏夹", "kind": "favorites"}]):
            with self.assertRaises(InformationError):
                service.create_group("u1", {"name": "财经", "kind": "finance_api", "adapters": []})

    def test_favorites_cannot_be_deleted(self):
        with patch.object(service.repo, "get_group", return_value={"id": 1, "kind": "favorites", "name": "收藏夹"}):
            with self.assertRaises(InformationError):
                service.delete_group("u1", 1)

    def test_fetch_skips_duplicate_titles(self):
        group = {"id": 2, "kind": "finance_api", "name": "财经", "config": "{}"}
        collected = {
            "items": [
                {"title": "同一标题", "url": "https://a", "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "published_at": "", "summary": "", "impact": "x"},
                {"title": "同一标题", "url": "https://b", "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "published_at": "", "summary": "", "impact": "x"},
                {"title": "新标题", "url": "https://c", "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "published_at": "2026-10-23 09:00", "summary": "", "impact": "x"},
                {"title": "过期公告", "url": "https://d", "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "published_at": "2026-10-19", "summary": "", "impact": "x"},
                {"title": "无日期", "url": "https://e", "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "published_at": "", "summary": "", "impact": "x"},
            ],
            "failed": [],
            "login_required": False,
        }
        inserted_row = {"id": 9, "group_id": 2, "source_kind": "finance_api", "source_name": "新浪财经", "source_icon": "", "title": "新标题", "url": "https://c", "published_at": "", "fetched_at": None, "fetched_date": date(2026, 10, 23), "summary": "", "impact": "x", "favorited": False, "favorite_from_kind": "", "favorite_from_name": "", "analysis": "{}"}
        with patch.object(service.repo, "get_group", return_value=group), \
             patch.object(service.repo, "public_group", return_value={"id": 2, "kind": "finance_api", "name": "财经", "config": {"adapters": ["sina"], "keywords": ""}}), \
             patch.object(fetch, "collect_group", return_value=collected), \
             patch.object(service.repo, "known_titles", return_value={"同一标题"}), \
             patch.object(service.repo, "insert_item", return_value=inserted_row) as insert, \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            out = service.fetch_today("u1", 2)
        self.assertEqual(out["skipped"], 4)
        self.assertEqual(len(out["inserted"]), 1)
        self.assertEqual(out["inserted"][0]["title"], "新标题")
        insert.assert_called_once()

    def test_source_group_list_excludes_favorited(self):
        group = {"id": 2, "kind": "finance_api", "name": "财经"}
        with patch.object(service.repo, "get_group", return_value=group), \
             patch.object(service.repo, "public_group", return_value={"id": 2, "kind": "finance_api", "name": "财经", "config": {}}), \
             patch.object(service.repo, "list_items", return_value=[]) as listed, \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            service.list_items("u1", 2, today_only=True)
        self.assertEqual(listed.call_args.kwargs.get("group_id"), 2)
        self.assertIs(listed.call_args.kwargs.get("favorited"), False)
        self.assertIs(listed.call_args.kwargs.get("hidden"), False)

    def test_review_list_includes_hidden_and_favorited(self):
        group = {"id": 2, "kind": "finance_api", "name": "财经"}
        with patch.object(service.repo, "get_group", return_value=group), \
             patch.object(service.repo, "public_group", return_value={"id": 2, "kind": "finance_api", "name": "财经", "config": {}}), \
             patch.object(service.repo, "list_items", return_value=[]) as listed, \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            service.list_items("u1", 2, today_only=True, for_review=True)
        self.assertEqual(listed.call_args.kwargs.get("group_id"), 2)
        self.assertIsNone(listed.call_args.kwargs.get("favorited"))
        self.assertIsNone(listed.call_args.kwargs.get("hidden"))

    def test_clear_history_only_deletes_past_unfavorited(self):
        with patch.object(service.repo, "clear_past_unfavorited", return_value=3) as clear, \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            out = service.clear_history("u1")
        clear.assert_called_once_with("u1", date(2026, 10, 23))
        self.assertEqual(out["deleted"], 3)

    def test_favorites_list_only_favorited(self):
        group = {"id": 1, "kind": "favorites", "name": "收藏夹"}
        with patch.object(service.repo, "get_group", return_value=group), \
             patch.object(service.repo, "public_group", return_value={"id": 1, "kind": "favorites", "name": "收藏夹", "config": {}}), \
             patch.object(service.repo, "list_items", return_value=[]) as listed, \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            service.list_items("u1", 1, today_only=True)
        self.assertIs(listed.call_args.kwargs.get("favorited"), True)
        self.assertIsNone(listed.call_args.kwargs.get("group_id"))

    def test_wechat_fetch_asks_for_login(self):
        group = {"id": 3, "kind": "wechat", "name": "公众号", "config": "{}"}
        with patch.object(service.repo, "get_group", return_value=group), \
             patch.object(service.repo, "public_group", return_value={"id": 3, "kind": "wechat", "name": "公众号", "config": {}}), \
             patch.object(fetch, "collect_group", return_value={"items": [], "failed": ["微信读书尚未登录。"], "login_required": True}):
            out = service.fetch_today("u1", 3)
        self.assertTrue(out["login_required"])
        self.assertEqual(out["inserted"], [])


class WechatCollectTests(unittest.TestCase):
    def test_keeps_today_articles_or_latest_one(self):
        payload = {
            "accounts": [
                {
                    "title": "公司观察",
                    "results": [
                        {"title": "昨日旧闻", "url": "https://a", "published_at": "2026-10-22 09:00", "summary": ""},
                        {"title": "今日快讯", "url": "https://b", "published_at": "2026-10-23 08:00", "summary": "回购"},
                    ],
                },
                {
                    "title": "没有今日",
                    "results": [
                        {"title": "最新一条", "url": "https://c", "published_at": "2026-10-21 12:00", "summary": ""},
                    ],
                },
            ],
        }
        with patch.object(fetch.weread, "list_shelf_articles", return_value=payload), \
             patch.object(fetch, "today", return_value=date(2026, 10, 23)):
            out = fetch.collect_group("wechat", {})
        titles = [item["title"] for item in out["items"]]
        self.assertEqual(titles, ["今日快讯", "最新一条"])
        self.assertFalse(out["login_required"])


class RssAdapterTests(unittest.TestCase):
    def test_cls_maps_telegraph_cache(self):
        request = httpx.Request("GET", "https://www.cls.cn/api/cache")
        response = httpx.Response(
            200,
            json={"data": {"roll_data": [{"id": 9, "title": "回购完成", "brief": "公司回购", "ctime": 1761177600}]}},
            request=request,
        )
        with patch.object(rss_repo.httpx, "get", return_value=response):
            items = rss_repo.fetch_cls()
        self.assertEqual(items[0]["title"], "回购完成")
        self.assertEqual(items[0]["url"], "https://www.cls.cn/detail/9")

    def test_36kr_reads_initial_state(self):
        html = (
            '<script>window.initialState = {"newsflashCatalogData":{"data":{"newsflashList":{"data":['
            '{"itemId":1,"templateMaterial":{"itemId":1,"widgetTitle":"融资完成","widgetContent":"摘要",'
            '"publishTime":1761177600000}}]}}}};</script>'
        )
        request = httpx.Request("GET", "https://www.36kr.com/newsflashes")
        response = httpx.Response(200, text=html, request=request)
        with patch.object(rss_repo.httpx, "get", return_value=response):
            items = rss_repo.fetch_36kr()
        self.assertEqual(items[0]["title"], "融资完成")
        self.assertEqual(items[0]["url"], "https://www.36kr.com/newsflashes/1")

    def test_36kr_html_reads_relative_time(self):
        html = (
            '<a class="item-title" href="/newsflashes/9">快讯标题</a>'
            '<div class="item-other"><span class="time">8分钟前</span></div>'
        )
        request = httpx.Request("GET", "https://www.36kr.com/newsflashes")
        response = httpx.Response(200, text=html, request=request)
        now = datetime(2026, 10, 23, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.object(rss_repo.httpx, "get", return_value=response), \
             patch.object(rss_repo, "_now", return_value=now):
            items = rss_repo.fetch_36kr()
        self.assertEqual(items[0]["title"], "快讯标题")
        self.assertEqual(items[0]["published_at"], "2026-10-23 12:00")

    def test_stcn_reads_list_titles(self):
        html = (
            '<div class="tt"><a href="/article/detail/88.html">要闻标题</a></div>'
            '<div class="text ellipsis-2"><a href="/article/detail/88.html">摘要一段</a></div>'
            '<div class="info "><span>证券时报</span><span>11:13</span></div>'
        )
        request = httpx.Request("GET", "https://www.stcn.com/article/list/yw.html")
        response = httpx.Response(200, text=html, request=request)
        now = datetime(2026, 10, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.object(rss_repo.httpx, "get", return_value=response), \
             patch.object(rss_repo, "_now", return_value=now):
            items = rss_repo.fetch_stcn()
        self.assertEqual(items[0]["title"], "要闻标题")
        self.assertEqual(items[0]["url"], "https://www.stcn.com/article/detail/88.html")
        self.assertEqual(items[0]["published_at"], "2026-10-23 11:13")


class AnnounceParseTests(unittest.TestCase):
    def setUp(self):
        announce.reset_caches()

    def test_cninfo_maps_title_and_time(self):
        org_req = httpx.Request("POST", "https://www.cninfo.com.cn/new/information/topSearch/query")
        query_req = httpx.Request("POST", "https://www.cninfo.com.cn/new/hisAnnouncement/query")
        org_resp = httpx.Response(
            200,
            json=[{"code": "600000", "orgId": "gssh0600000", "zwjc": "浦发银行"}],
            request=org_req,
        )
        query_resp = httpx.Response(
            200,
            json={"announcements": [{"announcementTitle": "2025年年报", "adjunctUrl": "finalpage/a.PDF", "announcementTime": 1761177600000}]},
            request=query_req,
        )
        with patch.object(announce.httpx, "post", side_effect=[org_resp, query_resp]) as post:
            items = announce.fetch_cninfo("600000.SH", "浦发银行")
        self.assertEqual(post.call_args.kwargs["data"]["stock"], "600000,gssh0600000")
        self.assertEqual(items[0]["title"], "2025年年报")
        self.assertEqual(items[0]["url"], "https://static.cninfo.com.cn/finalpage/a.PDF")
        self.assertTrue(items[0]["published_at"].startswith("2025-10-23") or items[0]["published_at"].startswith("2026-"))

    def test_sse_queries_security_code(self):
        request = httpx.Request("GET", "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do")
        response = httpx.Response(
            200,
            json={"pageHelp": {"data": [[{"TITLE": "浦发银行公告", "URL": "/disclosure/x.pdf", "SSEDATE": "2026-10-21"}]]}},
            request=request,
        )
        with patch.object(announce.httpx, "get", return_value=response) as get:
            items = announce.fetch_sse("600000.SH", "浦发银行")
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["SECURITY_CODE"], "600000")
        self.assertNotIn("STOCKCODE", params)
        self.assertEqual(items[0]["url"], "https://static.sse.com.cn/disclosure/x.pdf")

    def test_sec_resolves_cik_and_maps_filing(self):
        ticker_req = httpx.Request("GET", "https://www.sec.gov/files/company_tickers.json")
        efts_req = httpx.Request("GET", "https://efts.sec.gov/LATEST/search-index")
        ticker_resp = httpx.Response(
            200,
            json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
            request=ticker_req,
        )
        efts_resp = httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [{
                        "_id": "0000320193-26-000001:aapl-8k.htm",
                        "_source": {
                            "ciks": ["0000320193"],
                            "form": "8-K",
                            "file_date": "2026-10-01",
                            "file_description": "Current report",
                            "adsh": "0000320193-26-000001",
                        },
                    }],
                },
            },
            request=efts_req,
        )
        with patch.object(announce.httpx, "get", side_effect=[ticker_resp, efts_resp]) as get:
            items = announce.fetch_sec("AAPL.US", "苹果")
        self.assertEqual(get.call_args.kwargs["params"]["ciks"], "0000320193")
        self.assertIn("8-K", items[0]["title"])
        self.assertEqual(items[0]["url"], "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-8k.htm")

    def test_sec_resolves_etf_from_fund_ticker_list(self):
        company_req = httpx.Request("GET", "https://www.sec.gov/files/company_tickers.json")
        fund_req = httpx.Request("GET", "https://www.sec.gov/files/company_tickers_mf.json")
        efts_req = httpx.Request("GET", "https://efts.sec.gov/LATEST/search-index")
        company_resp = httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=company_req)
        fund_resp = httpx.Response(
            200,
            json={"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[1378872, "S000069448", "C000221604", "QQQM"]]},
            request=fund_req,
        )
        efts_resp = httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [{
                        "_id": "0001752724-25-017905:primary_doc.xml",
                        "_source": {
                            "ciks": ["0001378872"],
                            "form": "NPORT-P",
                            "file_date": "2026-04-29",
                            "adsh": "0001752724-25-017905",
                        },
                    }],
                },
            },
            request=efts_req,
        )
        with patch.object(announce.httpx, "get", side_effect=[company_resp, fund_resp, efts_resp]) as get:
            items = announce.fetch_sec("QQQM.US", "Invesco NASDAQ 100 ETF")
        self.assertEqual(get.call_args.kwargs["params"]["q"], "C000221604")
        self.assertIn("NPORT-P", get.call_args.kwargs["params"]["forms"])
        self.assertIn("NPORT-P", items[0]["title"])
        self.assertEqual(items[0]["url"], "https://www.sec.gov/Archives/edgar/data/1378872/000175272425017905/primary_doc.xml")

    def test_cninfo_falls_back_to_eastmoney_fund_when_org_missing(self):
        org_req = httpx.Request("POST", "https://www.cninfo.com.cn/new/information/topSearch/query")
        fund_req = httpx.Request("GET", "https://api.fund.eastmoney.com/f10/JJGG")
        org_resp = httpx.Response(200, json=[], request=org_req)
        fund_resp = httpx.Response(
            200,
            json={"ErrCode": 0, "Data": [{
                "FUNDCODE": "513650",
                "TITLE": "溢价风险提示公告",
                "ShortTitle": "南方标普500ETF",
                "PUBLISHDATEDesc": "2026-10-27",
                "ID": "AN202610271831040029",
            }]},
            request=fund_req,
        )
        with patch.object(announce.httpx, "post", return_value=org_resp), \
             patch.object(announce.httpx, "get", return_value=fund_resp) as get:
            items = announce.fetch_cninfo("513650.SH", "标普500ETF南方")
        self.assertEqual(get.call_args.kwargs["params"]["fundcode"], "513650")
        self.assertEqual(items[0]["title"], "溢价风险提示公告")
        self.assertEqual(items[0]["url"], "https://fund.eastmoney.com/gonggao/513650,AN202610271831040029.html")
        self.assertEqual(items[0]["published_at"], "2026-10-27")


if __name__ == "__main__":
    unittest.main()
