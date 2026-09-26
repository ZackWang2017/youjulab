import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_annot import add_pdf_annotation, delete_pdf_annotation, update_pdf_annotation


class PdfAnnotTests(unittest.TestCase):
    def _write(self, text="Hello annotation sample"):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), text)
        tmp = Path(tempfile.mkdtemp()) / "sample.pdf"
        doc.save(tmp)
        doc.close()
        return tmp

    def test_highlight_covers_selected_text(self):
        path = self._write()
        doc = pymupdf.open(path)
        page = doc[0]
        rect = page.search_for("Hello")[0]
        height = page.rect.height
        box = [rect.x0, height - rect.y1, rect.x1, height - rect.y0]
        doc.close()
        xref = add_pdf_annotation(path, "highlight", 1, [box])["xref"]
        self.assertGreater(xref, 0)
        saved = pymupdf.open(path)
        try:
            found = None
            overlap = False
            for annot in saved[0].annots():
                found = annot.type[0]
                overlap = annot.rect.intersects(rect)
                break
            self.assertEqual(found, pymupdf.PDF_ANNOT_HIGHLIGHT)
            self.assertTrue(overlap)
        finally:
            saved.close()

    def test_text_note_sits_in_margin(self):
        path = self._write()
        doc = pymupdf.open(path)
        page = doc[0]
        rect = page.search_for("Hello")[0]
        height = page.rect.height
        box = [rect.x0, height - rect.y1, rect.x1, height - rect.y0]
        doc.close()
        add_pdf_annotation(path, "text", 1, [box], "核对这段")
        saved = pymupdf.open(path)
        try:
            kinds = []
            note = ""
            text_overlap = True
            highlight_overlap = False
            for annot in saved[0].annots():
                kinds.append(annot.type[0])
                if annot.type[0] == pymupdf.PDF_ANNOT_TEXT:
                    note = annot.info.get("content") or ""
                    text_overlap = annot.rect.intersects(rect)
                if annot.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT:
                    highlight_overlap = annot.rect.intersects(rect)
            self.assertIn(pymupdf.PDF_ANNOT_TEXT, kinds)
            self.assertIn(pymupdf.PDF_ANNOT_HIGHLIGHT, kinds)
            self.assertIn("核对这段", note)
            self.assertFalse(text_overlap)
            self.assertTrue(highlight_overlap)
        finally:
            saved.close()

    def test_text_note_can_be_updated_and_deleted(self):
        path = self._write()
        created = add_pdf_annotation(path, "text", 1, [[72, 750, 180, 770]], "初稿")
        xref = created["xref"]
        self.assertGreater(xref, 0)
        self.assertGreater(created["mark_xref"], 0)
        update_pdf_annotation(path, xref, "改定")
        saved = pymupdf.open(path)
        try:
            note = ""
            for annot in saved[0].annots():
                if annot.type[0] == pymupdf.PDF_ANNOT_TEXT:
                    note = annot.info.get("content") or ""
                    self.assertEqual(annot.info.get("subject"), f"hl:{created['mark_xref']}")
                    break
            self.assertIn("改定", note)
        finally:
            saved.close()
        deleted = delete_pdf_annotation(path, xref)
        self.assertEqual(sorted(deleted), sorted([created["mark_xref"], xref]))
        gone = pymupdf.open(path)
        try:
            count = 0
            annots = gone[0].annots()
            if annots:
                for _ in annots:
                    count += 1
            self.assertEqual(count, 0)
        finally:
            gone.close()

    def test_many_boxes_are_coalesced(self):
        path = self._write()
        boxes = [
            [72 + (i % 8) * 12, 750 - (i // 8) * 10, 82 + (i % 8) * 12, 760 - (i // 8) * 10]
            for i in range(90)
        ]
        created = add_pdf_annotation(path, "text", 1, boxes, "大选区")
        self.assertGreater(created["xref"], 0)
        self.assertGreater(created["mark_xref"], 0)


if __name__ == "__main__":
    unittest.main()
