import { remoteJson } from '../remote/client';

/** 证券搜索结果；security_type 为「基金」表示 ETF。 */
export interface SecurityHit {
  ticker_code: string;
  name: string;
  security_type?: string | null;
}

export interface Portfolio {
  id: number;
  owner_id: string;
  name: string;
  base_currency: string;
  created_at: string;
  updated_at: string;
}

/** 「全部持仓统计」虚拟组合的 id：不在数据库里，由后端跨组合聚合。 */
export const ALL_PORTFOLIOS_ID = 0;
export const ALL_PORTFOLIOS_NAME = '全部持仓统计';
export const isAllPortfolios = (id: number | null): id is number => id === ALL_PORTFOLIOS_ID;

/** 持仓 / 统计响应里的组合信息；聚合视图带 aggregate 标记。 */
export interface PortfolioRef {
  id: number;
  name: string;
  aggregate?: boolean;
  portfolio_count?: number;
  portfolio_names?: string[];
}

export type PriceSource = 'live' | 'cache_today' | 'cache_last' | 'cash' | 'none';

export interface Holding {
  id: number;
  ticker_code: string;
  stock_code: string;
  market_code: string;
  market_name: string;
  name: string;
  currency: string;
  shares: string;
  avg_cost: string;
  status: string;
  opened_at: string;
  current_price: string | null;
  price_source: PriceSource;
  price_trade_date: string | null;
  /** 相对上一收盘价的涨跌额（行情源提供） */
  change: string | null;
  /** 涨跌幅，小数 */
  change_pct: string | null;
  /** 年初至今涨跌幅，小数（行情源提供；源未给时用上年末收盘价推算） */
  year_change_pct: string | null;
  market_value: string | null;
  profit: string | null;
  return_rate: string | null;
  market_value_cny: string | null;
  profit_cny: string | null;
  current_allocation_pct: string | null;
  target_allocation_pct: string | null;
  /** 聚合视图：该资产来自哪些组合 */
  portfolios?: string[] | null;
}

export interface HoldingsSummary {
  total_market_value_cny: string;
  total_cost_cny: string;
  total_profit_cny: string;
  overall_return_rate: string | null;
  /** 组合市值相对上一收盘的增加额（人民币） */
  total_change_cny: string | null;
  /** 组合市值日涨跌幅，小数 */
  total_change_pct: string | null;
  /** 组合市值年涨跌幅，小数 */
  total_year_change_pct: string | null;
  total_target_allocation_pct: string;
  total_current_allocation_pct: string;
  holdings_count: number;
}

export interface FxRates { usd_cny: string; hkd_cny: string; fetched_at: string; }

export interface HoldingsOverview {
  portfolio: PortfolioRef;
  holdings: Holding[];
  summary: HoldingsSummary;
  fx: FxRates;
}

export interface Transaction {
  id: number;
  holding_id: number | null;
  ticker_code: string;
  currency: string;
  action: string;
  price: string;
  shares: string;
  amount: string;
  external_flow: string;
  occurred_at: string;
  note: string | null;
}

export interface NavPoint {
  date: string;
  nav: string;
  daily_return: string | null;
  total_value_cny: string;
  net_flow_cny: string;
  xirr: string | null;
}

export interface PortfolioStats {
  portfolio: PortfolioRef;
  nav: {
    current: string | null;
    previous: string | null;
    growth_rate: string | null;
    as_of: string | null;
    previous_date: string | null;
    computed_at: string | null;
    /** 年初净值：上一年最后一条净值；今年建仓则为建仓日净值 1 */
    year_start: string | null;
    year_start_date: string | null;
    ytd_growth_rate: string | null;
    /** true 表示组合今年才建仓，「年初」实际是建仓日 */
    ytd_since_inception: boolean;
  };
  totals: {
    cum_invested_cny: string;
    cum_withdrawn_cny: string;
    total_market_value_cny: string;
    net_invested_cny: string;
    total_profit_cny: string;
    year_start_value_cny: string | null;
    ytd_inflow_cny: string | null;
    ytd_outflow_cny: string | null;
    ytd_profit_cny: string | null;
  };
  xirr: { current: string | null; as_of: string };
  /** 年化波动率与夏普比率（基于每日净值收益率，252 个交易日年化） */
  risk: {
    volatility: string | null;
    sharpe: string | null;
    annualized_return: string | null;
    observations: number;
    min_observations: number;
    /** 无风险利率：中国 10 年期国债收益率 */
    risk_free: { rate: string; as_of: string; source: string; fetched_at: string } | null;
  };
  series: NavPoint[];
  previous_trading_day: string;
  year: number;
  dividends: DividendSummary;
}

export interface ScheduledTask {
  name: string;
  description: string;
  at: string;
  weekdays_only: boolean;
  depends_on: string[];
  timezone: string;
  today: string;
  today_status: string | null;
  today_detail: string | null;
  today_finished_at: string | null;
  running: boolean;
}

export type HoldingAction = 'add' | 'reduce' | 'close' | 'deposit' | 'withdraw' | 'dividend';

/** 分红统计：按来源标的分组，yield_rate = 今年分红 / 当前持仓成本。 */
export interface DividendItem {
  ticker_code: string;
  name: string;
  total_cny: string;
  year_cny: string;
  count: number;
  last_date: string | null;
  cost_cny: string | null;
  yield_rate: string | null;
}

export interface DividendSummary {
  total_cny: string;
  year_cny: string;
  items: DividendItem[];
}

export interface BuyBody {
  ticker_code: string;
  name?: string;
  price?: string;
  shares: string;
  occurred_at?: string;
  note?: string;
  target_allocation_pct?: string;
  settle_with_cash?: boolean;
}

export interface TransactionBody {
  action: HoldingAction;
  price?: string;
  shares: string;
  occurred_at?: string;
  note?: string;
  target_allocation_pct?: string;
  settle_with_cash?: boolean;
  /** 分红来源标的，仅 action=dividend 时使用 */
  source_ticker?: string;
}

const base = '/api/portfolio/portfolios';
const overview = '/api/portfolio/overview';
/** 普通组合走 /portfolios/{id}/...，全部持仓统计走 /overview/... */
const scope = (id: number, tail: string) => (isAllPortfolios(id) ? `${overview}/${tail}` : `${base}/${id}/${tail}`);

export const portfolioApi = {
  list: () => remoteJson<{ portfolios: Portfolio[] }>(base).then(r => r.portfolios),
  create: (name: string) => remoteJson<Portfolio>(base, { method: 'POST', body: JSON.stringify({ name }) }),
  rename: (id: number, name: string) => remoteJson<Portfolio>(`${base}/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  remove: (id: number) => remoteJson<{ ok: boolean }>(`${base}/${id}`, { method: 'DELETE' }),
  holdings: (id: number, refresh = true) => remoteJson<HoldingsOverview>(`${scope(id, 'holdings')}?refresh=${refresh ? 1 : 0}`),
  buy: (id: number, body: BuyBody) => remoteJson<Holding>(`${base}/${id}/holdings`, { method: 'POST', body: JSON.stringify(body) }),
  transact: (id: number, holdingId: number, body: TransactionBody) =>
    remoteJson<Holding>(`${base}/${id}/holdings/${holdingId}/transactions`, { method: 'POST', body: JSON.stringify(body) }),
  transactions: (id: number, holdingId?: number) =>
    remoteJson<{ transactions: Transaction[] }>(
      holdingId ? `${base}/${id}/holdings/${holdingId}/transactions` : `${base}/${id}/transactions`,
    ).then(r => r.transactions),
  stats: (id: number) => remoteJson<PortfolioStats>(scope(id, 'stats')),
  refreshStats: (id: number) => remoteJson<PortfolioStats>(scope(id, 'stats/refresh'), { method: 'POST' }),
  tasks: () => remoteJson<{ tasks: ScheduledTask[]; history: Array<Record<string, string | null>> }>('/api/scheduler/tasks'),
  runTask: (name: string) => remoteJson<Record<string, unknown>>(`/api/scheduler/tasks/${encodeURIComponent(name)}/run`, { method: 'POST' }),
  searchSecurities: (q: string) => remoteJson<{ data?: { items?: SecurityHit[] } }>(`/api/financial/securities/search?q=${encodeURIComponent(q)}`).then(r => r.data?.items || []),
};
