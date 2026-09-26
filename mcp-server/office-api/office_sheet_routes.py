"""表格创建 / 读取 / 写入 / 样式 HTTP，与财务查询、选区 Agent、PDF 渲染分开。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.responses import JSONResponse

from office_sheet_client import OfficeError
from office_sheets import (
    create_spreadsheet,
    merge_cells,
    read_spreadsheet,
    set_cell_background,
    set_cell_border,
    set_cell_font,
    unmerge_cells,
    write_spreadsheet,
)


def register_routes(app, cors_headers: dict[str, str]) -> None:
    for name in ("create", "read", "write", "merge", "unmerge", "background", "border", "font"):
        app.add_route(f"/api/spreadsheets/{name}", _make(name, cors_headers), methods=["POST"])


def mount_sheet_mcp(host_app, sheets_mcp) -> None:
    """把表格 MCP 挂到同一进程的 /mcp/sheets，不占用财务 /mcp。"""
    sheets_app = sheets_mcp.streamable_http_app()
    host_app.router.routes.extend(list(sheets_app.routes))
    inner = host_app.router.lifespan_context
    sheets_life = sheets_app.router.lifespan_context

    @asynccontextmanager
    async def combined(scope_app):
        async with inner(scope_app):
            async with sheets_life(scope_app):
                yield

    host_app.router.lifespan_context = combined


def _make(action: str, cors_headers: dict[str, str]):
    async def handler(request):
        return await _handle(request, cors_headers, action)

    return handler


async def _handle(request, cors_headers: dict[str, str], action: str) -> JSONResponse:
    try:
        body = await request.json()
        project_name = body["project_name"]
        table_name = body["table_name"]
        sheet_name = body["sheet_name"]
        start_cell = body.get("start_cell") or "A1"
        end_cell = body.get("end_cell") or ""
        if action == "create":
            result = create_spreadsheet(project_name, table_name, sheet_name)
        elif action == "read":
            result = read_spreadsheet(project_name, table_name, sheet_name, start_cell, end_cell)
        elif action == "write":
            result = write_spreadsheet(project_name, table_name, sheet_name, body.get("values"), start_cell)
        elif action == "merge":
            result = merge_cells(project_name, table_name, sheet_name, start_cell, end_cell)
        elif action == "unmerge":
            result = unmerge_cells(project_name, table_name, sheet_name, start_cell, end_cell)
        elif action == "background":
            result = set_cell_background(
                project_name, table_name, sheet_name, start_cell, end_cell, body.get("color") or "",
            )
        elif action == "border":
            result = set_cell_border(
                project_name, table_name, sheet_name, start_cell, end_cell,
                body.get("style") or "thin",
                body.get("color") or "#000000",
                body.get("sides") or "all",
            )
        else:
            result = set_cell_font(
                project_name, table_name, sheet_name, start_cell, end_cell,
                body.get("font_family") or "",
                body.get("font_size"),
                body.get("bold"),
                body.get("italic"),
                body.get("underline"),
                body.get("strikethrough"),
                body.get("color") or "",
                body.get("align") or "",
                body.get("vertical_align") or "",
            )
        return JSONResponse(result, headers=cors_headers)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)
    except OfficeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors_headers)
    except Exception:
        return JSONResponse({"error": "表格请求失败，请检查 office-api 是否已启动。"}, status_code=502, headers=cors_headers)
