"""按项目名和导图名读写 Mind Elixir 思维导图。"""
from __future__ import annotations

import copy
from typing import Any, Callable
from uuid import uuid4

from mindmap_repository import OfficeError, find_mindmap, load_mindmap_asset, save_mindmap_asset
from office_repository import client_for
from spreadsheet_service import _ensure_project, _ready
from workbook_service import trim_name


class MindmapServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def create_mindmap(project_name: str, mindmap_name: str, root_topic: str = "") -> dict:
    """创建思维导图。项目不存在会创建；同名导图已存在则失败。"""
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    mindmap_name = trim_name(mindmap_name, "导图名")
    topic = _topic(root_topic or mindmap_name, "中心主题")
    project, created_project = _ensure_project(api, project_name, create=True)
    if find_mindmap(api, project["id"], mindmap_name):
        raise ValueError(f"导图「{mindmap_name}」已存在。")
    asset, _ = load_mindmap_asset(api, project["id"], mindmap_name, create=True)
    content = _content_from_asset(asset, topic)
    content["nodeData"]["topic"] = topic
    try:
        saved = save_mindmap_asset(api, asset, content)
    except OfficeError as exc:
        raise MindmapServiceError(exc.status, str(exc)) from exc
    return {
        "ok": True,
        "action": "create",
        "project_name": project_name,
        "mindmap_name": mindmap_name,
        "created": {"project": created_project, "mindmap": True},
        "root": _public_node(content["nodeData"]),
        "revision": saved.get("revision", 0),
    }


def read_mindmap(project_name: str, mindmap_name: str) -> dict:
    """读取完整导图树和扁平节点列表。不自动创建。"""
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    mindmap_name = trim_name(mindmap_name, "导图名")
    project, _ = _ensure_project(api, project_name, create=False)
    asset, _ = load_mindmap_asset(api, project["id"], mindmap_name)
    content = _content_from_asset(asset, mindmap_name)
    nodes = _describe_nodes(content["nodeData"])
    return {
        "ok": True,
        "action": "read",
        "project_name": project_name,
        "mindmap_name": mindmap_name,
        "root": copy.deepcopy(content["nodeData"]),
        "nodes": nodes,
        "node_count": len(nodes),
        "direction": content.get("direction", 2),
        "revision": asset.get("revision", 0),
    }


def write_mindmap(project_name: str, mindmap_name: str, root: dict) -> dict:
    """用嵌套 root 节点整树替换导图。项目或导图不存在时会创建。"""
    normalized = _normalize_tree(root)

    def mutate(content: dict) -> dict:
        content["nodeData"] = normalized
        return {"root_id": normalized["id"]}

    return _mutate(project_name, mindmap_name, "write", mutate, create=True)


def add_mindmap_node(
    project_name: str,
    mindmap_name: str,
    parent_id: str,
    topic: str,
    note: str = "",
    tags: list[str] | None = None,
    hyperlink: str = "",
    node_id: str = "",
) -> dict:
    """在 parent_id 下新增子节点，返回生成或指定的 node_id。"""
    parent_id = _id(parent_id, "父节点 ID")
    new_node = _node(node_id or f"node-{uuid4().hex}", topic, note, tags, hyperlink)

    def mutate(content: dict) -> dict:
        root = content["nodeData"]
        if _find_node(root, new_node["id"]):
            raise ValueError(f"节点 ID「{new_node['id']}」已存在。")
        parent = _find_node(root, parent_id)
        if not parent:
            raise ValueError(f"父节点「{parent_id}」不存在。")
        parent.setdefault("children", []).append(new_node)
        return {"node_id": new_node["id"], "parent_id": parent_id}

    return _mutate(project_name, mindmap_name, "add_node", mutate)


def update_mindmap_node(
    project_name: str,
    mindmap_name: str,
    node_id: str,
    topic: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    hyperlink: str | None = None,
) -> dict:
    """更新节点。只修改提供的 topic、note、tags、hyperlink；空值可清除附加属性。"""
    node_id = _id(node_id, "节点 ID")
    if all(value is None for value in (topic, note, tags, hyperlink)):
        raise ValueError("请至少提供 topic、note、tags 或 hyperlink。")

    def mutate(content: dict) -> dict:
        node = _find_node(content["nodeData"], node_id)
        if not node:
            raise ValueError(f"节点「{node_id}」不存在。")
        if topic is not None:
            node["topic"] = _topic(topic, "节点主题")
        _set_optional(node, "note", note)
        if tags is not None:
            clean = _tags(tags)
            if clean:
                node["tags"] = clean
            else:
                node.pop("tags", None)
        _set_optional(node, "hyperLink", hyperlink)
        return {"node_id": node_id}

    return _mutate(project_name, mindmap_name, "update_node", mutate)


def delete_mindmap_node(project_name: str, mindmap_name: str, node_id: str) -> dict:
    """删除节点及全部后代。根节点不能删除。"""
    node_id = _id(node_id, "节点 ID")

    def mutate(content: dict) -> dict:
        root = content["nodeData"]
        if root["id"] == node_id:
            raise ValueError("根节点不能删除。")
        removed = _detach_node(root, node_id)
        if not removed:
            raise ValueError(f"节点「{node_id}」不存在。")
        return {"node_id": node_id, "deleted_count": len(_describe_nodes(removed))}

    return _mutate(project_name, mindmap_name, "delete_node", mutate)


def move_mindmap_node(
    project_name: str,
    mindmap_name: str,
    node_id: str,
    new_parent_id: str,
    index: int = -1,
) -> dict:
    """把节点移动到新父节点下。index 从 0 开始，-1 表示追加到末尾。"""
    node_id = _id(node_id, "节点 ID")
    new_parent_id = _id(new_parent_id, "新父节点 ID")
    if not isinstance(index, int) or index < -1:
        raise ValueError("index 应为 -1 或非负整数。")

    def mutate(content: dict) -> dict:
        root = content["nodeData"]
        if root["id"] == node_id:
            raise ValueError("根节点不能移动。")
        node = _find_node(root, node_id)
        parent = _find_node(root, new_parent_id)
        if not node:
            raise ValueError(f"节点「{node_id}」不存在。")
        if not parent:
            raise ValueError(f"新父节点「{new_parent_id}」不存在。")
        if _find_node(node, new_parent_id):
            raise ValueError("不能把节点移动到自身或其后代节点下。")
        detached = _detach_node(root, node_id)
        children = parent.setdefault("children", [])
        position = len(children) if index == -1 else min(index, len(children))
        children.insert(position, detached)
        return {"node_id": node_id, "parent_id": new_parent_id, "index": position}

    return _mutate(project_name, mindmap_name, "move_node", mutate)


def _mutate(
    project_name: str,
    mindmap_name: str,
    action: str,
    mutate: Callable[[dict], dict],
    create: bool = False,
) -> dict:
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    mindmap_name = trim_name(mindmap_name, "导图名")
    project, _ = _ensure_project(api, project_name, create=create)
    last_error: MindmapServiceError | None = None
    for _ in range(2):
        asset, created = load_mindmap_asset(api, project["id"], mindmap_name, create=create)
        content = _content_from_asset(asset, mindmap_name)
        extra = mutate(content) or {}
        try:
            saved = save_mindmap_asset(api, asset, content)
        except OfficeError as exc:
            last_error = MindmapServiceError(exc.status, str(exc))
            if exc.status == 409:
                continue
            raise last_error from exc
        nodes = _describe_nodes(content["nodeData"])
        return {
            "ok": True,
            "action": action,
            "project_name": project_name,
            "mindmap_name": mindmap_name,
            "created": {"mindmap": created},
            "node_count": len(nodes),
            "revision": saved.get("revision", 0),
            **extra,
        }
    raise last_error or MindmapServiceError(409, "内容已在其他会话更新，请重试。")


def _content_from_asset(asset: dict, fallback_topic: str) -> dict:
    raw = asset.get("content")
    if not isinstance(raw, dict) or not isinstance(raw.get("nodeData"), dict):
        return _new_content(fallback_topic)
    content = copy.deepcopy(raw)
    content["format"] = "mind-elixir-v1"
    content["nodeData"] = _normalize_tree(content["nodeData"])
    content.setdefault("arrows", [])
    content.setdefault("summaries", [])
    content.setdefault("direction", 2)
    return content


def _new_content(topic: str) -> dict:
    return {
        "format": "mind-elixir-v1",
        "nodeData": {"id": "root", "topic": _topic(topic, "中心主题"), "children": []},
        "arrows": [],
        "summaries": [],
        "direction": 2,
    }


def _normalize_tree(root: Any) -> dict:
    if not isinstance(root, dict):
        raise ValueError("root 必须是包含 id、topic、children 的对象。")
    seen: set[str] = set()

    def visit(raw: dict) -> dict:
        node_id = _id(raw.get("id"), "节点 ID")
        if node_id in seen:
            raise ValueError(f"节点 ID「{node_id}」重复。")
        seen.add(node_id)
        children = raw.get("children") or []
        if not isinstance(children, list) or not all(isinstance(item, dict) for item in children):
            raise ValueError(f"节点「{node_id}」的 children 必须是对象数组。")
        node = _node(node_id, raw.get("topic"), raw.get("note", ""), raw.get("tags"), raw.get("hyperLink", ""))
        node["children"] = [visit(child) for child in children]
        for key in ("direction", "style", "image", "icons", "expanded"):
            if key in raw:
                node[key] = copy.deepcopy(raw[key])
        return node

    return visit(root)


def _node(node_id: Any, topic: Any, note: Any = "", tags: Any = None, hyperlink: Any = "") -> dict:
    node = {"id": _id(node_id, "节点 ID"), "topic": _topic(topic, "节点主题"), "children": []}
    _set_optional(node, "note", note)
    clean_tags = _tags(tags)
    if clean_tags:
        node["tags"] = clean_tags
    _set_optional(node, "hyperLink", hyperlink)
    return node


def _id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > 160:
        raise ValueError(f"{label}不能超过 160 个字符。")
    return text


def _topic(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > 500:
        raise ValueError(f"{label}不能超过 500 个字符。")
    return text


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags 必须是字符串数组。")
    result = []
    for item in value:
        raw = item.get("text") if isinstance(item, dict) else item
        tag = str(raw or "").strip()
        if tag and tag not in result:
            result.append(tag[:80])
    return result[:20]


def _set_optional(node: dict, key: str, value: Any) -> None:
    if value is None:
        return
    text = str(value or "").strip()
    if text:
        node[key] = text
    else:
        node.pop(key, None)


def _find_node(root: dict, node_id: str) -> dict | None:
    if root.get("id") == node_id:
        return root
    for child in root.get("children") or []:
        found = _find_node(child, node_id)
        if found:
            return found
    return None


def _detach_node(root: dict, node_id: str) -> dict | None:
    children = root.get("children") or []
    for index, child in enumerate(children):
        if child.get("id") == node_id:
            return children.pop(index)
        found = _detach_node(child, node_id)
        if found:
            return found
    return None


def _public_node(node: dict) -> dict:
    return {key: copy.deepcopy(value) for key, value in node.items() if key != "children"}


def _describe_nodes(root: dict, parent_id: str | None = None, path: list[str] | None = None) -> list[dict]:
    current_path = [*(path or []), root["topic"]]
    item = _public_node(root)
    item.update({
        "parent_id": parent_id,
        "path": current_path,
        "child_count": len(root.get("children") or []),
    })
    result = [item]
    for child in root.get("children") or []:
        result.extend(_describe_nodes(child, root["id"], current_path))
    return result
