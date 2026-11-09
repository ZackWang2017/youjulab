"""项目计划资产的数据访问层。"""
from __future__ import annotations

from typing import Any


def find_project_plan(api: Any, project_id: str, name: str) -> dict | None:
    matches = [item for item in api.assets(project_id, "project_plan") if item.get("name") == name]
    if len(matches) > 1:
        raise ValueError(f"存在多个同名项目计划「{name}」，请先在工作区重命名后再操作。")
    return matches[0] if matches else None


def load_project_plan_asset(api: Any, project_id: str, name: str, create: bool = False) -> tuple[dict, bool]:
    found = find_project_plan(api, project_id, name)
    created = False
    if not found:
        if not create:
            names = [item.get("name") or "" for item in api.assets(project_id, "project_plan")]
            extra = "、".join(item for item in names if item) or "无"
            raise ValueError(f"项目计划「{name}」不存在。已有：{extra}。")
        found = api.create_asset(project_id, name, kind="project_plan")
        created = True
    asset = api.get_asset(found["id"])
    if asset.get("kind") != "project_plan":
        raise ValueError(f"「{name}」不是项目计划，不能用本接口访问。")
    return asset, created


def save_project_plan_asset(api: Any, asset: dict, content: dict) -> dict:
    return api.save_asset(asset["id"], content, asset.get("revision", 0), name=asset.get("name"))
