"""投资组合业务：组合增删改、持仓买卖与现金进出、实时市值、净值与收益统计。"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import market_quote_repository as market
import portfolio_repository as repo
from nav_service import (
    MIN_RISK_OBSERVATIONS, DatedSeries, NavPoint, PricingContext, compute_nav_series, previous_weekday, risk_metrics, xirr,
)

log = logging.getLogger("portfolio.service")

ZERO = Decimal("0")
ONE = Decimal("1")
Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")
SECURITY_ACTIONS = {"add", "reduce", "close"}
CASH_ACTIONS = {"deposit", "withdraw", "dividend"}
MAX_PORTFOLIOS = 30


class PortfolioError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise PortfolioError(f"{field} 不是有效数字。") from None
    if not number.is_finite():
        raise PortfolioError(f"{field} 不是有效数字。")
    if positive and number <= 0:
        raise PortfolioError(f"{field} 必须大于 0。")
    return number


def _when(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            try:
                moment = datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                raise PortfolioError("发生时间格式无效。") from None
    if moment.tzinfo is None:
        from zoneinfo import ZoneInfo

        moment = moment.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if moment > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise PortfolioError("发生时间不能晚于现在。")
    return moment


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _q(value: Decimal | None, quantum: Decimal = Q4) -> Decimal | None:
    return value.quantize(quantum, ROUND_HALF_UP) if value is not None else None


def cash_ticker(currency: str) -> str:
    return f"{currency.upper()}.CASH"


def is_cash_ticker(ticker: str) -> bool:
    return (ticker or "").upper().endswith(".CASH")


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------

def list_portfolios(owner_id: str) -> list[dict]:
    return [_json(row) for row in repo.list_portfolios(owner_id)]


def create_portfolio(owner_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise PortfolioError("组合名称不能为空。")
    if len(name) > 80:
        raise PortfolioError("组合名称最多 80 个字符。")
    if len(repo.list_portfolios(owner_id)) >= MAX_PORTFOLIOS:
        raise PortfolioError(f"最多创建 {MAX_PORTFOLIOS} 个组合。")
    return _json(repo.create_portfolio(owner_id, name))


def rename_portfolio(owner_id: str, portfolio_id: int, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise PortfolioError("组合名称不能为空。")
    row = repo.rename_portfolio(owner_id, portfolio_id, name[:80])
    if not row:
        raise PortfolioError("组合不存在。", 404)
    return _json(row)


def delete_portfolio(owner_id: str, portfolio_id: int) -> None:
    if not repo.delete_portfolio(owner_id, portfolio_id):
        raise PortfolioError("组合不存在。", 404)


def _portfolio(owner_id: str, portfolio_id: int) -> dict:
    row = repo.get_portfolio(owner_id, portfolio_id)
    if not row:
        raise PortfolioError("组合不存在。", 404)
    return row


# ---------------------------------------------------------------------------
# 证券解析
# ---------------------------------------------------------------------------

def resolve_security(ticker_code: str, name: str | None = None) -> dict:
    """把用户选中的 ticker 变成 {ticker_code, name, market_code, currency}。"""
    code, market_code = market.split_ticker(ticker_code)
    if not code:
        raise PortfolioError("请选择证券或现金品种。")
    if market_code == market.CASH_MARKET:
        currency = code.upper()
        if currency not in market.CASH_CURRENCIES:
            raise PortfolioError("现金币种仅支持 CNY / HKD / USD。")
        return {
            "ticker_code": cash_ticker(currency),
            "name": market.CASH_NAMES[currency],
            "market_code": market.CASH_MARKET,
            "currency": currency,
        }
    if market_code not in market.MARKET_NAMES:
        raise PortfolioError(f"不支持的市场：{market_code}")
    ticker = f"{code}.{market_code}"
    display = (name or "").strip()
    if not display:
        try:
            import security_search_repository as securities

            found = securities.fetch_by_tickers([ticker])
            if found:
                display = found[0]["name"]
        except Exception as exc:  # noqa: BLE001
            log.warning("securities lookup failed: %s", exc)
    if not display and market_code != "US":
        # A/H 股必须能在证券表里找到，防止手滑输错代码；美股 / ETF 允许手动输入（证券表可能未同步）
        raise PortfolioError(f"未找到证券 {ticker}，请从搜索结果中选择。", 404)
    return {
        "ticker_code": ticker,
        "name": display or code,
        "market_code": market_code,
        "currency": market.currency_of(market_code, code),
    }


# ---------------------------------------------------------------------------
# 现金结算
# ---------------------------------------------------------------------------

def _cash_holding(portfolio_id: int, currency: str, *, create: bool, when: datetime) -> dict | None:
    ticker = cash_ticker(currency)
    holding = repo.find_open_holding(portfolio_id, ticker)
    if holding or not create:
        return holding
    return repo.insert_holding(
        portfolio_id,
        {
            "ticker_code": ticker,
            "name": market.CASH_NAMES.get(currency, f"{currency} 现金"),
            "market_code": market.CASH_MARKET,
            "currency": currency,
            "shares": ZERO,
            "avg_cost": ONE,
            "opened_at": when,
        },
    )


def _settle_cash(portfolio_id: int, currency: str, amount: Decimal, *, direction: str, when: datetime, note: str | None) -> None:
    """买入扣现金（direction=out）/ 卖出入现金（direction=in），记一条内部结算流水。"""
    holding = _cash_holding(portfolio_id, currency, create=direction == "in", when=when)
    if direction == "out":
        if not holding or Decimal(holding["shares"]) < amount:
            have = Decimal(holding["shares"]) if holding else ZERO
            raise PortfolioError(
                f"{currency} 现金不足（现有 {have.quantize(Q2)}，需要 {amount.quantize(Q2)}）。请先存入现金，或取消「用组合现金结算」。"
            )
        repo.update_holding(holding["id"], shares=Decimal(holding["shares"]) - amount)
    else:
        repo.update_holding(holding["id"], shares=Decimal(holding["shares"]) + amount)
    repo.insert_transaction(
        portfolio_id,
        {
            "holding_id": holding["id"],
            "ticker_code": holding["ticker_code"],
            "currency": currency,
            "action": "cash_out" if direction == "out" else "cash_in",
            "price": ONE,
            "shares": amount,
            "amount": amount,
            "external_flow": ZERO,
            "occurred_at": when,
            "note": note,
        },
    )


# ---------------------------------------------------------------------------
# 买入 / 增减持 / 清仓 / 现金进出
# ---------------------------------------------------------------------------

def _refresh_nav_quietly(portfolio_id: int) -> None:
    """流水变化后顺手重算净值，失败不影响交易本身。"""
    try:
        recompute_nav(portfolio_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("nav refresh after transaction failed portfolio=%s: %s", portfolio_id, exc)


def buy(owner_id: str, portfolio_id: int, body: dict) -> dict:
    result = _buy(owner_id, portfolio_id, body)
    _refresh_nav_quietly(portfolio_id)
    return result


def transact(owner_id: str, portfolio_id: int, holding_id: int, body: dict) -> dict:
    result = _transact(owner_id, portfolio_id, holding_id, body)
    _refresh_nav_quietly(portfolio_id)
    return result


def _buy(owner_id: str, portfolio_id: int, body: dict) -> dict:
    _portfolio(owner_id, portfolio_id)
    security = resolve_security(str(body.get("ticker_code") or ""), body.get("name"))
    when = _when(body.get("occurred_at"))
    note = (body.get("note") or "").strip() or None
    is_cash = security["market_code"] == market.CASH_MARKET
    shares = _decimal(body.get("shares"), "金额" if is_cash else "数量")
    price = ONE if is_cash else _decimal(body.get("price"), "价格")
    target = body.get("target_allocation_pct")
    target_pct = _decimal(target, "目标占比", positive=False) if target not in (None, "") else None
    if target_pct is not None and not (ZERO <= target_pct <= Decimal("100")):
        raise PortfolioError("目标占比需在 0-100 之间。")

    existing = repo.find_open_holding(portfolio_id, security["ticker_code"])
    if is_cash:
        holding = existing or _cash_holding(portfolio_id, security["currency"], create=True, when=when)
        return _apply_cash(portfolio_id, holding, "deposit", shares, when, note, target_pct)

    settle = bool(body.get("settle_with_cash"))
    amount = price * shares
    if settle:
        _settle_cash(portfolio_id, security["currency"], amount, direction="out", when=when, note=note)
    if existing:
        if target_pct is not None:
            repo.update_holding(existing["id"], target_allocation_pct=target_pct)
        return _apply_security(portfolio_id, existing, "add", price, shares, when, note, settle)
    holding = repo.insert_holding(
        portfolio_id,
        {**security, "shares": shares, "avg_cost": price, "target_allocation_pct": target_pct, "opened_at": when},
    )
    repo.insert_transaction(
        portfolio_id,
        {
            "holding_id": holding["id"],
            "ticker_code": holding["ticker_code"],
            "currency": holding["currency"],
            "action": "buy",
            "price": price,
            "shares": shares,
            "amount": amount,
            "external_flow": ZERO if settle else amount,
            "occurred_at": when,
            "note": note,
        },
    )
    return _json(repo.get_holding(portfolio_id, holding["id"]))


def _transact(owner_id: str, portfolio_id: int, holding_id: int, body: dict) -> dict:
    _portfolio(owner_id, portfolio_id)
    holding = repo.get_holding(portfolio_id, holding_id)
    if not holding:
        raise PortfolioError("持仓不存在。", 404)
    action = str(body.get("action") or "").strip()
    when = _when(body.get("occurred_at"))
    note = (body.get("note") or "").strip() or None
    target = body.get("target_allocation_pct")
    target_pct = _decimal(target, "目标占比", positive=False) if target not in (None, "") else None
    if holding["market_code"] == market.CASH_MARKET:
        if action not in CASH_ACTIONS:
            raise PortfolioError("现金品种只能存入、取出或记分红。")
        amount = _decimal(body.get("shares", body.get("amount")), "金额")
        if action == "dividend":
            return _apply_dividend(portfolio_id, holding, amount, when, body.get("source_ticker"), note)
        return _apply_cash(portfolio_id, holding, action, amount, when, note, target_pct)
    if action not in SECURITY_ACTIONS:
        raise PortfolioError("action 必须是 add / reduce / close。")
    shares = _decimal(body.get("shares"), "数量")
    price = _decimal(body.get("price"), "价格")
    settle = bool(body.get("settle_with_cash"))
    if action == "add" and settle:
        _settle_cash(portfolio_id, holding["currency"], price * shares, direction="out", when=when, note=note)
    if target_pct is not None:
        repo.update_holding(holding["id"], target_allocation_pct=target_pct)
    return _apply_security(portfolio_id, holding, action, price, shares, when, note, settle)


def _apply_dividend(portfolio_id: int, holding: dict, amount: Decimal, when: datetime, source_ticker: str | None, note: str | None) -> dict:
    """分红入账：现金增加，但不计外部资金流入（外部投入只在建仓时计一次，分红是持仓收益）。

    分红来源标的写进流水备注 ``分红：名称（代码）``，统计时按备注归集到该标的。
    """
    source = _dividend_source(portfolio_id, source_ticker)
    label = f"分红：{source['name']}（{source['ticker_code']}）"
    text = f"{label}；{note}" if note else label
    if len(text) > 200:
        text = text[:200]
    balance = Decimal(holding["shares"]) + amount
    repo.update_holding(holding["id"], shares=balance)
    repo.insert_transaction(
        portfolio_id,
        {
            "holding_id": holding["id"],
            "ticker_code": holding["ticker_code"],
            "currency": holding["currency"],
            "action": "dividend",
            "price": ONE,
            "shares": amount,
            "amount": amount,
            "external_flow": ZERO,
            "occurred_at": when,
            "note": text,
        },
    )
    return _json(repo.get_holding(portfolio_id, holding["id"]))


_DIVIDEND_NOTE = re.compile(r"^分红：(.+)（([0-9A-Za-z]+\.[A-Z]+)）")


def _dividend_source(portfolio_id: int, source_ticker: str | None) -> dict:
    ticker = (source_ticker or "").strip().upper()
    if not ticker:
        raise PortfolioError("请选择分红来源标的。")
    code, market_code = market.split_ticker(ticker)
    ticker = f"{code}.{market_code}"
    held = repo.find_open_holding(portfolio_id, ticker)
    if held:
        return {"ticker_code": ticker, "name": held["name"]}
    # 已清仓的标的也可能补记历史分红
    for row in repo.list_holdings(portfolio_id, status=None):
        if row["ticker_code"] == ticker:
            return {"ticker_code": ticker, "name": row["name"]}
    raise PortfolioError(f"该组合没有持有 {ticker}，无法记分红。", 404)


def dividend_summary(transactions: list[dict], *, year: int, fx: "market.FxRates") -> dict:
    """从分红流水归集统计：累计 / 今年，按来源标的分组，金额折算人民币。

    股息率 = 今年该标的分红(CNY) / 当前持仓成本(CNY)，由调用方用持仓成本补齐。
    """
    groups: dict[str, dict] = {}
    total = year_total = ZERO
    for tx in transactions:
        if tx["action"] != "dividend":
            continue
        match = _DIVIDEND_NOTE.match(tx.get("note") or "")
        name, ticker = (match.group(1), match.group(2)) if match else ("未标明标的", "")
        amount = Decimal(tx["amount"])
        amount_cny = amount * fx.rate_to_cny(tx["currency"])
        day = tx["occurred_at"].date() if hasattr(tx["occurred_at"], "date") else tx["occurred_at"]
        group = groups.setdefault(ticker, {"ticker_code": ticker, "name": name, "total_cny": ZERO, "year_cny": ZERO, "count": 0, "last_date": None})
        group["total_cny"] += amount_cny
        group["count"] += 1
        if group["last_date"] is None or day > group["last_date"]:
            group["last_date"] = day
        total += amount_cny
        if day.year == year:
            group["year_cny"] += amount_cny
            year_total += amount_cny
    items = sorted(groups.values(), key=lambda g: g["year_cny"], reverse=True)
    return {"total_cny": total, "year_cny": year_total, "items": items}


def _apply_cash(portfolio_id: int, holding: dict, action: str, amount: Decimal, when: datetime, note: str | None, target_pct: Decimal | None) -> dict:
    balance = Decimal(holding["shares"])
    if action == "withdraw":
        if amount > balance:
            raise PortfolioError(f"取出金额不能超过现有现金 {balance.quantize(Q2)}。")
        balance -= amount
        flow = -amount
    else:
        balance += amount
        flow = amount
    fields: dict[str, Any] = {"shares": balance}
    if target_pct is not None:
        fields["target_allocation_pct"] = target_pct
    repo.update_holding(holding["id"], **fields)
    repo.insert_transaction(
        portfolio_id,
        {
            "holding_id": holding["id"],
            "ticker_code": holding["ticker_code"],
            "currency": holding["currency"],
            "action": action,
            "price": ONE,
            "shares": amount,
            "amount": amount,
            "external_flow": flow,
            "occurred_at": when,
            "note": note,
        },
    )
    return _json(repo.get_holding(portfolio_id, holding["id"]))


def _apply_security(portfolio_id: int, holding: dict, action: str, price: Decimal, shares: Decimal, when: datetime, note: str | None, settle: bool) -> dict:
    if holding["status"] != "open":
        raise PortfolioError("持仓已清仓。")
    current = Decimal(holding["shares"])
    avg_cost = Decimal(holding["avg_cost"])
    amount = price * shares
    fields: dict[str, Any] = {}
    if action == "add":
        total = current + shares
        fields["avg_cost"] = ((current * avg_cost + amount) / total).quantize(Q6) if total > 0 else ZERO
        fields["shares"] = total
        flow = ZERO if settle else amount
    elif action == "reduce":
        if shares >= current:
            raise PortfolioError("减持数量不能大于等于现有数量，请使用清仓。")
        fields["shares"] = current - shares
        flow = ZERO if settle else -amount
    else:  # close
        shares = current
        amount = price * shares
        fields.update(shares=ZERO, status="closed", closed_at=when)
        flow = ZERO if settle else -amount
    if action in ("reduce", "close") and settle:
        _settle_cash(portfolio_id, holding["currency"], amount, direction="in", when=when, note=note)
    repo.update_holding(holding["id"], **fields)
    repo.insert_transaction(
        portfolio_id,
        {
            "holding_id": holding["id"],
            "ticker_code": holding["ticker_code"],
            "currency": holding["currency"],
            "action": action,
            "price": price,
            "shares": shares,
            "amount": amount,
            "external_flow": flow,
            "occurred_at": when,
            "note": note,
        },
    )
    return _json(repo.get_holding(portfolio_id, holding["id"]))


def list_transactions(owner_id: str, portfolio_id: int, holding_id: int | None = None, limit: int = 200) -> list[dict]:
    _portfolio(owner_id, portfolio_id)
    return [_json(row) for row in repo.list_transactions(portfolio_id, holding_id=holding_id, limit=limit)]


# ---------------------------------------------------------------------------
# 实时市值
# ---------------------------------------------------------------------------

_CASH_ORDER = ("CNY", "HKD", "USD")


def _security_types(tickers: list[str]) -> dict[str, str]:
    """证券表里的类型（股票 / 基金），用来把 ETF 标成「美股ETF」/「场内基金」。查不到不影响展示。"""
    if not tickers:
        return {}
    try:
        import security_search_repository as securities

        return {row["ticker_code"]: row.get("security_type") or "" for row in securities.fetch_by_tickers(tickers)}
    except Exception as exc:  # noqa: BLE001
        log.warning("security type lookup failed: %s", exc)
        return {}


def _market_label(market_code: str, security_type: str | None) -> str:
    base = market.MARKET_NAMES.get(market_code, market_code)
    if security_type != "基金":
        return base
    if market_code == "US":
        return f"{base}ETF"
    if market_code in ("SH", "SZ"):
        return "场内基金"  # 沪深 ETF / LOF
    return base


ALL_PORTFOLIOS_NAME = "全部持仓统计"


def _aggregate_payload(portfolios: list[dict]) -> dict:
    return {
        "id": 0,
        "name": ALL_PORTFOLIOS_NAME,
        "aggregate": True,
        "portfolio_count": len(portfolios),
        "portfolio_names": [p["name"] for p in portfolios],
    }


def _merge_holdings(portfolios: list[dict]) -> list[dict]:
    """把所有组合的在持仓按 ticker 合并：数量相加、成本按数量加权，并记录来源组合。"""
    merged: dict[str, dict] = {}
    for portfolio in portfolios:
        for h in repo.list_holdings(int(portfolio["id"]), status="open"):
            shares = Decimal(h["shares"])
            if shares <= 0:
                continue
            cost = shares * Decimal(h["avg_cost"])
            item = merged.get(h["ticker_code"])
            if item is None:
                merged[h["ticker_code"]] = {
                    **h,
                    "id": None,
                    "shares": shares,
                    "_cost": cost,
                    "target_allocation_pct": None,
                    "portfolios": [portfolio["name"]],
                }
                continue
            item["shares"] += shares
            item["_cost"] += cost
            item["opened_at"] = min(item["opened_at"], h["opened_at"])
            if portfolio["name"] not in item["portfolios"]:
                item["portfolios"].append(portfolio["name"])
    for item in merged.values():
        item["avg_cost"] = item.pop("_cost") / item["shares"]
    return list(merged.values())


def _year_start_prices(tickers: list[str], year: int) -> dict[str, Decimal]:
    """上年末收盘价。库里没有时向行情源补一条，供港股、美股计算年涨跌。"""
    if not tickers:
        return {}
    bases = repo.year_start_closes(tickers, year)
    fetched: list[tuple[str, date, Decimal]] = []
    for ticker in tickers:
        if ticker in bases:
            continue
        found = market.fetch_previous_year_close(ticker, year)
        if found is None:
            continue
        day, close = found
        bases[ticker] = close
        fetched.append((ticker, day, close))
    if fetched:
        repo.upsert_close_prices(fetched)
    return bases


def holdings_overview(owner_id: str, portfolio_id: int, *, refresh_live: bool = True) -> dict:
    portfolio = _portfolio(owner_id, portfolio_id)
    rows = repo.list_holdings(portfolio_id, status="open")
    return _render_overview(portfolio, rows, refresh_live=refresh_live)


def all_holdings_overview(owner_id: str, *, refresh_live: bool = True) -> dict:
    """「全部持仓统计」：用户所有组合的在持仓合并后的实时市值。"""
    portfolios = repo.list_portfolios(owner_id)
    rows = _merge_holdings(portfolios)
    return _render_overview(_aggregate_payload(portfolios), rows, refresh_live=refresh_live)


def _render_overview(portfolio: dict, rows: list[dict], *, refresh_live: bool) -> dict:
    fx = market.get_fx_rates()
    fx_out = {"usd_cny": fx.usd_cny, "hkd_cny": fx.hkd_cny, "fetched_at": fx.fetched_at}
    today = date.today()

    live: dict[str, market.LiveQuote] = {}
    if refresh_live:
        live = market.fetch_live_quotes([r["ticker_code"] for r in rows if r["market_code"] != market.CASH_MARKET])
    security_types = _security_types([r["ticker_code"] for r in rows if r["market_code"] != market.CASH_MARKET])
    # 港股、美股行情不提供年初至今涨跌幅，用上年末收盘价计算
    need_year_base = [
        r["ticker_code"] for r in rows
        if r["market_code"] != market.CASH_MARKET
        and not (live.get(r["ticker_code"]) and live[r["ticker_code"]].year_change_pct is not None)
    ]
    year_bases = _year_start_prices(need_year_base, today.year)

    rendered: list[dict] = []
    total_value_cny = ZERO
    total_cost_cny = ZERO
    # 组合级涨跌：由各标的市值与涨跌幅反推「上一收盘 / 年初」市值后加总
    day_change_cny = ZERO
    year_change_cny = ZERO
    had_day_change = False
    had_year_change = False
    for h in rows:
        currency = h["currency"]
        shares = Decimal(h["shares"])
        avg_cost = Decimal(h["avg_cost"])
        is_cash = h["market_code"] == market.CASH_MARKET
        price: Decimal | None
        source = "none"
        trade_date: date | None = None
        quote = live.get(h["ticker_code"])
        if is_cash:
            price, source, trade_date = ONE, "cash", today
        elif quote:
            price, source, trade_date = quote.price, "live", today
        else:
            cached = repo.latest_close(h["ticker_code"])
            if cached:
                trade_date, price = cached
                source = "cache_today" if trade_date == today else "cache_last"
            else:
                price = None
        change = change_pct = year_change_pct = None
        if not is_cash and price is not None:
            if quote and quote.change_pct is not None:
                change, change_pct = quote.change, quote.change_pct
            elif quote and quote.prev_close and quote.prev_close > 0:
                change = price - quote.prev_close
                change_pct = change / quote.prev_close
            if quote and quote.year_change_pct is not None:
                year_change_pct = quote.year_change_pct
            elif year_bases.get(h["ticker_code"]):
                year_change_pct = price / year_bases[h["ticker_code"]] - ONE
        rate = fx.rate_to_cny(currency)
        market_value = shares * price if price is not None else None
        cost = shares * avg_cost
        profit = (market_value - cost) if market_value is not None else None
        return_rate = (profit / cost) if profit is not None and cost > 0 else None
        market_value_cny = market_value * rate if market_value is not None else None
        profit_cny = profit * rate if profit is not None else None
        cost_cny = cost * rate
        if market_value_cny is not None:
            total_value_cny += market_value_cny
            if change_pct is not None and change_pct > -ONE:
                day_change_cny += market_value_cny * change_pct / (ONE + change_pct)
                had_day_change = True
            if year_change_pct is not None and year_change_pct > -ONE:
                year_change_cny += market_value_cny * year_change_pct / (ONE + year_change_pct)
                had_year_change = True
        total_cost_cny += cost_cny
        rendered.append(
            {
                "id": h["id"],
                "ticker_code": h["ticker_code"],
                "stock_code": market.split_ticker(h["ticker_code"])[0],
                "market_code": h["market_code"],
                "market_name": _market_label(h["market_code"], security_types.get(h["ticker_code"])),
                "name": h["name"],
                "currency": currency,
                "shares": shares,
                "avg_cost": avg_cost,
                "status": h["status"],
                "opened_at": h["opened_at"],
                "current_price": _q(price),
                "price_source": source,
                "price_trade_date": trade_date,
                "change": _q(change),
                "change_pct": _q(change_pct, Q6),
                "year_change_pct": _q(year_change_pct, Q6),
                "market_value": _q(market_value),
                "profit": _q(profit),
                "return_rate": _q(return_rate),
                "market_value_cny": _q(market_value_cny),
                "profit_cny": _q(profit_cny),
                "current_allocation_pct": None,
                "target_allocation_pct": _q(Decimal(h["target_allocation_pct"]), Q2) if h.get("target_allocation_pct") is not None else None,
                "portfolios": h.get("portfolios"),
                "_mv_cny": market_value_cny,
            }
        )

    total_target = ZERO
    total_current = ZERO
    for item in rendered:
        mv = item.pop("_mv_cny")
        if mv is not None and total_value_cny > 0:
            pct = mv / total_value_cny * Decimal("100")
            item["current_allocation_pct"] = _q(pct, Q2)
            total_current += pct
        if item["target_allocation_pct"] is not None:
            total_target += item["target_allocation_pct"]

    securities = [i for i in rendered if i["market_code"] != market.CASH_MARKET]
    cash = sorted(
        [i for i in rendered if i["market_code"] == market.CASH_MARKET],
        key=lambda i: _CASH_ORDER.index(i["currency"]) if i["currency"] in _CASH_ORDER else 9,
    )
    total_profit = total_value_cny - total_cost_cny
    day_base = total_value_cny - day_change_cny
    year_base = total_value_cny - year_change_cny
    summary = {
        "total_market_value_cny": _q(total_value_cny),
        "total_cost_cny": _q(total_cost_cny),
        "total_profit_cny": _q(total_profit),
        "overall_return_rate": _q(total_profit / total_cost_cny) if total_cost_cny > 0 else None,
        # 组合市值相对上一收盘的增加额与涨跌幅（含现金拖累）
        "total_change_cny": _q(day_change_cny) if had_day_change else None,
        "total_change_pct": _q(day_change_cny / day_base, Q6) if had_day_change and day_base > 0 else None,
        # 组合市值相对年初（或上年末收盘）的涨跌幅
        "total_year_change_pct": _q(year_change_cny / year_base, Q6) if had_year_change and year_base > 0 else None,
        "total_target_allocation_pct": _q(total_target, Q2),
        "total_current_allocation_pct": _q(total_current, Q2),
        "holdings_count": len(rendered),
    }
    return _json({"portfolio": portfolio, "holdings": securities + cash, "summary": summary, "fx": fx_out})


# ---------------------------------------------------------------------------
# 净值 / 收益统计
# ---------------------------------------------------------------------------

def recompute_nav(portfolio_id: int, *, end: date | None = None) -> int:
    """按流水 + 已存收盘价 + 汇率重算该组合的全部每日净值，并落库。"""
    transactions = repo.transactions_in_order(portfolio_id)
    if not transactions:
        repo.replace_nav_series(portfolio_id, [])
        return 0
    points = _nav_points(transactions, end=end)
    return repo.replace_nav_series(portfolio_id, [p.as_row() for p in points])


def _nav_points(transactions: list[dict], *, end: date | None = None) -> list[NavPoint]:
    """对任意一组流水（可跨组合）按净值法计算每日净值，不落库。"""
    if not transactions:
        return []
    end = end or date.today()
    first = min(t["occurred_at"] for t in transactions).date() - timedelta(days=7)
    tickers = sorted({t["ticker_code"] for t in transactions if not is_cash_ticker(t["ticker_code"])})
    closes = {t: DatedSeries(points) for t, points in repo.close_prices_between(tickers, first, end).items()}
    fx_rows = repo.fx_rates_between(first, end)
    live_fx = market.get_fx_rates()
    pricing = PricingContext(
        closes=closes,
        fx={currency: DatedSeries(points) for currency, points in fx_rows.items()},
        fallback_fx=live_fx.as_dict(),
    )
    return compute_nav_series(transactions, pricing, end=end, is_cash=is_cash_ticker)


def stats(owner_id: str, portfolio_id: int, *, refresh: bool = False) -> dict:
    portfolio = _portfolio(owner_id, portfolio_id)
    if refresh:
        recompute_nav(portfolio_id)
    series = repo.nav_series(portfolio_id)
    overview = holdings_overview(owner_id, portfolio_id, refresh_live=True)
    return _stats_payload(
        portfolio, series, Decimal(overview["summary"]["total_market_value_cny"]),
        dividends=_dividends(repo.transactions_in_order(portfolio_id), overview),
    )


def all_stats(owner_id: str) -> dict:
    """「全部持仓统计」：把用户所有组合的流水合在一起按净值法即时计算，不落库。"""
    portfolios = repo.list_portfolios(owner_id)
    transactions: list[dict] = []
    for portfolio in portfolios:
        transactions.extend(repo.transactions_in_order(int(portfolio["id"])))
    series = [p.as_row() for p in _nav_points(transactions)]
    now = datetime.now(timezone.utc)
    for row in series:
        row["computed_at"] = now
    overview = all_holdings_overview(owner_id, refresh_live=True)
    return _stats_payload(
        _aggregate_payload(portfolios), series, Decimal(overview["summary"]["total_market_value_cny"]),
        dividends=_dividends(transactions, overview),
    )


def _year_to_date(series: list[dict], nav_now: Decimal | None, total_value_live: Decimal, *, today: date | None = None) -> dict:
    """今年以来的统计，全部由每日净值记录推出，不需要额外存表。

    年初基准 = 上一年最后一条净值记录（净值、总市值）；组合是今年才建仓的则以建仓日为基准
    （净值 1、市值 0，``since_inception=True``）。今年新增 / 取出 = 今年各日外部资金流入 / 流出之和；
    今年净浮盈 = 当前市值 − 年初市值 − 今年新增 + 今年取出。
    """
    today = today or date.today()
    year_start = date(today.year, 1, 1)
    baseline = None
    for row in series:
        if row["nav_date"] < year_start:
            baseline = row
        else:
            break
    inflow = ZERO
    outflow = ZERO
    for row in series:
        if row["nav_date"] < year_start:
            continue
        flow = Decimal(row["net_flow_cny"])
        if flow > 0:
            inflow += flow
        elif flow < 0:
            outflow += -flow
    if baseline is not None:
        start_nav = Decimal(baseline["nav"])
        start_value = Decimal(baseline["total_value_cny"])
        start_date = baseline["nav_date"]
        since_inception = False
    else:
        start_nav = ONE if series else None
        start_value = ZERO
        start_date = series[0]["nav_date"] if series else None
        since_inception = bool(series)
    growth = (nav_now / start_nav - ONE) if nav_now is not None and start_nav else None
    profit = (total_value_live - start_value - inflow + outflow) if series else None
    return {
        "year": today.year,
        "start_date": start_date,
        "start_nav": start_nav,
        "start_value_cny": start_value if series else None,
        "growth_rate": growth,
        "inflow_cny": inflow if series else None,
        "outflow_cny": outflow if series else None,
        "profit_cny": profit,
        "since_inception": since_inception,
    }


def _dividends(transactions: list[dict], overview: dict) -> dict:
    """分红统计：按来源标的分组，并按当前持仓成本算股息率（今年分红 / 持仓成本）。"""
    summary = dividend_summary(transactions, year=date.today().year, fx=market.get_fx_rates())
    cost_by_ticker: dict[str, Decimal] = {}
    for h in overview["holdings"]:
        if h["market_code"] == market.CASH_MARKET or h["market_value_cny"] is None:
            continue
        cost = Decimal(h["shares"]) * Decimal(h["avg_cost"]) * market.get_fx_rates().rate_to_cny(h["currency"])
        cost_by_ticker[h["ticker_code"]] = cost
    for item in summary["items"]:
        cost = cost_by_ticker.get(item["ticker_code"])
        item["cost_cny"] = _q(cost) if cost else None
        item["yield_rate"] = _q(item["year_cny"] / cost, Q6) if cost and cost > 0 else None
        item["total_cny"] = _q(item["total_cny"])
        item["year_cny"] = _q(item["year_cny"])
    summary["total_cny"] = _q(summary["total_cny"])
    summary["year_cny"] = _q(summary["year_cny"])
    return summary


def _stats_payload(portfolio: dict, series: list[dict], total_value_live: Decimal, *, dividends: dict | None = None) -> dict:
    latest = series[-1] if series else None
    previous = series[-2] if len(series) >= 2 else None
    nav_now = Decimal(latest["nav"]) if latest else None
    nav_prev = Decimal(previous["nav"]) if previous else None
    growth = (nav_now / nav_prev - ONE) if nav_now is not None and nav_prev else None

    cum_in = Decimal(latest["cum_invested_cny"]) if latest else ZERO
    cum_out = Decimal(latest["cum_withdrawn_cny"]) if latest else ZERO

    # 用实时市值再算一次「当前」XIRR，曲线用每日落库值
    flows: list[tuple[date, Decimal]] = []
    for row in series:
        flow = Decimal(row["net_flow_cny"])
        if flow:
            flows.append((row["nav_date"], -flow))
    live_xirr = xirr(flows + [(date.today(), total_value_live)]) if flows else None
    ytd = _year_to_date(series, nav_now, total_value_live)

    # 波动率 / 夏普：日收益率来自每日净值；无风险利率取 10 年期国债收益率
    risk_free = market.get_risk_free_rate()
    risk = risk_metrics(
        [Decimal(row["daily_return"]) if row.get("daily_return") is not None else None for row in series],
        risk_free.rate if risk_free else None,
    )

    payload = {
        "portfolio": portfolio,
        "nav": {
            "current": _q(nav_now, Q4),
            "previous": _q(nav_prev, Q4),
            "growth_rate": _q(growth, Q6),
            "as_of": latest["nav_date"] if latest else None,
            "previous_date": previous["nav_date"] if previous else None,
            "computed_at": latest["computed_at"] if latest else None,
            "year_start": _q(ytd["start_nav"], Q4),
            "year_start_date": ytd["start_date"],
            "ytd_growth_rate": _q(ytd["growth_rate"], Q6),
            "ytd_since_inception": ytd["since_inception"],
        },
        "totals": {
            "cum_invested_cny": _q(cum_in),
            "cum_withdrawn_cny": _q(cum_out),
            "total_market_value_cny": _q(total_value_live),
            "net_invested_cny": _q(cum_in - cum_out),
            "total_profit_cny": _q(total_value_live - (cum_in - cum_out)),
            "year_start_value_cny": _q(ytd["start_value_cny"]),
            "ytd_inflow_cny": _q(ytd["inflow_cny"]),
            "ytd_outflow_cny": _q(ytd["outflow_cny"]),
            "ytd_profit_cny": _q(ytd["profit_cny"]),
        },
        "year": ytd["year"],
        "dividends": dividends or {"total_cny": ZERO, "year_cny": ZERO, "items": []},
        "xirr": {
            "current": _q(live_xirr, Q6),
            "as_of": date.today(),
        },
        "risk": {
            "volatility": _q(risk["volatility"], Q6),
            "sharpe": _q(risk["sharpe"], Q4),
            "annualized_return": _q(risk["annualized_return"], Q6),
            "observations": risk["observations"],
            "min_observations": MIN_RISK_OBSERVATIONS,
            "risk_free": risk_free.as_dict() if risk_free else None,
        },
        "series": [
            {
                "date": row["nav_date"],
                "nav": row["nav"],
                "daily_return": row["daily_return"],
                "total_value_cny": row["total_value_cny"],
                "net_flow_cny": row["net_flow_cny"],
                "xirr": row["xirr"],
            }
            for row in series
        ],
        "previous_trading_day": previous_weekday(date.today()),
    }
    return _json(payload)
