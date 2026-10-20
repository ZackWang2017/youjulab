"""持仓管理：净值法、XIRR、行情解析、计划任务调度与 REST 入口。全部不访问数据库和外网。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "portfolio-service" / "repository"))
sys.path.insert(0, str(HERE / "portfolio-service" / "service"))
sys.path.insert(0, str(HERE / "portfolio-service" / "endpoints"))
sys.path.insert(0, str(HERE / "financial-data-service" / "repository"))

import market_quote_repository as market  # noqa: E402
import portfolio_endpoint  # noqa: E402
import portfolio_service as service  # noqa: E402
from nav_service import DatedSeries, PricingContext, compute_nav_series, previous_weekday, xirr  # noqa: E402
from scheduler.daily_scheduler import DailyTask, DailyTaskScheduler, MemoryRunStore  # noqa: E402

TZ = timezone.utc


def tx(day: str, ticker: str, action: str, shares: str, price: str = "1", flow: str = "0", currency: str = "CNY", **extra):
    return {
        "id": extra.get("id", 0),
        "occurred_at": datetime.fromisoformat(f"{day}T03:00:00").replace(tzinfo=TZ),
        "ticker_code": ticker,
        "currency": currency,
        "action": action,
        "shares": D(shares),
        "price": D(price),
        "external_flow": D(flow),
    }


class NavMethodTests(unittest.TestCase):
    def test_units_follow_flows_and_nav_follows_value(self):
        rows = [
            tx("2026-01-05", "CNY.CASH", "deposit", "10000", flow="10000", id=1),
            tx("2026-01-05", "CNY.CASH", "cash_out", "5000", id=2),
            tx("2026-01-05", "600000.SH", "buy", "500", price="10", id=3),
            tx("2026-01-07", "CNY.CASH", "deposit", "1000", flow="1000", id=4),
        ]
        pricing = PricingContext(closes={"600000.SH": DatedSeries([
            (date(2026, 1, 5), D("10")), (date(2026, 1, 6), D("11")), (date(2026, 1, 7), D("12")),
        ])})
        points = compute_nav_series(rows, pricing, end=date(2026, 1, 7))
        self.assertEqual([p.nav_date for p in points], [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)])
        self.assertEqual(points[0].nav, D("1.000000"))
        self.assertEqual(points[0].total_value_cny, D("10000.0000"))
        # 第二天股价 10→11：5000 现金 + 500×11 = 10500 → 净值 1.05
        self.assertEqual(points[1].nav, D("1.050000"))
        self.assertEqual(points[1].daily_return, D("0.05000000"))
        # 第三天追加 1000 元：按前一日净值 1.05 折算份额，不抬高净值
        self.assertEqual(points[2].units, (D("10000") + D("1000") / D("1.05")).quantize(D("0.00000001")))
        self.assertEqual(points[2].total_value_cny, D("12000.0000"))
        self.assertEqual(points[2].cum_invested_cny, D("11000.0000"))
        self.assertEqual(points[2].net_flow_cny, D("1000.0000"))

    def test_withdrawal_reduces_units_and_price_carries_forward(self):
        rows = [
            tx("2026-02-02", "00700.HK", "buy", "100", price="400", flow="40000", currency="HKD", id=1),
            tx("2026-02-10", "00700.HK", "reduce", "50", price="440", flow="-22000", currency="HKD", id=2),
        ]
        pricing = PricingContext(
            closes={"00700.HK": DatedSeries([(date(2026, 2, 2), D("400")), (date(2026, 2, 9), D("440"))])},
            fx={"HKD": DatedSeries([(date(2026, 2, 1), D("0.9"))])},
        )
        points = compute_nav_series(rows, pricing, end=date(2026, 2, 10))
        by_date = {p.nav_date: p for p in points}
        self.assertEqual(by_date[date(2026, 2, 2)].total_value_cny, D("36000.0000"))
        self.assertEqual(by_date[date(2026, 2, 9)].nav, D("1.100000"))
        last = by_date[date(2026, 2, 10)]
        # 2-10 没有收盘价，沿用 2-9 的 440：剩余 50 股 × 440 × 0.9 = 19800
        self.assertEqual(last.total_value_cny, D("19800.0000"))
        self.assertEqual(last.cum_withdrawn_cny, D("19800.0000"))
        self.assertEqual(last.nav, D("1.100000"))

    def test_inception_day_units_equal_market_value_and_nav_is_one(self):
        # 建仓日成本 371.687，当日收盘 417.8：份额 = 当日总市值，净值恒为 1；累计投入仍按实际资金
        rows = [tx("2026-10-17", "00700.HK", "buy", "4400", price="371.687", flow="1635422.8", currency="HKD", id=1)]
        pricing = PricingContext(
            closes={"00700.HK": DatedSeries([(date(2026, 10, 16), D("417.8")), (date(2026, 10, 19), D("440"))])},
            fallback_fx={"HKD": D("0.852028")},
        )
        points = compute_nav_series(rows, pricing, end=date(2026, 10, 19))
        first = points[0]
        market_value = D("4400") * D("417.8") * D("0.852028")
        self.assertEqual(first.nav, D("1.000000"))
        self.assertIsNone(first.daily_return)
        self.assertEqual(first.total_value_cny, market_value.quantize(D("0.0001")))
        self.assertEqual(first.units, market_value.quantize(D("0.00000001")))
        self.assertEqual(first.cum_invested_cny, (D("1635422.8") * D("0.852028")).quantize(D("0.0001")))
        self.assertEqual(points[-1].nav, (D("440") / D("417.8")).quantize(D("0.000001")))

    def test_missing_close_falls_back_to_last_transaction_price(self):
        rows = [tx("2026-03-02", "AAPL.US", "buy", "10", price="200", flow="2000", currency="USD", id=1)]
        pricing = PricingContext(fallback_fx={"USD": D("7")})
        points = compute_nav_series(rows, pricing, end=date(2026, 3, 3))
        self.assertEqual(points[-1].total_value_cny, D("14000.0000"))
        self.assertEqual(points[-1].nav, D("1.000000"))

    def test_empty_transactions_produce_no_points(self):
        self.assertEqual(compute_nav_series([], PricingContext(), end=date.today()), [])


class XirrTests(unittest.TestCase):
    def test_single_year_double_is_100_percent(self):
        rate = xirr([(date(2025, 1, 1), D("-100")), (date(2026, 1, 1), D("200"))])
        self.assertAlmostEqual(float(rate), 1.0, places=4)

    def test_mixed_flows(self):
        rate = xirr([
            (date(2025, 1, 1), D("-1000")),
            (date(2025, 7, 1), D("-1000")),
            (date(2026, 1, 1), D("2200")),
        ])
        self.assertTrue(0.12 < float(rate) < 0.14)

    def test_requires_both_signs_and_minimum_span(self):
        self.assertIsNone(xirr([(date(2025, 1, 1), D("-100")), (date(2025, 6, 1), D("-50"))]))
        self.assertIsNone(xirr([(date(2025, 1, 1), D("-100")), (date(2025, 1, 3), D("150"))]))
        self.assertIsNone(xirr([(date(2025, 1, 1), D("-100"))]))

    def test_previous_weekday_skips_weekend(self):
        self.assertEqual(previous_weekday(date(2026, 10, 19)), date(2026, 10, 16))  # 周一 → 上周五
        self.assertEqual(previous_weekday(date(2026, 10, 17)), date(2026, 10, 16))  # 周六 → 周五
        self.assertEqual(previous_weekday(date(2026, 10, 14)), date(2026, 10, 13))


class MarketParsingTests(unittest.TestCase):
    def test_split_ticker_and_currency(self):
        self.assertEqual(market.split_ticker("600000.SH"), ("600000", "SH"))
        self.assertEqual(market.split_ticker("00700"), ("00700", "HK"))
        self.assertEqual(market.split_ticker("000001"), ("000001", "SZ"))
        self.assertEqual(market.split_ticker("aapl"), ("AAPL", "US"))
        self.assertEqual(market.currency_of("HK", "00700"), "HKD")
        self.assertEqual(market.currency_of("SH", "900901"), "USD")
        self.assertEqual(market.currency_of("SZ", "200002"), "HKD")
        self.assertEqual(market.currency_of("CASH", "USD"), "USD")
        self.assertEqual(market.currency_of("SZ", "000001"), "CNY")

    def test_tencent_symbols(self):
        self.assertEqual(market.tencent_symbols("600000.SH"), ["sh600000"])
        self.assertEqual(market.tencent_symbols("700.HK"), ["hk00700"])
        # 美股实时报价只认裸代码；日 K 需要带交易所后缀
        self.assertEqual(market.tencent_symbols("spy.US"), ["usSPY"])
        self.assertEqual(market.tencent_kline_symbols("SPY.US"), ["usSPY.OQ", "usSPY.N", "usSPY.AM", "usSPY"])
        self.assertEqual(market.tencent_kline_symbols("600000.SH"), ["sh600000"])
        self.assertEqual(market.tencent_symbols("CNY.CASH"), [])

    def test_us_kline_picks_suffix_with_full_history_and_remembers_it(self):
        full = [["2026-10-15", "770", "771.5"], ["2026-10-16", "771", "773.24"]]
        latest_only = [["2026-10-16", "771", "773.24"]]
        calls: list[str] = []

        def fake_get(url, params=None, headers=None, timeout=None):
            symbol = params["param"].split(",")[0]
            calls.append(symbol)
            rows = full if symbol == "usSPY.AM" else latest_only
            payload = {"code": 0, "data": {symbol: {"qfqday": rows}}}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        market._us_kline_symbol.pop("SPY.US", None)
        with patch.object(market.httpx, "get", fake_get):
            closes = market.fetch_daily_closes("SPY.US", date(2026, 10, 15), date(2026, 10, 16))
            self.assertEqual(closes, [(date(2026, 10, 15), D("771.5")), (date(2026, 10, 16), D("773.24"))])
            self.assertEqual(calls, ["usSPY.OQ", "usSPY.N", "usSPY.AM"])
            calls.clear()
            market.fetch_daily_closes("SPY.US", date(2026, 10, 15), date(2026, 10, 16))
            self.assertEqual(calls, ["usSPY.AM"])  # 记住了正确后缀
        market._us_kline_symbol.pop("SPY.US", None)

    def test_live_quotes_parse_gbk_payload(self):
        body = 'v_sh600000="1~浦发银行~600000~9.73~9.64~";\nv_hk00700="100~腾讯控股~00700~417.800~";\nv_usAAPL="";\n'

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url):
                return httpx.Response(200, content=body.encode("gbk"), request=httpx.Request("GET", url))

        with patch.object(market.httpx, "Client", FakeClient):
            quotes = market.fetch_live_quotes(["600000.SH", "00700.HK", "AAPL.US", "CNY.CASH"])
        self.assertEqual(quotes["600000.SH"].price, D("9.73"))
        self.assertEqual(quotes["00700.HK"].price, D("417.800"))
        self.assertNotIn("AAPL.US", quotes)

    def test_daily_closes_parse_kline(self):
        payload = {"code": 0, "data": {"sh600000": {"qfqday": [
            ["2026-10-15", "9.650", "9.640", "9.700", "9.530", "685743.000"],
            ["2026-10-16", "9.600", "9.730", "9.790", "9.530", "922942.000"],
            ["2026-10-20", "9.700", "9.800", "9.900", "9.600", "1.000"],
        ]}}}

        def fake_get(url, params=None, headers=None, timeout=None):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        with patch.object(market.httpx, "get", fake_get):
            closes = market.fetch_daily_closes("600000.SH", date(2026, 10, 15), date(2026, 10, 17))
        self.assertEqual(closes, [(date(2026, 10, 15), D("9.640")), (date(2026, 10, 16), D("9.730"))])


class UsUniverseTests(unittest.TestCase):
    def test_parse_nasdaq_traded_marks_etf_as_fund_and_skips_junk(self):
        import securities_repository as universe

        text = "\n".join([
            "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares",
            "Y|AAPL|Apple Inc. - Common Stock|Q|Q|N|100|N|N||AAPL|N",
            "Y|SPY|State Street SPDR S&P 500 ETF Trust|P| |Y|40|N||SPY|SPY|N",
            "Y|ZTEST|Test Issue|Q|Q|N|100|Y|N||ZTEST|N",
            "Y|BRK.A|Berkshire Hathaway Class A|N| |N|1|N||BRK.A|BRK.A|N",
            "File Creation Time: 1017202620:00||||||||||",
        ])
        records = universe.parse_nasdaq_traded(text)
        by_ticker = {r.ticker_code: r for r in records}
        self.assertEqual(set(by_ticker), {"AAPL.US", "SPY.US"})
        self.assertEqual(by_ticker["AAPL.US"].security_type, "股票")
        self.assertEqual(by_ticker["AAPL.US"].name, "Apple Inc. - Common Stock [NASDAQ]")
        self.assertEqual(by_ticker["SPY.US"].security_type, "基金")
        self.assertEqual(by_ticker["SPY.US"].name, "State Street SPDR S&P 500 ETF Trust [NYSE ARCA]")

    def test_market_label_marks_us_etf(self):
        self.assertEqual(service._market_label("US", "基金"), "美股ETF")
        self.assertEqual(service._market_label("US", "股票"), "美股")
        self.assertEqual(service._market_label("SZ", "基金"), "场内基金")
        self.assertEqual(service._market_label("SZ", "股票"), "深交所")
        self.assertEqual(service._market_label("HK", "基金"), "港交所")


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryRunStore()
        self.scheduler = DailyTaskScheduler(self.store, tz="Asia/Shanghai", enabled=True)
        self.calls: list[tuple[str, date]] = []

    def _task(self, name: str, **kwargs):
        def run(day: date):
            self.calls.append((name, day))
            return {"ok": name}
        return self.scheduler.register(DailyTask(name=name, run=run, at=time(17, 0), **kwargs))

    def _at(self, text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_runs_once_after_trigger_time_on_weekdays(self):
        task = self._task("a")
        self.assertFalse(self.scheduler.is_due(task, self._at("2026-10-14T16:59:00")))
        self.assertTrue(self.scheduler.is_due(task, self._at("2026-10-14T17:00:00")))
        self.scheduler.tick(self._at("2026-10-14T17:00:30"))
        self.assertEqual(self.calls, [("a", date(2026, 10, 14))])
        self.scheduler.tick(self._at("2026-10-14T18:00:00"))
        self.assertEqual(len(self.calls), 1, "同一天不会重复执行")
        self.assertFalse(self.scheduler.is_due(task, self._at("2026-10-17T18:00:00")), "周六不触发")
        self.assertTrue(self.scheduler.is_due(task, self._at("2026-10-15T17:00:00")))

    def test_late_start_catches_up_same_day(self):
        self._task("a")
        self.scheduler.tick(self._at("2026-10-14T22:30:00"))
        self.assertEqual(self.calls, [("a", date(2026, 10, 14))])

    def test_dependency_chain_runs_in_order_within_one_tick(self):
        self._task("fx")
        self._task("closes")
        self._task("nav", depends_on=("fx", "closes"))
        self.scheduler.tick(self._at("2026-10-14T17:00:00"))
        self.assertEqual([c[0] for c in self.calls], ["fx", "closes", "nav"])

    def test_dependency_blocks_when_upstream_failed(self):
        def boom(day: date):
            raise RuntimeError("network down")
        self.scheduler.register(DailyTask(name="fx", run=boom, at=time(17, 0)))
        self._task("nav", depends_on=("fx",))
        results = self.scheduler.tick(self._at("2026-10-14T17:00:00"))
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("network down", results[0]["detail"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.get("fx", date(2026, 10, 14)).status, "failed")

    def test_failed_task_retries_after_interval_up_to_max_attempts(self):
        attempts = {"n": 0}

        def flaky(day: date):
            attempts["n"] += 1
            raise RuntimeError("flaky")
        task = self.scheduler.register(DailyTask(name="flaky", run=flaky, at=time(17, 0), retry_after=timedelta(minutes=30), max_attempts=2))
        self.scheduler.tick(self._at("2026-10-14T17:00:00"))
        self.assertFalse(self.scheduler.is_due(task, self._at("2026-10-14T17:10:00")))
        record = self.store.get("flaky", date(2026, 10, 14))
        record.finished_at = datetime(2026, 10, 14, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertTrue(self.scheduler.is_due(task, self._at("2026-10-14T17:31:00")))
        self.scheduler.tick(self._at("2026-10-14T17:31:00"))
        record = self.store.get("flaky", date(2026, 10, 14))
        record.finished_at = datetime(2026, 10, 14, 17, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertFalse(self.scheduler.is_due(task, self._at("2026-10-14T19:00:00")), "达到最大尝试次数后不再自动重试")
        self.assertEqual(attempts["n"], 2)

    def test_manual_run_skips_when_done_unless_forced(self):
        self._task("a")
        self.scheduler.run_task("a")
        skipped = self.scheduler.run_task("a", force=False)
        self.assertTrue(skipped.get("skipped"))
        forced = self.scheduler.run_task("a", force=True)
        self.assertEqual(forced["status"], "success")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.scheduler.describe()[0]["today_status"], "success")
        with self.assertRaises(KeyError):
            self.scheduler.run_task("missing")

    def test_register_rejects_unknown_dependency(self):
        with self.assertRaises(ValueError):
            self._task("nav", depends_on=("fx",))


class RiskMetricsTests(unittest.TestCase):
    def test_volatility_and_sharpe(self):
        from nav_service import risk_metrics

        returns = [D(x) for x in ("0.01", "-0.005", "0.012", "0.0", "-0.01", "0.02", "0.003", "-0.012", "0.008", "-0.004", "0.006", "-0.016")]
        out = risk_metrics(returns, D("0.01648"))
        self.assertEqual(out["observations"], 12)
        mean = sum(float(r) for r in returns) / 12
        std = (sum((float(r) - mean) ** 2 for r in returns) / 11) ** 0.5
        self.assertAlmostEqual(float(out["volatility"]), std * 252 ** 0.5, places=9)
        self.assertAlmostEqual(float(out["sharpe"]), (mean - 0.01648 / 252) / std * 252 ** 0.5, places=9)
        self.assertAlmostEqual(float(out["annualized_return"]), mean * 252, places=9)

    def test_too_few_samples_or_missing_rate(self):
        from nav_service import risk_metrics

        few = risk_metrics([D("0.01")] * 5 + [None, None], D("0.02"))
        self.assertEqual(few["observations"], 5)
        self.assertIsNone(few["volatility"])
        self.assertIsNone(few["sharpe"])
        no_rf = risk_metrics([D("0.01"), D("-0.01")] * 6, None)
        self.assertIsNotNone(no_rf["volatility"])
        self.assertIsNone(no_rf["sharpe"])

    def test_quote_changes_from_tencent_fields(self):
        fields = [""] * 63
        fields[3], fields[4], fields[31], fields[32], fields[62] = "5.16", "5.23", "-0.07", "-1.34", "-27.63"
        long_fields = fields + [""] * 25  # 沪深北约 88 列，[62] 才是年初至今
        prev, change, pct, year_pct = market._quote_changes(long_fields, market="SH")
        self.assertEqual((prev, change, pct, year_pct), (D("5.23"), D("-0.07"), D("-0.0134"), D("-0.2763")))
        # 港股正好 78 列，[62] 是近 5 日涨跌，不能当成年涨跌
        hk = fields + [""] * 15
        self.assertEqual(market._quote_changes(hk, market="HK")[3], None)
        self.assertEqual(market._quote_changes(fields, market="US")[3], None)
        self.assertEqual(market._quote_changes(["5.16"], market="SH"), (None, None, None, None))

    def test_year_start_price_fetches_missing_prior_close(self):
        with patch.object(service.repo, "year_start_closes", return_value={"600519.SH": D("1349")}), \
             patch.object(service.market, "fetch_previous_year_close", return_value=(date(2025, 12, 31), D("599"))) as fetch, \
             patch.object(service.repo, "upsert_close_prices", return_value=1) as saved:
            bases = service._year_start_prices(["600519.SH", "00700.HK"], 2026)
        fetch.assert_called_once_with("00700.HK", 2026)
        saved.assert_called_once_with([("00700.HK", date(2025, 12, 31), D("599"))])
        self.assertEqual(bases["00700.HK"], D("599"))
        self.assertEqual(bases["600519.SH"], D("1349"))

    def test_eastmoney_cgb_10y_parse(self):
        payload = {"result": {"data": [{"SOLAR_DATE": "2026-10-16 00:00:00", "EMM00588704": 1.2547, "EMM00166466": 1.648}]}}

        def fake_get(url, params=None, headers=None, timeout=None):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        with patch.object(market.httpx, "get", fake_get):
            rf = market._fetch_eastmoney_cgb_10y()
        self.assertEqual(rf.rate, D("0.01648"))
        self.assertEqual(rf.as_of, date(2026, 10, 16))


class YearToDateTests(unittest.TestCase):
    @staticmethod
    def _row(day: str, nav: str, value: str, flow: str = "0"):
        return {"nav_date": date.fromisoformat(day), "nav": D(nav), "total_value_cny": D(value), "net_flow_cny": D(flow)}

    def test_uses_last_point_of_previous_year_as_baseline(self):
        series = [
            self._row("2025-06-02", "1.000000", "100000", "100000"),
            self._row("2025-12-30", "1.200000", "120000"),
            self._row("2025-12-31", "1.250000", "125000"),
            self._row("2026-03-10", "1.300000", "180000", "50000"),   # 今年新增 5 万
            self._row("2026-07-01", "1.280000", "157000", "-20000"),  # 今年取出 2 万
            self._row("2026-10-16", "1.375000", "168000"),
        ]
        ytd = service._year_to_date(series, D("1.375"), D("170000"), today=date(2026, 10, 17))
        self.assertEqual(ytd["year"], 2026)
        self.assertEqual(ytd["start_date"], date(2025, 12, 31))
        self.assertEqual(ytd["start_nav"], D("1.25"))
        self.assertEqual(ytd["start_value_cny"], D("125000"))
        self.assertEqual(ytd["growth_rate"], D("1.375") / D("1.25") - 1)  # +10%
        self.assertEqual(ytd["inflow_cny"], D("50000"))
        self.assertEqual(ytd["outflow_cny"], D("20000"))
        # 170000 − 125000 − 50000 + 20000
        self.assertEqual(ytd["profit_cny"], D("15000"))
        self.assertFalse(ytd["since_inception"])

    def test_portfolio_started_this_year_uses_inception(self):
        series = [
            self._row("2026-10-17", "1.000000", "3348313.75", "3507314.90"),
        ]
        ytd = service._year_to_date(series, D("1.0"), D("3400000"), today=date(2026, 10, 17))
        self.assertTrue(ytd["since_inception"])
        self.assertEqual(ytd["start_nav"], D("1"))
        self.assertEqual(ytd["start_value_cny"], D("0"))
        self.assertEqual(ytd["start_date"], date(2026, 10, 17))
        self.assertEqual(ytd["inflow_cny"], D("3507314.90"))
        self.assertEqual(ytd["profit_cny"], D("3400000") - D("3507314.90"))

    def test_empty_series(self):
        ytd = service._year_to_date([], None, D("0"), today=date(2026, 10, 17))
        self.assertIsNone(ytd["start_nav"])
        self.assertIsNone(ytd["profit_cny"])
        self.assertFalse(ytd["since_inception"])


class ServiceValidationTests(unittest.TestCase):
    def test_merge_holdings_sums_shares_and_weights_cost(self):
        opened = datetime(2026, 10, 1, tzinfo=TZ)
        by_portfolio = {
            1: [
                {"id": 11, "ticker_code": "00700.HK", "name": "腾讯", "currency": "HKD", "market_code": "HK", "shares": D("100"),
                 "avg_cost": D("400"), "status": "open", "opened_at": opened, "target_allocation_pct": D("30")},
                {"id": 12, "ticker_code": "CNY.CASH", "name": "现金", "currency": "CNY", "market_code": "CASH", "shares": D("1000"),
                 "avg_cost": D("1"), "status": "open", "opened_at": opened, "target_allocation_pct": None},
            ],
            2: [
                {"id": 21, "ticker_code": "00700.HK", "name": "腾讯", "currency": "HKD", "market_code": "HK", "shares": D("300"),
                 "avg_cost": D("440"), "status": "open", "opened_at": opened - timedelta(days=3), "target_allocation_pct": None},
            ],
        }
        with patch.object(service.repo, "list_holdings", side_effect=lambda pid, status="open": by_portfolio[pid]):
            merged = {h["ticker_code"]: h for h in service._merge_holdings([{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}])}
        tencent = merged["00700.HK"]
        self.assertEqual(tencent["shares"], D("400"))
        self.assertEqual(tencent["avg_cost"], D("430"))  # (100×400 + 300×440) / 400
        self.assertEqual(tencent["portfolios"], ["甲", "乙"])
        self.assertEqual(tencent["opened_at"], opened - timedelta(days=3))
        self.assertIsNone(tencent["id"])
        self.assertIsNone(tencent["target_allocation_pct"])
        self.assertEqual(merged["CNY.CASH"]["portfolios"], ["甲"])

    def test_dividend_note_groups_by_source_and_year(self):
        from datetime import timezone as tz

        def tx(day: str, amount: str, note: str, currency: str = "CNY"):
            return {"action": "dividend", "amount": D(amount), "currency": currency,
                    "occurred_at": datetime.fromisoformat(day).replace(tzinfo=tz.utc), "note": note}

        rows = [
            tx("2026-06-01", "1200", "分红：贵州茅台（600519.SH）；2025 年度"),
            tx("2025-06-01", "800", "分红：贵州茅台（600519.SH）"),
            tx("2026-09-01", "1000", "分红：腾讯控股（00700.HK）", currency="HKD"),
            {"action": "deposit", "amount": D("5000"), "currency": "CNY",
             "occurred_at": datetime(2026, 1, 1, tzinfo=tz.utc), "note": None},
        ]

        class Fx:
            def rate_to_cny(self, currency: str) -> D:
                return D("0.9") if currency == "HKD" else D("1")

        summary = service.dividend_summary(rows, year=2026, fx=Fx())
        self.assertEqual(summary["year_cny"], D("1200") + D("900"))
        self.assertEqual(summary["total_cny"], D("2000") + D("900"))
        by_ticker = {i["ticker_code"]: i for i in summary["items"]}
        self.assertEqual(by_ticker["600519.SH"]["count"], 2)
        self.assertEqual(by_ticker["600519.SH"]["year_cny"], D("1200"))
        self.assertEqual(by_ticker["00700.HK"]["year_cny"], D("900"))

    def test_resolve_cash_and_unknown_market(self):
        cash = service.resolve_security("hkd.cash")
        self.assertEqual(cash["ticker_code"], "HKD.CASH")
        self.assertEqual(cash["currency"], "HKD")
        with self.assertRaises(service.PortfolioError):
            service.resolve_security("JPY.CASH")
        with self.assertRaises(service.PortfolioError):
            service.resolve_security("600000.XX")

    def test_resolve_security_requires_known_a_share(self):
        with patch("security_search_repository.fetch_by_tickers", return_value=[]):
            with self.assertRaises(service.PortfolioError):
                service.resolve_security("600000.SH")
        with patch("security_search_repository.fetch_by_tickers", return_value=[{"ticker_code": "600000.SH", "name": "浦发银行"}]):
            found = service.resolve_security("600000")
        self.assertEqual(found, {"ticker_code": "600000.SH", "name": "浦发银行", "market_code": "SH", "currency": "CNY"})
        us = service.resolve_security("AAPL.US")
        self.assertEqual(us["currency"], "USD")

    def test_decimal_and_time_validation(self):
        with self.assertRaises(service.PortfolioError):
            service._decimal("abc", "价格")
        with self.assertRaises(service.PortfolioError):
            service._decimal("-1", "价格")
        self.assertEqual(service._decimal("0", "目标", positive=False), D("0"))
        when = service._when("2026-10-01")
        self.assertEqual(when.tzinfo.key, "Asia/Shanghai")
        with self.assertRaises(service.PortfolioError):
            service._when("2999-01-01")
        self.assertEqual(service._json({"a": D("1.50"), "d": date(2026, 1, 1)}), {"a": "1.50", "d": "2026-01-01"})


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from starlette.applications import Starlette

        self.app = Starlette()
        portfolio_endpoint._scheduler = DailyTaskScheduler(MemoryRunStore(), enabled=False)
        portfolio_endpoint.register_routes(self.app, {"Access-Control-Allow-Origin": "*"})

    async def _client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def test_requires_user_header(self):
        async with await self._client() as client:
            response = await client.get("/api/portfolio/portfolios")
        self.assertEqual(response.status_code, 401)
        self.assertIn("用户", response.json()["error"])

    async def test_list_and_create_delegate_to_service_with_owner(self):
        with patch.object(service, "list_portfolios", return_value=[{"id": 1, "name": "A"}]) as listed, \
             patch.object(service, "create_portfolio", return_value={"id": 2, "name": "B"}) as created:
            async with await self._client() as client:
                response = await client.get("/api/portfolio/portfolios", headers={"X-Office-User-Id": "u1"})
                self.assertEqual(response.json(), {"portfolios": [{"id": 1, "name": "A"}]})
                response = await client.post("/api/portfolio/portfolios", headers={"X-Office-User-Id": "u1"}, json={"name": "B"})
                self.assertEqual(response.json()["id"], 2)
        listed.assert_called_once_with("u1")
        created.assert_called_once_with("u1", "B")

    async def test_service_errors_map_to_status(self):
        with patch.object(service, "rename_portfolio", side_effect=service.PortfolioError("组合不存在。", 404)):
            async with await self._client() as client:
                response = await client.put("/api/portfolio/portfolios/9", headers={"X-Office-User-Id": "u1"}, json={"name": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "组合不存在。"})
        async with await self._client() as client:
            response = await client.get("/api/portfolio/portfolios/abc/holdings", headers={"X-Office-User-Id": "u1"})
        self.assertEqual(response.status_code, 400)

    async def test_overview_routes_aggregate_for_owner(self):
        with patch.object(service, "all_holdings_overview", return_value={"portfolio": {"id": 0, "aggregate": True}}) as holdings, \
             patch.object(service, "all_stats", return_value={"nav": {}}) as stats:
            async with await self._client() as client:
                response = await client.get("/api/portfolio/overview/holdings?refresh=0", headers={"X-Office-User-Id": "u1"})
                self.assertEqual(response.json()["portfolio"]["aggregate"], True)
                response = await client.get("/api/portfolio/overview/stats", headers={"X-Office-User-Id": "u1"})
                self.assertEqual(response.status_code, 200)
        holdings.assert_called_once_with("u1", refresh_live=False)
        stats.assert_called_once_with("u1")

    async def test_scheduler_routes(self):
        portfolio_endpoint._scheduler.register(DailyTask(name="demo", run=lambda day: {"day": day.isoformat()}))
        async with await self._client() as client:
            listed = await client.get("/api/scheduler/tasks")
            self.assertEqual(listed.json()["tasks"][0]["name"], "demo")
            ran = await client.post("/api/scheduler/tasks/demo/run")
            self.assertEqual(ran.json()["status"], "success")
            missing = await client.post("/api/scheduler/tasks/nope/run")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
