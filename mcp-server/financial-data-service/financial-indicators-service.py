"""财务指标计算服务 —— 基于 financial_statements 表，按证券代码/年份/期间汇总。

指标清单与 screening-profile 初筛口径对齐（见 prompts 29-41）。
科目映射通过 ``ITEM_FIELD_MAP`` 配置；未配置的标准键对应指标返回 ``missing_inputs``，不猜测取值。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STATEMENT_SERVICE_PATH = Path(__file__).resolve().parent / "financial-statement-service.py"


def _load_statement_service():
    spec = importlib.util.spec_from_file_location("financial_statement_service", STATEMENT_SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load: {STATEMENT_SERVICE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_statement_service = _load_statement_service()
query_financial_statements = _statement_service.query_financial_statements

# ---------------------------------------------------------------------------
# 标准键 → 财报 source_item_name（可多个，求和）
# 空列表表示尚未配置，依赖该键的指标将标记 missing_inputs
# ---------------------------------------------------------------------------

HK_ITEM_FIELD_MAP: dict[str, list[str]] = {
    "net_profit": ["股东应占溢利"],
    "equity": ["股东权益"],
    "total_assets": ["总资产"],
    "pretax_profit": ["除税前溢利"],
    "revenue": ["营运收入"],
    "gross_profit": ["毛利"],
    "accounts_receivable": ["应收帐款"],
    "current_assets": ["流动资产合计"],
    "current_liab": ["流动负债合计"],
    "inventory": ["存货"],
    "ocf": ["经营业务现金净额"],
    "cash_net_change": ["现金净额"],
    "cash_balance": ["期末现金"],
    # 待用户确认科目名后填入：
    "interest_debt": [],
    "productive_assets": [],
    "bank_acceptance": [],
    "three_fees_components": [],
}

MARKET_ITEM_FIELD_MAP: dict[str, dict[str, list[str]]] = {
    "HK": HK_ITEM_FIELD_MAP,
}

STATUS_OK = "ok"
STATUS_MISSING = "missing_inputs"
STATUS_ERROR = "calc_error"


@dataclass(slots=True)
class IndicatorDefinition:
    id: str
    name: str
    unit: str
    formula: str
    calculator: Callable[[dict[str, float | None]], tuple[float | None, list[str]]]


def _clean(value: float | None) -> float | None:
    if value is None:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 6)


def _ratio(numerator: float | None, denominator: float | None, *, as_percent: bool = False) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    result = numerator / denominator
    return _clean(result * 100.0 if as_percent else result)


def _require(values: dict[str, float | None], keys: list[str]) -> tuple[bool, list[str]]:
    missing = [key for key in keys if values.get(key) is None]
    return not missing, missing


def _calc_roe(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["net_profit", "equity"])
    if not ok:
        return None, missing
    return _ratio(v["net_profit"], v["equity"], as_percent=True), []


def _calc_interest_debt_ratio(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["interest_debt", "total_assets"])
    if not ok:
        return None, missing
    return _ratio(v["interest_debt"], v["total_assets"], as_percent=True), []


def _calc_asset_light(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["pretax_profit", "productive_assets"])
    if not ok:
        return None, missing
    return _ratio(v["pretax_profit"], v["productive_assets"], as_percent=True), []


def _calc_receivables_ratio(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["accounts_receivable", "total_assets", "bank_acceptance"])
    if not ok:
        return None, missing
    receivables = v["accounts_receivable"] - v["bank_acceptance"]
    return _ratio(receivables, v["total_assets"], as_percent=True), []


def _calc_gross_margin(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    if v.get("gross_profit") is not None and v.get("revenue") is not None:
        return _ratio(v["gross_profit"], v["revenue"], as_percent=True), []
    ok, missing = _require(v, ["revenue", "cogs"])
    if not ok:
        return None, missing + (["gross_profit"] if v.get("gross_profit") is None else [])
    gross = v["revenue"] - v["cogs"]
    return _ratio(gross, v["revenue"], as_percent=True), []


def _calc_fee_to_revenue(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["three_fees", "revenue"])
    if not ok:
        return None, missing
    return _ratio(v["three_fees"], v["revenue"], as_percent=True), []


def _calc_fee_to_gross_profit(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["three_fees", "gross_profit"])
    if not ok:
        return None, missing
    return _ratio(v["three_fees"], v["gross_profit"], as_percent=True), []


def _calc_ocf_to_profit(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["ocf", "net_profit"])
    if not ok:
        return None, missing
    return _ratio(v["ocf"], v["net_profit"]), []


def _calc_current_ratio(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["current_assets", "current_liab"])
    if not ok:
        return None, missing
    return _ratio(v["current_assets"], v["current_liab"]), []


def _calc_quick_ratio(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["current_assets", "current_liab", "inventory"])
    if not ok:
        return None, missing
    quick_assets = v["current_assets"] - v["inventory"]
    return _ratio(quick_assets, v["current_liab"]), []


def _calc_cash_net_change(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["cash_net_change"])
    if not ok:
        return None, missing
    return _clean(v["cash_net_change"]), []


def _calc_cash_balance(v: dict[str, float | None]) -> tuple[float | None, list[str]]:
    ok, missing = _require(v, ["cash_balance"])
    if not ok:
        return None, missing
    return _clean(v["cash_balance"]), []


INDICATOR_DEFINITIONS: tuple[IndicatorDefinition, ...] = (
    IndicatorDefinition("roe", "ROE", "%", "净利润 / 净资产 × 100", _calc_roe),
    IndicatorDefinition("interest_debt_ratio", "有息负债率", "%", "有息负债 / 总资产 × 100", _calc_interest_debt_ratio),
    IndicatorDefinition("asset_light", "轻资产", "%", "税前利润总额 / 生产资产 × 100", _calc_asset_light),
    IndicatorDefinition("receivables_ratio", "应收(减银票)/总资产", "%", "(应收账款 - 银票) / 总资产 × 100", _calc_receivables_ratio),
    IndicatorDefinition("gross_margin", "毛利率", "%", "毛利 / 营业收入 × 100", _calc_gross_margin),
    IndicatorDefinition("fee_to_revenue", "三费/营业收入", "%", "三费 / 营业收入 × 100", _calc_fee_to_revenue),
    IndicatorDefinition("fee_to_gross_profit", "三费/毛利润", "%", "三费 / 毛利润 × 100", _calc_fee_to_gross_profit),
    IndicatorDefinition("ocf_to_profit", "经营现金流净额/净利润", "x", "经营现金流净额 / 净利润", _calc_ocf_to_profit),
    IndicatorDefinition("current_ratio", "流动比", "x", "流动资产 / 流动负债", _calc_current_ratio),
    IndicatorDefinition("quick_ratio", "速动比", "x", "(流动资产 - 存货) / 流动负债", _calc_quick_ratio),
    IndicatorDefinition("cash_net_change", "现金及现金等价物净增加额", "元", "现金流量表科目", _calc_cash_net_change),
    IndicatorDefinition("cash_balance", "期末现金及现金等价物余额", "元", "资产负债表科目", _calc_cash_balance),
)


def get_item_field_map(market_code: str | None) -> dict[str, list[str]]:
    if market_code and market_code in MARKET_ITEM_FIELD_MAP:
        return MARKET_ITEM_FIELD_MAP[market_code]
    return HK_ITEM_FIELD_MAP


def _index_statement_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    indexed: dict[str, float] = {}
    for row in rows:
        name = str(row.get("source_item_name") or "").strip()
        value = row.get("standard_value")
        if value is None:
            value = row.get("source_value")
        if not name or value is None:
            continue
        indexed[name] = float(value)
    return indexed


def _resolve_standard_values(
    indexed_items: dict[str, float],
    field_map: dict[str, list[str]],
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}

    for key, item_names in field_map.items():
        if key == "three_fees_components":
            continue
        if not item_names:
            values[key] = None
            continue
        total = 0.0
        found = False
        for name in item_names:
            if name in indexed_items:
                total += indexed_items[name]
                found = True
        values[key] = total if found else None

    components = field_map.get("three_fees_components") or []
    if components:
        total = 0.0
        found = False
        for name in components:
            if name in indexed_items:
                total += indexed_items[name]
                found = True
        values["three_fees"] = total if found else None
    else:
        values["three_fees"] = None

    return values


def compute_indicators_for_period(
    *,
    ticker_code: str,
    market_code: str,
    fiscal_year: int,
    report_period: str,
    rows: list[dict[str, Any]],
    field_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    active_map = field_map or get_item_field_map(market_code)
    indexed = _index_statement_rows(rows)
    standard_values = _resolve_standard_values(indexed, active_map)

    indicators: list[dict[str, Any]] = []
    for definition in INDICATOR_DEFINITIONS:
        value, missing = definition.calculator(standard_values)
        status = STATUS_OK if value is not None else (STATUS_MISSING if missing else STATUS_ERROR)
        indicators.append({
            "id": definition.id,
            "name": definition.name,
            "unit": definition.unit,
            "formula": definition.formula,
            "value": value,
            "status": status,
            "missing_fields": missing,
        })

    return {
        "ticker_code": ticker_code,
        "market_code": market_code,
        "fiscal_year": fiscal_year,
        "report_period": report_period,
        "inputs": {key: val for key, val in standard_values.items() if val is not None},
        "indicators": indicators,
    }


def _group_key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        row["ticker_code"],
        row["market_code"],
        int(row["fiscal_year"]),
        row["report_period"],
    )


def query_financial_indicators(
    ticker_code: str | None = None,
    market_code: str | None = None,
    fiscal_year: int | None = None,
    report_period: str | None = None,
    *,
    currency_unit: str = "元",
    field_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """按证券代码、年份、期间查询并汇总 12 项财务指标。

    空参数表示匹配任意值；返回按 (ticker_code, fiscal_year, report_period) 分组的指标列表。
    """
    rows = query_financial_statements(
        ticker_code=ticker_code,
        market_code=market_code,
        fiscal_year=fiscal_year,
        report_period=report_period,
        currency_unit=currency_unit,
    )

    grouped: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = _group_key(row)
        grouped.setdefault(key, []).append(row)

    periods: list[dict[str, Any]] = []
    for (code, market, year, period), period_rows in sorted(grouped.items(), reverse=True):
        active_map = field_map or get_item_field_map(market)
        periods.append(
            compute_indicators_for_period(
                ticker_code=code,
                market_code=market,
                fiscal_year=year,
                report_period=period,
                rows=period_rows,
                field_map=active_map,
            )
        )

    return {
        "total_periods": len(periods),
        "currency_unit": currency_unit,
        "periods": periods,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute financial indicators from financial_statements")
    parser.add_argument("--ticker-code", default="00700.HK")
    parser.add_argument("--market-code", default="")
    parser.add_argument("--fiscal-year", default="")
    parser.add_argument("--report-period", default="年报")
    parser.add_argument("--currency-unit", default="元")
    args = parser.parse_args(argv)

    fiscal_year = int(args.fiscal_year) if args.fiscal_year.strip() else None

    try:
        result = query_financial_indicators(
            ticker_code=args.ticker_code or None,
            market_code=args.market_code or None,
            fiscal_year=fiscal_year,
            report_period=args.report_period or None,
            currency_unit=args.currency_unit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"query failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
