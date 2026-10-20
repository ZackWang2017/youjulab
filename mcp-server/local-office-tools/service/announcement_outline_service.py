"""MCP-facing PDF outline service; work and access control stay in office-backend."""
from __future__ import annotations

from office_repository import client_for


def generate_pdf_outline(document_id: str) -> dict:
    """为原生文本 PDF 生成公告标题层级；复杂版式会返回 unsupported。"""
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    return client_for().generate_pdf_outline(doc_id)
