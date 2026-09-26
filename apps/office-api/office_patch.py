"""根据选区生成待确认修改，并拒绝过期补丁。"""
from __future__ import annotations

import re
from uuid import uuid4

from office_context import OfficeContext

_A1_RANGE = re.compile(
    r"\$?([A-Za-z]{1,3})\$?(\d{1,7}):\$?([A-Za-z]{1,3})\$?(\d{1,7})"
)
_A1_CELL = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d{1,7})")


def _sheet_matrix(replacement) -> list:
    if isinstance(replacement, str):
        replacement = [[replacement]]
    if not isinstance(replacement, list) or not replacement:
        raise ValueError("修改必须是单元格矩阵。")
    cols = None
    for row in replacement:
        if not isinstance(row, list) or not row:
            raise ValueError("修改必须保持矩形。")
        if cols is None:
            cols = len(row)
        elif len(row) != cols:
            raise ValueError("修改必须保持矩形。")
        if any(v is not None and type(v) not in (str, int, float, bool) for v in row):
            raise ValueError("单元格值无效。")
    if len(replacement) * cols > 2000:
        raise ValueError("选区不能超过 2000 个单元格。")
    return replacement


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError("写入位置无效。")
        n = n * 26 + ord(ch) - 64
    return n - 1


def _col_name(index: int) -> str:
    n = index + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _format_a1(row: int, column: int, rows: int, cols: int) -> str:
    start = f"{_col_name(column)}{row + 1}"
    if rows == 1 and cols == 1:
        return start
    return f"{start}:{_col_name(column + cols - 1)}{row + rows}"


def _parse_a1_target(text: str) -> tuple[int, int, int | None, int | None] | None:
    """解析区域或单元格。区域优先，避免把 Q4 这类列名当成单元格。"""
    ranged = _A1_RANGE.search(text or "")
    if ranged:
        return (
            int(ranged.group(2)) - 1,
            _col_index(ranged.group(1)),
            int(ranged.group(4)) - 1,
            _col_index(ranged.group(3)),
        )
    cell = _A1_CELL.search(text or "")
    if not cell:
        return None
    return int(cell.group(2)) - 1, _col_index(cell.group(1)), None, None


def aim_sheet_write(context: OfficeContext, result: dict) -> OfficeContext:
    """写入矩阵与当前选区尺寸不同时，改用回答里的 A1 区域作为补丁目标。"""
    replacement = result.get("replacement")
    if not isinstance(replacement, list):
        return context
    matrix = _sheet_matrix(replacement)
    sel = context.selection
    rows, cols = len(matrix), len(matrix[0])
    if rows == sel["rows"] and cols == sel["cols"]:
        return context
    address = str(result.get("address") or "").strip()
    parsed = _parse_a1_target(address) if address else _parse_a1_target(str(result.get("answer") or ""))
    if not parsed:
        raise ValueError("写入矩阵与当前选区行列数不一致，且没有给出 A1 区域。")
    row, column, end_row, end_col = parsed
    if row < 0 or column < 0:
        raise ValueError("写入位置无效。")
    if end_row is not None and end_col is not None:
        if end_row < row or end_col < column:
            raise ValueError("写入位置无效。")
        if end_row - row + 1 != rows or end_col - column + 1 != cols:
            raise ValueError("写入区域与矩阵行列数不一致。")
    selection = {
        "sheet_id": sel.get("sheet_id"),
        "row": row,
        "column": column,
        "rows": rows,
        "cols": cols,
        "address": _format_a1(row, column, rows, cols),
        "values": [[None for _ in range(cols)] for _ in range(rows)],
    }
    return context.model_copy(update={"selection": selection})


def create_patch(context: OfficeContext, replacement, task: str = "explain") -> dict:
    if context.surface == "pdf":
        raise ValueError("当前 PDF 为只读。")
    s = context.selection
    if task == "generate":
        if context.surface == "sheet":
            replacement = _sheet_matrix(replacement)
        elif context.surface == "mindmap":
            if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 40000:
                raise ValueError("生成内容必须是 40000 字以内的节点文本。")
            replacement = replacement.strip()
        elif context.surface == "calendar":
            if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 40000:
                raise ValueError("生成内容必须是 40000 字以内的 Markdown 正文。")
            replacement = replacement.strip()
        elif not isinstance(replacement, str) or not replacement or len(replacement) > 40000:
            raise ValueError("生成内容必须是 40000 字以内的文本。")
    elif context.surface == "sheet":
        replacement = _sheet_matrix(replacement)
        if len(replacement) != s["rows"] or len(replacement[0]) != s["cols"]:
            raise ValueError("修改必须保持选区行列数。")
    elif context.surface == "mindmap":
        if not isinstance(replacement, str) or len(replacement) > 40000:
            raise ValueError("修改必须是 40000 字以内的节点文本。")
    elif context.surface == "calendar":
        if not isinstance(replacement, str) or len(replacement) > 40000:
            raise ValueError("修改必须是 40000 字以内的 Markdown 正文。")
    elif not isinstance(replacement, str) or len(replacement) > 40000:
        raise ValueError("修改必须是 40000 字以内的文本。")
    return {**context.model_dump(exclude={"surrounding"}), "patch_id": uuid4().hex,
            "replacement": replacement, "task": task}


def validate_office_patch(patch: dict, current_context: dict) -> dict:
    current = OfficeContext.model_validate(current_context)
    for key in ("document_id", "surface", "revision"):
        if patch.get(key) != getattr(current, key):
            raise ValueError("文档或选区已改变，请重新生成建议。")
    task = patch.get("task") or "explain"
    if current.surface == "sheet":
        patch_sel = patch.get("selection") or {}
        if patch_sel.get("sheet_id") != current.selection.get("sheet_id"):
            raise ValueError("文档或选区已改变，请重新生成建议。")
        target = current if patch_sel == current.selection else current.model_copy(update={"selection": patch_sel})
        OfficeContext.model_validate(target.model_dump())
        create_patch(target, patch.get("replacement"), task)
    else:
        if patch.get("selection") != current.selection:
            raise ValueError("文档或选区已改变，请重新生成建议。")
        create_patch(current, patch.get("replacement"), task)
    return {"valid": True, "patch_id": patch.get("patch_id")}
