import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "local-office-tools" / "service"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "repository"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "endpoints"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

import project_plan_service as plans
from office_repository import set_client_factory
from project_plan_endpoint import register_tools
from test_invp_office import FakeOffice


class ProjectPlanApiTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_task_crud_move_and_parent_rollup(self):
        plans.create_project_plan("腾讯", "年度计划")
        root = plans.add_project_plan_task("腾讯", "年度计划", "业绩跟踪", start_date="2026-01-01", end_date="2026-12-31", task_id="root")
        self.assertEqual(root["task_id"], "root")
        child = plans.add_project_plan_task("腾讯", "年度计划", "一季报", "root", "2026-01-01", "2026-03-31", 50, "关注广告", "q1")
        self.assertEqual(child["parent_id"], "root")
        plans.add_project_plan_task("腾讯", "年度计划", "二季报", "root", "2026-04-01", "2026-06-30", 70, task_id="q2")
        read = plans.read_project_plan("腾讯", "年度计划")
        root_task = next(item for item in read["tasks"] if item["id"] == "root")
        self.assertEqual((root_task["startDate"], root_task["endDate"], root_task["progress"]), ("2026-01-01", "2026-06-30", 60))

        moved = plans.move_project_plan_task("腾讯", "年度计划", "q2", "", 0)
        self.assertEqual(moved["parent_id"], None)
        updated = plans.update_project_plan_task("腾讯", "年度计划", "q1", note="确认会议纪要", progress=80)
        q1 = next(item for item in updated["tasks"] if item["id"] == "q1")
        self.assertEqual((q1["progress"], q1["note"]), (80, "确认会议纪要"))
        deleted = plans.delete_project_plan_task("腾讯", "年度计划", "root")
        self.assertEqual(deleted["deleted_count"], 2)

    def test_rejects_cyclic_move_and_parent_manual_rollup(self):
        plans.create_project_plan("P", "计划")
        plans.add_project_plan_task("P", "计划", "父", task_id="parent")
        plans.add_project_plan_task("P", "计划", "子", "parent", task_id="child")
        with self.assertRaises(ValueError):
            plans.move_project_plan_task("P", "计划", "parent", "child")
        with self.assertRaises(ValueError):
            plans.update_project_plan_task("P", "计划", "parent", progress=90)

    def test_tools_are_registered_on_dedicated_mcp(self):
        mcp = FastMCP("invp-project-plans")
        register_tools(mcp)
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertEqual(names, {
            "create_project_plan", "read_project_plan", "add_project_plan_task",
            "update_project_plan_task", "move_project_plan_task", "delete_project_plan_task",
        })


if __name__ == "__main__":
    unittest.main()
