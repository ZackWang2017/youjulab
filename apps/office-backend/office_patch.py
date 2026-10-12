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


_FORMAT_ALIASES = {
    "accounting": "#,##0.00",
    "financial": "#,##0.00",
    "finance": "#,##0.00",
    "财务": "#,##0.00",
    "财务数值": "#,##0.00",
    "千分位": "#,##0.00",
    "percent": "0.00%",
    "percentage": "0.00%",
    "百分数": "0.00%",
    "百分比": "0.00%",
}


def resolve_number_format(raw) -> str:
    text = str(raw or "").strip()
    if not text or len(text) > 64:
        raise ValueError("数字格式无效。")
    key = text.lower()
    pattern = _FORMAT_ALIASES.get(text) or _FORMAT_ALIASES.get(key) or text
    if any(ch in pattern for ch in ("\n", "\r", "\0")):
        raise ValueError("数字格式无效。")
    return pattern


def _range_size(address: str) -> tuple[int, int, int, int]:
    parsed = _parse_a1_target(address)
    if not parsed:
        raise ValueError("格式区域无效。")
    row, column, end_row, end_col = parsed
    if row < 0 or column < 0:
        raise ValueError("格式区域无效。")
    if end_row is None or end_col is None:
        end_row, end_col = row, column
    if end_row < row or end_col < column:
        raise ValueError("格式区域无效。")
    rows = end_row - row + 1
    cols = end_col - column + 1
    if rows * cols > 50_000:
        raise ValueError("格式区域不能超过 50000 个单元格。")
    return row, column, rows, cols


def _merge_format_ops(ops: list[dict]) -> list[dict]:
    """把相同格式、同行跨度、相邻列的区域合并，减少模型按列拆条导致的超限。"""
    if len(ops) <= 1:
        return ops
    ordered = sorted(ops, key=lambda o: (o["number_format"], o["row"], o["column"]))
    merged: list[dict] = []
    for op in ordered:
        if not merged:
            merged.append(dict(op))
            continue
        prev = merged[-1]
        same_fmt = prev["number_format"] == op["number_format"]
        same_rows = prev["row"] == op["row"] and prev["rows"] == op["rows"]
        adjacent_cols = op["column"] <= prev["column"] + prev["cols"]
        if same_fmt and same_rows and adjacent_cols:
            end_col = max(prev["column"] + prev["cols"] - 1, op["column"] + op["cols"] - 1)
            prev["cols"] = end_col - prev["column"] + 1
            prev["address"] = _format_a1(prev["row"], prev["column"], prev["rows"], prev["cols"])
            continue
        merged.append(dict(op))
    return merged


def _normalize_format_ops(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("格式修改必须是非空列表。")
    if len(raw) > 200:
        raise ValueError("格式修改不能超过 200 个区域。")
    ops: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("格式修改条目无效。")
        address = str(item.get("address") or "").strip()
        if not address:
            raise ValueError("格式区域无效。")
        row, column, rows, cols = _range_size(address)
        pattern = resolve_number_format(item.get("number_format") or item.get("format"))
        ops.append({
            "address": _format_a1(row, column, rows, cols),
            "number_format": pattern,
            "row": row,
            "column": column,
            "rows": rows,
            "cols": cols,
        })
    return _merge_format_ops(ops)


def _normalize_mindmap_ops(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("导图修改必须是非空列表。")
    if len(raw) > 100:
        raise ValueError("导图修改不能超过 100 项。")
    ops: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("导图修改条目无效。")
        kind = item.get("op")
        if kind not in ("add", "update", "delete", "move"):
            raise ValueError("导图修改类型无效。")
        op: dict = {"op": kind}
        if kind in ("update", "delete", "move"):
            node_id = str(item.get("id") or "").strip()
            if not node_id or len(node_id) > 200:
                raise ValueError("导图节点无效。")
            op["id"] = node_id
        if kind in ("add", "move"):
            parent_id = str(item.get("parent_id") or "").strip()
            if not parent_id or len(parent_id) > 200:
                raise ValueError("导图父节点无效。")
            op["parent_id"] = parent_id
        if kind in ("add", "update"):
            topic = str(item.get("topic") or "").strip()
            if not topic or len(topic) > 4000:
                raise ValueError("导图节点标题无效。")
            op["topic"] = topic
        if "index" in item:
            if type(item["index"]) is not int or not 0 <= item["index"] <= 10000:
                raise ValueError("导图节点位置无效。")
            op["index"] = item["index"]
        ops.append(op)
    return ops


def create_patch(
    context: OfficeContext,
    replacement,
    task: str = "explain",
    *,
    format_ops: list | None = None,
    mindmap_ops: list | None = None,
) -> dict:
    if context.surface == "pdf":
        raise ValueError("当前 PDF 为只读。")
    ops = _normalize_format_ops(format_ops) if format_ops else []
    map_ops = _normalize_mindmap_ops(mindmap_ops) if mindmap_ops else []
    if context.surface != "sheet" and ops:
        raise ValueError("仅表格支持数字格式修改。")
    if context.surface != "mindmap" and map_ops:
        raise ValueError("仅思维导图支持节点修改。")
    s = context.selection
    if replacement is None:
        if not ops and not map_ops:
            raise ValueError("修改内容为空。")
        if context.surface not in ("sheet", "mindmap"):
            raise ValueError("修改内容为空。")
    elif task == "generate":
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
    patch = {
        **context.model_dump(exclude={"surrounding"}),
        "patch_id": uuid4().hex,
        "replacement": replacement,
        "task": task,
    }
    if ops:
        patch["format_ops"] = ops
    if map_ops:
        patch["mindmap_ops"] = map_ops
    return patch


def validate_office_patch(patch: dict, current_context: dict) -> dict:
    current = OfficeContext.model_validate(current_context)
    for key in ("document_id", "surface", "revision"):
        if patch.get(key) != getattr(current, key):
            raise ValueError("文档或选区已改变，请重新生成建议。")
    task = patch.get("task") or "explain"
    format_ops = patch.get("format_ops")
    mindmap_ops = patch.get("mindmap_ops")
    if current.surface == "sheet":
        patch_sel = patch.get("selection") or {}
        if patch_sel.get("sheet_id") != current.selection.get("sheet_id"):
            raise ValueError("文档或选区已改变，请重新生成建议。")
        target = current if patch_sel == current.selection else current.model_copy(update={"selection": patch_sel})
        OfficeContext.model_validate(target.model_dump())
        create_patch(target, patch.get("replacement"), task, format_ops=format_ops, mindmap_ops=mindmap_ops)
    else:
        if patch.get("selection") != current.selection:
            raise ValueError("文档或选区已改变，请重新生成建议。")
        create_patch(current, patch.get("replacement"), task, format_ops=format_ops, mindmap_ops=mindmap_ops)
    return {"valid": True, "patch_id": patch.get("patch_id")}
