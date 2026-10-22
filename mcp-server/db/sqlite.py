"""单机 MCP 的 SQLite 连接与极小的 psycopg 兼容层。"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

_CAST = re.compile(r"::[A-Za-z_][A-Za-z0-9_]*(?:\[\])?")


def _convert_date(value: bytes) -> date:
    return date.fromisoformat(value.decode("utf-8"))


def _convert_datetime(value: bytes) -> datetime:
    return datetime.fromisoformat(value.decode("utf-8"))


def _convert_decimal(value: bytes) -> Decimal:
    return Decimal(value.decode("utf-8"))


sqlite3.register_adapter(Decimal, str)
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
sqlite3.register_converter("DATE", _convert_date)
sqlite3.register_converter("TIMESTAMPTZ", _convert_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)
sqlite3.register_converter("NUMERIC", _convert_decimal)
sqlite3.register_converter("DECIMAL", _convert_decimal)


def translate_sql(sql: str) -> str:
    """转换本项目已使用的基础 PostgreSQL 语法，避免仓储层分叉。"""
    text = _CAST.sub("", sql)
    text = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBIGSERIAL\s+PRIMARY\s+KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBIGSERIAL\b", "INTEGER", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBIGINT\b", "INTEGER", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTIMESTAMPTZ\b", "TIMESTAMP", text, flags=re.IGNORECASE)
    text = re.sub(r"\bILIKE\b", "LIKE", text, flags=re.IGNORECASE)
    return text.replace("%s", "?")


class SqliteCursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def __enter__(self) -> "SqliteCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._cursor.close()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> "SqliteCursor":
        statement = translate_sql(sql)
        values = tuple(params or ())
        if not values and statement.count(";") > 1:
            self._cursor.executescript(statement)
        else:
            self._cursor.execute(statement, values)
        return self

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> "SqliteCursor":
        self._cursor.executemany(translate_sql(sql), [tuple(row) for row in params])
        return self

    def fetchone(self) -> sqlite3.Row | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[sqlite3.Row]:
        return self._cursor.fetchall()


class SqliteConnection:
    def __init__(self, path: Path):
        self._connection = sqlite3.connect(
            path,
            timeout=10,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._connection.execute("PRAGMA busy_timeout = 10000")

    def cursor(self) -> SqliteCursor:
        return SqliteCursor(self._connection.cursor())

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()
