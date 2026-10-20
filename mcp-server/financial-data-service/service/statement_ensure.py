"""查询财报前，若该证券在库中一行都没有，则补抓远程数据。

只在「整只股票没有任何财报」时请求远程。已有报表、只是本次筛选没命中时不重复抓取。
同一证券同时只能有一次补抓；远程失败或确认没有数据源时，短时间内不再重试。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

SERVICE_DIR = Path(__file__).resolve().parent
REPO_DIR = SERVICE_DIR.parent / "repository"
MCP_ROOT = SERVICE_DIR.parents[1]
for _path in (str(MCP_ROOT), str(REPO_DIR), str(SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import a_share_statement_repository as a_share  # noqa: E402
import statement_service  # noqa: E402
from statement_repository import security_exists, statement_ticker_exists  # noqa: E402

NEGATIVE_TTL_SECONDS = 600
SUPPORTED_MARKETS = {"SH", "SZ", "BJ", "HK"}

_locks_guard = threading.Lock()
_ticker_locks: dict[str, threading.Lock] = {}
_negative: dict[str, tuple[float, dict[str, Any]]] = {}


def reset_statement_sync_state() -> None:
    """清空进程内的补抓锁和失败缓存。测试用。"""
    with _locks_guard:
        _ticker_locks.clear()
        _negative.clear()


def _lock_for(ticker_code: str) -> threading.Lock:
    with _locks_guard:
        lock = _ticker_locks.get(ticker_code)
        if lock is None:
            lock = threading.Lock()
            _ticker_locks[ticker_code] = lock
        return lock


def _sync_payload(
    *,
    status: str,
    ticker_code: str | None,
    data_source: str | None = None,
    saved: int = 0,
    message: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "ticker_code": ticker_code,
        "data_source": data_source,
        "saved": saved,
        "message": message,
    }


def _remember_negative(ticker_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    _negative[ticker_code] = (time.monotonic() + NEGATIVE_TTL_SECONDS, payload)
    return payload


def _cached_negative(ticker_code: str) -> dict[str, Any] | None:
    cached = _negative.get(ticker_code)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _negative.pop(ticker_code, None)
        return None
    return payload


def resolve_statement_ticker(code: str) -> tuple[str, str]:
    """返回 (ticker_code, market_code)。带后缀的按后缀，纯数字优先匹配证券库里已有的代码。"""
    text = code.strip().upper()
    if not text:
        raise ValueError("ticker_code 为空")
    if "." in text:
        symbol, market = text.split(".", 1)
        market = market.upper()
        if market == "HK":
            return f"{symbol.zfill(5)}.HK", "HK"
        if market == "US":
            return f"{symbol}.US", "US"
        if market in {"SH", "SZ", "BJ"}:
            return a_share.normalize_a_ticker(text)
        return f"{symbol}.{market}", market
    if text.isascii() and text.isalpha():
        return f"{text}.US", "US"
    if text.isdigit() and len(text) <= 5:
        hk_code = f"{text.zfill(5)}.HK"
        try:
            a_code, a_market = a_share.normalize_a_ticker(text)
        except ValueError:
            return hk_code, "HK"
        if security_exists(a_code) and not security_exists(hk_code):
            return a_code, a_market
        return hk_code, "HK"
    return a_share.normalize_a_ticker(text)


def _sync_market(ticker_code: str, market: str) -> dict[str, Any]:
    if market == "HK":
        import eastmoney_repository as hk

        return hk.sync_hk_financial_statements(ticker_code)
    return a_share.sync_a_share_financial_statements(ticker_code, source="auto")


def ensure_statements(ticker: str) -> dict[str, Any]:
    """保证该证券至少有一份财报。已有数据时直接返回 ready。"""
    ticker_code, market = resolve_statement_ticker(ticker)
    if statement_ticker_exists(ticker_code):
        return _sync_payload(status="ready", ticker_code=ticker_code)

    cached = _cached_negative(ticker_code)
    if cached is not None:
        return cached

    with _lock_for(ticker_code):
        if statement_ticker_exists(ticker_code):
            return _sync_payload(status="ready", ticker_code=ticker_code)
        cached = _cached_negative(ticker_code)
        if cached is not None:
            return cached
        if market == "US":
            return _remember_negative(
                ticker_code,
                _sync_payload(
                    status="unavailable",
                    ticker_code=ticker_code,
                    message=f"暂无美股财报数据源，无法补齐 {ticker_code}。",
                ),
            )
        if market not in SUPPORTED_MARKETS:
            return _remember_negative(
                ticker_code,
                _sync_payload(
                    status="unavailable",
                    ticker_code=ticker_code,
                    message=f"暂不支持补齐 {ticker_code} 的财报。",
                ),
            )
        if not security_exists(ticker_code):
            return _remember_negative(
                ticker_code,
                _sync_payload(
                    status="unavailable",
                    ticker_code=ticker_code,
                    message=f"证券 {ticker_code} 不在证券库中，无法补齐财报。",
                ),
            )
        try:
            summary = _sync_market(ticker_code, market)
        except Exception as exc:  # noqa: BLE001
            return _remember_negative(
                ticker_code,
                _sync_payload(
                    status="unavailable",
                    ticker_code=ticker_code,
                    message=f"补齐 {ticker_code} 财报失败：{exc}",
                ),
            )
        saved = int(summary.get("saved") or 0)
        source = str(summary.get("data_source") or "")
        if saved <= 0:
            return _remember_negative(
                ticker_code,
                _sync_payload(
                    status="unavailable",
                    ticker_code=ticker_code,
                    data_source=source or None,
                    message=f"远程没有返回 {ticker_code} 的财报。",
                ),
            )
        return _sync_payload(
            status="fetched",
            ticker_code=ticker_code,
            data_source=source or None,
            saved=saved,
            message=f"库中没有 {ticker_code} 的财报，已从{source}补齐。",
        )


def query_statements_with_sync(
    ticker_code: str | None = None,
    market_code: str | None = None,
    fiscal_year: int | None = None,
    report_period: str | None = None,
    item_category: str | None = None,
    source_item_name: str | None = None,
    standard_item_name: str | None = None,
    currency_unit: str = statement_service.DEFAULT_CURRENCY_UNIT,
) -> dict[str, Any]:
    """按原条件查询。指定了证券且库中完全没有财报时，先补抓再查。"""
    query = statement_service._normalize_query(
        statement_service.FinancialStatementQuery(
            ticker_code=ticker_code,
            market_code=market_code,
            fiscal_year=fiscal_year,
            report_period=report_period,
            item_category=item_category,
            source_item_name=source_item_name,
            standard_item_name=standard_item_name,
            currency_unit=currency_unit,
        )
    )
    if query.ticker_code:
        sync = ensure_statements(query.ticker_code)
        query = statement_service.FinancialStatementQuery(
            ticker_code=sync["ticker_code"],
            market_code=query.market_code,
            fiscal_year=query.fiscal_year,
            report_period=query.report_period,
            item_category=query.item_category,
            source_item_name=query.source_item_name,
            standard_item_name=query.standard_item_name,
            currency_unit=query.currency_unit,
        )
    else:
        sync = _sync_payload(status="ready", ticker_code=None)

    items = statement_service.query_financial_statements(query=query)
    if query.ticker_code and not items and sync["status"] in {"ready", "fetched"}:
        note = "本地已有该证券财报，但没有符合本次筛选的报告期或科目。"
        message = sync["message"]
        sync = {**sync, "message": f"{message}{note}" if message else note}
    return {
        "currency_unit": query.currency_unit,
        "items": items,
        "sync": sync,
    }
