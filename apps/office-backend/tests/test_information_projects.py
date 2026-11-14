"""Project assets and permissions, using only temporary databases."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from information.repository import information_repository as repo
from information.service import information_service as service
from store import MemoryStore


class ProjectInformationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "office.sqlite"
        env = patch.dict(os.environ, {"OFFICE_SQLITE_PATH": str(self.path), "OFFICE_FILE_ROOT": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        from server import create_app
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        self.addCleanup(self.client.close)
        account = self.client.post("/auth/register", json={"email": "scope@example.com", "password": "secret1"}).json()
        self.owner = account["user"]["id"]
        self.headers = {"Authorization": f"Bearer {account['token']}"}
        self.first = account["user"]["last_project_id"]
        self.second = self.store.create_project(self.owner, "second")["id"]

    def request(self, method, url, **kwargs):
        return self.client.request(method, url, headers=self.headers, **kwargs)

    def group(self, project):
        response = self.request("POST", "/api/information/groups", json={"name": "news", "kind": "wechat", "project_id": project})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def item(self, group):
        return repo.insert_item({"owner_id": self.owner, "group_id": group["id"], "source_kind": "wechat",
                                 "source_name": "source", "title": "same article", "fetched_date": date.today()})

    def test_project_favorites_deduplication_and_recent(self):
        legacy = repo.create_group(self.owner, "legacy", "wechat")
        one, two = self.group(self.first), self.group(self.second)
        self.assertNotEqual(one["asset_id"], two["asset_id"])
        self.assertIsNotNone(self.item(one))
        self.assertIsNotNone(self.item(two))
        self.assertEqual(self.request("GET", "/api/information/groups").status_code, 400)
        for project, group in [(self.first, one), (self.second, two)]:
            groups = self.request("GET", f"/api/information/groups?project_id={project}").json()["groups"]
            self.assertEqual(len(groups), 2)
            self.assertNotIn(legacy["id"], [row["id"] for row in groups])
            self.assertEqual(groups[0]["name"], "资讯收藏栏")
            self.assertTrue(all(row["project_id"] == project and row["asset_id"] for row in groups))
            asset = self.store.asset(self.owner, group["asset_id"])
            self.assertEqual(asset["content"], {"format": "invp-information-group-v1", "group_id": group["id"]})
        self.assertEqual(self.request("POST", f"/assets/{one['asset_id']}/open").status_code, 200)
        recent = self.request("GET", f"/assets/recent?project_id={self.first}").json()["assets"]
        self.assertIn(one["asset_id"], [row["id"] for row in recent])
        self.assertNotIn(two["asset_id"], [row["id"] for row in recent])

    def test_generic_creation_rename_delete_and_project_cascade(self):
        result = self.request("POST", f"/projects/{self.first}/assets", json={"kind": "information", "name": "generic"})
        self.assertEqual(result.status_code, 200, result.text)
        asset = result.json()
        group_id = asset["content"]["group_id"]
        self.assertEqual(self.request("PUT", f"/assets/{asset['id']}", json={"name": "renamed"}).status_code, 200)
        self.assertEqual(repo.get_group(self.owner, group_id)["name"], "renamed")
        self.assertEqual(self.request("PUT", f"/api/information/groups/{group_id}", json={"name": "news renamed"}).status_code, 200)
        self.assertEqual(self.store.asset(self.owner, asset["id"])["name"], "news renamed")
        self.assertEqual(self.request("PUT", f"/assets/{asset['id']}", json={"visibility": "user"}).status_code, 200)
        self.assertEqual(self.store.asset(self.owner, asset['id'])['project_id'], self.first)
        self.assertEqual(self.request("DELETE", f"/assets/{asset['id']}").status_code, 200)
        self.assertIsNone(repo.get_group(self.owner, group_id))
        group = self.group(self.second)
        item = self.item(group)
        review = repo.upsert_review({"owner_id": self.owner, "group_id": group["id"], "review_date": date.today()})
        self.assertEqual(self.request("DELETE", f"/projects/{self.second}").status_code, 200)
        self.assertIsNone(repo.get_group(self.owner, group["id"]))
        self.assertIsNone(repo.get_item(self.owner, item["id"]))
        self.assertIsNone(repo.get_review(self.owner, review["id"]))

    def test_inaccessible_groups_items_reviews_and_versions(self):
        group = self.group(self.first)
        item = self.item(group)
        review = repo.upsert_review({"owner_id": self.owner, "group_id": group["id"], "review_date": date.today()})
        self.store._projects[self.first]["owner_id"] = "other"
        for path in [f"/api/information/groups/{group['id']}/items", f"/api/information/items/{item['id']}",
                     f"/api/information/reviews/{review['id']}", f"/assets/{group['asset_id']}?meta=1"]:
            self.assertEqual(self.request("GET", path).status_code, 404, path)
        self.assertEqual(self.request("POST", f"/assets/{group['asset_id']}/open").status_code, 404)
        token = service.active_store.set(self.store)
        try:
            with self.assertRaises(service.InformationError):
                service.save_analysis(self.owner, item["id"], {})
        finally:
            service.active_store.reset(token)

    def test_project_title_query_and_favorites_visibility(self):
        one, two = self.group(self.first), self.group(self.second)
        item = self.item(one)
        self.assertEqual(repo.known_titles(self.owner, self.second), set())
        self.assertEqual(repo.known_titles(self.owner, self.first), {"same article"})
        self.request("POST", f"/api/information/items/{item['id']}/favorite", json={"favorited": True})
        fav = repo.find_group_by_kind(self.owner, "favorites", self.second)
        self.assertEqual(self.request("GET", f"/api/information/groups/{fav['id']}/items").json()["items"], [])
        self.store.delete_asset(self.owner, one["asset_id"])
        self.assertEqual(self.request("GET", f"/api/information/items/{item['id']}").status_code, 404)

    def test_visibility_persists_without_changing_project(self):
        group = self.group(self.first)
        endpoint = f"/api/information/groups/{group['id']}"
        response = self.request('PUT', endpoint, json={'visibility': 'user'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['visibility'], 'user')
        self.assertEqual(self.store.asset(self.owner, group['asset_id'])['project_id'], self.first)
        self.assertEqual(self.request('PUT', endpoint, json={'visibility': 'invalid'}).status_code, 400)
        groups = self.request('GET', f'/api/information/groups?project_id={self.first}').json()['groups']
        fav = next(row for row in groups if row['kind'] == 'favorites')
        self.assertEqual(fav['visibility'], 'user')
        self.assertEqual(self.request('PUT', f"/api/information/groups/{fav['id']}", json={'visibility': 'project'}).status_code, 200)
        groups = self.request('GET', f'/api/information/groups?project_id={self.first}').json()['groups']
        self.assertEqual(next(row for row in groups if row['id'] == fav['id'])['visibility'], 'project')
        self.assertEqual(self.request('PUT', f"/api/information/groups/{fav['id']}", json={'name': 'changed'}).status_code, 400)

    def test_clear_requires_project_and_preserves_other_projects(self):
        one, two = self.group(self.first), self.group(self.second)
        old = []
        for group in [one, two]:
            old.append(repo.insert_item({"owner_id": self.owner, "group_id": group["id"], "source_kind": "wechat",
                                        "source_name": "source", "title": "old", "fetched_date": date(2000, 1, 1)}))
        self.assertEqual(self.request("POST", "/api/information/clear").status_code, 400)
        self.assertEqual(self.request("POST", "/api/information/clear?project_id=missing").status_code, 404)
        response = self.request("POST", f"/api/information/clear?project_id={self.first}")
        self.assertEqual(response.json(), {"deleted": 1})
        self.assertIsNone(repo.get_item(self.owner, old[0]["id"]))
        self.assertIsNotNone(repo.get_item(self.owner, old[1]["id"]))

    def test_ai_stream_inherits_live_store_permission_checks(self):
        group = self.group(self.first)
        item = self.item(group)
        self.store.delete_asset(self.owner, group["asset_id"])

        async def analyze(owner, item_id):
            self.assertIs(service.active_store.get(), self.store)
            service.get_item(owner, item_id)
            yield {"type": "done"}

        with patch("server.model_configured", return_value=True), patch("server.iter_analyze_events", analyze):
            response = self.request("POST", "/assistant/information/analyze", json={"item_id": item["id"]})
        self.assertIn('"type": "error"', response.text)
        self.assertNotIn('"type": "done"', response.text)

    def test_unlinked_asset_cannot_open_or_expose_metadata(self):
        asset = self.store.create_asset(self.owner, self.first, "information", "orphan",
                                        {"format": "invp-information-group-v1", "group_id": 999})
        self.assertEqual(self.request("GET", f"/assets/{asset['id']}?meta=1").status_code, 404)
        self.assertEqual(self.request("POST", f"/assets/{asset['id']}/open").status_code, 404)


class SchemaUpgradeTests(unittest.TestCase):
    def test_live_sqlite_store_keeps_created_assets_after_reload(self):
        from sqlite_store import SqliteStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "office.sqlite"
            with patch.dict(os.environ, {"OFFICE_SQLITE_PATH": str(path)}):
                store = SqliteStore(path)
                try:
                    project = store.create_project("u", "project")
                    token = service.active_store.set(store)
                    try:
                        groups = service.list_groups("u", project["id"])
                        group = service.create_group("u", {"project_id": project["id"], "name": "news", "kind": "wechat"})
                    finally:
                        service.active_store.reset(token)
                    self.assertEqual(len(store.assets("u", project["id"], "information")), 2)
                    self.assertTrue(store.asset("u", groups[0]["asset_id"]))
                finally:
                    store.close()
                reopened = SqliteStore(path)
                try:
                    self.assertEqual(reopened.asset("u", group["asset_id"])["content"]["group_id"], group["id"])
                    self.assertEqual(len(repo.list_groups("u", project["id"])), 2)
                finally:
                    reopened.close()

    def test_legacy_nullable_upgrade_and_item_constraint_preserve_rows(self):
        old = repo._DDL.replace("UNIQUE (owner_id, group_id, title)", "UNIQUE (owner_id, title)")
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(old)
            conn.execute("INSERT INTO information_groups(id, owner_id, name, kind) VALUES (1,'u','legacy','wechat')")
            conn.execute("INSERT INTO information_items(id,owner_id,group_id,source_kind,source_name,title,fetched_date) VALUES(7,'u',1,'wechat','s','article','2026-11-11')")
            repo.initialize_schema(conn)
            repo.initialize_schema(conn)
            self.assertEqual(conn.execute("SELECT project_id,asset_id FROM information_groups").fetchone(), (None, None))
            self.assertEqual(conn.execute("SELECT id,title FROM information_items").fetchone(), (7, "article"))
            conn.execute("INSERT INTO information_groups(id,owner_id,name,kind) VALUES(2,'u','other','wechat')")
            conn.execute("INSERT INTO information_items(owner_id,group_id,source_kind,source_name,title,fetched_date) VALUES('u',2,'wechat','s','article','2026-11-11')")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
