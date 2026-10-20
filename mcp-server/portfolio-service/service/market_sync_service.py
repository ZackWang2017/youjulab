"""收盘后任务：补齐持仓证券的日收盘价、快照当日汇率、重算所有组合的每日净值。

这些函数都是同步阻塞的，交给 scheduler 在线程里跑；也可以通过 REST 手动触发。
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import market_quote_repository as market
import portfolio_repository as repo
import portfolio_service
from nav_service import previous_weekday

log = logging.getLogger("portfolio.sync")

_BACKFILL_DAYS = 10
_SLEEP_BETWEEN = 0.15


def sync_close_prices(run_date: date | None = None, *, tickers: list[str] | None = None) -> dict:
    """对所有组合里出现过的证券，从「已有最新收盘价 + 1 天」补到 run_date。"""
    end = run_date or date.today()
    held = repo.held_tickers_everywhere()
    if tickers:
        wanted = set(tickers)
        held = [h for h in held if h["ticker_code"] in wanted]
    scanned = ok = failed = inserted = 0
    for item in held:
        ticker = item["ticker_code"]
        scanned += 1
        last = repo.last_close_date(ticker)
        start = (last + timedelta(days=1)) if last else (item["first_date"] - timedelta(days=_BACKFILL_DAYS))
        if start > end:
            ok += 1
            continue
        try:
            closes = market.fetch_daily_closes(ticker, start, end)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("close sync failed %s: %s", ticker, exc)
            continue
        if closes:
            inserted += repo.upsert_close_prices((ticker, day, close) for day, close in closes)
        ok += 1
        time.sleep(_SLEEP_BETWEEN)
    return {"scanned": scanned, "ok": ok, "failed": failed, "rows_inserted": inserted, "end": end.isoformat()}


def snapshot_fx_rates(run_date: date | None = None) -> dict:
    day = run_date or date.today()
    rates = market.get_fx_rates(force=True)
    repo.upsert_fx_rates(day, rates.as_dict())
    return {"date": day.isoformat(), "usd_cny": str(rates.usd_cny), "hkd_cny": str(rates.hkd_cny)}


def recompute_all_nav(run_date: date | None = None, *, through_previous_trading_day: bool = True) -> dict:
    """计划任务在 17:00 跑时按需求固定计算「上一交易日」净值；手动刷新则算到当天。"""
    run_date = run_date or date.today()
    end = previous_weekday(run_date) if through_previous_trading_day else run_date
    ids = repo.all_portfolio_ids()
    rows = 0
    failed = 0
    for portfolio_id in ids:
        try:
            rows += portfolio_service.recompute_nav(portfolio_id, end=end)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("nav recompute failed portfolio=%s: %s", portfolio_id, exc)
    return {"portfolios": len(ids), "failed": failed, "nav_rows": rows, "end": end.isoformat()}


def post_close_pipeline(run_date: date | None = None) -> dict:
    """手动「立即更新」时一次跑完：汇率 → 收盘价 → 净值。"""
    fx = snapshot_fx_rates(run_date)
    closes = sync_close_prices(run_date)
    nav = recompute_all_nav(run_date, through_previous_trading_day=False)
    return {"fx": fx, "closes": closes, "nav": nav}
