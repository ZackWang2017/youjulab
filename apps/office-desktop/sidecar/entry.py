"""Electron 打包用的 office-backend 入口。开发态也可：python entry.py。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_path() -> None:
    if getattr(sys, "frozen", False):
        return
    # apps/office-desktop/sidecar → apps/office-backend
    backend = Path(__file__).resolve().parents[2] / "office-backend"
    sys.path.insert(0, str(backend))


def _prepare_frozen_ssl() -> None:
    """打包态：修复 certifi 路径，并为静态链接 OpenSSL 补齐配置，避免 SSLContext Errno 2。"""
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", "") or "")
    if not meipass.is_dir():
        return
    cacert = meipass / "certifi" / "cacert.pem"
    if cacert.is_file():
        os.environ.setdefault("SSL_CERT_FILE", str(cacert))
        os.environ.setdefault("REQUESTS_CA_BUNDLE", str(cacert))
    for candidate in (
        Path("/private/etc/ssl/openssl.cnf"),
        Path("/etc/ssl/openssl.cnf"),
        Path("/opt/homebrew/etc/openssl@3/openssl.cnf"),
    ):
        if candidate.is_file():
            os.environ.setdefault("OPENSSL_CONF", str(candidate))
            break
    try:
        import certifi

        if cacert.is_file():
            certifi.where = lambda: str(cacert)  # type: ignore[method-assign]
    except Exception:
        pass
    # 启动期探测：便于确认打包态 SSL 是否可用（失败时打印到 stderr，不阻断服务）。
    if os.environ.get("YOUJU_DEBUG_SSL", "").strip() in {"1", "true", "yes"}:
        try:
            import _ssl  # noqa: F401
            import ssl

            print(f"[youju] OPENSSL_VERSION={ssl.OPENSSL_VERSION}", file=sys.stderr)
            print(f"[youju] OPENSSL_CONF={os.environ.get('OPENSSL_CONF')!r}", file=sys.stderr)
            print(f"[youju] verify_paths={ssl.get_default_verify_paths()!r}", file=sys.stderr)
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            print("[youju] SSLContext ok", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            import traceback

            print(f"[youju] SSLContext failed: {exc!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


_prepare_path()
_prepare_frozen_ssl()

def main() -> None:
    if '--backup-catalog' in sys.argv:
        from backup_catalog import main as catalog_main
        catalog_main(sys.argv[sys.argv.index('--backup-catalog') + 1:])
        return
    if '--user-backup' in sys.argv:
        from user_backup import main as user_backup_main
        user_backup_main(sys.argv[sys.argv.index('--user-backup') + 1:])
        return
    if any(flag in sys.argv for flag in ('--backup-sqlite', '--archive-backup', '--extract-backup')):
        from desktop_data import main as backup_main
        backup_main()
        return
    from config import api_host, api_port
    from server import create_app
    import uvicorn

    uvicorn.run(create_app(), host=api_host(), port=api_port(), reload=False)


if __name__ == "__main__":
    main()
