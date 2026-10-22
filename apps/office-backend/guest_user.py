"""桌面访客：未登录也可使用本地 SqliteStore。"""
from __future__ import annotations

from store import Store, public_user

GUEST_EMAIL = "guest@local.youjulab"
GUEST_PASSWORD = "guest-local-only"
DEFAULT_PROJECT_NAME = "未命名项目"


def ensure_guest_user(store: Store) -> dict:
    found = store.user_by_email(GUEST_EMAIL)
    if found:
        # 访客项目被认领后可能清空；补一个默认项，避免 /auth/me 反复 ensure 再被绑走堆积
        if not store.projects(found["id"]):
            project = store.create_project(found["id"], DEFAULT_PROJECT_NAME)
            store.update_user(found["id"], last_project_id=project["id"])
            found = store.user_by_id(found["id"]) or found
        return public_user(found)
    created = store.create_user(GUEST_EMAIL, GUEST_PASSWORD)
    project = store.create_project(created["id"], DEFAULT_PROJECT_NAME)
    store.update_user(created["id"], last_project_id=project["id"])
    store.ensure_user_tools(created["id"])
    refreshed = store.user_by_id(created["id"]) or created
    return public_user(refreshed)
