"""把高亮和文字批注写入 PDF 文件。"""
from __future__ import annotations

from pathlib import Path

import pymupdf

MAX_NOTE = 200_000  # 问AI 多轮 Markdown 较长；普通批注一般远小于此
MAX_BOXES = 200  # 大选区 getClientRects 很多；前端会先按行合并
ICON = 16
EDITABLE = (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_TEXT)


def _rect(page: pymupdf.Page, box: list[float]) -> pymupdf.Rect:
    x0, y0, x1, y1 = (float(v) for v in box)
    height = page.rect.height
    return pymupdf.Rect(min(x0, x1), height - max(y0, y1), max(x0, x1), height - min(y0, y1))


def _union(rects: list[pymupdf.Rect]) -> pymupdf.Rect:
    area = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        area |= rect
    return area


def _margin_point(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> pymupdf.Point:
    union = _union(rects)
    y = union.y0
    left = pymupdf.Rect(page.rect.x0 + 4, y, page.rect.x0 + 4 + ICON, y + ICON)
    if not left.intersects(union):
        return pymupdf.Point(left.x0, left.y0)
    right_x = min(page.rect.x1 - ICON - 4, union.x1 + 6)
    right = pymupdf.Rect(right_x, y, right_x + ICON, y + ICON)
    if not right.intersects(union):
        return pymupdf.Point(right.x0, right.y0)
    above = max(page.rect.y0 + 2, union.y0 - ICON - 4)
    return pymupdf.Point(union.x0, above)


def _rewrite(path: Path, mutate) -> None:
    tmp = path.with_suffix(".annot.pdf")
    doc = pymupdf.open(path)
    try:
        mutate(doc)
        doc.save(tmp, incremental=False, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    finally:
        doc.close()
    tmp.replace(path)


def _annots(doc: pymupdf.Document):
    for page in doc:
        annots = page.annots()
        if not annots:
            continue
        for annot in annots:
            yield page, annot


def _coalesce_rects(rects: list[pymupdf.Rect], max_n: int) -> list[pymupdf.Rect]:
    """把过多高亮矩形合并到 max_n 段，避免大选区被拒。"""
    if len(rects) <= max_n:
        return rects
    if max_n <= 1:
        return [_union(rects)]
    group = max(1, (len(rects) + max_n - 1) // max_n)
    out: list[pymupdf.Rect] = []
    for i in range(0, len(rects), group):
        out.append(_union(rects[i : i + group]))
    return out[:max_n]


def _boxes(page: pymupdf.Page, boxes: list[list[float]]) -> list[pymupdf.Rect]:
    if not boxes:
        raise ValueError("请先选择要批注的文字。")
    if len(boxes) > MAX_BOXES * 4:
        raise ValueError(f"选区过高亮块过多（最多约 {MAX_BOXES} 段），请缩小选区。")
    rects = []
    for box in boxes:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError("批注坐标无效。")
        rects.append(_rect(page, box))
    return _coalesce_rects(rects, MAX_BOXES)


def _note(content: str, required: bool) -> str:
    note = (content or "").strip()
    if required and not note:
        raise ValueError("批注内容不能为空。")
    if len(note) > MAX_NOTE:
        raise ValueError(f"批注内容过长（最多 {MAX_NOTE} 字）。")
    return note


def add_pdf_annotation(path: Path, kind: str, page_number: int, boxes: list[list[float]], content: str = "") -> dict:
    if kind not in ("highlight", "text"):
        raise ValueError("只支持高亮或文字批注。")
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("页码无效。")
    note = _note(content, required=(kind == "text"))
    created = {"xref": 0, "mark_xref": 0}

    def mutate(doc: pymupdf.Document) -> None:
        if page_number > doc.page_count:
            raise ValueError("页码超出 PDF 页数。")
        page = doc[page_number - 1]
        rects = _boxes(page, boxes)
        if kind == "highlight":
            annot = page.add_highlight_annot(rects)
            annot.set_colors(stroke=(1, 0.85, 0.2))
            annot.set_opacity(0.35)
            annot.update()
            created["xref"] = annot.xref
            return
        mark = page.add_highlight_annot(rects)
        mark.set_colors(stroke=(1, 0.85, 0.2))
        mark.set_opacity(0.35)
        # 高亮只存短标记，完整正文写在文字批注上，避免 Contents 双份截断
        mark.set_info(title="office-text-mark", content="office-text-mark")
        mark.update()
        point = _margin_point(page, rects)
        annot = page.add_text_annot(point, note)
        annot.set_rect(pymupdf.Rect(point.x, point.y, point.x + ICON, point.y + ICON))
        info = {"content": note, "subject": f"hl:{mark.xref}"}
        if note.startswith("【问AI】"):
            info["title"] = "office-ai-ask"
        annot.set_info(**info)
        # 部分阅读器对 set_info 的超长 Contents 不稳定，再显式写一次
        try:
            annot.set_info(content=note)
        except Exception:
            pass
        annot.update()
        created["xref"] = annot.xref
        created["mark_xref"] = mark.xref

    _rewrite(path, mutate)
    if created["xref"] < 1:
        raise ValueError("无法写入批注。")
    return created


def update_pdf_annotation(path: Path, xref: int, content: str) -> None:
    if not isinstance(xref, int) or xref < 1:
        raise ValueError("批注无效。")
    note = _note(content, required=True)

    def mutate(doc: pymupdf.Document) -> None:
        for _page, annot in _annots(doc):
            if annot.xref != xref:
                continue
            if annot.type[0] != pymupdf.PDF_ANNOT_TEXT:
                raise ValueError("只有文字批注可以编辑。")
            info = dict(annot.info or {})
            info["content"] = note
            if note.startswith("【问AI】"):
                info["title"] = "office-ai-ask"
            annot.set_info(info)
            try:
                annot.set_info(content=note)
            except Exception:
                pass
            annot.update()
            # 关联高亮只保留短标记，完整内容在文字批注
            subject = info.get("subject") or ""
            if subject.startswith("hl:"):
                try:
                    linked = int(subject[3:])
                except ValueError:
                    linked = None
                if linked:
                    for _p, mark in _annots(doc):
                        if mark.xref == linked:
                            mark.set_info(title="office-text-mark", content="office-text-mark")
                            mark.update()
                            break
            return
        raise ValueError("批注不存在。")

    _rewrite(path, mutate)


def delete_pdf_annotation(path: Path, xref: int) -> list[int]:
    if not isinstance(xref, int) or xref < 1:
        raise ValueError("批注无效。")
    deleted: list[int] = []

    def mutate(doc: pymupdf.Document) -> None:
        target = None
        linked = None
        for page, annot in _annots(doc):
            if annot.xref != xref:
                continue
            if annot.type[0] not in EDITABLE:
                raise ValueError("不能删除这种批注。")
            target = (page, annot)
            subject = (annot.info or {}).get("subject") or ""
            if annot.type[0] == pymupdf.PDF_ANNOT_TEXT and subject.startswith("hl:"):
                try:
                    linked = int(subject[3:])
                except ValueError:
                    linked = None
            break
        if not target:
            raise ValueError("批注不存在。")
        if linked:
            for page, annot in list(_annots(doc)):
                if annot.xref == linked:
                    deleted.append(annot.xref)
                    page.delete_annot(annot)
                    break
        deleted.append(target[1].xref)
        target[0].delete_annot(target[1])

    _rewrite(path, mutate)
    return deleted
