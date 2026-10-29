"""资讯 Agent 技能：读 skills/information-skills 提示词，调用模型，结果写回 mcp-server。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from assistant import iter_chat_events, strip_thinking
from config import ROOT, mcp_remote_base
from model_runtime import model_configuration_error, model_configured, model_name
from remote_auth import remote_token

SKILL_DIR = ROOT / "skills" / "information-skills"
KEYWORD_SKILLS = {
    "finance_api": "finance-api-keywords",
    "finance_rss": "finance-rss-keywords",
}


def load_skill(name: str) -> str:
    path = SKILL_DIR / f"{name}.md"
    if not path.is_file():
        raise ValueError(f"未找到资讯技能：{name}")
    return path.read_text(encoding="utf-8")


def _remote(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    token = remote_token()
    if not token:
        raise ValueError("请先登录远程服务。")
    try:
        res = httpx.request(
            method,
            f"{mcp_remote_base().rstrip('/')}{path}",
            json=payload,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"远程服务不可用：{exc}") from exc
    if res.status_code == 401:
        raise ValueError("请先登录远程服务。")
    data = res.json() if res.content else {}
    if res.is_error:
        raise ValueError(str((data or {}).get("error") or res.text[:300] or f"HTTP {res.status_code}"))
    return data if isinstance(data, dict) else {}


def _parse_json(raw: str) -> dict:
    text = strip_thinking(raw).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("模型未返回可用的 JSON。") from exc
    if not isinstance(data, dict):
        raise ValueError("模型未返回对象。")
    return data


async def _complete(system: str, user: str) -> AsyncIterator[dict]:
    if not model_configured():
        raise ValueError(model_configuration_error())
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
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
    yield {"type": "result", "raw": raw, "thinking": thinking.strip()}


async def iter_keyword_events(kind: str, description: str, extra: dict | None = None) -> AsyncIterator[dict]:
    skill = KEYWORD_SKILLS.get(kind)
    if not skill:
        raise ValueError("该类型不支持 AI 生成关键字。")
    text = (description or "").strip()
    if not text:
        raise ValueError("请先描述期望的关键字。")
    extra = extra or {}
    payload: dict[str, Any] = {"description": text}
    current = str(extra.get("keywords") or extra.get("current_keywords") or "").strip()
    revision = str(extra.get("revision") or "").strip()
    if current:
        payload["current_keywords"] = current
    if revision:
        payload["revision"] = revision
    for key, value in extra.items():
        if key not in {"keywords", "current_keywords", "revision"}:
            payload[key] = value
    async for event in _complete(load_skill(skill), json.dumps(payload, ensure_ascii=False)):
        if event["type"] != "result":
            yield event
            continue
        data = _parse_json(event["raw"])
        keywords = str(data.get("keywords") or "").strip()
        if not keywords:
            raise ValueError("模型没有给出关键字。")
        yield {"type": "done", "keywords": keywords, "thinking": event["thinking"], "model_name": model_name() or "模型"}


async def iter_impact_events(group_id: int, item_ids: list[int] | None = None) -> AsyncIterator[dict]:
    listed = _remote("GET", f"/api/information/groups/{int(group_id)}/items", params={"today": "1"})
    items = listed.get("items") or []
    if item_ids:
        wanted = {int(item_id) for item_id in item_ids}
        items = [item for item in items if int(item.get("id") or 0) in wanted]
    if not items:
        raise ValueError("没有可判断的资讯。")
    compact = [
        {
            "id": item["id"],
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "source_name": item.get("source_name") or "",
            "source_kind": item.get("source_kind") or "",
        }
        for item in items
    ]
    async for event in _complete(load_skill("item-impact"), json.dumps({"items": compact}, ensure_ascii=False)):
        if event["type"] != "result":
            yield event
            continue
        data = _parse_json(event["raw"])
        rows = data.get("items") if isinstance(data.get("items"), list) else []
        saved = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            impact = str(row.get("impact") or "").strip()
            if not impact:
                continue
            saved.append(_remote("PUT", f"/api/information/items/{int(row['id'])}/impact", {"impact": impact}))
        yield {"type": "done", "items": saved, "thinking": event["thinking"], "model_name": model_name() or "模型"}


async def iter_analyze_events(item_id: int) -> AsyncIterator[dict]:
    item = _find_item(int(item_id))
    url = str(item.get("url") or "").strip()
    page: dict[str, Any] = {"error": "没有原文链接。", "title": "", "sections": []}
    if url:
        try:
            page = _remote("POST", "/api/local-tools/read-page", {"url": url})
        except ValueError as exc:
            page = {"error": str(exc), "title": "", "sections": []}
    payload = {
        "title": item.get("title") or "",
        "url": url,
        "source_name": item.get("source_name") or "",
        "published_at": item.get("published_at") or "",
        "page": page,
    }
    async for event in _complete(load_skill("item-analysis"), json.dumps(payload, ensure_ascii=False)):
        if event["type"] != "result":
            yield event
            continue
        data = _parse_json(event["raw"])
        analysis = {
            "summary": str(data.get("summary") or ""),
            "performance": str(data.get("performance") or ""),
            "financial": str(data.get("financial") or ""),
        }
        saved = _remote("PUT", f"/api/information/items/{int(item_id)}/analysis", analysis)
        yield {"type": "done", "item": saved, "analysis": analysis, "thinking": event["thinking"], "model_name": model_name() or "模型"}


def _review_item_refs(rows: list, items: list[dict]) -> list[dict]:
    by_id = {int(item["id"]): item for item in items if item.get("id")}
    used: set[int] = set()
    refs: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = by_id.get(int(row.get("id") or 0))
        if not item:
            continue
        sentiment = str(row.get("sentiment") or "中性").strip()
        if sentiment not in {"利好", "利空", "中性"}:
            sentiment = "中性"
        used.add(int(item["id"]))
        refs.append({
            "id": int(item["id"]),
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "sentiment": sentiment,
        })
    for item in items:
        item_id = int(item.get("id") or 0)
        if item_id and item_id not in used:
            refs.append({
                "id": item_id,
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "sentiment": "中性",
            })
    return refs


async def iter_review_events(group_id: int) -> AsyncIterator[dict]:
    gid = int(group_id)
    listed = _remote("GET", f"/api/information/groups/{gid}/items", params={"today": "1", "review": "1"})
    summary = _remote("GET", f"/api/information/groups/{gid}/summary")
    items = listed.get("items") or []
    payload = {
        "date": summary.get("date"),
        "group": listed.get("group") or summary.get("group"),
        "items": [
            {
                "id": item.get("id"),
                "title": item.get("title") or "",
                "source_name": item.get("source_name") or "",
                "published_at": item.get("published_at") or "",
                "url": item.get("url") or "",
                "impact": item.get("impact") or "",
                "analysis": item.get("analysis"),
            }
            for item in items
        ],
    }
    async for event in _complete(load_skill("daily-review"), json.dumps(payload, ensure_ascii=False)):
        if event["type"] != "result":
            yield event
            continue
        data = _parse_json(event["raw"])
        sentiment = str(data.get("sentiment") or "中性").strip()
        if sentiment not in {"利好", "利空", "中性"}:
            sentiment = "中性"
        refs = _review_item_refs(data.get("items") if isinstance(data.get("items"), list) else [], items)
        saved = _remote("POST", f"/api/information/groups/{gid}/reviews", {
            "review_date": summary.get("date"),
            "success_count": len(items),
            "fail_count": 0,
            "sentiment": sentiment,
            "content": str(data.get("content") or "").strip(),
            "item_refs": refs,
        })
        yield {"type": "done", "review": saved, "thinking": event["thinking"], "model_name": model_name() or "模型"}


def _find_item(item_id: int) -> dict:
    return _remote("GET", f"/api/information/items/{int(item_id)}")
