"""PDF 财报解析模块 —— 确定性提取 + 分类初判 + 章节定位（与 LLM 无关）。

设计要点（与「读财报」伴读场景匹配）：
- 一次性提取全文并按页缓存（同路径同 mtime 不重复解析，翻页零成本）。
- 分类初判：关键词规则（年报/半年报/一季报/三季报；A股/港股/美股）——初判，最终结论由模型复核。
- 章节定位：三表、审计报告、经营情况讨论等关键字锚点 → 页码。
- 扫描件检测：文本量过低时标记 likely_scanned（OCR 属后续阶段）。

经 server.py 暴露为 mcp__finreport__* 工具：
  parse_pdf(path)      → 元信息 + 分类初判 + 章节索引 + 封面预览
  pdf_page(path, page) → 单页文本（1-based，逐页伴读用）
  pdf_text(path, a, b) → 页区间文本（深度分析分块用）
"""

from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

import pymupdf  # PyMuPDF（用 pymupdf 而非 fitz，避免弃用警告污染 MCP stdio 流）

# ---------------------------------------------------------------------------
# 缓存：同一路径同一 mtime 只解析一次
# ---------------------------------------------------------------------------

_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()

_MAX_SECTION_SCAN_PAGES = 60   # 章节定位扫描前 60 页
_HEAD_PAGES_FOR_CLASSIFY = 4   # 分类只看前 4 页（封面/目录/重要提示）


def _resolve(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        root = os.environ.get("FINANCE_PDF_ROOT", "")
        if root:
            p = Path(root) / p
    return str(p.resolve())


def _is_b64_artifact(path: str) -> bool:
    return path.endswith(".b64") or path.endswith(".pdf.b64")


def _open_pdf(key: str):
    """打开 PDF：原生 .pdf 走文件；DSH 上传产物 .pdf.b64 先 base64 解码再内存打开。"""
    if _is_b64_artifact(key):
        raw = Path(key).read_text(encoding="utf-8")
        pdf_bytes = base64.b64decode(raw, validate=True)
        return pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return pymupdf.open(key)


def _load(path: str) -> dict:
    """提取并缓存：返回 {name, size, mtime, page_count, pages, total_chars, scan_quality}。"""
    key = _resolve(path)
    try:
        st = os.stat(key)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"PDF 文件不存在: {path}（服务端解析为: {key}；FINANCE_PDF_ROOT={os.environ.get('FINANCE_PDF_ROOT', '(未设置，按服务启动目录解析)')}）。"
            f"请改用绝对路径；DSH「上传 PDF」产物为 `.pdf.b64`，也需传其绝对路径。"
        )

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached["mtime"] == st.st_mtime_ns and cached["size"] == st.st_size:
            return cached

    doc = _open_pdf(key)
    try:
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()

    total_chars = sum(len(t) for t in pages)
    entry = {
        "name": Path(key).name,
        "size": st.st_size,
        "mtime": st.st_mtime_ns,
        "page_count": len(pages),
        "pages": pages,
        "total_chars": total_chars,
        # 文字型 PDF 文本量应随页数增长；远低于阈值判定为扫描件（需 OCR）
        "scan_quality": "text" if total_chars >= max(200, 20 * len(pages)) else "likely_scanned",
    }
    with _CACHE_LOCK:
        _CACHE[key] = entry
    return entry


# ---------------------------------------------------------------------------
# 分类初判（关键词规则）
# ---------------------------------------------------------------------------

_REPORT_TYPE_RULES = [
    ("annual", "年报", ["年度报告"]),
    ("semi", "半年报", ["半年度报告", "半年度", "中期报告"]),
    ("q1", "一季报", ["第一季度报告", "一季度报告", "一季报"]),
    ("q3", "三季报", ["第三季度报告", "三季度报告", "三季报"]),
]

_MARKET_RULES = [
    ("hk", "港股", ["香港联合交易所有限公司", "联交所", "香港交易所", "港元", "港币", "上市规则"]),
    ("a", "A股", ["深圳证券交易所", "上海证券交易所", "北京证券交易所", "人民币普通股", "中国证监会"]),
    ("us", "美股", ["sec", "form 10-k", "form 10-q", "nasdaq", "nyse", "united states securities"]),
]


def _score_rules(head: str, rules) -> tuple:
    best = None
    matched = {}
    for key, label, kws in rules:
        cnt = sum(head.count(k.lower()) for k in kws)
        if cnt > 0:
            matched[key] = {"label": label, "hits": cnt, "keywords": [k for k in kws if k.lower() in head]}
            if best is None or cnt > best[1]:
                best = (key, label, cnt)
    return best, matched


def classify(pages: list[str]) -> dict:
    head = "\n".join(pages[:_HEAD_PAGES_FOR_CLASSIFY]).lower()
    rt_best, rt_matched = _score_rules(head, _REPORT_TYPE_RULES)
    mk_best, mk_matched = _score_rules(head, _MARKET_RULES)
    return {
        "report_type": rt_best[0] if rt_best else "unknown",
        "report_type_label": rt_best[1] if rt_best else "未识别",
        "market": mk_best[0] if mk_best else "unknown",
        "market_label": mk_best[1] if mk_best else "未识别",
        "matched_keywords": {"report_type": rt_matched, "market": mk_matched},
    }


# ---------------------------------------------------------------------------
# 章节定位
# ---------------------------------------------------------------------------

_SECTION_ANCHORS = [
    ("重要提示", ["重要提示"]),
    ("公司简介", ["公司简介", "公司信息"]),
    ("会计数据摘要", ["会计数据和财务指标摘要", "主要会计数据"]),
    ("经营情况讨论", ["经营情况讨论与分析", "管理层讨论与分析", "业务回顾"]),
    ("董事会报告", ["董事会报告"]),
    ("重要事项", ["重要事项"]),
    ("股东情况", ["股份变动及股东情况", "股东情况"]),
    ("审计报告", ["审计报告"]),
    ("合并资产负债表", ["合并资产负债表"]),
    ("合并利润表", ["合并利润表", "合并损益表"]),
    ("合并现金流量表", ["合并现金流量表"]),
    ("母公司资产负债表", ["母公司资产负债表"]),
]


def _find_first(pages: list[str], keywords: list[str]) -> int | None:
    for i, text in enumerate(pages[:_MAX_SECTION_SCAN_PAGES]):
        low = text.lower()
        if any(k.lower() in low for k in keywords):
            return i + 1
    return None


def sections(pages: list[str]) -> list[dict]:
    return [
        {"name": name, "page": page}
        for name, kws in _SECTION_ANCHORS
        if (page := _find_first(pages, kws)) is not None
    ]


# ---------------------------------------------------------------------------
# 对外接口（供 server.py 的 MCP 工具调用）
# ---------------------------------------------------------------------------

def parse(path: str) -> dict:
    info = _load(path)
    pages = info["pages"]
    return {
        "path": _resolve(path),
        "name": info["name"],
        "size_bytes": info["size"],
        "page_count": info["page_count"],
        "total_chars": info["total_chars"],
        "scan_quality": info["scan_quality"],
        "classification": classify(pages),
        "sections": sections(pages),
        "cover_preview": pages[0][:600] if pages else "",
    }


def page_text(path: str, page: int) -> dict:
    info = _load(path)
    if page < 1 or page > info["page_count"]:
        raise ValueError(f"页码越界: {page}（共 {info['page_count']} 页，1-based）")
    text = info["pages"][page - 1]
    return {
        "path": _resolve(path),
        "name": info["name"],
        "page": page,
        "page_count": info["page_count"],
        "chars": len(text),
        "text": text,
    }


def range_text(path: str, start_page: int, end_page: int, max_pages: int = 100, max_chars: int = 300_000) -> dict:
    info = _load(path)
    n = info["page_count"]
    if start_page < 1 or end_page > n or start_page > end_page:
        raise ValueError(f"页区间越界: {start_page}~{end_page}（共 {n} 页）")
    if end_page - start_page + 1 > max_pages:
        raise ValueError(f"页区间过大（最多 {max_pages} 页/次），请分段取")
    chunks = info["pages"][start_page - 1:end_page]
    text = "\n".join(f"──── 第 {start_page + i} 页 ────\n{chunk}" for i, chunk in enumerate(chunks))
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n…（截断，请缩小页区间）"
    return {
        "path": _resolve(path),
        "name": info["name"],
        "start_page": start_page,
        "end_page": end_page,
        "page_count": n,
        "chars": len(text),
        "truncated": truncated,
        "text": text,
    }


# ---------------------------------------------------------------------------
# 页图渲染（左侧浏览页用）：PyMuPDF 出 PNG，LRU 缓存
# ---------------------------------------------------------------------------

_IMG_CACHE: dict[tuple[str, int, int], bytes] = {}
_IMG_CACHE_LOCK = threading.Lock()
_IMG_CACHE_MAX = 60


def page_image(path: str, page: int, dpi: int = 110) -> dict:
    """渲染第 `page` 页（1-based）为 PNG，返回 base64 data URL（浏览器直用）。"""
    info = _load(path)
    n = info["page_count"]
    if page < 1 or page > n:
        raise ValueError(f"页码越界: {page}（共 {n} 页，1-based）")
    key = (_resolve(path), page, dpi)
    with _IMG_CACHE_LOCK:
        data = _IMG_CACHE.get(key)
        if data is None and len(_IMG_CACHE) >= _IMG_CACHE_MAX:
            _IMG_CACHE.clear()
    if data is None:
        doc = _open_pdf(path)
        try:
            pix = doc[page - 1].get_pixmap(dpi=dpi, annots=True)
            data = pix.tobytes("png")
        finally:
            doc.close()
        with _IMG_CACHE_LOCK:
            _IMG_CACHE[key] = data
    return {
        "path": _resolve(path),
        "name": info["name"],
        "page": page,
        "page_count": n,
        "dpi": dpi,
        "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
    }


# ---------------------------------------------------------------------------
# 原生 PDF 字节（左侧「财报浏览」直接用 PDF 渲染，不再走位图）
# ---------------------------------------------------------------------------

_PDF_CACHE: dict[tuple[str, int | None, float], bytes] = {}
_PDF_CACHE_LOCK = threading.Lock()
_PDF_CACHE_MAX = 30


def pdf_bytes(path: str, page: int | None = None) -> dict:
    """返回 PDF 原始字节：`page` 为 None 时整份文档；否则仅第 `page` 页（1-based）的单页 PDF。

    浏览器端用 <iframe> 直接加载（矢量渲染、文字可选、可缩放），翻页即重新加载对应页。
    """
    info = _load(path)
    n = info["page_count"]
    if page is not None and (page < 1 or page > n):
        raise ValueError(f"页码越界: {page}（共 {n} 页，1-based）")
    key = (_resolve(path), page, info["mtime"])
    with _PDF_CACHE_LOCK:
        data = _PDF_CACHE.get(key)
        if data is None and len(_PDF_CACHE) >= _PDF_CACHE_MAX:
            _PDF_CACHE.clear()
    if data is None:
        doc = _open_pdf(path)
        try:
            if page is not None:
                doc.select([page - 1])
            data = doc.tobytes()
        finally:
            doc.close()
        with _PDF_CACHE_LOCK:
            _PDF_CACHE[key] = data
    return {
        "path": _resolve(path),
        "name": info["name"],
        "page": page,
        "page_count": n,
        "bytes": data,
    }
