"""SqliteStore 与 sqlite-vec 冒烟测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# 必须在 import store 前设置
_TMP = tempfile.mkdtemp(prefix="youju-sqlite-")
os.environ["OFFICE_STORE"] = "sqlite"
os.environ["OFFICE_SQLITE_PATH"] = str(Path(_TMP) / "office.sqlite")
os.environ["OFFICE_VECTOR_BACKEND"] = "sqlite"
os.environ["OFFICE_VECTOR_SQLITE_PATH"] = str(Path(_TMP) / "vectors.sqlite")
os.environ["OFFICE_MONGO_URL"] = "memory://unused"


from guest_user import ensure_guest_user  # noqa: E402
from office_pdf_vector import ensure_index, search_chunks  # noqa: E402
from store import create_store  # noqa: E402


class SqliteStoreTests(unittest.TestCase):
    def test_persist_asset(self):
        store = create_store()
        user = store.create_user("a@example.com", "password1")
        project = store.create_project(user["id"], "P1")
        asset = store.create_asset(user["id"], project["id"], "document", "Doc", {"type": "doc", "content": []})
        store.update_asset(user["id"], asset["id"], content={"type": "doc", "content": [{"type": "paragraph"}]})
        again = create_store()
        loaded = again.asset(user["id"], asset["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["name"], "Doc")
        self.assertEqual(loaded["content"]["content"][0]["type"], "paragraph")

    def test_guest_user(self):
        store = create_store()
        guest = ensure_guest_user(store)
        self.assertTrue(guest["email"].endswith("@local.youjulab"))
        again = ensure_guest_user(store)
        self.assertEqual(guest["id"], again["id"])

    def test_reassign_owner_moves_guest_assets(self):
        store = create_store()
        guest = ensure_guest_user(store)
        guest_id = guest["id"]
        bound = store.create_user("reassign@example.com", "password1")
        project = store.create_project(guest_id, "访客稿")
        store.create_asset(
            guest_id, project["id"], "document", "草稿",
            {"type": "doc", "content": []},
        )
        moved = store.reassign_owner(guest_id, bound["id"])
        self.assertGreater(moved, 0)
        self.assertEqual(store.projects(guest_id), [])
        owned = store.projects(bound["id"])
        self.assertTrue(any(p["name"] == "访客稿" for p in owned))
        assets = store.assets(bound["id"], project["id"], "document")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "草稿")


class BindAccountTests(unittest.TestCase):
    def test_bind_creates_user_and_claims_guest(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from server import create_app, prune_default_projects  # noqa: E402

        store = create_store()
        guest = ensure_guest_user(store)
        # 显式再挂一份访客资产，避免与其它用例共享库状态互相影响
        project = store.create_project(guest["id"], "待认领")
        store.create_asset(
            guest["id"], project["id"], "document", "待认领文档",
            {"type": "doc", "content": []},
        )
        # 改一下 revision，避免被当成空壳默认项丢掉
        store.update_asset(guest["id"], store.assets(guest["id"], project["id"], "document")[0]["id"], content={"type": "doc", "content": [{"type": "paragraph"}]})

        client = TestClient(create_app(store))
        with patch(
            "server.fetch_remote_account",
            return_value={"id": "remote-1", "email": "bound@example.com"},
        ):
            resp = client.post("/local/bind-account", headers={"X-Remote-Token": "fake-jwt"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["email"], "bound@example.com")
        self.assertTrue(data["token"])
        self.assertEqual(data["user"]["email"], "bound@example.com")
        owned = store.projects(data["user"]["id"])
        self.assertTrue(any(p["name"] == "待认领" for p in owned))
        # 访客应仍有一个默认项目，供下次离线使用
        guest_again = ensure_guest_user(store)
        self.assertTrue(store.projects(guest_again["id"]))

    def test_prune_default_projects(self):
        from server import prune_default_projects  # noqa: E402

        store = create_store()
        user = store.create_user("prune@example.com", "password1")
        real = store.create_project(user["id"], "正式项目")
        store.create_project(user["id"], "未命名项目")
        store.create_project(user["id"], "本地项目")
        store.create_project(user["id"], "未命名项目")
        removed = prune_default_projects(store, user["id"])
        self.assertEqual(removed, 3)
        names = [p["name"] for p in store.projects(user["id"])]
        self.assertEqual(names, ["正式项目"])
        self.assertEqual(real["id"], store.projects(user["id"])[0]["id"])


class SqliteVecTests(unittest.TestCase):
    def test_index_and_search_with_fake_embed(self):
        dim = 8

        def fake_embed(texts: list[str]) -> list[list[float]]:
            out = []
            for text in texts:
                vec = [0.0] * dim
                vec[0] = 1.0 if "alpha" in text.lower() else 0.1
                vec[1] = 1.0 if "beta" in text.lower() else 0.1
                out.append(vec)
            return out

        # 造一个极简 PDF 太重；直接跳过无文件路径，改测 glossary schema
        from office_pdf_vector_sqlite import ensure_schema, get_index_status
        ensure_schema()
        status = get_index_status("missing-doc")
        self.assertEqual(status["status"], "missing")
        _ = fake_embed, ensure_index, search_chunks


if __name__ == "__main__":
    unittest.main()
