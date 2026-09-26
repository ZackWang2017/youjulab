"""Small selection-first agent runtime shared by HTTP and MCP."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import yaml

from office_context import OfficeContext
from office_patch import create_patch

ROOT = Path(__file__).resolve().parents[2]


def registry(surface: str, task: str = "explain"):
    router = yaml.safe_load((ROOT / "agents/office-copilot.agent.yaml").read_text())
    routes = router["routes"][surface]
    if isinstance(routes, dict):
        if task not in routes:
            raise ValueError("PDF is read-only in this MVP" if surface == "pdf" else "Unknown office task")
        filename = routes[task]
    else:
        filename = routes
    agent = yaml.safe_load((ROOT / "agents" / filename).read_text())
    skill = (ROOT / "skills" / agent["skill"] / "SKILL.md").read_text()
    contract = (ROOT / "skills/common/contracts.md").read_text()
    return agent["name"], router["instructions"] + "\n" + skill + "\n" + contract


async def run_office_agent(context: dict, intent: str, mode: str = "demo") -> dict:
    """Run the selected skill and return an answer plus an optional reviewable patch.

    demo only trims whitespace; model calls a configured chat-completions endpoint.
    This bounded MVP uses the supplied context, with no autonomous tool-call loop.
    """
    ctx = OfficeContext.model_validate(context)
    if not intent.strip() or len(intent) > 4000:
        raise ValueError("Intent must contain 1 to 4000 characters")
    agent, system = registry(ctx.surface)
    if mode == "demo":
        s = ctx.selection
        replacement = None
        if ctx.surface == "sheet":
            replacement = [[v.strip() if isinstance(v, str) and not v.startswith("=") else v
                            for v in row] for row in s["values"]]
        elif ctx.surface == "document":
            replacement = s["text"].strip()
        answer = "本地演示：仅清理选区首尾空白，不调用模型，也不执行自由指令。"
        if ctx.surface == "pdf":
            answer = f"本地摘录 · {s.get('filename', 'PDF')}，第 {s['page']} 页\n{s['text']}"
        if replacement == s.get("values", s.get("text")):
            replacement = None
            answer += "\n选区无需清理。"
        result = {"answer": answer, "replacement": replacement}
    elif mode == "model":
        base = os.environ.get("OFFICE_MODEL_BASE_URL", "").rstrip("/")
        model = os.environ.get("OFFICE_MODEL_NAME", "")
        if not base or not model:
            raise ValueError("Set OFFICE_MODEL_BASE_URL and OFFICE_MODEL_NAME in .env")
        headers = {}
        if os.environ.get("OFFICE_MODEL_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['OFFICE_MODEL_API_KEY']}"
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(base + "/chat/completions", headers=headers, json={
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps({"intent": intent, "context": ctx.model_dump()}, ensure_ascii=False)}],
                "response_format": {"type": "json_object"},
            })
            if response.is_error:
                raise ValueError(f"Model endpoint returned HTTP {response.status_code}")
            result = json.loads(response.json()["choices"][0]["message"]["content"])
    else:
        raise ValueError("Unknown agent mode")
    if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
        raise ValueError("Model must return a JSON object containing answer")
    patch = None
    if result.get("replacement") is not None:
        patch = create_patch(ctx, result["replacement"])
    return {"agent": agent, "mode": mode, "answer": result["answer"], "patch": patch}


def register_tools(mcp):
    mcp.tool()(run_office_agent)
