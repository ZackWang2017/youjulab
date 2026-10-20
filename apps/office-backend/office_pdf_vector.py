"""办公 PDF 向量索引：pymupdf 仅在未就绪/文件变更时解析一次，写入 Postgres/pgvector。"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any, Callable

import pymupdf
import psycopg
from psycopg.rows import dict_row

from config import embedding_dim, pg_url
from office_pdf_embed import EmbedError, embed_texts

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 120
MAX_CHUNK_CHARS = 2000
DEFAULT_TOP_K = 8
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

EmbedFn = Callable[[list[str]], list[list[float]]]


def pg_configured() -> bool:
    return bool(pg_url())


def _conn():
    url = pg_url()
    if not url:
        raise ValueError("请先配置 OFFICE_PG_URL（含 pgvector 的 Postgres）。")
    return psycopg.connect(url, row_factory=dict_row)


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        dim = embedding_dim()
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pdf_index (
                        document_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        file_mtime BIGINT NOT NULL,
                        content_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        page_count INT NOT NULL DEFAULT 0,
                        chunk_count INT NOT NULL DEFAULT 0,
                        error TEXT,
                        empty BOOLEAN NOT NULL DEFAULT FALSE,
                        layout_version INT NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS pdf_chunks (
                        document_id TEXT NOT NULL REFERENCES pdf_index(document_id) ON DELETE CASCADE,
                        chunk_id TEXT NOT NULL,
                        page INT NOT NULL,
                        ord INT NOT NULL,
                        text TEXT NOT NULL,
                        embedding vector({dim}) NOT NULL,
                        PRIMARY KEY (document_id, chunk_id)
                    )
                    """
                )
                cur.execute("ALTER TABLE pdf_index ADD COLUMN IF NOT EXISTS layout_version INT NOT NULL DEFAULT 0")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pdf_layout_blocks (
                        document_id TEXT NOT NULL REFERENCES pdf_index(document_id) ON DELETE CASCADE,
                        block_id TEXT NOT NULL,
                        page INT NOT NULL,
                        ord INT NOT NULL,
                        text TEXT NOT NULL,
                        font_size DOUBLE PRECISION NOT NULL DEFAULT 0,
                        bold BOOLEAN NOT NULL DEFAULT FALSE,
                        x0 DOUBLE PRECISION NOT NULL DEFAULT 0,
                        y0 DOUBLE PRECISION NOT NULL DEFAULT 0,
                        x1 DOUBLE PRECISION NOT NULL DEFAULT 0,
                        y1 DOUBLE PRECISION NOT NULL DEFAULT 0,
                        indent DOUBLE PRECISION NOT NULL DEFAULT 0,
                        y_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
                        PRIMARY KEY (document_id, block_id)
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS pdf_layout_blocks_document_ord ON pdf_layout_blocks (document_id, ord)")
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS pdf_chunks_document_ord
                    ON pdf_chunks (document_id, ord)
                    """
                )
            conn.commit()
        _SCHEMA_READY = True


def _doc_lock(document_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(document_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[document_id] = lock
        return lock


def _file_meta(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    st = path.stat()
    return int(st.st_mtime_ns), hashlib.sha256(data).hexdigest()


def _split_page(page: int, text: str) -> list[dict[str, Any]]:
    clean = re.sub(r"[ \t]+\n", "\n", text or "").strip()
    if not clean:
        return []
    if len(clean) <= CHUNK_SIZE:
        return [{"page": page, "text": clean[:MAX_CHUNK_CHARS]}]
    parts: list[dict[str, Any]] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + CHUNK_SIZE)
        if end < len(clean):
            cut = clean.rfind("\n", start + CHUNK_SIZE // 2, end)
            if cut > start:
                end = cut
        chunk = clean[start:end].strip()
        if chunk:
            parts.append({"page": page, "text": chunk[:MAX_CHUNK_CHARS]})
        if end >= len(clean):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return parts


def extract_chunks(path: Path) -> tuple[int, list[dict[str, Any]]]:
    doc = pymupdf.open(path)
    try:
        chunks: list[dict[str, Any]] = []
        for i, page in enumerate(doc):
            chunks.extend(_split_page(i + 1, page.get_text("text")))
        return doc.page_count, chunks
    finally:
        doc.close()


def extract_layout_blocks(path: Path) -> list[dict[str, Any]]:
    """Extract native text lines in reading order without changing vector chunks."""
    doc = pymupdf.open(path)
    try:
        result: list[dict[str, Any]] = []
        order = 0
        for page_no, page in enumerate(doc, 1):
            raw = page.get_text("rawdict")
            height = max(1.0, float(page.rect.height))
            for block in raw.get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    spans = line.get("spans") or []
                    span_texts = [
                        str(span.get("text") or "".join(str(char.get("c") or "") for char in span.get("chars") or []))
                        for span in spans
                    ]
                    spans = [span for span, value in zip(spans, span_texts) if value.strip()]
                    text = "".join(value for value in span_texts if value.strip()).strip()
                    if not text:
                        continue
                    bbox = line.get("bbox") or [0, 0, 0, 0]
                    size = max((float(span.get("size") or 0) for span in spans), default=0.0)
                    bold = any("bold" in str(span.get("font") or "").lower() or int(span.get("flags") or 0) & 16 for span in spans)
                    result.append({
                        "block_id": f"p{page_no}-l{order}", "page": page_no, "ord": order, "text": text,
                        "font_size": size, "bold": bold, "x0": float(bbox[0]), "y0": float(bbox[1]),
                        "x1": float(bbox[2]), "y1": float(bbox[3]), "indent": float(bbox[0]),
                        "y_ratio": max(0.0, min(1.0, float(bbox[1]) / height)),
                    })
                    order += 1
        return result
    finally:
        doc.close()


def get_index_status(document_id: str) -> dict[str, Any]:
    ensure_schema()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pdf_index WHERE document_id = %s", (document_id,))
            row = cur.fetchone()
    if not row:
        return {"document_id": document_id, "status": "missing", "page_count": 0, "chunk_count": 0}
    return {
        "document_id": row["document_id"],
        "status": row["status"],
        "page_count": row["page_count"],
        "chunk_count": row["chunk_count"],
        "empty": bool(row.get("empty")),
        "error": row.get("error") or "",
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "layout_version": int(row.get("layout_version") or 0),
    }


def delete_document_index(document_id: str) -> None:
    if not pg_configured():
        return
    ensure_schema()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pdf_index WHERE document_id = %s", (document_id,))
        conn.commit()


def ensure_layout_blocks(document_id: str, path: str | Path, version: int = 1) -> list[dict[str, Any]]:
    """Lazily persist layout metadata; this path never generates embeddings."""
    ensure_schema()
    status = get_index_status(document_id)
    if status.get("status") != "ready":
        raise ValueError(f"文档尚未完成索引（状态：{status.get('status')}）。")
    if status.get("empty"):
        raise ValueError("未提取到可用文本层，扫描件暂不支持生成大纲。")
    pdf_path = Path(path)
    with _doc_lock(document_id):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT layout_version FROM pdf_index WHERE document_id = %s", (document_id,))
                current = cur.fetchone()
                if current and int(current.get("layout_version") or 0) >= version:
                    cur.execute("SELECT * FROM pdf_layout_blocks WHERE document_id = %s ORDER BY ord", (document_id,))
                    return list(cur.fetchall())
        blocks = extract_layout_blocks(pdf_path)
        if not blocks:
            raise ValueError("PDF 没有可用于识别结构的原生文本。")
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pdf_layout_blocks WHERE document_id = %s", (document_id,))
                for item in blocks:
                    cur.execute(
                        """INSERT INTO pdf_layout_blocks
                           (document_id, block_id, page, ord, text, font_size, bold, x0, y0, x1, y1, indent, y_ratio)
                           VALUES (%(document_id)s, %(block_id)s, %(page)s, %(ord)s, %(text)s, %(font_size)s, %(bold)s,
                                   %(x0)s, %(y0)s, %(x1)s, %(y1)s, %(indent)s, %(y_ratio)s)""",
                        {"document_id": document_id, **item},
                    )
                cur.execute("UPDATE pdf_index SET layout_version = %s, updated_at = NOW() WHERE document_id = %s", (version, document_id))
            conn.commit()
        return blocks


def _vec_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in values) + "]"


def ensure_index(
    *,
    document_id: str,
    user_id: str,
    path: str | Path,
    embed: EmbedFn | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """未就绪或文件变更时解析入库；已就绪且未变则直接返回。"""
    ensure_schema()
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise ValueError("PDF 文件不存在。")
    mtime, content_hash = _file_meta(pdf_path)
    embed_fn = embed or embed_texts

    with _doc_lock(document_id):
        current = get_index_status(document_id)
        if (
            not force
            and current.get("status") == "ready"
        ):
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT file_mtime, content_hash FROM pdf_index WHERE document_id = %s",
                        (document_id,),
                    )
                    row = cur.fetchone()
            if row and int(row["file_mtime"]) == mtime and row["content_hash"] == content_hash:
                return get_index_status(document_id)

        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pdf_index (document_id, user_id, file_mtime, content_hash, status, page_count, chunk_count, error, empty, updated_at)
                    VALUES (%s, %s, %s, %s, 'indexing', 0, 0, NULL, FALSE, NOW())
                    ON CONFLICT (document_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        file_mtime = EXCLUDED.file_mtime,
                        content_hash = EXCLUDED.content_hash,
                        status = 'indexing',
                        error = NULL,
                        updated_at = NOW()
                    """,
                    (document_id, user_id, mtime, content_hash),
                )
                cur.execute("DELETE FROM pdf_chunks WHERE document_id = %s", (document_id,))
                cur.execute("DELETE FROM pdf_layout_blocks WHERE document_id = %s", (document_id,))
            conn.commit()

        try:
            page_count, chunks = extract_chunks(pdf_path)
            if not chunks:
                with _conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pdf_index SET status = 'ready', page_count = %s, chunk_count = 0, layout_version = 0,
                                   empty = TRUE, error = '未提取到可检索文本（可能是扫描件，需要 OCR）。', updated_at = NOW()
                            WHERE document_id = %s
                            """,
                            (page_count, document_id),
                        )
                    conn.commit()
                return get_index_status(document_id)

            vectors = embed_fn([c["text"] for c in chunks])
            if len(vectors) != len(chunks):
                raise EmbedError("embedding 条数与文本块不一致。")

            with _conn() as conn:
                with conn.cursor() as cur:
                    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
                        chunk_id = f"p{chunk['page']}-{i}"
                        cur.execute(
                            """
                            INSERT INTO pdf_chunks (document_id, chunk_id, page, ord, text, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s::vector)
                            """,
                            (document_id, chunk_id, chunk["page"], i, chunk["text"], _vec_literal(vec)),
                        )
                    cur.execute(
                        """
                        UPDATE pdf_index SET status = 'ready', page_count = %s, chunk_count = %s, layout_version = 0,
                               empty = FALSE, error = NULL, updated_at = NOW()
                        WHERE document_id = %s
                        """,
                        (page_count, len(chunks), document_id),
                    )
                conn.commit()
            return get_index_status(document_id)
        except Exception as exc:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE pdf_index SET status = 'failed', error = %s, updated_at = NOW()
                        WHERE document_id = %s
                        """,
                        (str(exc)[:800], document_id),
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
    literal = _vec_literal(vec)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT page, text, 1 - (embedding <=> %s::vector) AS score
                FROM pdf_chunks
                WHERE document_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (literal, document_id, literal, k),
            )
            rows = cur.fetchall()
    return [
        {"page": int(row["page"]), "text": row["text"], "score": float(row["score"] or 0)}
        for row in rows
    ]
