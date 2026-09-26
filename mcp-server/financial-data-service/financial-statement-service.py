"""financial_statements 表查询服务。

空参数表示匹配任意值；非空参数按 AND 联合过滤。
支持按目标货币单位转换数值（元 / 万元 / 亿元）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_connection

DEFAULT_CURRENCY_UNIT = "元"
SUPPORTED_CURRENCY_UNITS = ("元", "万元", "亿元")
UNIT_TO_YUAN: dict[str, Decimal] = {
    "元": Decimal("1"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
}

ENUM_COLUMNS = {
    "market_code": "market_code_enum",
    "report_period": "report_period_enum",
    "item_category": "item_category_enum",
}

QUERY_COLUMNS = (
    "id",
    "ticker_code",
    "market_code",
    "fiscal_year",
    "report_period",
    "item_category",
    "source_item_name",
    "standard_item_name",
    "source_value",
    "standard_value",
    "source_unit",
    "standard_unit",
    "created_at",
    "updated_at",
)


@dataclass(slots=True)
class FinancialStatementQuery:
    ticker_code: str | None = None
    market_code: str | None = None
    fiscal_year: int | None = None
    report_period: str | None = None
    item_category: str | None = None
    source_item_name: str | None = None
    standard_item_name: str | None = None
    currency_unit: str = DEFAULT_CURRENCY_UNIT


def _is_set(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def normalize_currency_unit(unit: str | None) -> str:
    text = (unit or DEFAULT_CURRENCY_UNIT).strip()
    if text not in SUPPORTED_CURRENCY_UNITS:
        raise ValueError(f"unsupported currency unit: {text}")
    return text


def convert_amount(
    value: object | None,
    *,
    from_unit: str | None,
    to_unit: str,
) -> float | None:
    if value is None:
        return None

    target_unit = normalize_currency_unit(to_unit)
    source_unit = (from_unit or DEFAULT_CURRENCY_UNIT).strip() or DEFAULT_CURRENCY_UNIT

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

    if source_unit == target_unit:
        return float(amount)

    source_factor = UNIT_TO_YUAN.get(source_unit)
    target_factor = UNIT_TO_YUAN.get(target_unit)
    if source_factor is None or target_factor is None:
        return float(amount)

    converted = amount * source_factor / target_factor
    return float(converted)


def _normalize_query(query: FinancialStatementQuery) -> FinancialStatementQuery:
    fiscal_year = query.fiscal_year
    if isinstance(fiscal_year, str):
        fiscal_year = int(fiscal_year.strip()) if fiscal_year.strip() else None

    def _strip_text(value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    return FinancialStatementQuery(
        ticker_code=_strip_text(query.ticker_code),
        market_code=_strip_text(query.market_code),
        fiscal_year=fiscal_year,
        report_period=_strip_text(query.report_period),
        item_category=_strip_text(query.item_category),
        source_item_name=_strip_text(query.source_item_name),
        standard_item_name=_strip_text(query.standard_item_name),
        currency_unit=normalize_currency_unit(query.currency_unit),
    )


def _build_where(query: FinancialStatementQuery) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    if _is_set(query.ticker_code):
        conditions.append("ticker_code = %s")
        params.append(query.ticker_code)

    if _is_set(query.market_code):
        enum_type = ENUM_COLUMNS["market_code"]
        conditions.append(f"market_code = %s::{enum_type}")
        params.append(query.market_code)

    if query.fiscal_year is not None:
        conditions.append("fiscal_year = %s")
        params.append(query.fiscal_year)

    if _is_set(query.report_period):
        enum_type = ENUM_COLUMNS["report_period"]
        conditions.append(f"report_period = %s::{enum_type}")
        params.append(query.report_period)

    if _is_set(query.item_category):
        enum_type = ENUM_COLUMNS["item_category"]
        conditions.append(f"item_category = %s::{enum_type}")
        params.append(query.item_category)

    if _is_set(query.source_item_name):
        conditions.append("source_item_name = %s")
        params.append(query.source_item_name)

    if _is_set(query.standard_item_name):
        conditions.append("standard_item_name = %s")
        params.append(query.standard_item_name)

    if conditions:
        return " WHERE " + " AND ".join(conditions), params
    return "", params


def _serialize_row(row: dict[str, Any], *, currency_unit: str) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"source_value", "standard_value"}:
            unit_key = "source_unit" if key == "source_value" else "standard_unit"
            from_unit = row.get(unit_key)
            serialized[key] = convert_amount(value, from_unit=from_unit, to_unit=currency_unit)
            continue
        if isinstance(value, Decimal):
            serialized[key] = float(value)
        elif hasattr(value, "isoformat"):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value

    serialized["source_unit"] = currency_unit
    serialized["standard_unit"] = currency_unit
    serialized["currency_unit"] = currency_unit
    return serialized


def query_financial_statements(
    ticker_code: str | None = None,
    market_code: str | None = None,
    fiscal_year: int | None = None,
    report_period: str | None = None,
    item_category: str | None = None,
    source_item_name: str | None = None,
    standard_item_name: str | None = None,
    currency_unit: str = DEFAULT_CURRENCY_UNIT,
    *,
    query: FinancialStatementQuery | None = None,
) -> list[dict[str, Any]]:
    """按条件查询财报数据；空参数不参与过滤。

    ``currency_unit`` 为目标展示单位（元/万元/亿元）。
    当库内单位与目标单位不一致时，自动换算 ``source_value`` / ``standard_value``。
    """
    if query is None:
        query = FinancialStatementQuery(
            ticker_code=ticker_code,
            market_code=market_code,
            fiscal_year=fiscal_year,
            report_period=report_period,
            item_category=item_category,
            source_item_name=source_item_name,
            standard_item_name=standard_item_name,
            currency_unit=currency_unit,
        )
    query = _normalize_query(query)

    where_sql, params = _build_where(query)
    columns = ", ".join(QUERY_COLUMNS)
    sql = f"""
        SELECT {columns}
        FROM financial_statements
        {where_sql}
        ORDER BY ticker_code, fiscal_year DESC, report_period, item_category, source_item_name
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        _serialize_row(dict(row), currency_unit=query.currency_unit)
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query financial_statements")
    parser.add_argument("--ticker-code", default="")
    parser.add_argument("--market-code", default="")
    parser.add_argument("--fiscal-year", default="")
    parser.add_argument("--report-period", default="")
    parser.add_argument("--item-category", default="")
    parser.add_argument("--source-item-name", default="")
    parser.add_argument("--standard-item-name", default="")
    parser.add_argument("--currency-unit", default=DEFAULT_CURRENCY_UNIT)
    args = parser.parse_args(argv)

    fiscal_year: int | None = None
    if args.fiscal_year.strip():
        fiscal_year = int(args.fiscal_year)

    try:
        rows = query_financial_statements(
            ticker_code=args.ticker_code or None,
            market_code=args.market_code or None,
            fiscal_year=fiscal_year,
            report_period=args.report_period or None,
            item_category=args.item_category or None,
            source_item_name=args.source_item_name or None,
            standard_item_name=args.standard_item_name or None,
            currency_unit=args.currency_unit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"query failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\ntotal: {len(rows)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
