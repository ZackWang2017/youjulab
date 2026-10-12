"""财报 PDF 的 MCP 工具与浏览器读取入口。"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from starlette.responses import JSONResponse, Response

import pdf_report_service as pdf_report


def register_tools(mcp) -> None:
    @mcp.tool()
    def parse_pdf(path: str) -> dict:
        """解析 PDF 财报：一次性提取全文并分页缓存。"""
        return pdf_report.parse(path)

    @mcp.tool()
    def pdf_page(path: str, page: int) -> dict:
        """取 PDF 某一页的文本（page 为 1-based）。"""
        return pdf_report.page_text(path, page)

    @mcp.tool()
    def pdf_text(path: str, start_page: int, end_page: int) -> dict:
        """取 PDF 页区间文本。深度分析分块用：每次最多 100 页、30 万字符。"""
        return pdf_report.range_text(path, start_page, end_page)


def register_routes(app, cors_headers: dict[str, str], upload_root: Path) -> None:
    upload_root.mkdir(parents=True, exist_ok=True)

    async def upload(request):
        name = request.query_params.get("name", "upload.pdf")
        safe = Path(name).name
        if not safe.lower().endswith(".pdf"):
            safe = f"{safe}.pdf"
        body = await request.body()
        if not body:
            return JSONResponse({"error": "empty body"}, status_code=400, headers=cors_headers)
        target = upload_root / f"{uuid4().hex[:8]}-{safe}"
        target.write_bytes(body)
        return JSONResponse({
            "path": str(target),
            "name": safe,
            "size_bytes": len(body),
            "hint": "把这个 path 告诉模型：解析 <path>",
        }, headers=cors_headers)

    async def parse_rest(request):
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "missing path"}, status_code=400, headers=cors_headers)
        try:
            return JSONResponse(pdf_report.parse(path), headers=cors_headers)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)

    async def page_text_rest(request):
        path = request.query_params.get("path", "")
        page = request.query_params.get("page", "1")
        try:
            return JSONResponse(pdf_report.page_text(path, int(page)), headers=cors_headers)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)

    async def page_image_rest(request):
        path = request.query_params.get("path", "")
        page = request.query_params.get("page", "1")
        dpi = request.query_params.get("dpi", "110")
        try:
            return JSONResponse(pdf_report.page_image(path, int(page), int(dpi)), headers=cors_headers)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)

    async def pdf_rest(request):
        path = request.query_params.get("path", "")
        page_raw = request.query_params.get("page", "")
        try:
            page = None if page_raw == "" else int(page_raw)
            result = pdf_report.pdf_bytes(path, page)
            return Response(content=result["bytes"], media_type="application/pdf", headers=cors_headers)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=cors_headers)

    app.add_route("/upload", upload, methods=["POST"])
    app.add_route("/parse", parse_rest, methods=["GET"])
    app.add_route("/page-text", page_text_rest, methods=["GET"])
    app.add_route("/page-image", page_image_rest, methods=["GET"])
    app.add_route("/pdf", pdf_rest, methods=["GET"])


def default_upload_root(server_dir: Path) -> Path:
    return Path(os.environ.get("FINANCE_PDF_ROOT", str(server_dir.parent))) / "uploads"
