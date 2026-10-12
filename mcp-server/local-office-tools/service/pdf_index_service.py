"""办公 PDF 索引编排。文件与向量库在 office-backend，这里只决定何时建索引、何时检索。"""
from __future__ import annotations

from office_repository import client_for


def ensure_office_pdf_index(document_id: str) -> dict:
    """确保办公 PDF 已解析入库。"""
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    return client_for().ensure_pdf_index(doc_id)


def get_office_pdf_index_status(document_id: str) -> dict:
    """读取办公 PDF 向量索引状态。"""
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    return client_for().pdf_index_status(doc_id)


def search_office_pdf(document_id: str, query: str, top_k: int = 8) -> dict:
    """对已索引的办公 PDF 做语义检索，返回相关段落与页码。"""
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 不能为空。")
    return client_for().search_pdf(doc_id, query, top_k)
