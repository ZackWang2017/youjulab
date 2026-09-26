import json
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_server_exposes_and_runs_office_tools(self):
        server = Path(__file__).resolve().parents[1] / "server.py"
        params = StdioServerParameters(command=sys.executable, args=[str(server)],
                                      env={"FINANCE_MCP_TRANSPORT": "stdio", "DATA_PROVIDER": "mock"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                self.assertTrue({"parse_pdf", "get_indicators", "run_office_agent", "validate_office_patch"} <= names)
                self.assertNotIn("create_spreadsheet", names)
                self.assertNotIn("read_spreadsheet", names)
                self.assertNotIn("write_spreadsheet", names)
                self.assertNotIn("merge_cells", names)
                self.assertNotIn("set_cell_font", names)
                context = {"document_id": "doc", "surface": "document", "revision": 1,
                           "selection": {"from": 1, "to": 8, "text": " hello ", "single_block": True}}
                result = await session.call_tool("run_office_agent", {"context": context, "intent": "trim", "mode": "demo"})
                self.assertFalse(result.isError)
                payload = json.loads(result.content[0].text)
                self.assertEqual(payload["patch"]["replacement"], "hello")
