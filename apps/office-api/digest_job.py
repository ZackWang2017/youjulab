"""每日资讯定时任务：校验、执行、写回文档。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import ROOT, digest_tz, model_configured
from digest_fetch import collect_topic_items
from digest_render import markdown_to_tiptap_nodes, merge_into_document
from assistant import iter_chat_events
from store import Store, now

TITLE_PREFIX = "每日资讯"
MAX_TOPICS = 12
MAX_SOURCES = 20


def default_job(document_id: str, user_id: str) -> dict:
    return {
        "document_id": document_id,
        "user_id": user_id,
        "enabled": False,
        "hour": 8,
        "minute": 0,
        "title_prefix": TITLE_PREFIX,
        "output_order": "newest_first",
        "topics": [],
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "updated_at": now(),
    }


def public_job(job: dict) -> dict:
    return {
        "document_id": job["document_id"],
        "enabled": bool(job.get("enabled")),
        "hour": int(job.get("hour", 8)),
        "minute": int(job.get("minute", 0)),
        "title_prefix": job.get("title_prefix") or TITLE_PREFIX,
        "output_order": job.get("output_order") or "newest_first",
        "topics": job.get("topics") or [],
        "last_run_at": job.get("last_run_at"),
        "last_status": job.get("last_status"),
        "last_error": job.get("last_error") or "",
        "updated_at": job.get("updated_at"),
    }


def validate_job_body(body: dict) -> dict:
    hour = int(body.get("hour", 8))
    minute = int(body.get("minute", 0))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("执行时间无效。")
    order = body.get("output_order") or "newest_first"
    if order not in ("newest_first", "newest_last"):
        raise ValueError("输出位置无效。")
    topics_in = body.get("topics") or []
    if not isinstance(topics_in, list) or len(topics_in) > MAX_TOPICS:
        raise ValueError(f"子主题最多 {MAX_TOPICS} 个。")
    topics: list[dict] = []
    for topic in topics_in:
        if not isinstance(topic, dict):
            raise ValueError("子主题格式无效。")
        name = (topic.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValueError("子主题名称需为 1–80 字。")
        sources_in = topic.get("sources") or []
        if not isinstance(sources_in, list) or len(sources_in) > MAX_SOURCES:
            raise ValueError(f"每个子主题来源最多 {MAX_SOURCES} 个。")
        sources: list[dict] = []
        for src in sources_in:
            if not isinstance(src, dict):
                raise ValueError("资讯来源格式无效。")
            kind = (src.get("kind") or "rss").strip().lower()
            if kind not in ("rss", "url", "rsshub"):
                raise ValueError("来源类型仅支持 rss / url / rsshub。")
            label = (src.get("label") or "").strip()[:120]
            item: dict[str, Any] = {"kind": kind, "label": label}
            if kind == "rsshub":
                route = (src.get("route") or src.get("url") or "").strip()
                if not route or len(route) > 500:
                    raise ValueError("RSSHub 路由无效。")
                item["route"] = route
            else:
                url = (src.get("url") or "").strip()
                if not url.startswith("http://") and not url.startswith("https://"):
                    raise ValueError("请填写以 http(s) 开头的网址或 RSS。")
                if len(url) > 2000:
                    raise ValueError("来源地址过长。")
                item["url"] = url
            sources.append(item)
        topics.append({"name": name, "sources": sources})
    return {
        "enabled": bool(body.get("enabled")),
        "hour": hour,
        "minute": minute,
        "title_prefix": TITLE_PREFIX,
        "output_order": order,
        "topics": topics,
    }


def _skill_system() -> str:
    path = ROOT / "skills" / "document-digest" / "SKILL.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "整理每日资讯。只使用提供的条目，用 Markdown 输出，注明来源链接，不要编造。"
    )


def _stamp(tz_name: str) -> tuple[str, datetime]:
    tz = ZoneInfo(tz_name)
    local = datetime.now(tz)
    return local.strftime("%Y-%m-%d %H:%M"), local


async def _llm_digest(title: str, topics_payload: list[dict]) -> str:
    if not model_configured():
        raise ValueError("请先配置模型后再执行资讯任务。")
    user_payload = {
        "title": title,
        "topics": topics_payload,
        "instruction": "按子主题整理以上资讯，输出 Markdown。",
    }
    messages = [
        {"role": "system", "content": _skill_system()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    answer = ""
    async for event in iter_chat_events(messages, stream=True, json_object=False, thinking=False):
        if event.get("type") == "delta" and event.get("text"):
            # 忽略 reasoning，只要终答
            if event.get("part") == "reasoning":
                continue
            answer += event["text"]
    text = answer.strip()
    if not text:
        raise ValueError("模型未返回整理结果。")
    return text


async def run_digest_job(store: Store, job: dict) -> dict:
    """执行一次任务并写回文档。"""
    user_id = job["user_id"]
    document_id = job["document_id"]
    asset = store.asset(user_id, document_id)
    if not asset or asset.get("kind") != "document":
        raise ValueError("文档不存在。")
    topics = job.get("topics") or []
    if not topics:
        raise ValueError("请至少配置一个子主题与资讯来源。")

    stamp, _ = _stamp(digest_tz())
    title = f"{job.get('title_prefix') or TITLE_PREFIX}（{stamp}）"
    topics_payload: list[dict] = []
    fetch_notes: list[str] = []
    for topic in topics:
        name = topic.get("name") or "未命名"
        try:
            items = collect_topic_items(topic)
        except Exception as exc:
            fetch_notes.append(f"{name}: {exc}")
            items = []
        topics_payload.append({"name": name, "items": items})

    if not any(t["items"] for t in topics_payload):
        detail = "；".join(fetch_notes) if fetch_notes else "各来源均无新内容。"
        raise ValueError(f"未能拉取到资讯。{detail}")

    markdown = await _llm_digest(title, topics_payload)
    if not markdown.lstrip().startswith("#"):
        markdown = f"# {title}\n\n{markdown}"
    nodes = markdown_to_tiptap_nodes(markdown)
    merged = merge_into_document(asset.get("content"), nodes, job.get("output_order") or "newest_first")
    updated = store.update_asset(
        user_id,
        document_id,
        content=merged,
        revision=int(asset.get("revision") or 0) + 1,
    )
    status = {
        "last_run_at": now(),
        "last_status": "ok",
        "last_error": "；".join(fetch_notes) if fetch_notes else "",
        "updated_at": now(),
    }
    store.upsert_digest_job(user_id, document_id, {**job, **status})
    return {"ok": True, "title": title, "revision": updated.get("revision") if updated else None, "warnings": fetch_notes}


def due_jobs(store: Store, local_now: datetime | None = None) -> list[dict]:
    tz = ZoneInfo(digest_tz())
    current = local_now or datetime.now(tz)
    hour, minute = current.hour, current.minute
    day_key = current.strftime("%Y-%m-%d")
    due: list[dict] = []
    for job in store.list_enabled_digest_jobs():
        if int(job.get("hour", -1)) != hour or int(job.get("minute", -1)) != minute:
            continue
        last = job.get("last_run_at") or ""
        # 同一天同一分钟不重复（用 last_run_at 日期+时分粗判）
        if last:
            try:
                # ISO UTC → 本地日
                from datetime import datetime as dt
                ran = dt.fromisoformat(last.replace("Z", "+00:00")).astimezone(tz)
                if ran.strftime("%Y-%m-%d") == day_key and ran.hour == hour and ran.minute == minute:
                    continue
            except Exception:
                pass
        due.append(job)
    return due
