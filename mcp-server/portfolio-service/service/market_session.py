"""各市场的交易时段，用来决定一只证券现在该用实时价还是收盘价。

原则：正在交易用实时价；尚未开盘或已经收盘，用最近一个已结束交易日的收盘价。
不处理节假日，周末视为休市。午休仍算在交易时段内，沿用最近一笔成交价。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")

# (时区, 开盘, 收盘)。收盘时刻本身不再算交易时段。
_SESSIONS: dict[str, tuple[ZoneInfo, time, time]] = {
    "SH": (SH, time(9, 30), time(15, 0)),
    "SZ": (SH, time(9, 30), time(15, 0)),
    "BJ": (SH, time(9, 30), time(15, 0)),
    "HK": (SH, time(9, 30), time(16, 0)),
    "US": (NY, time(9, 30), time(16, 0)),
}


def _session(market: str) -> tuple[ZoneInfo, time, time]:
    return _SESSIONS.get((market or "").upper(), _SESSIONS["SH"])


def exchange_zone(market: str) -> ZoneInfo:
    return _session(market)[0]


def session_in_progress(market: str, now: datetime) -> bool:
    """当地时间落在开盘（含）到收盘（不含）之间，且是工作日。"""
    zone, start, end = _session(market)
    local = now.astimezone(zone)
    if local.weekday() >= 5:
        return False
    return start <= local.time() < end


def after_bell(market: str, now: datetime) -> bool:
    """当地已经过了今天的收盘时刻。"""
    zone, _start, end = _session(market)
    local = now.astimezone(zone)
    return local.weekday() < 5 and local.time() >= end


def last_closed_session_date(market: str, now: datetime) -> date:
    """最近一个已经结束的交易日，用该市场的本地日期。"""
    zone, _start, _end = _session(market)
    local = now.astimezone(zone)
    if after_bell(market, now):
        return local.date()
    return _previous_weekday(local.date())


def _previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def mark_price(
    market: str,
    now: datetime,
    *,
    live: Decimal | None,
    prev_close: Decimal | None,
    closes: list[tuple[date, Decimal]] | None = None,
) -> tuple[Decimal | None, date | None, bool]:
    """返回 (价格, 价格所属交易日, 是否为交易时段内的实时价)。

    交易时段用实时价。开盘前：沪深京/港股用行情昨收，避开集合竞价；美股不能照搬——
    腾讯美股 ``prev_close`` 在下一交易日开盘前仍是「上一笔行情的昨收」，会比最近收盘再早一天，
    因此优先用已入库收盘价，没有则用现价字段（收盘后到开盘前通常就是官方收盘）。
    收盘后：沪深京和港股的行情现价就是收盘价；美股盘后现价可能偏离官方收盘，优先用已入库的收盘价。
    """
    by_date = {day: price for day, price in (closes or [])}
    closed = last_closed_session_date(market, now)
    cached = by_date.get(closed)
    market_code = (market or "").upper()
    if session_in_progress(market, now):
        if live is not None:
            return live, now.astimezone(exchange_zone(market)).date(), True
        if cached is not None:
            return cached, closed, False
        return prev_close, closed if prev_close is not None else None, False
    if after_bell(market, now):
        if market_code == "US" and cached is not None:
            return cached, closed, False
        if market_code != "US" and live is not None:
            return live, closed, False
        if cached is not None:
            return cached, closed, False
        if live is not None:
            return live, closed, False
        return prev_close, closed if prev_close is not None else None, False
    # 开盘前 / 周末
    if market_code == "US":
        if cached is not None:
            return cached, closed, False
        if live is not None:
            return live, closed, False
        return prev_close, closed if prev_close is not None else None, False
    if prev_close is not None:
        return prev_close, closed, False
    if cached is not None:
        return cached, closed, False
    return None, None, False
