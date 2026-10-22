"""Electron 打包用的 mcp-server HTTP 入口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _mcp_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "mcp-server"
    # apps/office-desktop/mcp-sidecar → 仓库根目录/mcp-server
    return Path(__file__).resolve().parents[3] / "mcp-server"


def main() -> None:
    root = _mcp_root()
    server = root / "server.py"
    if not server.is_file():
        raise RuntimeError(f"找不到打包的 mcp-server：{server}")
    os.environ.setdefault("MCP_SOURCE_ROOT", str(root))
    sys.path.insert(0, str(root))
    # 静态导入令 PyInstaller 分析所有三层路由依赖；源码目录仍用于规则和模板资源。
    from server import _run_http, mcp  # noqa: PLC0415

    transport = os.environ.get("FINANCE_MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        _run_http()
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
