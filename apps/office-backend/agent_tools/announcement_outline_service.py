"""公告 PDF 大纲：复用本进程 announcement_intelligence 流水线。"""
from __future__ import annotations

from pathlib import Path

from agent_tools.errors import OfficeError
from agent_tools.store_client import client_for
from announcement_intelligence import iter_outline_events
from config import resolve_asset_file_path
from model_runtime import model_configuration_error, model_configured
from office_pdf_vector import ensure_index, get_index_status


async def generate_pdf_outline(document_id: str) -> dict:
    """为原生文本 PDF 生成公告标题层级；复杂版式会返回 unsupported。"""
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    if not model_configured():
        raise OfficeError(503, model_configuration_error())
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

    status = get_index_status(raw["id"])
    if status.get("status") != "ready":
        status = ensure_index(document_id=raw["id"], user_id=raw["owner_id"], path=path)
        try:
            api.store.update_asset(api.owner_id, raw["id"], index_status=status.get("status"))
        except Exception:
            pass
    if status.get("empty"):
        raise OfficeError(400, "未提取到可用文本层，扫描件暂不支持生成大纲。")

    final = None
    async for event in iter_outline_events(raw["id"], str(path)):
        if event.get("type") == "error":
            raise OfficeError(400, str(event.get("error") or "生成 PDF 大纲失败。"))
        if event.get("type") == "done":
            final = event
    if not final:
        raise OfficeError(502, "PDF 大纲服务未返回结果。")
    return final
