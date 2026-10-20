"""净值法与 XIRR 的纯计算，不访问数据库，便于单测。

净值法（份额法）：
  - 初始净值 1.0；某日有外部资金进出 F（折算 CNY，流入为正）时，按前一日净值折算份额：units += F / nav_prev；
  - 当日净值 nav = 当日总市值 V / units；日收益率 = nav / nav_prev - 1。
XIRR：把所有外部流水取反（投入为负、取出为正）加上截至当日的总市值作为终值，求年化内部收益率。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

ZERO = Decimal("0")
ONE = Decimal("1")
_Q_VALUE = Decimal("0.0001")
_Q_NAV = Decimal("0.000001")
_Q_UNITS = Decimal("0.00000001")
_Q_RATE = Decimal("0.00000001")

# 持仓数量增加的动作；其余动作减少
_INCREASE_ACTIONS = {"buy", "add", "deposit", "cash_in", "dividend"}
_DECREASE_ACTIONS = {"reduce", "close", "withdraw", "cash_out"}


@dataclass(slots=True)
class Flow:
    """一笔外部资金进出（已折算 CNY，流入为正）。"""

    day: date
    amount_cny: Decimal


@dataclass(slots=True)
class NavPoint:
    nav_date: date
    total_value_cny: Decimal
    net_flow_cny: Decimal
    units: Decimal
    nav: Decimal
    daily_return: Decimal | None
    cum_invested_cny: Decimal
    cum_withdrawn_cny: Decimal
    xirr: Decimal | None

    def as_row(self) -> dict:
        return {
            "nav_date": self.nav_date,
            "total_value_cny": self.total_value_cny,
            "net_flow_cny": self.net_flow_cny,
            "units": self.units,
            "nav": self.nav,
            "daily_return": self.daily_return,
            "cum_invested_cny": self.cum_invested_cny,
            "cum_withdrawn_cny": self.cum_withdrawn_cny,
            "xirr": self.xirr,
        }


class DatedSeries:
    """按日期查「当日或之前最近一条」的序列。"""

    def __init__(self, points: Iterable[tuple[date, Decimal]]):
        ordered = sorted(points, key=lambda p: p[0])
        self._dates = [p[0] for p in ordered]
        self._values = [p[1] for p in ordered]

    def __bool__(self) -> bool:
        return bool(self._dates)

    def dates(self) -> list[date]:
        return list(self._dates)

    def at(self, day: date) -> Decimal | None:
        idx = bisect_right(self._dates, day)
        if idx == 0:
            return None
        return self._values[idx - 1]


@dataclass(slots=True)
class PricingContext:
    closes: dict[str, DatedSeries] = field(default_factory=dict)
    fx: dict[str, DatedSeries] = field(default_factory=dict)
    fallback_fx: dict[str, Decimal] = field(default_factory=dict)

    def rate_to_cny(self, currency: str, day: date) -> Decimal:
        currency = (currency or "CNY").upper()
        if currency == "CNY":
            return ONE
        series = self.fx.get(currency)
        value = series.at(day) if series else None
        if value is None:
            value = self.fallback_fx.get(currency)
        return value if value is not None else ZERO


def local_day(moment: datetime, tz: str = "Asia/Shanghai") -> date:
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(ZoneInfo(tz)).date()


def _shares_delta(action: str, shares: Decimal) -> Decimal:
    if action in _INCREASE_ACTIONS:
        return shares
    if action in _DECREASE_ACTIONS:
        return -shares
    return ZERO


def compute_nav_series(
    transactions: Sequence[dict],
    pricing: PricingContext,
    *,
    end: date,
    tz: str = "Asia/Shanghai",
    is_cash: callable = lambda ticker: ticker.endswith(".CASH"),
) -> list[NavPoint]:
    """根据流水 + 收盘价 + 汇率，重算从首笔流水到 ``end`` 的每日净值。

    transactions 每项至少含 ``occurred_at``（datetime）、``ticker_code``、``currency``、``action``、
    ``shares``、``price``、``external_flow``（持仓币种计价，流入为正）。
    """
    if not transactions:
        return []
    ordered = sorted(transactions, key=lambda t: (t["occurred_at"], t.get("id", 0)))
    tx_by_day: dict[date, list[dict]] = {}
    for tx in ordered:
        tx_by_day.setdefault(local_day(tx["occurred_at"], tz), []).append(tx)
    first_day = min(tx_by_day)
    if end < first_day:
        end = first_day

    # 评估日期：流水日 ∪ 持有证券的收盘价日期 ∪ end
    days: set[date] = {d for d in tx_by_day if d <= end}
    days.add(end)
    tickers = {tx["ticker_code"] for tx in ordered}
    for ticker in tickers:
        series = pricing.closes.get(ticker)
        if series:
            days.update(d for d in series.dates() if first_day <= d <= end)
    eval_days = sorted(days)

    positions: dict[str, Decimal] = {}
    last_tx_price: dict[str, Decimal] = {}
    currency_of: dict[str, str] = {}
    units = ZERO
    nav_prev = ONE
    cum_in = ZERO
    cum_out = ZERO
    flows: list[Flow] = []
    out: list[NavPoint] = []

    for day in eval_days:
        flow_cny = ZERO
        funding_day = units <= 0  # 建仓日（或清仓后重新建仓日）
        for tx in tx_by_day.get(day, []):
            ticker = tx["ticker_code"]
            shares = Decimal(tx["shares"])
            positions[ticker] = positions.get(ticker, ZERO) + _shares_delta(tx["action"], shares)
            currency_of[ticker] = tx["currency"]
            price = Decimal(tx["price"]) if tx.get("price") is not None else None
            if price is not None and price > 0:
                last_tx_price[ticker] = price
            external = Decimal(tx.get("external_flow") or 0)
            if external:
                flow_cny += external * pricing.rate_to_cny(tx["currency"], day)

        if flow_cny:
            if flow_cny > 0:
                cum_in += flow_cny
            else:
                cum_out += -flow_cny
            flows.append(Flow(day=day, amount_cny=flow_cny))
            units += flow_cny / nav_prev

        value = ZERO
        for ticker, qty in positions.items():
            if qty == 0:
                continue
            if is_cash(ticker):
                price = ONE
            else:
                series = pricing.closes.get(ticker)
                price = series.at(day) if series else None
                if price is None:
                    price = last_tx_price.get(ticker, ZERO)
            value += qty * price * pricing.rate_to_cny(currency_of.get(ticker, "CNY"), day)

        if units <= 0:
            # 全部取出或尚无资金：净值沿用前值，份额归零
            units = ZERO
            nav = nav_prev
            daily = None
        elif funding_day and flow_cny > 0 and value > 0:
            # 建仓日：以当日总市值作为份额基准，首日净值恒为 1（重新建仓则沿用前值）。
            # 成交价与当日收盘价的差异计入份额而不是净值；累计投入仍按实际资金记录。
            units = value / nav_prev
            nav = nav_prev
            daily = None if not out else ZERO
        else:
            nav = value / units
            daily = (nav / nav_prev - ONE) if nav_prev else None
        xirr_value = xirr([(f.day, -f.amount_cny) for f in flows] + [(day, value)]) if flows else None
        out.append(
            NavPoint(
                nav_date=day,
                total_value_cny=value.quantize(_Q_VALUE, ROUND_HALF_UP),
                net_flow_cny=flow_cny.quantize(_Q_VALUE, ROUND_HALF_UP),
                units=units.quantize(_Q_UNITS, ROUND_HALF_UP),
                nav=nav.quantize(_Q_NAV, ROUND_HALF_UP),
                daily_return=daily.quantize(_Q_RATE, ROUND_HALF_UP) if daily is not None else None,
                cum_invested_cny=cum_in.quantize(_Q_VALUE, ROUND_HALF_UP),
                cum_withdrawn_cny=cum_out.quantize(_Q_VALUE, ROUND_HALF_UP),
                xirr=xirr_value.quantize(_Q_RATE, ROUND_HALF_UP) if xirr_value is not None else None,
            )
        )
        nav_prev = nav
    return out


MIN_XIRR_SPAN_DAYS = 7
MAX_XIRR = 10.0  # 年化 1000% 以上视为噪声（持有期太短），不展示


def _bounded(rate: float) -> Decimal | None:
    if rate <= -0.999999 or rate > MAX_XIRR:
        return None
    return Decimal(repr(rate))


def xirr(cashflows: Sequence[tuple[date, Decimal]], *, guess: float = 0.1) -> Decimal | None:
    """年化内部收益率。至少要有一笔负（投入）和一笔正（取出/终值）现金流，否则返回 None。

    持有期不足 ``MIN_XIRR_SPAN_DAYS`` 天时年化没有意义，返回 None。
    """
    flows = [(d, float(a)) for d, a in cashflows if a]
    if len(flows) < 2:
        return None
    if not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    t1 = max(d for d, _ in flows)
    if (t1 - t0).days < MIN_XIRR_SPAN_DAYS:
        return None
    times = [((d - t0).days / 365.0, a) for d, a in flows]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for t, a in times)

    def dnpv(rate: float) -> float:
        return sum(-t * a / (1.0 + rate) ** (t + 1) for t, a in times)

    rate = guess
    for _ in range(60):
        try:
            value = npv(rate)
            slope = dnpv(rate)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(value) < 1e-7:
            return _bounded(rate)
        if slope == 0:
            break
        step = value / slope
        new_rate = rate - step
        if new_rate <= -0.999999:
            new_rate = (rate - 0.999999) / 2
        if abs(new_rate - rate) < 1e-10:
            rate = new_rate
            break
        rate = new_rate
    try:
        if abs(npv(rate)) < 1e-5 and rate > -0.999999:
            return _bounded(rate)
    except (OverflowError, ZeroDivisionError):
        pass

    # 牛顿法不收敛时用二分法兜底
    low, high = -0.999999, MAX_XIRR
    try:
        f_low, f_high = npv(low), npv(high)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7 or (high - low) < 1e-10:
            return _bounded(mid)
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return _bounded((low + high) / 2)


TRADING_DAYS_PER_YEAR = 252
MIN_RISK_OBSERVATIONS = 10  # 少于 10 个日收益样本时波动率 / 夏普没有统计意义


def risk_metrics(daily_returns: Sequence[Decimal | None], risk_free_annual: Decimal | None) -> dict:
    """年化波动率与夏普比率。

    - 波动率 = 日收益率样本标准差 × √252
    - 夏普 = (日均收益 − 无风险日收益) / 日收益标准差 × √252，无风险利率按 252 个交易日折算到日
    返回 {volatility, sharpe, annualized_return, observations}，样本不足或无风险利率缺失时对应值为 None。
    """
    samples = [float(r) for r in daily_returns if r is not None]
    n = len(samples)
    out: dict = {"volatility": None, "sharpe": None, "annualized_return": None, "observations": n}
    if n < MIN_RISK_OBSERVATIONS:
        return out
    mean = sum(samples) / n
    variance = sum((r - mean) ** 2 for r in samples) / (n - 1)
    std = variance ** 0.5
    root = TRADING_DAYS_PER_YEAR ** 0.5
    out["volatility"] = Decimal(repr(std * root))
    out["annualized_return"] = Decimal(repr(mean * TRADING_DAYS_PER_YEAR))
    if risk_free_annual is not None and std > 0:
        rf_daily = float(risk_free_annual) / TRADING_DAYS_PER_YEAR
        out["sharpe"] = Decimal(repr((mean - rf_daily) / std * root))
    return out


def previous_weekday(day: date) -> date:
    """上一交易日的近似：往前找最近的工作日（不处理节假日，收盘价缺失时会自动向前结转）。"""
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor
