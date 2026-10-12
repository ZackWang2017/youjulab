"""每日资讯：Markdown → TipTap JSON 节点。"""
from __future__ import annotations

import re


def _inline_nodes(text: str) -> list[dict]:
    """粗略解析 **bold** / *em* / `code` 为 TipTap text marks。"""
    if not text:
        return []
    pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")
    nodes: list[dict] = []
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            nodes.append({"type": "text", "text": text[pos:match.start()]})
        token = match.group(0)
        if token.startswith("**") and token.endswith("**"):
            nodes.append({"type": "text", "text": token[2:-2], "marks": [{"type": "bold"}]})
        elif token.startswith("*") and token.endswith("*"):
            nodes.append({"type": "text", "text": token[1:-1], "marks": [{"type": "italic"}]})
        elif token.startswith("`") and token.endswith("`"):
            nodes.append({"type": "text", "text": token[1:-1], "marks": [{"type": "code"}]})
        else:
            nodes.append({"type": "text", "text": token})
        pos = match.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes or [{"type": "text", "text": ""}]


def markdown_to_tiptap_nodes(src: str) -> list[dict]:
    text = (src or "").replace("\r\n", "\n").strip()
    if not text:
        return [{"type": "paragraph"}]
    lines = text.split("\n")
    nodes: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            nodes.append({
                "type": "heading",
                "attrs": {"level": level},
                "content": _inline_nodes(heading.group(2).strip()),
            })
            i += 1
            continue
        if re.match(r"^[-*]\s+", line):
            items: list[dict] = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                item_text = re.sub(r"^[-*]\s+", "", lines[i]).strip()
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _inline_nodes(item_text)}],
                })
                i += 1
            nodes.append({"type": "bulletList", "content": items})
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i]).strip()
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _inline_nodes(item_text)}],
                })
                i += 1
            nodes.append({"type": "orderedList", "content": items})
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,3})\s+", lines[i]) \
                and not re.match(r"^[-*]\s+", lines[i]) and not re.match(r"^\d+\.\s+", lines[i]):
            para.append(lines[i])
            i += 1
        joined = " ".join(p.strip() for p in para)
        nodes.append({"type": "paragraph", "content": _inline_nodes(joined)})
    return nodes or [{"type": "paragraph"}]


def merge_into_document(doc: dict | None, fragment_nodes: list[dict], order: str) -> dict:
    """order: newest_first | newest_last"""
    base = doc if isinstance(doc, dict) and doc.get("type") == "doc" else {"type": "doc", "content": []}
    existing = list(base.get("content") or [])
    # 去掉尾部空段落，避免堆积
    while existing and existing[-1].get("type") == "paragraph" and not existing[-1].get("content"):
        existing.pop()
    if order == "newest_last":
        content = existing + fragment_nodes + [{"type": "paragraph"}]
    else:
        content = fragment_nodes + [{"type": "paragraph"}] + existing
    return {"type": "doc", "content": content or [{"type": "paragraph"}]}
