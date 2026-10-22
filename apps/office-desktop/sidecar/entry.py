"""Electron 打包用的 office-backend 入口。开发态也可：python entry.py。"""
from __future__ import annotations

import sys
from pathlib import Path


def _prepare_path() -> None:
    if getattr(sys, "frozen", False):
        return
    # apps/office-desktop/sidecar → apps/office-backend
    backend = Path(__file__).resolve().parents[2] / "office-backend"
    sys.path.insert(0, str(backend))


_prepare_path()

from config import api_host, api_port  # noqa: E402
from server import create_app  # noqa: E402


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=api_host(), port=api_port(), reload=False)


if __name__ == "__main__":
    main()
