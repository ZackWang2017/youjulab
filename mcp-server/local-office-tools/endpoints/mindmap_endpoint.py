"""思维导图 HTTP / MCP 入口。"""
from __future__ import annotations

from starlette.responses import JSONResponse

from mindmap_service import (
    MindmapServiceError,
    add_mindmap_node,
    create_mindmap,
    delete_mindmap_node,
    move_mindmap_node,
    read_mindmap,
    update_mindmap_node,
    write_mindmap,
)
from spreadsheet_service import SpreadsheetServiceError


def register_tools(mcp) -> None:
    mcp.tool()(create_mindmap)
    mcp.tool()(read_mindmap)
    mcp.tool()(write_mindmap)
    mcp.tool()(add_mindmap_node)
    mcp.tool()(update_mindmap_node)
    mcp.tool()(delete_mindmap_node)
    mcp.tool()(move_mindmap_node)


def register_routes(app, cors_headers: dict[str, str]) -> None:
    for name in ("create", "read", "write", "add-node", "update-node", "delete-node", "move-node"):
        app.add_route(f"/api/mindmaps/{name}", _make(name, cors_headers), methods=["POST"])


def _make(action: str, cors_headers: dict[str, str]):
    async def handler(request):
        return await _handle(request, cors_headers, action)

    return handler


async def _handle(request, cors_headers: dict[str, str], action: str) -> JSONResponse:
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
        return JSONResponse(result, headers=cors_headers)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)
    except (MindmapServiceError, SpreadsheetServiceError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors_headers)
    except Exception:
        return JSONResponse({"error": "导图请求失败，请检查 office-backend 是否已启动。"}, status_code=502, headers=cors_headers)
