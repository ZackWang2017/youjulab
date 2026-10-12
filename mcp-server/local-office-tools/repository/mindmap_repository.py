"""思维导图资产的数据访问层。"""
from __future__ import annotations

from typing import Any

from office_repository import OfficeError


def find_mindmap(api: Any, project_id: str, name: str) -> dict | None:
    matches = [item for item in api.assets(project_id, "mindmap") if item.get("name") == name]
    if len(matches) > 1:
        raise ValueError(f"存在多个同名导图「{name}」，请先在工作区重命名后再操作。")
    return matches[0] if matches else None


def create_mindmap_asset(api: Any, project_id: str, name: str) -> dict:
    return api.create_asset(project_id, name, kind="mindmap")


def load_mindmap_asset(api: Any, project_id: str, name: str, create: bool = False) -> tuple[dict, bool]:
    found = find_mindmap(api, project_id, name)
    created = False
    if not found:
        if not create:
            names = [item.get("name") or "" for item in api.assets(project_id, "mindmap")]
            extra = "、".join(item for item in names if item) or "无"
            raise ValueError(f"导图「{name}」不存在。已有：{extra}。")
        found = create_mindmap_asset(api, project_id, name)
        created = True
    asset = api.get_asset(found["id"])
    if asset.get("kind") != "mindmap":
        raise ValueError(f"「{name}」不是导图，不能用本接口访问。")
    return asset, created


def save_mindmap_asset(api: Any, asset: dict, content: dict) -> dict:
    return api.save_asset(asset["id"], content, asset.get("revision", 0), name=asset.get("name"))


__all__ = [
    "OfficeError",
    "create_mindmap_asset",
    "find_mindmap",
    "load_mindmap_asset",
    "save_mindmap_asset",
]
