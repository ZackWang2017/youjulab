"""搜索配额与参数整理。外部 HTTP 由 search_repository 完成。"""
from __future__ import annotations

import os
from typing import Any

import search_repository
import usage


def _quota_dict(st: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": st.get("enabled"),
        "switch_on": st.get("switch_on"),
        "auto_closed": st.get("auto_closed"),
        "used_today": st.get("used_today"),
        "daily_limit": st.get("daily_limit"),
        "day": st.get("day"),
        "closed_reason": st.get("closed_reason"),
    }


def _refuse_quota(engine: str, query: str, st: dict[str, Any] | None = None) -> dict[str, Any]:
    st = st or usage.status(engine)
    reason = st.get("closed_reason")
    if reason == "switch_off":
        err = f"{engine} 搜索已关闭（SEARCH_{engine.upper()}_ENABLED=0）"
    elif reason == "limit_zero":
        err = f"{engine} 搜索日限额为 0，拒绝调用"
    elif reason == "daily_limit_reached":
        err = (
            f"{engine} 今日已达上限 {st.get('daily_limit')} 次，已自动关闭；"
            f"已用 {st.get('used_today')}（{st.get('day')}）"
        )
    elif st.get("store_error"):
        err = f"搜索计数存储不可用: {st['store_error']}"
    else:
        err = f"{engine} 搜索不可用"
    return {
        "error": err,
        "engine": engine,
        "query": query,
        "results": [],
        "quota": _quota_dict(st),
    }


def _attach_quota(payload: dict[str, Any], engine: str, st: dict[str, Any] | None = None) -> dict[str, Any]:
    payload["quota"] = _quota_dict(st or usage.status(engine))
    return payload


def _begin_search(engine: str, query: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    st = usage.status(engine)
    if st.get("store_error") or not st.get("enabled"):
        return _refuse_quota(engine, query, st), None
    gate = usage.try_consume(engine)
    if not gate.get("ok"):
        return _refuse_quota(engine, query, gate), None
    return None, gate


def ensure_ready() -> dict[str, Any]:
    usage.ensure_schema()
    return quota_status()


def quota_status() -> dict[str, Any]:
    return {
        "baidu": usage.status(usage.ENGINE_BAIDU),
        "baike": usage.status(usage.ENGINE_BAIKE),
        "google": usage.status(usage.ENGINE_GOOGLE),
    }


def _guard(engine: str, query: str, configured: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not configured:
        return None, None
    return _begin_search(engine, query)


def baidu_web_search(query: str, num: int = 5, topic: str = "") -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "baidu", "query": q, "results": []}
    try:
        search_repository._norm_topic(topic)
    except ValueError as exc:
        return {"error": str(exc), "engine": "baidu", "query": q, "results": []}
    configured = bool((os.environ.get("BAIDU_QIANFAN_API_KEY") or "").strip())
    refused, gate = _guard(usage.ENGINE_BAIDU, q, configured)
    if refused:
        return refused
    payload = search_repository.baidu_web_search(query, num=num, topic=topic, apply_quota=False)
    return _attach_quota(payload, usage.ENGINE_BAIDU, gate)


def baidu_baike_search(query: str, num: int = 5) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "baike", "query": q, "results": []}
    configured = bool((os.environ.get("BAIDU_QIANFAN_API_KEY") or "").strip())
    refused, gate = _guard(usage.ENGINE_BAIKE, q, configured)
    if refused:
        return refused
    payload = search_repository.baidu_baike_search(query, num=num, apply_quota=False)
    return _attach_quota(payload, usage.ENGINE_BAIKE, gate)


def google_web_search(query: str, num: int = 5, topic: str = "") -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "engine": "google", "query": q, "results": []}
    try:
        search_repository._norm_topic(topic)
    except ValueError as exc:
        return {"error": str(exc), "engine": "google", "query": q, "results": []}
    configured = bool((os.environ.get("GOOGLE_CSE_API_KEY") or "").strip() and (os.environ.get("GOOGLE_CSE_CX") or "").strip())
    refused, gate = _guard(usage.ENGINE_GOOGLE, q, configured)
    if refused:
        return refused
    payload = search_repository.google_web_search(query, num=num, topic=topic, apply_quota=False)
    return _attach_quota(payload, usage.ENGINE_GOOGLE, gate)
