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


def file_root() -> Path:
    raw = os.environ.get("OFFICE_FILE_ROOT", str(ROOT / "data" / "office-files"))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def jwt_secret() -> str:
    return os.environ.get("OFFICE_JWT_SECRET", "office-dev-secret-please-change-32").strip() or "office-dev-secret-please-change-32"


def api_host() -> str:
    return os.environ.get("OFFICE_API_HOST", "127.0.0.1")


def api_port() -> int:
    return int(os.environ.get("OFFICE_API_PORT", "8100"))


def mcp_http_base() -> str:
    """mcp-server 的 HTTP 根地址。文档技能经 /api/documents/write 写回文档。"""
    host = (os.environ.get("FINANCE_MCP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = (os.environ.get("FINANCE_MCP_PORT") or "8000").strip() or "8000"
    return f"http://{host}:{port}"


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


def digest_tz() -> str:
    return (os.environ.get("DIGEST_TZ") or "Asia/Shanghai").strip() or "Asia/Shanghai"
