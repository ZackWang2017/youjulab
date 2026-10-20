"""行情数据源：腾讯实时报价、腾讯日 K 收盘价、新浪外汇牌价。

全部走 httpx，不依赖 akshare。失败时返回空值，由调用方回退到缓存收盘价。
ticker_code 统一为 ``600000.SH`` / ``00700.HK`` / ``AAPL.US`` / ``CNY.CASH``。
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx

log = logging.getLogger("portfolio.market")

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_SINA_FX_URL = "https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny"
_BATCH = 60
# 腾讯美股：实时报价用裸代码 ``usSPY``；日 K 要带交易所后缀（NASDAQ .OQ / NYSE .N / Arca·AMEX .AM）
# 才能拿到完整历史，裸代码只返回最近一天。后缀未知时逐个尝试，取行数最多的结果。
_US_KLINE_SUFFIXES = (".OQ", ".N", ".AM", "")

CASH_MARKET = "CASH"
CASH_CURRENCIES = ("CNY", "HKD", "USD")
CASH_NAMES = {"CNY": "人民币现金 (CNY)", "HKD": "港元现金 (HKD)", "USD": "美元现金 (USD)"}
MARKET_NAMES = {"SH": "上交所", "SZ": "深交所", "BJ": "北交所", "HK": "港交所", "US": "美股", CASH_MARKET: "现金"}


@dataclass(slots=True)
class LiveQuote:
    ticker_code: str
    price: Decimal
    fetched_at: datetime
    prev_close: Decimal | None = None   # 上一收盘价
    change: Decimal | None = None        # 相对上一收盘价的涨跌额（行情源提供）
    change_pct: Decimal | None = None    # 涨跌幅，小数（行情源提供，如 -0.0134）
    year_change_pct: Decimal | None = None  # 相对上年末收盘价的涨跌幅（行情源提供）


@dataclass(slots=True)
class FxRates:
    usd_cny: Decimal
    hkd_cny: Decimal
    fetched_at: datetime

    def rate_to_cny(self, currency: str) -> Decimal:
        c = (currency or "").upper()
        if c == "CNY":
            return Decimal("1")
        if c == "USD":
            return self.usd_cny
        if c == "HKD":
            return self.hkd_cny
        return Decimal("0")

    def as_dict(self) -> dict[str, Decimal]:
        return {"USD": self.usd_cny, "HKD": self.hkd_cny}


def to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            return Decimal(repr(value))
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "-"}:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def split_ticker(ticker_code: str) -> tuple[str, str]:
    """``600000.SH`` → ``("600000", "SH")``；没有后缀时按 A 股规则猜市场。"""
    text = (ticker_code or "").strip().upper()
    if "." in text:
        code, market = text.rsplit(".", 1)
        return code, market
    if text.isdigit():
        if len(text) == 5:
            return text, "HK"
        return text, _detect_a_market(text)
    return text, "US"


def _detect_a_market(code: str) -> str:
    if code.startswith(("60", "68", "9", "11", "13")):
        return "SH"
    if code.startswith(("00", "30", "20", "12", "15", "16", "18")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88", "92", "4")):
        return "BJ"
    return "SH"


def currency_of(market_code: str, code: str) -> str:
    mc = (market_code or "").upper()
    sc = (code or "").strip().upper()
    if mc == CASH_MARKET:
        return sc or "CNY"
    if mc == "US":
        return "USD"
    if mc == "HK":
        return "HKD"
    if mc == "SH" and sc.startswith("9"):
        return "USD"
    if mc == "SZ" and sc.startswith("200"):
        return "HKD"
    return "CNY"


def tencent_symbols(ticker_code: str) -> list[str]:
    """实时报价用的腾讯代码。"""
    code, market = split_ticker(ticker_code)
    if market == "SH":
        return [f"sh{code}"]
    if market == "SZ":
        return [f"sz{code}"]
    if market == "BJ":
        return [f"bj{code}"]
    if market == "HK":
        return [f"hk{code.zfill(5)}"]
    if market == "US":
        return [f"us{code.upper()}"]
    return []


_us_kline_symbol: dict[str, str] = {}  # 已确认可用的美股日 K 代码（含后缀），进程内缓存


def tencent_kline_symbols(ticker_code: str) -> list[str]:
    """日 K 用的腾讯代码；美股需要尝试多个交易所后缀。"""
    code, market = split_ticker(ticker_code)
    if market == "US":
        return [f"us{code.upper()}{suffix}" for suffix in _US_KLINE_SUFFIXES]
    return tencent_symbols(ticker_code)


def _parse_tencent_payload(body: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for raw in body.splitlines():
        line = raw.strip().rstrip(";")
        if not line.startswith("v_"):
            continue
        eq = line.find("=")
        if eq < 0:
            continue
        symbol = line[2:eq]
        fields = line[eq + 1:].strip().strip('"')
        if fields:
            out[symbol] = fields.split("~")
    return out


# 沪深京行情（约 88 列）的 [62] 是年初至今涨跌幅。港股同样有 78 列，但 [62] 是近 5 日涨跌，
# 例如 2026-10-16 腾讯控股 [62]=-1.65，对应的是 10-09 收盘 424.8 到 417.8，不是相对上年末。
_YEAR_CHANGE_MARKETS = {"SH", "SZ", "BJ"}


def _quote_changes(fields: list[str], *, market: str) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """腾讯行情里现成的涨跌字段：4 上一收盘、31 涨跌额、32 涨跌幅(%)。

    涨跌幅源数据是百分数，这里转成小数。年初至今只采信沪深京的 [62]；
    港股和美股的同下标是别的指标，交给调用方用上年末收盘价计算。
    """
    prev_close = to_decimal(fields[4]) if len(fields) > 4 else None
    change = to_decimal(fields[31]) if len(fields) > 32 else None
    change_pct = to_decimal(fields[32]) if len(fields) > 32 else None
    year_pct = to_decimal(fields[62]) if market in _YEAR_CHANGE_MARKETS and len(fields) > 62 else None
    pct = lambda v: v / Decimal(100) if v is not None else None
    return prev_close, change, pct(change_pct), pct(year_pct)


def fetch_live_quotes(tickers: Iterable[str], *, timeout: float = 10.0) -> dict[str, LiveQuote]:
    """批量实时价。返回 ``{ticker_code: LiveQuote}``，拿不到的不出现在结果里。"""
    wanted = [t for t in dict.fromkeys(tickers) if split_ticker(t)[1] != CASH_MARKET]
    if not wanted:
        return {}
    symbol_to_ticker: dict[str, str] = {}
    for ticker in wanted:
        for symbol in tencent_symbols(ticker):
            symbol_to_ticker[symbol] = ticker
    symbols = list(symbol_to_ticker)
    now = datetime.now()
    out: dict[str, LiveQuote] = {}
    headers = {"User-Agent": _UA, "Referer": "https://gu.qq.com/"}
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for i in range(0, len(symbols), _BATCH):
                chunk = symbols[i:i + _BATCH]
                try:
                    response = client.get(_TENCENT_QUOTE_URL + ",".join(chunk))
                except httpx.HTTPError as exc:
                    log.warning("tencent quote failed: %s", exc)
                    continue
                if response.status_code != 200:
                    continue
                parsed = _parse_tencent_payload(response.content.decode("gbk", errors="replace"))
                for symbol, fields in parsed.items():
                    ticker = symbol_to_ticker.get(symbol)
                    if not ticker or ticker in out or len(fields) <= 3:
                        continue
                    price = to_decimal(fields[3])
                    if price is None or price <= 0:
                        continue
                    prev_close, change, change_pct, year_pct = _quote_changes(fields, market=split_ticker(ticker)[1])
                    out[ticker] = LiveQuote(
                        ticker_code=ticker, price=price, fetched_at=now,
                        prev_close=prev_close, change=change, change_pct=change_pct, year_change_pct=year_pct,
                    )
    except Exception as exc:  # noqa: BLE001
        log.warning("tencent quote batch error: %s", exc)
    return out


def fetch_daily_closes(ticker_code: str, start: date, end: date, *, timeout: float = 15.0) -> list[tuple[date, Decimal]]:
    """腾讯日 K（前复权）收盘价，按日期正序。失败返回空列表。"""
    if start > end:
        return []
    days = max(1, (end - start).days + 5)
    headers = {"User-Agent": _UA, "Referer": "https://gu.qq.com/"}
    best: list[tuple[date, Decimal]] = []
    best_symbol = ""
    symbols = tencent_kline_symbols(ticker_code)
    remembered = _us_kline_symbol.get(ticker_code)
    if remembered in symbols:
        symbols = [remembered] + [s for s in symbols if s != remembered]
    for symbol in symbols:
        params = {"param": f"{symbol},day,{start.isoformat()},{end.isoformat()},{min(days, 800)},qfq"}
        try:
            response = httpx.get(_TENCENT_KLINE_URL, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("tencent kline failed %s: %s", symbol, exc)
            continue
        data = ((payload or {}).get("data") or {}).get(symbol) or {}
        rows = data.get("qfqday") or data.get("day") or []
        out: list[tuple[date, Decimal]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            try:
                day = datetime.strptime(str(row[0])[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            close = to_decimal(row[2])
            if close is None or close <= 0:
                continue
            if start <= day <= end:
                out.append((day, close))
        if len(out) > len(best):
            best = out
            best_symbol = symbol
        if len(out) > 1:
            # 多于一行说明后缀正确（错误后缀 / 裸代码只回最近一天），记住以后直接用
            _us_kline_symbol[ticker_code] = symbol
            break
    if best and ticker_code not in _us_kline_symbol and len(symbols) > 1:
        _us_kline_symbol[ticker_code] = best_symbol
    return best


def fetch_previous_year_close(ticker_code: str, year: int) -> tuple[date, Decimal] | None:
    """``year`` 之前最后一个交易日的收盘价，用作年涨跌的基准。"""
    start = date(year - 1, 12, 1)
    end = date(year - 1, 12, 31)
    rows = fetch_daily_closes(ticker_code, start, end)
    if not rows:
        return None
    return rows[-1]


_fx_lock = threading.Lock()
_fx_cache: FxRates | None = None
_FX_TTL = timedelta(minutes=5)
_FX_LINE = re.compile(r'var\s+hq_str_(fx_[a-z]+)="([^"]*)"')


def _fetch_sina_fx() -> FxRates | None:
    headers = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn"}
    try:
        response = httpx.get(_SINA_FX_URL, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("sina fx failed: %s", exc)
        return None
    if response.status_code != 200:
        return None
    body = response.content.decode("gbk", errors="replace")
    parsed: dict[str, Decimal] = {}
    for match in _FX_LINE.finditer(body):
        fields = match.group(2).split(",")
        if len(fields) < 4:
            continue
        value = to_decimal(fields[3])
        if value is not None and value > 0:
            parsed[match.group(1)] = value
    usd = parsed.get("fx_susdcny")
    hkd = parsed.get("fx_shkdcny")
    if usd is None or hkd is None:
        return None
    return FxRates(usd_cny=usd, hkd_cny=hkd, fetched_at=datetime.now())


def get_fx_rates(*, force: bool = False) -> FxRates:
    """5 分钟进程内缓存；拿不到时退回旧值或保守默认值。"""
    global _fx_cache
    with _fx_lock:
        if not force and _fx_cache and datetime.now() - _fx_cache.fetched_at < _FX_TTL:
            return _fx_cache
        fresh = _fetch_sina_fx()
        if fresh:
            _fx_cache = fresh
            return fresh
        if _fx_cache:
            return _fx_cache
        log.error("sina fx unreachable, using default rates")
        return FxRates(usd_cny=Decimal("7.15"), hkd_cny=Decimal("0.92"), fetched_at=datetime.now())


# ---------------------------------------------------------------------------
# 无风险利率：中国 10 年期国债收益率
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RiskFreeRate:
    rate: Decimal          # 年化小数，如 0.01648
    as_of: date            # 收益率对应日期
    source: str            # 数据来源展示名
    fetched_at: datetime

    def as_dict(self) -> dict:
        return {"rate": self.rate, "as_of": self.as_of, "source": self.source, "fetched_at": self.fetched_at}


_EASTMONEY_YIELD_URL = "https://datacenter.eastmoney.com/api/data/get"
_EASTMONEY_CGB_10Y_FIELD = "EMM00166466"  # 中国国债收益率 10 年（与 akshare bond_zh_us_rate 字段一致）
_SINA_CGB_10Y_URL = "https://hq.sinajs.cn/list=globalbd_gcny10"
_rf_lock = threading.Lock()
_rf_cache: RiskFreeRate | None = None
_RF_TTL = timedelta(hours=1)


def _fetch_eastmoney_cgb_10y() -> RiskFreeRate | None:
    params = {"type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL", "st": "SOLAR_DATE", "sr": "-1", "p": "1", "ps": "1"}
    headers = {"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"}
    try:
        response = httpx.get(_EASTMONEY_YIELD_URL, params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        rows = ((response.json().get("result") or {}).get("data") or [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("eastmoney treasury yield failed: %s", exc)
        return None
    for row in rows:
        pct = to_decimal(row.get(_EASTMONEY_CGB_10Y_FIELD))
        if pct is None or pct <= 0:
            continue
        try:
            as_of = datetime.strptime(str(row.get("SOLAR_DATE"))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        return RiskFreeRate(rate=pct / Decimal(100), as_of=as_of, source="东方财富 · 中债国债收益率曲线", fetched_at=datetime.now())
    return None


def _fetch_sina_cgb_10y() -> RiskFreeRate | None:
    headers = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn"}
    try:
        response = httpx.get(_SINA_CGB_10Y_URL, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("sina cgb yield failed: %s", exc)
        return None
    if response.status_code != 200:
        return None
    body = response.content.decode("gbk", errors="replace")
    match = re.search(r'hq_str_globalbd_gcny10="([^"]*)"', body)
    if not match:
        return None
    fields = match.group(1).split(",")
    pct = to_decimal(fields[1]) if len(fields) > 1 else None
    if pct is None or pct <= 0:
        return None
    try:
        as_of = datetime.strptime(fields[12], "%Y-%m-%d").date() if len(fields) > 12 else date.today()
    except ValueError:
        as_of = date.today()
    return RiskFreeRate(rate=pct / Decimal(100), as_of=as_of, source="新浪财经 · 全球债市", fetched_at=datetime.now())


def get_risk_free_rate(*, force: bool = False) -> RiskFreeRate | None:
    """10 年期国债收益率，1 小时进程内缓存。东方财富为主、新浪兜底；都拿不到返回 None（界面不显示夏普）。"""
    global _rf_cache
    with _rf_lock:
        if not force and _rf_cache and datetime.now() - _rf_cache.fetched_at < _RF_TTL:
            return _rf_cache
        fresh = _fetch_eastmoney_cgb_10y() or _fetch_sina_cgb_10y()
        if fresh:
            _rf_cache = fresh
        return _rf_cache
