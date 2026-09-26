"""按项目名 / 表名 / sheet 名创建、读取或写入表格。不注册到财务 MCP 工具列表。

外部令牌只用来向 office-api 换取登录账号（邮箱 / 用户 id）。
项目和表格挂在该账号下，不挂在可重新生成的 token 上。
"""
from __future__ import annotations

from typing import Any, Callable

from office_sheet_client import OfficeError, client_for
from office_workbook import (
    ensure_worksheet,
    find_sheet,
    merge_range,
    normalize_values,
    parse_a1,
    read_cells,
    set_background,
    set_border,
    set_font,
    sheet_names,
    to_a1,
    trim_name,
    unmerge_range,
    workbook_from_asset,
    write_cells,
)


def create_spreadsheet(project_name: str, table_name: str, sheet_name: str) -> dict:
    """按名称创建表格：项目名、表名（工作簿）、sheet 名（工作表标签）。

    项目或表格不存在时会创建。若同名表格里已经有该 sheet，则报错，避免覆盖。
    只写 Univer JSON，不查询财务报表，也不渲染表格。
    """
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    table_name = trim_name(table_name, "表名")
    sheet_name = trim_name(sheet_name, "sheet名")
    project, created_project = _ensure_project(api, project_name, create=True)

    def mutate(content: dict, created_table: bool) -> dict:
        created_sheet = ensure_worksheet(content, sheet_name)
        if not created_table and not created_sheet:
            raise ValueError(f"表格「{table_name}」中已存在工作表「{sheet_name}」。")
        return {"sheet": created_sheet}

    saved, created_table, extra = _mutate(api, project["id"], table_name, mutate)
    return {
        "ok": True,
        "action": "create",
        "project_name": project_name,
        "table_name": table_name,
        "sheet_name": sheet_name,
        "created": {"project": created_project, "table": created_table, "sheet": extra["sheet"]},
        "revision": saved["revision"],
    }


def write_spreadsheet(
    project_name: str,
    table_name: str,
    sheet_name: str,
    values: Any,
    start_cell: str = "A1",
) -> dict:
    """按名称写入单元格：项目名、表名、sheet 名，从 start_cell（默认 A1）起写入二维 values。

    项目 / 表格 / sheet 不存在时会一并创建。合并写入，不清空 sheet 上其它单元格。
    """
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    table_name = trim_name(table_name, "表名")
    sheet_name = trim_name(sheet_name, "sheet名")
    matrix = normalize_values(values)
    start_row, start_column = parse_a1(start_cell)
    project, _ = _ensure_project(api, project_name, create=True)

    def mutate(content: dict, _created_table: bool) -> dict:
        ensure_worksheet(content, sheet_name)
        write_cells(content, sheet_name, matrix, start_row, start_column)
        return {}

    saved, _, _ = _mutate(api, project["id"], table_name, mutate)
    return {
        "ok": True,
        "action": "write",
        "project_name": project_name,
        "table_name": table_name,
        "sheet_name": sheet_name,
        "start_cell": (start_cell or "A1").strip().upper(),
        "rows": len(matrix),
        "cols": max(len(row) for row in matrix),
        "revision": saved["revision"],
    }


def read_spreadsheet(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str = "A1",
    end_cell: str = "",
) -> dict:
    """按名称读取单元格：项目名、表名、sheet 名。

    默认从 start_cell（A1）读到已用区域右下角。指定 end_cell 则读该矩形。
    公式以 = 开头的字符串返回。不创建缺失的项目/表格/sheet，也不渲染表格。
    """
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    table_name = trim_name(table_name, "表名")
    sheet_name = trim_name(sheet_name, "sheet名")
    start_row, start_column = parse_a1(start_cell)
    end_row = end_column = None
    if (end_cell or "").strip():
        end_row, end_column = parse_a1(end_cell)
        if end_row < start_row or end_column < start_column:
            raise ValueError("end_cell 必须在 start_cell 的右下方。")
    project, _ = _ensure_project(api, project_name, create=False)
    table, _ = _ensure_table(api, project["id"], table_name, create=False)
    content = workbook_from_asset(table, table_name)
    names = sheet_names(content)
    if not find_sheet(content, sheet_name):
        extra = "、".join(names) if names else "无"
        raise ValueError(f"工作表「{sheet_name}」不存在。已有：{extra}。")
    values = read_cells(content, sheet_name, start_row, start_column, end_row, end_column)
    if values:
        last_cell = to_a1(start_row + len(values) - 1, start_column + max(len(row) for row in values) - 1)
    else:
        last_cell = (start_cell or "A1").strip().upper()
    return {
        "ok": True,
        "action": "read",
        "project_name": project_name,
        "table_name": table_name,
        "sheet_name": sheet_name,
        "sheet_names": names,
        "start_cell": (start_cell or "A1").strip().upper(),
        "end_cell": last_cell,
        "values": values,
        "rows": len(values),
        "cols": max((len(row) for row in values), default=0),
        "revision": table.get("revision", 0),
    }


def merge_cells(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str,
    end_cell: str,
) -> dict:
    """合并单元格：项目名、表名、sheet 名，从 start_cell 到 end_cell。重叠的已有合并会报错。"""
    saved, extra = _named_write(
        project_name, table_name, sheet_name,
        lambda content: merge_range(content, sheet_name, start_cell, end_cell),
    )
    return _named_result("merge", project_name, table_name, sheet_name, saved, extra, start_cell, end_cell)


def unmerge_cells(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str,
    end_cell: str = "",
) -> dict:
    """取消合并：项目名、表名、sheet 名。去掉与 start_cell:end_cell 重叠的合并。省略 end_cell 则只看起点所在合并。"""
    saved, extra = _named_write(
        project_name, table_name, sheet_name,
        lambda content: unmerge_range(content, sheet_name, start_cell, end_cell),
        create_missing=False,
    )
    return _named_result("unmerge", project_name, table_name, sheet_name, saved, extra, start_cell, end_cell)


def set_cell_background(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str = "A1",
    end_cell: str = "",
    color: str = "",
) -> dict:
    """设置单元格背景色：项目名、表名、sheet 名。color 为 #RRGGBB 或 red/yellow 等；空或 none 表示清除。"""
    saved, extra = _named_write(
        project_name, table_name, sheet_name,
        lambda content: set_background(content, sheet_name, start_cell, end_cell, color),
    )
    return _named_result("background", project_name, table_name, sheet_name, saved, extra, start_cell, end_cell)


def set_cell_border(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str = "A1",
    end_cell: str = "",
    style: str = "thin",
    color: str = "#000000",
    sides: str = "all",
) -> dict:
    """设置单元格边框：style 为 none/thin/medium/thick/dashed/dotted/double；sides 为 all/outside/inside/top/bottom/left/right。"""
    saved, extra = _named_write(
        project_name, table_name, sheet_name,
        lambda content: set_border(content, sheet_name, start_cell, end_cell, style, color, sides),
    )
    return _named_result("border", project_name, table_name, sheet_name, saved, extra, start_cell, end_cell)


def set_cell_font(
    project_name: str,
    table_name: str,
    sheet_name: str,
    start_cell: str = "A1",
    end_cell: str = "",
    font_family: str = "",
    font_size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    strikethrough: bool | None = None,
    color: str = "",
    align: str = "",
    vertical_align: str = "",
) -> dict:
    """设置字体：可改 font_family、font_size、bold、italic、underline、strikethrough、color、align、vertical_align。只改提供的项。"""
    saved, extra = _named_write(
        project_name, table_name, sheet_name,
        lambda content: set_font(
            content, sheet_name, start_cell, end_cell,
            font_family, font_size, bold, italic, underline, strikethrough, color, align, vertical_align,
        ),
    )
    return _named_result("font", project_name, table_name, sheet_name, saved, extra, start_cell, end_cell)


def register_tools(mcp) -> None:
    mcp.tool()(create_spreadsheet)
    mcp.tool()(read_spreadsheet)
    mcp.tool()(write_spreadsheet)
    mcp.tool()(merge_cells)
    mcp.tool()(unmerge_cells)
    mcp.tool()(set_cell_background)
    mcp.tool()(set_cell_border)
    mcp.tool()(set_cell_font)


def _ready():
    api = client_for()
    me = api.me() or {}
    if not (me.get("email") or "").strip():
        raise OfficeError(401, "令牌无法对应到登录邮箱。")
    return api


def _named(items: list[dict], name: str, label: str) -> dict | None:
    matches = [item for item in items if item.get("name") == name]
    if len(matches) > 1:
        raise ValueError(f"存在多个同名{label}「{name}」，请先在工作区重命名后再操作。")
    return matches[0] if matches else None


def _ensure_project(api, name: str, create: bool) -> tuple[dict, bool]:
    found = _named(api.projects(), name, "项目")
    if found:
        return found, False
    if not create:
        raise ValueError(f"项目「{name}」不存在。")
    return api.create_project(name), True


def _ensure_table(api, project_id: str, name: str, create: bool) -> tuple[dict, bool]:
    found = _named(api.assets(project_id, "sheet"), name, "表格")
    created = False
    if not found:
        if not create:
            raise ValueError(f"表格「{name}」不存在。")
        found = api.create_asset(project_id, name)
        created = True
    asset = api.get_asset(found["id"])
    if asset.get("kind") != "sheet":
        raise ValueError(f"「{name}」不是表格，不能用本接口访问。")
    return asset, created


def _named_write(
    project_name: str,
    table_name: str,
    sheet_name: str,
    mutate: Callable[[dict], dict],
    create_missing: bool = True,
) -> tuple[dict, dict]:
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    table_name = trim_name(table_name, "表名")
    sheet_name = trim_name(sheet_name, "sheet名")
    project, _ = _ensure_project(api, project_name, create=create_missing)

    def wrapped(content: dict, _created_table: bool) -> dict:
        if create_missing:
            ensure_worksheet(content, sheet_name)
        elif not find_sheet(content, sheet_name):
            names = sheet_names(content)
            extra = "、".join(names) if names else "无"
            raise ValueError(f"工作表「{sheet_name}」不存在。已有：{extra}。")
        return mutate(content) or {}

    saved, _, extra = _mutate(api, project["id"], table_name, wrapped, create_table=create_missing)
    return saved, extra


def _named_result(
    action: str,
    project_name: str,
    table_name: str,
    sheet_name: str,
    saved: dict,
    extra: dict,
    start_cell: str,
    end_cell: str,
) -> dict:
    result = {
        "ok": True,
        "action": action,
        "project_name": trim_name(project_name, "项目名"),
        "table_name": trim_name(table_name, "表名"),
        "sheet_name": trim_name(sheet_name, "sheet名"),
        "start_cell": (start_cell or "A1").strip().upper(),
        "revision": saved["revision"],
    }
    if (end_cell or "").strip():
        result["end_cell"] = end_cell.strip().upper()
    result.update(extra)
    return result


def _mutate(
    api,
    project_id: str,
    table_name: str,
    mutate: Callable[[dict, bool], dict],
    create_table: bool = True,
) -> tuple[dict, bool, dict]:
    last_error: OfficeError | None = None
    for _ in range(2):
        table, created_table = _ensure_table(api, project_id, table_name, create=create_table)
        content = workbook_from_asset(table, table_name)
        extra = mutate(content, created_table)
        try:
            saved = api.save_asset(table["id"], content, table.get("revision", 0), name=table_name)
            return saved, created_table, extra
        except OfficeError as exc:
            last_error = exc
            if exc.status != 409:
                raise
    raise last_error or OfficeError(409, "内容已在其他会话更新，请重试。")
