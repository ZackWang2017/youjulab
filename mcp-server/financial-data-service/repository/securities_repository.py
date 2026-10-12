"""从互联网拉取 SH / SZ / HK 证券代码并写入 securities 表。

数据源：
  - SH / SZ：新浪财经 Market_Center API
  - HK：港交所官方 ListOfSecurities.xlsx

用法（在 mcp-server 目录下）：
  ../.venv/bin/python data-providor/securities_symbol_data.py
  ../.venv/bin/python data-providor/securities_symbol_data.py --markets SH,SZ
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import openpyxl

# 允许从 mcp-server 目录直接运行本脚本
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.connection import get_connection

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SINA_BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
SINA_PAGE_SIZE = 80
SINA_NODES = {"SH": "sh_a", "SZ": "sz_a"}
HKEX_LIST_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
DEFAULT_MARKETS = ("SH", "SZ", "HK")
SECURITY_TYPE_STOCK = "股票"


@dataclass(frozen=True, slots=True)
class SecurityRecord:
    ticker_code: str
    name: str
    security_type: str = SECURITY_TYPE_STOCK


def _http_get(url: str, *, referer: str = "https://finance.sina.com.cn/", timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": referer},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get_with_retry(url: str, *, retries: int = 3, pause: float = 1.0, **kwargs) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _http_get(url, **kwargs)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(pause * attempt)
    raise RuntimeError(f"request failed after {retries} retries: {url}") from last_error


def format_ticker(code: str, market: str) -> str:
    code = str(code).strip()
    if market == "HK":
        code = code.zfill(5)
    return f"{code}.{market}"


def fetch_sina_market(market: str) -> list[SecurityRecord]:
    node = SINA_NODES[market]
    count_url = f"{SINA_BASE}/Market_Center.getHQNodeStockCount?node={node}"
    total = int(json.loads(_http_get_with_retry(count_url)))
    pages = math.ceil(total / SINA_PAGE_SIZE)

    records: list[SecurityRecord] = []
    for page in range(1, pages + 1):
        data_url = (
            f"{SINA_BASE}/Market_Center.getHQNodeData"
            f"?page={page}&num={SINA_PAGE_SIZE}&sort=symbol&asc=1&node={node}"
        )
        batch = json.loads(_http_get_with_retry(data_url))
        for item in batch:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            if not code or not name or name == "FAILED":
                continue
            records.append(
                SecurityRecord(
                    ticker_code=format_ticker(code, market),
                    name=name,
                )
            )
    return records


def fetch_hk_equities() -> list[SecurityRecord]:
    content = _http_get_with_retry(
        HKEX_LIST_URL,
        referer="https://www.hkex.com.hk/",
        timeout=60,
    )
    workbook = openpyxl.load_workbook(BytesIO(content), data_only=True)
    worksheet = workbook[workbook.sheetnames[0]]

    records: list[SecurityRecord] = []
    for row in worksheet.iter_rows(min_row=4, values_only=True):
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(row[1]).strip() if row[1] else ""
        category = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        if not code or not name or category != "Equity":
            continue
        records.append(
            SecurityRecord(
                ticker_code=format_ticker(code, "HK"),
                name=name,
            )
        )
    return records


def fetch_market(market: str) -> list[SecurityRecord]:
    market = market.upper()
    if market in SINA_NODES:
        return fetch_sina_market(market)
    if market == "HK":
        return fetch_hk_equities()
    raise ValueError(f"unsupported market: {market}")


def save_securities(records: Iterable[SecurityRecord]) -> int:
    rows = [
        (item.ticker_code, item.name, item.security_type)
        for item in records
    ]
    if not rows:
        return 0

    sql = """
        INSERT INTO securities (ticker_code, name, security_type)
        VALUES (%s, %s, %s::security_type_enum)
        ON CONFLICT (ticker_code) DO UPDATE SET
            name = EXCLUDED.name,
            security_type = EXCLUDED.security_type,
            updated_at = NOW()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def sync_securities(*, markets: Iterable[str] = DEFAULT_MARKETS) -> dict:
    summary: dict[str, int | str] = {"saved_total": 0}
    for market in markets:
        market = market.upper()
        records = fetch_market(market)
        saved = save_securities(records)
        summary[market] = saved
        summary["saved_total"] = int(summary["saved_total"]) + saved
    return summary


def _parse_markets(raw: str) -> tuple[str, ...]:
    markets = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    invalid = [m for m in markets if m not in {"SH", "SZ", "HK"}]
    if invalid:
        raise ValueError(f"unsupported markets: {', '.join(invalid)}")
    return markets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync SH/SZ/HK securities into PostgreSQL")
    parser.add_argument(
        "--markets",
        default=",".join(DEFAULT_MARKETS),
        help="comma-separated markets: SH,SZ,HK",
    )
    args = parser.parse_args(argv)

    try:
        markets = _parse_markets(args.markets)
        summary = sync_securities(markets=markets)
    except Exception as exc:  # noqa: BLE001
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    for market in markets:
        print(f"{market}: {summary[market]} records")
    print(f"total saved/updated: {summary['saved_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
