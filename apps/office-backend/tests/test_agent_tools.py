"""办公资产工具（原 mcp local-office-tools）直连 Store 单测。"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from mcp.server.fastmcp import FastMCP

import agent_tools.document_content_service as document
import agent_tools.document_service as office_docs
import agent_tools.mindmap_service as office_mindmaps
import agent_tools.project_plan_service as office_plans
import agent_tools.spreadsheet_service as office_sheets
import agent_tools.workbook_service as workbook
from agent_tools.errors import OfficeError
from agent_tools.paths import is_sheet_path, is_user_office_path
from agent_tools.spreadsheet_service import SpreadsheetServiceError
from agent_tools.store_client import set_client_factory


class FakeOffice:
    def __init__(self, fail_auth: bool = False):
        self.fail_auth = fail_auth
        self.projects_list: list[dict] = []
        self.assets_by_project: dict[str, list[dict]] = {}
        self.docs: dict[str, dict] = {}

    def me(self):
        if self.fail_auth:
            raise OfficeError(401, "令牌无效或已过期。")
        return {"id": "u1", "email": "mcp-agent@invp.local"}

    def projects(self):
        return list(self.projects_list)

    def create_project(self, name: str):
        project = {"id": f"p{len(self.projects_list) + 1}", "name": name}
        self.projects_list.append(project)
        self.assets_by_project[project["id"]] = []
        return project

    def assets(self, project_id: str, kind: str = "sheet"):
        return [item for item in self.assets_by_project.get(project_id, []) if item["kind"] == kind]

    def create_asset(self, project_id: str, name: str, kind: str = "sheet"):
        asset_id = f"a{len(self.docs) + 1}"
        if kind == "document":
            content = {"type": "doc", "content": [{"type": "paragraph"}]}
        elif kind == "mindmap":
            content = {
                "format": "mind-elixir-v1",
                "nodeData": {"id": "root", "topic": name, "children": []},
                "arrows": [],
                "summaries": [],
                "direction": 2,
            }
        elif kind == "project_plan":
            content = {"format": "invp-project-plan-v1", "tasks": []}
        else:
            content = {"name": name}
        doc = {
            "id": asset_id,
            "project_id": project_id,
            "kind": kind,
            "name": name,
            "revision": 0,
            "content": content,
        }
        self.docs[asset_id] = doc
        self.assets_by_project[project_id].append({
            "id": asset_id, "project_id": project_id, "kind": kind, "name": name, "revision": 0,
        })
        return copy.deepcopy(doc)

    def get_asset(self, asset_id: str):
        return copy.deepcopy(self.docs[asset_id])

    def save_asset(self, asset_id: str, content: dict, revision: int, name: str | None = None):
        doc = self.docs[asset_id]
        if revision != doc["revision"]:
            raise OfficeError(409, "内容已在其他会话更新，请重新打开。")
        doc["content"] = copy.deepcopy(content)
        doc["revision"] += 1
        if name is not None:
            doc["name"] = name
        for item in self.assets_by_project[doc["project_id"]]:
            if item["id"] == asset_id:
                item["name"] = doc["name"]
                item["revision"] = doc["revision"]
        return copy.deepcopy(doc)


class PathTests(unittest.TestCase):
    def test_office_paths(self):
        self.assertTrue(is_sheet_path("/mcp/sheets"))
        self.assertTrue(is_sheet_path("/api/spreadsheets/read"))
        self.assertTrue(is_user_office_path("/mcp/documents"))
        self.assertTrue(is_user_office_path("/api/mindmaps/add-node"))
        self.assertTrue(is_user_office_path("/api/project-plans/add-task"))
        self.assertFalse(is_sheet_path("/mcp/documents"))
        self.assertFalse(is_sheet_path("/mcp"))


class WorkbookTests(unittest.TestCase):
    def test_parse_a1_and_write_named_sheet(self):
        self.assertEqual(workbook.parse_a1("B3"), (2, 1))
        content = workbook.new_workbook("销售表")
        workbook.ensure_worksheet(content, "Q1")
        workbook.write_cells(content, "Q1", [["月份", 100], ["=A1", None]], 0, 0)
        sheet = workbook.find_sheet(content, "Q1")[1]
        self.assertEqual(sheet["cellData"]["0"]["0"]["v"], "月份")
        self.assertEqual(sheet["cellData"]["0"]["1"]["t"], 2)
        self.assertEqual(sheet["cellData"]["1"]["0"]["f"], "=A1")
        self.assertEqual(workbook.to_a1(2, 1), "B3")
        self.assertEqual(workbook.read_cells(content, "Q1", 0, 0), [["月份", 100], ["=A1", None]])

    def test_merge_background_border_and_font(self):
        content = workbook.new_workbook("销售表")
        workbook.ensure_worksheet(content, "Q1")
        workbook.write_cells(content, "Q1", [["标题", "金额"], ["1月", 100]], 0, 0)
        merged = workbook.merge_range(content, "Q1", "A1", "B1")
        self.assertTrue(merged["merged"])
        workbook.set_background(content, "Q1", "A1", "B1", "yellow")
        workbook.set_border(content, "Q1", "A1", "B2", "thin", "#000000", "outside")
        workbook.set_font(content, "Q1", "A1", "B1", font_family="微软雅黑", font_size=14, bold=True, align="center")
        sheet = workbook.find_sheet(content, "Q1")[1]
        style = content["styles"][sheet["cellData"]["0"]["0"]["s"]]
        self.assertEqual(style["bg"]["rgb"], "#FFFF00")
        self.assertEqual(style["ff"], "微软雅黑")
        unmerged = workbook.unmerge_range(content, "Q1", "A1", "B1")
        self.assertEqual(unmerged["removed"], 1)


class SheetWriteTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_write_uses_project_table_sheet_names(self):
        created = office_sheets.create_spreadsheet("年度预算", "销售表", "Q1")
        self.assertEqual(created["created"], {"project": True, "table": True, "sheet": True})
        written = office_sheets.write_spreadsheet(
            "年度预算", "销售表", "Q1",
            values=[["月份", "金额"], ["1月", 100]],
            start_cell="A1",
        )
        self.assertEqual(written["rows"], 2)
        read = office_sheets.read_spreadsheet("年度预算", "销售表", "Q1")
        self.assertEqual(read["values"], [["月份", "金额"], ["1月", 100]])

    def test_encrypted_sheet_is_rejected(self):
        office_sheets.create_spreadsheet("年度预算", "机密表", "Q1")
        secret = next(item for item in self.api.docs.values() if item["name"] == "机密表")
        secret["locked"] = True
        secret["encrypted"] = True
        secret["content"] = {"format": "youju-sheet-encrypted-v1"}
        with self.assertRaises(SpreadsheetServiceError) as ctx:
            office_sheets.read_spreadsheet("年度预算", "机密表", "Q1")
        self.assertEqual(ctx.exception.status, 403)

    def test_sheet_write_requires_login_email(self):
        self.api.me = lambda: {"id": "u1"}
        with self.assertRaises(SpreadsheetServiceError) as ctx:
            office_sheets.create_spreadsheet("P", "T", "S1")
        self.assertEqual(ctx.exception.status, 401)

    def test_create_duplicate_sheet_rejected(self):
        office_sheets.create_spreadsheet("P", "T", "S1")
        with self.assertRaises(ValueError):
            office_sheets.create_spreadsheet("P", "T", "S1")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_markdown_roundtrip(self):
        doc = document.markdown_to_document("# 标题\n\n正文 **加粗**")
        self.assertIn("标题", document.document_to_markdown(doc))

    def test_create_write_read_and_format(self):
        office_docs.create_document("年度预算", "纪要")
        written = office_docs.write_document("年度预算", "纪要", "# 周会\n\n讨论进度", mode="replace")
        self.assertEqual(written["block_count"], 2)
        read = office_docs.read_document("年度预算", "纪要")
        self.assertIn("周会", read["markdown"])


class MindmapTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_mindmap_nodes(self):
        created = office_mindmaps.create_mindmap("公司研究", "腾讯投资逻辑", "腾讯控股")
        self.assertEqual(created["root"]["topic"], "腾讯控股")
        office_mindmaps.add_mindmap_node(
            "公司研究", "腾讯投资逻辑", "root", "核心业务", note="收入来源", tags=["业务"], node_id="business",
        )
        office_mindmaps.add_mindmap_node("公司研究", "腾讯投资逻辑", "business", "游戏", node_id="games")
        read = office_mindmaps.read_mindmap("公司研究", "腾讯投资逻辑")
        games = next(item for item in read["nodes"] if item["id"] == "games")
        self.assertEqual(games["parent_id"], "business")


class ProjectPlanTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_task_crud(self):
        office_plans.create_project_plan("腾讯", "年度计划")
        office_plans.add_project_plan_task("腾讯", "年度计划", "业绩跟踪", start_date="2026-01-01", end_date="2026-12-31", task_id="root")
        office_plans.add_project_plan_task("腾讯", "年度计划", "一季报", "root", "2026-01-01", "2026-03-31", 50, "关注广告", "q1")
        read = office_plans.read_project_plan("腾讯", "年度计划")
        root_task = next(item for item in read["tasks"] if item["id"] == "root")
        self.assertEqual(root_task["progress"], 50)


class ToolRegistrationTests(unittest.TestCase):
    def test_sheet_tools(self):
        sheets = FastMCP("youju-sheets")
        for tool in (
            office_sheets.create_spreadsheet, office_sheets.read_spreadsheet, office_sheets.write_spreadsheet,
            office_sheets.merge_cells, office_sheets.unmerge_cells,
            office_sheets.set_cell_background, office_sheets.set_cell_border, office_sheets.set_cell_font,
        ):
            sheets.tool()(tool)
        names = {tool.name for tool in sheets._tool_manager.list_tools()}
        self.assertEqual(names, {
            "create_spreadsheet", "read_spreadsheet", "write_spreadsheet",
            "merge_cells", "unmerge_cells",
            "set_cell_background", "set_cell_border", "set_cell_font",
        })




class AgentToolsHttpTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from server import create_app
        from store import MemoryStore
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        auth = self.client.post("/auth/register", json={"email": "agent@example.com", "password": "secret1"}).json()
        jwt = {"Authorization": f"Bearer {auth['token']}"}
        self.token = self.client.get("/auth/access-token", headers=jwt).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_spreadsheet_rest_with_user_token(self):
        created = self.client.post("/api/spreadsheets/create", headers=self.headers, json={
            "project_name": "年度预算", "table_name": "销售表", "sheet_name": "Q1",
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["ok"])
        written = self.client.post("/api/spreadsheets/write", headers=self.headers, json={
            "project_name": "年度预算", "table_name": "销售表", "sheet_name": "Q1",
            "values": [["月份", 100]], "start_cell": "A1",
        })
        self.assertEqual(written.status_code, 200, written.text)
        read = self.client.post("/api/spreadsheets/read", headers=self.headers, json={
            "project_name": "年度预算", "table_name": "销售表", "sheet_name": "Q1",
        })
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["values"], [["月份", 100]])

    def test_rejects_missing_token(self):
        resp = self.client.post("/api/spreadsheets/create", json={
            "project_name": "P", "table_name": "T", "sheet_name": "S1",
        })
        self.assertEqual(resp.status_code, 401)

if __name__ == "__main__":
    unittest.main()
