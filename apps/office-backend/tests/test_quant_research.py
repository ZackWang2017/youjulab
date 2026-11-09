import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server import create_app
from store import MemoryStore
from quant_research.schema import default_strategy, parse_prices, strategy_content, validate_plan
from quant_research.engine import allocation_code, run_backtest
from quant_research.repository import RunRepository
from quant_research.service import ResearchService


CSV = "date,A,B\n2020-01-02,100,100\n2020-01-03,110,100\n2020-01-06,121,100\n2020-01-07,133.1,100\n"


class QuantSchemaTests(unittest.TestCase):
    def test_csv_rejects_missing_duplicate_or_invalid_prices(self):
        for text in [CSV.replace("121,100", ",100"), CSV.replace("2020-01-06", "2020-01-03"), CSV.replace("121,100", "NaN,100"), CSV.replace("date,A,B", "date,A,A")]:
            with self.assertRaises(ValueError):
                parse_prices(text)

    def test_data_basis_changes_snapshot_hash(self):
        base = default_strategy()
        base["dataset"] = {**parse_prices(CSV), "basis": "price", "source": "测试数据"}
        first = strategy_content(base)
        base["dataset"]["basis"] = "total_return"
        self.assertNotEqual(first["dataset"]["sha256"], strategy_content(base)["dataset"]["sha256"])

    def test_plan_disallows_unknown_tools_weights_and_fees(self):
        for params in [{"weights": {"A": 0.8}, "rebalance": "once"}, {"weights": {"UNKNOWN": 1}, "rebalance": "once"}, {"weights": {"A": 1}, "rebalance": "once", "fee_bps": -1}]:
            with self.assertRaises(ValueError):
                validate_plan({"tool": "bt_allocation", "summary": "测试", "code": "pass", "parameters": params}, ["A"])

    @unittest.skipUnless(importlib.util.find_spec("bt"), "bt not installed")
    def test_backtest_delays_execution_and_recomputes_metrics(self):
        data = {**parse_prices(CSV), "basis": "price", "source": "synthetic test fixture"}
        result = run_backtest(data, {"weights": {"A": 1}, "rebalance": "once", "fee_bps": 0})
        self.assertAlmostEqual(result["metrics"]["total_return"], 0.21, places=8)
        charged = run_backtest(data, {"weights": {"A": 1}, "rebalance": "once", "fee_bps": 10})
        self.assertLess(charged["metrics"]["total_return"], result["metrics"]["total_return"])

    def test_durable_runs_are_idempotent_and_keep_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = RunRepository(Path(directory) / "runs.sqlite")
            snapshot = {"revision": 1, "content": {"code": "original"}}
            run, created = repo.create("u", "a", "key", snapshot)
            self.assertTrue(created)
            snapshot["content"]["code"] = "changed"
            same, created = repo.create("u", "a", "key", snapshot)
            self.assertFalse(created)
            self.assertEqual(same["snapshot"]["content"]["code"], "original")
            repo.update(run["id"], status="cancelled")
            repo.update(run["id"], status="completed")
            self.assertEqual(repo.get(run["id"])["status"], "cancelled")
            repo.db.close()

    def test_runs_share_office_database_without_asset_updates_erasing_them(self):
        from sqlite_store import SqliteStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "office.sqlite"
            store = SqliteStore(path)
            user = store.create_user("persist-quant@example.com", "secret1")
            project = store.create_project(user["id"], "P")
            asset = store.create_asset(user["id"], project["id"], "strategy", "策略", default_strategy())
            repo = RunRepository(path)
            run, _ = repo.create(user["id"], asset["id"], "key", {"revision": 0, "content": {"code": "snapshot"}})
            store.update_asset(user["id"], asset["id"], name="新名称")
            self.assertEqual(repo.get(run["id"])["snapshot"]["content"]["code"], "snapshot")
            repo.update(run["id"], status="cancelled")
            store.close()
            repo.db.close()
            restored = RunRepository(path)
            self.assertEqual(restored.get(run["id"])["status"], "cancelled")
            restored.db.close()


class QuantApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        auth = self.client.post("/auth/register", json={"email": "quant@example.com", "password": "secret1"}).json()
        self.headers = {"Authorization": "Bearer " + auth["token"]}
        self.project = auth["user"]["last_project_id"]
        response = self.client.post(f"/projects/{self.project}/assets", headers=self.headers, json={"kind": "strategy", "name": "研究测试"})
        self.assertEqual(response.status_code, 200, response.text)
        self.asset = response.json()

    def import_data(self):
        response = self.client.put(f"/assets/{self.asset['id']}/strategy/data", headers=self.headers, json={"csv": CSV, "basis": "price", "source": "synthetic test fixture", "revision": self.asset["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.asset = response.json()

    def test_strategy_asset_scope_and_permission(self):
        self.assertEqual(self.asset["content"]["format"], "youju-strategy-v1")
        listed = self.client.get(f"/projects/{self.project}/assets?kind=strategy", headers=self.headers).json()["assets"]
        self.assertIn(self.asset["id"], [a["id"] for a in listed])
        auth = self.client.post("/auth/register", json={"email": "other-quant@example.com", "password": "secret1"}).json()
        headers = {"Authorization": "Bearer " + auth["token"]}
        self.assertEqual(self.client.get(f"/assets/{self.asset['id']}/strategy/runs", headers=headers).status_code, 404)
        self.assertEqual(self.client.delete(f"/assets/{self.asset['id']}", headers=self.headers).status_code, 200)

    def test_project_switch_does_not_keep_another_projects_strategy(self):
        other = self.client.post("/projects", headers=self.headers, json={"name": "另一个项目"}).json()
        selected = self.client.post(f"/projects/{other['id']}/select", headers=self.headers).json()
        self.assertIsNone(selected["last_assets"]["strategy"])
        restored = self.client.post(f"/projects/{self.project}/select", headers=self.headers).json()
        self.assertEqual(restored["last_assets"]["strategy"], self.asset["id"])

    def test_import_validates_revision_and_does_not_fabricate_data(self):
        response = self.client.post(f"/assets/{self.asset['id']}/strategy/runs", headers=self.headers, json={"revision": self.asset["revision"], "request_key": "first"})
        self.assertEqual(response.status_code, 400)
        self.import_data()
        self.assertEqual(self.asset["content"]["dataset"]["rows"][0][0], "2020-01-02")
        stale = self.client.put(f"/assets/{self.asset['id']}/strategy/data", headers=self.headers, json={"csv": CSV, "basis": "price", "source": "test", "revision": 0})
        self.assertEqual(stale.status_code, 409)

    def test_agent_selects_tool_and_saves_plan_without_executing_code(self):
        self.import_data()
        plan = {"tool": "bt_allocation", "summary": "A/B各50%，买入持有。", "code": "pass", "parameters": {"weights": {"A": .5, "B": .5}, "rebalance": "once", "fee_bps": 0}, "assumptions": ["价格收益口径"]}
        async def fake_model(*args, **kwargs):
            yield {"type": "delta", "part": "content", "text": json.dumps(plan)}
        with patch("quant_research.endpoints.model_configured", return_value=True), patch("quant_research.agent.iter_chat_events", fake_model):
            response = self.client.post(f"/assets/{self.asset['id']}/strategy/research", headers=self.headers, json={"instruction": "A/B各50%", "revision": self.asset["revision"]})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        done = [e for e in events if e["type"] == "done"][0]
        self.assertIn("bt.Strategy", done["asset"]["content"]["code"])
        self.assertEqual(done["asset"]["revision"], self.asset["revision"] + 1)
        self.assertEqual(self.client.get(f"/assets/{self.asset['id']}/strategy/runs", headers=self.headers).json()["runs"], [])

    def test_missing_sandbox_fails_honestly(self):
        self.import_data()
        content = self.asset["content"]
        content.update(code="def target_weights(history, parameters):\n    return {'A': 1}\n", plan={"tool": "python_research", "summary": "自定义研究", "code": "pass", "parameters": {}, "assumptions": []})
        saved = self.client.put(f"/assets/{self.asset['id']}", headers=self.headers, json={"content": content, "revision": self.asset["revision"]}).json()
        with patch.object(ResearchService, "start", lambda worker, run: worker.execute(run)), patch("quant_research.sandbox.available", return_value={"ready": False, "message": "自定义 Python 需要 Docker 沙箱"}):
            response = self.client.post(f"/assets/{self.asset['id']}/strategy/runs", headers=self.headers, json={"revision": saved["revision"], "request_key": "sandbox"})
        run_id = response.json()["id"]
        result = self.client.get(f"/assets/{self.asset['id']}/strategy/runs/{run_id}", headers=self.headers).json()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Docker", result["error"])
        self.assertIsNone(result["result"])

