import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import create_app
from store import MemoryStore
from assistant import registry, visible_json_string, _fit_chat_budget, parse_model_result, strip_thinking, answer_directive
from office_patch import aim_sheet_write, create_patch, validate_office_patch
from sheet_cache import structure_fingerprint
from office_context import OfficeContext


SHEET_CONTEXT = {
    "document_id": "sheet-1",
    "surface": "sheet",
    "revision": 0,
    "selection": {
        "sheet_id": "s1",
        "row": 0,
        "column": 0,
        "rows": 1,
        "cols": 1,
        "values": [["  hi  "]],
    },
    "surrounding": "",
}

MINDMAP_CONTEXT = {
    "document_id": "mindmap-1",
    "surface": "mindmap",
    "revision": 0,
    "selection": {
        "id": "root",
        "text": "投资研究",
        "path": ["投资研究"],
        "parent_id": None,
        "is_root": True,
    },
    "surrounding": json.dumps({
        "selected_id": "root",
        "tree": {
            "id": "root", "topic": "投资研究", "children": [
                {"id": "n1", "topic": "行业", "children": []},
            ],
        },
    }, ensure_ascii=False),
}


class OfficeApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))

    def auth(self, email, password="secret1"):
        return self.client.post("/auth/register", json={"email": email, "password": password}).json()

    def test_register_seeds_project_and_last_selection(self):
        data = self.auth("a@example.com")
        self.assertTrue(data["token"])
        user = data["user"]
        self.assertTrue(user["last_project_id"])
        self.assertTrue(user["last_assets"]["sheet"])
        self.assertTrue(user["last_assets"]["document"])
        self.assertTrue(user["last_assets"]["research"])
        self.assertTrue(user["last_assets"].get("calendar"))
        me = self.client.get("/auth/me", headers={"Authorization": f"Bearer {data['token']}"}).json()
        self.assertEqual(me["last_project_id"], user["last_project_id"])
        headers = {"Authorization": f"Bearer {data['token']}"}
        sheet = self.client.get(f"/assets/{user['last_assets']['sheet']}", headers=headers).json()
        document = self.client.get(f"/assets/{user['last_assets']['document']}", headers=headers).json()
        research = self.client.get(f"/assets/{user['last_assets']['research']}", headers=headers).json()
        calendar = self.client.get(f"/assets/{user['last_assets']['calendar']}", headers=headers).json()
        self.assertEqual(sheet["name"], "未命名表格")
        self.assertEqual(document["name"], "未命名文档")
        self.assertEqual(document["placement"], "project")
        self.assertEqual(document["content"], {"type": "doc", "content": [{"type": "paragraph"}]})
        meta = self.client.get(f"/assets/{user['last_assets']['document']}?meta=1", headers=headers).json()
        self.assertEqual(meta["revision"], document["revision"])
        self.assertNotIn("content", meta)
        self.assertEqual(research["content"]["format"], "invp-research-diagram-v1")
        self.assertEqual(research["content"]["type"], "timeline")
        self.assertEqual(calendar["name"], "日常工作")
        self.assertIsNone(calendar["project_id"])
        self.assertEqual(calendar["placement"], "user")
        self.assertEqual(calendar["content"]["format"], "invp-calendar-v1")
        self.assertEqual(calendar["content"]["weeks"], {})
        self.assertEqual(calendar["content"]["days"], {})
        self.assertEqual(calendar["content"]["todos"], "")
        calc = self.client.get(f"/projects/{user['last_project_id']}/assets?kind=calc", headers=headers).json()["assets"]
        self.assertEqual(len(calc), 1)
        self.assertIsNone(calc[0]["project_id"])
        self.assertEqual(calc[0]["placement"], "user")
        notebooks = self.client.get(f"/projects/{user['last_project_id']}/assets?kind=notebook", headers=headers).json()["assets"]
        self.assertEqual(len(notebooks), 1)
        self.assertEqual(notebooks[0]["name"], "笔记本")
        self.assertIsNone(notebooks[0]["project_id"])
        self.assertEqual(notebooks[0]["placement"], "user")
        notebook = self.client.get(f"/assets/{notebooks[0]['id']}", headers=headers).json()
        self.assertEqual(notebook["content"], {"type": "doc", "content": [{"type": "paragraph"}]})

    def test_document_visibility_and_legacy_migration(self):
        data = self.auth("visibility@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        first_project = data["user"]["last_project_id"]
        personal = self.client.post(
            f"/projects/{first_project}/assets",
            headers=headers,
            json={"kind": "document", "name": "个人笔记", "visibility": "user"},
        ).json()
        self.assertEqual(personal["placement"], "user")
        self.assertIsNone(personal["project_id"])

        second_project = self.client.post("/projects", headers=headers, json={"name": "二号项目"}).json()
        second_docs = self.client.get(
            f"/projects/{second_project['id']}/assets?kind=document", headers=headers,
        ).json()["assets"]
        self.assertIn(personal["id"], [item["id"] for item in second_docs])

        legacy_id = data["user"]["last_assets"]["document"]
        self.store._assets[legacy_id].pop("visibility", None)
        self.store._assets[legacy_id]["project_id"] = None
        migrated = self.client.get(
            f"/projects/{first_project}/assets?kind=document", headers=headers,
        ).json()["assets"]
        legacy = next(item for item in migrated if item["id"] == legacy_id)
        self.assertEqual(legacy["placement"], "project")
        self.assertEqual(legacy["project_id"], first_project)

        # 编辑时可改可见范围：项目可见 → 我可见 → 再绑回当前项目
        shared = self.client.put(
            f"/assets/{legacy_id}",
            headers=headers,
            json={"revision": 0, "name": "共享笔记", "visibility": "user"},
        ).json()
        self.assertEqual(shared["placement"], "user")
        self.assertIsNone(shared["project_id"])
        self.client.post(f"/projects/{first_project}/select", headers=headers)
        rebound = self.client.put(
            f"/assets/{legacy_id}",
            headers=headers,
            json={"revision": shared["revision"], "visibility": "project"},
        ).json()
        self.assertEqual(rebound["placement"], "project")
        self.assertEqual(rebound["project_id"], first_project)

        removed = self.client.delete(f"/assets/{personal['id']}", headers=headers)
        self.assertEqual(removed.status_code, 200)

    def test_project_plan_visibility_create_and_edit(self):
        data = self.auth("plan-vis@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        first = data["user"]["last_project_id"]
        personal = self.client.post(
            f"/projects/{first}/assets",
            headers=headers,
            json={"kind": "project_plan", "name": "个人计划", "visibility": "user"},
        ).json()
        self.assertEqual(personal["placement"], "user")
        self.assertIsNone(personal["project_id"])

        second = self.client.post("/projects", headers=headers, json={"name": "另一项目"}).json()
        listed = self.client.get(
            f"/projects/{second['id']}/assets?kind=project_plan", headers=headers,
        ).json()["assets"]
        self.assertIn(personal["id"], [item["id"] for item in listed])

        self.client.post(f"/projects/{second['id']}/select", headers=headers)
        project_scoped = self.client.put(
            f"/assets/{personal['id']}",
            headers=headers,
            json={"revision": personal["revision"], "name": "项目计划", "visibility": "project"},
        ).json()
        self.assertEqual(project_scoped["placement"], "project")
        self.assertEqual(project_scoped["project_id"], second["id"])
        only_second = self.client.get(
            f"/projects/{first}/assets?kind=project_plan", headers=headers,
        ).json()["assets"]
        self.assertNotIn(personal["id"], [item["id"] for item in only_second])
        still_second = self.client.get(
            f"/projects/{second['id']}/assets?kind=project_plan", headers=headers,
        ).json()["assets"]
        self.assertIn(personal["id"], [item["id"] for item in still_second])

    def test_user_tools_stay_visible_across_projects(self):
        data = self.auth("a@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        first = data["user"]["last_project_id"]
        calendar_id = data["user"]["last_assets"]["calendar"]
        second = self.client.post("/projects", headers=token, json={"name": "二号项目"}).json()
        listed = self.client.get(f"/projects/{second['id']}/assets?kind=calendar", headers=token).json()["assets"]
        self.assertEqual([item["id"] for item in listed], [calendar_id])
        notebook_id = self.client.get(f"/projects/{first}/assets?kind=notebook", headers=token).json()["assets"][0]["id"]
        notebooks = self.client.get(f"/projects/{second['id']}/assets?kind=notebook", headers=token).json()["assets"]
        self.assertEqual([item["id"] for item in notebooks], [notebook_id])
        again = self.client.post(
            f"/projects/{second['id']}/assets", headers=token, json={"kind": "calendar", "name": "另一份"},
        ).json()
        self.assertEqual(again["id"], calendar_id)
        sheets = self.client.get(f"/projects/{second['id']}/assets?kind=sheet", headers=token).json()["assets"]
        self.assertNotIn(data["user"]["last_assets"]["sheet"], [item["id"] for item in sheets])
        self.client.delete(f"/projects/{first}", headers=token)
        kept = self.client.get(f"/assets/{calendar_id}", headers=token)
        self.assertEqual(kept.status_code, 200)
        self.assertIsNone(kept.json()["project_id"])
        kept_notebook = self.client.get(f"/assets/{notebook_id}", headers=token)
        self.assertEqual(kept_notebook.status_code, 200)
        self.assertIsNone(kept_notebook.json()["project_id"])

    def test_project_plan_is_project_scoped_and_saves_normally(self):
        data = self.auth("plan@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        first = data["user"]["last_project_id"]
        created = self.client.post(
            f"/projects/{first}/assets", headers=token,
            json={"kind": "project_plan", "name": "季度投研计划"},
        ).json()
        self.assertEqual(created["kind"], "project_plan")
        self.assertEqual(created["placement"], "project")
        self.assertEqual(created["project_id"], first)
        self.assertEqual(created["content"], {"format": "invp-project-plan-v1", "tasks": []})
        listed = self.client.get(f"/projects/{first}/assets?kind=project_plan", headers=token).json()["assets"]
        self.assertIn(created["id"], [item["id"] for item in listed])
        saved = self.client.put(
            f"/assets/{created['id']}", headers=token,
            json={"revision": created["revision"], "content": {"format": "invp-project-plan-v1", "tasks": [{"id": "t1"}]}},
        ).json()
        self.assertEqual(saved["revision"], created["revision"] + 1)
        self.assertEqual(saved["content"]["tasks"][0]["id"], "t1")
        second = self.client.post("/projects", headers=token, json={"name": "另一个项目"}).json()
        elsewhere = self.client.get(f"/projects/{second['id']}/assets?kind=project_plan", headers=token).json()["assets"]
        self.assertNotIn(created["id"], [item["id"] for item in elsewhere])

        # 历史脏数据也不能把项目计划变成跨项目资产。
        self.store._assets[created["id"]]["project_id"] = None
        elsewhere = self.client.get(f"/projects/{second['id']}/assets?kind=project_plan", headers=token).json()["assets"]
        self.assertNotIn(created["id"], [item["id"] for item in elsewhere])

    def test_user_cannot_read_foreign_project_or_asset(self):
        a = self.auth("a@example.com")
        b = self.auth("b@example.com")
        project_id = a["user"]["last_project_id"]
        asset_id = a["user"]["last_assets"]["sheet"]
        headers = {"Authorization": f"Bearer {b['token']}"}
        self.assertEqual(self.client.get(f"/projects/{project_id}/assets", headers=headers).status_code, 404)
        self.assertEqual(self.client.get(f"/assets/{asset_id}", headers=headers).status_code, 404)
        self.assertEqual(self.client.put(f"/assets/{asset_id}", headers=headers, json={"content": {"x": 1}}).status_code, 404)
        self.assertEqual(self.client.post(f"/projects/{project_id}/select", headers=headers).status_code, 404)

    def test_select_updates_last_project_and_asset(self):
        data = self.auth("a@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        created = self.client.post("/projects", headers=token, json={"name": "二号项目"}).json()
        selected = self.client.post(f"/projects/{created['id']}/select", headers=token).json()
        self.assertEqual(selected["last_project_id"], created["id"])
        assets = self.client.get(f"/projects/{created['id']}/assets?kind=sheet", headers=token).json()["assets"]
        extra = self.client.post(f"/projects/{created['id']}/assets", headers=token,
                                 json={"kind": "sheet", "name": "第二张表"}).json()
        after = self.client.post(f"/assets/{extra['id']}/select", headers=token).json()
        self.assertEqual(after["last_assets"]["sheet"], extra["id"])
        me = self.client.get("/auth/me", headers=token).json()
        self.assertEqual(me["last_assets"]["sheet"], extra["id"])
        self.assertEqual(len(assets) + 1, 2)

    def test_recent_assets_follow_opening_order_and_include_project_name(self):
        data = self.auth("recent@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        project_id = data["user"]["last_project_id"]
        first = self.client.post(
            f"/projects/{project_id}/assets", headers=token,
            json={"kind": "sheet", "name": "第一张表"},
        ).json()
        second = self.client.post(
            f"/projects/{project_id}/assets", headers=token,
            json={"kind": "document", "name": "研究笔记"},
        ).json()
        self.client.post(f"/assets/{first['id']}/select", headers=token)
        recent = self.client.get("/assets/recent", headers=token).json()["assets"]
        self.assertEqual([item["id"] for item in recent[:2]], [first["id"], second["id"]])
        project = self.client.get("/projects", headers=token).json()["projects"][0]
        self.assertEqual(recent[0]["project_name"], project["name"])

        self.client.post(f"/assets/{second['id']}/select?track_recent=false", headers=token)
        unchanged = self.client.get("/assets/recent", headers=token).json()["assets"]
        self.assertEqual([item["id"] for item in unchanged[:2]], [first["id"], second["id"]])

        other_project = self.client.post("/projects", headers=token, json={"name": "另一项目"}).json()
        other = self.client.post(
            f"/projects/{other_project['id']}/assets", headers=token,
            json={"kind": "pdf", "name": "另一项目 PDF"},
        ).json()
        first_project_recent = self.client.get(
            f"/assets/recent?project_id={project_id}", headers=token,
        ).json()["assets"]
        other_project_recent = self.client.get(
            f"/assets/recent?project_id={other_project['id']}", headers=token,
        ).json()["assets"]
        self.assertNotIn(other["id"], [item["id"] for item in first_project_recent])
        self.assertEqual(other_project_recent[0]["id"], other["id"])

    def test_recent_assets_can_be_manually_reordered(self):
        data = self.auth("recent-order@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        project_id = data["user"]["last_project_id"]
        first = self.client.post(
            f"/projects/{project_id}/assets", headers=token,
            json={"kind": "sheet", "name": "估值表"},
        ).json()
        second = self.client.post(
            f"/projects/{project_id}/assets", headers=token,
            json={"kind": "document", "name": "研究笔记"},
        ).json()
        self.client.post(f"/assets/{first['id']}/select", headers=token)
        recent = self.client.get(f"/assets/recent?project_id={project_id}", headers=token).json()["assets"]
        ordered_ids = [item["id"] for item in recent]
        reordered_ids = list(reversed(ordered_ids))

        response = self.client.put(
            "/assets/recent", headers=token,
            json={"project_id": project_id, "asset_ids": reordered_ids},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["assets"]], reordered_ids)
        persisted = self.client.get(f"/assets/recent?project_id={project_id}", headers=token).json()["assets"]
        self.assertEqual([item["id"] for item in persisted], reordered_ids)

        invalid = self.client.put(
            "/assets/recent", headers=token,
            json={"project_id": project_id, "asset_ids": reordered_ids[:-1]},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_save_revision_conflict(self):
        data = self.auth("a@example.com")
        token = {"Authorization": f"Bearer {data['token']}"}
        asset_id = data["user"]["last_assets"]["document"]
        first = self.client.put(f"/assets/{asset_id}", headers=token, json={"content": {"type": "doc", "content": []}, "revision": 0})
        self.assertEqual(first.status_code, 200)
        conflict = self.client.put(f"/assets/{asset_id}", headers=token, json={"content": {"type": "doc"}, "revision": 0})
        self.assertEqual(conflict.status_code, 409)

    def test_wrong_password(self):
        self.auth("a@example.com")
        res = self.client.post("/auth/login", json={"email": "a@example.com", "password": "wrong12"})
        self.assertEqual(res.status_code, 401)

    def test_change_password_requires_current_password_and_updates_login(self):
        data = self.auth("a@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        wrong = self.client.put("/auth/password", headers=headers, json={"current_password": "wrong12", "new_password": "updated12"})
        self.assertEqual(wrong.status_code, 400)
        changed = self.client.put("/auth/password", headers=headers, json={"current_password": "secret1", "new_password": "updated12"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"email": "a@example.com", "password": "secret1"}).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"email": "a@example.com", "password": "updated12"}).status_code, 200)

    def test_user_model_config_encrypts_key_and_never_returns_plaintext(self):
        key = base64.urlsafe_b64encode(b"x" * 32).decode("ascii")
        with patch.dict(os.environ, {
            "LLM_CONFIG_SET": "1",
            "OFFICE_MODEL_CREDENTIALS_KEY": key,
        }, clear=False):
            data = self.auth("model@example.com")
            headers = {"Authorization": f"Bearer {data['token']}"}
            secret = "sk-user-secret-1234"
            saved = self.client.put("/auth/model-config", headers=headers, json={
                "provider": "deepseek",
                "model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": secret,
            })
            self.assertEqual(saved.status_code, 200)
            self.assertNotIn(secret, saved.text)
            row = self.store.get_user_model_config(data["user"]["id"])
            self.assertIsNotNone(row)
            self.assertNotIn("api_key", row)
            self.assertNotIn(secret, json.dumps(row))
            self.assertTrue(row["api_key_ciphertext"])
            self.assertTrue(row["api_key_nonce"])
            shown = self.client.get("/auth/model-config", headers=headers)
            self.assertEqual(shown.status_code, 200)
            self.assertNotIn(secret, shown.text)
            self.assertEqual(shown.json()["key_last4"], "1234")
            self.assertTrue(shown.json()["active_configured"])
            deleted = self.client.delete("/auth/model-config", headers=headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["deleted"])

    def test_user_model_config_requires_encryption_key(self):
        with patch.dict(os.environ, {
            "LLM_CONFIG_SET": "1",
            "OFFICE_MODEL_CREDENTIALS_KEY": "",
        }, clear=False):
            data = self.auth("no-key@example.com")
            headers = {"Authorization": f"Bearer {data['token']}"}
            response = self.client.put("/auth/model-config", headers=headers, json={
                "provider": "deepseek", "model": "deepseek-flash",
                "base_url": "https://api.deepseek.com", "api_key": "sk-no-store",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIn("OFFICE_MODEL_CREDENTIALS_KEY", response.json()["error"])

    def test_register_short_password_is_readable(self):
        res = self.client.post("/auth/register", json={"email": "a@example.com", "password": "123"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"], "密码至少 6 位。")

    def test_assistant_status_requires_login(self):
        self.assertEqual(self.client.get("/assistant/status").status_code, 401)

    def test_assistant_status_reports_model_name(self):
        data = self.auth("a@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        with patch("server.model_name", return_value="deepseek-flash"), patch("server.model_configured", return_value=True), patch("server.model_api_key", return_value="sk-test"):
            res = self.client.get("/assistant/status", headers=headers).json()
        self.assertTrue(res["configured"])
        self.assertTrue(res["has_api_key"])
        self.assertEqual(res["model_name"], "deepseek-flash")

    def test_assistant_run_without_model_is_unavailable(self):
        data = self.auth("a@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        with patch("server.model_configured", return_value=False):
            res = self.client.post("/assistant/run", headers=headers, json={"context": SHEET_CONTEXT, "intent": "清理空白"})
        self.assertEqual(res.status_code, 503)
        self.assertTrue(res.json()["error"])

    def test_assistant_run_streams_answer_and_patch(self):
        data = self.auth("a@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        payload = json.dumps({"answer": "已清理空白。", "replacement": [["hi"]]}, ensure_ascii=False)

        async def tokens(_messages, json_object=True):
            yield payload[:6]
            yield payload[6:]

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": SHEET_CONTEXT, "intent": "清理空白"}) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("已清理空白", body)
        self.assertIn('"type": "done"', body)
        self.assertIn('"replacement"', body)

    def test_visible_json_string_partial(self):
        self.assertEqual(visible_json_string('{"answer": "你好'), "你好")
        self.assertEqual(visible_json_string('{"answer": "你好\\n世界"}'), "你好\n世界")

    def test_registry_routes_by_surface_and_task(self):
        name, prompt = registry("sheet", "explain")
        self.assertEqual(name, "spreadsheet-analyst")
        self.assertIn("选区解释", prompt)
        name, prompt = registry("document", "generate")
        self.assertEqual(name, "document-writer")
        self.assertIn("生成内容", prompt)
        name, prompt = registry("research", "generate")
        self.assertEqual(name, "research-diagram-writer")
        self.assertIn("invp-research-diagram-v1", prompt)
        with self.assertRaisesRegex(ValueError, "PDF 仅支持选区解释"):
            registry("pdf", "generate")

    def test_mindmap_patch_supports_tree_operations(self):
        ctx = OfficeContext.model_validate(MINDMAP_CONTEXT)
        patch = create_patch(ctx, None, "explain", mindmap_ops=[
            {"op": "add", "parent_id": "root", "topic": "公司"},
            {"op": "move", "id": "n1", "parent_id": "root", "index": 0},
            {"op": "update", "id": "n1", "topic": "行业研究"},
        ])
        self.assertEqual(len(patch["mindmap_ops"]), 3)
        self.assertTrue(validate_office_patch(patch, MINDMAP_CONTEXT)["valid"])

    def test_mindmap_session_crud(self):
        data = self.auth("mindmap-session@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        project = data["user"]["last_project_id"]
        mindmap = self.client.get(f"/projects/{project}/assets?kind=mindmap", headers=headers).json()["assets"][0]
        asset_id = mindmap["id"]
        created = self.client.post(f"/assets/{asset_id}/assistant-sessions", headers=headers)
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session"]["id"]
        saved = self.client.put(
            f"/assets/{asset_id}/assistant-sessions/{session_id}", headers=headers,
            json={"anchor": MINDMAP_CONTEXT, "turns": [{"role": "user", "content": "整理这份导图"}]},
        )
        self.assertEqual(saved.status_code, 200)
        deleted = self.client.delete(f"/assets/{asset_id}/assistant-sessions/{session_id}", headers=headers)
        self.assertEqual(deleted.status_code, 200)

    def test_explain_empty_selection_is_rejected(self):
        data = self.auth("empty@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        empty = {
            **SHEET_CONTEXT,
            "selection": {**SHEET_CONTEXT["selection"], "values": [[""]]},
        }
        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": empty, "intent": "解释一下", "task": "explain"}) as res:
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("未选择任务内容", body)

    def test_explain_whole_sheet_surrounding_ok(self):
        data = self.auth("sheet-all@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        whole_sheet = {
            **SHEET_CONTEXT,
            "selection": {
                **SHEET_CONTEXT["selection"],
                "values": [[None]],
                "address": "",
            },
            "surrounding": json.dumps({
                "scope": "sheet",
                "sheet_id": "s1",
                "values": [[1, 2], [3, 4]],
            }, ensure_ascii=False),
        }
        payload = json.dumps({"answer": "整表合计为 10。", "replacement": None}, ensure_ascii=False)

        async def tokens(_messages, json_object=True):
            yield payload

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": whole_sheet, "intent": "求和", "task": "explain"}) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("整表合计为 10", body)
        self.assertIn('"type": "done"', body)

    def test_explain_follow_up_with_history(self):
        data = self.auth("sheet-hist@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        anchor = {
            **SHEET_CONTEXT,
            "selection": {
                **SHEET_CONTEXT["selection"],
                "values": [[None]],
                "address": "",
            },
            "surrounding": json.dumps({"scope": "sheet", "values": [[5, 6]]}, ensure_ascii=False),
        }
        payload = json.dumps({"answer": "环比上升。", "replacement": None}, ensure_ascii=False)

        async def tokens(_messages, json_object=True):
            yield payload

        history = [
            {"role": "user", "content": "均值多少？"},
            {"role": "assistant", "content": "均值为 5.5。"},
        ]
        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": anchor, "intent": "趋势如何？", "task": "explain", "history": history}) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("环比上升", body)

    def test_registry_includes_spreadsheet_chat_skill(self):
        _, prompt = registry("sheet", "explain", chat_mode=True)
        self.assertIn("spreadsheet-chat", prompt)
        self.assertIn("表含义", prompt)
        self.assertIn("只给出执行结果", prompt)
        self.assertNotIn("Stream a JSON object", prompt)

    def test_sheet_write_aims_at_named_range(self):
        ctx = OfficeContext.model_validate({
            **SHEET_CONTEXT,
            "selection": {**SHEET_CONTEXT["selection"], "address": "", "values": [[None]]},
        })
        matrix = [[120.5, "12%", "NM"], [80, "3%", "NM"], [0, "0%", "NM"]]
        aimed = aim_sheet_write(ctx, {
            "answer": "已生成写入 B13:D15（3 行 × 3 列）的替换矩阵，列为 全年金额/Q4同比/NM。",
            "replacement": matrix,
        })
        patch = create_patch(aimed, matrix, "explain")
        self.assertEqual(patch["selection"]["row"], 12)
        self.assertEqual(patch["selection"]["column"], 1)
        self.assertEqual((patch["selection"]["rows"], patch["selection"]["cols"]), (3, 3))
        self.assertEqual(patch["selection"]["address"], "B13:D15")
        live = OfficeContext.model_validate(SHEET_CONTEXT)
        self.assertEqual(validate_office_patch(patch, live.model_dump())["valid"], True)

    def test_answer_directive_is_brief_unless_analysis(self):
        self.assertIn("只给出执行结果", answer_directive("合计多少"))
        self.assertIn("只给出执行结果", answer_directive("按地区汇总并筛选大于 100 的行"))
        self.assertIn("详细作答", answer_directive("请分析收入变化"))
        self.assertIn("详细作答", answer_directive("讨论并预测下季"))
        self.assertIn("详细作答", answer_directive("推测下滑原因"))
        self.assertIn('"address"', answer_directive("写入 B13:I15"))
        self.assertIn("format_ops", answer_directive("把金额列改为财务数值类型，环比和同比列改为百分数格式保留两位"))
        self.assertIn("0.00%", answer_directive("环比列改成百分数格式"))

    def test_sheet_format_ops_patch(self):
        ctx = OfficeContext.model_validate(SHEET_CONTEXT)
        patch = create_patch(
            ctx,
            None,
            "explain",
            format_ops=[
                {"address": "C2:C4", "number_format": "accounting"},
                {"address": "D2:E4", "number_format": "百分数"},
            ],
        )
        self.assertIsNone(patch["replacement"])
        self.assertEqual(patch["format_ops"][0]["number_format"], "#,##0.00")
        self.assertEqual(patch["format_ops"][1]["number_format"], "0.00%")
        self.assertEqual(patch["format_ops"][1]["address"], "D2:E4")
        self.assertEqual(validate_office_patch(patch, ctx.model_dump())["valid"], True)

    def test_sheet_format_ops_merge_adjacent_columns(self):
        ctx = OfficeContext.model_validate(SHEET_CONTEXT)
        patch = create_patch(
            ctx,
            None,
            "explain",
            format_ops=[
                {"address": "B2:B10", "number_format": "accounting"},
                {"address": "C2:C10", "number_format": "accounting"},
                {"address": "D2:D10", "number_format": "accounting"},
                {"address": "E2:E10", "number_format": "0.00%"},
                {"address": "F2:F10", "number_format": "百分数"},
            ],
        )
        self.assertEqual(len(patch["format_ops"]), 2)
        self.assertEqual(patch["format_ops"][0]["address"], "B2:D10")
        self.assertEqual(patch["format_ops"][0]["number_format"], "#,##0.00")
        self.assertEqual(patch["format_ops"][1]["address"], "E2:F10")
        self.assertEqual(patch["format_ops"][1]["number_format"], "0.00%")

    def test_parse_model_result_recovers_malformed_json(self):
        recovered = parse_model_result('{"answer": "第一点\n第二点', fallback="第一点\n第二点", require_json=False)
        self.assertEqual(recovered["answer"], "第一点\n第二点")
        markdown = parse_model_result("合计为 **10**。", fallback="", require_json=False)
        self.assertEqual(markdown["answer"], "合计为 **10**。")

    def test_strip_thinking_keeps_final_answer(self):
        self.assertEqual(strip_thinking("<think>逐步分析</think>\n合计为 10。"), "合计为 10。")
        self.assertEqual(
            strip_thinking("## 思考过程\n\n很长的推理\n\n## 回答\n\n结论是 3。"),
            "结论是 3。",
        )

    def test_reasoning_streams_apart_from_answer(self):
        data = self.auth("think@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}

        async def tokens(_messages, json_object=True):
            yield {"part": "reasoning", "text": "先看金额列。"}
            yield "合计为 10。"

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers, json={
                "context": SHEET_CONTEXT, "intent": "合计多少", "task": "explain",
            }) as res:
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn('"part": "reasoning"', body)
        self.assertIn("先看金额列", body)
        self.assertIn("合计为 10", body)
        done = body.rsplit("data: ", 1)[-1]
        self.assertNotIn("先看金额列", done)

    def test_explain_plain_markdown_chat_ok(self):
        data = self.auth("sheet-md@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        whole_sheet = {
            **SHEET_CONTEXT,
            "selection": {
                **SHEET_CONTEXT["selection"],
                "values": [[None]],
                "address": "",
            },
            "surrounding": json.dumps({"scope": "sheet", "values": [[1, 2]]}, ensure_ascii=False),
        }

        async def tokens(_messages, json_object=True):
            yield "## 合计\n"
            yield "结果是 3。"

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": whole_sheet, "intent": "求和", "task": "explain"}) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("结果是 3", body)
        self.assertIn('"type": "done"', body)
        self.assertNotIn("没有返回可解析的 JSON", body)

    def test_session_save_clips_oversized_turn(self):
        data = self.auth("sess-long@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        asset_id = data["user"]["last_assets"]["sheet"]
        created = self.client.post(f"/assets/{asset_id}/assistant-sessions", headers=headers).json()
        session_id = created["session"]["id"]
        huge = "x" * 30000
        res = self.client.put(
            f"/assets/{asset_id}/assistant-sessions/{session_id}",
            headers=headers,
            json={
                "turns": [
                    {"role": "user", "content": "分析这张表"},
                    {"role": "assistant", "content": huge},
                ],
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        saved = res.json()["session"]["turns"][1]["content"]
        self.assertLessEqual(len(saved), 24000)

    def test_sheet_cache_profile_and_intent_slices(self):
        data = self.auth("sheet-cache@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        asset_id = data["user"]["last_assets"]["sheet"]
        values = [["指标", "金额"]] + [[f"项{i}", i] for i in range(8)]
        created = self.client.put(f"/assets/{asset_id}/sheet-cache", headers=headers, json={
            "sheet_id": "s1",
            "sheet_name": "经营",
            "revision": 2,
            "origin": {"row": 0, "col": 0},
            "rows": len(values),
            "cols": 2,
            "range": "A1:B9",
            "values": values,
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertFalse(created.json()["has_profile"])

        async def fake_profile(cache):
            return "B列是金额。A列是指标。数据从第2行开始。", structure_fingerprint(cache)

        with patch("server.model_configured", return_value=True), patch("server.generate_sheet_profile", fake_profile):
            prof = self.client.post(f"/assets/{asset_id}/sheet-cache/profile", headers=headers, json={"sheet_id": "s1"})
        self.assertEqual(prof.status_code, 200, prof.text)
        self.assertTrue(prof.json()["has_profile"])

        seen: dict[str, str] = {}

        async def tokens(messages, json_object=True):
            seen["text"] = messages[-1]["content"]
            yield "已回答。"

        context = {
            **SHEET_CONTEXT,
            "document_id": asset_id,
            "surrounding": "",
            "selection": {**SHEET_CONTEXT["selection"], "sheet_id": "s1", "address": "B2", "values": [[999]]},
        }
        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers, json={
                "context": context, "intent": "合计多少", "task": "explain", "use_sheet_cache": True,
            }) as res:
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("已回答", body)
        self.assertIn("B列是金额", seen["text"])
        self.assertIn("项7", seen["text"])
        self.assertIn("只给出执行结果", seen["text"])
        self.assertNotIn("999", seen["text"])

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers, json={
                "context": context, "intent": "看所选单元格", "task": "explain", "use_sheet_cache": True,
            }) as res:
                self.assertEqual(res.status_code, 200)
        self.assertIn("999", seen["text"])

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers, json={
                "context": context, "intent": "列出全部明细", "task": "explain", "use_sheet_cache": True,
            }) as res:
                self.assertEqual(res.status_code, 200)
        self.assertIn("项7", seen["text"])

        patched = self.client.patch(f"/assets/{asset_id}/sheet-cache", headers=headers, json={
            "sheet_id": "s1",
            "revision": 3,
            "cells": [{"row": 3, "column": 1, "value": 42}],
        })
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertTrue(patched.json()["has_profile"])

        header_changed = self.client.patch(f"/assets/{asset_id}/sheet-cache", headers=headers, json={
            "sheet_id": "s1",
            "revision": 4,
            "cells": [{"row": 0, "column": 0, "value": "科目"}],
        })
        self.assertFalse(header_changed.json()["has_profile"])

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True):
            with self.client.stream("POST", "/assistant/run", headers=headers, json={
                "context": context, "intent": "合计多少", "task": "explain", "use_sheet_cache": True,
            }) as res:
                missing = b"".join(res.iter_bytes()).decode()
        self.assertIn("请先刷新表结构", missing)

    def test_fit_chat_budget_drops_old_turns(self):
        ctx = OfficeContext.model_validate({
            **SHEET_CONTEXT,
            "surrounding": "x" * 90_000,
        })
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}-" + ("y" * 5000)}
                     for i in range(12)]
        fitted, kept, meta = _fit_chat_budget(ctx, history, "最新问题")
        self.assertLess(len(kept), len(history))
        self.assertGreater(meta["dropped_turns"], 0)

    def test_assistant_sessions_crud(self):
        data = self.auth("sess@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        asset_id = data["user"]["last_assets"]["sheet"]
        created = self.client.post(f"/assets/{asset_id}/assistant-sessions", headers=headers).json()
        session_id = created["session"]["id"]
        self.assertTrue(session_id)
        listed = self.client.get(f"/assets/{asset_id}/assistant-sessions", headers=headers).json()
        self.assertEqual(listed["sessions"][0]["id"], session_id)
        anchor = {
            **SHEET_CONTEXT,
            "surrounding": json.dumps({"scope": "sheet", "values": [[1]]}, ensure_ascii=False),
        }
        updated = self.client.put(
            f"/assets/{asset_id}/assistant-sessions/{session_id}",
            headers=headers,
            json={
                "anchor": anchor,
                "turns": [
                    {"role": "user", "content": "合计多少？"},
                    {"role": "assistant", "content": "合计为 1。"},
                ],
            },
        ).json()
        self.assertEqual(updated["session"]["title"], "合计多少？")
        renamed = self.client.put(
            f"/assets/{asset_id}/assistant-sessions/{session_id}",
            headers=headers,
            json={"title": "一季度分析"},
        ).json()
        self.assertEqual(renamed["session"]["title"], "一季度分析")
        kept = self.client.put(
            f"/assets/{asset_id}/assistant-sessions/{session_id}",
            headers=headers,
            json={
                "turns": [
                    {"role": "user", "content": "合计多少？"},
                    {"role": "assistant", "content": "合计为 1。"},
                    {"role": "user", "content": "再看趋势"},
                    {"role": "assistant", "content": "上升。"},
                ],
            },
        ).json()
        self.assertEqual(kept["session"]["title"], "一季度分析")
        loaded = self.client.get(f"/assets/{asset_id}/assistant-sessions/{session_id}", headers=headers).json()
        self.assertEqual(len(loaded["session"]["turns"]), 4)
        deleted = self.client.delete(f"/assets/{asset_id}/assistant-sessions/{session_id}", headers=headers).json()
        self.assertTrue(deleted["deleted"])
        missing = self.client.get(f"/assets/{asset_id}/assistant-sessions/{session_id}", headers=headers)
        self.assertEqual(missing.status_code, 404)

    def test_session_history_run_ok(self):
        data = self.auth("sess-run@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        asset_id = data["user"]["last_assets"]["sheet"]
        anchor = {
            **SHEET_CONTEXT,
            "surrounding": json.dumps({"scope": "sheet", "values": [[3, 4]]}, ensure_ascii=False),
        }
        payload = json.dumps({"answer": "继续分析。", "replacement": None}, ensure_ascii=False)

        async def tokens(_messages, json_object=True):
            yield payload

        history = [
            {"role": "user", "content": "求和"},
            {"role": "assistant", "content": "和为 7。"},
        ]
        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream(
                "POST", "/assistant/run", headers=headers,
                json={"context": anchor, "intent": "再解释一下", "task": "explain", "history": history},
            ) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("继续分析", body)

    def test_generate_pdf_is_rejected(self):
        data = self.auth("pdf-gen@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        pdf_context = {
            "document_id": "pdf-1",
            "surface": "pdf",
            "revision": 0,
            "selection": {"page": 1, "filename": "a.pdf", "text": "Revenue"},
            "surrounding": "",
        }
        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": pdf_context, "intent": "写一段", "task": "generate"}) as res:
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("PDF 仅支持选区解释", body)

    def test_generate_streams_insert_patch(self):
        data = self.auth("gen@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        cursor = {
            "document_id": "doc-1",
            "surface": "document",
            "revision": 0,
            "selection": {"from": 1, "to": 1, "text": "", "single_block": True},
            "surrounding": "",
        }
        payload = json.dumps({"answer": "已起草。", "replacement": "新段落"}, ensure_ascii=False)

        async def tokens(_messages, json_object=True):
            yield payload

        with patch("server.model_configured", return_value=True), patch("assistant.model_configured", return_value=True), \
             patch("assistant.stream_model_tokens", tokens):
            with self.client.stream("POST", "/assistant/run", headers=headers,
                                    json={"context": cursor, "intent": "写一句开场", "task": "generate"}) as res:
                self.assertEqual(res.status_code, 200)
                body = b"".join(res.iter_bytes()).decode()
        self.assertIn("已起草", body)
        self.assertIn("新段落", body)
        self.assertIn('"task": "generate"', body)

    def test_user_access_token_binds_own_workspace(self):
        owner = self.auth("owner@example.com")
        jwt = {"Authorization": f"Bearer {owner['token']}"}
        token = self.client.get("/auth/access-token", headers=jwt)
        self.assertEqual(token.status_code, 200)
        access = token.json()["access_token"]
        self.assertEqual(len(access), 32)
        self.assertNotIn("access_token", owner["user"])
        mcp = {"Authorization": f"Bearer {access}"}
        me = self.client.get("/auth/me", headers=mcp)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], "owner@example.com")
        self.client.post("/projects", headers=mcp, json={"name": "年度预算"})
        names = [item["name"] for item in self.client.get("/projects", headers=jwt).json()["projects"]]
        self.assertIn("年度预算", names)

    def test_user_access_token_cannot_see_other_workspace(self):
        first = self.auth("first@example.com")
        second = self.auth("second@example.com")
        self.client.post("/projects", headers={"Authorization": f"Bearer {second['token']}"}, json={"name": "只要这份"})
        first_token = self.client.get("/auth/access-token", headers={"Authorization": f"Bearer {first['token']}"}).json()["access_token"]
        names = [item["name"] for item in self.client.get("/projects", headers={"Authorization": f"Bearer {first_token}"}).json()["projects"]]
        self.assertNotIn("只要这份", names)
        me = self.client.get("/auth/me", headers={"Authorization": f"Bearer {first_token}"}).json()
        self.assertEqual(me["email"], "first@example.com")

    def test_rotate_access_token_invalidates_old_and_updates_skill(self):
        data = self.auth("owner@example.com")
        jwt = {"Authorization": f"Bearer {data['token']}"}
        old = self.client.get("/auth/access-token", headers=jwt).json()["access_token"]
        skill = self.client.get("/auth/skills/office", headers=jwt)
        self.assertEqual(skill.status_code, 200)
        self.assertIn(old, skill.text)
        self.assertNotIn("__ACCESS_TOKEN__", skill.text)
        rotated = self.client.post("/auth/access-token/rotate", headers=jwt)
        self.assertEqual(rotated.status_code, 200)
        new = rotated.json()["access_token"]
        self.assertEqual(len(new), 32)
        self.assertNotEqual(new, old)
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": f"Bearer {new}"}).json()["email"], "owner@example.com")
        updated = self.client.get("/auth/skills/office", headers=jwt)
        self.assertIn(new, updated.text)
        self.assertNotIn(old, updated.text)

    def test_office_skill_is_downloadable_with_user_token(self):
        data = self.auth("owner@example.com")
        jwt = {"Authorization": f"Bearer {data['token']}"}
        catalog = self.client.get("/auth/skills", headers=jwt).json()["skills"]
        self.assertEqual([item["id"] for item in catalog], ["office"])
        token = self.client.get("/auth/access-token", headers=jwt).json()["access_token"]
        skill = self.client.get("/auth/skills/office", headers=jwt)
        self.assertEqual(skill.status_code, 200)
        self.assertIn(token, skill.text)
        self.assertNotIn("__ACCESS_TOKEN__", skill.text)
        self.assertIn("/mcp/sheets", skill.text)
        self.assertIn("/api/spreadsheets/read", skill.text)
        self.assertIn("/mcp/documents", skill.text)
        self.assertIn("generate_pdf_outline", skill.text)
        self.assertNotIn("announcement-pdf/SKILL.md", skill.text)

    def test_rotate_token_keeps_projects_on_login_account(self):
        data = self.auth("owner@example.com")
        jwt = {"Authorization": f"Bearer {data['token']}"}
        old = self.client.get("/auth/access-token", headers=jwt).json()["access_token"]
        mcp = {"Authorization": f"Bearer {old}"}
        created = self.client.post("/projects", headers=mcp, json={"name": "腾讯跟踪"})
        self.assertEqual(created.status_code, 200)
        rotated = self.client.post("/auth/access-token/rotate", headers=jwt).json()["access_token"]
        new_mcp = {"Authorization": f"Bearer {rotated}"}
        names_mcp = [item["name"] for item in self.client.get("/projects", headers=new_mcp).json()["projects"]]
        names_jwt = [item["name"] for item in self.client.get("/projects", headers=jwt).json()["projects"]]
        self.assertIn("腾讯跟踪", names_mcp)
        self.assertEqual(sorted(names_mcp), sorted(names_jwt))
        self.assertEqual(self.client.get("/projects", headers=mcp).status_code, 401)
        me = self.client.get("/auth/me", headers=new_mcp).json()
        self.assertEqual(me["email"], "owner@example.com")

    def test_shared_static_token_is_not_a_workspace_user(self):
        self.auth("owner@example.com")
        mcp = {"Authorization": "Bearer invpdevstatic32tokenpleasechange"}
        self.assertEqual(self.client.get("/auth/me", headers=mcp).status_code, 401)

    def test_pdf_file_can_be_replaced(self):
        data = self.auth("pdf-save@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        user = data["user"]
        created = self.client.post(f"/projects/{user['last_project_id']}/assets",
                                   headers=headers, json={"kind": "pdf", "name": "年报"}).json()
        payload = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
        first = self.client.put(f"/assets/{created['id']}/file", headers=headers,
                                files={"file": ("n.pdf", payload, "application/pdf")})
        self.assertEqual(first.status_code, 200, first.text)
        rev = first.json()["revision"]
        second = self.client.put(f"/assets/{created['id']}/file", headers=headers,
                                 files={"file": ("n.pdf", payload + b"%annot\n", "application/pdf")})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertGreater(second.json()["revision"], rev)
        downloaded = self.client.get(f"/assets/{created['id']}/file", headers=headers)
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.content.startswith(b"%PDF"))

    def test_pdf_notebook_is_saved_with_pdf_asset(self):
        data = self.auth("pdf-notebook@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        created = self.client.post(
            f"/projects/{data['user']['last_project_id']}/assets",
            headers=headers,
            json={"kind": "pdf", "name": "研报"},
        ).json()
        notebook = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "投资要点"}]}]}
        saved = self.client.put(
            f"/assets/{created['id']}",
            headers=headers,
            json={"revision": created["revision"], "content": {"notebook": notebook}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["content"], {"notebook": notebook})
        rejected = self.client.put(
            f"/assets/{created['id']}",
            headers=headers,
            json={"revision": saved.json()["revision"], "content": {"unexpected": "body"}},
        )
        self.assertEqual(rejected.status_code, 400)

    def _sample_pdf(self, text="Hello annotation sample"):
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), text)
        payload = doc.tobytes()
        doc.close()
        return payload

    def test_pdf_highlight_annotation_is_written(self):
        import pymupdf
        data = self.auth("pdf-annot@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        user = data["user"]
        created = self.client.post(f"/projects/{user['last_project_id']}/assets",
                                   headers=headers, json={"kind": "pdf", "name": "批注"}).json()
        uploaded = self.client.put(f"/assets/{created['id']}/file", headers=headers,
                                   files={"file": ("n.pdf", self._sample_pdf(), "application/pdf")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        res = self.client.post(f"/assets/{created['id']}/annotations", headers=headers, json={
            "kind": "highlight", "page": 1, "boxes": [[72, 120, 280, 140]],
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertGreater(res.json()["revision"], uploaded.json()["revision"])
        self.assertGreater(res.json()["annot"]["xref"], 0)
        saved = self.client.get(f"/assets/{created['id']}/file", headers=headers)
        self.assertEqual(saved.headers.get("cache-control"), "no-store")
        doc = pymupdf.open(stream=saved.content, filetype="pdf")
        try:
            found = None
            for annot in doc[0].annots():
                found = annot.type[0]
                break
            self.assertEqual(found, pymupdf.PDF_ANNOT_HIGHLIGHT)
        finally:
            doc.close()

    def test_pdf_text_annotation_requires_content(self):
        data = self.auth("pdf-note@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        user = data["user"]
        created = self.client.post(f"/projects/{user['last_project_id']}/assets",
                                   headers=headers, json={"kind": "pdf", "name": "批注"}).json()
        uploaded = self.client.put(f"/assets/{created['id']}/file", headers=headers,
                                   files={"file": ("n.pdf", self._sample_pdf(), "application/pdf")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        empty = self.client.post(f"/assets/{created['id']}/annotations", headers=headers, json={
            "kind": "text", "page": 1, "boxes": [[72, 120, 280, 140]], "content": "  ",
        })
        self.assertEqual(empty.status_code, 400)
        res = self.client.post(f"/assets/{created['id']}/annotations", headers=headers, json={
            "kind": "text", "page": 1, "boxes": [[72, 120, 280, 140]], "content": "请核对这段。",
        })
        self.assertEqual(res.status_code, 200, res.text)
        xref = res.json()["annot"]["xref"]
        self.assertGreater(xref, 0)
        edited = self.client.patch(f"/assets/{created['id']}/annotations/{xref}", headers=headers,
                                   json={"content": "已核对。"})
        self.assertEqual(edited.status_code, 200, edited.text)
        removed = self.client.delete(f"/assets/{created['id']}/annotations/{xref}", headers=headers)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertIn(xref, removed.json().get("deleted") or [])
        self.assertGreaterEqual(len(removed.json().get("deleted") or []), 2)
        gone = self.client.get(f"/assets/{created['id']}/file", headers=headers)
        import pymupdf
        empty = pymupdf.open(stream=gone.content, filetype="pdf")
        try:
            count = 0
            annots = empty[0].annots()
            if annots:
                for _ in annots:
                    count += 1
            self.assertEqual(count, 0)
        finally:
            empty.close()

    def test_portfolio_gateway_forwards_with_user_id(self):
        import server as server_module

        data = self.auth("holder@example.com")
        headers = {"Authorization": f"Bearer {data['token']}"}
        calls = []

        async def fake_forward(method, path, *, user_id, query="", body=b""):
            calls.append((method, path, user_id, query, body))
            return 200, {"portfolios": [{"id": 1, "name": "A"}]}

        with patch.object(server_module, "forward_portfolio", fake_forward):
            listed = self.client.get("/portfolio/portfolios?x=1", headers=headers)
            created = self.client.post("/portfolio/portfolios", headers=headers, json={"name": "A"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["portfolios"][0]["name"], "A")
        self.assertEqual(calls[0][:4], ("GET", "portfolios", data["user"]["id"], "x=1"))
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(json.loads(calls[1][4]), {"name": "A"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.client.get("/portfolio/portfolios").status_code, 401)

    def test_portfolio_gateway_only_allows_portfolio_paths(self):
        import portfolio_proxy
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            portfolio_proxy._target("documents/1")
        self.assertTrue(portfolio_proxy._target("portfolios/3/stats").endswith("/api/portfolio/portfolios/3/stats"))
        self.assertTrue(portfolio_proxy._target("overview/holdings").endswith("/api/portfolio/overview/holdings"))
        self.assertTrue(portfolio_proxy._target("scheduler/tasks").endswith("/api/scheduler/tasks"))


if __name__ == "__main__":
    unittest.main()
