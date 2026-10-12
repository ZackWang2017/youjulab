"""选区翻译：同页上下文、术语锁定、本文翻译记忆、规则校对、通过后再解释。"""
from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from assistant import iter_chat_events
from config import embedding_dim
from office_pdf_embed import embed_texts
from office_pdf_vector import _conn, _vec_literal, ensure_schema, get_index_status, pg_configured
from pdf_glossary import en_key, match_terms

MAX_CONTEXT = 2000
CompleteFn = Callable[[list[dict]], Awaitable[tuple[str, str]]]

_NUM = re.compile(
    r"\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?\)|(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_EN_RUN = re.compile(r"\b[A-Za-z][A-Za-z'’-]{2,}(?:\s+[A-Za-z][A-Za-z'’-]{2,}){5,}\b")
_MEMORY_READY = False


def normalize_number(token: str) -> str:
    raw = token.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    body = raw[1:-1] if negative else raw
    percent = body.endswith("%")
    if percent:
        body = body[:-1]
    body = body.replace(",", "")
    if negative:
        body = "-" + body
    return body + ("%" if percent else "")


def source_numbers(text: str) -> list[str]:
    seen: list[str] = []
    for match in _NUM.finditer(text or ""):
        value = normalize_number(match.group(0))
        if value not in seen:
            seen.append(value)
    return seen


def qa_issues(source: str, translation: str, terms: list[dict[str, Any]]) -> list[str]:
    """只对照选区。上下文里的数字不在 source 中，因此不会被要求出现。"""
    issues: list[str] = []
    target = translation or ""
    found = set(source_numbers(target))
    compact = target.replace(",", "").replace("(", "-").replace(")", "")
    for number in source_numbers(source):
        plain = number[1:] if number.startswith("-") else number
        if number in found or number in compact or plain in compact:
            continue
        issues.append(f"译文缺少选区中的数字 {number}")
    for term in terms:
        zh = term["zh"]
        if term.get("keep"):
            continue
        if zh and zh not in target:
            issues.append(f"术语「{term['en']}」须译为「{zh}」")
        for banned in term.get("forbidden") or []:
            if banned and banned in target:
                issues.append(f"不要把「{term['en']}」译成「{banned}」")
    keep = {term["en_key"] for term in terms if term.get("keep")}
    for run in _EN_RUN.findall(target):
        if en_key(run) in keep:
            continue
        issues.append(f"译文中仍有英文句子：{run[:80]}")
    return issues


def neighbor_context(chunks: list[dict[str, Any]], page: int, selection: str, limit: int = MAX_CONTEXT) -> str:
    """选区所在页及前后各一页中，紧挨选区的文本。选区本身不放进上下文。"""
    window = [c for c in chunks if abs(int(c.get("page") or 0) - page) <= 1]
    window.sort(key=lambda c: (int(c["page"]), int(c.get("ord") or 0)))
    if not window:
        return ""
    needle = (selection or "").strip()
    hit = -1
    if needle:
        for i, chunk in enumerate(window):
            if needle in (chunk.get("text") or ""):
                hit = i
                break
    if hit < 0:
        same = [i for i, chunk in enumerate(window) if int(chunk["page"]) == page]
        hit = same[0] if same else 0
    parts: list[str] = []
    hit_text = window[hit].get("text") or ""
    if needle and needle in hit_text:
        before, _, after = hit_text.partition(needle)
        if before.strip():
            parts.append(before.strip())
        if after.strip():
            parts.append(after.strip())
    elif hit_text.strip():
        parts.append(hit_text.strip())
    if hit > 0:
        parts.insert(0, (window[hit - 1].get("text") or "").strip())
    if hit + 1 < len(window):
        parts.append((window[hit + 1].get("text") or "").strip())
    text = "\n".join(part for part in parts if part)
    return text[:limit]


def render_answer(translation: str, explanation: str, terms: list[dict[str, Any]], issues: list[str]) -> str:
    term_lines = "\n".join(f"- {term['en']}：{term['zh']}" for term in terms) or "无命中术语"
    qa = "通过" if not issues else "\n".join(f"- {item}" for item in issues)
    blocks = [f"## 译文\n{(translation or '').strip()}"]
    if (explanation or "").strip():
        blocks.append(f"## 解释\n{explanation.strip()}")
    blocks.append(f"## 术语\n{term_lines}")
    blocks.append(f"## 校对\n{qa}")
    return "\n\n".join(blocks)


def _term_block(terms: list[dict[str, Any]]) -> str:
    if not terms:
        return "（无命中术语）"
    lines = []
    for term in terms:
        extra = "（保留英文）" if term.get("keep") else ""
        banned = "、".join(term.get("forbidden") or [])
        if banned:
            extra += f"（不要译成：{banned}）"
        lines.append(f"- {term['en']} = {term['zh']}{extra}")
    return "\n".join(lines)


def translation_prompt(
    source: str,
    context: str,
    terms: list[dict[str, Any]],
    memory: list[dict[str, str]],
    issues: list[str] | None = None,
    note: str = "",
) -> list[dict]:
    memory_text = "\n".join(f"原文：{row['source']}\n译文：{row['target']}" for row in memory) or "（无）"
    system = (
        "你是金融财报译者。只输出简体中文译文，不要标题，不要解释。\n"
        "必须使用给定术语。金额、百分比、日期、会计期间与选区原文一致。\n"
        "股票代码和准则号按术语表保留英文。\n"
        "上下文只用于指代、科目口径和表格标题，不要翻译上下文，也不要用上下文里的数字替换选区数字。"
    )
    user = (
        f"术语：\n{_term_block(terms)}\n\n"
        f"本文已确认译文：\n{memory_text}\n\n"
        f"上下文（不要翻译）：\n{context or '（无）'}\n\n"
        f"选区：\n{source}"
    )
    if note.strip():
        user += f"\n\n用户补充：\n{note.strip()}"
    if issues:
        user += "\n\n上一稿未通过校对，请只改正这些问题：\n" + "\n".join(f"- {item}" for item in issues)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def explanation_prompt(source: str, translation: str, context: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "用简体中文简要解释这段财报译文：在说什么、计量或科目口径、和上下文的关系。"
                "不要改写译文，不要引入选区里没有的金额。不要使用网页搜索。只输出解释正文。"
            ),
        },
        {
            "role": "user",
            "content": f"上下文：\n{context or '（无）'}\n\n原文：\n{source}\n\n译文：\n{translation}",
        },
    ]


async def complete_chat(messages: list[dict]) -> tuple[str, str]:
    reasoning = ""
    content = ""
    async for event in iter_chat_events(messages, stream=True, thinking=True):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            reasoning += event["text"]
        else:
            content += event["text"]
    return content.strip(), reasoning.strip()


def ensure_memory_schema() -> None:
    global _MEMORY_READY
    if _MEMORY_READY or not pg_configured():
        return
    ensure_schema()
    dim = embedding_dim()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS pdf_translation_memory (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    page INT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    terms TEXT NOT NULL DEFAULT '',
                    embedding vector({dim}) NOT NULL
                )
                """
            )
        conn.commit()
    _MEMORY_READY = True


def search_memory(document_id: str, query: str, limit: int = 3) -> list[dict[str, str]]:
    if not pg_configured() or not (query or "").strip():
        return []
    try:
        ensure_memory_schema()
        vec = embed_texts([query])[0]
        literal = _vec_literal(vec)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source, target
                    FROM pdf_translation_memory
                    WHERE document_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (document_id, literal, limit),
                )
                rows = cur.fetchall()
        return [{"source": row["source"], "target": row["target"]} for row in rows]
    except Exception:
        return []


def save_memory(
    project_id: str,
    document_id: str,
    page: int,
    source: str,
    target: str,
    terms: list[dict[str, Any]],
) -> None:
    if not pg_configured():
        return
    ensure_memory_schema()
    vec = embed_texts([source])[0]
    names = ",".join(term["en"] for term in terms)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pdf_translation_memory
                    (id, project_id, document_id, page, source, target, terms, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                """,
                (uuid.uuid4().hex, project_id, document_id, page, source, target, names, _vec_literal(vec)),
            )
        conn.commit()


def load_page_chunks(document_id: str, page: int) -> list[dict[str, Any]]:
    if not pg_configured():
        return []
    try:
        status = get_index_status(document_id)
    except Exception:
        return []
    if status.get("status") != "ready":
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT page, ord, text FROM pdf_chunks
                WHERE document_id = %s AND page BETWEEN %s AND %s
                ORDER BY page, ord
                """,
                (document_id, max(1, page - 1), page + 1),
            )
            rows = cur.fetchall()
    return [{"page": int(row["page"]), "ord": int(row["ord"]), "text": row["text"]} for row in rows]


async def translate_segment(
    source: str,
    *,
    page: int,
    document_id: str,
    project_id: str,
    complete: CompleteFn | None = None,
    chunks: list[dict[str, Any]] | None = None,
    memory: list[dict[str, str]] | None = None,
    terms: list[dict[str, Any]] | None = None,
    remember: Callable[[str, str, list[dict[str, Any]]], None] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """唯一翻译入口。选区调用一次；以后整份 PDF 按页块重复调用。"""
    text = (source or "").strip()
    run = complete or complete_chat
    found = terms if terms is not None else match_terms(text, project_id=project_id)
    page_chunks = chunks if chunks is not None else load_page_chunks(document_id, page)
    context = neighbor_context(page_chunks, page, text)
    recalled = memory if memory is not None else search_memory(document_id, text)
    draft, reasoning = await run(translation_prompt(text, context, found, recalled, note=note))
    issues = qa_issues(text, draft, found)
    if issues:
        repaired, more = await run(translation_prompt(text, context, found, recalled, issues, note))
        reasoning = (reasoning + "\n" + more).strip()
        draft = repaired or draft
        issues = qa_issues(text, draft, found)
    explanation = ""
    if not issues:
        explanation, more = await run(explanation_prompt(text, draft, context))
        reasoning = (reasoning + "\n" + more).strip()
        if remember is not None:
            remember(text, draft, found)
        elif memory is None:
            try:
                save_memory(project_id, document_id, page, text, draft, found)
            except Exception:
                pass
    return {
        "translation": draft,
        "explanation": explanation,
        "terms": found,
        "issues": issues,
        "reasoning": reasoning,
        "context": context,
        "answer": render_answer(draft, explanation, found, issues),
    }


async def iter_pdf_translate_events(
    *,
    selection: str,
    page: int,
    document_id: str,
    project_id: str,
    note: str = "",
) -> AsyncIterator[dict]:
    result = await translate_segment(
        selection, page=page, document_id=document_id, project_id=project_id, note=note,
    )
    reasoning = result["reasoning"]
    if reasoning:
        yield {"type": "delta", "text": reasoning, "part": "reasoning"}
    answer = result["answer"]
    if answer:
        yield {"type": "delta", "text": answer, "part": "answer"}
    from config import model_name
    yield {
        "type": "done",
        "answer": answer,
        "thinking": reasoning,
        "model_name": model_name() or "模型",
    }
