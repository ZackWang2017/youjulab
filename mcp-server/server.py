"""投研数据 MCP 服务 —— 支持两种运行形态：

1) 本地 stdio（默认，DSH 子进程拉起）：FINANCE_MCP_TRANSPORT=stdio
   .venv/bin/python mcp-server/server.py

2) 独立第三方 REST 服务（streamable-http，DSH 远程 URL 调用）：
   FINANCE_MCP_TRANSPORT=http .venv/bin/python mcp-server/server.py
   启动后监听 http://<host>:<port>/mcp（默认 0.0.0.0:8000）

环境变量：
  FINANCE_MCP_TRANSPORT  stdio（默认）| http
  FINANCE_MCP_HOST       http 模式监听地址（默认 0.0.0.0）
  FINANCE_MCP_PORT       http 模式端口（默认 8000）
  MCP_ACCESS_TOKEN       静态 32 位访问令牌（http 模式必填；有默认值，不签发）
  ANALYSIS_PROFILE       analysis-profile.yaml 绝对路径（默认 ../research-config/analysis-profile.yaml）
  DATA_PROVIDER          mock（默认）| real

DSH 侧接入（dsh-mcp-client 插件）见 agent-preset/investment-research/agent.cordis.yml，
模型看到的工具名始终是 mcp__finresearch__<tool>，与传输方式无关。

设计要点：指标数值全部在本服务内确定性计算，模型只负责取数与解读。
表格创建/读取/写入/合并/样式走同一进程的 /mcp/sheets 与 /api/spreadsheets/*，与财务 /mcp、
/api/financial、PDF 渲染、选区 /api/office 分开。外部搜索等走 /mcp/external-tools。
财务与外部工具入口用静态 MCP_ACCESS_TOKEN；表格入口使用办公工作区每个用户自己的
32 位访问令牌，用它找到登录邮箱账号后再挂项目。
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from uuid import uuid4

# mcp 1.x Settings.lifespan 含前向引用，pydantic-settings 会打无害警告（不影响运行）
warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition",
    category=Warning,
    module=r"pydantic_settings.*",
)

import yaml
from mcp.server.fastmcp import FastMCP

# 允许直接从项目根运行本文件（python3 mcp-server/server.py）
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "office-api"))

import data_api  # noqa: E402
import indicators  # noqa: E402
import screening  # noqa: E402
import pdf_report  # noqa: E402
import office_context  # noqa: E402
import office_patch  # noqa: E402
import office_agent  # noqa: E402
import office_sheets  # noqa: E402
import office_docs  # noqa: E402
import office_pdf_rag  # noqa: E402
import external_tools  # noqa: E402
from access_token import bearer_matches, bearer_token, is_user_office_path, reset_incoming_access_token, set_incoming_access_token  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE.parent / ".env")
PROFILE_PATH = Path(
    os.environ.get("ANALYSIS_PROFILE", HERE.parent / "research-config" / "analysis-profile.yaml")
)
SCREENING_PATH = Path(
    os.environ.get("SCREENING_PROFILE", HERE.parent / "research-config" / "screening-profile.yaml")
)

mcp = FastMCP("finresearch")
office_context.register_tools(mcp)
office_patch.register_tools(mcp)
office_agent.register_tools(mcp)
office_pdf_rag.register_tools(mcp)
sheets_mcp = FastMCP("invp-sheets", streamable_http_path="/mcp/sheets")
office_sheets.register_tools(sheets_mcp)
docs_mcp = FastMCP("invp-documents", streamable_http_path="/mcp/documents")
office_docs.register_tools(docs_mcp)
external_mcp = FastMCP("invp-external-tools", streamable_http_path="/mcp/external-tools")
external_tools.register_tools(external_mcp)
_provider = data_api.get_provider()


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_profile() -> dict:
    return _load_yaml(PROFILE_PATH)


@mcp.tool()
def get_profile() -> dict:
    """返回当前分析档案：指标清单、阈值、权重、评级线、报告模板。与 analysis-profile.yaml 同源。

    远程部署时分析档案只存在于服务端，本工具是模型获取口径的唯一可靠途径。
    """
    p = _load_profile()
    return {
        "meta": p["meta"],
        "scoring": p["scoring"],
        "indicators": p["indicators"],
        "report_template": p["report_template"],
    }


@mcp.tool()
def list_tickers() -> list:
    """列出当前数据提供者可分析的标的代码。"""
    return _provider.list_tickers()


@mcp.tool()
def get_company_info(ticker: str) -> dict:
    """获取标的基本面信息（名称、行业、业务、币种、数据期间）。"""
    return _provider.get_company_info(ticker)


@mcp.tool()
def get_statements(ticker: str, period: str = "latest") -> dict:
    """获取原始财务报表（period: latest / y1 / y2）。用于抽查明细与交叉验证，不要据此自行重算指标。"""
    return _provider.get_statements(ticker, period)


@mcp.tool()
def get_indicators(ticker: str) -> dict:
    """核心工具：按分析档案确定性计算全部指标（三期序列、趋势、分维度得分、总分与 A/B/C/D 评级）。

    所有数字以此返回为准：模型不得自行估算或按原始报表重算。
    """
    profile = _load_profile()
    statements = {p: _provider.get_statements(ticker, p) for p in data_api.PERIODS}
    result = indicators.compute(profile, statements)
    result["company"] = _provider.get_company_info(ticker)
    result["periods"] = data_api.PERIODS
    return result


@mcp.tool()
def screen_stock(ticker: str) -> dict:
    """初筛工具：按初筛指标档案（screening-profile.yaml）的 12 项指标逐项确定性判定（股票第一步筛选）。

    返回每项：数值、判定（pass 通过 / warning 提示 / fail 不得分 / exclude 一票排除 / pending 占位）、
    判定依据文本、得分；以及汇总：结论（pass / review / fail / exclude）、总分、排除项/警告项清单。
    模型不得自行重算指标；解读规则以本返回与 `initial-screening` skill 为准。
    """
    profile = _load_yaml(SCREENING_PATH)
    statements = {p: _provider.get_statements(ticker, p) for p in data_api.PERIODS}
    result = screening.compute(profile, statements)
    result["company"] = _provider.get_company_info(ticker)
    return result


# ---------------------------------------------------------------------------
# 读财报：PDF 解析（伴读场景）
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_pdf(path: str) -> dict:
    """解析 PDF 财报：一次性提取全文并分页缓存。

    返回：页数、分类初判（年报/半年报/一季报/三季报 + 港股/A股/美股）、
    章节页码索引（三表/审计报告/经营情况讨论等）、封面预览、扫描件标记。
    之后用 pdf_page / pdf_text 按需取文本（已缓存，零重复解析）。
    """
    return pdf_report.parse(path)


@mcp.tool()
def pdf_page(path: str, page: int) -> dict:
    """取 PDF 某一页的文本（page 为 1-based）。逐页伴读用：一次只解释当前页。"""
    return pdf_report.page_text(path, page)


@mcp.tool()
def pdf_text(path: str, start_page: int, end_page: int) -> dict:
    """取 PDF 页区间文本（含页码分隔标记）。深度分析分块用：每次最多 100 页、30 万字符。"""
    return pdf_report.range_text(path, start_page, end_page)


# ---------------------------------------------------------------------------
# 运行形态
# ---------------------------------------------------------------------------

def _run_http() -> None:
    """同一进程：财务 /mcp、表格 /mcp/sheets、外部工具 /mcp/external-tools，以及各自 REST。"""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    app = mcp.streamable_http_app()
    from office_routes import register_routes
    from office_sheet_routes import mount_sheet_mcp, register_routes as register_sheet_routes
    from office_doc_routes import register_routes as register_document_routes
    register_routes(app)

    _CORS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }
    register_sheet_routes(app, _CORS)
    register_document_routes(app, _CORS)
    mount_sheet_mcp(app, sheets_mcp)
    mount_sheet_mcp(app, docs_mcp)
    external_tools.mount_external_mcp(app, external_mcp)

    async def guard(request, call_next):
        # CORS 预检
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=_CORS)
        header = request.headers.get("authorization", "")
        token = bearer_token(header)
        if is_user_office_path(request.url.path):
            if not token:
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_CORS)
            saved = set_incoming_access_token(token)
            try:
                response = await call_next(request)
            finally:
                reset_incoming_access_token(saved)
            response.headers.update(_CORS)
            return response
        if not bearer_matches(header):
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=_CORS)
        response = await call_next(request)
        response.headers.update(_CORS)
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=guard)
    print(
        "mcp-server: finance+external-tools use MCP_ACCESS_TOKEN; sheets and documents use per-user access token",
        file=sys.stderr,
    )

    # ── /upload：浏览器直传 PDF（chat 框上传用）─────────────────────────────
    upload_root = Path(os.environ.get("FINANCE_PDF_ROOT", str(HERE.parent))) / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)

    async def upload(request):
        name = request.query_params.get("name", "upload.pdf")
        safe = Path(name).name  # 防路径穿越
        if not safe.lower().endswith(".pdf"):
            safe = f"{safe}.pdf"
        body = await request.body()
        if not body:
            return JSONResponse({"error": "empty body"}, status_code=400, headers=_CORS)
        target = upload_root / f"{uuid4().hex[:8]}-{safe}"
        target.write_bytes(body)
        return JSONResponse({
            "path": str(target),
            "name": safe,
            "size_bytes": len(body),
            "hint": "把这个 path 告诉模型：解析 <path>",
        }, headers=_CORS)

    app.add_route("/upload", upload, methods=["POST"])

    # ── 读财报 UI 端点：parse / page-text / page-image（浏览器双栏视图直查）──
    async def parse_rest(request):
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "missing path"}, status_code=400, headers=_CORS)
        try:
            return JSONResponse(pdf_report.parse(path), headers=_CORS)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=_CORS)

    app.add_route("/parse", parse_rest, methods=["GET"])

    async def page_text_rest(request):
        path = request.query_params.get("path", "")
        page = request.query_params.get("page", "1")
        try:
            return JSONResponse(pdf_report.page_text(path, int(page)), headers=_CORS)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=_CORS)

    app.add_route("/page-text", page_text_rest, methods=["GET"])

    async def page_image_rest(request):
        path = request.query_params.get("path", "")
        page = request.query_params.get("page", "1")
        dpi = request.query_params.get("dpi", "110")
        try:
            return JSONResponse(pdf_report.page_image(path, int(page), int(dpi)), headers=_CORS)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=_CORS)

    app.add_route("/page-image", page_image_rest, methods=["GET"])

    # ── /pdf：原生 PDF 字节（<iframe> 直接渲染；page 缺省返回整份，否则单页）──
    from starlette.responses import Response

    async def pdf_rest(request):
        path = request.query_params.get("path", "")
        page_raw = request.query_params.get("page", "")
        try:
            page = None if page_raw == "" else int(page_raw)
            result = pdf_report.pdf_bytes(path, page)
            return Response(content=result["bytes"], media_type="application/pdf", headers=_CORS)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400, headers=_CORS)

    app.add_route("/pdf", pdf_rest, methods=["GET"])

    # ── 财务数据 REST API（mcp-visual 等前端消费）────────────────────────────
    import importlib.util

    financial_api_path = HERE / "data-api" / "financial-data-api.py"
    spec = importlib.util.spec_from_file_location("financial_data_api", financial_api_path)
    financial_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(financial_api)
    financial_api.register_routes(app, cors_headers=_CORS)

    host = os.environ.get("FINANCE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("FINANCE_MCP_PORT", "8000"))
    print(f"finresearch MCP server: streamable-http at http://{host}:{port}/mcp", file=sys.stderr)
    print(f"invp-sheets MCP: streamable-http at http://{host}:{port}/mcp/sheets", file=sys.stderr)
    print(f"invp-documents MCP: streamable-http at http://{host}:{port}/mcp/documents", file=sys.stderr)
    print(f"invp-external-tools MCP: streamable-http at http://{host}:{port}/mcp/external-tools", file=sys.stderr)
    print(f"spreadsheet API: POST http://{host}:{port}/api/spreadsheets/{{create,read,write,merge,unmerge,background,border,font}}", file=sys.stderr)
    print(f"document API: POST http://{host}:{port}/api/documents/{{create,read,write,format}}", file=sys.stderr)
    print(f"finresearch upload endpoint: POST http://{host}:{port}/upload?name=<文件名>.pdf", file=sys.stderr)
    print(f"finresearch financial API: GET http://{host}:{port}/api/financial/statements", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    transport = os.environ.get("FINANCE_MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        _run_http()
    else:
        # stdio 传输：DSH 的 dsh-mcp-client 以子进程方式拉起
        print("finresearch MCP server: stdio (waiting for DSH client)", file=sys.stderr)
        mcp.run(transport="stdio")
