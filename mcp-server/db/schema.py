"""数据库 DDL：证券、财报数据、项目映射三张表。"""

from __future__ import annotations

# 枚举约束与 prompts 字段一一对应
SECURITY_TYPES = ("股票", "债券", "基金", "期货", "期权", "外汇", "黄金", "其他")
MARKET_CODES = ("SH", "SZ", "BJ", "HK", "US", "其他")
REPORT_PERIODS = ("年报", "1季报", "2季报", "3季报", "4季报")
ITEM_CATEGORIES = ("资产负债表", "利润表", "现金流量表", "其他")

_CREATE_ENUMS = """
DO $$ BEGIN
    CREATE TYPE security_type_enum AS ENUM ({security_types});
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE market_code_enum AS ENUM ({market_codes});
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE report_period_enum AS ENUM ({report_periods});
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE item_category_enum AS ENUM ({item_categories});
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
""".format(
    security_types=", ".join(f"'{v}'" for v in SECURITY_TYPES),
    market_codes=", ".join(f"'{v}'" for v in MARKET_CODES),
    report_periods=", ".join(f"'{v}'" for v in REPORT_PERIODS),
    item_categories=", ".join(f"'{v}'" for v in ITEM_CATEGORIES),
)

CREATE_SECURITIES = """
CREATE TABLE IF NOT EXISTS securities (
    ticker_code     VARCHAR(32) PRIMARY KEY,          -- 证券代码，如 600000.SH
    name            VARCHAR(128) NOT NULL,          -- 证券简称
    security_type   security_type_enum NOT NULL,    -- 股票/债券/基金等
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_FINANCIAL_STATEMENTS = """
CREATE TABLE IF NOT EXISTS financial_statements (
    id                  BIGSERIAL PRIMARY KEY,
    ticker_code         VARCHAR(32) NOT NULL REFERENCES securities(ticker_code),
    market_code         market_code_enum NOT NULL,
    fiscal_year         SMALLINT NOT NULL CHECK (fiscal_year >= 1900 AND fiscal_year <= 2100),
    report_period       report_period_enum NOT NULL,
    item_category       item_category_enum NOT NULL,
    source_item_name    VARCHAR(256) NOT NULL,
    standard_item_name  VARCHAR(256) NOT NULL,
    source_value        NUMERIC(24, 6),
    standard_value      NUMERIC(24, 6),
    source_unit         VARCHAR(16),
    standard_unit       VARCHAR(16),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        ticker_code, fiscal_year, report_period,
        item_category, source_item_name
    )
);

CREATE INDEX IF NOT EXISTS idx_financial_statements_lookup
    ON financial_statements (ticker_code, fiscal_year, report_period);
"""

CREATE_ITEM_MAPPINGS = """
CREATE TABLE IF NOT EXISTS financial_item_mappings (
    id                  BIGSERIAL PRIMARY KEY,
    data_source         VARCHAR(64) NOT NULL,       -- 新浪财经、pdf年报等
    standard_item_name  VARCHAR(256) NOT NULL,
    source_item_name    VARCHAR(256) NOT NULL,
    source_unit         VARCHAR(16),
    standard_unit       VARCHAR(16),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (data_source, source_item_name)
);

CREATE INDEX IF NOT EXISTS idx_item_mappings_standard
    ON financial_item_mappings (data_source, standard_item_name);
"""

ALL_DDL = (_CREATE_ENUMS, CREATE_SECURITIES, CREATE_FINANCIAL_STATEMENTS, CREATE_ITEM_MAPPINGS)
