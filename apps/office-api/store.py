"""用户、项目、资产存储：MongoDB 或进程内内存。"""
from __future__ import annotations

import copy
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from config import mongo_db, mongo_url

KINDS = ("sheet", "document", "pdf", "mindmap", "calendar")
DEFAULT_SHEET_NAME = "未命名表格"
DEFAULT_DOCUMENT_NAME = "未命名文档"
DEFAULT_MINDMAP_NAME = "未命名导图"
DEFAULT_CALENDAR_NAME = "日常工作"
ACCESS_TOKEN_LENGTH = 32
CALENDAR_SCOPES = ("personal", "project")


def empty_last_assets() -> dict:
    return {"sheet": None, "document": None, "pdf": None, "mindmap": None, "calendar": None}


def new_access_token() -> str:
    return secrets.token_hex(ACCESS_TOKEN_LENGTH // 2)


def default_sheet() -> dict:
    return {}


def default_document() -> dict:
    return {"type": "doc", "content": [{"type": "paragraph"}]}


def default_mindmap(topic: str = "中心主题") -> dict:
    """Mind Elixir 可直接 init 的空导图。"""
    return {
        "format": "mind-elixir-v1",
        "nodeData": {"id": "root", "topic": topic or "中心主题", "children": []},
        "arrows": [],
        "summaries": [],
        "direction": 2,
    }


def default_calendar() -> dict:
    return {
        "format": "invp-calendar-v1",
        "timezone": "Asia/Shanghai",
        "days": {},
        "monthlyEvents": [],
        "todos": "",
    }


def normalize_scope(kind: str, scope: str | None) -> str | None:
    if kind != "calendar":
        return None
    raw = (scope or "personal").strip().lower()
    return raw if raw in CALENDAR_SCOPES else "personal"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid4().hex


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return secrets.compare_digest(check, digest)


def public_user(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "email": doc["email"],
        "last_project_id": doc.get("last_project_id"),
        "last_assets": {**empty_last_assets(), **dict(doc.get("last_assets") or {})},
    }


def public_project(doc: dict) -> dict:
    return {"id": doc["id"], "name": doc["name"], "created_at": doc["created_at"], "updated_at": doc["updated_at"]}


def public_asset(doc: dict, include_content: bool = False) -> dict:
    item = {
        "id": doc["id"],
        "project_id": doc["project_id"],
        "kind": doc["kind"],
        "name": doc["name"],
        "revision": doc.get("revision", 0),
        "updated_at": doc["updated_at"],
        "filename": doc.get("filename"),
        "size": doc.get("size"),
    }
    if doc.get("kind") == "calendar":
        item["scope"] = doc.get("scope") or "personal"
    if doc.get("index_status"):
        item["index_status"] = doc["index_status"]
    if include_content and doc["kind"] != "pdf":
        item["content"] = doc.get("content")
    return item


class Store:
    def create_user(self, email: str, password: str) -> dict: raise NotImplementedError
    def user_by_email(self, email: str) -> dict | None: raise NotImplementedError
    def user_by_id(self, user_id: str) -> dict | None: raise NotImplementedError
    def user_by_access_token(self, token: str) -> dict | None: raise NotImplementedError
    def ensure_access_token(self, user: dict) -> dict: raise NotImplementedError
    def rotate_access_token(self, user_id: str) -> dict | None: raise NotImplementedError
    def update_user(self, user_id: str, **fields: Any) -> dict | None: raise NotImplementedError
    def create_project(self, owner_id: str, name: str) -> dict: raise NotImplementedError
    def projects(self, owner_id: str) -> list[dict]: raise NotImplementedError
    def project(self, owner_id: str, project_id: str) -> dict | None: raise NotImplementedError
    def update_project(self, owner_id: str, project_id: str, **fields: Any) -> dict | None: raise NotImplementedError
    def delete_project(self, owner_id: str, project_id: str) -> list[dict] | None: raise NotImplementedError
    def create_asset(self, owner_id: str, project_id: str, kind: str, name: str, content: Any = None, scope: str | None = None) -> dict: raise NotImplementedError
    def assets(self, owner_id: str, project_id: str, kind: str | None = None) -> list[dict]: raise NotImplementedError
    def personal_calendars(self, owner_id: str) -> list[dict]: raise NotImplementedError
    def reassign_personal_calendars(self, owner_id: str, from_project_id: str, to_project_id: str) -> int: raise NotImplementedError
    def asset(self, owner_id: str, asset_id: str) -> dict | None: raise NotImplementedError
    def update_asset(self, owner_id: str, asset_id: str, **fields: Any) -> dict | None: raise NotImplementedError
    def delete_asset(self, owner_id: str, asset_id: str) -> dict | None: raise NotImplementedError
    def all_projects(self) -> list[dict]: raise NotImplementedError
    def project_any(self, project_id: str) -> dict | None: raise NotImplementedError
    def assets_any(self, project_id: str, kind: str | None = None) -> list[dict]: raise NotImplementedError
    def asset_any(self, asset_id: str) -> dict | None: raise NotImplementedError
    def users(self) -> list[dict]: raise NotImplementedError
    def get_digest_job(self, user_id: str, document_id: str) -> dict | None: raise NotImplementedError
    def upsert_digest_job(self, user_id: str, document_id: str, fields: dict) -> dict: raise NotImplementedError
    def delete_digest_job(self, user_id: str, document_id: str) -> None: raise NotImplementedError
    def list_enabled_digest_jobs(self) -> list[dict]: raise NotImplementedError
    def list_assistant_sessions(self, user_id: str, document_id: str, limit: int = 30) -> list[dict]: raise NotImplementedError
    def get_assistant_session(self, user_id: str, session_id: str) -> dict | None: raise NotImplementedError
    def upsert_assistant_session(self, user_id: str, document_id: str, fields: dict) -> dict: raise NotImplementedError
    def delete_assistant_session(self, user_id: str, session_id: str) -> bool: raise NotImplementedError
    def get_sheet_cache(self, user_id: str, document_id: str, sheet_id: str) -> dict | None: raise NotImplementedError
    def upsert_sheet_cache(self, user_id: str, document_id: str, fields: dict) -> dict: raise NotImplementedError


class MemoryStore(Store):
    def __init__(self) -> None:
        self._users: dict[str, dict] = {}
        self._projects: dict[str, dict] = {}
        self._assets: dict[str, dict] = {}
        self._digest_jobs: dict[str, dict] = {}
        self._assistant_sessions: dict[str, dict] = {}
        self._sheet_cache: dict[str, dict] = {}

    def create_user(self, email: str, password: str) -> dict:
        user = {
            "id": new_id(), "email": email.lower(), "password_hash": hash_password(password),
            "access_token": self._unique_access_token(),
            "last_project_id": None, "last_assets": empty_last_assets(),
            "created_at": now(),
        }
        self._users[user["id"]] = user
        return user

    def _unique_access_token(self) -> str:
        for _ in range(8):
            token = new_access_token()
            if not self.user_by_access_token(token):
                return token
        raise RuntimeError("无法生成访问令牌。")

    def user_by_email(self, email: str) -> dict | None:
        needle = email.lower()
        return next((u for u in self._users.values() if u["email"] == needle), None)

    def user_by_id(self, user_id: str) -> dict | None:
        found = self._users.get(user_id)
        return copy.deepcopy(found) if found else None

    def user_by_access_token(self, token: str) -> dict | None:
        token = (token or "").strip()
        if len(token) != ACCESS_TOKEN_LENGTH:
            return None
        for user in self._users.values():
            stored = user.get("access_token") or ""
            if len(stored) == len(token) and secrets.compare_digest(stored, token):
                return copy.deepcopy(user)
        return None

    def ensure_access_token(self, user: dict) -> dict:
        token = (user.get("access_token") or "").strip()
        if len(token) == ACCESS_TOKEN_LENGTH:
            return user
        return self.rotate_access_token(user["id"]) or user

    def rotate_access_token(self, user_id: str) -> dict | None:
        return self.update_user(user_id, access_token=self._unique_access_token())

    def update_user(self, user_id: str, **fields: Any) -> dict | None:
        user = self._users.get(user_id)
        if not user:
            return None
        user.update(fields)
        return copy.deepcopy(user)

    def create_project(self, owner_id: str, name: str) -> dict:
        project = {"id": new_id(), "owner_id": owner_id, "name": name, "created_at": now(), "updated_at": now()}
        self._projects[project["id"]] = project
        return copy.deepcopy(project)

    def projects(self, owner_id: str) -> list[dict]:
        items = [copy.deepcopy(p) for p in self._projects.values() if p["owner_id"] == owner_id]
        items.sort(key=lambda p: p["updated_at"], reverse=True)
        return items

    def project(self, owner_id: str, project_id: str) -> dict | None:
        found = self._projects.get(project_id)
        if not found or found["owner_id"] != owner_id:
            return None
        return copy.deepcopy(found)

    def update_project(self, owner_id: str, project_id: str, **fields: Any) -> dict | None:
        found = self._projects.get(project_id)
        if not found or found["owner_id"] != owner_id:
            return None
        found.update(fields)
        found["updated_at"] = now()
        return copy.deepcopy(found)

    def delete_project(self, owner_id: str, project_id: str) -> list[dict] | None:
        found = self._projects.get(project_id)
        if not found or found["owner_id"] != owner_id:
            return None
        removed = [copy.deepcopy(a) for a in self._assets.values() if a["project_id"] == project_id]
        self._assets = {k: v for k, v in self._assets.items() if v["project_id"] != project_id}
        del self._projects[project_id]
        return removed

    def create_asset(self, owner_id: str, project_id: str, kind: str, name: str, content: Any = None, scope: str | None = None) -> dict:
        asset = {
            "id": new_id(), "owner_id": owner_id, "project_id": project_id, "kind": kind, "name": name,
            "revision": 0, "content": content, "file_path": None, "filename": None, "size": None,
            "updated_at": now(),
        }
        norm = normalize_scope(kind, scope)
        if norm:
            asset["scope"] = norm
        self._assets[asset["id"]] = asset
        return copy.deepcopy(asset)

    def personal_calendars(self, owner_id: str) -> list[dict]:
        items = [
            copy.deepcopy(a) for a in self._assets.values()
            if a["owner_id"] == owner_id and a["kind"] == "calendar" and a.get("scope") == "personal"
        ]
        items.sort(key=lambda a: a["updated_at"], reverse=True)
        return items

    def reassign_personal_calendars(self, owner_id: str, from_project_id: str, to_project_id: str) -> int:
        n = 0
        for asset in self._assets.values():
            if (
                asset["owner_id"] == owner_id
                and asset["project_id"] == from_project_id
                and asset["kind"] == "calendar"
                and asset.get("scope") == "personal"
            ):
                asset["project_id"] = to_project_id
                asset["updated_at"] = now()
                n += 1
        return n

    def assets(self, owner_id: str, project_id: str, kind: str | None = None) -> list[dict]:
        if kind == "calendar":
            by_id: dict[str, dict] = {}
            for a in self._assets.values():
                if a["owner_id"] != owner_id or a["kind"] != "calendar":
                    continue
                scope = a.get("scope") or "personal"
                if scope == "personal" or a["project_id"] == project_id:
                    by_id[a["id"]] = copy.deepcopy(a)
            items = list(by_id.values())
            items.sort(key=lambda a: a["updated_at"], reverse=True)
            return items
        items = [copy.deepcopy(a) for a in self._assets.values()
                 if a["owner_id"] == owner_id and a["project_id"] == project_id and (kind is None or a["kind"] == kind)]
        items.sort(key=lambda a: a["updated_at"], reverse=True)
        return items

    def asset(self, owner_id: str, asset_id: str) -> dict | None:
        found = self._assets.get(asset_id)
        if not found or found["owner_id"] != owner_id:
            return None
        return copy.deepcopy(found)

    def update_asset(self, owner_id: str, asset_id: str, **fields: Any) -> dict | None:
        found = self._assets.get(asset_id)
        if not found or found["owner_id"] != owner_id:
            return None
        found.update(fields)
        found["updated_at"] = now()
        return copy.deepcopy(found)

    def delete_asset(self, owner_id: str, asset_id: str) -> dict | None:
        found = self._assets.get(asset_id)
        if not found or found["owner_id"] != owner_id:
            return None
        del self._assets[asset_id]
        return found

    def all_projects(self) -> list[dict]:
        items = [copy.deepcopy(p) for p in self._projects.values()]
        items.sort(key=lambda p: p["updated_at"], reverse=True)
        return items

    def project_any(self, project_id: str) -> dict | None:
        found = self._projects.get(project_id)
        return copy.deepcopy(found) if found else None

    def assets_any(self, project_id: str, kind: str | None = None) -> list[dict]:
        items = [copy.deepcopy(a) for a in self._assets.values()
                 if a["project_id"] == project_id and (kind is None or a["kind"] == kind)]
        items.sort(key=lambda a: a["updated_at"], reverse=True)
        return items

    def asset_any(self, asset_id: str) -> dict | None:
        found = self._assets.get(asset_id)
        return copy.deepcopy(found) if found else None

    def users(self) -> list[dict]:
        return [copy.deepcopy(u) for u in self._users.values()]

    def get_digest_job(self, user_id: str, document_id: str) -> dict | None:
        found = getattr(self, "_digest_jobs", {}).get(f"{user_id}:{document_id}")
        return copy.deepcopy(found) if found else None

    def upsert_digest_job(self, user_id: str, document_id: str, fields: dict) -> dict:
        if not hasattr(self, "_digest_jobs"):
            self._digest_jobs = {}
        key = f"{user_id}:{document_id}"
        current = self._digest_jobs.get(key) or {
            "id": new_id(), "user_id": user_id, "document_id": document_id,
        }
        current.update(fields)
        current["user_id"] = user_id
        current["document_id"] = document_id
        current["updated_at"] = now()
        self._digest_jobs[key] = current
        return copy.deepcopy(current)

    def delete_digest_job(self, user_id: str, document_id: str) -> None:
        if hasattr(self, "_digest_jobs"):
            self._digest_jobs.pop(f"{user_id}:{document_id}", None)

    def list_enabled_digest_jobs(self) -> list[dict]:
        jobs = getattr(self, "_digest_jobs", {})
        return [copy.deepcopy(j) for j in jobs.values() if j.get("enabled")]

    def list_assistant_sessions(self, user_id: str, document_id: str, limit: int = 30) -> list[dict]:
        rows = [
            copy.deepcopy(s) for s in self._assistant_sessions.values()
            if s.get("user_id") == user_id and s.get("document_id") == document_id
        ]
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows[:limit]

    def get_assistant_session(self, user_id: str, session_id: str) -> dict | None:
        found = self._assistant_sessions.get(session_id)
        if not found or found.get("user_id") != user_id:
            return None
        return copy.deepcopy(found)

    def upsert_assistant_session(self, user_id: str, document_id: str, fields: dict) -> dict:
        session_id = (fields.get("id") or "").strip()
        current = self._assistant_sessions.get(session_id) if session_id else None
        if current and current.get("user_id") != user_id:
            raise ValueError("无权修改该会话。")
        if not current:
            current = {
                "id": session_id or new_id(),
                "user_id": user_id,
                "document_id": document_id,
                "created_at": now(),
            }
        current.update(fields)
        current["user_id"] = user_id
        current["document_id"] = document_id
        current["updated_at"] = now()
        self._assistant_sessions[current["id"]] = current
        return copy.deepcopy(current)

    def delete_assistant_session(self, user_id: str, session_id: str) -> bool:
        found = self._assistant_sessions.get(session_id)
        if not found or found.get("user_id") != user_id:
            return False
        del self._assistant_sessions[session_id]
        return True

    def _sheet_cache_key(self, user_id: str, document_id: str, sheet_id: str) -> str:
        return f"{user_id}:{document_id}:{sheet_id}"

    def get_sheet_cache(self, user_id: str, document_id: str, sheet_id: str) -> dict | None:
        found = self._sheet_cache.get(self._sheet_cache_key(user_id, document_id, sheet_id))
        return copy.deepcopy(found) if found else None

    def upsert_sheet_cache(self, user_id: str, document_id: str, fields: dict) -> dict:
        sheet_id = (fields.get("sheet_id") or "").strip()
        key = self._sheet_cache_key(user_id, document_id, sheet_id)
        current = self._sheet_cache.get(key) or {
            "user_id": user_id,
            "document_id": document_id,
            "created_at": now(),
            "profile": "",
            "profile_fingerprint": "",
        }
        current.update(fields)
        current["user_id"] = user_id
        current["document_id"] = document_id
        current["sheet_id"] = sheet_id
        current["updated_at"] = now()
        self._sheet_cache[key] = current
        return copy.deepcopy(current)


class MongoStore(Store):
    def __init__(self, url: str, dbname: str) -> None:
        from pymongo import MongoClient
        self.client = MongoClient(url, serverSelectionTimeoutMS=3000)
        try:
            self.client.admin.command("ping")
        except Exception as exc:
            raise RuntimeError(f"无法连接 MongoDB（{url}）。可改用 OFFICE_MONGO_URL=memory://office 做本地演示。") from exc
        self.db = self.client[dbname]
        self.db.users.create_index("email", unique=True)
        self.db.users.create_index("access_token", unique=True, sparse=True)
        self.db.projects.create_index([("owner_id", 1), ("updated_at", -1)])
        self.db.assets.create_index([("owner_id", 1), ("project_id", 1), ("kind", 1)])
        self.db.digest_jobs.create_index([("user_id", 1), ("document_id", 1)], unique=True)
        self.db.digest_jobs.create_index([("enabled", 1), ("hour", 1), ("minute", 1)])
        self.db.assistant_sessions.create_index([("user_id", 1), ("document_id", 1), ("updated_at", -1)])
        self.db.assistant_sessions.create_index("id", unique=True)
        self.db.sheet_cache.create_index([("user_id", 1), ("document_id", 1), ("sheet_id", 1)], unique=True)

    def _one(self, collection: str, query: dict) -> dict | None:
        doc = self.db[collection].find_one(query)
        return doc

    def create_user(self, email: str, password: str) -> dict:
        user = {
            "id": new_id(), "email": email.lower(), "password_hash": hash_password(password),
            "access_token": self._unique_access_token(),
            "last_project_id": None, "last_assets": empty_last_assets(),
            "created_at": now(),
        }
        self.db.users.insert_one(user)
        return user

    def _unique_access_token(self) -> str:
        for _ in range(8):
            token = new_access_token()
            if not self.user_by_access_token(token):
                return token
        raise RuntimeError("无法生成访问令牌。")

    def user_by_email(self, email: str) -> dict | None:
        return self._one("users", {"email": email.lower()})

    def user_by_id(self, user_id: str) -> dict | None:
        return self._one("users", {"id": user_id})

    def user_by_access_token(self, token: str) -> dict | None:
        token = (token or "").strip()
        if len(token) != ACCESS_TOKEN_LENGTH:
            return None
        return self._one("users", {"access_token": token})

    def ensure_access_token(self, user: dict) -> dict:
        token = (user.get("access_token") or "").strip()
        if len(token) == ACCESS_TOKEN_LENGTH:
            return user
        return self.rotate_access_token(user["id"]) or user

    def rotate_access_token(self, user_id: str) -> dict | None:
        return self.update_user(user_id, access_token=self._unique_access_token())

    def update_user(self, user_id: str, **fields: Any) -> dict | None:
        self.db.users.update_one({"id": user_id}, {"$set": fields})
        return self.user_by_id(user_id)

    def create_project(self, owner_id: str, name: str) -> dict:
        project = {"id": new_id(), "owner_id": owner_id, "name": name, "created_at": now(), "updated_at": now()}
        self.db.projects.insert_one(project)
        return project

    def projects(self, owner_id: str) -> list[dict]:
        return list(self.db.projects.find({"owner_id": owner_id}, {"_id": 0}).sort("updated_at", -1))

    def project(self, owner_id: str, project_id: str) -> dict | None:
        return self._one("projects", {"id": project_id, "owner_id": owner_id})

    def update_project(self, owner_id: str, project_id: str, **fields: Any) -> dict | None:
        fields["updated_at"] = now()
        self.db.projects.update_one({"id": project_id, "owner_id": owner_id}, {"$set": fields})
        return self.project(owner_id, project_id)

    def delete_project(self, owner_id: str, project_id: str) -> list[dict] | None:
        if not self.project(owner_id, project_id):
            return None
        removed = list(self.db.assets.find({"owner_id": owner_id, "project_id": project_id}, {"_id": 0}))
        self.db.assets.delete_many({"owner_id": owner_id, "project_id": project_id})
        self.db.projects.delete_one({"id": project_id, "owner_id": owner_id})
        return removed

    def create_asset(self, owner_id: str, project_id: str, kind: str, name: str, content: Any = None, scope: str | None = None) -> dict:
        asset = {
            "id": new_id(), "owner_id": owner_id, "project_id": project_id, "kind": kind, "name": name,
            "revision": 0, "content": content, "file_path": None, "filename": None, "size": None,
            "updated_at": now(),
        }
        norm = normalize_scope(kind, scope)
        if norm:
            asset["scope"] = norm
        self.db.assets.insert_one(asset)
        return asset

    def personal_calendars(self, owner_id: str) -> list[dict]:
        return list(self.db.assets.find(
            {"owner_id": owner_id, "kind": "calendar", "scope": "personal"},
            {"_id": 0, "content": 0, "file_path": 0},
        ).sort("updated_at", -1))

    def reassign_personal_calendars(self, owner_id: str, from_project_id: str, to_project_id: str) -> int:
        result = self.db.assets.update_many(
            {
                "owner_id": owner_id,
                "project_id": from_project_id,
                "kind": "calendar",
                "scope": "personal",
            },
            {"$set": {"project_id": to_project_id, "updated_at": now()}},
        )
        return int(result.modified_count)

    def assets(self, owner_id: str, project_id: str, kind: str | None = None) -> list[dict]:
        projection = {"_id": 0, "content": 0, "file_path": 0}
        if kind == "calendar":
            rows = list(self.db.assets.find(
                {
                    "owner_id": owner_id,
                    "kind": "calendar",
                    "$or": [
                        {"scope": "personal"},
                        {"project_id": project_id, "scope": "project"},
                        {"project_id": project_id, "scope": {"$exists": False}},
                    ],
                },
                projection,
            ).sort("updated_at", -1))
            # 去重（理论上不会重复）
            seen: set[str] = set()
            out = []
            for row in rows:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                out.append(row)
            return out
        query: dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
        if kind:
            query["kind"] = kind
        return list(self.db.assets.find(query, projection).sort("updated_at", -1))

    def asset(self, owner_id: str, asset_id: str) -> dict | None:
        return self._one("assets", {"id": asset_id, "owner_id": owner_id})

    def update_asset(self, owner_id: str, asset_id: str, **fields: Any) -> dict | None:
        fields["updated_at"] = now()
        self.db.assets.update_one({"id": asset_id, "owner_id": owner_id}, {"$set": fields})
        return self.asset(owner_id, asset_id)

    def delete_asset(self, owner_id: str, asset_id: str) -> dict | None:
        found = self.asset(owner_id, asset_id)
        if not found:
            return None
        self.db.assets.delete_one({"id": asset_id, "owner_id": owner_id})
        return found

    def all_projects(self) -> list[dict]:
        return list(self.db.projects.find({}, {"_id": 0}).sort("updated_at", -1))

    def project_any(self, project_id: str) -> dict | None:
        return self._one("projects", {"id": project_id})

    def assets_any(self, project_id: str, kind: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"project_id": project_id}
        if kind:
            query["kind"] = kind
        return list(self.db.assets.find(query, {"_id": 0, "content": 0, "file_path": 0}).sort("updated_at", -1))

    def asset_any(self, asset_id: str) -> dict | None:
        return self._one("assets", {"id": asset_id})

    def users(self) -> list[dict]:
        return list(self.db.users.find({}, {"_id": 0, "password_hash": 0, "access_token": 0}))

    def get_digest_job(self, user_id: str, document_id: str) -> dict | None:
        return self._one("digest_jobs", {"user_id": user_id, "document_id": document_id})

    def upsert_digest_job(self, user_id: str, document_id: str, fields: dict) -> dict:
        payload = {**fields, "user_id": user_id, "document_id": document_id, "updated_at": now()}
        existing = self.get_digest_job(user_id, document_id)
        if existing:
            self.db.digest_jobs.update_one(
                {"user_id": user_id, "document_id": document_id},
                {"$set": payload},
            )
        else:
            payload["id"] = new_id()
            self.db.digest_jobs.insert_one(payload)
        return self.get_digest_job(user_id, document_id) or payload

    def delete_digest_job(self, user_id: str, document_id: str) -> None:
        self.db.digest_jobs.delete_one({"user_id": user_id, "document_id": document_id})

    def list_enabled_digest_jobs(self) -> list[dict]:
        return list(self.db.digest_jobs.find({"enabled": True}, {"_id": 0}))

    def list_assistant_sessions(self, user_id: str, document_id: str, limit: int = 30) -> list[dict]:
        return list(self.db.assistant_sessions.find(
            {"user_id": user_id, "document_id": document_id},
            {"_id": 0},
        ).sort("updated_at", -1).limit(limit))

    def get_assistant_session(self, user_id: str, session_id: str) -> dict | None:
        return self._one("assistant_sessions", {"id": session_id, "user_id": user_id})

    def upsert_assistant_session(self, user_id: str, document_id: str, fields: dict) -> dict:
        session_id = (fields.get("id") or "").strip()
        existing = self.get_assistant_session(user_id, session_id) if session_id else None
        payload = {**fields, "user_id": user_id, "document_id": document_id, "updated_at": now()}
        if existing:
            self.db.assistant_sessions.update_one({"id": session_id, "user_id": user_id}, {"$set": payload})
            return self.get_assistant_session(user_id, session_id) or payload
        payload["id"] = session_id or new_id()
        payload.setdefault("created_at", now())
        self.db.assistant_sessions.insert_one(payload)
        return self.get_assistant_session(user_id, payload["id"]) or payload

    def delete_assistant_session(self, user_id: str, session_id: str) -> bool:
        result = self.db.assistant_sessions.delete_one({"id": session_id, "user_id": user_id})
        return result.deleted_count > 0

    def get_sheet_cache(self, user_id: str, document_id: str, sheet_id: str) -> dict | None:
        return self.db.sheet_cache.find_one(
            {"user_id": user_id, "document_id": document_id, "sheet_id": sheet_id},
            {"_id": 0},
        )

    def upsert_sheet_cache(self, user_id: str, document_id: str, fields: dict) -> dict:
        sheet_id = (fields.get("sheet_id") or "").strip()
        query = {"user_id": user_id, "document_id": document_id, "sheet_id": sheet_id}
        existing = self.get_sheet_cache(user_id, document_id, sheet_id)
        payload = {**fields, "user_id": user_id, "document_id": document_id, "sheet_id": sheet_id, "updated_at": now()}
        if existing:
            self.db.sheet_cache.update_one(query, {"$set": payload})
        else:
            payload.setdefault("created_at", now())
            payload.setdefault("profile", "")
            payload.setdefault("profile_fingerprint", "")
            self.db.sheet_cache.insert_one(payload)
        return self.get_sheet_cache(user_id, document_id, sheet_id) or payload


def create_store(url: str | None = None) -> Store:
    url = url if url is not None else mongo_url()
    if url.startswith("memory:"):
        return MemoryStore()
    return MongoStore(url, mongo_db())
