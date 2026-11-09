"""Manual balance and unit-NAV products on the existing holdings/ledger schema."""
from decimal import Decimal
from uuid import uuid4

import portfolio_repository as repo

MARKETS = {'FIXED', 'FUND'}


def is_manual(ticker):
    return ticker.rsplit('.', 1)[-1].upper() in MARKETS


def product_info(holding):
    txs = repo.list_transactions(holding['portfolio_id'], holding_id=holding.get('_manual_holding_id', holding['id']))
    txs = sorted(txs, key=lambda t: (t['occurred_at'], t['id']))
    invested = sum((Decimal(t['amount']) for t in txs if t['action'] in ('buy', 'add')), Decimal(0))
    withdrawn = sum((Decimal(t['amount']) for t in txs if t['action'] in ('reduce', 'close')), Decimal(0))
    return {'invested': invested, 'withdrawn': withdrawn,
            'price': Decimal(txs[-1]['price']) if txs else Decimal(holding['avg_cost']),
            'as_of': txs[-1]['occurred_at'] if txs else holding['opened_at']}


def create(owner_id, portfolio_id, body):
    import portfolio_service as s
    kind = body.get('asset_type')
    if kind not in ('fixed_income', 'fund'):
        raise s.PortfolioError('未知产品类型。')
    name = str(body.get('name') or '').strip()
    currency = str(body.get('currency') or 'CNY').upper()
    if not name or len(name) > 128:
        raise s.PortfolioError('请填写产品名称（不超过128字）。')
    if currency not in ('CNY', 'HKD', 'USD'):
        raise s.PortfolioError('产品币种仅支持 CNY、HKD、USD。')
    when = s._when(body.get('occurred_at'))
    fixed = kind == 'fixed_income'
    qty = s._decimal(body.get('amount') if fixed else body.get('shares'), '转入金额' if fixed else '确认份额')
    price = Decimal(1) if fixed else s._decimal(body.get('price'), '买入单位净值')
    target = body.get('target_allocation_pct')
    target = s._decimal(target, '目标占比', positive=False) if target not in (None, '') else None
    if target is not None and not 0 <= target <= 100:
        raise s.PortfolioError('目标占比需在0-100之间。')
    with repo.atomic_product(portfolio_id):
        s._portfolio(owner_id, portfolio_id)
        if body.get('settle_with_cash'):
            s._settle_cash(portfolio_id, currency, qty * price, direction='out', when=when, note=body.get('note'))
        holding = repo.insert_holding(portfolio_id, {
            'ticker_code': uuid4().hex[:20].upper() + ('.FIXED' if fixed else '.FUND'),
            'name': name, 'market_code': 'FIXED' if fixed else 'FUND', 'currency': currency,
            'shares': qty, 'avg_cost': price, 'opened_at': when, 'target_allocation_pct': target,
        })
        _ledger(portfolio_id, holding, 'buy', qty, price, qty * price,
                Decimal(0) if body.get('settle_with_cash') else qty * price, when, body.get('note'))
        return s._json(holding)


def _ledger(pid, holding, action, qty, price, amount, flow, when, note):
    repo.insert_transaction(pid, {'holding_id': holding['id'], 'ticker_code': holding['ticker_code'],
        'currency': holding['currency'], 'action': action, 'shares': qty, 'price': price,
        'amount': amount, 'external_flow': flow, 'occurred_at': when, 'note': note})


def transact(owner_id, pid, hid, body):
    import portfolio_service as s
    with repo.atomic_product(pid):
        s._portfolio(owner_id, pid)
        h = repo.get_holding(pid, hid)
        if not h or h['market_code'] not in MARKETS:
            raise s.PortfolioError('产品不存在。', 404)
        if h['status'] != 'open':
            raise s.PortfolioError('产品已全部转出。')
        when = s._when(body.get('occurred_at'))
        info = product_info(h)
        if when < info['as_of']:
            raise s.PortfolioError('请按时间顺序记录，日期不能早于该产品最近一笔记录。')
        action = body.get('action')
        fixed = h['market_code'] == 'FIXED'
        current = Decimal(h['shares'])
        note = body.get('note')
        if action == 'valuation':
            value = s._decimal(body.get('amount') if fixed else body.get('price'), '持有总金额' if fixed else '单位净值', positive=False)
            if value < 0:
                raise s.PortfolioError('估值不能为负数。')
            if fixed:
                repo.update_holding(hid, shares=value)
            _ledger(pid, h, action, Decimal(0), Decimal(1) if fixed else value,
                    value if fixed else Decimal(0), Decimal(0), when, note)
        elif action in ('add', 'reduce', 'close'):
            qty = current if action == 'close' else s._decimal(body.get('amount') if fixed else body.get('shares'), '金额' if fixed else '份额')
            price = Decimal(1) if fixed else s._decimal(body.get('price'), '成交单位净值', positive=False)
            if price < 0:
                raise s.PortfolioError('成交单位净值不能为负数。')
            amount = (s._decimal(body.get('amount'), '全部转出到账金额', positive=False)
                      if fixed and action == 'close' else qty * price)
            if amount < 0 or (action == 'add' and price <= 0):
                raise s.PortfolioError('金额或净值无效。')
            if action == 'reduce' and qty >= current:
                raise s.PortfolioError('部分转出须小于持有金额／份额，请选择全部转出。')
            settle = bool(body.get('settle_with_cash'))
            if settle:
                s._settle_cash(pid, h['currency'], amount, direction='out' if action == 'add' else 'in', when=when, note=note)
            next_qty = current + qty if action == 'add' else current - qty
            fields = {'shares': next_qty}
            if action == 'add' and not fixed:
                fields['avg_cost'] = (current * Decimal(h['avg_cost']) + amount) / next_qty
            if action == 'close':
                fields.update(shares=Decimal(0), status='closed', closed_at=when)
            repo.update_holding(hid, **fields)
            flow = Decimal(0) if settle else (amount if action == 'add' else -amount)
            _ledger(pid, h, action, qty, price, amount, flow, when, note)
        else:
            raise s.PortfolioError('产品仅支持转入、转出和更新估值。')
        return s._json(repo.get_holding(pid, hid))
