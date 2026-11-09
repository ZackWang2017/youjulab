"""对标原 OfficeClient：服务层用同一套 me/projects/assets API，底层直连 Store。"""
from __future__ import annotations

import contextvars
from typing import Any, Callable

from store import (
    Store,
    default_document,
    default_diagram,
    default_mindmap,
    default_project_plan,
    default_sheet,
    public_asset,
    public_project,
    public_user,
)
from sheet_crypto import is_encrypted

from agent_tools.errors import OfficeError

ClientFactory = Callable[[], Any]
_factory: ClientFactory | None = None
_current_store: contextvars.ContextVar[Store | None] = contextvars.ContextVar("agent_tools_store", default=None)
_current_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("agent_tools_user", default=None)


def set_client_factory(factory: ClientFactory | None) -> None:
    global _factory
    _factory = factory


def bind_agent_context(store: Store, user: dict) -> tuple[contextvars.Token, contextvars.Token]:
    return _current_store.set(store), _current_user.set(user)


def reset_agent_context(store_token: contextvars.Token, user_token: contextvars.Token) -> None:
    _current_store.reset(store_token)
    _current_user.reset(user_token)


def client_for() -> Any:
    if _factory:
        return _factory()
    store = _current_store.get()
    user = _current_user.get()
    if store is None or not user:
        raise OfficeError(401, "请提供用户访问令牌。")
    return StoreClient(store, user)


class StoreClient:
    def __init__(self, store: Store, user: dict):
        self.store = store
        self.user = user
        self.owner_id = str(user["id"])

    def me(self) -> dict:
        return public_user(self.user)

    def projects(self) -> list[dict]:
        return [public_project(item) for item in self.store.projects(self.owner_id)]

    def create_project(self, name: str) -> dict:
        return public_project(self.store.create_project(self.owner_id, name.strip()))

    def assets(self, project_id: str, kind: str = "sheet") -> list[dict]:
        return [public_asset(item) for item in self.store.assets(self.owner_id, project_id, kind)]

    def create_asset(self, project_id: str, name: str, kind: str = "sheet", *, content: dict | None = None, visibility: str = "project") -> dict:
        if not self.store.project(self.owner_id, project_id):
            raise OfficeError(404, "项目不存在。")
        if visibility not in {"project", "user"}:
            raise ValueError("visibility 无效。")
        content = content if content is not None else _default_content(kind, name.strip())
        if kind == "diagram":
            from drawio import diagram_content
            content = diagram_content(content)
        asset = self.store.create_asset(self.owner_id, None if visibility == "user" else project_id, kind, name.strip(), content)
        return public_asset(asset, include_content=True)

    def get_asset(self, asset_id: str) -> dict:
        asset = self.store.asset(self.owner_id, asset_id)
        if not asset:
            raise OfficeError(404, "文件不存在。")
        if is_encrypted(asset.get("content")):
            raise OfficeError(403, "表格已加密，请先在编辑器中解锁。")
        return public_asset(asset, include_content=True)

    def save_asset(self, asset_id: str, content: dict, revision: int, name: str | None = None) -> dict:
        asset = self.store.asset(self.owner_id, asset_id)
        if not asset:
            raise OfficeError(404, "文件不存在。")
        if revision != asset.get("revision", 0):
            raise OfficeError(409, "内容已在其他会话更新，请重新打开。")
        if asset.get("kind") == "sheet":
            from sheet_identity import sheet_content_for_asset
            try:
                content = sheet_content_for_asset(asset_id, content)
            except ValueError as exc:
                raise OfficeError(409, str(exc)) from exc
        if asset.get("kind") == "diagram":
            from drawio import diagram_content
            content = diagram_content(content)
        fields: dict[str, Any] = {"content": content, "revision": asset.get("revision", 0) + 1}
        if name is not None:
            fields["name"] = name.strip() or asset["name"]
        if asset.get('kind') in ('diagram', 'sheet'):
            updated = self.store.update_asset(self.owner_id, asset_id, expected_revision=revision, **fields)
        else:
            updated = self.store.update_asset(self.owner_id, asset_id, **fields)
        if not updated:
            if self.store.asset(self.owner_id, asset_id):
                raise OfficeError(409, "绘图已更新，请重新读取后合并修改。")
            raise OfficeError(404, "文件不存在。")
        return public_asset(updated, include_content=True)


def _default_content(kind: str, name: str) -> Any:
    if kind == "sheet":
        return default_sheet()
    if kind == "document":
        return default_document()
    if kind == "diagram":
        return default_diagram()
    if kind == "mindmap":
        return default_mindmap(name or "中心主题")
    if kind == "project_plan":
        return default_project_plan()
    return None
