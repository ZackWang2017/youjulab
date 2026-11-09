"""Manual products use an isolated SQLite database; never touch user holdings."""
import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE/'portfolio-service/service'), str(HERE/'portfolio-service/repository')]
import portfolio_repository as repo
import portfolio_service as service
import market_quote_repository as market
from nav_service import PricingContext, compute_nav_series, xirr


class ManualProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'FINANCE_STORE': 'sqlite', 'FINANCE_SQLITE_PATH': str(Path(self.tmp.name)/'test.sqlite')})
        self.env.start()
        self.schema = repo._schema_ready
        repo._schema_ready = False
        self.refresh = patch.object(service, '_refresh_nav_quietly')
        self.refresh.start()
        self.p = repo.create_portfolio('owner', 'test')

    def tearDown(self):
        self.refresh.stop()
        repo._schema_ready = self.schema
        self.env.stop()
        self.tmp.cleanup()

    def create(self, kind='fixed_income', **extra):
        return service.buy('owner', self.p['id'], {'asset_type': kind, 'name': '产品', 'currency': 'CNY',
            'amount': '10000', 'shares': '1000', 'price': '1', 'occurred_at': '2025-01-01', **extra})

    def act(self, h, action, day='2025-07-01', **extra):
        return service.transact('owner', self.p['id'], h['id'], {'action': action, 'occurred_at': day, **extra})

    def overview(self):
        fx = market.FxRates(D('7'), D('.9'), 'test')
        with patch.object(market, 'get_fx_rates', return_value=fx), patch.object(market, 'fetch_live_quotes', return_value={}) as quote:
            result = service.holdings_overview('owner', self.p['id'])
            self.assertFalse(any(t.endswith(('.FIXED', '.FUND')) for call in quote.call_args_list for t in call.args[0]))
            return result

    def test_fixed_simple_return_and_absolute_valuation(self):
        h = self.create()
        self.act(h, 'valuation', amount='10200')
        self.act(h, 'reduce', '2025-07-02', amount='3000')
        item = self.overview()['holdings'][0]
        self.assertEqual(D(item['market_value']), D('7200'))
        self.assertEqual(D(item['profit']), D('200'))
        self.assertEqual(D(item['return_rate']), D('.02'))
        # A repeated balance report replaces value, not income or principal.
        self.act(h, 'valuation', '2025-07-03', amount='7200')
        self.assertEqual(D(self.overview()['holdings'][0]['profit']), D('200'))
        txs = repo.transactions_in_order(self.p['id'])
        points = compute_nav_series(txs, PricingContext(), end=date(2025,7,3))
        self.assertEqual(points[-1].total_value_cny, D('7200'))
        self.assertEqual(points[-1].cum_invested_cny, D('10000'))
        self.assertEqual(points[-1].cum_withdrawn_cny, D('3000'))
        self.assertEqual(sum(D(t['external_flow']) for t in txs if t['action']=='valuation'), 0)

    def test_fund_weighted_purchase_nav_and_manual_price(self):
        h = self.create('fund')
        self.act(h, 'add', shares='1000', price='2')
        self.act(h, 'valuation', '2025-07-02', price='1.8')
        row = self.overview()['holdings'][0]
        self.assertEqual(D(row['avg_cost']), D('1.5'))
        self.assertEqual(D(row['market_value']), D('3600'))
        self.assertEqual(D(row['return_rate']), D('.2'))
        txs = repo.transactions_in_order(self.p['id'])
        points = compute_nav_series(txs, PricingContext(), end=date(2025,7,2))
        self.assertEqual(points[-1].total_value_cny, D('3600'))
        self.assertEqual(points[-1].cum_invested_cny, D('3000'))

    def test_cash_settlement_is_internal_and_close_preserves_realized_gain(self):
        service.buy('owner', self.p['id'], {'ticker_code':'CNY.CASH','shares':'20000','occurred_at':'2024-12-01'})
        h = self.create(settle_with_cash=True)
        self.act(h, 'valuation', amount='10300')
        self.act(h, 'close', '2025-07-02', amount='10300', settle_with_cash=True)
        txs = repo.transactions_in_order(self.p['id'])
        points = compute_nav_series(txs, PricingContext(), end=date(2025,7,2))
        self.assertEqual(points[-1].total_value_cny, D('20300'))
        self.assertEqual(points[-1].cum_invested_cny, D('20000'))
        self.assertEqual(points[-1].cum_withdrawn_cny, D('0'))
        self.assertEqual(repo.get_holding(self.p['id'],h['id'])['status'], 'closed')

    def test_xirr_uses_dated_flows_and_final_balance(self):
        h = self.create()
        self.act(h, 'valuation', '2026-01-01', amount='10500')
        points = compute_nav_series(repo.transactions_in_order(self.p['id']), PricingContext(), end=date(2026,1,1))
        self.assertAlmostEqual(float(points[-1].xirr), .05, places=6)
        self.assertAlmostEqual(float(xirr([(date(2025,1,1),D('-10000')),(date(2026,1,1),D('10500'))])), .05, places=6)

    def test_rejects_invalid_ownership_dates_and_values_without_changes(self):
        h = self.create()
        before = repo.transactions_in_order(self.p['id'])
        for body in [dict(action='valuation', amount='-1'), dict(action='valuation',amount='NaN'),
                     dict(action='reduce',amount='10001'), dict(action='valuation',amount='9000', occurred_at='2024-01-01')]:
            with self.assertRaises(service.PortfolioError):
                service.transact('owner', self.p['id'], h['id'], {'occurred_at':'2025-07-01',**body})
        with self.assertRaises(service.PortfolioError):
            service.transact('other',self.p['id'],h['id'],{'action':'valuation','amount':'0'})
        self.assertEqual(len(repo.transactions_in_order(self.p['id'])),len(before))
        self.assertEqual(repo.get_holding(self.p['id'],h['id'])['shares'], D('10000'))

    def test_zero_balance_is_visible_and_keeps_negative_return(self):
        h = self.create()
        self.act(h, 'valuation', amount='0')
        self.assertEqual(D(self.overview()['holdings'][0]['return_rate']), D('-1'))
        rows = service._merge_holdings([self.p])
        self.assertEqual(len(rows),1)

    def test_rollback_covers_cash_product_and_ledger(self):
        service.buy('owner',self.p['id'],{'ticker_code':'CNY.CASH','shares':'20000','occurred_at':'2024-12-01'})
        original = repo.insert_transaction
        def fail_product(pid, tx):
            if tx['ticker_code'].endswith('.FIXED'): raise RuntimeError('simulated ledger failure')
            return original(pid,tx)
        with patch.object(repo,'insert_transaction',side_effect=fail_product), self.assertRaises(RuntimeError):
            self.create(settle_with_cash=True)
        rows = repo.list_holdings(self.p['id'])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['shares'],D('20000'))
        self.assertEqual(len(repo.transactions_in_order(self.p['id'])),1)

    def test_manual_products_not_sent_to_market_sync_and_persist_after_reopen(self):
        h = self.create('fund')
        self.act(h, 'valuation', price='1.12')
        self.assertEqual(repo.held_tickers_everywhere(),[])
        repo._schema_ready=False
        self.assertEqual(repo.get_holding(self.p['id'],h['id'])['name'],'产品')
        self.assertEqual(D(self.overview()['holdings'][0]['current_price']),D('1.12'))

    def test_partial_fund_redemption_keeps_purchase_nav_basis(self):
        h = self.create('fund')
        self.act(h, 'valuation', price='1.2')
        self.act(h, 'reduce', '2025-07-02', shares='200', price='1.2')
        row = self.overview()['holdings'][0]
        self.assertEqual(D(row['shares']),D('800'))
        self.assertEqual(D(row['avg_cost']),D('1'))
        self.assertEqual(D(row['return_rate']),D('.2'))
        points = compute_nav_series(repo.transactions_in_order(self.p['id']),PricingContext(),end=date(2025,7,2))
        self.assertEqual(points[-1].total_value_cny,D('960'))
        self.assertEqual(points[-1].cum_withdrawn_cny,D('240'))

    def test_aggregate_overview_and_nav_include_both_product_types(self):
        h = self.create()
        self.act(h,'valuation',amount='10200')
        second = repo.create_portfolio('owner','second')
        fund = service.buy('owner',second['id'],{'asset_type':'fund','name':'基金','currency':'CNY',
            'shares':'1000','price':'1.5','occurred_at':'2025-01-01'})
        service.transact('owner',second['id'],fund['id'],{'action':'valuation','price':'1.8','occurred_at':'2025-07-01'})
        fx = market.FxRates(D('7'),D('.9'),'test')
        with patch.object(market,'get_fx_rates',return_value=fx),patch.object(market,'fetch_live_quotes',return_value={}):
            result = service.all_holdings_overview('owner')
        self.assertEqual(len(result['holdings']),2)
        self.assertEqual(D(result['summary']['total_market_value_cny']),D('12000'))
        txs = repo.transactions_in_order(self.p['id']) + repo.transactions_in_order(second['id'])
        points = compute_nav_series(txs,PricingContext(),end=date(2025,7,1))
        self.assertEqual(points[-1].total_value_cny,D('12000'))
        self.assertEqual(points[-1].cum_invested_cny,D('11500'))


if __name__ == '__main__': unittest.main()
