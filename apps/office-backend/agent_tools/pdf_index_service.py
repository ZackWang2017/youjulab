"""办公 PDF 索引：本进程直连向量库。"""
from __future__ import annotations

from pathlib import Path

from agent_tools.errors import OfficeError
from agent_tools.store_client import client_for
from office_pdf_vector import ensure_index, get_index_status, search_chunks
from config import resolve_asset_file_path


def _pdf_asset(document_id: str):
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    api = client_for()
    asset = api.get_asset(doc_id)
    if asset.get("kind") != "pdf":
        raise OfficeError(404, "PDF 不存在或尚未上传。")
    raw = api.store.asset(api.owner_id, doc_id)
    if not raw or not raw.get("file_path"):
        raise OfficeError(404, "PDF 文件缺失。")
    path = resolve_asset_file_path(raw.get("file_path"), raw.get("id"))
    if not path:
        raise OfficeError(404, "PDF 文件缺失。")
    return raw, Path(path)


def ensure_office_pdf_index(document_id: str) -> dict:
    """确保办公 PDF 已解析入库。"""
    asset, path = _pdf_asset(document_id)
    status = ensure_index(document_id=asset["id"], user_id=asset["owner_id"], path=path)
    try:
        api = client_for()
        api.store.update_asset(api.owner_id, asset["id"], index_status=status.get("status"))
    except Exception:
        pass
    return status


def get_office_pdf_index_status(document_id: str) -> dict:
    """读取办公 PDF 向量索引状态。"""
    asset, _path = _pdf_asset(document_id)
    return get_index_status(asset["id"])


def search_office_pdf(document_id: str, query: str, top_k: int = 8) -> dict:
    """对已索引的办公 PDF 做语义检索，返回相关段落与页码。"""
    asset, path = _pdf_asset(document_id)
    status = get_index_status(asset["id"])
    if status.get("status") != "ready":
        status = ensure_index(document_id=asset["id"], user_id=asset["owner_id"], path=path)
        try:
            api = client_for()
            api.store.update_asset(api.owner_id, asset["id"], index_status=status.get("status"))
        except Exception:
            pass
    hits = search_chunks(document_id=asset["id"], query=query, top_k=top_k)
    return {
        "document_id": asset["id"],
        "status": status.get("status"),
        "hits": hits,
        "count": len(hits),
    }
