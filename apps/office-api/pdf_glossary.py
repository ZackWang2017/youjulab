"""财报术语：内置词表与项目词表。项目词覆盖同名内置词。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from office_pdf_vector import _conn, pg_configured

HERE = Path(__file__).resolve().parent
GLOSSARY_PATH = HERE / "data" / "finance_glossary.json"
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
_SCHEMA_READY = False


def en_key(text: str) -> str:
    raw = (text or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return " ".join(_WORD.findall(raw))


def load_builtin() -> list[dict[str, Any]]:
    rows = json.loads(GLOSSARY_PATH.read_text())
    out: list[dict[str, Any]] = []
    for row in rows:
        key = en_key(str(row.get("en") or ""))
        zh = str(row.get("zh") or "").strip()
        if not key or not zh:
            continue
        forbidden = row.get("forbidden") or []
        if isinstance(forbidden, str):
            forbidden = [part.strip() for part in forbidden.split(",") if part.strip()]
        out.append({
            "en": str(row["en"]).strip(),
            "en_key": key,
            "zh": zh,
            "forbidden": [str(item).strip() for item in forbidden if str(item).strip()],
            "keep": bool(row.get("keep")),
            "source": "builtin",
        })
    return out


def ensure_term_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or not pg_configured():
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_terms (
                    en_key TEXT PRIMARY KEY,
                    en TEXT NOT NULL,
                    zh TEXT NOT NULL,
                    forbidden TEXT NOT NULL DEFAULT '',
                    keep_en BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS project_terms (
                    project_id TEXT NOT NULL,
                    en_key TEXT NOT NULL,
                    en TEXT NOT NULL,
                    zh TEXT NOT NULL,
                    forbidden TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (project_id, en_key)
                )
                """
            )
            for term in load_builtin():
                cur.execute(
                    """
                    INSERT INTO finance_terms (en_key, en, zh, forbidden, keep_en)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (en_key) DO UPDATE SET
                        en = EXCLUDED.en,
                        zh = EXCLUDED.zh,
                        forbidden = EXCLUDED.forbidden,
                        keep_en = EXCLUDED.keep_en
                    """,
                    (term["en_key"], term["en"], term["zh"], ",".join(term["forbidden"]), term["keep"]),
                )
        conn.commit()
    _SCHEMA_READY = True


def _split_forbidden(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def list_project_terms(project_id: str) -> list[dict[str, Any]]:
    if not pg_configured():
        raise ValueError("请先配置 OFFICE_PG_URL。")
    ensure_term_schema()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT en, zh, forbidden FROM project_terms
                WHERE project_id = %s ORDER BY en_key
                """,
                (project_id,),
            )
            rows = cur.fetchall()
    return [
        {"en": row["en"], "zh": row["zh"], "forbidden": _split_forbidden(row["forbidden"])}
        for row in rows
    ]


def upsert_project_term(project_id: str, en: str, zh: str, forbidden: list[str] | None = None) -> dict[str, Any]:
    if not pg_configured():
        raise ValueError("请先配置 OFFICE_PG_URL。")
    key = en_key(en)
    target = (zh or "").strip()
    if not key or not target:
        raise ValueError("请填写英文术语和中文译法。")
    if len(en) > 120 or len(target) > 120:
        raise ValueError("术语过长。")
    banned = [item.strip() for item in (forbidden or []) if item and item.strip()]
    ensure_term_schema()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO project_terms (project_id, en_key, en, zh, forbidden)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (project_id, en_key) DO UPDATE SET
                    en = EXCLUDED.en, zh = EXCLUDED.zh, forbidden = EXCLUDED.forbidden
                """,
                (project_id, key, en.strip(), target, ",".join(banned)),
            )
        conn.commit()
    return {"en": en.strip(), "zh": target, "forbidden": banned}


def delete_project_term(project_id: str, en: str) -> None:
    if not pg_configured():
        raise ValueError("请先配置 OFFICE_PG_URL。")
    ensure_term_schema()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM project_terms WHERE project_id = %s AND en_key = %s",
                (project_id, en_key(en)),
            )
        conn.commit()


def project_term_rows(project_id: str) -> list[dict[str, Any]]:
    if not project_id or not pg_configured():
        return []
    try:
        ensure_term_schema()
    except Exception:
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT en, en_key, zh, forbidden FROM project_terms WHERE project_id = %s",
                (project_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "en": row["en"],
            "en_key": row["en_key"],
            "zh": row["zh"],
            "forbidden": _split_forbidden(row["forbidden"]),
            "keep": False,
            "source": "project",
        }
        for row in rows
    ]


def active_terms(project_id: str = "") -> list[dict[str, Any]]:
    merged = {term["en_key"]: term for term in load_builtin()}
    for term in project_term_rows(project_id):
        merged[term["en_key"]] = term
    return list(merged.values())


def _spans(text_key: str, term_key: str) -> list[tuple[int, int]]:
    if not term_key:
        return []
    found: list[tuple[int, int]] = []
    start = 0
    while True:
        at = text_key.find(term_key, start)
        if at < 0:
            break
        before = at == 0 or text_key[at - 1] == " "
        after_at = at + len(term_key)
        after = after_at == len(text_key) or text_key[after_at] == " "
        if before and after:
            found.append((at, after_at))
        start = at + 1
    return found


def match_terms(text: str, terms: list[dict[str, Any]] | None = None, project_id: str = "") -> list[dict[str, Any]]:
    """按长短语优先匹配。复数、连字符与大小写都算命中。"""
    catalog = terms if terms is not None else active_terms(project_id)
    text_key = en_key(text)
    if not text_key:
        return []
    ordered = sorted(catalog, key=lambda term: len(term["en_key"]), reverse=True)
    taken: list[tuple[int, int]] = []
    hits: list[dict[str, Any]] = []
    for term in ordered:
        keys = {term["en_key"]}
        if term["en_key"].endswith("s") and not term["en_key"].endswith("ss"):
            keys.add(term["en_key"][:-1])
        else:
            keys.add(term["en_key"] + "s")
        span = None
        for key in keys:
            for start, end in _spans(text_key, key):
                if any(not (end <= left or start >= right) for left, right in taken):
                    continue
                span = (start, end)
                break
            if span:
                break
        if not span:
            continue
        taken.append(span)
        hits.append(term)
    return hits
