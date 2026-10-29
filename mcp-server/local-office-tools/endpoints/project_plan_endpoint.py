"""项目计划 HTTP / MCP 入口。"""
from __future__ import annotations

from starlette.responses import JSONResponse

from project_plan_service import (
    ProjectPlanServiceError, add_project_plan_task, create_project_plan, delete_project_plan_task,
    move_project_plan_task, read_project_plan, update_project_plan_task,
)
from spreadsheet_service import SpreadsheetServiceError


def register_tools(mcp) -> None:
    for tool in (create_project_plan, read_project_plan, add_project_plan_task, update_project_plan_task, move_project_plan_task, delete_project_plan_task):
        mcp.tool()(tool)


def register_routes(app, cors_headers: dict[str, str]) -> None:
    for action in ("create", "read", "add-task", "update-task", "move-task", "delete-task"):
        app.add_route(f"/api/project-plans/{action}", _make(action, cors_headers), methods=["POST"])


def _make(action: str, cors_headers: dict[str, str]):
    async def handler(request):
        return await _handle(request, cors_headers, action)
    return handler


async def _handle(request, cors_headers: dict[str, str], action: str) -> JSONResponse:
    try:
        body = await request.json()
        common = (body["project_name"], body["plan_name"])
        if action == "create":
            result = create_project_plan(*common)
        elif action == "read":
            result = read_project_plan(*common)
        elif action == "add-task":
            result = add_project_plan_task(*common, body["name"], body.get("parent_id") or "", body.get("start_date") or "", body.get("end_date") or "", body.get("progress", 0), body.get("note") or "", body.get("task_id") or "")
        elif action == "update-task":
            result = update_project_plan_task(*common, body["task_id"], body.get("name"), body.get("start_date"), body.get("end_date"), body.get("progress"), body.get("note"))
        elif action == "move-task":
            result = move_project_plan_task(*common, body["task_id"], body.get("parent_id") or "", body.get("index", -1))
        else:
            result = delete_project_plan_task(*common, body["task_id"])
        return JSONResponse(result, headers=cors_headers)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)
    except (ProjectPlanServiceError, SpreadsheetServiceError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors_headers)
    except Exception:
        return JSONResponse({"error": "项目计划请求失败，请检查 office-backend 是否已启动。"}, status_code=502, headers=cors_headers)
