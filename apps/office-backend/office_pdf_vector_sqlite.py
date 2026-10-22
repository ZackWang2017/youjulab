"""本机 sqlite-vec：PDF 向量存本地，检索本地完成。"""
from __future__ import annotations

import sqlite3
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import sqlite_vec

from config import embedding_dim, vector_sqlite_path
from office_pdf_embed import EmbedError, embed_texts

DEFAULT_TOP_K = 8
EmbedFn = Callable[[list[str]], list[list[float]]]

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_CONN_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _db_path() -> Path:
    return vector_sqlite_path()


def _connect() -> sqlite3.Connection:
    global _CONN
    with _CONN_LOCK:
        if _CONN is None:
            path = _db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _CONN = sqlite3.connect(str(path), check_same_thread=False)
            _CONN.row_factory = sqlite3.Row
            _CONN.enable_load_extension(True)
            sqlite_vec.load(_CONN)
            _CONN.enable_load_extension(False)
            _CONN.execute("PRAGMA journal_mode=WAL")
        return _CONN


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        dim = embedding_dim()
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pdf_index (
                document_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                file_mtime INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                empty INTEGER NOT NULL DEFAULT 0,
                layout_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pdf_chunks (
                document_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                page INTEGER NOT NULL,
                ord INTEGER NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (document_id, chunk_id),
                FOREIGN KEY (document_id) REFERENCES pdf_index(document_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS pdf_layout_blocks (
                document_id TEXT NOT NULL,
                block_id TEXT NOT NULL,
                page INTEGER NOT NULL,
                ord INTEGER NOT NULL,
                text TEXT NOT NULL,
                font_size REAL NOT NULL DEFAULT 0,
                bold INTEGER NOT NULL DEFAULT 0,
                x0 REAL NOT NULL DEFAULT 0,
                y0 REAL NOT NULL DEFAULT 0,
                x1 REAL NOT NULL DEFAULT 0,
                y1 REAL NOT NULL DEFAULT 0,
                indent REAL NOT NULL DEFAULT 0,
                y_ratio REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (document_id, block_id),
                FOREIGN KEY (document_id) REFERENCES pdf_index(document_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS pdf_chunks_document_ord ON pdf_chunks (document_id, ord);
            CREATE INDEX IF NOT EXISTS pdf_layout_blocks_document_ord ON pdf_layout_blocks (document_id, ord);
            CREATE TABLE IF NOT EXISTS finance_terms (
                en_key TEXT PRIMARY KEY,
                en TEXT NOT NULL,
                zh TEXT NOT NULL,
                forbidden TEXT NOT NULL DEFAULT '',
                keep_en INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS project_terms (
                project_id TEXT NOT NULL,
                en_key TEXT NOT NULL,
                en TEXT NOT NULL,
                zh TEXT NOT NULL,
                forbidden TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (project_id, en_key)
            );
            """
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS pdf_chunk_vec USING vec0(document_id TEXT, chunk_id TEXT, embedding float[{dim}])"
        )
        conn.commit()
        _SCHEMA_READY = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *[float(x) for x in vec])


def get_index_status(document_id: str) -> dict[str, Any]:
    ensure_schema()
    conn = _connect()
    row = conn.execute("SELECT * FROM pdf_index WHERE document_id = ?", (document_id,)).fetchone()
    if not row:
        return {"document_id": document_id, "status": "missing", "page_count": 0, "chunk_count": 0}
    return {
        "document_id": row["document_id"],
        "status": row["status"],
        "page_count": row["page_count"],
        "chunk_count": row["chunk_count"],
        "empty": bool(row["empty"]),
        "error": row["error"] or "",
        "updated_at": row["updated_at"],
        "layout_version": int(row["layout_version"] or 0),
    }


def delete_document_index(document_id: str) -> None:
    ensure_schema()
    conn = _connect()
    conn.execute("DELETE FROM pdf_chunk_vec WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pdf_chunks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pdf_layout_blocks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pdf_index WHERE document_id = ?", (document_id,))
    conn.commit()


def ensure_layout_blocks(document_id: str, path: str | Path, version: int = 1) -> list[dict[str, Any]]:
    from office_pdf_vector import _doc_lock, extract_layout_blocks

    ensure_schema()
    status = get_index_status(document_id)
    if status.get("status") != "ready":
        raise ValueError(f"文档尚未完成索引（状态：{status.get('status')}）。")
    if status.get("empty"):
        raise ValueError("未提取到可用文本层，扫描件暂不支持生成大纲。")
    pdf_path = Path(path)
    with _doc_lock(document_id):
        conn = _connect()
        current = conn.execute(
            "SELECT layout_version FROM pdf_index WHERE document_id = ?", (document_id,)
        ).fetchone()
        if current and int(current["layout_version"] or 0) >= version:
            rows = conn.execute(
                "SELECT * FROM pdf_layout_blocks WHERE document_id = ? ORDER BY ord",
                (document_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        blocks = extract_layout_blocks(pdf_path)
        if not blocks:
            raise ValueError("PDF 没有可用于识别结构的原生文本。")
        conn.execute("DELETE FROM pdf_layout_blocks WHERE document_id = ?", (document_id,))
        for item in blocks:
            conn.execute(
                """INSERT INTO pdf_layout_blocks
                   (document_id, block_id, page, ord, text, font_size, bold, x0, y0, x1, y1, indent, y_ratio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id, item["block_id"], item["page"], item["ord"], item["text"],
                    item["font_size"], 1 if item["bold"] else 0, item["x0"], item["y0"],
                    item["x1"], item["y1"], item["indent"], item["y_ratio"],
                ),
            )
        conn.execute(
            "UPDATE pdf_index SET layout_version = ?, updated_at = ? WHERE document_id = ?",
            (version, _now(), document_id),
        )
        conn.commit()
        return blocks


def ensure_index(
    *,
    document_id: str,
    user_id: str,
    path: str | Path,
    embed: EmbedFn | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from office_pdf_vector import _doc_lock, _file_meta, extract_chunks

    ensure_schema()
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise ValueError("PDF 文件不存在。")
    mtime, content_hash = _file_meta(pdf_path)
    embed_fn = embed or embed_texts
    conn = _connect()

    with _doc_lock(document_id):
        current = get_index_status(document_id)
        if not force and current.get("status") == "ready":
            row = conn.execute(
                "SELECT file_mtime, content_hash FROM pdf_index WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if row and int(row["file_mtime"]) == mtime and row["content_hash"] == content_hash:
                return get_index_status(document_id)

        conn.execute(
            """
            INSERT INTO pdf_index (document_id, user_id, file_mtime, content_hash, status, page_count, chunk_count, error, empty, layout_version, updated_at)
            VALUES (?, ?, ?, ?, 'indexing', 0, 0, NULL, 0, 0, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                user_id=excluded.user_id,
                file_mtime=excluded.file_mtime,
                content_hash=excluded.content_hash,
                status='indexing',
                error=NULL,
                updated_at=excluded.updated_at
            """,
            (document_id, user_id, mtime, content_hash, _now()),
        )
        conn.execute("DELETE FROM pdf_chunk_vec WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM pdf_chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM pdf_layout_blocks WHERE document_id = ?", (document_id,))
        conn.commit()

        try:
            page_count, chunks = extract_chunks(pdf_path)
            if not chunks:
                conn.execute(
                    """UPDATE pdf_index SET status='ready', page_count=?, chunk_count=0, layout_version=0,
                       empty=1, error=?, updated_at=? WHERE document_id=?""",
                    (page_count, "未提取到可检索文本（可能是扫描件，需要 OCR）。", _now(), document_id),
                )
                conn.commit()
                return get_index_status(document_id)

            vectors = embed_fn([c["text"] for c in chunks])
            if len(vectors) != len(chunks):
                raise EmbedError("embedding 条数与文本块不一致。")

            for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
                chunk_id = f"p{chunk['page']}-{i}"
                conn.execute(
                    "INSERT INTO pdf_chunks (document_id, chunk_id, page, ord, text) VALUES (?, ?, ?, ?, ?)",
                    (document_id, chunk_id, chunk["page"], i, chunk["text"]),
                )
                conn.execute(
                    "INSERT INTO pdf_chunk_vec (document_id, chunk_id, embedding) VALUES (?, ?, ?)",
                    (document_id, chunk_id, _pack(vec)),
                )
            conn.execute(
                """UPDATE pdf_index SET status='ready', page_count=?, chunk_count=?, layout_version=0,
                   empty=0, error=NULL, updated_at=? WHERE document_id=?""",
                (page_count, len(chunks), _now(), document_id),
            )
            conn.commit()
            return get_index_status(document_id)
        except Exception as exc:
            conn.execute(
                "UPDATE pdf_index SET status='failed', error=?, updated_at=? WHERE document_id=?",
                (str(exc)[:800], _now(), document_id),
            )
            conn.commit()
            raise


def search_chunks(
    *,
    document_id: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    embed: EmbedFn | None = None,
) -> list[dict[str, Any]]:
    ensure_schema()
    ask = (query or "").strip()
    if not ask:
        raise ValueError("检索问题不能为空。")
    k = max(1, min(int(top_k or DEFAULT_TOP_K), 20))
    status = get_index_status(document_id)
    if status.get("status") != "ready":
        raise ValueError(f"文档尚未完成索引（状态：{status.get('status')}）。")
    if status.get("empty") or not status.get("chunk_count"):
        return []
    embed_fn = embed or embed_texts
    vec = embed_fn([ask])[0]
    conn = _connect()
    rows = conn.execute(
        """
        SELECT c.page AS page, c.text AS text, v.distance AS distance
        FROM pdf_chunk_vec v
        JOIN pdf_chunks c ON c.document_id = v.document_id AND c.chunk_id = v.chunk_id
        WHERE v.embedding MATCH ? AND k = ? AND v.document_id = ?
        ORDER BY v.distance
        """,
        (_pack(vec), k, document_id),
    ).fetchall()
    return [
        {"page": int(row["page"]), "text": row["text"], "score": float(1.0 / (1.0 + float(row["distance"] or 0)))}
        for row in rows
    ]


def sqlite_configured() -> bool:
    return True


def glossary_conn() -> sqlite3.Connection:
    ensure_schema()
    return _connect()
