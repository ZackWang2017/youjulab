"""东方财富 datacenter 港股财报拉取，写入 financial_statements 表。

数据源：https://datacenter.eastmoney.com/securities/api/data/v1/get
不依赖 AKShare；数值保留原始 HKD 最小单位，入库单位字段为「元」。

用法（在 mcp-server 目录下）：
  ../.venv/bin/python financial-data-service/repository/eastmoney_repository.py --ticker 00700.HK

A 股财报在同目录 a_share_statement_repository.py，复用本文件的请求与入库。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.connection import get_connection

EM_DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
EM_REFERER = "https://quote.eastmoney.com/"
EM_PAGE_SIZE = 500
EM_REQUEST_PAUSE = 0.15
UNIT_YUAN = "元"
DATA_SOURCE = "东方财富"
MARKET_HK = "HK"

# 东方财富 DATE_TYPE_CODE → 库内 report_period 枚举
DATE_TYPE_TO_PERIOD = {
    "001": "年报",
    "002": "2季报",
    "003": "1季报",
    "004": "3季报",
}

STATEMENT_REPORTS: dict[str, str] = {
    "资产负债表": "RPT_HKF10_FN_BALANCE_PC",
    "利润表": "RPT_HKF10_FN_INCOME_PC",
    "现金流量表": "RPT_HKF10_FN_CASHFLOW_PC",
}


@dataclass(frozen=True, slots=True)
class FinancialStatementRecord:
    ticker_code: str
    market_code: str
    fiscal_year: int
    report_period: str
    item_category: str
    source_item_name: str
    standard_item_name: str
    source_value: Decimal | None
    standard_value: Decimal | None
    source_unit: str
    standard_unit: str


def normalize_ticker(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        symbol, market = code.split(".", 1)
        return f"{symbol.zfill(5)}.{market}"
    return f"{code.zfill(5)}.HK"


def parse_report_date(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_decimal(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


class EastMoneyHkClient:
    def __init__(self, *, timeout: float = 30.0, retries: int = 3) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0", "Referer": EM_REFERER},
            trust_env=False,
        )
        self._retries = retries

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EastMoneyHkClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_page(
        self,
        *,
        report_name: str,
        secucode: str,
        page_number: int,
        page_size: int = EM_PAGE_SIZE,
    ) -> dict:
        params = {
            "reportName": report_name,
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": page_number,
            "pageSize": page_size,
            "sortTypes": -1,
            "sortColumns": "REPORT_DATE",
            "source": "F10",
            "client": "PC",
        }
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                response = self._client.get(EM_DATACENTER_URL, params=params)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    message = payload.get("message") or "eastmoney api returned success=false"
                    raise RuntimeError(message)
                result = payload.get("result")
                if result is None:
                    raise RuntimeError("eastmoney api missing result")
                return result
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt < self._retries:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(f"eastmoney request failed: {report_name} page={page_number}") from last_error

    def iter_statement_rows(
        self,
        *,
        secucode: str,
        item_category: str,
        report_name: str,
    ) -> Iterator[dict]:
        first = self._get_page(report_name=report_name, secucode=secucode, page_number=1)
        pages = int(first.get("pages") or 1)
        rows = first.get("data") or []
        for row in rows:
            yield row
        for page_number in range(2, pages + 1):
            time.sleep(EM_REQUEST_PAUSE)
            result = self._get_page(
                report_name=report_name,
                secucode=secucode,
                page_number=page_number,
            )
            for row in result.get("data") or []:
                yield row

    def fetch_latest_row(self, *, report_name: str, secucode: str) -> dict | None:
        """按报告期倒序取最新一行，供增量检查，不拉全表。"""
        result = self._get_page(
            report_name=report_name,
            secucode=secucode,
            page_number=1,
            page_size=1,
        )
        rows = result.get("data") or []
        return rows[0] if rows else None


def row_to_record(row: dict, *, ticker_code: str, item_category: str) -> FinancialStatementRecord | None:
    item_name = str(row.get("STD_ITEM_NAME") or "").strip()
    if not item_name:
        return None

    report_dt = parse_report_date(row.get("REPORT_DATE"))
    if report_dt is None:
        return None

    date_type = str(row.get("DATE_TYPE_CODE") or "").strip()
    report_period = DATE_TYPE_TO_PERIOD.get(date_type)
    if report_period is None:
        return None

    amount = to_decimal(row.get("AMOUNT"))

    return FinancialStatementRecord(
        ticker_code=ticker_code,
        market_code=MARKET_HK,
        fiscal_year=report_dt.year,
        report_period=report_period,
        item_category=item_category,
        source_item_name=item_name,
        standard_item_name=item_name,
        source_value=amount,
        standard_value=amount,
        source_unit=UNIT_YUAN,
        standard_unit=UNIT_YUAN,
    )


def fetch_hk_financial_statements(
    ticker: str,
    *,
    client: EastMoneyHkClient | None = None,
) -> list[FinancialStatementRecord]:
    ticker_code = normalize_ticker(ticker)
    records: list[FinancialStatementRecord] = []

    def _collect(active_client: EastMoneyHkClient) -> None:
        for item_category, report_name in STATEMENT_REPORTS.items():
            for row in active_client.iter_statement_rows(
                secucode=ticker_code,
                item_category=item_category,
                report_name=report_name,
            ):
                record = row_to_record(row, ticker_code=ticker_code, item_category=item_category)
                if record is not None:
                    records.append(record)

    if client is not None:
        _collect(client)
    else:
        with EastMoneyHkClient() as owned:
            _collect(owned)

    return records


def save_financial_statements(records: Iterable[FinancialStatementRecord]) -> int:
    rows = [
        (
            item.ticker_code,
            item.market_code,
            item.fiscal_year,
            item.report_period,
            item.item_category,
            item.source_item_name,
            item.standard_item_name,
            item.source_value,
            item.standard_value,
            item.source_unit,
            item.standard_unit,
        )
        for item in records
    ]
    if not rows:
        return 0

    sql = """
        INSERT INTO financial_statements (
            ticker_code, market_code, fiscal_year, report_period, item_category,
            source_item_name, standard_item_name,
            source_value, standard_value, source_unit, standard_unit
        )
        VALUES (
            %s, %s::market_code_enum, %s, %s::report_period_enum, %s::item_category_enum,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (ticker_code, fiscal_year, report_period, item_category, source_item_name)
        DO UPDATE SET
            market_code = EXCLUDED.market_code,
            standard_item_name = EXCLUDED.standard_item_name,
            source_value = EXCLUDED.source_value,
            standard_value = EXCLUDED.standard_value,
            source_unit = EXCLUDED.source_unit,
            standard_unit = EXCLUDED.standard_unit,
            updated_at = NOW()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def sync_hk_financial_statements(ticker: str) -> dict[str, int | str]:
    ticker_code = normalize_ticker(ticker)
    records = fetch_hk_financial_statements(ticker_code)
    saved = save_financial_statements(records)

    by_category: dict[str, int] = {}
    for record in records:
        by_category[record.item_category] = by_category.get(record.item_category, 0) + 1

    return {
        "ticker_code": ticker_code,
        "data_source": DATA_SOURCE,
        "fetched": len(records),
        "saved": saved,
        "balance_sheet": by_category.get("资产负债表", 0),
        "income_statement": by_category.get("利润表", 0),
        "cash_flow": by_category.get("现金流量表", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync HK financial statements from East Money into PostgreSQL",
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help='HK ticker, e.g. 00700 or 00700.HK',
    )
    args = parser.parse_args(argv)

    try:
        summary = sync_hk_financial_statements(args.ticker)
    except Exception as exc:  # noqa: BLE001
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    print(f"ticker: {summary['ticker_code']}")
    print(f"source: {summary['data_source']}")
    print(f"fetched: {summary['fetched']}")
    print(f"saved/updated: {summary['saved']}")
    print(f"  资产负债表: {summary['balance_sheet']}")
    print(f"  利润表: {summary['income_statement']}")
    print(f"  现金流量表: {summary['cash_flow']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
