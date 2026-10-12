"""选区 Agent 的模型 HTTP。只负责请求聊天接口并取回 JSON。"""
from __future__ import annotations

import json
import os

import httpx


async def complete_json(system: str, user_content: str) -> dict:
    base = os.environ.get("OFFICE_MODEL_BASE_URL", "").rstrip("/")
    model = os.environ.get("OFFICE_MODEL_NAME", "")
    if not base or not model:
        raise ValueError("Set OFFICE_MODEL_BASE_URL and OFFICE_MODEL_NAME in .env")
    headers = {}
    if os.environ.get("OFFICE_MODEL_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['OFFICE_MODEL_API_KEY']}"
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            base + "/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
    if response.is_error:
        raise ValueError(f"Model endpoint returned HTTP {response.status_code}")
    result = json.loads(response.json()["choices"][0]["message"]["content"])
    if not isinstance(result, dict):
        raise ValueError("Model must return a JSON object containing answer")
    return result
