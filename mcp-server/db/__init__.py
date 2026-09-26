"""PostgreSQL 持久化层。"""

from db.config import DatabaseConfig, load_config
from db.connection import connect, get_connection
from db.schema import (
    CREATE_FINANCIAL_STATEMENTS,
    CREATE_ITEM_MAPPINGS,
    CREATE_SECURITIES,
    ITEM_CATEGORIES,
    MARKET_CODES,
    REPORT_PERIODS,
    SECURITY_TYPES,
)

__all__ = [
    "DatabaseConfig",
    "load_config",
    "connect",
    "get_connection",
    "ensure_database",
    "init_schema",
    "SECURITY_TYPES",
    "MARKET_CODES",
    "REPORT_PERIODS",
    "ITEM_CATEGORIES",
    "CREATE_SECURITIES",
    "CREATE_FINANCIAL_STATEMENTS",
    "CREATE_ITEM_MAPPINGS",
]


def ensure_database():
    from db.bootstrap import ensure_database as _ensure_database

    return _ensure_database()


def init_schema():
    from db.bootstrap import init_schema as _init_schema

    return _init_schema()
