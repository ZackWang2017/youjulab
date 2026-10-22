"""本机 SQLite 资产库：逻辑复用 MemoryStore，按脏表增量刷盘。"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from store import MemoryStore


_TABLES = (
    "users",
    "projects",
    "assets",
    "digest_jobs",
    "document_skills",
    "assistant_sessions",
    "sheet_cache",
    "sheet_charts",
    "user_model_configs",
)

_ATTR = {
    "users": "_users",
    "projects": "_projects",
    "assets": "_assets",
    "digest_jobs": "_digest_jobs",
    "document_skills": "_document_skills",
    "assistant_sessions": "_assistant_sessions",
    "sheet_cache": "_sheet_cache",
    "sheet_charts": "_sheet_charts",
    "user_model_configs": "_user_model_configs",
}

# 方法 → 可能改动的表；未列出的写操作仍全量落盘。
_METHOD_TABLES: dict[str, tuple[str, ...]] = {
    "create_user": ("users",),
    "update_user": ("users",),
    "ensure_access_token": ("users",),
    "rotate_access_token": ("users",),
    "create_project": ("projects",),
    "update_project": ("projects",),
    "delete_project": ("projects", "assets"),
    "create_asset": ("assets",),
    "update_asset": ("assets",),
    "delete_asset": ("assets",),
    "promote_user_assets": ("assets",),
    "normalize_document_visibility": ("assets",),
    "upsert_user_model_config": ("user_model_configs",),
    "delete_user_model_config": ("user_model_configs",),
    "upsert_digest_job": ("digest_jobs",),
    "delete_digest_job": ("digest_jobs",),
    "upsert_document_skill": ("document_skills",),
    "delete_document_skill": ("document_skills",),
    "delete_document_skills": ("document_skills",),
    "create_assistant_session": ("assistant_sessions",),
    "update_assistant_session": ("assistant_sessions",),
    "delete_assistant_session": ("assistant_sessions",),
    "upsert_sheet_cache": ("sheet_cache",),
    "create_sheet_chart": ("sheet_charts",),
    "update_sheet_chart": ("sheet_charts",),
    "delete_sheet_charts": ("sheet_charts",),
    "reassign_owner": _TABLES,
}


class SqliteStore(MemoryStore):
    """桌面默认存储：内存读写，变更按脏表写入 SQLite（请求内可合并）。"""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._dirty: set[str] = set()
        self._defer = 0
        self._ensure_schema()
        self._load()

    def _ensure_schema(self) -> None:
        for table in _TABLES:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
        self._conn.commit()

    def _load(self) -> None:
        for table, attr in _ATTR.items():
            rows = self._conn.execute(f"SELECT id, data FROM {table}").fetchall()
            bucket: dict[str, dict] = {}
            for row_id, raw in rows:
                try:
                    bucket[row_id] = json.loads(raw)
                except json.JSONDecodeError:
                    continue
            setattr(self, attr, bucket)

    def begin_batch(self) -> None:
        with self._lock:
            self._defer += 1

    def end_batch(self) -> None:
        with self._lock:
            if self._defer > 0:
                self._defer -= 1
            if self._defer == 0 and self._dirty:
                self._flush_dirty()

    @contextmanager
    def batch(self) -> Iterator[None]:
        self.begin_batch()
        try:
            yield
        finally:
            self.end_batch()

    def _mark_dirty(self, tables: tuple[str, ...] | None = None) -> None:
        if tables is None:
            self._dirty.update(_TABLES)
        else:
            self._dirty.update(tables)

    def _flush_dirty(self) -> None:
        if not self._dirty:
            return
        tables = tuple(t for t in _TABLES if t in self._dirty)
        self._dirty.clear()
        for table in tables:
            attr = _ATTR[table]
            bucket: dict[str, dict] = getattr(self, attr)
            self._conn.execute(f"DELETE FROM {table}")
            self._conn.executemany(
                f"INSERT INTO {table} (id, data) VALUES (?, ?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in bucket.items()],
            )
        self._conn.commit()

    def _save(self, tables: tuple[str, ...] | None = None) -> None:
        with self._lock:
            self._mark_dirty(tables)
            if self._defer == 0:
                self._flush_dirty()

    def close(self) -> None:
        with self._lock:
            self._defer = 0
            self._mark_dirty(_TABLES)
            self._flush_dirty()
            self._conn.close()


def _wrap_mutations(cls: type[SqliteStore]) -> type[SqliteStore]:
    skip = {
        "ensure_user_tools",
        "_unique_access_token",
        "_sheet_cache_key",
        "_ensure_schema",
        "_load",
        "_save",
        "_mark_dirty",
        "_flush_dirty",
        "begin_batch",
        "end_batch",
        "batch",
        "close",
        "__init__",
    }
    reads = {
        "user_by_email", "user_by_id", "user_by_access_token", "projects", "project",
        "assets", "asset", "all_projects", "project_any", "assets_any", "asset_any",
        "users", "get_user_model_config", "get_digest_job", "list_document_skills",
        "get_document_skill", "list_enabled_digest_jobs", "list_assistant_sessions",
        "get_assistant_session", "get_sheet_cache", "list_sheet_charts", "get_sheet_chart",
    }
    for name in dir(MemoryStore):
        if name.startswith("_") or name in skip or name in reads:
            continue
        original = getattr(MemoryStore, name)
        if not callable(original):
            continue

        def make(method_name: str = name):
            def wrapped(self: SqliteStore, *args: Any, **kwargs: Any):
                with self._lock:
                    result = getattr(MemoryStore, method_name)(self, *args, **kwargs)
                    self._save(_METHOD_TABLES.get(method_name))
                    return result
            return wrapped

        setattr(cls, name, make())
    return cls


SqliteStore = _wrap_mutations(SqliteStore)
