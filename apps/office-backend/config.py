"""office-backend 环境配置。"""
from __future__ import annotations

import os
import secrets
import base64
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def mongo_url() -> str:
    return os.environ.get("OFFICE_MONGO_URL", "memory://office").strip() or "memory://office"


def mongo_db() -> str:
    return os.environ.get("OFFICE_MONGO_DB", "office").strip() or "office"


def office_store_mode() -> str:
    """memory | sqlite。文档资产只留在本机 SQLite。"""
    raw = (os.environ.get("OFFICE_STORE") or "").strip().lower()
    if raw == "memory":
        return "memory"
    if raw in ("sqlite", "mongo"):
        return "sqlite"
    url = mongo_url()
    if url.startswith("memory:"):
        return "memory"
    return "sqlite"


def sqlite_path() -> Path:
    raw = (os.environ.get("OFFICE_SQLITE_PATH") or "").strip()
    if not raw:
        url = mongo_url()
        if url.startswith("sqlite:"):
            raw = url[len("sqlite:"):].lstrip("/")
            if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
                path = Path(raw)
            else:
                path = ROOT / raw
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        raw = str(ROOT / "data" / "office-local" / "office.sqlite")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_file_root_override: Path | None = None


def default_file_root() -> Path:
    raw = os.environ.get("OFFICE_FILE_ROOT", str(ROOT / "data" / "office-files"))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_file_root_override(path: Path | None) -> Path:
    global _file_root_override
    if path is None:
        _file_root_override = None
        return default_file_root()
    target = path.expanduser()
    if not target.is_absolute():
        target = ROOT / target
    target.mkdir(parents=True, exist_ok=True)
    _file_root_override = target
    return target


def file_root() -> Path:
    if _file_root_override is not None:
        return _file_root_override
    return default_file_root()


def resolve_asset_file_path(stored: str | None, asset_id: str | None = None) -> Path | None:
    """把库里的 file_path 解析到当前 OFFICE_FILE_ROOT。

    备份/换机器后绝对路径常失效；只要相对 office-files/ 之后的后缀还在，就能找回文件。
    """
    if not stored:
        return None
    raw = Path(stored).expanduser()
    if raw.is_file():
        return raw
    text = stored.replace("\\", "/")
    marker = "office-files/"
    idx = text.find(marker)
    root = file_root()
    if idx >= 0:
        candidate = root / text[idx + len(marker) :]
        if candidate.is_file():
            return candidate
    if asset_id:
        # 当前上传约定：{root}/{owner}/{project}/{asset_id}.pdf
        matches = list(root.glob(f"*/*/{asset_id}.pdf"))
        if len(matches) == 1 and matches[0].is_file():
            return matches[0]
    return None


def jwt_secret() -> str:
    return os.environ.get("OFFICE_JWT_SECRET", "office-dev-secret-please-change-32").strip() or "office-dev-secret-please-change-32"


def api_host() -> str:
    return os.environ.get("OFFICE_API_HOST", "127.0.0.1")


def api_port() -> int:
    return int(os.environ.get("OFFICE_API_PORT", "8100"))


def mcp_remote_base() -> str:
    """远程 mcp 根地址。调试默认本机 8000；部署后改 MCP_REMOTE_BASE_URL 或在登录窗里改。"""
    explicit = (os.environ.get("MCP_REMOTE_BASE_URL") or os.environ.get("FINANCE_MCP_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.environ.get("FINANCE_MCP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = (os.environ.get("FINANCE_MCP_PORT") or "8000").strip() or "8000"
    return f"http://{host}:{port}"


def mcp_http_base() -> str:
    return mcp_remote_base()


def mcp_access_token() -> str:
    """与 mcp-server 共用的静态令牌，供 mcp 调用本服务时鉴权。"""
    for key in ("MCP_ACCESS_TOKEN", "FINANCE_MCP_TOKEN"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return "invpdevstatic32tokenpleasechange"


def mcp_token_matches(token: str) -> bool:
    expected = mcp_access_token()
    provided = (token or "").strip()
    if not provided or len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


def model_base_url() -> str:
    return (os.environ.get("OFFICE_MODEL_BASE_URL") or "").strip().rstrip("/")


def model_name() -> str:
    return (os.environ.get("OFFICE_MODEL_NAME") or "").strip()


def model_api_key() -> str:
    return (os.environ.get("OFFICE_MODEL_API_KEY") or "").strip()


def model_configured() -> bool:
    return bool(model_base_url() and model_name())


def llm_config_set() -> int:
    """模型凭据来源：0 平台、1 当前用户、3 按用户选择（当前仅自有模型）。"""
    raw = (os.environ.get("LLM_CONFIG_SET") or "0").strip()
    try:
        return int(raw)
    except ValueError:
        return -1


def user_model_encryption_key() -> bytes | None:
    """读取 AES-256-GCM 主密钥；缺失时拒绝保存，绝不降级为明文。"""
    raw = (os.environ.get("OFFICE_MODEL_CREDENTIALS_KEY") or "").strip()
    if not raw:
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None
    return key if len(key) == 32 else None


def pg_url() -> str:
    return (os.environ.get("OFFICE_PG_URL") or "").strip()


def vector_backend() -> str:
    """pg | sqlite。桌面 OFFICE_STORE=sqlite 时默认 sqlite。"""
    raw = (os.environ.get("OFFICE_VECTOR_BACKEND") or "").strip().lower()
    if raw in ("pg", "postgres", "pgvector"):
        return "pg"
    if raw in ("sqlite", "sqlite-vec", "sqlite_vec"):
        return "sqlite"
    if office_store_mode() == "sqlite":
        return "sqlite"
    return "pg" if pg_url() else "sqlite"


def vector_sqlite_path() -> Path:
    raw = (os.environ.get("OFFICE_VECTOR_SQLITE_PATH") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = ROOT / path
    else:
        path = sqlite_path().with_name("office-vectors.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def embedding_provider() -> str:
    return (os.environ.get("OFFICE_EMBEDDING_PROVIDER") or "ollama").strip().lower() or "ollama"


def embedding_base_url() -> str:
    return (os.environ.get("OFFICE_EMBEDDING_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


def embedding_model() -> str:
    return (os.environ.get("OFFICE_EMBEDDING_MODEL") or "bge-m3").strip() or "bge-m3"


def embedding_dim() -> int:
    return int(os.environ.get("OFFICE_EMBEDDING_DIM") or "1024")


def embedding_api_key() -> str:
    return (os.environ.get("OFFICE_EMBEDDING_API_KEY") or "").strip()


def remote_office_base() -> str:
    """桌面本地 backend 调用云端 office 的根地址（含 /office 前缀与否均可）。"""
    return (os.environ.get("OFFICE_REMOTE_BASE_URL") or "").strip().rstrip("/")


def remote_user_token() -> str:
    return (os.environ.get("OFFICE_REMOTE_USER_TOKEN") or "").strip()


def digest_tz() -> str:
    return (os.environ.get("DIGEST_TZ") or "Asia/Shanghai").strip() or "Asia/Shanghai"
