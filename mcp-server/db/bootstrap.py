"""初始化 finresearch 数据库及表结构。

用法（在 mcp-server 目录下）：
  .venv/bin/python -m db.bootstrap
"""

from __future__ import annotations

import sys

import psycopg

from db.config import load_config, sqlite_path, store_mode
from db.connection import get_connection
from db.schema import ALL_DDL


def ensure_database() -> None:
    if store_mode() == "sqlite":
        sqlite_path()
        return
    cfg = load_config()
    with psycopg.connect(cfg.admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg.dbname,))
            if cur.fetchone():
                return
            cur.execute(f'CREATE DATABASE "{cfg.dbname}" ENCODING \'UTF8\'')
            print(f"created database: {cfg.dbname}")


def init_schema() -> None:
    if store_mode() == "sqlite":
        from db.sqlite_schema import ALL_DDL
    else:
        from db.schema import ALL_DDL
    with get_connection() as conn:
        with conn.cursor() as cur:
            for ddl in ALL_DDL:
                cur.execute(ddl)
    print("schema initialized")


def main() -> int:
    try:
        ensure_database()
        init_schema()
    except (psycopg.Error, RuntimeError) as exc:
        print(f"database init failed: {exc}", file=sys.stderr)
        return 1
    if store_mode() == "sqlite":
        print(f"ready: sqlite://{sqlite_path()}")
    else:
        cfg = load_config()
        print(f"ready: {cfg.dsn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
