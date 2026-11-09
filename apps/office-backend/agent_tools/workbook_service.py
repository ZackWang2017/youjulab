"""Univer 工作簿 JSON：按 sheet 名定位并写入单元格，不渲染表格。"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable
from uuid import uuid4

A1 = re.compile(r"^([A-Za-z]+)([1-9]\d*)$")
HEX_COLOR = re.compile(r"^#?[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?$")
CELL_LIMIT = 10000
NAME_LIMIT = 80
SCALAR = (str, int, float, bool, type(None))
BORDER_STYLES = {
    "none": 0, "无": 0,
    "thin": 1, "细": 1, "细线": 1,
    "hair": 2,
    "dotted": 3, "点线": 3, "点": 3,
    "dashed": 4, "虚线": 4,
    "dash_dot": 5, "dash-dot": 5,
    "dash_dot_dot": 6, "dash-dot-dot": 6,
    "double": 7, "双线": 7,
    "medium": 8, "中": 8, "中等": 8,
    "medium_dashed": 9, "medium-dashed": 9,
    "medium_dash_dot": 10,
    "medium_dash_dot_dot": 11,
    "slant_dash_dot": 12,
    "thick": 13, "粗": 13, "粗线": 13,
}
NAMED_COLORS = {
    "black": "#000000", "黑": "#000000", "黑色": "#000000",
    "white": "#FFFFFF", "白": "#FFFFFF", "白色": "#FFFFFF",
    "red": "#FF0000", "红": "#FF0000", "红色": "#FF0000",
    "green": "#00B050", "绿": "#00B050", "绿色": "#00B050",
    "blue": "#4C78FF", "蓝": "#4C78FF", "蓝色": "#4C78FF",
    "yellow": "#FFFF00", "黄": "#FFFF00", "黄色": "#FFFF00",
    "orange": "#FFC000", "橙": "#FFC000", "橙色": "#FFC000",
    "gray": "#BFBFBF", "grey": "#BFBFBF", "灰": "#BFBFBF", "灰色": "#BFBFBF",
}
ALIGN_H = {
    "left": 1, "左": 1, "居左": 1,
    "center": 2, "中": 2, "居中": 2,
    "right": 3, "右": 3, "居右": 3,
}
ALIGN_V = {
    "top": 1, "上": 1, "顶端": 1,
    "middle": 2, "中": 2, "居中": 2, "垂直居中": 2,
    "bottom": 3, "下": 3, "底端": 3,
}


def trim_name(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > NAME_LIMIT:
        raise ValueError(f"{label}不能超过 {NAME_LIMIT} 个字符。")
    return text


def parse_a1(cell: str, label: str = "单元格") -> tuple[int, int]:
    text = (cell or "A1").strip().upper()
    match = A1.fullmatch(text)
    if not match:
        raise ValueError(f"{label}必须是 A1 这类单元格地址。")
    column = 0
    for char in match.group(1):
        column = column * 26 + (ord(char) - 64)
    return int(match.group(2)) - 1, column - 1


def parse_range(start_cell: str, end_cell: str = "") -> tuple[int, int, int, int]:
    start_row, start_column = parse_a1(start_cell or "A1", "start_cell")
    if (end_cell or "").strip():
        end_row, end_column = parse_a1(end_cell, "end_cell")
    else:
        end_row, end_column = start_row, start_column
    if end_row < start_row or end_column < start_column:
        raise ValueError("end_cell 必须在 start_cell 的右下方。")
    rows = end_row - start_row + 1
    cols = end_column - start_column + 1
    if rows * cols > CELL_LIMIT:
        raise ValueError(f"一次最多操作 {CELL_LIMIT} 个单元格。")
    return start_row, start_column, end_row, end_column


def normalize_values(values: Any) -> list[list[Any]]:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except json.JSONDecodeError as exc:
            raise ValueError("values 不是合法 JSON。") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("values 必须是非空的二维数组。")
    rows: list[list[Any]] = []
    width = 0
    for row in values:
        if not isinstance(row, list):
            raise ValueError("values 每一行都必须是数组。")
        cells = []
        for cell in row:
            if type(cell) not in SCALAR:
                raise ValueError("单元格只能是字符串、数字、布尔值或空。")
            cells.append(cell)
        width = max(width, len(cells))
        rows.append(cells)
    if width < 1:
        raise ValueError("values 至少要有一个单元格。")
    if len(rows) * width > CELL_LIMIT:
        raise ValueError(f"一次最多写入 {CELL_LIMIT} 个单元格。")
    return rows


def is_workbook(content: Any) -> bool:
    return isinstance(content, dict) and ("sheets" in content or "sheetOrder" in content)


def new_workbook(table_name: str, workbook_id: str | None = None) -> dict:
    return {
        "id": workbook_id or uuid4().hex,
        "name": table_name,
        "locale": "zhCN",
        "styles": {},
        "sheetOrder": [],
        "sheets": {},
    }


def workbook_from_asset(asset: dict, table_name: str) -> dict:
    content = copy.deepcopy(asset.get("content") if isinstance(asset.get("content"), dict) else {})
    if not is_workbook(content):
        content = new_workbook(table_name, workbook_id=asset.get("id"))
    content["name"] = table_name
    if asset.get("id") and not content.get("id"):
        content["id"] = asset["id"]
    content.setdefault("sheets", {})
    content.setdefault("sheetOrder", [])
    content.setdefault("styles", {})
    return content


def find_sheet(content: dict, sheet_name: str) -> tuple[str, dict] | None:
    sheets = content.get("sheets") or {}
    if not isinstance(sheets, dict):
        return None
    for sheet_id, sheet in sheets.items():
        if isinstance(sheet, dict) and sheet.get("name") == sheet_name:
            return str(sheet_id), sheet
    return None


def empty_sheet(sheet_id: str, sheet_name: str) -> dict:
    return {
        "id": sheet_id,
        "name": sheet_name,
        "rowCount": 1000,
        "columnCount": 26,
        "defaultRowHeight": 24,
        "defaultColumnWidth": 88,
        "cellData": {},
        "rowData": {},
        "columnData": {},
        "mergeData": [],
        "hidden": 0,
        "freeze": {"startRow": -1, "startColumn": -1, "ySplit": 0, "xSplit": 0},
        "showGridlines": 1,
    }


def ensure_worksheet(content: dict, sheet_name: str) -> bool:
    if find_sheet(content, sheet_name):
        return False
    sheet_id = f"sheet-{uuid4().hex[:8]}"
    sheets = content.setdefault("sheets", {})
    order = content.setdefault("sheetOrder", [])
    sheets[sheet_id] = empty_sheet(sheet_id, sheet_name)
    if sheet_id not in order:
        order.append(sheet_id)
    return True


_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_PERCENT_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)%$")


def _coerce_numeric_string(value: str) -> float | int | None:
    """纯数字文本转为数值，避免 Univer「以文本形式存储的数字」告警；保留前导零编号。"""
    s = value.strip()
    if not s:
        return None
    if _PERCENT_NUMBER.match(s):
        return float(s[:-1]) / 100.0
    if not _PLAIN_NUMBER.match(s):
        return None
    # 00123 这类编号保持文本
    if re.match(r"^[+-]?0\d+$", s):
        return None
    num = float(s)
    if num.is_integer() and "e" not in s.lower() and "." not in s:
        return int(num)
    return num


def cell_payload(value: Any) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.startswith("="):
        return {"f": value, "v": None}
    if isinstance(value, bool):
        return {"v": value, "t": 3}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"v": value, "t": 2}
    if isinstance(value, str):
        coerced = _coerce_numeric_string(value)
        if coerced is not None:
            return {"v": coerced, "t": 2}
        return {"v": value, "t": 1}
    return {"v": str(value), "t": 1}


def to_a1(row: int, column: int) -> str:
    n = column + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row + 1}"


def cell_value(cell: Any) -> Any:
    if not isinstance(cell, dict):
        return None
    formula = cell.get("f")
    if isinstance(formula, str) and formula:
        return formula
    if "v" in cell:
        return cell["v"]
    return None


def _row_map(cell_data: dict, row: int) -> dict:
    row_map = cell_data.get(str(row), cell_data.get(row))
    return row_map if isinstance(row_map, dict) else {}


def used_bounds(cell_data: dict) -> tuple[int, int, int, int] | None:
    if not isinstance(cell_data, dict) or not cell_data:
        return None
    min_row = min_col = None
    max_row = max_col = 0
    for row_key, row_map in cell_data.items():
        try:
            row = int(row_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(row_map, dict) or not row_map:
            continue
        cols = []
        for col_key in row_map:
            try:
                cols.append(int(col_key))
            except (TypeError, ValueError):
                continue
        if not cols:
            continue
        min_row = row if min_row is None else min(min_row, row)
        max_row = max(max_row, row)
        min_col = min(cols) if min_col is None else min(min_col, min(cols))
        max_col = max(max_col, max(cols))
    if min_row is None or min_col is None:
        return None
    return min_row, min_col, max_row, max_col


def sheet_names(content: dict) -> list[str]:
    sheets = content.get("sheets") or {}
    if not isinstance(sheets, dict):
        return []
    names: list[str] = []
    for sheet_id in content.get("sheetOrder") or []:
        sheet = sheets.get(sheet_id)
        if isinstance(sheet, dict) and sheet.get("name") and sheet["name"] not in names:
            names.append(sheet["name"])
    for sheet in sheets.values():
        if isinstance(sheet, dict) and sheet.get("name") and sheet["name"] not in names:
            names.append(sheet["name"])
    return names


def read_cells(
    content: dict,
    sheet_name: str,
    start_row: int,
    start_column: int,
    end_row: int | None = None,
    end_column: int | None = None,
) -> list[list[Any]]:
    found = find_sheet(content, sheet_name)
    if not found:
        raise ValueError(f"工作表「{sheet_name}」不存在。")
    _, sheet = found
    cell_data = sheet.get("cellData") if isinstance(sheet.get("cellData"), dict) else {}
    if end_row is None or end_column is None:
        bounds = used_bounds(cell_data)
        if not bounds:
            return []
        _min_row, _min_col, max_row, max_col = bounds
        end_row = max(max_row, start_row) if end_row is None else end_row
        end_column = max(max_col, start_column) if end_column is None else end_column
    if end_row < start_row or end_column < start_column:
        return []
    rows = end_row - start_row + 1
    cols = end_column - start_column + 1
    if rows * cols > CELL_LIMIT:
        raise ValueError(f"一次最多读取 {CELL_LIMIT} 个单元格。")
    matrix: list[list[Any]] = []
    for row in range(start_row, end_row + 1):
        row_map = _row_map(cell_data, row)
        line: list[Any] = []
        for col in range(start_column, end_column + 1):
            cell = row_map.get(str(col), row_map.get(col))
            line.append(cell_value(cell))
        matrix.append(line)
    return matrix


def write_cells(content: dict, sheet_name: str, values: list[list[Any]], start_row: int, start_column: int) -> None:
    found = find_sheet(content, sheet_name)
    if not found:
        raise ValueError(f"工作表「{sheet_name}」不存在。")
    _, sheet = found
    cell_data = sheet.setdefault("cellData", {})
    if not isinstance(cell_data, dict):
        cell_data = {}
        sheet["cellData"] = cell_data
    last_row = start_row
    last_col = start_column
    for row_offset, row in enumerate(values):
        row_index = start_row + row_offset
        last_row = max(last_row, row_index)
        row_key = str(row_index)
        row_map = cell_data.get(row_key)
        if not isinstance(row_map, dict):
            row_map = {}
            cell_data[row_key] = row_map
        for col_offset, value in enumerate(row):
            col_index = start_column + col_offset
            last_col = max(last_col, col_index)
            col_key = str(col_index)
            existing = row_map.get(col_key)
            existing_style = existing.get("s") if isinstance(existing, dict) else None
            payload = cell_payload(value)
            if payload is None:
                if existing_style is not None:
                    row_map[col_key] = {"s": existing_style}
                else:
                    row_map.pop(col_key, None)
            else:
                if existing_style is not None:
                    payload["s"] = existing_style
                row_map[col_key] = payload
        if not row_map:
            cell_data.pop(row_key, None)
    _expand_sheet(sheet, last_row, last_col)


def merge_range(
    content: dict,
    sheet_name: str,
    start_cell: str,
    end_cell: str,
) -> dict:
    start_row, start_column, end_row, end_column = parse_range(start_cell, end_cell)
    if start_row == end_row and start_column == end_column:
        raise ValueError("合并区域至少包含两个单元格，请同时提供 start_cell 和 end_cell。")
    sheet = _require_sheet(content, sheet_name)
    merges = _merge_list(sheet)
    incoming = (start_row, start_column, end_row, end_column)
    for item in merges:
        existing = _as_merge(item)
        if existing is None:
            continue
        if existing == incoming:
            _expand_sheet(sheet, end_row, end_column)
            return {"merged": False, "reason": "already_merged"}
        if _ranges_overlap(existing, incoming):
            raise ValueError(f"合并区域与已有合并 {to_a1(*existing[:2])}:{to_a1(*existing[2:])} 重叠。")
    merges.append({
        "startRow": start_row,
        "startColumn": start_column,
        "endRow": end_row,
        "endColumn": end_column,
    })
    _expand_sheet(sheet, end_row, end_column)
    return {"merged": True}


def unmerge_range(
    content: dict,
    sheet_name: str,
    start_cell: str,
    end_cell: str = "",
) -> dict:
    incoming = parse_range(start_cell, end_cell)
    sheet = _require_sheet(content, sheet_name)
    merges = _merge_list(sheet)
    kept: list[dict] = []
    removed = 0
    for item in merges:
        existing = _as_merge(item)
        if existing is None:
            continue
        if _ranges_overlap(existing, incoming):
            removed += 1
        else:
            kept.append(item)
    sheet["mergeData"] = kept
    return {"removed": removed}


def set_background(
    content: dict,
    sheet_name: str,
    start_cell: str,
    end_cell: str,
    color: str,
) -> dict:
    rgb = parse_color(color, allow_empty=True)
    start_row, start_column, end_row, end_column = parse_range(start_cell, end_cell)

    def patch(style: dict, _row: int, _col: int) -> None:
        if rgb:
            style["bg"] = {"rgb": rgb}
        else:
            style.pop("bg", None)

    count = _apply_style(content, sheet_name, start_row, start_column, end_row, end_column, patch)
    return {"cells": count, "color": rgb or ""}


def set_border(
    content: dict,
    sheet_name: str,
    start_cell: str,
    end_cell: str,
    style: str = "thin",
    color: str = "#000000",
    sides: str = "all",
) -> dict:
    start_row, start_column, end_row, end_column = parse_range(start_cell, end_cell)
    border_style = parse_border_style(style)
    rgb = None if border_style == 0 else parse_color(color or "#000000")
    edges = parse_border_sides(sides)
    stroke = None if border_style == 0 else {"s": border_style, "cl": {"rgb": rgb}}

    def patch(cell_style: dict, row: int, col: int) -> None:
        current = cell_style.get("bd") if isinstance(cell_style.get("bd"), dict) else {}
        wanted = _border_sides_for_cell(row, col, start_row, start_column, end_row, end_column, edges)
        for side in wanted:
            if stroke is None:
                current.pop(side, None)
            else:
                current[side] = copy.deepcopy(stroke)
        if current:
            cell_style["bd"] = current
        else:
            cell_style.pop("bd", None)

    count = _apply_style(content, sheet_name, start_row, start_column, end_row, end_column, patch)
    return {
        "cells": count,
        "style": style.strip().lower() or "thin",
        "color": rgb or "",
        "sides": ",".join(edges),
    }


def set_font(
    content: dict,
    sheet_name: str,
    start_cell: str,
    end_cell: str,
    font_family: str = "",
    font_size: Any = None,
    bold: Any = None,
    italic: Any = None,
    underline: Any = None,
    strikethrough: Any = None,
    color: str = "",
    align: str = "",
    vertical_align: str = "",
) -> dict:
    family = (font_family or "").strip()
    size = parse_font_size(font_size)
    bold_flag = parse_optional_bool(bold, "bold")
    italic_flag = parse_optional_bool(italic, "italic")
    underline_flag = parse_optional_bool(underline, "underline")
    strike_flag = parse_optional_bool(strikethrough, "strikethrough")
    rgb = parse_color(color, allow_empty=True) if (color or "").strip() else None
    ht = parse_align(align, ALIGN_H, "align")
    vt = parse_align(vertical_align, ALIGN_V, "vertical_align")
    if not any([
        family, size is not None, bold_flag is not None, italic_flag is not None,
        underline_flag is not None, strike_flag is not None, rgb, ht is not None, vt is not None,
    ]):
        raise ValueError("请至少提供一项字体设置：font_family、font_size、bold、italic、underline、strikethrough、color、align 或 vertical_align。")
    start_row, start_column, end_row, end_column = parse_range(start_cell, end_cell)

    def patch(style: dict, _row: int, _col: int) -> None:
        if family:
            style["ff"] = family
        if size is not None:
            style["fs"] = size
        if bold_flag is not None:
            style["bl"] = 1 if bold_flag else 0
        if italic_flag is not None:
            style["it"] = 1 if italic_flag else 0
        if underline_flag is not None:
            style["ul"] = {"s": 1 if underline_flag else 0}
        if strike_flag is not None:
            style["st"] = {"s": 1 if strike_flag else 0}
        if rgb:
            style["cl"] = {"rgb": rgb}
        if ht is not None:
            style["ht"] = ht
        if vt is not None:
            style["vt"] = vt

    count = _apply_style(content, sheet_name, start_row, start_column, end_row, end_column, patch)
    return {"cells": count}


def parse_color(value: str, allow_empty: bool = False) -> str | None:
    text = (value or "").strip()
    if not text or text.lower() in ("none", "null", "clear", "transparent", "无", "空"):
        if allow_empty:
            return None
        raise ValueError("颜色不能为空。")
    named = NAMED_COLORS.get(text.lower()) or NAMED_COLORS.get(text)
    if named:
        return named
    if not HEX_COLOR.fullmatch(text):
        raise ValueError("颜色必须是 #RGB / #RRGGBB，或 red/yellow/blue 这类颜色名。")
    hex_text = text[1:] if text.startswith("#") else text
    if len(hex_text) == 3:
        hex_text = "".join(ch * 2 for ch in hex_text)
    return f"#{hex_text.upper()}"


def parse_border_style(value: str) -> int:
    text = (value or "thin").strip().lower()
    if text.isdigit():
        number = int(text)
        if number in range(14):
            return number
    if text not in BORDER_STYLES:
        raise ValueError("边框样式只支持 none/thin/medium/thick/dashed/dotted/double。")
    return BORDER_STYLES[text]


def parse_border_sides(value: str) -> list[str]:
    text = (value or "all").strip().lower()
    aliases = {
        "all": "all", "全部": "all", "所有": "all",
        "outside": "outside", "outer": "outside", "外边框": "outside", "外": "outside",
        "inside": "inside", "inner": "inside", "内边框": "inside", "内": "inside",
        "top": "top", "上": "top", "上边框": "top",
        "bottom": "bottom", "下": "bottom", "下边框": "bottom",
        "left": "left", "左": "left", "左边框": "left",
        "right": "right", "右": "right", "右边框": "right",
        "horizontal": "horizontal", "水平": "horizontal",
        "vertical": "vertical", "垂直": "vertical",
    }
    parts = [part.strip() for part in re.split(r"[,/|，、]", text) if part.strip()]
    if not parts:
        parts = ["all"]
    sides: list[str] = []
    for part in parts:
        mapped = aliases.get(part) or aliases.get(part.lower())
        if not mapped:
            raise ValueError("边框位置只支持 all/outside/inside/top/bottom/left/right/horizontal/vertical。")
        if mapped not in sides:
            sides.append(mapped)
    return sides


def parse_font_size(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        size = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("font_size 必须是数字。") from exc
    if size < 1 or size > 409:
        raise ValueError("font_size 必须在 1 到 409 之间。")
    return int(size) if size.is_integer() else size


def parse_optional_bool(value: Any, label: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "是", "真"):
        return True
    if text in ("0", "false", "no", "n", "否", "假"):
        return False
    raise ValueError(f"{label} 必须是 true 或 false。")


def parse_align(value: str, table: dict[str, int], label: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    mapped = table.get(text.lower()) or table.get(text)
    if mapped is None:
        raise ValueError(f"{label} 取值无效。")
    return mapped


def _require_sheet(content: dict, sheet_name: str) -> dict:
    found = find_sheet(content, sheet_name)
    if not found:
        raise ValueError(f"工作表「{sheet_name}」不存在。")
    return found[1]


def _expand_sheet(sheet: dict, last_row: int, last_col: int) -> None:
    sheet["rowCount"] = max(int(sheet.get("rowCount") or 0), last_row + 50, 1000)
    sheet["columnCount"] = max(int(sheet.get("columnCount") or 0), last_col + 5, 26)


def _merge_list(sheet: dict) -> list:
    data = sheet.get("mergeData")
    if not isinstance(data, list):
        data = []
        sheet["mergeData"] = data
    return data


def _as_merge(item: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(item, dict):
        return None
    try:
        return (
            int(item.get("startRow", item.get("start_row"))),
            int(item.get("startColumn", item.get("start_column"))),
            int(item.get("endRow", item.get("end_row"))),
            int(item.get("endColumn", item.get("end_column"))),
        )
    except (TypeError, ValueError):
        return None


def _ranges_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return not (
        left[2] < right[0]
        or left[0] > right[2]
        or left[3] < right[1]
        or left[1] > right[3]
    )


def _cell_data(sheet: dict) -> dict:
    cell_data = sheet.setdefault("cellData", {})
    if not isinstance(cell_data, dict):
        cell_data = {}
        sheet["cellData"] = cell_data
    return cell_data


def _ensure_cell(cell_data: dict, row: int, column: int) -> dict:
    row_key = str(row)
    row_map = cell_data.get(row_key)
    if not isinstance(row_map, dict):
        row_map = {}
        cell_data[row_key] = row_map
    col_key = str(column)
    cell = row_map.get(col_key)
    if not isinstance(cell, dict):
        cell = {}
        row_map[col_key] = cell
    return cell


def _resolved_style(content: dict, cell: dict) -> dict:
    style = cell.get("s") if isinstance(cell, dict) else None
    if isinstance(style, dict):
        return copy.deepcopy(style)
    if isinstance(style, str):
        styles = content.get("styles") if isinstance(content.get("styles"), dict) else {}
        found = styles.get(style)
        if isinstance(found, dict):
            return copy.deepcopy(found)
    return {}


def intern_style(content: dict, style: dict) -> str | None:
    cleaned = {key: value for key, value in style.items() if value is not None and value != {}}
    if not cleaned:
        return None
    styles = content.setdefault("styles", {})
    if not isinstance(styles, dict):
        styles = {}
        content["styles"] = styles
    dumped = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
    for style_id, existing in styles.items():
        if isinstance(existing, dict) and json.dumps(existing, sort_keys=True, ensure_ascii=False) == dumped:
            return str(style_id)
    style_id = f"s{uuid4().hex[:10]}"
    styles[style_id] = cleaned
    return style_id


def _apply_style(
    content: dict,
    sheet_name: str,
    start_row: int,
    start_column: int,
    end_row: int,
    end_column: int,
    patch: Callable[[dict, int, int], None],
) -> int:
    sheet = _require_sheet(content, sheet_name)
    cell_data = _cell_data(sheet)
    count = 0
    for row in range(start_row, end_row + 1):
        for column in range(start_column, end_column + 1):
            cell = _ensure_cell(cell_data, row, column)
            style = _resolved_style(content, cell)
            patch(style, row, column)
            style_id = intern_style(content, style)
            count += 1
            if style_id:
                cell["s"] = style_id
            else:
                cell.pop("s", None)
                if not cell:
                    row_map = cell_data.get(str(row))
                    if isinstance(row_map, dict):
                        row_map.pop(str(column), None)
                        if not row_map:
                            cell_data.pop(str(row), None)
    _expand_sheet(sheet, end_row, end_column)
    return count


def _border_sides_for_cell(
    row: int,
    column: int,
    start_row: int,
    start_column: int,
    end_row: int,
    end_column: int,
    edges: list[str],
) -> list[str]:
    wanted: list[str] = []

    def add(*sides: str) -> None:
        for side in sides:
            if side not in wanted:
                wanted.append(side)

    for edge in edges:
        if edge == "all":
            add("t", "r", "b", "l")
        elif edge == "outside":
            if row == start_row:
                add("t")
            if row == end_row:
                add("b")
            if column == start_column:
                add("l")
            if column == end_column:
                add("r")
        elif edge == "inside":
            if row < end_row:
                add("b")
            if row > start_row:
                add("t")
            if column < end_column:
                add("r")
            if column > start_column:
                add("l")
        elif edge == "horizontal":
            if row < end_row:
                add("b")
            if row > start_row:
                add("t")
        elif edge == "vertical":
            if column < end_column:
                add("r")
            if column > start_column:
                add("l")
        elif edge == "top" and row == start_row:
            add("t")
        elif edge == "bottom" and row == end_row:
            add("b")
        elif edge == "left" and column == start_column:
            add("l")
        elif edge == "right" and column == end_column:
            add("r")
    return wanted
