# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：打出包含完整 mcp-server 源码的 onedir sidecar。"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPECDIR = Path(SPEC).resolve().parent
DESKTOP = SPECDIR.parent
ROOT = DESKTOP.parents[1]
MCP = ROOT / "mcp-server"
ENTRY = SPECDIR / "entry.py"
LAYERS = (
    "external-tools/repository",
    "external-tools/service",
    "external-tools/endpoints",
    "local-office-tools/repository",
    "local-office-tools/service",
    "local-office-tools/endpoints",
    "financial-data-service/repository",
    "financial-data-service/service",
    "financial-data-service/endpoints",
    "local-tools/repository",
    "local-tools/service",
    "local-tools/endpoints",
    "portfolio-service/repository",
    "portfolio-service/service",
    "portfolio-service/endpoints",
    "account-service/repository",
    "account-service/service",
    "account-service/endpoints",
    "embedding-service/service",
    "embedding-service/endpoints",
)

# 将完整业务源码作为运行时数据带入。server.py 会从这个目录动态加载三层模块。
datas = [(str(MCP), "mcp-server")]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

# server.py 的业务层在运行期从随包源码加载，第三方依赖则需显式收集。
for pkg in (
    "mcp",
    "uvicorn",
    "starlette",
    "anyio",
    "httpx",
    "httpcore",
    "pydantic",
    "pydantic_core",
    "psycopg",
    "psycopg_binary",
    "dotenv",
    "yaml",
    "pymupdf",
    "fitz",
    "openpyxl",
    "feedparser",
    "qrcode",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001 - 兼容可选依赖缺失的开发环境
        print(f"collect_all({pkg}) skip: {exc}", file=sys.stderr)

a = Analysis(
    [str(ENTRY)],
    pathex=[str(MCP), *[str(MCP / layer) for layer in LAYERS]],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="youju-mcp-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="youju-mcp-server",
)
