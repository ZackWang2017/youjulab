"""执行文档技能：本地工具收集材料，再按对应技能交给模型整理并写入文档。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

from assistant import iter_chat_events
from config import ROOT, digest_tz, mcp_access_token, mcp_http_base
from document_skills import SKILL_TYPES

_MAX_SEARCH = 5
_MAX_TEXT = 4000
_MAX_SEEN = 1000


def run_document_skill(skill: dict, *, project_name: str, document_name: str, user_token: str) -> dict[str, Any]:
    if skill.get("type") != "news_brief":
        raise ValueError("该技能类型还不能执行。")
    params = skill.get("params") or {}
    seen = _stored_fingerprints(skill)
    fresh, added = _select_new_items(_collect_items(params), seen)
    if not fresh:
        raise ValueError("本次没有新资讯。")
    markdown = asyncio.run(_compose_news_brief(_payload(params, fresh)))
    _append_document(project_name, document_name, markdown, user_token)
    return {
        "ok": True,
        "title": markdown.split("\n", 1)[0].strip(),
        "seen_fingerprints": _remember(seen, added),
    }


async def _write_news_brief(params: dict) -> str:
    return await _compose_news_brief(_payload(params, _collect_items(params)))


def _payload(params: dict, items: list[dict]) -> dict:
    stamp = datetime.now(ZoneInfo(digest_tz())).strftime("%Y年%m月%d日 %H:%M:%S")
    return {
        "generated_at": stamp,
        "stock_names": _stock_names(params.get("stocks")),
        "topic": (params.get("topic") or "").strip(),
        "items": items,
    }


def _select_new_items(items: list[dict], seen: list[str]) -> tuple[list[dict], list[str]]:
    known = set(seen)
    current: set[str] = set()
    fresh: list[dict] = []
    added: list[str] = []
    for item in items:
        fingerprint = _fingerprint(item)
        legacy = _legacy_fingerprint(item)
        if not fingerprint:
            continue
        # legacy 只与历史记录比较，避免两篇不同 URL 但摘要相同的文章在同一轮互相误伤。
        if fingerprint in known or fingerprint in current or (legacy and legacy in known):
            continue
        current.add(fingerprint)
        added.append(fingerprint)
        fresh.append(item)
    return fresh, added


def _fingerprint(item: dict) -> str:
    url = _canonical_url(str(item.get("url") or ""))
    if url:
        return _hash_key("url", url)
    source = " ".join(str(item.get("source") or "").lower().split())
    title = " ".join(str(item.get("title") or "").lower().split())
    published = _published_day(str(item.get("published_at") or ""))
    if title:
        return _hash_key("article", "|".join((source, title, published)))
    return _legacy_fingerprint(item)


def _legacy_fingerprint(item: dict) -> str:
    text = " ".join((item.get("text") or "").split())
    if not text:
        text = " ".join((item.get("error") or "").split())
    if not text:
        return ""
    return hashlib.sha256(text.encode()).hexdigest()


def _hash_key(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


def _canonical_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    # 常见跟踪参数不参与文章身份；微信文章 token 和必要参数仍保留。
    ignored = {"from", "scene", "subscene", "clicktime", "enterid", "ascene", "devicetype", "version", "lang", "nettype", "exportkey", "pass_ticket", "wx_header", "utm_source", "utm_medium", "utm_campaign"}
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in ignored])
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _stored_fingerprints(skill: dict) -> list[str]:
    raw = skill.get("seen_fingerprints") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def _remember(previous: list[str], added: list[str]) -> list[str]:
    merged = list(previous)
    for digest in added:
        if digest not in merged:
            merged.append(digest)
    return merged[-_MAX_SEEN:]


_PRESET_LABELS = {
    "hkex": "港交所",
    "sina": "新浪财经",
    "eastmoney": "东方财富",
    "xueqiu": "雪球",
    "jisilu": "集思录",
    "tencent": "腾讯自选股",
}


def _collect_items(params: dict) -> list[dict]:
    items: list[dict] = []
    items.extend(_preset_items(params))
    if "weread" in _source_ids(params):
        items.extend(_weread_items())
    items = _deduplicate_items(items)
    if "today" in _date_filters(params):
        items = [item for item in items if _is_today(item.get("published_at"))]
    return items


def _deduplicate_items(items: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = _fingerprint(item)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _date_filters(params: dict) -> set[str]:
    return {part.strip() for part in str(params.get("date_filter") or "").split(",") if part.strip()}


def _published_day(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # 当前各资讯源统一返回 YYYY-MM-DD 或 YYYY-MM-DD HH:MM；保留宽松解析以兼容 ISO 时间。
    candidate = text[:10]
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _is_today(raw: Any) -> bool:
    today = datetime.now(ZoneInfo(digest_tz())).strftime("%Y-%m-%d")
    return _published_day(str(raw or "")) == today


def _preset_items(params: dict) -> list[dict]:
    sources = [source for source in _source_ids(params) if source in _PRESET_LABELS]
    stocks = _stock_pairs(params.get("stocks"))
    items: list[dict] = []
    for source in sources:
        if source not in _PRESET_LABELS:
            continue
        label = _PRESET_LABELS[source]
        if not stocks:
            items.append(_item(label, label, "", "", "", "请先选择股票。"))
            continue
        for code, name in stocks:
            fetched = _fetch_preset(source, code, name)
            if fetched.get("error"):
                items.append(_item(label, name or code or label, "", "", "", fetched["error"]))
                continue
            found = fetched.get("results") or []
            if not found:
                items.append(_item(label, name or code or label, "", "", "", "没有查询结果。"))
                continue
            for row in found:
                items.append(_item(
                    label,
                    row.get("title") or "未命名",
                    row.get("url") or "",
                    row.get("published_at") or "",
                    row.get("summary") or row.get("title") or "",
                    "",
                ))
    return items


def _fetch_preset(source: str, ticker_code: str, name: str) -> dict:
    try:
        response = httpx.get(
            f"{mcp_http_base()}/api/local-tools/preset-news",
            params={"source": source, "ticker_code": ticker_code, "name": name},
            headers={"Authorization": f"Bearer {mcp_access_token()}"},
            timeout=25.0,
        )
    except httpx.HTTPError as exc:
        return {"error": f"查询失败：{exc}", "results": []}
    if response.status_code >= 400:
        return {"error": f"查询失败 HTTP {response.status_code}", "results": []}
    try:
        payload = response.json()
    except Exception:
        return {"error": "查询结果无法解析。", "results": []}
    if not isinstance(payload, dict):
        return {"error": "查询结果无法解析。", "results": []}
    return payload


def _stock_pairs(raw: str | None) -> list[tuple[str, str]]:
    text = (raw or "").strip()
    if not text:
        return []
    if "|" in text or "\n" in text:
        pairs = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            code, _, name = line.partition("|")
            pairs.append((code.strip(), (name or code).strip()))
        return pairs
    return [("", part) for part in text.split() if part]


def _stock_names(raw: str | None) -> list[str]:
    return [name for _, name in _stock_pairs(raw) if name]


def _brief_messages(payload: dict) -> list[dict]:
    skill_name = SKILL_TYPES["news_brief"]["skill"]
    path = ROOT / "skills" / skill_name / "SKILL.md"
    if not path.is_file():
        raise ValueError("资讯简报技能文件缺失。")
    return [
        {"role": "system", "content": path.read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def _iter_brief_events(payload: dict):
    answer = ""
    async for event in iter_chat_events(_brief_messages(payload), stream=True, json_object=False, thinking=True):
        if event.get("type") != "delta" or not event.get("text"):
            continue
        if event.get("part") == "reasoning":
            yield {"type": "delta", "text": event["text"], "part": "reasoning"}
            continue
        answer += event["text"]
    text = _strip_fence(answer)
    if not text.strip():
        raise ValueError("模型未返回简报。")
    yield {"type": "brief", "markdown": text if text.endswith("\n") else text + "\n"}


async def _compose_news_brief(payload: dict) -> str:
    raw = ""
    async for event in _iter_brief_events(payload):
        if event.get("type") == "brief":
            raw = event["markdown"]
    return _render_brief(payload, raw)


_CHAIN = (
    ("业务板块", "business"),
    ("财务指标", "financial"),
    ("影响周期", "period"),
    ("验证方法", "verification"),
)


def _render_brief(payload: dict, raw: str) -> str:
    """把模型返回的条目排成置顶简报：分割线、二级时间标题、三级来源、连续序号和空心圆。"""
    groups = _brief_groups(_load_brief_json(raw))
    lines = ["---", f"## {payload.get('generated_at') or ''}"]
    number = 1
    wrote = False
    for group in groups:
        source = str(group.get("source") or "未注明来源").strip() or "未注明来源"
        rows = [item for item in group.get("items") or [] if isinstance(item, dict)]
        if not rows:
            continue
        wrote = True
        lines.append(f"### {source}")
        for item in rows:
            lines.extend(_render_item(number, item))
            number += 1
    if not wrote:
        lines.append("本次没有可用资讯。")
    return "\n".join(lines).strip() + "\n"


def _load_brief_json(raw: str) -> dict:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回简报。")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("模型未返回简报。") from exc
    if not isinstance(data, dict):
        raise ValueError("模型未返回简报。")
    return data


def _brief_groups(data: dict) -> list[dict]:
    groups = data.get("groups")
    if isinstance(groups, list):
        return [group for group in groups if isinstance(group, dict)]
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("模型未返回简报。")
    grouped: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "未注明来源").strip() or "未注明来源"
        if not grouped or grouped[-1]["source"] != source:
            grouped.append({"source": source, "items": []})
        grouped[-1]["items"].append(item)
    return grouped


def _render_item(number: int, item: dict) -> list[str]:
    stocks = str(item.get("stocks") or "").strip()
    title = str(item.get("title") or "未命名").strip() or "未命名"
    published = str(item.get("published_at") or "").strip() or "发布时间未知"
    body = f"【{stocks}】{title}" if stocks else title
    head = f"{number}. **{body}**（{published}）"
    lines = [head]
    error = str(item.get("error") or "").strip()
    if error:
        lines.append(error)
        return lines
    summary = str(item.get("summary") or "").strip() or "材料不足"
    url = str(item.get("url") or "").strip()
    if url:
        summary = f"{summary} 🔗 [原文]({url})"
    lines.append(summary)
    for label, key in _CHAIN:
        value = str(item.get(key) or "").strip() or "材料不足"
        lines.append(f"○ **{label}**：{value}")
    return lines


def _fresh_items(skill: dict) -> tuple[list[dict], list[str], list[str]]:
    params = skill.get("params") or {}
    seen = _stored_fingerprints(skill)
    fresh, added = _select_new_items(_collect_items(params), seen)
    return fresh, added, seen


async def iter_skill_run_events(
    skill: dict,
    *,
    project_name: str,
    document_name: str,
    user_token: str,
    prepare_document=None,
):
    """执行技能并向调用方送出状态、思考过程和完成事件。"""
    if skill.get("type") != "news_brief":
        yield {"type": "error", "error": "该技能类型还不能执行。"}
        return
    yield {"type": "status", "text": "正在获取资讯…"}
    try:
        fresh, added, seen = await asyncio.to_thread(_fresh_items, skill)
    except ValueError as exc:
        yield {"type": "error", "error": str(exc)}
        return
    if not fresh:
        yield {"type": "error", "error": "本次没有新资讯。"}
        return
    yield {"type": "status", "text": "正在整理简报…"}
    payload = _payload(skill.get("params") or {}, fresh)
    markdown = ""
    try:
        async for event in _iter_brief_events(payload):
            if event.get("type") == "brief":
                markdown = _render_brief(payload, event["markdown"])
            else:
                yield event
        document_id = None
        if prepare_document:
            document_name, document_id, notice = await asyncio.to_thread(prepare_document, markdown)
            if notice:
                yield {"type": "status", "text": notice}
        yield {"type": "status", "text": "正在写入文档…"}
        await asyncio.to_thread(_append_document, project_name, document_name, markdown, user_token)
    except ValueError as exc:
        yield {"type": "error", "error": str(exc)}
        return
    title = payload["generated_at"]
    done = {
        "type": "done",
        "ok": True,
        "title": title,
        "seen_fingerprints": _remember(seen, added),
    }
    if document_id:
        done["document_id"] = document_id
    yield done


def _source_ids(params: dict) -> list[str]:
    return [part.strip() for part in (params.get("preset_sources") or "").split(",") if part.strip()]


def _weread_items() -> list[dict]:
    fetched = _fetch_weread_shelf()
    accounts = fetched.get("accounts") or []
    article_count = sum(len(account.get("results") or []) for account in accounts if isinstance(account, dict))
    if fetched.get("error") and not accounts:
        print(f"微信读书：{fetched['error']}", file=sys.stderr)
        return [_item("微信读书", "微信读书", "", "", "", fetched["error"])]
    print(f"微信读书：收到 {len(accounts)} 个公众号、{article_count} 篇文章", file=sys.stderr)
    items = []
    for account in accounts:
        name = account.get("title") or account.get("id") or "微信读书"
        if account.get("error"):
            items.append(_item(name, name, "", "", "", account["error"]))
            continue
        found = account.get("results") or []
        if not found:
            items.append(_item(name, name, "", "", "", "没有文章。"))
            continue
        for row in found:
            items.append(_item(
                name,
                row.get("title") or "未命名",
                row.get("url") or "",
                row.get("published_at") or "",
                row.get("summary") or row.get("title") or "",
                "",
            ))
    return items


def _fetch_weread_shelf() -> dict:
    try:
        response = httpx.get(
            f"{mcp_http_base()}/api/local-tools/weread-shelf",
            params={"num": _MAX_SEARCH},
            headers={"Authorization": f"Bearer {mcp_access_token()}"},
            timeout=180.0,
        )
    except httpx.HTTPError as exc:
        return {"error": f"查询失败：{exc}", "accounts": []}
    if response.status_code >= 400:
        return {"error": f"查询失败 HTTP {response.status_code}", "accounts": []}
    try:
        payload = response.json()
    except Exception:
        return {"error": "查询结果无法解析。", "accounts": []}
    if not isinstance(payload, dict):
        return {"error": "查询结果无法解析。", "accounts": []}
    return payload


def _item(source: str, title: str, url: str, published_at: str, text: str, error: str) -> dict:
    body = (text or "").strip()
    if len(body) > _MAX_TEXT:
        body = body[:_MAX_TEXT] + "…"
    return {
        "source": source,
        "title": title,
        "url": url,
        "published_at": published_at,
        "text": body,
        "error": error,
    }


def _append_document(project_name: str, document_name: str, markdown: str, user_token: str) -> None:
    response = httpx.post(
        f"{mcp_http_base()}/api/documents/write",
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
        json={
            "project_name": project_name,
            "document_name": document_name,
            "markdown": markdown,
            "mode": "prepend",
        },
        timeout=40.0,
    )
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = body.get("error") or ""
        except Exception:
            detail = response.text[:200]
        raise ValueError(detail or f"写入文档失败 HTTP {response.status_code}")


def _strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
    
