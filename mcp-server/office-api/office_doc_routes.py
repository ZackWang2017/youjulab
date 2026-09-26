"""文档创建 / 读取 / 修改 / 格式 HTTP，与财务查询、表格读写、选区 Agent 分开。"""
from __future__ import annotations

from starlette.responses import JSONResponse

from office_docs import create_document, format_document, read_document, write_document
from office_sheet_client import OfficeError


def register_routes(app, cors_headers: dict[str, str]) -> None:
    for name in ("create", "read", "write", "format"):
        app.add_route(f"/api/documents/{name}", _make(name, cors_headers), methods=["POST"])


def _make(action: str, cors_headers: dict[str, str]):
    async def handler(request):
        return await _handle(request, cors_headers, action)

    return handler


async def _handle(request, cors_headers: dict[str, str], action: str) -> JSONResponse:
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
        return JSONResponse(result, headers=cors_headers)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)
    except OfficeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors_headers)
    except Exception:
        return JSONResponse({"error": "文档请求失败，请检查 office-api 是否已启动。"}, status_code=502, headers=cors_headers)
