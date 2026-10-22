"""PostgreSQL 连接配置（环境变量可覆盖默认值）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "zackwang"
    password: str = ""
    dbname: str = "finresearch"
    admin_dbname: str = "postgres"

    @property
    def dsn(self) -> str:
        if self.password:
            userinfo = f"{quote_plus(self.user)}:{quote_plus(self.password)}"
        else:
            userinfo = quote_plus(self.user)
        return f"postgresql://{userinfo}@{self.host}:{self.port}/{self.dbname}"

    @property
    def admin_dsn(self) -> str:
        if self.password:
            userinfo = f"{quote_plus(self.user)}:{quote_plus(self.password)}"
        else:
            userinfo = quote_plus(self.user)
        return f"postgresql://{userinfo}@{self.host}:{self.port}/{self.admin_dbname}"


def store_mode() -> str:
    """postgres | sqlite。桌面 MCP 默认 SQLite，服务端保持 PostgreSQL 兼容。"""
    raw = (os.environ.get("FINANCE_STORE") or "").strip().lower()
    if raw in {"postgres", "postgresql"}:
        return "postgres"
    if raw == "sqlite":
        return "sqlite"
    return "sqlite" if (os.environ.get("MCP_DESKTOP") or "").strip() == "1" else "postgres"


def sqlite_path() -> Path:
    raw = (os.environ.get("FINANCE_SQLITE_PATH") or "").strip()
    if not raw:
        raw = str(Path.home() / ".youjulab" / "finresearch.sqlite")
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> DatabaseConfig:
    url = os.environ.get("FINANCE_DATABASE_URL", "").strip()
    if url:
        # 允许直接传入完整 DSN；admin 连接仍用分项配置或默认 postgres 库
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return DatabaseConfig(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            user=parsed.username or os.environ.get("FINANCE_DB_USER", "zackwang"),
            password=parsed.password or os.environ.get("FINANCE_DB_PASSWORD", ""),
            dbname=(parsed.path or "/finresearch").lstrip("/") or "finresearch",
            admin_dbname=os.environ.get("FINANCE_DB_ADMIN", "postgres"),
        )

    return DatabaseConfig(
        host=os.environ.get("FINANCE_DB_HOST", "localhost"),
        port=int(os.environ.get("FINANCE_DB_PORT", "5432")),
        user=os.environ.get("FINANCE_DB_USER", "zackwang"),
        password=os.environ.get("FINANCE_DB_PASSWORD", ""),
        dbname=os.environ.get("FINANCE_DB_NAME", "finresearch"),
        admin_dbname=os.environ.get("FINANCE_DB_ADMIN", "postgres"),
    )
