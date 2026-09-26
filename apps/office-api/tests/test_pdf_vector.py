"""PDF 向量索引与切块测试（可用假 embedding，依赖本机 OFFICE_PG_URL）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

HERE = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(HERE))

from config import embedding_dim, pg_url  # noqa: E402
from office_pdf_vector import (  # noqa: E402
    delete_document_index,
    ensure_index,
    ensure_schema,
    extract_chunks,
    get_index_status,
    search_chunks,
)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    dim = embedding_dim()
    out = []
    for text in texts:
        vec = [0.0] * dim
        # 稳定伪向量：字符码分布
        for i, ch in enumerate(text[:dim]):
            vec[i % dim] += (ord(ch) % 31) / 31.0
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        out.append([x / norm for x in vec])
    return out


@unittest.skipUnless(pg_url(), "OFFICE_PG_URL not configured")
class PdfVectorTests(unittest.TestCase):
    def setUp(self):
        ensure_schema()
        self.doc_id = "test-pdf-vector-doc"
        self.user_id = "test-user"
        delete_document_index(self.doc_id)
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Alpha revenue grew in Europe markets.")
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Beta cost structure and supply chain risk.")
        self.tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        doc.save(self.path)
        doc.close()

    def tearDown(self):
        delete_document_index(self.doc_id)
        Path(self.path).unlink(missing_ok=True)

    def test_extract_chunks(self):
        pages, chunks = extract_chunks(Path(self.path))
        self.assertEqual(pages, 2)
        self.assertGreaterEqual(len(chunks), 2)

    def test_ensure_idempotent_and_search(self):
        with patch("office_pdf_vector.embed_texts", _fake_embed):
            first = ensure_index(document_id=self.doc_id, user_id=self.user_id, path=self.path, embed=_fake_embed)
            self.assertEqual(first["status"], "ready")
            self.assertGreater(first["chunk_count"], 0)
            second = ensure_index(document_id=self.doc_id, user_id=self.user_id, path=self.path, embed=_fake_embed)
            self.assertEqual(second["status"], "ready")
            hits = search_chunks(document_id=self.doc_id, query="supply chain risk", top_k=3, embed=_fake_embed)
            self.assertTrue(hits)
            self.assertTrue(any(h["page"] == 2 for h in hits))

    def test_status_missing(self):
        status = get_index_status("no-such-doc-xyz")
        self.assertEqual(status["status"], "missing")


if __name__ == "__main__":
    unittest.main()
