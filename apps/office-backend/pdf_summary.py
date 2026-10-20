"""PDF 选区层级摘要：仅处理用户划选的原文，不检索全文。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from assistant import iter_chat_events
from model_runtime import model_configuration_error, model_configured, model_name

MAX_SELECTION = 16_000


async def iter_pdf_selection_summary_events(selection: str) -> AsyncIterator[dict]:
    text = (selection or "").strip()
    if not model_configured():
        raise ValueError(model_configuration_error())
    if not text:
        raise ValueError("请先在 PDF 中选择需要整理的文字。")
    if len(text) > MAX_SELECTION:
        raise ValueError(f"选中文字过长（最多 {MAX_SELECTION} 字）。")
    messages = [
        {"role": "system", "content": """你是投研材料整理助手。只依据用户提供的选区，不补充外部事实。
请识别标题、条目、产品/服务、金额、数量、增长额、增长率、期间等关键信息，并严格输出 JSON：
{"items":[{"text":"层级标题或要点，保留关键数字与关键词","children":[...] }]}。
每条只保留关键词和数字，避免完整句：优先用“产品：收入 12亿元，同比 +18%”这类短语，单条不超过 36 个中文字符；不要复述背景、原因、结论或选区原文。
不限制每层要点数，应完整覆盖选区中有价值的独立要点；最多 4 层。没有可靠层级时使用单层要点。不得输出 Markdown、解释或其他字段。"""},
        {"role": "user", "content": f"选区原文：\n{text}"},
    ]
    raw = ""
    thinking = ""
    async for event in iter_chat_events(messages, json_object=True, stream=True, thinking=True):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            thinking += str(event["text"])
            yield {"type": "thinking", "text": str(event["text"])}
        else:
            raw += str(event["text"])
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("模型未返回可用的结构化摘要。") from exc
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise ValueError("模型未返回可用的层级摘要。")
    yield {"type": "done", "items": items, "thinking": thinking.strip(), "model_name": model_name() or "模型"}
