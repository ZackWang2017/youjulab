"""SQLite 版基础金融数据表。枚举由 CHECK 约束在服务层校验。"""
from __future__ import annotations

ALL_DDL = (
    """
    CREATE TABLE IF NOT EXISTS securities (
        ticker_code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        security_type TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS financial_statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker_code TEXT NOT NULL REFERENCES securities(ticker_code),
        market_code TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL CHECK (fiscal_year >= 1900 AND fiscal_year <= 2100),
        report_period TEXT NOT NULL,
        item_category TEXT NOT NULL,
        source_item_name TEXT NOT NULL,
        standard_item_name TEXT NOT NULL,
        source_value NUMERIC,
        standard_value NUMERIC,
        source_unit TEXT,
        standard_unit TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (ticker_code, fiscal_year, report_period, item_category, source_item_name)
    );
    CREATE INDEX IF NOT EXISTS idx_financial_statements_lookup
        ON financial_statements (ticker_code, fiscal_year, report_period);
    """,
    """
    CREATE TABLE IF NOT EXISTS financial_item_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_source TEXT NOT NULL,
        standard_item_name TEXT NOT NULL,
        source_item_name TEXT NOT NULL,
        source_unit TEXT,
        standard_unit TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (data_source, source_item_name)
    );
    CREATE INDEX IF NOT EXISTS idx_item_mappings_standard
        ON financial_item_mappings (data_source, standard_item_name);
    """,
)
