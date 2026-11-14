"""Information migration checks; all writes use temporary SQLite files."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from information.repository import information_repository as repo


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "office.sqlite"
        self.env = patch.dict(os.environ, {"OFFICE_SQLITE_PATH": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def item(self, group, owner="alice", title="news"):
        return repo.insert_item({
            "owner_id": owner, "group_id": group["id"], "title": title,
            "source_kind": "wechat", "source_name": "source",
            "fetched_date": date(2026, 11, 11),
        })

    def test_isolation_foreign_keys_and_serializers(self):
        group = repo.create_group("alice", "news", "wechat", {"key": "value"})
        item = self.item(group)
        self.assertEqual(repo.public_group(group)["config"], {"key": "value"})
        self.assertEqual(repo.public_item(item)["title"], "news")
        self.assertIsNone(repo.get_group("bob", group["id"]))
        self.assertIsNone(repo.get_item("bob", item["id"]))
        self.assertIsNone(repo.set_hidden("bob", item["id"]))
        self.assertEqual(repo.list_items("bob"), [])
        self.assertIsNone(self.item(group))
        with self.assertRaises(sqlite3.IntegrityError):
            self.item(group, owner="bob")
        with self.assertRaises(sqlite3.IntegrityError):
            repo.upsert_review({"owner_id": "bob", "group_id": group["id"],
                                "review_date": date(2026, 11, 11)})
        review = repo.upsert_review({"owner_id": "alice", "group_id": group["id"],
                                     "review_date": date(2026, 11, 11), "content": "first"})
        updated = repo.upsert_review({"owner_id": "alice", "group_id": group["id"],
                                      "review_date": date(2026, 11, 11), "content": "second"})
        self.assertEqual(review["id"], updated["id"])
        self.assertEqual(repo.public_review(updated)["content"], "second")
        self.assertIsNone(repo.get_review("bob", review["id"]))

    def test_concurrent_favorites_and_item_deduplication(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups = list(pool.map(lambda _: repo.create_group("alice", "favorites", "favorites"), range(20)))
        self.assertEqual(len({row["id"] for row in groups}), 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda _: self.item(groups[0]), range(20)))
        self.assertEqual(sum(item is not None for item in items), 1)

    def test_delete_preserves_favorites_and_cascades_other_rows(self):
        group = repo.create_group("alice", "news", "wechat")
        favorite = self.item(group)
        other = self.item(group, title="other")
        repo.set_favorite("alice", favorite["id"], True, "wechat", "news")
        review = repo.upsert_review({"owner_id": "alice", "group_id": group["id"],
                                     "review_date": date(2026, 11, 11)})
        self.assertFalse(repo.delete_group("bob", group["id"]))
        self.assertTrue(repo.delete_group("alice", group["id"]))
        saved = repo.get_item("alice", favorite["id"])
        self.assertEqual(saved["group_id"], repo.find_group_by_kind("alice", "favorites")["id"])
        self.assertIsNone(repo.get_item("alice", other["id"]))
        self.assertIsNone(repo.get_review("alice", review["id"]))

    def test_initialize_supplied_connection_and_restore_sequences(self):
        with sqlite3.connect(":memory:") as conn:
            with patch.object(repo, "sqlite_path", side_effect=AssertionError("must not resolve real path")):
                repo.initialize_schema(conn)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.execute("INSERT INTO information_groups(id,owner_id,name,kind) VALUES(150,'a','n','wechat')")
            conn.execute("INSERT INTO information_groups(owner_id,name,kind) VALUES('a','next','wechat')")
            self.assertEqual(conn.execute("SELECT max(id) FROM information_groups").fetchone()[0], 151)
        self.assertFalse(self.path.exists())

    def test_shares_json_store_tables_and_reconfiguration(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE assets(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            conn.execute("INSERT INTO assets VALUES('asset', '{}')")
        repo.create_group("alice", "news", "wechat")
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT data FROM assets").fetchone()[0], "{}")
        with patch.dict(os.environ, {"OFFICE_SQLITE_PATH": str(Path(self.temp.name) / "other.sqlite")}):
            self.assertEqual(repo.list_groups("alice"), [])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        RepositoryTests.setUp(self)
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.testclient import TestClient
        from information.endpoints import register_routes

        async def user_from(request: Request):
            from remote_auth import set_remote_token
            set_remote_token(request.headers.get("x-remote-token"))
            owner = request.headers.get("x-test-owner")
            if not owner:
                raise HTTPException(401, "login required")
            return {"id": owner}

        # Resolve the dependency annotation in this test module.
        user_from.__annotations__["request"] = Request
        self.app = FastAPI()
        from store import MemoryStore
        self.store = MemoryStore()
        self.project = self.store.create_project("alice", "project")
        self.app.state.store = self.store
        register_routes(self.app, user_from)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_http_user_scope_and_validation(self):
        self.assertEqual(self.client.get("/api/information/groups").status_code, 401)
        headers = {"x-test-owner": "alice"}
        result = self.client.post("/api/information/groups", headers=headers,
                                  json={"name": "news", "kind": "wechat", "project_id": self.project["id"]})
        self.assertEqual(result.status_code, 200, result.text)
        gid = result.json()["id"]
        self.assertEqual(self.client.get(f"/api/information/groups/{gid}/items",
                                        headers={"x-test-owner": "bob"}).status_code, 404)
        bad = self.client.post("/api/information/groups", headers=headers, content="[]")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json(), {"error": "请求体必须是对象。"})
        self.assertEqual(self.client.get("/api/information/groups/bad/items", headers=headers).status_code, 400)

    def test_weread_owner_and_fetch_forwarding(self):
        from information.service import information_service as service
        headers = {"x-test-owner": "alice"}
        with patch.object(service.weread, "current_status", return_value={"logged_in": False}) as status:
            self.assertEqual(self.client.get("/api/information/weread/status", headers=headers).status_code, 200)
            status.assert_called_once_with("alice")
        token = service.active_store.set(self.store)
        try:
            group = service.create_group("alice", {"name": "news", "kind": "wechat", "project_id": self.project["id"]})
        finally:
            service.active_store.reset(token)
        with patch.object(service.fetch, "collect_group", return_value={"items": []}) as collect:
            response = self.client.post(f"/api/information/groups/{group['id']}/fetch", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            collect.assert_called_once_with("wechat", {}, "alice")

    def test_holdings_use_remote_portfolio_and_propagate_token(self):
        import httpx
        from information.service import information_service as service
        seen = []

        def remote_get(url, **kwargs):
            seen.append((url, kwargs))
            payload = {"portfolios": [{"id": 4}]} if url.endswith("/portfolios") else {
                "holdings": [{"ticker_code": "600519.SH", "market_code": "SH", "name": "stock"},
                             {"ticker_code": "CNY", "market_code": "CASH"},
                             {"ticker_code": "600519.SH", "market_code": "SH"}]}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        with patch.object(service.httpx, "get", side_effect=remote_get):
            response = self.client.get("/api/information/holdings",
                                       headers={"x-test-owner": "alice", "x-remote-token": "remote-jwt"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["holdings"]), 1)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all("/api/portfolio/" in url for url, _ in seen))
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer remote-jwt" for _, kwargs in seen))
        self.assertEqual(seen[1][1]["params"], {"refresh": "0"})
        self.assertEqual(self.client.get("/api/information/holdings",
                                        headers={"x-test-owner": "alice"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
