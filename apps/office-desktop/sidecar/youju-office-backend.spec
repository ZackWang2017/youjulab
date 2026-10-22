# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：打出 onedir 的 youju-office-backend。"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECDIR = Path(SPEC).resolve().parent
DESKTOP = SPECDIR.parent
APPS = DESKTOP.parent
BACKEND = APPS / "office-backend"
ENTRY = SPECDIR / "entry.py"

datas = []
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
    "multipart",
    "email_validator",
]

for pkg in (
    "uvicorn",
    "fastapi",
    "starlette",
    "anyio",
    "httpx",
    "httpcore",
    "pydantic",
    "pydantic_core",
    "jwt",
    "cryptography",
    "dotenv",
    "yaml",
    "pymupdf",
    "fitz",
    "sqlite_vec",
    "psycopg",
    "psycopg_binary",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001 — 可选依赖缺了就跳过
        print(f"collect_all({pkg}) skip: {exc}", file=sys.stderr)

# office-backend 本地包（不含 tests）
hiddenimports += collect_submodules("announcement_intelligence")
for name in BACKEND.glob("*.py"):
    if name.name == "__init__.py":
        continue
    hiddenimports.append(name.stem)

a = Analysis(
    [str(ENTRY)],
    pathex=[str(BACKEND)],
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
    name="youju-office-backend",
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
    name="youju-office-backend",
)
