"""指定公众号的文章列表。rsshub 用微信号，weread 用微信读书里的公众号 id。"""
from __future__ import annotations

import os
import re
from typing import Any

import wechat_account_repository as account_repository
import weread_session_service as weread_service

_MAX_RESULTS = 10
_ACCOUNT = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


def list_wechat_account(account: str, source: str = "rsshub", num: int = 10) -> dict[str, Any]:
    source_name = (source or "").strip().lower()
    account_id = (account or "").strip()
    if source_name not in {"rsshub", "weread"}:
        return {"error": "source 只能是 rsshub 或 weread。", "source": source_name, "account": account_id, "results": []}
    if not _ACCOUNT.match(account_id):
        return {"error": "账号标识只能包含字母、数字、下划线和短横线。", "source": source_name, "account": account_id, "results": []}
    if source_name == "weread":
        fetched = weread_service.list_articles(account_id, _clamp(num))
        return {
            "error": fetched.get("error") or "",
            "source": source_name,
            "account": account_id,
            "results": fetched.get("results") or [],
        }
    feed_url, error = _feed_url(source_name, account_id)
    if error:
        return {"error": error, "source": source_name, "account": account_id, "results": []}
    try:
        fetched = account_repository.fetch_feed(feed_url)
    except ValueError as exc:
        return {"error": str(exc), "source": source_name, "account": account_id, "results": []}
    if fetched.get("error"):
        return {"error": fetched["error"], "source": source_name, "account": account_id, "results": []}
    return {
        "source": source_name,
        "account": account_id,
        "results": (fetched.get("items") or [])[:_clamp(num)],
    }


def _feed_url(source_name: str, account_id: str) -> tuple[str, str]:
    if source_name == "rsshub":
        base = (os.environ.get("DIGEST_RSSHUB_BASE") or "").strip().rstrip("/")
        if not base:
            return "", "未配置 DIGEST_RSSHUB_BASE，无法拉取指定公众号。"
        return f"{base}/wechat/sogou/{account_id}", ""
    return "", "source 只能是 rsshub 或 weread。"


def _clamp(num: int) -> int:
    try:
        count = int(num)
    except (TypeError, ValueError):
        count = _MAX_RESULTS
    return max(1, min(_MAX_RESULTS, count))
