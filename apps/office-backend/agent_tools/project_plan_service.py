"""按名称读写轻量项目计划，保持与工作区 project-plan 编辑器一致的数据约束。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable
from uuid import uuid4

from agent_tools.errors import OfficeError
from agent_tools.project_plan_repository import load_project_plan_asset, save_project_plan_asset
from agent_tools.spreadsheet_service import _ensure_project, _ready
from agent_tools.workbook_service import trim_name

FORMAT = "invp-project-plan-v1"


class ProjectPlanServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def create_project_plan(project_name: str, plan_name: str) -> dict:
    """创建项目计划。项目不存在会创建；同名计划已存在则失败。"""
    api = _ready()
    project_name, plan_name = _names(project_name, plan_name)
    project, created_project = _ensure_project(api, project_name, create=True)
    asset, created = load_project_plan_asset(api, project["id"], plan_name, create=True)
    if not created:
        raise ValueError(f"项目计划「{plan_name}」已存在。")
    return _result("create", project_name, plan_name, asset, _content(asset), {"created": {"project": created_project, "project_plan": True}})


def read_project_plan(project_name: str, plan_name: str) -> dict:
    """读取计划事项。返回扁平 tasks，使用 task_id 表示父子关系。"""
    api = _ready()
    project_name, plan_name = _names(project_name, plan_name)
    project, _ = _ensure_project(api, project_name, create=False)
    asset, _ = load_project_plan_asset(api, project["id"], plan_name)
    return _result("read", project_name, plan_name, asset, _content(asset), {})


def add_project_plan_task(
    project_name: str,
    plan_name: str,
    name: str,
    parent_id: str = "",
    start_date: str = "",
    end_date: str = "",
    progress: int = 0,
    note: str = "",
    task_id: str = "",
) -> dict:
    """新增根事项或子事项。省略日期时根事项为今天至明天，子事项继承父事项日期。"""
    task_name = _text(name, "事项名称", 160)
    parent_id = str(parent_id or "").strip()
    requested_id = str(task_id or "").strip()
    if requested_id and len(requested_id) > 160:
        raise ValueError("task_id 不能超过 160 个字符。")

    def mutate(content: dict) -> dict:
        tasks = content["tasks"]
        ids = {task["id"] for task in tasks}
        if requested_id and requested_id in ids:
            raise ValueError(f"事项 ID「{requested_id}」已存在。")
        parent = _task(tasks, parent_id) if parent_id else None
        if parent_id and not parent:
            raise ValueError(f"父事项「{parent_id}」不存在。")
        fallback_start, fallback_end = (parent["startDate"], parent["endDate"]) if parent else _today_range()
        start = _date(start_date, "开始日期") if start_date else fallback_start
        end = _date(end_date, "截止日期") if end_date else fallback_end
        if end < start:
            raise ValueError("截止日期不能早于开始日期。")
        new_id = requested_id or f"task-{uuid4().hex}"
        tasks.append({
            "id": new_id, "name": task_name, "parentId": parent_id or None,
            "startDate": start, "endDate": end, "progress": _progress(progress),
            "note": _note(note), "order": len([task for task in tasks if task["parentId"] == (parent_id or None)]),
        })
        return {"task_id": new_id, "parent_id": parent_id or None}

    return _mutate(project_name, plan_name, "add_task", mutate)


def update_project_plan_task(
    project_name: str,
    plan_name: str,
    task_id: str,
    name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    progress: int | None = None,
    note: str | None = None,
) -> dict:
    """更新事项提供的字段。父事项有子项时，开始、截止和进度由子项自动汇总，不能手工修改。"""
    task_id = _id(task_id, "事项 ID")
    if all(value is None for value in (name, start_date, end_date, progress, note)):
        raise ValueError("请至少提供 name、start_date、end_date、progress 或 note。")

    def mutate(content: dict) -> dict:
        tasks = content["tasks"]
        task = _task(tasks, task_id)
        if not task:
            raise ValueError(f"事项「{task_id}」不存在。")
        has_children = any(item["parentId"] == task_id for item in tasks)
        if has_children and any(value is not None for value in (start_date, end_date, progress)):
            raise ValueError("有子事项的父事项日期和进度由子事项自动汇总。")
        if name is not None:
            task["name"] = _text(name, "事项名称", 160)
        if note is not None:
            task["note"] = _note(note)
        start = _date(start_date, "开始日期") if start_date is not None else task["startDate"]
        end = _date(end_date, "截止日期") if end_date is not None else task["endDate"]
        if end < start:
            raise ValueError("截止日期不能早于开始日期。")
        task["startDate"], task["endDate"] = start, end
        if progress is not None:
            task["progress"] = _progress(progress)
        return {"task_id": task_id}

    return _mutate(project_name, plan_name, "update_task", mutate)


def move_project_plan_task(
    project_name: str,
    plan_name: str,
    task_id: str,
    parent_id: str = "",
    index: int = -1,
) -> dict:
    """调整事项顺序或父级。parent_id 为空移到根级；index=-1 追加到末尾。拒绝循环关系。"""
    task_id = _id(task_id, "事项 ID")
    parent_id = str(parent_id or "").strip()
    if not isinstance(index, int) or index < -1:
        raise ValueError("index 应为 -1 或非负整数。")

    def mutate(content: dict) -> dict:
        tasks = content["tasks"]
        task = _task(tasks, task_id)
        if not task:
            raise ValueError(f"事项「{task_id}」不存在。")
        if parent_id:
            parent = _task(tasks, parent_id)
            if not parent:
                raise ValueError(f"父事项「{parent_id}」不存在。")
            if parent_id == task_id or _is_descendant(tasks, parent_id, task_id):
                raise ValueError("不能把事项移动到自身或其子事项下。")
        task["parentId"] = parent_id or None
        siblings = sorted((item for item in tasks if item["parentId"] == task["parentId"] and item["id"] != task_id), key=lambda item: item["order"])
        siblings.insert(len(siblings) if index == -1 else min(index, len(siblings)), task)
        for order, sibling in enumerate(siblings):
            sibling["order"] = order
        return {"task_id": task_id, "parent_id": parent_id or None, "index": task["order"]}

    return _mutate(project_name, plan_name, "move_task", mutate)


def delete_project_plan_task(project_name: str, plan_name: str, task_id: str) -> dict:
    """删除事项及全部子事项，返回 deleted_count。"""
    task_id = _id(task_id, "事项 ID")

    def mutate(content: dict) -> dict:
        tasks = content["tasks"]
        if not _task(tasks, task_id):
            raise ValueError(f"事项「{task_id}」不存在。")
        deleted = {task_id}
        while True:
            additions = {item["id"] for item in tasks if item["parentId"] in deleted} - deleted
            if not additions:
                break
            deleted.update(additions)
        content["tasks"] = [item for item in tasks if item["id"] not in deleted]
        return {"task_id": task_id, "deleted_count": len(deleted)}

    return _mutate(project_name, plan_name, "delete_task", mutate)


def _mutate(project_name: str, plan_name: str, action: str, mutate: Callable[[dict], dict]) -> dict:
    api = _ready()
    project_name, plan_name = _names(project_name, plan_name)
    project, _ = _ensure_project(api, project_name, create=False)
    last_error: ProjectPlanServiceError | None = None
    for _ in range(2):
        asset, _ = load_project_plan_asset(api, project["id"], plan_name)
        content = _content(asset)
        extra = mutate(content) or {}
        content = _normalize(content)
        try:
            saved = save_project_plan_asset(api, asset, content)
        except OfficeError as exc:
            last_error = ProjectPlanServiceError(exc.status, str(exc))
            if exc.status == 409:
                continue
            raise last_error from exc
        return _result(action, project_name, plan_name, saved, content, extra)
    raise last_error or ProjectPlanServiceError(409, "项目计划已在其他会话更新，请重试。")


def _content(asset: dict) -> dict:
    raw = asset.get("content")
    return _normalize(raw if isinstance(raw, dict) else {})


def _normalize(raw: dict) -> dict:
    source = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    fallback_start, fallback_end = _today_range()
    tasks: list[dict] = []
    used: set[str] = set()
    for index, value in enumerate(source):
        item = value if isinstance(value, dict) else {}
        task_id = str(item.get("id") or "").strip() or f"task-{index + 1}-{uuid4().hex[:6]}"
        while task_id in used:
            task_id = f"task-{index + 1}-{uuid4().hex[:6]}"
        used.add(task_id)
        start = _date_or(item.get("startDate"), fallback_start)
        end = _date_or(item.get("endDate"), fallback_end)
        tasks.append({
            "id": task_id,
            "name": _text(item.get("name") or f"事项 {index + 1}", "事项名称", 160),
            "parentId": str(item.get("parentId") or "").strip() or None,
            "startDate": start,
            "endDate": end if end >= start else start,
            "progress": _progress(item.get("progress", 0)),
            "note": _note(item.get("note", "")),
            "order": max(0, _integer(item.get("order"), index)),
        })
    ids = {item["id"] for item in tasks}
    for task in tasks:
        if task["parentId"] not in ids or task["parentId"] == task["id"] or _is_descendant(tasks, task["parentId"], task["id"]):
            task["parentId"] = None
    tasks.sort(key=lambda item: (item["parentId"] or "", item["startDate"], item["endDate"], item["order"], item["id"]))
    _renumber(tasks)
    _derive(tasks)
    return {"format": FORMAT, "tasks": tasks}


def _derive(tasks: list[dict]) -> None:
    children: dict[str, list[dict]] = {}
    for task in tasks:
        if task["parentId"]:
            children.setdefault(task["parentId"], []).append(task)
    for task in sorted(tasks, key=lambda item: _depth(tasks, item), reverse=True):
        direct = children.get(task["id"], [])
        if direct:
            task["startDate"] = min(item["startDate"] for item in direct)
            task["endDate"] = max(item["endDate"] for item in direct)
            task["progress"] = round(sum(item["progress"] for item in direct) / len(direct))


def _depth(tasks: list[dict], task: dict) -> int:
    by_id = {item["id"]: item for item in tasks}
    depth, seen, parent_id = 0, {task["id"]}, task["parentId"]
    while parent_id and parent_id not in seen:
        parent = by_id.get(parent_id)
        if not parent:
            break
        seen.add(parent_id)
        depth += 1
        parent_id = parent["parentId"]
    return depth


def _renumber(tasks: list[dict]) -> None:
    order: dict[str | None, int] = {}
    for task in tasks:
        parent_id = task["parentId"]
        task["order"] = order.get(parent_id, 0)
        order[parent_id] = task["order"] + 1


def _task(tasks: list[dict], task_id: str) -> dict | None:
    return next((item for item in tasks if item["id"] == task_id), None)


def _is_descendant(tasks: list[dict], task_id: str | None, ancestor_id: str) -> bool:
    current = task_id
    seen: set[str] = set()
    while current and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        task = _task(tasks, current)
        current = task["parentId"] if task else None
    return False


def _names(project_name: str, plan_name: str) -> tuple[str, str]:
    return trim_name(project_name, "项目名"), trim_name(plan_name, "项目计划名")


def _id(value: Any, label: str) -> str:
    return _text(value, label, 160)


def _text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符。")
    return text


def _note(value: Any) -> str:
    return str(value or "")[:4000]


def _progress(value: Any) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("进度应为 0 到 100 的整数。") from None
    if not 0 <= numeric <= 100:
        raise ValueError("进度应为 0 到 100 的整数。")
    return numeric


def _integer(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _today_range() -> tuple[str, str]:
    start = date.today()
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _date_or(value: Any, fallback: str) -> str:
    try:
        return _date(str(value), "日期")
    except ValueError:
        return fallback


def _date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{label}必须是 YYYY-MM-DD。") from None


def _result(action: str, project_name: str, plan_name: str, asset: dict, content: dict, extra: dict) -> dict:
    return {
        "ok": True, "action": action, "project_name": project_name, "plan_name": plan_name,
        "tasks": content["tasks"], "task_count": len(content["tasks"]), "revision": asset.get("revision", 0), **extra,
    }
