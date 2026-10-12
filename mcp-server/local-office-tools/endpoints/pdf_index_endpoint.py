"""办公 PDF 索引的 MCP 入口。"""
from __future__ import annotations

from pdf_index_service import ensure_office_pdf_index, get_office_pdf_index_status, search_office_pdf


def register_tools(mcp) -> None:
    mcp.tool()(ensure_office_pdf_index)
    mcp.tool()(get_office_pdf_index_status)
    mcp.tool()(search_office_pdf)
