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

import mindmap_service as mindmaps
from mindmap_endpoint import register_tools
from office_repository import set_client_factory
from test_invp_office import FakeOffice


class MindmapApiTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeOffice()
        set_client_factory(lambda: self.api)

    def tearDown(self):
        set_client_factory(None)

    def test_create_and_node_crud(self):
        created = mindmaps.create_mindmap("研究项目", "公司分析", "投资逻辑")
        self.assertEqual(created["root"]["topic"], "投资逻辑")

        first = mindmaps.add_mindmap_node(
            "研究项目", "公司分析", "root", "竞争优势", "护城河", ["基本面"], node_id="moat",
        )
        self.assertEqual(first["node_id"], "moat")
        mindmaps.add_mindmap_node("研究项目", "公司分析", "moat", "品牌", node_id="brand")
        updated = mindmaps.update_mindmap_node(
            "研究项目", "公司分析", "brand", topic="品牌心智", hyperlink="https://example.com",
        )
        self.assertEqual(updated["action"], "update_node")

        moved = mindmaps.move_mindmap_node("研究项目", "公司分析", "brand", "root", 0)
        self.assertEqual(moved["index"], 0)
        read = mindmaps.read_mindmap("研究项目", "公司分析")
        brand = next(item for item in read["nodes"] if item["id"] == "brand")
        self.assertEqual(brand["parent_id"], "root")
        self.assertEqual(brand["topic"], "品牌心智")

        deleted = mindmaps.delete_mindmap_node("研究项目", "公司分析", "moat")
        self.assertEqual(deleted["deleted_count"], 1)
        self.assertEqual(mindmaps.read_mindmap("研究项目", "公司分析")["node_count"], 2)

    def test_write_replaces_tree_and_rejects_bad_operations(self):
        written = mindmaps.write_mindmap("P", "行业图", {
            "id": "root",
            "topic": "半导体",
            "children": [
                {"id": "upstream", "topic": "上游", "children": []},
                {"id": "downstream", "topic": "下游", "children": []},
            ],
        })
        self.assertEqual(written["node_count"], 3)
        with self.assertRaises(ValueError):
            mindmaps.delete_mindmap_node("P", "行业图", "root")
        with self.assertRaises(ValueError):
            mindmaps.move_mindmap_node("P", "行业图", "upstream", "upstream")
        with self.assertRaises(ValueError):
            mindmaps.write_mindmap("P", "行业图", {
                "id": "root", "topic": "重复", "children": [{"id": "root", "topic": "重复", "children": []}],
            })

    def test_tools_are_registered_on_dedicated_mcp(self):
        mcp = FastMCP("invp-mindmaps")
        register_tools(mcp)
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertEqual(names, {
            "create_mindmap", "read_mindmap", "write_mindmap", "add_mindmap_node",
            "update_mindmap_node", "delete_mindmap_node", "move_mindmap_node",
        })


if __name__ == "__main__":
    unittest.main()
