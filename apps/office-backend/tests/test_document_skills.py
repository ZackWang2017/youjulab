"""文档技能：类型在后台，参数随技能保存，执行时走本地工具并写回文档。"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from document_skill_run import WeReadLoginRequired, _collect_items, _select_new_items, _write_news_brief, run_document_skill  # noqa: E402
from config import digest_tz  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
from securities_lookup import lookup_securities  # noqa: E402
from server import create_app  # noqa: E402
from store import MemoryStore  # noqa: E402


class DocumentSkillApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(MemoryStore()))

    def _auth(self) -> tuple[dict, str]:
        data = self.client.post("/auth/register", json={"email": "skill@example.com", "password": "secret1"}).json()
        return {"Authorization": f"Bearer {data['token']}"}, data["user"]["last_assets"]["document"]

    def test_create_list_and_run(self) -> None:
        headers, document_id = self._auth()
        types = self.client.get("/document-skill-types", headers=headers).json()["types"]
        self.assertEqual(types[0]["id"], "news_brief")
        self.assertEqual(types[0]["label"], "资讯简报")
        fields = {field["key"]: field for field in types[0]["params"]}
        self.assertNotIn("wechat_accounts", fields)
        self.assertEqual(fields["preset_sources"]["label"], "信息源")
        self.assertEqual(fields["date_filter"]["label"], "时间范围")
        self.assertEqual(fields["date_filter"]["options"], [{"id": "today", "label": "仅今日"}])
        self.assertEqual(
            [option["label"] for option in fields["preset_sources"]["options"]],
            ["港交所", "新浪财经", "东方财富", "雪球", "集思录", "腾讯自选股", "微信读书"],
        )
        created = self.client.post(f"/assets/{document_id}/skills", headers=headers, json={
            "name": "腾讯简报",
            "type": "news_brief",
            "params": {
                "stocks": "腾讯控股",
                "topic": "游戏",
                "preset_sources": "hkex",
                "date_filter": "today",
                "wechat_accounts": "",
            },
        })
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["type_label"], "资讯简报")
        self.assertEqual(body["params"]["date_filter"], "today")
        self.assertTrue(body["project_id"])
        listed = self.client.get(f"/assets/{document_id}/skills", headers=headers).json()["skills"]
        self.assertEqual([item["name"] for item in listed], ["腾讯简报"])
        seen = {}

        async def fake(skill, *, project_name, document_name, user_token, prepare_document=None):
            del skill, project_name, user_token, prepare_document
            seen["document_name"] = document_name
            yield {"type": "done", "ok": True, "title": "腾讯简报"}

        with patch("server.iter_skill_run_events", fake):
            ran = self.client.post(f"/assets/{document_id}/skills/{body['id']}/run", headers=headers)
        self.assertEqual(ran.status_code, 200, ran.text)
        self.assertIn("腾讯简报", ran.text)
        self.assertEqual(seen["document_name"], "未命名文档")
        removed = self.client.delete(f"/assets/{document_id}/skills/{body['id']}", headers=headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get(f"/assets/{document_id}/skills", headers=headers).json()["skills"], [])

    def test_stock_names_collapse_spaces(self) -> None:
        headers, document_id = self._auth()
        created = self.client.post(f"/assets/{document_id}/skills", headers=headers, json={
            "name": "多股票",
            "type": "news_brief",
            "params": {
                "stocks": "腾讯控股  阿里巴巴",
                "topic": "游戏",
                "preset_sources": "hkex",
                "wechat_accounts": "",
            },
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["params"]["stocks"], "腾讯控股 阿里巴巴")

    def test_preset_source_is_enough(self) -> None:
        headers, document_id = self._auth()
        created = self.client.post(f"/assets/{document_id}/skills", headers=headers, json={
            "name": "港交所简报",
            "type": "news_brief",
            "params": {
                "stocks": "00700.HK|腾讯控股",
                "topic": "游戏",
                "preset_sources": "hkex,sina",
                "wechat_accounts": "",
            },
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["params"]["stocks"], "00700.HK|腾讯控股")
        self.assertEqual(created.json()["params"]["preset_sources"], "hkex,sina")

    def test_requires_a_source(self) -> None:
        headers, document_id = self._auth()
        created = self.client.post(f"/assets/{document_id}/skills", headers=headers, json={
            "name": "空来源",
            "type": "news_brief",
            "params": {"stocks": "腾讯控股", "topic": "游戏", "preset_sources": "", "wechat_accounts": ""},
        })
        self.assertEqual(created.status_code, 400)

    def test_oversized_save_archives_and_keeps_skill(self) -> None:
        headers, document_id = self._auth()
        created = self.client.post(f"/assets/{document_id}/skills", headers=headers, json={
            "name": "腾讯简报",
            "type": "news_brief",
            "params": {"stocks": "腾讯控股", "topic": "游戏", "preset_sources": "hkex"},
        })
        self.assertEqual(created.status_code, 200, created.text)
        skill_id = created.json()["id"]
        huge = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x" * 200}]}]}
        with patch("document_archive.MAX_DOCUMENT_BYTES", 80):
            saved = self.client.put(f"/assets/{document_id}", headers=headers, json={"content": huge, "revision": 0})
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertNotEqual(body["id"], document_id)
        self.assertEqual(body["name"], "未命名文档")
        self.assertIn("_bak_", body["archived_name"])
        archived = self.client.get(f"/assets/{document_id}", headers=headers).json()
        self.assertEqual(archived["name"], body["archived_name"])
        self.assertEqual(self.client.get(f"/assets/{document_id}/skills", headers=headers).json()["skills"], [])
        moved = self.client.get(f"/assets/{body['id']}/skills", headers=headers).json()["skills"]
        self.assertEqual([item["id"] for item in moved], [skill_id])


class NewsBriefMarkdownTests(unittest.TestCase):
    def test_keeps_each_fetched_item(self) -> None:
        with patch("document_skill_run._fetch_preset", return_value={
            "results": [{
                "title": "回购",
                "url": "https://example.com/a",
                "published_at": "2026-09-26 08:00",
                "summary": "继续回购。",
            }],
            "error": "",
        }):
            items = _collect_items({
                "stocks": "腾讯控股",
                "topic": "游戏",
                "preset_sources": "hkex",
                "wechat_accounts": "",
            })
        self.assertEqual(items[0]["title"], "回购")
        self.assertEqual(items[0]["url"], "https://example.com/a")
        self.assertIn("继续回购。", items[0]["text"])

    def test_model_writes_from_skill(self):
        captured = {}

        async def fake(messages, **kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = json.loads(messages[1]["content"])
            yield {"type": "delta", "text": json.dumps({
                "groups": [
                    {"source": "网页", "items": [{
                        "stocks": "腾讯控股",
                        "title": "回购",
                        "published_at": "2026-09-26 08:00",
                        "summary": "继续回购。",
                        "url": "https://example.com/a",
                        "business": "游戏",
                        "financial": "材料不足",
                        "period": "材料不足",
                        "verification": "季报",
                        "error": "",
                    }]},
                    {"source": "港交所", "items": [{
                        "stocks": "腾讯控股",
                        "title": "翌日披露",
                        "published_at": "发布时间未知",
                        "summary": "披露报表。",
                        "url": "https://example.com/b",
                        "business": "材料不足",
                        "financial": "材料不足",
                        "period": "材料不足",
                        "verification": "材料不足",
                        "error": "",
                    }]},
                ],
            }, ensure_ascii=False)}

        with patch("document_skill_run._fetch_preset", return_value={
            "results": [{
                "title": "回购",
                "url": "https://example.com/a",
                "published_at": "",
                "summary": "继续回购。",
            }],
            "error": "",
        }), patch("document_skill_run.iter_chat_events", fake):
            text = asyncio.run(_write_news_brief({
                "stocks": "腾讯控股 阿里巴巴",
                "topic": "游戏",
                "preset_sources": "hkex",
                "wechat_accounts": "",
            }))
        self.assertIn("业务板块", captured["system"])
        self.assertEqual(captured["user"]["stock_names"], ["腾讯控股", "阿里巴巴"])
        self.assertEqual(captured["user"]["items"][0]["url"], "https://example.com/a")
        self.assertTrue(text.startswith("---\n## "))
        self.assertIn("### 网页", text)
        self.assertIn("1. **【腾讯控股】回购**（2026-09-26 08:00）", text)
        self.assertIn("### 港交所", text)
        self.assertIn("2. **【腾讯控股】翌日披露**（发布时间未知）", text)
        self.assertIn("继续回购。 🔗 [原文](https://example.com/a)", text)
        self.assertNotIn("\n🔗 [原文]", text)
        self.assertIn("○ **业务板块**：游戏", text)
        self.assertIn("○ **验证方法**：季报", text)
        self.assertIn("发布时间", captured["system"])

    def test_removed_caixin_source_is_skipped(self) -> None:
        calls = []

        def fetch(source, ticker_code, name):
            calls.append((source, ticker_code, name))
            return {"results": [], "error": ""}

        with patch("document_skill_run._fetch_preset", side_effect=fetch):
            items = _collect_items({
                "stocks": "00700.HK|腾讯控股",
                "topic": "游戏",
                "preset_sources": "hkex,caixin",
                "wechat_accounts": "",
            })
        self.assertEqual(calls, [("hkex", "00700.HK", "腾讯控股")])
        self.assertFalse(any(item["source"] == "财新" for item in items))

    def test_weread_queries_the_shelf_once(self) -> None:
        with patch("document_skill_run._fetch_preset", return_value={"results": [], "error": ""}) as preset, \
             patch("document_skill_run._fetch_weread_shelf", return_value={
                 "error": "",
                 "accounts": [{
                     "id": "MP_WXS_1",
                     "title": "腾讯财经",
                     "error": "",
                     "results": [{
                         "title": "早报",
                         "url": "https://mp.weixin.qq.com/s/a",
                         "published_at": "2026-09-26 08:00",
                         "summary": "摘要",
                     }],
                 }],
             }) as shelf:
            items = _collect_items({
                "stocks": "00700.HK|腾讯控股\n09988.HK|阿里巴巴",
                "topic": "游戏",
                "preset_sources": "hkex,weread",
            })
        self.assertEqual(preset.call_count, 2)
        shelf.assert_called_once_with()
        weread = [item for item in items if item["source"] == "腾讯财经"]
        self.assertEqual(weread[0]["title"], "早报")

    def test_weread_login_required_stops_before_model_generation(self) -> None:
        with patch("document_skill_run._fetch_weread_shelf", return_value={
            "error": "微信读书登录已过期。", "login_required": True, "accounts": [],
        }), patch("document_skill_run.start_weread_login", return_value={
            "session_id": "scan-1", "status": "pending", "qr_data_url": "data:image/svg+xml;base64,test",
        }):
            with self.assertRaises(WeReadLoginRequired) as raised:
                _collect_items({"preset_sources": "weread"})
        self.assertEqual(raised.exception.session["session_id"], "scan-1")

    def test_weread_title_is_kept_when_the_summary_is_empty(self) -> None:
        with patch("document_skill_run._fetch_weread_shelf", return_value={
            "error": "",
            "accounts": [{
                "id": "MP_WXS_1",
                "title": "腾讯财经",
                "error": "",
                "results": [{
                    "title": "早报",
                    "url": "https://mp.weixin.qq.com/s/a",
                    "published_at": "",
                    "summary": "",
                }],
            }],
        }):
            items = _collect_items({"stocks": "", "topic": "", "preset_sources": "weread"})
        fresh, _ = _select_new_items(items, [])
        self.assertEqual(fresh[0]["title"], "早报")
        self.assertEqual(fresh[0]["text"], "早报")

    def test_same_body_is_skipped_until_text_changes(self) -> None:
        first, seen = _select_new_items([{"text": "继续  回购。", "error": ""}], [])
        again, extra = _select_new_items([{"text": "继续 回购。", "error": ""}], seen)
        changed, _ = _select_new_items([{"text": "回购金额上调。", "error": ""}], seen)
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        self.assertEqual(extra, [])
        self.assertEqual(changed[0]["text"], "回购金额上调。")

    def test_same_url_is_skipped_when_summary_or_tracking_query_changes(self) -> None:
        first = {
            "source": "腾讯财经", "title": "早报",
            "url": "https://mp.weixin.qq.com/s/abc?from=timeline&utm_source=test",
            "published_at": "2026-09-29 08:00", "text": "短摘要", "error": "",
        }
        changed = {
            **first,
            "url": "https://mp.weixin.qq.com/s/abc?scene=1",
            "text": "后续抓取到了更长的正文摘要",
        }
        fresh, seen = _select_new_items([first], [])
        again, added = _select_new_items([changed], seen)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(again, [])
        self.assertEqual(added, [])

    def test_same_summary_with_different_urls_is_not_deduplicated(self) -> None:
        items = [
            {"source": "公众号", "title": "甲", "url": "https://example.com/a", "text": "摘要", "error": ""},
            {"source": "公众号", "title": "乙", "url": "https://example.com/b", "text": "摘要", "error": ""},
        ]
        fresh, _ = _select_new_items(items, [])
        self.assertEqual([item["title"] for item in fresh], ["甲", "乙"])

    def test_today_filter_removes_old_and_unknown_weread_articles(self) -> None:
        today = datetime.now(ZoneInfo(digest_tz()))
        yesterday = today - timedelta(days=1)
        with patch("document_skill_run._fetch_weread_shelf", return_value={
            "error": "",
            "accounts": [{
                "id": "MP_WXS_1",
                "title": "腾讯财经",
                "error": "",
                "results": [
                    {"title": "今日", "url": "https://mp.weixin.qq.com/s/today", "published_at": today.strftime("%Y-%m-%d 08:00"), "summary": "今日摘要"},
                    {"title": "昨日", "url": "https://mp.weixin.qq.com/s/old", "published_at": yesterday.strftime("%Y-%m-%d 08:00"), "summary": "昨日摘要"},
                    {"title": "未知", "url": "https://mp.weixin.qq.com/s/unknown", "published_at": "", "summary": "未知摘要"},
                    {"title": "今日重复", "url": "https://mp.weixin.qq.com/s/today?scene=1", "published_at": today.strftime("%Y-%m-%d 08:00"), "summary": "重复摘要"},
                ],
            }],
        }):
            items = _collect_items({"preset_sources": "weread", "date_filter": "today"})
        self.assertEqual([item["title"] for item in items], ["今日"])

    def test_repeat_run_does_not_call_the_model(self) -> None:
        item = {
            "source": "网页", "title": "回购", "url": "https://example.com/a",
            "published_at": "2026-09-26 08:00", "text": "继续回购。", "error": "",
        }
        skill = {"type": "news_brief", "params": {"stocks": "腾讯控股", "topic": "游戏", "preset_sources": "hkex"}}
        calls = {"model": 0, "writes": 0}

        async def fake(messages, **kwargs):
            calls["model"] += 1
            yield {"type": "delta", "text": json.dumps({
                "groups": [{"source": "网页", "items": [{
                    "stocks": "腾讯控股", "title": "回购", "published_at": "2026-09-26 08:00",
                    "summary": "继续回购。", "url": "https://example.com/a",
                    "business": "材料不足", "financial": "材料不足", "period": "材料不足",
                    "verification": "材料不足", "error": "",
                }]}],
            }, ensure_ascii=False)}

        def append(*args, **kwargs):
            calls["writes"] += 1

        with patch("document_skill_run._collect_items", return_value=[item]), \
             patch("document_skill_run.iter_chat_events", fake), \
             patch("document_skill_run._append_document", append):
            first = run_document_skill(skill, project_name="项目", document_name="文档", user_token="token")
            with self.assertRaisesRegex(ValueError, "本次没有新资讯"):
                run_document_skill(
                    {**skill, "seen_fingerprints": first["seen_fingerprints"]},
                    project_name="项目", document_name="文档", user_token="token",
                )
        self.assertEqual(calls["model"], 1)
        self.assertEqual(calls["writes"], 1)

    def test_lookup_reads_mcp_items(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "data": {"items": [{"ticker_code": "00700.HK", "name": "腾讯控股"}]}}
        with patch("securities_lookup.httpx.get", return_value=response) as get:
            items = lookup_securities("腾讯")
        self.assertEqual(items, [{"ticker_code": "00700.HK", "name": "腾讯控股"}])
        self.assertIn("/api/financial/securities/search", get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
