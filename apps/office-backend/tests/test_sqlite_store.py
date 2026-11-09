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

class BindAccountTests(unittest.TestCase):
    def test_workspace_requires_authenticated_user(self):
        from fastapi.testclient import TestClient

        from server import create_app  # noqa: E402

        response = TestClient(create_app(create_store())).get("/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_bind_creates_user(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from server import create_app, prune_default_projects  # noqa: E402

        store = create_store()
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
        self.assertTrue(owned)

    def test_bind_keeps_password_for_offline_login_and_syncs_changes(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from server import create_app  # noqa: E402

        store = create_store()
        client = TestClient(create_app(store))
        remote = {"id": "remote-2", "email": "offline@example.com"}
        with patch("server.fetch_remote_account", return_value=remote):
            bound = client.post(
                "/local/bind-account",
                headers={"X-Remote-Token": "fake-jwt"},
                json={"password": "secret12"},
            )
        self.assertEqual(bound.status_code, 200, bound.text)
        login = client.post("/auth/login", json={"email": "offline@example.com", "password": "secret12"})
        self.assertEqual(login.status_code, 200, login.text)

        headers = {"Authorization": f"Bearer {login.json()['token']}", "X-Remote-Token": "fake-jwt"}
        with patch("server.fetch_remote_account", return_value=remote):
            synced = client.put("/local/password", headers=headers, json={"password": "changed12"})
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertEqual(client.post("/auth/login", json={"email": "offline@example.com", "password": "secret12"}).status_code, 401)
        self.assertEqual(client.post("/auth/login", json={"email": "offline@example.com", "password": "changed12"}).status_code, 200)

        with patch("server.fetch_remote_account", return_value={"id": "x", "email": "other@example.com"}):
            denied = client.put("/local/password", headers=headers, json={"password": "hijack12"})
        self.assertEqual(denied.status_code, 403)

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
        import pymupdf

        from config import embedding_dim
        from office_pdf_vector import delete_document_index, get_index_status

        dim = embedding_dim()
        doc_id = "sqlite-progress-doc"
        seen: dict = {}

        def fake_embed(texts: list[str]) -> list[list[float]]:
            seen["during"] = get_index_status(doc_id)
            out = []
            for text in texts:
                vec = [0.0] * dim
                vec[0] = 1.0 if "alpha" in text.lower() else 0.1
                vec[1] = 1.0 if "beta" in text.lower() else 0.1
                out.append(vec)
            return out

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Alpha revenue grew.")
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Beta supply chain risk.")
        path = Path(_TMP) / "tiny.pdf"
        doc.save(path)
        doc.close()
        try:
            missing = get_index_status("missing-doc")
            self.assertEqual(missing["status"], "missing")
            self.assertEqual(missing["progress_done"], 0)
            status = ensure_index(document_id=doc_id, user_id="u", path=str(path), embed=fake_embed)
            self.assertEqual(status["status"], "ready")
            self.assertGreater(status["chunk_count"], 0)
            self.assertEqual(status["progress_done"], status["chunk_count"])
            self.assertEqual(status["progress_total"], status["chunk_count"])
            self.assertEqual(status["phase"], "")
            during = seen["during"]
            self.assertEqual(during["phase"], "embedding")
            self.assertEqual(during["progress_done"], 0)
            self.assertEqual(during["progress_total"], status["chunk_count"])
            hits = search_chunks(document_id=doc_id, query="supply chain", top_k=2, embed=fake_embed)
            self.assertTrue(hits)
        finally:
            delete_document_index(doc_id)


if __name__ == "__main__":
    unittest.main()
