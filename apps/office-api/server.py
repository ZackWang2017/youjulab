"""办公工作区 HTTP 服务：登录、项目、表格/文档/PDF/思维导图/日历资产。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from contextlib import asynccontextmanager
import asyncio

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from auth import create_token, read_token  # noqa: E402
from assistant import clip_turn_content, iter_assistant_events, sse_pack  # noqa: E402
from office_patch import validate_office_patch  # noqa: E402
from sheet_cache import (  # noqa: E402
    apply_cache_patch,
    bind_sheet_context,
    normalize_cache_body,
    public_sheet_cache,
    structure_fingerprint,
)
from sheet_profile import generate_sheet_profile  # noqa: E402
from assistant_sessions import public_session, public_session_list_item, session_title, validate_session_turns  # noqa: E402
from pdf_ask import iter_pdf_ask_events  # noqa: E402
from pdf_glossary import delete_project_term, list_project_terms, upsert_project_term  # noqa: E402
from pdf_translate import iter_pdf_translate_events  # noqa: E402
from config import api_host, api_port, file_root, model_api_key, model_configured, model_name  # noqa: E402
from pdf_annot import add_pdf_annotation, delete_pdf_annotation, update_pdf_annotation  # noqa: E402
from office_pdf_vector import delete_document_index, ensure_index, get_index_status, pg_configured  # noqa: E402
from digest_job import default_job, public_job, run_digest_job, validate_job_body  # noqa: E402
from digest_scheduler import digest_scheduler_loop  # noqa: E402
from remote_data import call_remote  # noqa: E402
from skills import list_skills, render_skill  # noqa: E402
from store import (  # noqa: E402
    ACCESS_TOKEN_LENGTH,
    DEFAULT_CALENDAR_NAME,
    DEFAULT_DOCUMENT_NAME,
    DEFAULT_MINDMAP_NAME,
    DEFAULT_SHEET_NAME,
    KINDS,
    Store,
    create_store,
    default_calendar,
    default_document,
    default_mindmap,
    default_sheet,
    empty_last_assets,
    normalize_scope,
    public_asset,
    public_project,
    public_user,
    verify_password,
)

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

_INDEX_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-index")

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PDF = 30 * 1024 * 1024


def validation_message(exc: RequestValidationError) -> str:
    labels = {
        "email": "邮箱", "password": "密码", "name": "名称", "kind": "类型", "content": "内容",
        "revision": "版本", "url": "地址", "method": "请求方法", "function": "函数名",
        "headers": "请求头", "values": "选区",
    }
    seen: list[str] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        field = next((labels[part] for part in reversed(loc) if part in labels), "")
        kind = err.get("type", "")
        if kind == "missing":
            text = f"请填写{field}。" if field else "请填写完整信息。"
        elif kind == "string_too_short" and field == "密码":
            text = "密码至少 6 位。"
        elif kind == "string_too_short":
            text = f"{field}过短。" if field else "内容过短。"
        elif kind == "string_too_long":
            text = f"{field}过长。" if field else "内容过长。"
        elif kind == "model_attributes_type":
            text = "请求格式不正确。"
        else:
            text = err.get("msg") or "请求无效。"
        if text not in seen:
            seen.append(text)
    return " ".join(seen) or "请求无效。"


class AuthBody(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=128)


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CreateAssetBody(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["personal", "project"] | None = None


class SaveAssetBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    content: dict | list | None = None
    revision: int | None = Field(default=None, ge=0)


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)

    @field_validator("content", mode="before")
    @classmethod
    def clip_history_content(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return clip_turn_content(value)


class AssistantSessionTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)

    @field_validator("content", mode="before")
    @classmethod
    def clip_session_content(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return clip_turn_content(value)


class RunBody(BaseModel):
    context: dict
    intent: str = Field(min_length=1, max_length=4000)
    task: Literal["explain", "generate"] = "explain"
    history: list[HistoryTurn] = Field(default_factory=list, max_length=40)
    summary: str = Field(default="", max_length=8000)
    use_sheet_cache: bool = False


class SheetCachePutBody(BaseModel):
    sheet_id: str = Field(min_length=1, max_length=200)
    sheet_name: str = ""
    revision: int = Field(default=0, ge=0)
    origin: dict = Field(default_factory=dict)
    rows: int = 0
    cols: int = 0
    range: str = ""
    merges: list = Field(default_factory=list)
    note: str = ""
    truncated: bool = False
    values: list
    headers: list | None = None


class SheetCachePatchBody(BaseModel):
    sheet_id: str = Field(min_length=1, max_length=200)
    revision: int | None = Field(default=None, ge=0)
    cells: list[dict] = Field(default_factory=list)
    merges: list | None = None
    rows: int | None = None
    cols: int | None = None
    range: str | None = None


class SheetCacheProfileBody(BaseModel):
    sheet_id: str = Field(min_length=1, max_length=200)


class AssistantSessionBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    anchor: dict | None = None
    turns: list[AssistantSessionTurn] | None = None
    summary: str = Field(default="", max_length=8000)


class ValidateBody(BaseModel):
    context: dict
    patch: dict


class RemoteDataBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    method: str = "POST"
    function: str = Field(default="", max_length=120)
    headers: dict | str | None = None
    values: list | None = None


class PdfAnnotBody(BaseModel):
    kind: Literal["highlight", "text"]
    page: int = Field(ge=1, le=5000)
    # 与 pdf_annot.MAX_BOXES 对齐；超长选区由服务端 coalesce
    boxes: list[list[float]] = Field(min_length=1, max_length=800)
    content: str = Field(default="", max_length=200_000)


class PdfAnnotUpdateBody(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


class PdfAskTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)


class PdfAskBody(BaseModel):
    selection: str = Field(min_length=1, max_length=8000)
    question: str = Field(default="", max_length=2000)
    page: int = Field(ge=1, le=5000)
    document_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(default="", max_length=240)
    history: list[PdfAskTurn] = Field(default_factory=list, max_length=20)
    translate: bool = False


class ProjectTermBody(BaseModel):
    en: str = Field(min_length=1, max_length=120)
    zh: str = Field(min_length=1, max_length=120)
    forbidden: list[str] = Field(default_factory=list, max_length=12)


def schedule_pdf_index(document_id: str, user_id: str, path: str) -> None:
    if not pg_configured():
        return

    def run() -> None:
        try:
            status = ensure_index(document_id=document_id, user_id=user_id, path=path)
            # best-effort：把状态回写到资产元数据，便于 UI
            try:
                store = create_store()
                store.update_asset(user_id, document_id, index_status=status.get("status"))
            except Exception:
                pass
        except Exception:
            try:
                store = create_store()
                store.update_asset(user_id, document_id, index_status="failed")
            except Exception:
                pass

    _INDEX_POOL.submit(run)


def get_store(request: Request) -> Store:
    return request.app.state.store


def user_from(request: Request, store: Store = Depends(get_store)) -> dict:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "请先登录。")
    token = header.split(" ", 1)[1].strip()
    if len(token) == ACCESS_TOKEN_LENGTH:
        user = store.user_by_access_token(token)
        if not user:
            raise HTTPException(401, "访问令牌无效。")
        return workspace_account(user)
    user = store.user_by_id(read_token(token))
    if not user:
        raise HTTPException(401, "请先登录。")
    return workspace_account(user)


def workspace_account(user: dict) -> dict:
    """令牌只用来找到账号。项目/表格挂在用户 id 上，对应登录邮箱，不挂在可轮换的 token 上。"""
    user_id = (user.get("id") or "").strip()
    email = (user.get("email") or "").strip().lower()
    if not user_id or not email:
        raise HTTPException(401, "令牌无法对应到登录邮箱。")
    return user


def workspace_user_id(user: dict) -> str:
    return workspace_account(user)["id"]


def owner_id(user: dict, project: dict | None = None) -> str:
    if project:
        return project["owner_id"]
    return workspace_user_id(user)


def projects_of(store: Store, user: dict) -> list[dict]:
    return store.projects(workspace_user_id(user))


def project_of(store: Store, user: dict, project_id: str) -> dict | None:
    return store.project(workspace_user_id(user), project_id)


def assets_of(store: Store, user: dict, project_id: str, kind: str | None = None) -> list[dict]:
    return store.assets(workspace_user_id(user), project_id, kind)


def asset_of(store: Store, user: dict, asset_id: str) -> dict | None:
    return store.asset(workspace_user_id(user), asset_id)


def seed_project(store: Store, user: dict, project: dict) -> dict:
    oid = workspace_user_id(user)
    sheet = store.create_asset(oid, project["id"], "sheet", DEFAULT_SHEET_NAME, default_sheet())
    document = store.create_asset(oid, project["id"], "document", DEFAULT_DOCUMENT_NAME, default_document())
    mindmap = store.create_asset(oid, project["id"], "mindmap", DEFAULT_MINDMAP_NAME, default_mindmap())
    calendar_id = None
    if not store.personal_calendars(oid):
        calendar = store.create_asset(
            oid, project["id"], "calendar", DEFAULT_CALENDAR_NAME, default_calendar(), scope="personal",
        )
        calendar_id = calendar["id"]
    else:
        # 已有日常工作日历：沿用最近一份
        calendar_id = store.personal_calendars(oid)[0]["id"]
    return store.update_user(oid, last_project_id=project["id"],
                             last_assets={
                                 "sheet": sheet["id"],
                                 "document": document["id"],
                                 "pdf": None,
                                 "mindmap": mindmap["id"],
                                 "calendar": calendar_id,
                             }) or user


def _pick_last(assets: list[dict], last_id: str | None) -> str | None:
    if last_id and any(a["id"] == last_id for a in assets):
        return last_id
    return assets[0]["id"] if assets else None


def refresh_last_assets(store: Store, user_id: str, project_id: str, last: dict | None = None) -> dict:
    sheets = store.assets(user_id, project_id, "sheet")
    docs = store.assets(user_id, project_id, "document")
    pdfs = store.assets(user_id, project_id, "pdf")
    maps = store.assets(user_id, project_id, "mindmap")
    cals = store.assets(user_id, project_id, "calendar")
    prev = last or {}
    return {
        "sheet": _pick_last(sheets, prev.get("sheet")),
        "document": _pick_last(docs, prev.get("document")),
        "pdf": _pick_last(pdfs, prev.get("pdf")),
        "mindmap": _pick_last(maps, prev.get("mindmap")),
        "calendar": _pick_last(cals, prev.get("calendar")),
    }


def ensure_workspace(store: Store, user: dict) -> dict:
    oid = workspace_user_id(user)
    projects = store.projects(oid)
    if projects:
        return user
    project = store.create_project(oid, "未命名项目")
    return seed_project(store, user, project)


class DigestSourceBody(BaseModel):
    kind: Literal["rss", "url", "rsshub"] = "rss"
    url: str = Field(default="", max_length=2000)
    route: str = Field(default="", max_length=500)
    label: str = Field(default="", max_length=120)


class DigestTopicBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sources: list[DigestSourceBody] = Field(default_factory=list, max_length=20)


class DigestJobBody(BaseModel):
    enabled: bool = False
    hour: int = Field(default=8, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    output_order: Literal["newest_first", "newest_last"] = "newest_first"
    topics: list[DigestTopicBody] = Field(default_factory=list, max_length=12)


def create_app(store: Store | None = None) -> FastAPI:
    app_store = store or create_store()
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = app_store
        task = asyncio.create_task(digest_scheduler_loop(app_store, stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="office-api", lifespan=lifespan)
    app.state.store = app_store

    @app.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc: RequestValidationError):
        return JSONResponse({"error": validation_message(exc)}, status_code=422)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/auth/register")
    def register(body: AuthBody, store: Store = Depends(get_store)):
        email = body.email.strip()
        if not EMAIL.match(email):
            raise HTTPException(400, "邮箱格式不正确。")
        if store.user_by_email(email):
            raise HTTPException(409, "该邮箱已注册。")
        user = store.create_user(email, body.password)
        user = ensure_workspace(store, user)
        return {"token": create_token(user["id"]), "user": public_user(user)}

    @app.post("/auth/login")
    def login(body: AuthBody, store: Store = Depends(get_store)):
        user = store.user_by_email(body.email.strip())
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "邮箱或密码不正确。")
        user = store.ensure_access_token(user)
        user = ensure_workspace(store, user)
        return {"token": create_token(user["id"]), "user": public_user(user)}

    @app.get("/auth/me")
    def me(user: dict = Depends(user_from), store: Store = Depends(get_store)):
        store.ensure_access_token(user)
        return public_user(ensure_workspace(store, user))

    @app.get("/auth/access-token")
    def get_access_token(user: dict = Depends(user_from), store: Store = Depends(get_store)):
        user = store.ensure_access_token(user)
        return {"access_token": user["access_token"]}

    @app.post("/auth/access-token/rotate")
    def rotate_access_token(user: dict = Depends(user_from), store: Store = Depends(get_store)):
        user = store.rotate_access_token(user["id"])
        if not user:
            raise HTTPException(401, "请先登录。")
        return {"access_token": user["access_token"]}

    @app.get("/auth/skills")
    def skill_catalog(_user: dict = Depends(user_from)):
        return {"skills": list_skills()}

    @app.get("/auth/skills/{skill_id}")
    def download_skill(skill_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        user = store.ensure_access_token(user)
        try:
            text, filename = render_skill(skill_id, user["access_token"])
        except KeyError:
            raise HTTPException(404, "没有这个 skill。") from None
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/projects")
    def list_projects(user: dict = Depends(user_from), store: Store = Depends(get_store)):
        return {"projects": [public_project(p) for p in projects_of(store, user)]}

    @app.post("/projects")
    def add_project(body: NameBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        oid = owner_id(user)
        owner = store.user_by_id(oid) or user
        project = store.create_project(oid, body.name.strip())
        seed_project(store, owner, project)
        return public_project(project)

    @app.patch("/projects/{project_id}")
    def rename_project(project_id: str, body: NameBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        project = store.update_project(user["id"], project_id, name=body.name.strip())
        if not project:
            raise HTTPException(404, "项目不存在。")
        return public_project(project)

    @app.delete("/projects/{project_id}")
    def remove_project(project_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        projects = store.projects(user["id"])
        if len(projects) <= 1:
            raise HTTPException(400, "至少保留一个项目。")
        nxt = next((p for p in projects if p["id"] != project_id), None)
        if not nxt:
            raise HTTPException(400, "至少保留一个项目。")
        # 日常工作日历历迁到其他项目，避免随项目删除
        store.reassign_personal_calendars(user["id"], project_id, nxt["id"])
        removed = store.delete_project(user["id"], project_id)
        if removed is None:
            raise HTTPException(404, "项目不存在。")
        root = file_root()
        for asset in removed:
            if asset.get("kind") == "pdf":
                try:
                    delete_document_index(asset["id"])
                except Exception:
                    pass
            if asset.get("file_path"):
                path = Path(asset["file_path"])
                if path.is_file() and str(path).startswith(str(root)):
                    path.unlink(missing_ok=True)
        current = store.user_by_id(user["id"])
        if current and current.get("last_project_id") == project_id:
            store.update_user(
                user["id"],
                last_project_id=nxt["id"],
                last_assets=refresh_last_assets(store, user["id"], nxt["id"], current.get("last_assets")),
            )
        return {"ok": True}

    @app.post("/projects/{project_id}/select")
    def select_project(project_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        project = store.project(user["id"], project_id)
        if not project:
            raise HTTPException(404, "项目不存在。")
        maps = store.assets(user["id"], project_id, "mindmap")
        if not maps:
            store.create_asset(user["id"], project_id, "mindmap", DEFAULT_MINDMAP_NAME, default_mindmap())
        cals = store.assets(user["id"], project_id, "calendar")
        if not cals:
            store.create_asset(
                user["id"], project_id, "calendar", DEFAULT_CALENDAR_NAME, default_calendar(), scope="personal",
            )
        last = store.user_by_id(user["id"])["last_assets"]
        store.update_user(
            user["id"],
            last_project_id=project_id,
            last_assets=refresh_last_assets(store, user["id"], project_id, last),
        )
        return public_user(store.user_by_id(user["id"]))

    @app.get("/projects/{project_id}/terms")
    def get_project_terms(project_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not store.project(user["id"], project_id):
            raise HTTPException(404, "项目不存在。")
        try:
            return {"terms": list_project_terms(project_id)}
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/projects/{project_id}/terms")
    def post_project_term(project_id: str, body: ProjectTermBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not store.project(user["id"], project_id):
            raise HTTPException(404, "项目不存在。")
        try:
            return upsert_project_term(project_id, body.en, body.zh, body.forbidden)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/projects/{project_id}/terms")
    def remove_project_term(project_id: str, en: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not store.project(user["id"], project_id):
            raise HTTPException(404, "项目不存在。")
        try:
            delete_project_term(project_id, en)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True}

    @app.get("/projects/{project_id}/assets")
    def list_assets(project_id: str, kind: str | None = None, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not project_of(store, user, project_id):
            raise HTTPException(404, "项目不存在。")
        if kind and kind not in KINDS:
            raise HTTPException(400, "资产类型无效。")
        return {"assets": [public_asset(a) for a in assets_of(store, user, project_id, kind)]}

    @app.post("/projects/{project_id}/assets")
    def add_asset(project_id: str, body: CreateAssetBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        project = project_of(store, user, project_id)
        if not project:
            raise HTTPException(404, "项目不存在。")
        if body.kind not in KINDS:
            raise HTTPException(400, "资产类型无效。")
        scope = None
        if body.kind == "sheet":
            content = {"name": body.name.strip()}
        elif body.kind == "document":
            content = {"type": "doc", "content": [{"type": "paragraph"}]}
        elif body.kind == "mindmap":
            content = default_mindmap(body.name.strip())
        elif body.kind == "calendar":
            content = default_calendar()
            scope = normalize_scope("calendar", body.scope)
        else:
            content = None
        oid = owner_id(user, project)
        asset = store.create_asset(oid, project_id, body.kind, body.name.strip(), content, scope=scope)
        owner = store.user_by_id(oid)
        if owner:
            last = {**empty_last_assets(), **dict(owner.get("last_assets") or {})}
            last[body.kind] = asset["id"]
            store.update_user(oid, last_project_id=project_id, last_assets=last)
        return public_asset(asset, include_content=True)

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset:
            raise HTTPException(404, "文件不存在。")
        return public_asset(asset, include_content=True)

    @app.put("/assets/{asset_id}")
    def save_asset(asset_id: str, body: SaveAssetBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset:
            raise HTTPException(404, "文件不存在。")
        if body.revision is not None and body.revision != asset.get("revision", 0):
            raise HTTPException(409, "内容已在其他会话更新，请重新打开。")
        fields: dict = {"revision": asset.get("revision", 0) + 1}
        if body.name is not None:
            fields["name"] = body.name.strip()
        if body.content is not None and asset["kind"] != "pdf":
            fields["content"] = body.content
        updated = store.update_asset(asset["owner_id"], asset_id, **fields)
        return public_asset(updated, include_content=True)

    @app.delete("/assets/{asset_id}")
    def remove_asset(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = store.asset(user["id"], asset_id)
        if not asset:
            raise HTTPException(404, "文件不存在。")
        same = store.assets(user["id"], asset["project_id"], asset["kind"])
        if asset["kind"] != "pdf" and len(same) <= 1:
            raise HTTPException(400, "该类型至少保留一份。")
        store.delete_asset(user["id"], asset_id)
        if asset["kind"] == "document":
            try:
                store.delete_digest_job(owner_id(user), asset_id)
            except Exception:
                pass
        if asset["kind"] == "pdf":
            try:
                delete_document_index(asset_id)
            except Exception:
                pass
        if asset.get("file_path"):
            path = Path(asset["file_path"])
            if path.is_file() and str(path).startswith(str(file_root())):
                path.unlink(missing_ok=True)
        user_doc = store.user_by_id(user["id"])
        last = dict(user_doc["last_assets"])
        if last.get(asset["kind"]) == asset_id:
            remain = [a for a in same if a["id"] != asset_id]
            last[asset["kind"]] = remain[0]["id"] if remain else None
            store.update_user(user["id"], last_assets=last)
        return {"ok": True}

    @app.post("/assets/{asset_id}/select")
    def select_asset(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = store.asset(user["id"], asset_id)
        if not asset:
            raise HTTPException(404, "文件不存在。")
        last = dict(store.user_by_id(user["id"])["last_assets"])
        last[asset["kind"]] = asset["id"]
        store.update_user(user["id"], last_project_id=asset["project_id"], last_assets=last)
        return public_user(store.user_by_id(user["id"]))

    @app.put("/assets/{asset_id}/file")
    async def upload_pdf(asset_id: str, file: UploadFile = File(...), user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = store.asset(user["id"], asset_id)
        if not asset:
            raise HTTPException(404, "文件不存在。")
        if asset["kind"] != "pdf":
            raise HTTPException(400, "只有 PDF 可以上传文件。")
        data = await file.read()
        if len(data) > MAX_PDF:
            raise HTTPException(400, "MVP 支持 30 MB 以内的 PDF。")
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "请上传 PDF 文件。")
        dest = file_root() / user["id"] / asset["project_id"]
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{asset_id}.pdf"
        path.write_bytes(data)
        name = file.filename or asset["name"]
        if name.lower().endswith(".pdf"):
            name = name[:-4] or asset["name"]
        updated = store.update_asset(user["id"], asset_id, file_path=str(path), filename=file.filename or f"{name}.pdf",
                                     size=len(data), name=name, revision=asset.get("revision", 0) + 1,
                                     index_status="pending")
        last = dict(store.user_by_id(user["id"])["last_assets"])
        last["pdf"] = asset_id
        store.update_user(user["id"], last_assets=last)
        schedule_pdf_index(asset_id, user["id"], str(path))
        return public_asset(updated)

    def pdf_file(asset_id: str, user: dict, store: Store):
        asset = store.asset(user["id"], asset_id)
        if not asset or asset["kind"] != "pdf" or not asset.get("file_path"):
            raise HTTPException(404, "尚未上传 PDF。")
        path = Path(asset["file_path"])
        if not path.is_file():
            raise HTTPException(404, "尚未上传 PDF。")
        return asset, path

    def save_pdf_file(user: dict, asset: dict, path: Path, store: Store):
        size = path.stat().st_size
        if size > MAX_PDF:
            raise HTTPException(400, "MVP 支持 30 MB 以内的 PDF。")
        return public_asset(store.update_asset(user["id"], asset["id"], size=size, revision=asset.get("revision", 0) + 1))

    @app.post("/assets/{asset_id}/annotations")
    def annotate_pdf(asset_id: str, body: PdfAnnotBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset, path = pdf_file(asset_id, user, store)
        try:
            created = add_pdf_annotation(path, body.kind, body.page, body.boxes, body.content)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        saved = save_pdf_file(user, asset, path, store)
        annot = { "xref": created["xref"], "kind": body.kind, "page": body.page }
        if created.get("mark_xref"):
            annot["mark_xref"] = created["mark_xref"]
        return { **saved, "annot": annot }

    @app.patch("/assets/{asset_id}/annotations/{xref}")
    def update_pdf_annot(asset_id: str, xref: int, body: PdfAnnotUpdateBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset, path = pdf_file(asset_id, user, store)
        try:
            update_pdf_annotation(path, xref, body.content)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return save_pdf_file(user, asset, path, store)

    @app.delete("/assets/{asset_id}/annotations/{xref}")
    def delete_pdf_annot(asset_id: str, xref: int, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset, path = pdf_file(asset_id, user, store)
        try:
            deleted = delete_pdf_annotation(path, xref)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        saved = save_pdf_file(user, asset, path, store)
        return { **saved, "deleted": deleted }

    @app.get("/assets/{asset_id}/file")
    def download_pdf(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = store.asset(user["id"], asset_id)
        if not asset or asset["kind"] != "pdf" or not asset.get("file_path"):
            raise HTTPException(404, "尚未上传 PDF。")
        path = Path(asset["file_path"])
        if not path.is_file():
            raise HTTPException(404, "尚未上传 PDF。")
        return FileResponse(path, media_type="application/pdf", filename=asset.get("filename") or f"{asset['name']}.pdf",
                            headers={"Cache-Control": "no-store"})

    @app.get("/assistant/status")
    def assistant_status(_user: dict = Depends(user_from)):
        name = model_name()
        return {"ok": True, "configured": model_configured(), "has_api_key": bool(model_api_key()), "model_name": name or "未配置"}

    def _sheet_asset(asset_id: str, user: dict, store: Store) -> tuple[dict, str]:
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        return asset, owner_id(user)

    @app.get("/assets/{asset_id}/sheet-cache")
    def get_sheet_cache(asset_id: str, sheet_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        _, oid = _sheet_asset(asset_id, user, store)
        found = store.get_sheet_cache(oid, asset_id, sheet_id)
        if not found:
            raise HTTPException(404, "尚未缓存表结构。")
        return public_sheet_cache(found)

    @app.put("/assets/{asset_id}/sheet-cache")
    def put_sheet_cache(asset_id: str, body: SheetCachePutBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        _, oid = _sheet_asset(asset_id, user, store)
        try:
            fields = normalize_cache_body(body.model_dump(), revision=body.revision)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        existing = store.get_sheet_cache(oid, asset_id, fields["sheet_id"])
        fingerprint = structure_fingerprint(fields)
        if existing and existing.get("profile_fingerprint") == fingerprint and (existing.get("profile") or "").strip():
            fields["profile"] = existing.get("profile") or ""
            fields["profile_fingerprint"] = fingerprint
        else:
            fields["profile"] = ""
            fields["profile_fingerprint"] = ""
        saved = store.upsert_sheet_cache(oid, asset_id, fields)
        return public_sheet_cache(saved)

    @app.patch("/assets/{asset_id}/sheet-cache")
    def patch_sheet_cache(asset_id: str, body: SheetCachePatchBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        _, oid = _sheet_asset(asset_id, user, store)
        existing = store.get_sheet_cache(oid, asset_id, body.sheet_id)
        if not existing:
            raise HTTPException(404, "尚未缓存表结构。")
        saved = store.upsert_sheet_cache(oid, asset_id, apply_cache_patch(existing, body.model_dump()))
        return public_sheet_cache(saved)

    @app.post("/assets/{asset_id}/sheet-cache/profile")
    async def post_sheet_profile(asset_id: str, body: SheetCacheProfileBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        _, oid = _sheet_asset(asset_id, user, store)
        if not model_configured():
            raise HTTPException(503, "请先在后台配置模型。")
        cache = store.get_sheet_cache(oid, asset_id, body.sheet_id)
        if not cache:
            raise HTTPException(404, "尚未缓存表结构。")
        try:
            profile, fingerprint = await generate_sheet_profile(cache)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        saved = store.upsert_sheet_cache(oid, asset_id, {
            **cache,
            "profile": profile,
            "profile_fingerprint": fingerprint,
        })
        return public_sheet_cache(saved)

    @app.post("/assistant/run")
    async def assistant_run(body: RunBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not model_configured():
            raise HTTPException(503, "请先在后台配置模型。")

        async def events():
            try:
                context = body.context
                if body.use_sheet_cache and context.get("surface") == "sheet":
                    sheet_id = str((context.get("selection") or {}).get("sheet_id") or "")
                    cache = store.get_sheet_cache(owner_id(user), str(context.get("document_id") or ""), sheet_id)
                    context = bind_sheet_context(context, cache, body.intent)
                history = [item.model_dump() for item in body.history]
                async for event in iter_assistant_events(
                    context, body.intent, body.task, history, summary=body.summary,
                ):
                    yield sse_pack(event)
            except ValidationError:
                yield sse_pack({"type": "error", "error": "选区无效。"})
            except ValueError as exc:
                yield sse_pack({"type": "error", "error": str(exc)})
            except Exception:
                yield sse_pack({"type": "error", "error": "模型请求失败，请检查接口配置与服务日志。"})

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.post("/assistant/pdf-ask")
    async def assistant_pdf_ask(body: PdfAskBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        if not model_configured():
            raise HTTPException(503, "请先在后台配置模型。")
        if body.document_id.strip() == "":
            raise HTTPException(400, "缺少 document_id。")
        asset, path = pdf_file(body.document_id, user, store)
        if not body.translate and not body.question.strip():
            raise HTTPException(400, "请填写问题。")

        async def events():
            try:
                if body.translate:
                    async for event in iter_pdf_translate_events(
                        selection=body.selection,
                        page=body.page,
                        document_id=asset["id"],
                        project_id=str(asset.get("project_id") or ""),
                        note=body.question,
                    ):
                        yield sse_pack(event)
                    return
                async for event in iter_pdf_ask_events(
                    selection=body.selection,
                    question=body.question,
                    page=body.page,
                    document_id=asset["id"],
                    user_id=user["id"],
                    path=str(path),
                    filename=body.filename or asset.get("filename") or asset.get("name") or "",
                    history=[item.model_dump() for item in body.history],
                ):
                    yield sse_pack(event)
            except ValueError as exc:
                yield sse_pack({"type": "error", "error": str(exc)})
            except Exception:
                yield sse_pack({"type": "error", "error": "模型请求失败，请检查接口配置与服务日志。"})

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/assets/{asset_id}/digest-job")
    def get_digest_job(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "document":
            raise HTTPException(404, "文档不存在。")
        oid = owner_id(user)
        job = store.get_digest_job(oid, asset_id)
        if not job:
            return public_job(default_job(asset_id, oid))
        return public_job(job)

    @app.put("/assets/{asset_id}/digest-job")
    def put_digest_job(asset_id: str, body: DigestJobBody, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "document":
            raise HTTPException(404, "文档不存在。")
        oid = owner_id(user)
        try:
            fields = validate_job_body(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        existing = store.get_digest_job(oid, asset_id) or default_job(asset_id, oid)
        saved = store.upsert_digest_job(oid, asset_id, {**existing, **fields})
        return public_job(saved)

    @app.get("/assets/{asset_id}/assistant-sessions")
    def list_assistant_sessions(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        oid = owner_id(user)
        rows = store.list_assistant_sessions(oid, asset_id)
        return {"ok": True, "sessions": [public_session_list_item(row) for row in rows]}

    @app.post("/assets/{asset_id}/assistant-sessions")
    def create_assistant_session(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        oid = owner_id(user)
        saved = store.upsert_assistant_session(oid, asset_id, {
            "title": "新对话",
            "anchor": {},
            "turns": [],
            "summary": "",
        })
        return {"ok": True, "session": public_session(saved)}

    @app.get("/assets/{asset_id}/assistant-sessions/{session_id}")
    def get_assistant_session(
        asset_id: str, session_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store),
    ):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        oid = owner_id(user)
        found = store.get_assistant_session(oid, session_id)
        if not found or found.get("document_id") != asset_id:
            raise HTTPException(404, "会话不存在。")
        return {"ok": True, "session": public_session(found)}

    @app.put("/assets/{asset_id}/assistant-sessions/{session_id}")
    def put_assistant_session(
        asset_id: str,
        session_id: str,
        body: AssistantSessionBody,
        user: dict = Depends(user_from),
        store: Store = Depends(get_store),
    ):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        oid = owner_id(user)
        existing = store.get_assistant_session(oid, session_id)
        if not existing or existing.get("document_id") != asset_id:
            raise HTTPException(404, "会话不存在。")
        fields: dict = {"id": session_id}
        if body.title is not None:
            fields["title"] = body.title.strip() or "新对话"
        if body.anchor is not None:
            if not isinstance(body.anchor, dict):
                raise HTTPException(400, "anchor 格式无效。")
            fields["anchor"] = body.anchor
        if body.turns is not None:
            try:
                fields["turns"] = validate_session_turns([item.model_dump() for item in body.turns])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not fields.get("title"):
                current_title = (existing.get("title") or "").strip()
                if not current_title or current_title == "新对话":
                    fields["title"] = session_title(fields["turns"])
        if body.summary is not None:
            fields["summary"] = body.summary.strip()
        saved = store.upsert_assistant_session(oid, asset_id, {**existing, **fields})
        return {"ok": True, "session": public_session(saved)}

    @app.delete("/assets/{asset_id}/assistant-sessions/{session_id}")
    def delete_assistant_session(
        asset_id: str, session_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store),
    ):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "sheet":
            raise HTTPException(404, "表格不存在。")
        oid = owner_id(user)
        existing = store.get_assistant_session(oid, session_id)
        if not existing or existing.get("document_id") != asset_id:
            raise HTTPException(404, "会话不存在。")
        store.delete_assistant_session(oid, session_id)
        return {"ok": True, "deleted": session_id}

    @app.post("/assets/{asset_id}/digest-job/run")
    async def run_digest_now(asset_id: str, user: dict = Depends(user_from), store: Store = Depends(get_store)):
        asset = asset_of(store, user, asset_id)
        if not asset or asset["kind"] != "document":
            raise HTTPException(404, "文档不存在。")
        oid = owner_id(user)
        job = store.get_digest_job(oid, asset_id)
        if not job:
            raise HTTPException(400, "请先保存定时任务配置。")
        try:
            return await run_digest_job(store, job)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception:
            raise HTTPException(500, "执行资讯任务失败，请检查模型与来源配置。")

    @app.post("/sheets/remote-data")
    def sheets_remote_data(body: RemoteDataBody, _user: dict = Depends(user_from)):
        try:
            values = call_remote(
                body.url, body.values, body.method, body.function, body.headers,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        rows = len(values)
        cols = max((len(row) for row in values), default=0)
        return {"ok": True, "values": values, "rows": rows, "cols": cols}

    @app.post("/assistant/validate")
    def assistant_validate(body: ValidateBody, _user: dict = Depends(user_from)):
        try:
            return validate_office_patch(body.patch, body.context)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValidationError:
            raise HTTPException(400, "文档或选区已改变，请重新生成建议。")

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=api_host(), port=api_port(), reload=False)
