import copy
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "local-office-tools" / "service"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "repository"))
sys.path.insert(0, str(_ROOT / "local-office-tools" / "endpoints"))

from mcp.server.fastmcp import FastMCP

import access_token
import spreadsheet_service as office_sheets
import workbook_service as workbook
from office_repository import OfficeError, set_client_factory
from spreadsheet_endpoint import register_tools
from spreadsheet_service import SpreadsheetServiceError
from mindmap_endpoint import register_tools as register_mindmap_tools
import mindmap_service as office_mindmaps


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
        doc = {
            "id": asset_id,
            "project_id": project_id,
            "kind": kind,
            "name": name,
            "revision": 0,
            "content": (
                {"type": "doc", "content": [{"type": "paragraph"}]}
                if kind == "document" else (
                    {
                        "format": "mind-elixir-v1",
                        "nodeData": {"id": "root", "topic": name, "children": []},
                        "arrows": [],
                        "summaries": [],
                        "direction": 2,
                    }
                    if kind == "mindmap" else {"name": name}
                )
            ),
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


class AccessTokenTests(unittest.TestCase):
    def setUp(self):
        self._prev = {key: os.environ.get(key) for key in ("MCP_ACCESS_TOKEN", "FINANCE_MCP_TOKEN")}

    def tearDown(self):
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_default_token_is_32_chars_and_matches_bearer(self):
        self.assertEqual(len(access_token.DEFAULT_MCP_ACCESS_TOKEN), 32)
        os.environ.pop("MCP_ACCESS_TOKEN", None)
        os.environ.pop("FINANCE_MCP_TOKEN", None)
    def test_incoming_token_is_forwarded_to_office_client(self):
        from access_token import incoming_access_token, is_sheet_path, is_user_office_path, reset_incoming_access_token, set_incoming_access_token
        from office_repository import client_for, set_client_factory
        set_client_factory(None)
        self.assertTrue(is_sheet_path("/mcp/sheets"))
        self.assertTrue(is_sheet_path("/api/spreadsheets/read"))
        self.assertTrue(is_user_office_path("/mcp/documents"))
        self.assertTrue(is_user_office_path("/api/documents/read"))
        self.assertTrue(is_user_office_path("/mcp/mindmaps"))
        self.assertTrue(is_user_office_path("/api/mindmaps/add-node"))
        self.assertFalse(is_sheet_path("/mcp/documents"))
        self.assertFalse(is_sheet_path("/mcp"))
        token = "ab" * 16
        saved = set_incoming_access_token(token)
        try:
            self.assertEqual(incoming_access_token(), token)
            self.assertEqual(client_for().token, token)
        finally:
            reset_incoming_access_token(saved)


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
        self.assertNotIn("1", sheet["cellData"]["1"])
        self.assertEqual(workbook.to_a1(2, 1), "B3")
        self.assertEqual(
            workbook.read_cells(content, "Q1", 0, 0),
            [["月份", 100], ["=A1", None]],
        )

    def test_merge_background_border_and_font(self):
        content = workbook.new_workbook("销售表")
        workbook.ensure_worksheet(content, "Q1")
        workbook.write_cells(content, "Q1", [["标题", "金额"], ["1月", 100]], 0, 0)
        merged = workbook.merge_range(content, "Q1", "A1", "B1")
        self.assertTrue(merged["merged"])
        sheet = workbook.find_sheet(content, "Q1")[1]
        self.assertEqual(sheet["mergeData"][0], {
            "startRow": 0, "startColumn": 0, "endRow": 0, "endColumn": 1,
        })
        with self.assertRaises(ValueError):
            workbook.merge_range(content, "Q1", "A1", "A2")
        workbook.set_background(content, "Q1", "A1", "B1", "yellow")
        workbook.set_border(content, "Q1", "A1", "B2", "thin", "#000000", "outside")
        workbook.set_font(content, "Q1", "A1", "B1", font_family="微软雅黑", font_size=14, bold=True, align="center")
        style_id = sheet["cellData"]["0"]["0"]["s"]
        style = content["styles"][style_id]
        self.assertEqual(style["bg"]["rgb"], "#FFFF00")
        self.assertEqual(style["ff"], "微软雅黑")
        self.assertEqual(style["fs"], 14)
        self.assertEqual(style["bl"], 1)
        self.assertEqual(style["ht"], 2)
        self.assertEqual(style["bd"]["t"]["s"], 1)
        self.assertEqual(style["bd"]["l"]["s"], 1)
        inner = content["styles"][sheet["cellData"]["1"]["1"]["s"]]
        self.assertEqual(inner["bd"]["b"]["s"], 1)
        self.assertEqual(inner["bd"]["r"]["s"], 1)
        self.assertNotIn("t", inner["bd"])
        workbook.write_cells(content, "Q1", [["新标题"]], 0, 0)
        self.assertEqual(sheet["cellData"]["0"]["0"]["v"], "新标题")
        self.assertEqual(sheet["cellData"]["0"]["0"]["s"], style_id)
        unmerged = workbook.unmerge_range(content, "Q1", "A1", "B1")
        self.assertEqual(unmerged["removed"], 1)
        self.assertEqual(sheet["mergeData"], [])

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            workbook.parse_a1("3A")
        with self.assertRaises(ValueError):
            workbook.normalize_values([[{"x": 1}]])


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
        self.assertEqual(written["action"], "write")
        self.assertEqual(written["rows"], 2)
        sales = next(item for item in self.api.docs.values() if item["name"] == "销售表")
        q1 = workbook.find_sheet(sales["content"], "Q1")[1]
        self.assertEqual(q1["cellData"]["0"]["0"]["v"], "月份")
        self.assertEqual(q1["cellData"]["1"]["1"]["v"], 100)
        read = office_sheets.read_spreadsheet("年度预算", "销售表", "Q1")
        self.assertEqual(read["action"], "read")
        self.assertEqual(read["values"], [["月份", "金额"], ["1月", 100]])
        self.assertEqual(read["sheet_names"], ["Q1"])
        clipped = office_sheets.read_spreadsheet("年度预算", "销售表", "Q1", start_cell="B1", end_cell="B2")
        self.assertEqual(clipped["values"], [["金额"], [100]])

    def test_merge_and_format_named_sheet(self):
        office_sheets.create_spreadsheet("年度预算", "销售表", "Q1")
        office_sheets.write_spreadsheet("年度预算", "销售表", "Q1", [["标题", "金额"]], "A1")
        merged = office_sheets.merge_cells("年度预算", "销售表", "Q1", "A1", "B1")
        self.assertEqual(merged["action"], "merge")
        self.assertTrue(merged["merged"])
        painted = office_sheets.set_cell_background("年度预算", "销售表", "Q1", "A1", "B1", "#4C78FF")
        self.assertEqual(painted["cells"], 2)
        bordered = office_sheets.set_cell_border("年度预算", "销售表", "Q1", "A1", "B1", "medium", "black", "all")
        self.assertEqual(bordered["style"], "medium")
        fonted = office_sheets.set_cell_font("年度预算", "销售表", "Q1", "A1", "", "Arial", 12, True, None, None, None, "", "center", "")
        self.assertEqual(fonted["action"], "font")
        sales = next(item for item in self.api.docs.values() if item["name"] == "销售表")
        q1 = workbook.find_sheet(sales["content"], "Q1")[1]
        self.assertEqual(q1["mergeData"][0]["endColumn"], 1)
        style = sales["content"]["styles"][q1["cellData"]["0"]["0"]["s"]]
        self.assertEqual(style["bg"]["rgb"], "#4C78FF")
        self.assertEqual(style["bd"]["t"]["s"], 8)
        self.assertEqual(style["ff"], "Arial")
        self.assertEqual(style["bl"], 1)
        self.assertEqual(style["ht"], 2)
        removed = office_sheets.unmerge_cells("年度预算", "销售表", "Q1", "A1", "B1")
        self.assertEqual(removed["removed"], 1)

    def test_sheet_write_requires_login_email(self):
        self.api.me = lambda: {"id": "u1"}
        with self.assertRaises(SpreadsheetServiceError) as ctx:
            office_sheets.create_spreadsheet("P", "T", "S1")
        self.assertEqual(ctx.exception.status, 401)

    def test_read_missing_sheet_rejected(self):
        office_sheets.create_spreadsheet("P", "T", "S1")
        with self.assertRaises(ValueError):
            office_sheets.read_spreadsheet("P", "T", "missing")

    def test_create_duplicate_sheet_rejected(self):
        office_sheets.create_spreadsheet("P", "T", "S1")
        with self.assertRaises(ValueError):
            office_sheets.create_spreadsheet("P", "T", "S1")

    def test_sheet_tools_are_not_on_finance_mcp(self):
        sheets = FastMCP("invp-sheets")
        register_tools(sheets)
        names = {tool.name for tool in sheets._tool_manager.list_tools()}
        self.assertEqual(names, {
            "create_spreadsheet", "read_spreadsheet", "write_spreadsheet",
            "merge_cells", "unmerge_cells",
            "set_cell_background", "set_cell_border", "set_cell_font",
        })
        self.assertTrue({"get_indicators", "get_statements", "parse_pdf", "run_office_agent"}.isdisjoint(names))


class MindmapToolTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_mindmap_nodes_are_readable_and_mutable_by_stable_id(self):
        created = office_mindmaps.create_mindmap("公司研究", "腾讯投资逻辑", "腾讯控股")
        self.assertEqual(created["root"]["id"], "root")
        self.assertEqual(created["root"]["topic"], "腾讯控股")

        added = office_mindmaps.add_mindmap_node(
            "公司研究", "腾讯投资逻辑", "root", "核心业务",
            note="收入来源", tags=["业务", "业务"], node_id="business",
        )
        self.assertEqual(added["node_id"], "business")
        office_mindmaps.add_mindmap_node(
            "公司研究", "腾讯投资逻辑", "business", "游戏", node_id="games",
        )
        updated = office_mindmaps.update_mindmap_node(
            "公司研究", "腾讯投资逻辑", "games", topic="游戏业务", hyperlink="https://example.com",
        )
        self.assertEqual(updated["action"], "update_node")

        read = office_mindmaps.read_mindmap("公司研究", "腾讯投资逻辑")
        games = next(item for item in read["nodes"] if item["id"] == "games")
        self.assertEqual(games["parent_id"], "business")
        self.assertEqual(games["path"], ["腾讯控股", "核心业务", "游戏业务"])

        moved = office_mindmaps.move_mindmap_node(
            "公司研究", "腾讯投资逻辑", "games", "root", 0,
        )
        self.assertEqual(moved["parent_id"], "root")
        with self.assertRaisesRegex(ValueError, "自身或其后代"):
            office_mindmaps.move_mindmap_node(
                "公司研究", "腾讯投资逻辑", "business", "business",
            )
        deleted = office_mindmaps.delete_mindmap_node("公司研究", "腾讯投资逻辑", "business")
        self.assertEqual(deleted["deleted_count"], 1)
        with self.assertRaisesRegex(ValueError, "根节点"):
            office_mindmaps.delete_mindmap_node("公司研究", "腾讯投资逻辑", "root")

    def test_mindmap_tools_are_only_registered_on_mindmap_mcp(self):
        mindmaps = FastMCP("invp-mindmaps")
        register_mindmap_tools(mindmaps)
        names = {tool.name for tool in mindmaps._tool_manager.list_tools()}
        self.assertEqual(names, {
            "create_mindmap", "read_mindmap", "write_mindmap", "add_mindmap_node",
            "update_mindmap_node", "delete_mindmap_node", "move_mindmap_node",
        })
        self.assertTrue({"create_spreadsheet", "read_document", "run_office_agent"}.isdisjoint(names))


if __name__ == "__main__":
    unittest.main()
