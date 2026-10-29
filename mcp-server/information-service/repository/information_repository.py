"""资讯组、资讯条目与每日点评的持久化。首次访问时自动建表。"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from typing import Any

from db.connection import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS information_groups (
    id         BIGSERIAL PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    name       VARCHAR(80) NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_information_groups_owner ON information_groups (owner_id, kind);

CREATE TABLE IF NOT EXISTS information_items (
    id                   BIGSERIAL PRIMARY KEY,
    owner_id             TEXT NOT NULL,
    group_id             BIGINT NOT NULL REFERENCES information_groups(id) ON DELETE CASCADE,
    source_kind          VARCHAR(32) NOT NULL,
    source_name          VARCHAR(128) NOT NULL,
    source_icon          VARCHAR(512) NOT NULL DEFAULT '',
    title                VARCHAR(500) NOT NULL,
    url                  TEXT NOT NULL DEFAULT '',
    published_at         VARCHAR(32) NOT NULL DEFAULT '',
    fetched_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date         DATE NOT NULL,
    summary              TEXT NOT NULL DEFAULT '',
    impact               TEXT NOT NULL DEFAULT '',
    favorited            BOOLEAN NOT NULL DEFAULT FALSE,
    hidden               BOOLEAN NOT NULL DEFAULT FALSE,
    favorite_from_kind   VARCHAR(32) NOT NULL DEFAULT '',
    favorite_from_name   VARCHAR(80) NOT NULL DEFAULT '',
    analysis             TEXT NOT NULL DEFAULT '{}',
    UNIQUE (owner_id, title)
);
CREATE INDEX IF NOT EXISTS idx_information_items_group_day
    ON information_items (owner_id, group_id, fetched_date);
CREATE INDEX IF NOT EXISTS idx_information_items_favorite
    ON information_items (owner_id, favorited, fetched_date);

CREATE TABLE IF NOT EXISTS information_reviews (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    group_id      BIGINT NOT NULL REFERENCES information_groups(id) ON DELETE CASCADE,
    review_date   DATE NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    sentiment     VARCHAR(16) NOT NULL DEFAULT '中性',
    content       TEXT NOT NULL DEFAULT '',
    item_refs     TEXT NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, group_id, review_date)
);
CREATE INDEX IF NOT EXISTS idx_information_reviews_owner
    ON information_reviews (owner_id, review_date DESC);
"""

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute("ALTER TABLE information_items ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE")
                cur.execute("ALTER TABLE information_reviews ADD COLUMN IF NOT EXISTS item_refs TEXT NOT NULL DEFAULT '[]'")
        _schema_ready = True


def _row(row: dict | None) -> dict | None:
    return dict(row) if row is not None else None


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_config(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_analysis(raw: Any) -> dict:
    data = _parse_config(raw)
    return {
        "summary": str(data.get("summary") or ""),
        "performance": str(data.get("performance") or ""),
        "financial": str(data.get("financial") or ""),
    }


def _parse_item_refs(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        rows = raw
    else:
        try:
            rows = json.loads(raw or "[]")
        except (TypeError, ValueError):
            rows = []
    if not isinstance(rows, list):
        return []
    refs: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        sentiment = str(item.get("sentiment") or "中性").strip()
        if sentiment not in {"利好", "利空", "中性"}:
            sentiment = "中性"
        refs.append({
            "id": int(item.get("id") or 0),
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "sentiment": sentiment,
        })
    return refs


def public_group(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "owner_id": row["owner_id"],
        "name": row["name"],
        "kind": row["kind"],
        "config": _parse_config(row.get("config")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def public_item(row: dict) -> dict:
    analysis = _parse_analysis(row.get("analysis"))
    has_analysis = any(analysis.values())
    return {
        "id": int(row["id"]),
        "group_id": int(row["group_id"]),
        "source_kind": row["source_kind"],
        "source_name": row["source_name"],
        "source_icon": row.get("source_icon") or "",
        "title": row["title"],
        "url": row.get("url") or "",
        "published_at": row.get("published_at") or "",
        "fetched_at": _iso(row.get("fetched_at")),
        "fetched_date": _iso(row.get("fetched_date"))[:10],
        "summary": row.get("summary") or "",
        "impact": row.get("impact") or "",
        "favorited": bool(row.get("favorited")),
        "hidden": bool(row.get("hidden")),
        "favorite_from_kind": row.get("favorite_from_kind") or "",
        "favorite_from_name": row.get("favorite_from_name") or "",
        "analysis": analysis if has_analysis else None,
    }


def public_review(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "group_id": int(row["group_id"]),
        "review_date": _iso(row.get("review_date"))[:10],
        "success_count": int(row.get("success_count") or 0),
        "fail_count": int(row.get("fail_count") or 0),
        "sentiment": row.get("sentiment") or "中性",
        "content": row.get("content") or "",
        "item_refs": _parse_item_refs(row.get("item_refs")),
        "created_at": _iso(row.get("created_at")),
    }


def list_groups(owner_id: str) -> list[dict]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_groups WHERE owner_id = %s ORDER BY CASE kind WHEN 'favorites' THEN 0 ELSE 1 END, created_at, id",
                (owner_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_group(owner_id: str, group_id: int) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_groups WHERE owner_id = %s AND id = %s",
                (owner_id, group_id),
            )
            return _row(cur.fetchone())


def find_group_by_kind(owner_id: str, kind: str) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_groups WHERE owner_id = %s AND kind = %s ORDER BY id LIMIT 1",
                (owner_id, kind),
            )
            return _row(cur.fetchone())


def create_group(owner_id: str, name: str, kind: str, config: dict | None = None) -> dict:
    ensure_schema()
    payload = json.dumps(config or {}, ensure_ascii=False)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO information_groups (owner_id, name, kind, config) VALUES (%s, %s, %s, %s) RETURNING *",
                (owner_id, name, kind, payload),
            )
            return dict(cur.fetchone())


def update_group(owner_id: str, group_id: int, name: str, config: dict | None = None) -> dict | None:
    ensure_schema()
    payload = json.dumps(config or {}, ensure_ascii=False)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE information_groups SET name = %s, config = %s, updated_at = NOW() WHERE owner_id = %s AND id = %s RETURNING *",
                (name, payload, owner_id, group_id),
            )
            return _row(cur.fetchone())


def delete_group(owner_id: str, group_id: int) -> bool:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM information_groups WHERE owner_id = %s AND id = %s AND kind <> 'favorites'",
                (owner_id, group_id),
            )
            return cur.rowcount > 0


def known_titles(owner_id: str) -> set[str]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM information_items WHERE owner_id = %s", (owner_id,))
            return {str(r["title"]) for r in cur.fetchall()}


def insert_item(fields: dict) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO information_items (
                    owner_id, group_id, source_kind, source_name, source_icon,
                    title, url, published_at, fetched_date, summary, impact
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, title) DO NOTHING
                RETURNING *
                """,
                (
                    fields["owner_id"], fields["group_id"], fields["source_kind"], fields["source_name"],
                    fields.get("source_icon") or "", fields["title"], fields.get("url") or "",
                    fields.get("published_at") or "", fields["fetched_date"],
                    fields.get("summary") or "", fields.get("impact") or "",
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_items(
    owner_id: str,
    *,
    group_id: int | None = None,
    fetched_date: date | None = None,
    favorited: bool | None = None,
    hidden: bool | None = None,
) -> list[dict]:
    ensure_schema()
    sql = "SELECT * FROM information_items WHERE owner_id = %s"
    params: list[Any] = [owner_id]
    if favorited is True:
        sql += " AND favorited = TRUE"
    elif favorited is False:
        sql += " AND favorited = FALSE"
    if hidden is True:
        sql += " AND hidden = TRUE"
    elif hidden is False:
        sql += " AND hidden = FALSE"
    if group_id is not None:
        sql += " AND group_id = %s"
        params.append(group_id)
    if fetched_date is not None:
        sql += " AND fetched_date = %s"
        params.append(fetched_date)
    sql += " ORDER BY fetched_at DESC, id DESC"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def get_item(owner_id: str, item_id: int) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_items WHERE owner_id = %s AND id = %s",
                (owner_id, item_id),
            )
            return _row(cur.fetchone())


def set_favorite(owner_id: str, item_id: int, favorited: bool, from_kind: str, from_name: str) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE information_items
                SET favorited = %s, favorite_from_kind = %s, favorite_from_name = %s
                WHERE owner_id = %s AND id = %s
                RETURNING *
                """,
                (favorited, from_kind if favorited else "", from_name if favorited else "", owner_id, item_id),
            )
            return _row(cur.fetchone())


def set_hidden(owner_id: str, item_id: int, hidden: bool = True) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE information_items SET hidden = %s WHERE owner_id = %s AND id = %s RETURNING *",
                (hidden, owner_id, item_id),
            )
            return _row(cur.fetchone())


def delete_item(owner_id: str, item_id: int) -> bool:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM information_items WHERE owner_id = %s AND id = %s",
                (owner_id, item_id),
            )
            return cur.rowcount > 0


def clear_past_unfavorited(owner_id: str, today: date) -> int:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM information_items WHERE owner_id = %s AND favorited = FALSE AND fetched_date <> %s",
                (owner_id, today),
            )
            return int(cur.rowcount or 0)


def update_impact(owner_id: str, item_id: int, impact: str) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE information_items SET impact = %s WHERE owner_id = %s AND id = %s RETURNING *",
                (impact, owner_id, item_id),
            )
            return _row(cur.fetchone())


def update_analysis(owner_id: str, item_id: int, analysis: dict) -> dict | None:
    ensure_schema()
    payload = json.dumps({
        "summary": str(analysis.get("summary") or ""),
        "performance": str(analysis.get("performance") or ""),
        "financial": str(analysis.get("financial") or ""),
    }, ensure_ascii=False)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE information_items SET analysis = %s WHERE owner_id = %s AND id = %s RETURNING *",
                (payload, owner_id, item_id),
            )
            return _row(cur.fetchone())


def list_reviews(owner_id: str, group_id: int) -> list[dict]:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_reviews WHERE owner_id = %s AND group_id = %s ORDER BY review_date DESC, id DESC",
                (owner_id, group_id),
            )
            return [dict(r) for r in cur.fetchall()]


def get_review(owner_id: str, review_id: int) -> dict | None:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM information_reviews WHERE owner_id = %s AND id = %s",
                (owner_id, review_id),
            )
            return _row(cur.fetchone())


def upsert_review(fields: dict) -> dict:
    ensure_schema()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO information_reviews (
                    owner_id, group_id, review_date, success_count, fail_count, sentiment, content, item_refs
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, group_id, review_date) DO UPDATE SET
                    success_count = EXCLUDED.success_count,
                    fail_count = EXCLUDED.fail_count,
                    sentiment = EXCLUDED.sentiment,
                    content = EXCLUDED.content,
                    item_refs = EXCLUDED.item_refs,
                    created_at = NOW()
                RETURNING *
                """,
                (
                    fields["owner_id"], fields["group_id"], fields["review_date"],
                    fields.get("success_count") or 0, fields.get("fail_count") or 0,
                    fields.get("sentiment") or "中性", fields.get("content") or "",
                    json.dumps(fields.get("item_refs") or [], ensure_ascii=False),
                ),
            )
            return dict(cur.fetchone())
