"""收盘后任务：补齐持仓证券的日收盘价、快照当日汇率、重算所有组合的每日净值。

这些函数都是同步阻塞的，交给 scheduler 在线程里跑；也可以通过 REST 手动触发。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import market_quote_repository as market
import portfolio_repository as repo
import portfolio_service
from market_session import exchange_zone, session_in_progress
from nav_service import previous_weekday

log = logging.getLogger("portfolio.sync")

_BACKFILL_DAYS = 10
# 最近这段每次都重拉并覆盖。盘中同步会把当时价格写成「当日收盘」，
# 若之后只补最新日期之后的缺口，这个未收盘价会留在库里，净值日增长就和行情涨跌幅对不上。
_REVISE_DAYS = 10
_SLEEP_BETWEEN = 0.15


def close_sync_start(last: date | None, first_held: date, end: date) -> date:
    """新证券从建仓往前补一段；已有收盘价时，除了补缺口，还要回头覆盖最近几天。"""
    revise_from = end - timedelta(days=_REVISE_DAYS)
    if last is None:
        return first_held - timedelta(days=_BACKFILL_DAYS)
    return min(last + timedelta(days=1), revise_from)


def _finished_session_bars(market_code: str, closes: list[tuple[date, object]], now: datetime) -> list:
    """交易时段内的当日 K 线还不是收盘价，不能写进收盘价表。"""
    if not session_in_progress(market_code, now):
        return list(closes)
    local_today = now.astimezone(exchange_zone(market_code)).date()
    return [(day, close) for day, close in closes if day != local_today]


def sync_close_prices(
    run_date: date | None = None,
    *,
    tickers: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """对所有组合里出现过的证券补齐日收盘价，并覆盖最近几天已写入的价格。"""
    end = run_date or date.today()
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    held = repo.held_tickers_everywhere()
    if tickers:
        wanted = set(tickers)
        held = [h for h in held if h["ticker_code"] in wanted]
    scanned = ok = failed = inserted = 0
    for item in held:
        ticker = item["ticker_code"]
        scanned += 1
        last = repo.last_close_date(ticker)
        start = close_sync_start(last, item["first_date"], end)
        if start > end:
            ok += 1
            continue
        try:
            closes = market.fetch_daily_closes(ticker, start, end)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("close sync failed %s: %s", ticker, exc)
            continue
        market_code = item.get("market_code") or market.split_ticker(ticker)[1]
        finished = _finished_session_bars(market_code, closes, now)
        if finished:
            inserted += repo.upsert_close_prices((ticker, day, close) for day, close in finished)
        ok += 1
        time.sleep(_SLEEP_BETWEEN)
    return {"scanned": scanned, "ok": ok, "failed": failed, "rows_inserted": inserted, "end": end.isoformat()}


def snapshot_fx_rates(run_date: date | None = None) -> dict:
    day = run_date or date.today()
    rates = market.get_fx_rates(force=True)
    repo.upsert_fx_rates(day, rates.as_dict())
    return {"date": day.isoformat(), "usd_cny": str(rates.usd_cny), "hkd_cny": str(rates.hkd_cny)}


def recompute_all_nav(run_date: date | None = None, *, through_previous_trading_day: bool = False) -> dict:
    """算到 run_date 当天。已收盘的市场用当日收盘价；还没开盘的市场没有新收盘，沿用上一交易日。"""
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
