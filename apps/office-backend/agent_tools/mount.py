"""在 office-backend 挂载外部 Agent 的 MCP / REST 办公工具入口。"""
from __future__ import annotations

import warnings
from contextlib import asynccontextmanager
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from config import office_api_base

from agent_tools.announcement_outline_service import generate_pdf_outline
from agent_tools.document_service import DocumentServiceError, create_document, format_document, read_document, write_document
from agent_tools.errors import OfficeError
from agent_tools.diagram_service import create_diagram, read_diagram, write_diagram
from agent_tools.mindmap_service import (
    MindmapServiceError,
    add_mindmap_node,
    create_mindmap,
    delete_mindmap_node,
    move_mindmap_node,
    read_mindmap,
    update_mindmap_node,
    write_mindmap,
)
from agent_tools.paths import is_user_office_path
from agent_tools.pdf_index_service import ensure_office_pdf_index, get_office_pdf_index_status, search_office_pdf
from agent_tools.project_plan_service import (
    ProjectPlanServiceError,
    add_project_plan_task,
    create_project_plan,
    delete_project_plan_task,
    move_project_plan_task,
    read_project_plan,
    update_project_plan_task,
)
from agent_tools.spreadsheet_service import (
    SpreadsheetServiceError,
    create_spreadsheet,
    merge_cells,
    read_spreadsheet,
    set_cell_background,
    set_cell_border,
    set_cell_font,
    unmerge_cells,
    write_spreadsheet,
)
from agent_tools.store_client import bind_agent_context, reset_agent_context
from model_runtime import set_current_model, settings_for_user
from store import ACCESS_TOKEN_LENGTH

warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition",
    category=Warning,
    module=r"pydantic_settings.*",
)

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def _bearer(header: str) -> str:
    if not (header or "").lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()


def _workspace_account(user: dict) -> dict:
    user_id = (user.get("id") or "").strip()
    email = (user.get("email") or "").strip().lower()
    if not user_id or not email:
        raise OfficeError(401, "令牌无法对应到登录邮箱。")
    return user


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return JSONResponse({"error": str(exc)}, status_code=400, headers=_CORS)
    if isinstance(
        exc,
        (SpreadsheetServiceError, DocumentServiceError, MindmapServiceError, ProjectPlanServiceError, OfficeError),
    ):
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=_CORS)
    return JSONResponse({"error": "办公工具请求失败。"}, status_code=502, headers=_CORS)


def mount_agent_tools(app: FastAPI, *, resolve_user: Callable[[Any, str], dict | None] | None = None) -> None:
    """挂载 /mcp/{sheets,documents,mindmaps,project-plans,diagrams} 与对应 REST。"""
    sheets_mcp = FastMCP("youju-sheets", streamable_http_path="/mcp/sheets")
    for tool in (
        create_spreadsheet,
        read_spreadsheet,
        write_spreadsheet,
        merge_cells,
        unmerge_cells,
        set_cell_background,
        set_cell_border,
        set_cell_font,
    ):
        sheets_mcp.tool()(tool)

    docs_mcp = FastMCP("youju-documents", streamable_http_path="/mcp/documents")
    for tool in (create_document, read_document, write_document, format_document):
        docs_mcp.tool()(tool)
    docs_mcp.tool()(ensure_office_pdf_index)
    docs_mcp.tool()(get_office_pdf_index_status)
    docs_mcp.tool()(search_office_pdf)
    docs_mcp.tool()(generate_pdf_outline)

    mindmaps_mcp = FastMCP("youju-mindmaps", streamable_http_path="/mcp/mindmaps")
    for tool in (
        create_mindmap,
        read_mindmap,
        write_mindmap,
        add_mindmap_node,
        update_mindmap_node,
        delete_mindmap_node,
        move_mindmap_node,
    ):
        mindmaps_mcp.tool()(tool)

    plans_mcp = FastMCP("youju-project-plans", streamable_http_path="/mcp/project-plans")
    for tool in (
        create_project_plan,
        read_project_plan,
        add_project_plan_task,
        update_project_plan_task,
        move_project_plan_task,
        delete_project_plan_task,
    ):
        plans_mcp.tool()(tool)

    _register_rest(app)
    public_base = urlsplit(office_api_base())
    hosts = ['127.0.0.1', 'localhost', '[::1]', '127.0.0.1:*', 'localhost:*', '[::1]:*']
    origins = ['http://127.0.0.1', 'http://localhost', 'http://[::1]', 'http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*']
    if public_base.scheme in {'http', 'https'} and public_base.netloc:
        hosts.append(public_base.netloc)
        origins.append(f'{public_base.scheme}://{public_base.netloc}')
    diagrams_mcp = FastMCP('youju-diagrams', streamable_http_path='/mcp/diagrams', stateless_http=True,
                          transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins))
    for tool in (create_diagram, read_diagram, write_diagram):
        diagrams_mcp.tool()(tool)
    for mcp in (sheets_mcp, docs_mcp, mindmaps_mcp, plans_mcp, diagrams_mcp):
        _mount_mcp(app, mcp)

    @app.middleware("http")
    async def agent_tools_auth(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" and is_user_office_path(path):
            return JSONResponse({}, headers=_CORS)
        if not is_user_office_path(path):
            return await call_next(request)
        store = getattr(request.app.state, "store", None)
        if store is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_CORS)
        token = _bearer(request.headers.get("authorization") or "")
        if not token or len(token) != ACCESS_TOKEN_LENGTH:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_CORS)
        user = None
        if resolve_user is not None:
            user = resolve_user(store, token)
        else:
            user = store.user_by_access_token(token)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_CORS)
        try:
            account = _workspace_account(user)
        except OfficeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=_CORS)
        set_current_model(settings_for_user(store, account["id"]))
        tokens = bind_agent_context(store, account)
        try:
            response = await call_next(request)
        finally:
            reset_agent_context(*tokens)
        for key, value in _CORS.items():
            response.headers.setdefault(key, value)
        return response


def _mount_mcp(host_app: FastAPI, mcp: FastMCP) -> None:
    mcp_app = mcp.streamable_http_app()
    host_app.router.routes.extend(list(mcp_app.routes))
    inner = host_app.router.lifespan_context
    mcp_life = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def combined(scope_app):
        async with inner(scope_app):
            async with mcp_life(scope_app):
                yield

    host_app.router.lifespan_context = combined


def _register_rest(app: FastAPI) -> None:
    for name in ('create', 'read', 'write'):
        app.add_api_route(f'/api/diagrams/{name}', _diagram_handler(name), methods=['POST'])
    sheet_actions = ("create", "read", "write", "merge", "unmerge", "background", "border", "font")
    for name in sheet_actions:
        app.add_api_route(f"/api/spreadsheets/{name}", _sheet_handler(name), methods=["POST"])

    for name in ("create", "read", "write", "format"):
        app.add_api_route(f"/api/documents/{name}", _document_handler(name), methods=["POST"])

    for name in ("create", "read", "write", "add-node", "update-node", "delete-node", "move-node"):
        app.add_api_route(f"/api/mindmaps/{name}", _mindmap_handler(name), methods=["POST"])

    for name in ("create", "read", "add-task", "update-task", "move-task", "delete-task"):
        app.add_api_route(f"/api/project-plans/{name}", _plan_handler(name), methods=["POST"])


def _diagram_handler(action: str):
    async def handler(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('请求必须是 JSON 对象。')
            if action == 'create':
                result = create_diagram(body['project_name'], body['diagram_name'],
                                        body.get('graph'), body.get('xml', ''), body.get('visibility', 'project'))
            else:
                common = {key: body.get(key, '') for key in ('project_name', 'diagram_name', 'asset_id')}
                if action == 'read':
                    include = body.get('include_xml', False)
                    if type(include) is not bool:
                        raise ValueError('include_xml 必须为布尔值。')
                    result = read_diagram(**common, include_xml=include)
                else:
                    result = write_diagram(body['expected_revision'], **common, graph=body.get('graph'), xml=body.get('xml', ''))
            return JSONResponse(result, headers=_CORS)
        except Exception as exc:
            return _error_response(exc)
    return handler


def _sheet_handler(action: str):
    async def handler(request: Request):
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
            return JSONResponse(result, headers=_CORS)
        except Exception as exc:
            return _error_response(exc)

    return handler


def _document_handler(action: str):
    async def handler(request: Request):
        try:
            body = await request.json()
            project_name = body["project_name"]
            document_name = body["document_name"]
            if action == "create":
                result = create_document(project_name, document_name)
            elif action == "read":
                result = read_document(project_name, document_name)
            elif action == "write":
                result = write_document(
                    project_name,
                    document_name,
                    body.get("markdown") or "",
                    body.get("mode") or "replace",
                    body.get("block_index", -1),
                )
            else:
                result = format_document(
                    project_name,
                    document_name,
                    body["block_index"],
                    body.get("block_type") or "",
                    body.get("bold"),
                    body.get("italic"),
                    body.get("strike"),
                    body.get("code"),
                )
            return JSONResponse(result, headers=_CORS)
        except Exception as exc:
            return _error_response(exc)

    return handler


def _mindmap_handler(action: str):
    async def handler(request: Request):
        try:
            body = await request.json()
            common = (body["project_name"], body["mindmap_name"])
            if action == "create":
                result = create_mindmap(*common, body.get("root_topic") or "")
            elif action == "read":
                result = read_mindmap(*common)
            elif action == "write":
                result = write_mindmap(*common, body["root"])
            elif action == "add-node":
                result = add_mindmap_node(
                    *common,
                    body["parent_id"],
                    body["topic"],
                    body.get("note") or "",
                    body.get("tags"),
                    body.get("hyperlink") or "",
                    body.get("node_id") or "",
                )
            elif action == "update-node":
                result = update_mindmap_node(
                    *common,
                    body["node_id"],
                    body.get("topic"),
                    body.get("note"),
                    body.get("tags"),
                    body.get("hyperlink"),
                )
            elif action == "delete-node":
                result = delete_mindmap_node(*common, body["node_id"])
            else:
                result = move_mindmap_node(
                    *common,
                    body["node_id"],
                    body["new_parent_id"],
                    body.get("index", -1),
                )
            return JSONResponse(result, headers=_CORS)
        except Exception as exc:
            return _error_response(exc)

    return handler


def _plan_handler(action: str):
    async def handler(request: Request):
        try:
            body = await request.json()
            common = (body["project_name"], body["plan_name"])
            if action == "create":
                result = create_project_plan(*common)
            elif action == "read":
                result = read_project_plan(*common)
            elif action == "add-task":
                result = add_project_plan_task(
                    *common,
                    body["name"],
                    body.get("parent_id") or "",
                    body.get("start_date") or "",
                    body.get("end_date") or "",
                    body.get("progress", 0),
                    body.get("note") or "",
                    body.get("task_id") or "",
                )
            elif action == "update-task":
                result = update_project_plan_task(
                    *common,
                    body["task_id"],
                    body.get("name"),
                    body.get("start_date"),
                    body.get("end_date"),
                    body.get("progress"),
                    body.get("note"),
                )
            elif action == "move-task":
                result = move_project_plan_task(
                    *common,
                    body["task_id"],
                    body.get("parent_id") or "",
                    body.get("index", -1),
                )
            else:
                result = delete_project_plan_task(*common, body["task_id"])
            return JSONResponse(result, headers=_CORS)
        except Exception as exc:
            return _error_response(exc)

    return handler
