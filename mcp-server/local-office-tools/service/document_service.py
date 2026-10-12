"""按项目名 / 文档名创建、读取、修改文档，并设置块级格式。不注册到财务 MCP。"""
from __future__ import annotations

from document_content_service import (
    append_markdown,
    describe_blocks,
    document_from_asset,
    document_to_markdown,
    format_block,
    insert_markdown,
    prepend_markdown,
    replace_document,
    update_block,
)
from office_repository import OfficeError, client_for
from spreadsheet_service import _ensure_project, _named, _ready
from workbook_service import trim_name


class DocumentServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def create_document(project_name: str, document_name: str) -> dict:
    """按名称创建文档。项目不存在会创建。同名文档已存在则失败。"""
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    document_name = trim_name(document_name, "文档名")
    project, created_project = _ensure_project(api, project_name, create=True)
    existing = _named(api.assets(project["id"], "document"), document_name, "文档")
    if existing:
        raise ValueError(f"文档「{document_name}」已存在。")
    created = api.create_asset(project["id"], document_name, kind="document")
    return {
        "ok": True,
        "action": "create",
        "project_name": project_name,
        "document_name": document_name,
        "created": {"project": created_project, "document": True},
        "revision": created.get("revision", 0),
    }


def read_document(project_name: str, document_name: str) -> dict:
    """按名称读取文档。返回 Markdown、块列表（index/type/text/marks）和 revision。不自动创建。"""
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    document_name = trim_name(document_name, "文档名")
    project, _ = _ensure_project(api, project_name, create=False)
    asset = _load_document(api, project["id"], document_name, create=False)
    doc = document_from_asset(asset)
    blocks = describe_blocks(doc)
    return {
        "ok": True,
        "action": "read",
        "project_name": project_name,
        "document_name": document_name,
        "markdown": document_to_markdown(doc),
        "blocks": blocks,
        "block_count": len(blocks),
        "revision": asset.get("revision", 0),
    }


def write_document(
    project_name: str,
    document_name: str,
    markdown: str,
    mode: str = "replace",
    block_index: int = -1,
) -> dict:
    """修改文档正文。mode：replace 整篇替换，append 追加到末尾，prepend 插到最上方，insert 插到 block_index 前，update 替换该块。

    项目或文档不存在时会创建。Markdown 支持标题、分割线（---）、段落、列表、引用、代码块，以及加粗、斜体、删除线、行内代码。
    编号条目下的「○ 」是第三级空心圆列表。同一行里从「🔗」起到原文链接缩小一号。
    """
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    document_name = trim_name(document_name, "文档名")
    action = (mode or "replace").strip().lower()
    if action not in ("replace", "append", "prepend", "insert", "update"):
        raise ValueError("mode 应为 replace、append、prepend、insert 或 update。")
    project, _ = _ensure_project(api, project_name, create=True)

    def mutate(content: dict) -> dict:
        if action == "replace":
            return replace_document(content, markdown)
        if action == "append":
            return append_markdown(content, markdown)
        if action == "prepend":
            return prepend_markdown(content, markdown)
        if action == "insert":
            return insert_markdown(content, markdown, block_index)
        return update_block(content, markdown, block_index)

    saved = _save(api, project["id"], document_name, mutate)
    doc = document_from_asset(saved)
    return _result("write", project_name, document_name, saved, doc, {"mode": action})


def format_document(
    project_name: str,
    document_name: str,
    block_index: int,
    block_type: str = "",
    bold: bool | None = None,
    italic: bool | None = None,
    strike: bool | None = None,
    code: bool | None = None,
) -> dict:
    """设置某一个块的格式。block_type：paragraph、h1、h2、h3、bullet、ordered、blockquote、code。

    bold / italic / strike / code 只改提供的项，作用在该块的全部文字上。文档不存在则报错。
    """
    api = _ready()
    project_name = trim_name(project_name, "项目名")
    document_name = trim_name(document_name, "文档名")
    if not (block_type or "").strip() and all(flag is None for flag in (bold, italic, strike, code)):
        raise ValueError("请至少指定 block_type 或一种文字样式。")
    project, _ = _ensure_project(api, project_name, create=False)

    def mutate(content: dict) -> dict:
        return format_block(content, block_index, block_type, bold, italic, strike, code)

    saved = _save(api, project["id"], document_name, mutate, create=False)
    doc = document_from_asset(saved)
    return _result("format", project_name, document_name, saved, doc, {
        "block_index": block_index,
        "block_type": (block_type or "").strip().lower(),
    })


def _load_document(api, project_id: str, name: str, create: bool) -> tuple[dict, bool] | dict:
    found = _named(api.assets(project_id, "document"), name, "文档")
    created = False
    if not found:
        if not create:
            names = [item.get("name") or "" for item in api.assets(project_id, "document")]
            extra = "、".join(name for name in names if name) or "无"
            raise ValueError(f"文档「{name}」不存在。已有：{extra}。")
        found = api.create_asset(project_id, name, kind="document")
        created = True
    asset = api.get_asset(found["id"])
    if asset.get("kind") != "document":
        raise ValueError(f"「{name}」不是文档，不能用本接口访问。")
    if create:
        return asset, created
    return asset


def _save(api, project_id: str, name: str, mutate, create: bool = True) -> dict:
    last_error: DocumentServiceError | None = None
    for _ in range(2):
        loaded = _load_document(api, project_id, name, create=create)
        asset = loaded[0] if create else loaded
        content = document_from_asset(asset)
        updated = mutate(content)
        try:
            return api.save_asset(asset["id"], updated, asset.get("revision", 0), name=name)
        except OfficeError as exc:
            last_error = DocumentServiceError(exc.status, str(exc))
            if exc.status != 409:
                raise last_error
    raise last_error or DocumentServiceError(409, "内容已在其他会话更新，请重试。")


def _result(action: str, project_name: str, document_name: str, saved: dict, doc: dict, extra: dict) -> dict:
    blocks = describe_blocks(doc)
    result = {
        "ok": True,
        "action": action,
        "project_name": project_name,
        "document_name": document_name,
        "markdown": document_to_markdown(doc),
        "blocks": blocks,
        "block_count": len(blocks),
        "revision": saved.get("revision", 0),
    }
    result.update(extra)
    return result
