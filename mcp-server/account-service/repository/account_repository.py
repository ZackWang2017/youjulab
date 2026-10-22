"""账号表。身份只存在远程 Postgres，不进本地文档库。"""
from __future__ import annotations

from db.connection import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def ensure_schema() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)


def get_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, password_hash FROM accounts WHERE email = %s", (email,))
            row = cur.fetchone()
    return dict(row) if row else None


def get_by_id(account_id: str) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, password_hash FROM accounts WHERE id = %s", (account_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def insert(account_id: str, email: str, password_hash: str) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO accounts (id, email, password_hash)
                VALUES (%s, %s, %s)
                RETURNING id, email, password_hash
                """,
                (account_id, email, password_hash),
            )
            row = cur.fetchone()
    return dict(row)


def update_password(account_id: str, password_hash: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET password_hash = %s WHERE id = %s", (password_hash, account_id))
