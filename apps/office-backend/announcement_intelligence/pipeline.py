"""Bounded-token announcement outline pipeline."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from assistant import iter_chat_events
from model_runtime import model_configuration_error, model_configured
from office_pdf_vector import ensure_layout_blocks

from .layout import compact_sample, document_profile, remove_running_headers
from .prompts import SELECTOR
from .rule_config import available_rule_packs
from .rules import apply_rules
from .schema import LayoutBlock
from .selector import selection_from_model

def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = text.split("\n", 1)[-1].removesuffix(fence).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("模型没有返回 JSON 对象。")
    return value


async def _model_json(system: str, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    raw: list[str] = []
    async for event in iter_chat_events(
        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
        json_object=True, stream=True, thinking=True,
    ):
        if event.get("type") != "delta":
            continue
        text = str(event.get("text") or "")
        if event.get("part") == "reasoning":
            yield {"type": "thinking", "text": text}
        else:
            raw.append(text)
    yield {"type": "model_result", "value": _parse_json("".join(raw))}


async def iter_outline_events(document_id: str, path: str) -> AsyncIterator[dict[str, Any]]:
    """Return progress/thinking/done events; callers turn them into SSE."""
    if not model_configured():
        yield {"type": "error", "error": model_configuration_error()}
        return
    yield {"type": "progress", "stage": "layout", "message": "正在读取原生 PDF 版式…"}
    rows = ensure_layout_blocks(document_id, path)
    blocks = [LayoutBlock.from_row(row) for row in rows]
    usable = remove_running_headers(blocks)
    if len(usable) < 3:
        yield {"type": "error", "error": "PDF 原生文本不足，暂不支持生成大纲。"}
        return
    sample = compact_sample(usable)
    profile = document_profile(usable)

    yield {"type": "progress", "stage": "select", "message": "正在选择适用的公告结构规则…"}
    selector_result: dict[str, Any] | None = None
    async for event in _model_json(SELECTOR, {
        "profile": profile,
        "sample_blocks": sample,
        "allowed_rule_packs": sorted(available_rule_packs()),
    }):
        if event["type"] == "thinking":
            yield event
        else:
            selector_result = event["value"]
    document_type, rule_pack, rules, reason = selection_from_model(selector_result)
    yield {
        "type": "progress", "stage": "select",
        "message": f"已识别为 {document_type}，使用 {rule_pack} 规则包执行全文本地解析。",
        "document_type": document_type, "rule_pack": rule_pack, "rule_source": "configured", "reason": reason,
    }
    yield {"type": "progress", "stage": "apply", "message": "正在使用本地规则解析全文结构…"}
    outline = apply_rules(usable, rules)
    if not outline:
        yield {"type": "done", "status": "unsupported", "message": "未识别到可用标题，文档结构复杂，暂时无法生成。", "document_type": document_type}
        return
    yield {"type": "done", "status": "success", "outline": outline, "document_type": document_type}
