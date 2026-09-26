"""办公 PDF 语义检索 MCP 工具（与 office-api 共用 office_pdf_vector）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICE_API = ROOT / "apps" / "office-api"
if str(OFFICE_API) not in sys.path:
    sys.path.append(str(OFFICE_API))

from access_token import bearer_token  # noqa: E402
from office_pdf_vector import ensure_index, get_index_status, search_chunks  # noqa: E402
from store import create_store  # noqa: E402


def _user_and_pdf(document_id: str):
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    token = bearer_token()
    if not token:
        raise ValueError("缺少访问令牌：请用办公账号的 32 位 access token。")
    store = create_store()
    user = store.user_by_access_token(token)
    if not user:
        raise ValueError("访问令牌无效。")
    asset = store.asset(user["id"], doc_id)
    if not asset or asset.get("kind") != "pdf" or not asset.get("file_path"):
        raise ValueError("PDF 不存在或尚未上传。")
    path = Path(asset["file_path"])
    if not path.is_file():
        raise ValueError("PDF 文件缺失。")
    return user, asset, path


def ensure_office_pdf_index(document_id: str) -> dict:
    """确保办公 PDF 已解析入库（未就绪或文件变更时才 pymupdf + embedding）。"""
    user, asset, path = _user_and_pdf(document_id)
    status = ensure_index(document_id=asset["id"], user_id=user["id"], path=path)
    try:
        store = create_store()
        store.update_asset(user["id"], asset["id"], index_status=status.get("status"))
    except Exception:
        pass
    return status


def get_office_pdf_index_status(document_id: str) -> dict:
    """读取办公 PDF 向量索引状态。"""
    _user_and_pdf(document_id)
    return get_index_status(document_id.strip())


def search_office_pdf(document_id: str, query: str, top_k: int = 8) -> dict:
    """对已索引的办公 PDF 做语义检索，返回相关段落与页码。"""
    user, asset, path = _user_and_pdf(document_id)
    status = get_index_status(asset["id"])
    if status.get("status") != "ready":
        status = ensure_index(document_id=asset["id"], user_id=user["id"], path=path)
    hits = search_chunks(document_id=asset["id"], query=query, top_k=top_k)
    return {"document_id": asset["id"], "status": status.get("status"), "hits": hits, "count": len(hits)}


def register_tools(mcp) -> None:
    mcp.tool()(ensure_office_pdf_index)
    mcp.tool()(get_office_pdf_index_status)
    mcp.tool()(search_office_pdf)
