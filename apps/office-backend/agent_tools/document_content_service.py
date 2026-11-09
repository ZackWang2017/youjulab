"""办公文档的 TipTap JSON 与 Markdown 互转，以及块级格式。编辑器使用 StarterKit。"""
from __future__ import annotations

import re

BLOCK_TYPES = ("paragraph", "h1", "h2", "h3", "bullet", "ordered", "blockquote", "code")
_INLINE = re.compile(r"`([^`]+)`|~~(.+?)~~|\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|_(.+?)_")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_ORDERED = re.compile(r"^(\d+)\.\s+(.*)$")
_HR = re.compile(r"^ {0,3}(?:---+|\*\*\*+|___+)[ \t]*$")
_CIRCLE = re.compile(r"^○\s*(.*)$")


def empty_document() -> dict:
    return {"type": "doc", "content": [{"type": "paragraph"}]}


def document_from_asset(asset: dict) -> dict:
    content = asset.get("content")
    if isinstance(content, dict) and content.get("type") == "doc":
        content.setdefault("content", [{"type": "paragraph"}])
        return content
    return empty_document()


def markdown_to_document(markdown: str) -> dict:
    blocks = _parse_blocks(markdown or "")
    return {"type": "doc", "content": blocks or [{"type": "paragraph"}]}


def document_to_markdown(doc: dict) -> str:
    parts = [_block_to_markdown(block) for block in doc.get("content") or []]
    return "\n\n".join(part for part in parts if part).strip()


def describe_blocks(doc: dict) -> list[dict]:
    described = []
    for index, block in enumerate(doc.get("content") or []):
        marks = sorted(_marks_in(block))
        described.append({
            "index": index,
            "type": _public_type(block),
            "text": _plain_text(block),
            "marks": marks,
        })
    return described


def replace_document(doc: dict, markdown: str) -> dict:
    return markdown_to_document(markdown)


def append_markdown(doc: dict, markdown: str) -> dict:
    extra = _parse_blocks(markdown)
    if not extra:
        raise ValueError("没有可追加的内容。")
    content = _without_blank_start(doc)
    content.extend(extra)
    return {"type": "doc", "content": content}


def prepend_markdown(doc: dict, markdown: str) -> dict:
    extra = _parse_blocks(markdown)
    if not extra:
        raise ValueError("没有可插入的内容。")
    content = _without_blank_start(doc)
    return {"type": "doc", "content": extra + content}


def _without_blank_start(doc: dict) -> list:
    content = list(doc.get("content") or [])
    if len(content) == 1 and content[0].get("type") == "paragraph" and not _plain_text(content[0]).strip():
        return []
    return content


def insert_markdown(doc: dict, markdown: str, block_index: int) -> dict:
    extra = _parse_blocks(markdown)
    if not extra:
        raise ValueError("没有可插入的内容。")
    content = list(doc.get("content") or [])
    index = _check_index(content, block_index, allow_end=True)
    content[index:index] = extra
    return {"type": "doc", "content": content}


def update_block(doc: dict, markdown: str, block_index: int) -> dict:
    extra = _parse_blocks(markdown)
    if not extra:
        raise ValueError("没有可替换的内容。")
    content = list(doc.get("content") or [])
    index = _check_index(content, block_index, allow_end=False)
    content[index:index + 1] = extra
    return {"type": "doc", "content": content}


def format_block(
    doc: dict,
    block_index: int,
    block_type: str = "",
    bold: bool | None = None,
    italic: bool | None = None,
    strike: bool | None = None,
    code: bool | None = None,
) -> dict:
    content = list(doc.get("content") or [])
    index = _check_index(content, block_index, allow_end=False)
    block = content[index]
    kind = (block_type or "").strip().lower()
    if kind:
        if kind not in BLOCK_TYPES:
            raise ValueError("block_type 应为 paragraph、h1、h2、h3、bullet、ordered、blockquote、code。")
        block = _rebuild(kind, block)
    if any(flag is not None for flag in (bold, italic, strike, code)):
        _apply_marks(block, {"bold": bold, "italic": italic, "strike": strike, "code": code})
    content[index] = block
    return {"type": "doc", "content": content}


def _parse_blocks(markdown: str) -> list[dict]:
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            blocks.append(_code_block("\n".join(code)))
            continue
        if line.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].lstrip())
                i += 1
            blocks.append(_blockquote("\n".join(quote)))
            continue
        if _HR.match(line):
            blocks.append({"type": "horizontalRule"})
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            blocks.append(_heading(len(heading.group(1)), heading.group(2)))
            i += 1
            continue
        if _BULLET.match(line):
            items: list[str] = []
            while i < len(lines) and _BULLET.match(lines[i]):
                items.append(_BULLET.match(lines[i]).group(1))
                i += 1
            blocks.append(_list_block("bulletList", items))
            continue
        if _ORDERED.match(line):
            listed, i = _parse_ordered(lines, i)
            blocks.append(listed)
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _special(lines[i]):
            para.append(lines[i])
            i += 1
        blocks.append(_paragraph(para))
    return blocks


def _special(line: str) -> bool:
    return (
        line.startswith("```")
        or line.startswith(">")
        or bool(_HR.match(line))
        or bool(_HEADING.match(line))
        or bool(_BULLET.match(line))
        or bool(_ORDERED.match(line))
        or bool(_CIRCLE.match(line.strip()))
        or line.strip().startswith("🔗")
    )


def _paragraph(lines: list[str]) -> dict:
    content: list[dict] = []
    for index, line in enumerate(lines):
        if index:
            content.append({"type": "hardBreak"})
        content.extend(_inline(line))
    node: dict = {"type": "paragraph"}
    if content:
        node["content"] = content
    _mark_source_link(node)
    return node


def _heading(level: int, text: str) -> dict:
    content = _inline(text)
    node: dict = {"type": "heading", "attrs": {"level": level}}
    if content:
        node["content"] = content
    return node


def _parse_ordered(lines: list[str], index: int) -> tuple[dict, int]:
    items: list[dict] = []
    start: int | None = None
    i = index
    while i < len(lines):
        if not lines[i].strip():
            nxt = _next_content(lines, i + 1)
            if nxt is None or _breaks_ordered(lines[nxt]):
                i = nxt if nxt is not None else len(lines)
                break
            i += 1
            continue
        matched = _ORDERED.match(lines[i])
        if not matched:
            break
        number = int(matched.group(1))
        if start is None:
            start = number
        title = matched.group(2)
        i += 1
        notes: list[str] = []
        circles: list[str] = []
        while i < len(lines):
            if not lines[i].strip():
                nxt = _next_content(lines, i + 1)
                if nxt is None or _ORDERED.match(lines[nxt]) or _breaks_ordered(lines[nxt]):
                    break
                i += 1
                continue
            if _ORDERED.match(lines[i]) or _breaks_ordered(lines[i]):
                break
            circle = _CIRCLE.match(lines[i].strip())
            if circle:
                circles.append(circle.group(1).strip())
                i += 1
                continue
            notes.append(lines[i].strip())
            i += 1
        items.append(_ordered_item(title, notes, circles))
    node: dict = {"type": "orderedList", "content": items}
    if start and start != 1:
        node["attrs"] = {"start": start}
    return node, i


def _next_content(lines: list[str], index: int) -> int | None:
    while index < len(lines):
        if lines[index].strip():
            return index
        index += 1
    return None


def _breaks_ordered(line: str) -> bool:
    return bool(
        _HR.match(line)
        or line.startswith("```")
        or line.startswith(">")
        or _HEADING.match(line)
        or _BULLET.match(line)
    )


def _ordered_item(title: str, notes: list[str], circles: list[str]) -> dict:
    content = [_paragraph([title])]
    for note in notes:
        content.append(_paragraph([note]))
    if circles:
        content.append({
            "type": "bulletList",
            "attrs": {"marker": "circle"},
            "content": [
                {"type": "listItem", "content": [_paragraph([text])]}
                for text in circles
            ],
        })
    return {"type": "listItem", "content": content}


def _mark_source_link(paragraph: dict) -> None:
    """从「🔗」起到紧随的原文链接缩小一号，前面的简评保持正文大小。"""
    rebuilt: list[dict] = []
    shrink_link = False
    for node in paragraph.get("content") or []:
        if node.get("type") != "text" or "🔗" not in (node.get("text") or ""):
            if shrink_link and _is_link(node):
                rebuilt.append(_with_mark(node, "small"))
                shrink_link = False
            else:
                rebuilt.append(node)
                shrink_link = False
            continue
        head, tail = (node.get("text") or "").split("🔗", 1)
        if head:
            leading = dict(node)
            leading["text"] = head
            rebuilt.append(leading)
        trailing = _with_mark(dict(node), "small")
        trailing["text"] = "🔗" + tail
        rebuilt.append(trailing)
        shrink_link = True
    if rebuilt:
        paragraph["content"] = rebuilt


def _is_link(node: dict) -> bool:
    return any(mark.get("type") == "link" for mark in node.get("marks") or [])


def _with_mark(node: dict, mark: str) -> dict:
    copied = dict(node)
    marks = [item for item in (copied.get("marks") or []) if item.get("type") != mark]
    marks.append({"type": mark})
    copied["marks"] = marks
    return copied


def _list_block(kind: str, items: list[str]) -> dict:
    return {
        "type": kind,
        "content": [
            {"type": "listItem", "content": [_paragraph([item])]}
            for item in items
        ],
    }


def _blockquote(text: str) -> dict:
    return {"type": "blockquote", "content": [_paragraph(text.split("\n"))]}


def _code_block(text: str) -> dict:
    node: dict = {"type": "codeBlock"}
    if text:
        node["content"] = [{"type": "text", "text": text}]
    return node


def _inline(text: str, marks: tuple[str, ...] = ()) -> list[dict]:
    if text == "":
        return []
    nodes: list[dict] = []
    pos = 0
    for match in _LINK.finditer(text):
        if match.start() > pos:
            nodes.extend(_styled(text[pos:match.start()], marks))
        node = _text(match.group(1), marks)
        node.setdefault("marks", []).append({"type": "link", "attrs": {"href": match.group(2)}})
        nodes.append(node)
        pos = match.end()
    if pos < len(text):
        nodes.extend(_styled(text[pos:], marks))
    return [node for node in nodes if node.get("text")]


def _styled(text: str, marks: tuple[str, ...] = ()) -> list[dict]:
    if text == "":
        return []
    nodes: list[dict] = []
    pos = 0
    for match in _INLINE.finditer(text):
        if match.start() > pos:
            nodes.append(_text(text[pos:match.start()], marks))
        kind, inner = _inline_hit(match)
        if kind == "code":
            nodes.append(_text(inner, tuple(dict.fromkeys((*marks, "code")))))
        else:
            nodes.extend(_styled(inner, tuple(dict.fromkeys((*marks, kind)))))
        pos = match.end()
    if pos < len(text):
        nodes.append(_text(text[pos:], marks))
    return [node for node in nodes if node.get("text")]


def _inline_hit(match: re.Match) -> tuple[str, str]:
    groups = ("code", "strike", "bold", "bold", "italic", "italic")
    for kind, value in zip(groups, match.groups()):
        if value is not None:
            return kind, value
    return "italic", ""


def _text(text: str, marks: tuple[str, ...]) -> dict:
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": mark} for mark in marks]
    return node


def _block_to_markdown(block: dict) -> str:
    kind = block.get("type")
    if kind == "horizontalRule":
        return "---"
    if kind == "heading":
        level = int((block.get("attrs") or {}).get("level") or 1)
        return f"{'#' * min(max(level, 1), 3)} {_inline_markdown(block.get('content'))}"
    if kind == "bulletList" and (block.get("attrs") or {}).get("marker") == "circle":
        return "\n".join(f"○ {_inline_markdown(_item_content(item))}" for item in block.get("content") or [])
    if kind == "bulletList":
        return "\n".join(f"- {_inline_markdown(_item_content(item))}" for item in block.get("content") or [])
    if kind == "orderedList":
        start = int((block.get("attrs") or {}).get("start") or 1)
        return "\n".join(
            _ordered_item_markdown(item, f"{start + offset}. ")
            for offset, item in enumerate(block.get("content") or [])
        )
    if kind == "blockquote":
        inner = document_to_markdown({"content": block.get("content") or []})
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    if kind == "codeBlock":
        return f"```\n{_plain_text(block)}\n```"
    return _inline_markdown(block.get("content"))


def _ordered_item_markdown(item: dict, prefix: str) -> str:
    lines: list[str] = []
    titled = False
    for child in item.get("content") or []:
        kind = child.get("type")
        if kind == "paragraph":
            text = _inline_markdown(child.get("content"))
            lines.append(prefix + text if not titled else text)
            titled = True
        elif kind == "bulletList" and (child.get("attrs") or {}).get("marker") == "circle":
            lines.extend(
                f"○ {_inline_markdown(_item_content(circle))}"
                for circle in child.get("content") or []
            )
    return "\n".join(lines)


def _item_content(item: dict) -> list:
    for child in item.get("content") or []:
        if child.get("type") == "paragraph":
            return child.get("content") or []
    return []


def _inline_markdown(nodes: list | None) -> str:
    parts: list[str] = []
    for node in nodes or []:
        if node.get("type") == "hardBreak":
            parts.append("\n")
            continue
        if node.get("type") != "text":
            continue
        text = node.get("text") or ""
        marks = {mark.get("type"): mark for mark in node.get("marks") or []}
        href = ((marks.get("link") or {}).get("attrs") or {}).get("href") or ""
        if href:
            text = f"[{text}]({href})"
        if "code" in marks:
            text = f"`{text}`"
        if "bold" in marks:
            text = f"**{text}**"
        if "italic" in marks:
            text = f"*{text}*"
        if "strike" in marks:
            text = f"~~{text}~~"
        parts.append(text)
    return "".join(parts)


def _plain_text(node: dict) -> str:
    if node.get("type") == "text":
        return node.get("text") or ""
    if node.get("type") == "hardBreak":
        return "\n"
    return "".join(_plain_text(child) for child in node.get("content") or [])


def _marks_in(node: dict) -> set[str]:
    found: set[str] = set()
    if node.get("type") == "text":
        found.update(mark.get("type") for mark in node.get("marks") or [] if mark.get("type"))
    for child in node.get("content") or []:
        found.update(_marks_in(child))
    return found


def _public_type(block: dict) -> str:
    kind = block.get("type")
    if kind == "horizontalRule":
        return "rule"
    if kind == "heading":
        level = int((block.get("attrs") or {}).get("level") or 1)
        return f"h{level}"
    if kind == "bulletList":
        return "bullet"
    if kind == "orderedList":
        return "ordered"
    if kind == "blockquote":
        return "blockquote"
    if kind == "codeBlock":
        return "code"
    return "paragraph"


def _rebuild(kind: str, block: dict) -> dict:
    if kind in ("bullet", "ordered"):
        items = _list_item_texts(block) or [_plain_text(block)]
        items = [item for item in items if item.strip()] or [""]
        return _list_block("bulletList" if kind == "bullet" else "orderedList", items)
    text = _plain_text(block)
    if kind == "code":
        return _code_block(text)
    if kind == "blockquote":
        return _blockquote(text)
    if kind in ("h1", "h2", "h3"):
        return _heading(int(kind[1]), text.replace("\n", " "))
    return _paragraph(text.split("\n") if text else [""])


def _list_item_texts(block: dict) -> list[str]:
    if block.get("type") not in ("bulletList", "orderedList"):
        return []
    return [_plain_text(item).strip("\n") for item in block.get("content") or []]


def _apply_marks(node: dict, flags: dict[str, bool | None]) -> None:
    active = {name: enabled for name, enabled in flags.items() if enabled is not None}
    if node.get("type") == "text":
        marks = [mark for mark in node.get("marks") or [] if mark.get("type") not in active]
        for name, enabled in active.items():
            if enabled:
                marks.append({"type": name})
        if marks:
            node["marks"] = marks
        else:
            node.pop("marks", None)
        return
    for child in node.get("content") or []:
        _apply_marks(child, flags)


def _check_index(content: list, block_index: int, *, allow_end: bool) -> int:
    if not isinstance(block_index, int) or isinstance(block_index, bool) or block_index < 0:
        raise ValueError("block_index 必须是从 0 开始的整数。")
    limit = len(content) if allow_end else len(content) - 1
    if block_index > limit or (not allow_end and not content):
        raise ValueError(f"block_index 超出范围，当前共 {len(content)} 个块。")
    return block_index
