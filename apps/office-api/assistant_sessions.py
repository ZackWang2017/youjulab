"""表格协作助手会话：校验与对外字段。"""
from __future__ import annotations

from assistant import clip_turn_content

MAX_SESSION_TURNS = 40
MAX_TURN_CHARS = 24000
TITLE_LEN = 40


def session_title(turns: list[dict]) -> str:
    for item in turns:
        if item.get("role") == "user":
            text = (item.get("content") or "").strip().replace("\n", " ")
            if text:
                return text[:TITLE_LEN]
    return "新对话"


def validate_session_turns(turns: list[dict]) -> list[dict]:
    if not isinstance(turns, list):
        raise ValueError("会话轮次格式无效。")
    recent = turns[-MAX_SESSION_TURNS:] if len(turns) > MAX_SESSION_TURNS else turns
    cleaned: list[dict] = []
    for item in recent:
        if not isinstance(item, dict):
            raise ValueError("会话轮次格式无效。")
        role = item.get("role")
        content = clip_turn_content(item.get("content") or "")
        if role not in ("user", "assistant"):
            raise ValueError("会话轮次角色无效。")
        if not content:
            raise ValueError("会话轮次内容无效。")
        cleaned.append({"role": role, "content": content})
    return cleaned


def public_session_list_item(session: dict) -> dict:
    turns = session.get("turns") if isinstance(session.get("turns"), list) else []
    return {
        "id": session.get("id"),
        "document_id": session.get("document_id"),
        "title": session.get("title") or session_title(turns),
        "updated_at": session.get("updated_at"),
        "created_at": session.get("created_at"),
        "turn_count": len(turns),
    }


def public_session(session: dict) -> dict:
    turns = validate_session_turns(session.get("turns") or [])
    anchor = session.get("anchor") if isinstance(session.get("anchor"), dict) else {}
    return {
        **public_session_list_item({**session, "turns": turns}),
        "anchor": anchor,
        "turns": turns,
        "summary": (session.get("summary") or "").strip(),
    }
