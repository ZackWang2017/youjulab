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
表格、文档和导图工具分别走同一进程的 /mcp/sheets、/mcp/documents、/mcp/mindmaps 及对应
/api 路由，与财务 /mcp、/api/financial、PDF 渲染、选区 /api/office 分开。外部搜索走 /mcp/external-tools。
单页读取走 /mcp/local-tools。财务、外部搜索与本地工具入口用静态 MCP_ACCESS_TOKEN；表格入口使用办公工作区每个用户自己的
32 位访问令牌，用它找到登录邮箱账号后再挂项目。
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# mcp 1.x Settings.lifespan 含前向引用，pydantic-settings 会打无害警告（不影响运行）
warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition",
    category=Warning,
    module=r"pydantic_settings.*",
)

from mcp.server.fastmcp import FastMCP

# 允许直接从项目根运行本文件（python3 mcp-server/server.py）
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _layer in (
    "external-tools/repository",
    "external-tools/service",
    "external-tools/endpoints",
    "local-office-tools/repository",
    "local-office-tools/service",
    "local-office-tools/endpoints",
    "financial-data-service/repository",
    "financial-data-service/service",
    "financial-data-service/endpoints",
    "local-tools/repository",
    "local-tools/service",
    "local-tools/endpoints",
):
    sys.path.insert(0, str(HERE / _layer))

import market_endpoint  # noqa: E402
import pdf_report_endpoint  # noqa: E402
import office_agent_endpoint  # noqa: E402
import spreadsheet_endpoint  # noqa: E402
import document_endpoint  # noqa: E402
import mindmap_endpoint  # noqa: E402
import pdf_index_endpoint  # noqa: E402
import statement_endpoint  # noqa: E402
import security_search_endpoint  # noqa: E402
import search_endpoint as external_tools  # noqa: E402
import website_crawler_endpoint  # noqa: E402
import wechat_search_endpoint  # noqa: E402
import wechat_account_endpoint  # noqa: E402
import weread_session_service  # noqa: E402
import wechat_article_endpoint  # noqa: E402
import preset_news_endpoint  # noqa: E402
import feed_endpoint  # noqa: E402
from access_token import bearer_matches, bearer_token, is_user_office_path, reset_incoming_access_token, set_incoming_access_token  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE.parent / ".env")

mcp = FastMCP("finresearch")
market_endpoint.register_tools(mcp)
pdf_report_endpoint.register_tools(mcp)
office_agent_endpoint.register_tools(mcp)
sheets_mcp = FastMCP("invp-sheets", streamable_http_path="/mcp/sheets")
spreadsheet_endpoint.register_tools(sheets_mcp)
docs_mcp = FastMCP("invp-documents", streamable_http_path="/mcp/documents")
document_endpoint.register_tools(docs_mcp)
pdf_index_endpoint.register_tools(docs_mcp)
mindmaps_mcp = FastMCP("invp-mindmaps", streamable_http_path="/mcp/mindmaps")
mindmap_endpoint.register_tools(mindmaps_mcp)
external_mcp = FastMCP("invp-external-tools", streamable_http_path="/mcp/external-tools")
external_tools.register_tools(external_mcp)
local_mcp = FastMCP("invp-local-tools", streamable_http_path="/mcp/local-tools")
website_crawler_endpoint.register_tools(local_mcp)
wechat_search_endpoint.register_tools(local_mcp)
wechat_account_endpoint.register_tools(local_mcp)
wechat_article_endpoint.register_tools(local_mcp)
preset_news_endpoint.register_tools(local_mcp)


# ---------------------------------------------------------------------------
# 运行形态
# ---------------------------------------------------------------------------

def _run_http() -> None:
    """同一进程：财务 /mcp、表格 /mcp/sheets、外部工具 /mcp/external-tools、本地工具 /mcp/local-tools。"""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    app = mcp.streamable_http_app()
    from office_agent_endpoint import register_routes
    from spreadsheet_endpoint import mount_sheet_mcp, register_routes as register_sheet_routes
    from document_endpoint import register_routes as register_document_routes
    from mindmap_endpoint import register_routes as register_mindmap_routes
    register_routes(app)

    _CORS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }
    register_sheet_routes(app, _CORS)
    register_document_routes(app, _CORS)
    register_mindmap_routes(app, _CORS)
    mount_sheet_mcp(app, sheets_mcp)
    mount_sheet_mcp(app, docs_mcp)
    mount_sheet_mcp(app, mindmaps_mcp)
    external_tools.mount_external_mcp(app, external_mcp)
    website_crawler_endpoint.mount_local_mcp(app, local_mcp)

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
        "mcp-server: finance, external-tools and local-tools use MCP_ACCESS_TOKEN; sheets, documents and mindmaps use per-user access token",
        file=sys.stderr,
    )

    pdf_report_endpoint.register_routes(app, _CORS, pdf_report_endpoint.default_upload_root(HERE))
    statement_endpoint.register_routes(app, cors_headers=_CORS)
    security_search_endpoint.register_routes(app, _CORS)
    preset_news_endpoint.register_routes(app, _CORS)
    wechat_account_endpoint.register_routes(app, _CORS)
    feed_endpoint.register_routes(app, _CORS)

    host = os.environ.get("FINANCE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("FINANCE_MCP_PORT", "8000"))
    print(f"finresearch MCP server: streamable-http at http://{host}:{port}/mcp", file=sys.stderr)
    print(f"invp-sheets MCP: streamable-http at http://{host}:{port}/mcp/sheets", file=sys.stderr)
    print(f"invp-documents MCP: streamable-http at http://{host}:{port}/mcp/documents", file=sys.stderr)
    print(f"invp-mindmaps MCP: streamable-http at http://{host}:{port}/mcp/mindmaps", file=sys.stderr)
    print(f"invp-external-tools MCP: streamable-http at http://{host}:{port}/mcp/external-tools", file=sys.stderr)
    print(f"invp-local-tools MCP: streamable-http at http://{host}:{port}/mcp/local-tools", file=sys.stderr)
    print(f"spreadsheet API: POST http://{host}:{port}/api/spreadsheets/{{create,read,write,merge,unmerge,background,border,font}}", file=sys.stderr)
    print(f"document API: POST http://{host}:{port}/api/documents/{{create,read,write,format}}", file=sys.stderr)
    print(f"mindmap API: POST http://{host}:{port}/api/mindmaps/{{create,read,write,add-node,update-node,delete-node,move-node}}", file=sys.stderr)
    print(f"finresearch upload endpoint: POST http://{host}:{port}/upload?name=<文件名>.pdf", file=sys.stderr)
    print(f"finresearch financial API: GET http://{host}:{port}/api/financial/statements", file=sys.stderr)
    print(f"securities search: GET http://{host}:{port}/api/financial/securities/search", file=sys.stderr)
    print(f"preset news: GET http://{host}:{port}/api/local-tools/preset-news", file=sys.stderr)
    print(f"wechat account: GET http://{host}:{port}/api/local-tools/wechat-account", file=sys.stderr)
    print(f"weread shelf: GET http://{host}:{port}/api/local-tools/weread-shelf", file=sys.stderr)
    print(f"feed: POST http://{host}:{port}/api/local-tools/feed", file=sys.stderr)
    weread_session_service.ensure_login()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    transport = os.environ.get("FINANCE_MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        _run_http()
    else:
        # stdio 传输：DSH 的 dsh-mcp-client 以子进程方式拉起
        print("finresearch MCP server: stdio (waiting for DSH client)", file=sys.stderr)
        weread_session_service.ensure_login()
        mcp.run(transport="stdio")
