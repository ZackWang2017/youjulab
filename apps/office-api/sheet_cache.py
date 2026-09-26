"""每张工作表一份共享缓存：结构矩阵给程序，表含义 profile 给模型。"""
from __future__ import annotations

import json
import re

MAX_PROFILE = 4000
MAX_OPENER = 100_000
SELECTION_INTENT = re.compile(r"所选|选区|这些单元格|当前选中|高亮单元格|这块区域")
WRITE_INTENT = re.compile(r"写入|填写|填入|写到|写进|覆盖|修改单元格|替换单元格")

PROFILE_SYSTEM = """你在为表格协作助手编写「表含义」，供后续提问直接使用，避免每次重新猜表结构。
只输出简体中文说明，不要 JSON，不要思考过程。
必须写清：
- 数据从哪一行开始，哪些行是标题、单位、表头
- 表头有几层，每一列的业务含义（含合并表头展开后的列名）
- 单位，以及不能相加的区块（金额 vs 比率）
- 若表中有多块区域，按 A1 范围分成区块
- 如何用 A1 或「分类+指标」定位
不要罗列全部数据，不要计算合计。
"""


def wants_selection(intent: str) -> bool:
    text = intent or ""
    return bool(SELECTION_INTENT.search(text) or WRITE_INTENT.search(text))


def headers_of(values: list) -> list:
    if not values or not isinstance(values[0], list):
        return []
    return ["" if v is None else str(v) for v in values[0]]


def structure_fingerprint(payload: dict) -> str:
    return json.dumps({
        "cols": payload.get("cols"),
        "origin": payload.get("origin"),
        "headers": payload.get("headers") or headers_of(payload.get("values") or []),
    }, ensure_ascii=False, sort_keys=True)


def public_sheet_cache(cache: dict | None) -> dict | None:
    if not cache:
        return None
    fingerprint = structure_fingerprint(cache)
    profile = (cache.get("profile") or "").strip()
    return {
        "ok": True,
        "sheet_id": cache.get("sheet_id"),
        "sheet_name": cache.get("sheet_name") or "",
        "revision": cache.get("revision", 0),
        "rows": cache.get("rows", 0),
        "cols": cache.get("cols", 0),
        "range": cache.get("range") or "",
        "has_profile": bool(profile),
        "profile_stale": bool(profile) and cache.get("profile_fingerprint") != fingerprint,
        "updated_at": cache.get("updated_at"),
    }


def normalize_cache_body(body: dict, *, revision: int) -> dict:
    values = body.get("values")
    if not isinstance(values, list):
        raise ValueError("表数据格式无效。")
    rows = [row if isinstance(row, list) else [] for row in values]
    width = max((len(row) for row in rows), default=0)
    origin = body.get("origin") if isinstance(body.get("origin"), dict) else {"row": 0, "col": 0}
    headers = body.get("headers") if isinstance(body.get("headers"), list) else headers_of(rows)
    sheet_id = str(body.get("sheet_id") or "").strip()
    if not sheet_id:
        raise ValueError("缺少 sheet_id。")
    return {
        "sheet_id": sheet_id,
        "sheet_name": str(body.get("sheet_name") or "").strip(),
        "revision": revision,
        "origin": {"row": int(origin.get("row") or 0), "col": int(origin.get("col") or 0)},
        "rows": int(body.get("rows") or len(rows)),
        "cols": int(body.get("cols") or width),
        "range": str(body.get("range") or ""),
        "merges": body.get("merges") if isinstance(body.get("merges"), list) else [],
        "note": str(body.get("note") or ""),
        "truncated": bool(body.get("truncated")),
        "values": rows,
        "headers": headers,
    }


def apply_cache_patch(cache: dict, body: dict) -> dict:
    next_cache = {**cache, "values": [list(row) if isinstance(row, list) else [] for row in (cache.get("values") or [])]}
    if body.get("revision") is not None:
        next_cache["revision"] = int(body["revision"])
    if isinstance(body.get("merges"), list):
        next_cache["merges"] = body["merges"]
    if isinstance(body.get("range"), str):
        next_cache["range"] = body["range"]
    values: list = next_cache["values"]
    for cell in body.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        row, col = cell.get("row"), cell.get("column", cell.get("col"))
        if type(row) is not int or type(col) is not int or row < 0 or col < 0:
            continue
        while len(values) <= row:
            values.append([])
        while len(values[row]) <= col:
            values[row].append(None)
        values[row][col] = cell.get("value")
    next_cache["rows"] = int(body["rows"]) if body.get("rows") is not None else len(values)
    next_cache["cols"] = int(body["cols"]) if body.get("cols") is not None else max((len(row) for row in values), default=0)
    next_cache["headers"] = headers_of(values)
    if cache.get("profile_fingerprint") != structure_fingerprint(next_cache):
        next_cache["profile"] = ""
        next_cache["profile_fingerprint"] = ""
    return next_cache


def compact_slice(cache: dict) -> dict:
    values = cache.get("values") or []
    return {
        "scope": "sheet",
        "sheet_id": cache.get("sheet_id"),
        "sheet_name": cache.get("sheet_name"),
        "range": cache.get("range"),
        "origin": cache.get("origin"),
        "rows": cache.get("rows"),
        "cols": cache.get("cols"),
        "merges": cache.get("merges") or [],
        "headers": cache.get("headers") or headers_of(values),
        "header_rows": values[:4],
        "sample_rows": values[1:6] if len(values) > 1 else [],
        "note": "默认只有表头和少量样例行。完整格子仅在用户要明细时提供。",
    }


def detail_surrounding(cache: dict, max_len: int) -> str:
    payload = {
        "scope": "sheet",
        "sheet_id": cache.get("sheet_id"),
        "range": cache.get("range"),
        "origin": cache.get("origin"),
        "rows": cache.get("rows"),
        "cols": cache.get("cols"),
        "merges": cache.get("merges") or [],
        "values": [],
        "truncated": True,
    }
    for row in cache.get("values") or []:
        payload["values"].append(row)
        if len(json.dumps(payload, ensure_ascii=False)) > max_len:
            payload["values"].pop()
            break
    else:
        payload["truncated"] = bool(cache.get("truncated"))
    return json.dumps(payload, ensure_ascii=False)


def opener_from_cache(cache: dict, intent: str, selection: dict | None = None) -> str:
    profile = (cache.get("profile") or "").strip()
    parts = [
        f"sheet_id：{cache.get('sheet_id') or ''}",
        f"sheet_name：{cache.get('sheet_name') or ''}",
        f"revision：{cache.get('revision', 0)}",
        f"range：{cache.get('range') or ''}",
        f"表含义：\n{profile}",
    ]
    selection_parts: list[str] = []
    if wants_selection(intent) and isinstance(selection, dict):
        address = selection.get("address") or ""
        if address:
            selection_parts.append(f"选区：{address}")
        selection_parts.append("选区数据：\n" + json.dumps({
            "address": address,
            "row": selection.get("row"),
            "column": selection.get("column"),
            "rows": selection.get("rows"),
            "cols": selection.get("cols"),
            "values": selection.get("values"),
        }, ensure_ascii=False))
    reserved = len("\n".join(selection_parts)) + len("表数据：\n") + 1
    used = len("\n".join(parts)) + reserved
    parts.append("表数据：\n" + detail_surrounding(cache, max(1000, MAX_OPENER - used)))
    parts.extend(selection_parts)
    text = "\n".join(parts)
    return text[:MAX_OPENER]


def bind_sheet_context(context: dict, cache: dict | None, intent: str) -> dict:
    profile = (cache or {}).get("profile") or ""
    if not cache or not str(profile).strip():
        raise ValueError("请先刷新表结构。")
    if cache.get("profile_fingerprint") != structure_fingerprint(cache):
        raise ValueError("表结构已变化，请刷新表结构。")
    selection = dict(context.get("selection") or {})
    source = selection if wants_selection(intent) else None
    if source is None:
        selection["address"] = ""
    return {
        **context,
        "selection": selection,
        "surrounding": opener_from_cache(cache, intent, source),
    }
