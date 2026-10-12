"""财务 MCP 工具入口：档案、标的、报表、指标与初筛。"""
from __future__ import annotations

from market_service import (
    get_company_info,
    get_indicators,
    get_profile,
    get_statements,
    list_tickers,
    screen_stock,
)


def register_tools(mcp) -> None:
    mcp.tool()(get_profile)
    mcp.tool()(list_tickers)
    mcp.tool()(get_company_info)
    mcp.tool()(get_statements)
    mcp.tool()(get_indicators)
    mcp.tool()(screen_stock)
