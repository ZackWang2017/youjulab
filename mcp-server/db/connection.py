"""PostgreSQL 连接工厂与上下文管理。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from db.config import DatabaseConfig, load_config, sqlite_path, store_mode
from db.sqlite import SqliteConnection


def connect(config: DatabaseConfig | None = None, *, admin: bool = False) -> Any:
    if store_mode() == "sqlite":
        if admin:
            raise RuntimeError("SQLite 不支持管理员数据库连接。")
        return SqliteConnection(sqlite_path())
    cfg = config or load_config()
    dsn = cfg.admin_dsn if admin else cfg.dsn
    return psycopg.connect(dsn, row_factory=dict_row)


@contextmanager
def get_connection(*, admin: bool = False) -> Iterator[Any]:
    conn = connect(admin=admin)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
