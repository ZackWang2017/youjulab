"""文档接近数据库单条上限时，把当前正文归档，并另开同名新文档承接技能。"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from config import digest_tz

# MongoDB 单条记录上限 16MB。留出余量，避免写到一半失败。
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
_NAME_LIMIT = 80


def content_bytes(content) -> int:
    return len(json.dumps(content if content is not None else {}, ensure_ascii=False, separators=(",", ":")).encode())


def exceeds(content) -> bool:
    return content_bytes(content) > MAX_DOCUMENT_BYTES


def would_exceed(content, extra: str = "") -> bool:
    extra_bytes = len((extra or "").encode())
    # 写入时 Markdown 会展开成文档节点，按原文的数倍估算，提前归档。
    return content_bytes(content) + extra_bytes * 4 + 8192 > MAX_DOCUMENT_BYTES


def archive_document(store, user_id: str, asset: dict) -> dict:
    """把当前文档改名为「原名_bak_时间戳」，再建一篇同名新文档。

    技能、定时资讯和助手会话改挂到新文档，打开记录也跟着走。
    """
    original = (asset.get("name") or "文档").strip() or "文档"
    names = {
        (item.get("name") or "")
        for item in store.assets(user_id, asset["project_id"], "document")
    }
    archived_name = _archive_name(original, names)
    store.update_asset(user_id, asset["id"], name=archived_name)
    fresh = store.create_asset(
        user_id,
        asset["project_id"],
        "document",
        original,
        {"type": "doc", "content": [{"type": "paragraph"}]},
    )
    store.reassign_document_links(user_id, asset["id"], fresh["id"])
    user = store.user_by_id(user_id)
    last = dict((user or {}).get("last_assets") or {})
    if last.get("document") == asset["id"]:
        last["document"] = fresh["id"]
        store.update_user(user_id, last_assets=last)
    return {"asset": fresh, "archived_name": archived_name}


def _archive_name(original: str, taken: set[str]) -> str:
    stamp = datetime.now(ZoneInfo(digest_tz())).strftime("%Y%m%d%H%M%S")
    suffix = f"_bak_{stamp}"
    base = original[: max(1, _NAME_LIMIT - len(suffix))]
    candidate = f"{base}{suffix}"
    number = 2
    while candidate in taken:
        extra = f"_{number}"
        candidate = f"{base[: max(1, _NAME_LIMIT - len(suffix) - len(extra))]}{suffix}{extra}"
        number += 1
    return candidate[:_NAME_LIMIT]
