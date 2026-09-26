"""office-api 环境配置。"""
from __future__ import annotations

import os
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


def model_base_url() -> str:
    return (os.environ.get("OFFICE_MODEL_BASE_URL") or "").strip().rstrip("/")


def model_name() -> str:
    return (os.environ.get("OFFICE_MODEL_NAME") or "").strip()


def model_api_key() -> str:
    return (os.environ.get("OFFICE_MODEL_API_KEY") or "").strip()


def model_configured() -> bool:
    return bool(model_base_url() and model_name())


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


def digest_rsshub_base() -> str:
    return (os.environ.get("DIGEST_RSSHUB_BASE") or "").strip().rstrip("/")
