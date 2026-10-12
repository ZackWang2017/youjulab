"""刷新表结构时，用当前切片让模型写一次表含义。"""
from __future__ import annotations

import json

from assistant import iter_chat_events, strip_thinking
from sheet_cache import MAX_PROFILE, PROFILE_SYSTEM, compact_slice, structure_fingerprint


async def generate_sheet_profile(cache: dict) -> tuple[str, str]:
    messages = [
        {"role": "system", "content": PROFILE_SYSTEM},
        {"role": "user", "content": json.dumps(compact_slice(cache), ensure_ascii=False)},
    ]
    text = ""
    async for event in iter_chat_events(messages, stream=True, json_object=False):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            continue
        text += event["text"]
    profile = strip_thinking(text).strip()
    if not profile:
        raise ValueError("模型没有返回表含义。")
    return profile[:MAX_PROFILE], structure_fingerprint(cache)
