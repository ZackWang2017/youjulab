"""MCP registration for announcement PDF outline generation."""
from __future__ import annotations

from announcement_outline_service import generate_pdf_outline


def register_tools(mcp) -> None:
    mcp.tool()(generate_pdf_outline)
