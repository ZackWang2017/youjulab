import { createIcons, icons } from 'lucide';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsType } from 'echarts/core';
import { ALL_PORTFOLIOS_NAME, isAllPortfolios, portfolioApi, type Holding, type HoldingAction, type HoldingsOverview, type PortfolioStats } from './api';
import { openAdjustDialog, openBuyDialog, openDividendDialog, openSchedulerDialog, openTransactionsDialog } from './dialogs';
import {
  formatDate, formatDateTime, formatMoney, formatMoneyWithCurrency, formatPercent, formatPercentPoint, formatShares, signClass, toNumber,
} from './format';
import './style.css';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export type PortfolioView = 'holdings' | 'stats';

const esc = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));

const SOURCE_LABEL: Record<string, { label: string; cls: string }> = {
  live: { label: '实时', cls: 'is-ok' },
  cache_today: { label: '今日收盘', cls: '' },
  cache_last: { label: '上次收盘', cls: 'is-warn' },
  cash: { label: '现金', cls: '' },
  none: { label: '无报价', cls: '' },
};

export interface PortfolioController {
  show(portfolioId: number | null, view: PortfolioView): Promise<void>;
  refresh(): Promise<void>;
  destroy(): void;
}

export function mountPortfolio(section: HTMLElement, onError: (error: unknown) => void): PortfolioController {
  section.innerHTML = '<div class="portfolio-shell" data-shell></div>';
  const shell = section.querySelector<HTMLElement>('[data-shell]')!;
  let portfolioId: number | null = null;
  let view: PortfolioView = 'holdings';
  let overview: HoldingsOverview | null = null;
  let stats: PortfolioStats | null = null;
  let loading = false;
  let charts: EChartsType[] = [];
  let seq = 0;

  const disposeCharts = () => { charts.forEach(c => c.dispose()); charts = []; };
  const resize = () => charts.forEach(c => c.resize());
  window.addEventListener('resize', resize);

  const cashBalances = () => {
    const out: Record<string, number> = {};
    overview?.holdings.filter(h => h.market_code === 'CASH').forEach(h => { out[h.currency] = toNumber(h.shares) ?? 0; });
    return out;
  };

  async function load() {
    if (portfolioId === null) { render(); return; }
    const current = ++seq;
    loading = true;
    render();
    try {
      if (view === 'holdings') overview = await portfolioApi.holdings(portfolioId);
      else {
        const [s, o] = await Promise.all([portfolioApi.stats(portfolioId), portfolioApi.holdings(portfolioId, false)]);
        stats = s;
        overview = o;
      }
    } catch (error) {
      if (current === seq) onError(error);
    } finally {
      if (current === seq) { loading = false; render(); }
    }
  }

  function render() {
    disposeCharts();
    if (portfolioId === null) {
      shell.innerHTML = `<div class="portfolio-empty"><i data-lucide="briefcase-business"></i><h2>还没有投资组合</h2><p>在上方「投资组合」下拉菜单里新建一个组合，然后记录买入与现金进出。有了组合之后，下拉菜单里会出现「${ALL_PORTFOLIOS_NAME}」。</p></div>`;
      createIcons({ icons });
      return;
    }
    shell.innerHTML = view === 'holdings' ? holdingsHtml() : statsHtml();
    createIcons({ icons });
    bind();
    if (view === 'stats' && stats) drawCharts();
  }

  /** 全部持仓统计：跨组合聚合、只读，不能买卖。 */
  const aggregate = () => isAllPortfolios(portfolioId);

  function aggregateNote() {
    const p = overview?.portfolio ?? stats?.portfolio;
    const count = p?.portfolio_count ?? 0;
    return count ? `合并 ${count} 个组合：${esc((p?.portfolio_names ?? []).join('、'))}` : '合并所有组合的持仓';
  }

  function holdingsHtml() {
    const fx = overview?.fx;
    const rows = overview?.holdings ?? [];
    const summary = overview?.summary;
    const all = aggregate();
    const fxLine = fx ? `汇率（兑人民币）：USD ${formatMoney(fx.usd_cny, 4)} · HKD ${formatMoney(fx.hkd_cny, 4)}<span class="portfolio-muted">更新 ${formatDateTime(fx.fetched_at)}</span>` : '<span class="portfolio-muted">正在获取行情…</span>';
    const head = `<header class="portfolio-head ${all ? 'is-aggregate' : ''}">
      <div><h1>${all ? `${ALL_PORTFOLIOS_NAME} · 实时市值` : '实时组合市值'}</h1><p>${all ? `<span class="portfolio-aggregate-note"><i data-lucide="layers"></i>${aggregateNote()}</span>` : ''}${fxLine}</p></div>
      <div class="portfolio-head-actions">
        ${all ? '' : '<button type="button" data-tx title="查看组合全部流水"><i data-lucide="receipt-text"></i>流水</button>'}
        <button type="button" data-tasks title="收盘后计划任务"><i data-lucide="clock"></i>计划任务</button>
        <button type="button" data-refresh ${loading ? 'disabled' : ''}><i data-lucide="refresh-ccw" class="${loading ? 'is-spinning' : ''}"></i>刷新行情</button>
        ${all ? '' : '<button type="button" class="primary" data-buy><i data-lucide="plus"></i>买入 / 存入</button>'}
      </div>
    </header>`;
    if (loading && !overview) return `${head}<div class="portfolio-state">正在加载持仓…</div>`;
    if (!rows.length) {
      return all
        ? `${head}<div class="portfolio-state"><p>所有组合都还没有持仓</p></div>`
        : `${head}<div class="portfolio-state"><p>暂无持仓</p><button type="button" data-buy><i data-lucide="plus"></i>新增买入 / 存入现金</button></div>`;
    }
    const body = rows.map(h => holdingRow(h, all)).join('');
    const foot = summary ? `<tfoot><tr>
      <td colspan="${all ? 8 : 7}">合计（折算人民币）<small class="portfolio-muted">成本 ¥${formatMoney(summary.total_cost_cny, 2)}</small></td>
      <td class="num strong">¥${formatMoney(summary.total_market_value_cny, 2)}</td>
      <td class="num ${signClass(summary.total_profit_cny)}">¥${formatMoney(summary.total_profit_cny, 2)}</td>
      <td class="num ${signClass(summary.overall_return_rate)}">${formatPercent(summary.overall_return_rate)}</td>
      <td class="num">${formatPercentPoint(summary.total_current_allocation_pct)}</td>
      ${all ? '' : `<td class="num portfolio-muted">${formatPercentPoint(summary.total_target_allocation_pct)}</td><td></td>`}</tr></tfoot>` : '';
    const columns = all
      ? '<th>资产</th><th>所属组合</th><th class="num">买入均价</th><th class="num">数量</th><th class="num">现价</th><th class="num">涨跌</th><th class="num">年涨跌</th><th class="num">市值</th><th class="num">市值(¥)</th><th class="num">盈亏</th><th class="num">收益率</th><th class="num">当前占比</th>'
      : '<th>资产</th><th class="num">买入均价</th><th class="num">数量</th><th class="num">现价</th><th class="num">涨跌</th><th class="num">年涨跌</th><th class="num">市值</th><th class="num">市值(¥)</th><th class="num">盈亏</th><th class="num">收益率</th><th class="num">当前占比</th><th class="num">目标占比</th><th class="center">操作</th>';
    return `${head}<div class="portfolio-body"><div class="portfolio-table-wrap"><table class="portfolio-table portfolio-holdings-table ${all ? 'is-aggregate' : ''}">
      <thead><tr>${columns}</tr></thead>
      <tbody>${body}</tbody>${foot}</table></div></div>`;
  }

  function holdingRow(h: Holding, all = false) {
    const cash = h.market_code === 'CASH';
    const source = SOURCE_LABEL[h.price_source] ?? SOURCE_LABEL.none;
    const profitSign = signClass(h.profit_cny ?? h.profit);
    const sources = (h.portfolios ?? []).map(name => `<span class="portfolio-chip-small">${esc(name)}</span>`).join('');
    return `<tr data-holding="${h.id ?? ''}">
      <td class="${all ? 'portfolio-aggregate-asset-cell' : ''}"><div class="portfolio-asset-name" title="${esc(h.name)}">${esc(h.name)}</div><div class="portfolio-asset-meta"><span>${esc(h.stock_code)}</span><span class="portfolio-badge market-${esc(h.market_code.toLowerCase())}">${esc(h.market_name)}</span><span>${esc(h.currency)}</span></div></td>
      ${all ? `<td class="portfolio-sources">${sources || '—'}</td>` : ''}
      <td class="num">${formatMoneyWithCurrency(h.avg_cost, h.currency, 4)}</td>
      <td class="num">${cash ? formatMoney(h.shares, 2) : formatShares(h.shares)}</td>
      <td class="num"><div>${formatMoneyWithCurrency(h.current_price, h.currency, 4)}</div>${cash ? '' : `<div class="portfolio-asset-meta right"><span class="portfolio-badge ${source.cls}">${source.label}</span>${h.price_trade_date ? `<span>${formatDate(h.price_trade_date)}</span>` : ''}</div>`}</td>
      <td class="num ${signClass(h.change_pct)}">${cash || h.change_pct === null ? '—' : `${formatMoney(h.change, 3)}<div class="portfolio-sub">${formatPercent(h.change_pct, 2, true)}</div>`}</td>
      <td class="num ${signClass(h.year_change_pct)}">${cash || h.year_change_pct === null ? '—' : formatPercent(h.year_change_pct, 2, true)}</td>
      <td class="num">${formatMoneyWithCurrency(h.market_value, h.currency, 2)}</td>
      <td class="num strong">${formatMoney(h.market_value_cny, 2)}</td>
      <td class="num ${profitSign}"><div>${formatMoneyWithCurrency(h.profit, h.currency, 2)}</div>${h.currency !== 'CNY' && h.profit_cny != null ? `<div class="portfolio-sub">¥${formatMoney(h.profit_cny, 2)}</div>` : ''}</td>
      <td class="num ${signClass(h.return_rate)}">${formatPercent(h.return_rate)}</td>
      <td class="num">${formatPercentPoint(h.current_allocation_pct)}</td>
      ${all ? '' : `<td class="num portfolio-muted">${formatPercentPoint(h.target_allocation_pct)}</td>
      <td class="center"><div class="portfolio-row-actions">
        <button type="button" data-action="${cash ? 'deposit' : 'add'}" data-holding-id="${h.id}">${cash ? '存入' : '增持'}</button>
        <button type="button" data-action="${cash ? 'withdraw' : 'reduce'}" data-holding-id="${h.id}">${cash ? '取出' : '减持'}</button>
        ${cash ? `<button type="button" data-dividend="${h.id}">分红</button>` : ''}
        ${cash ? '' : `<button type="button" data-action="close" data-holding-id="${h.id}" class="portfolio-danger">清仓</button>`}
        <button type="button" data-holding-tx="${h.id}" title="交易流水"><i data-lucide="receipt-text"></i></button>
      </div></td>`}
    </tr>`;
  }

  function statsHtml() {
    const all = aggregate();
    const note = all
      ? `<span class="portfolio-aggregate-note"><i data-lucide="layers"></i>${aggregateNote()}</span>把所有组合的资金流水合并后按净值法即时计算，不落库。`
      : `净值法按每日收盘价与资金进出计算；工作日 17:00 自动计算上一交易日净值${stats?.nav.computed_at ? `，最近计算 ${formatDateTime(stats.nav.computed_at)}` : ''}。`;
    const head = `<header class="portfolio-head ${all ? 'is-aggregate' : ''}">
      <div><h1>${all ? `${ALL_PORTFOLIOS_NAME} · 净值和收益` : '净值和收益统计'}</h1><p class="portfolio-muted">${note}</p></div>
      <div class="portfolio-head-actions">
        <button type="button" data-tasks title="收盘后计划任务"><i data-lucide="clock"></i>计划任务</button>
        <button type="button" class="primary" data-refresh-stats ${loading ? 'disabled' : ''}><i data-lucide="refresh-ccw" class="${loading ? 'is-spinning' : ''}"></i>立即更新</button>
      </div>
    </header>`;
    if (loading && !stats) return `${head}<div class="portfolio-state">正在计算…</div>`;
    if (!stats) return `${head}<div class="portfolio-state">暂无数据</div>`;
    const nav = stats.nav;
    const t = stats.totals;
    const hasSeries = stats.series.length > 0;
    const growth = toNumber(nav.growth_rate);
    const xirrNow = toNumber(stats.xirr.current);
    const ytdGrowth = toNumber(nav.ytd_growth_rate);
    const yearLabel = `${stats.year} 年`;
    const yearStartLabel = nav.ytd_since_inception ? '年初净值 <small>建仓日</small>' : `年初净值${nav.year_start_date ? ` <small>${formatDate(nav.year_start_date)}</small>` : ''}`;
    const risk = stats.risk;
    const vol = toNumber(risk.volatility);
    const sharpe = toNumber(risk.sharpe);
    const riskReady = risk.observations >= risk.min_observations;
    const riskHint = riskReady ? '' : `样本 ${risk.observations}/${risk.min_observations} 日`;
    const rf = risk.risk_free;
    const metric = (label: string, value: string, cls = '', hint = '') =>
      `<div class="portfolio-metric"><span>${label}</span><b class="${cls}">${value}</b>${hint ? `<small>${hint}</small>` : ''}</div>`;
    return `${head}<div class="portfolio-body portfolio-stats">
      <section class="portfolio-card portfolio-card-nav">
        <div class="portfolio-card-title"><i data-lucide="gauge"></i>${all ? '全部持仓净值' : '组合净值'}<span class="portfolio-card-title-aside">${nav.as_of ? `净值日期 ${formatDate(nav.as_of)}` : ''}</span></div>
        <div class="portfolio-nav-main"><span class="portfolio-big ${signClass(growth)}">${nav.current ? formatMoney(nav.current, 4) : '—'}</span><span class="portfolio-chip ${signClass(growth)}">${growth === null ? '—' : formatPercent(growth, 2, true)}</span><span class="portfolio-muted">日增长率</span></div>
        <div class="portfolio-metrics">
          ${metric(`前一日净值${nav.previous_date ? ` <small>${formatDate(nav.previous_date)}</small>` : ''}`, nav.previous ? formatMoney(nav.previous, 4) : '—')}
          ${metric('净值增长率', growth === null ? '—' : formatPercent(growth, 2, true), signClass(growth))}
          ${metric(yearStartLabel, nav.year_start ? formatMoney(nav.year_start, 4) : '—')}
          ${metric(`${yearLabel}净值增长率`, ytdGrowth === null ? '—' : formatPercent(ytdGrowth, 2, true), signClass(ytdGrowth))}
          ${metric('年化波动率', vol === null ? '—' : formatPercent(vol, 2), '', riskHint)}
          ${metric('夏普比率', sharpe === null ? '—' : sharpe.toFixed(2), sharpe === null ? '' : signClass(sharpe), riskReady && rf === null ? '无风险利率未获取' : riskHint)}
        </div>
        <p class="portfolio-footnote">${rf
          ? `无风险利率取中国 10 年期国债收益率 <b>${formatPercent(rf.rate, 3)}</b>（${formatDate(rf.as_of)}，${esc(rf.source)}，获取于 ${formatDateTime(rf.fetched_at)}）；波动率与夏普按 252 个交易日年化。`
          : '无风险利率（中国 10 年期国债收益率）暂未获取到，夏普比率不显示；波动率按 252 个交易日年化。'}</p>
        ${hasSeries ? '' : '<p class="portfolio-hint">记录第一笔买入或存入后，这里会出现净值。</p>'}
      </section>
      <section class="portfolio-card portfolio-card-chart">
        <div class="portfolio-card-title"><i data-lucide="chart-line"></i>净值曲线</div>
        <div class="portfolio-chart" data-chart="nav">${hasSeries ? '' : '<p class="portfolio-hint">暂无数据</p>'}</div>
      </section>
      <section class="portfolio-card portfolio-card-xirr">
        <div class="portfolio-card-title"><i data-lucide="trending-up"></i>XIRR 年化收益</div>
        <div class="portfolio-nav-main"><span class="portfolio-big ${signClass(xirrNow)}">${xirrNow === null ? '—' : formatPercent(xirrNow, 2, true)}</span><span class="portfolio-muted">截至 ${formatDate(stats.xirr.as_of)}（按实时市值）</span></div>
        <div class="portfolio-chart portfolio-chart-small" data-chart="xirr">${hasSeries ? '' : '<p class="portfolio-hint">持有满 7 天后开始显示 XIRR 曲线</p>'}</div>
      </section>
      <section class="portfolio-card portfolio-card-totals">
        <div class="portfolio-card-title"><i data-lucide="wallet"></i>资金与市值</div>
        <div class="portfolio-totals">
          <div><span>累计投入成本</span><b>¥${formatMoney(t.cum_invested_cny, 2)}</b></div>
          <div><span>累计取出</span><b>¥${formatMoney(t.cum_withdrawn_cny, 2)}</b></div>
          <div><span>当前总市值</span><b class="strong">¥${formatMoney(t.total_market_value_cny, 2)}</b></div>
          <div><span>净投入</span><b>¥${formatMoney(t.net_invested_cny, 2)}</b></div>
          <div><span>累计收益（市值 − 净投入）</span><b class="${signClass(t.total_profit_cny)}">¥${formatMoney(t.total_profit_cny, 2)}</b></div>
        </div>
        <div class="portfolio-totals-year"><i data-lucide="calendar-range"></i>${yearLabel}${nav.ytd_since_inception ? '（自建仓起）' : ''}</div>
        <div class="portfolio-totals">
          <div><span>年初市值</span><b>${t.year_start_value_cny === null ? '—' : `¥${formatMoney(t.year_start_value_cny, 2)}`}</b></div>
          <div><span>今年新增</span><b>${t.ytd_inflow_cny === null ? '—' : `¥${formatMoney(t.ytd_inflow_cny, 2)}`}</b></div>
          <div><span>今年取出</span><b>${t.ytd_outflow_cny === null ? '—' : `¥${formatMoney(t.ytd_outflow_cny, 2)}`}</b></div>
          <div><span>今年净浮盈（市值 − 年初 − 新增 + 取出）</span><b class="${signClass(t.ytd_profit_cny)}">${t.ytd_profit_cny === null ? '—' : `¥${formatMoney(t.ytd_profit_cny, 2)}`}</b></div>
        </div>
        ${dividendSection(stats, yearLabel)}
      </section>
    </div>`;
  }

  function dividendSection(data: PortfolioStats, yearLabel: string) {
    const d = data.dividends;
    const rows = d.items.map(item => `<tr>
      <td><div class="portfolio-asset-name">${esc(item.name || '未标明标的')}</div>${item.ticker_code ? `<div class="portfolio-asset-meta"><span>${esc(item.ticker_code)}</span></div>` : ''}</td>
      <td class="num">${item.count} 次${item.last_date ? `<div class="portfolio-sub">最近 ${formatDate(item.last_date)}</div>` : ''}</td>
      <td class="num">¥${formatMoney(item.year_cny, 2)}</td>
      <td class="num">¥${formatMoney(item.total_cny, 2)}</td>
      <td class="num ${signClass(item.yield_rate)}">${item.yield_rate === null ? '—' : formatPercent(item.yield_rate, 2)}</td>
    </tr>`).join('');
    return `<div class="portfolio-totals-year"><i data-lucide="hand-coins"></i>分红</div>
      <div class="portfolio-totals">
        <div><span>${yearLabel}累计分红</span><b class="${signClass(toNumber(d.year_cny) ? d.year_cny : null)}">¥${formatMoney(d.year_cny, 2)}</b></div>
        <div><span>累计分红</span><b>¥${formatMoney(d.total_cny, 2)}</b></div>
        <div><span>分红标的</span><b>${d.items.length} 只</b></div>
      </div>
      ${rows ? `<table class="portfolio-table portfolio-dividend-table">
        <thead><tr><th>标的</th><th class="num">次数</th><th class="num">${yearLabel}分红</th><th class="num">累计分红</th><th class="num">股息率</th></tr></thead>
        <tbody>${rows}</tbody></table>` : '<p class="portfolio-hint">还没有分红记录。在「实时组合市值」的现金账户行点「分红」，选择来源标的与金额即可。</p>'}
      <p class="portfolio-footnote">股息率 = ${yearLabel}该标的分红 ÷ 当前持仓成本（均折算人民币）；已清仓标的无持仓成本，股息率显示为 —。</p>`;
  }

  function drawCharts() {
    if (!stats || !stats.series.length) return;
    const dates = stats.series.map(p => p.date);
    const navHost = shell.querySelector<HTMLElement>('[data-chart="nav"]');
    if (navHost) {
      const chart = echarts.init(navHost);
      const navs = stats.series.map(p => toNumber(p.nav));
      const values = stats.series.map(p => toNumber(p.total_value_cny));
      chart.setOption({
        grid: { left: 52, right: 16, top: 18, bottom: 32 },
        tooltip: {
          trigger: 'axis',
          formatter: (params: any) => {
            const idx = params[0].dataIndex;
            const p = stats!.series[idx];
            return `${p.date}<br>净值 <b>${formatMoney(p.nav, 4)}</b><br>日收益 ${p.daily_return == null ? '-' : formatPercent(p.daily_return, 2, true)}<br>总市值 ¥${formatMoney(values[idx], 2)}${toNumber(p.net_flow_cny) ? `<br>资金${toNumber(p.net_flow_cny)! > 0 ? '流入' : '流出'} ¥${formatMoney(Math.abs(toNumber(p.net_flow_cny)!), 2)}` : ''}`;
          },
        },
        xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#78858d', fontSize: 11 }, axisLine: { lineStyle: { color: '#dfe4e7' } } },
        yAxis: { type: 'value', scale: true, axisLabel: { color: '#78858d', fontSize: 11, formatter: (v: number) => v.toFixed(3) }, splitLine: { lineStyle: { color: '#eef1f3' } } },
        series: [{
          type: 'line', data: navs, smooth: true, showSymbol: false, lineStyle: { width: 2, color: '#147c6e' },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(20,124,110,.28)' }, { offset: 1, color: 'rgba(20,124,110,.02)' }] } },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: '#b9c2c7', type: 'dashed' }, label: { show: false }, data: [{ yAxis: 1 }] },
        }],
      });
      charts.push(chart);
    }
    const xirrHost = shell.querySelector<HTMLElement>('[data-chart="xirr"]');
    if (xirrHost) {
      const points = stats.series.map(p => toNumber(p.xirr));
      if (points.some(v => v !== null)) {
        const chart = echarts.init(xirrHost);
        chart.setOption({
          grid: { left: 52, right: 16, top: 14, bottom: 28 },
          tooltip: { trigger: 'axis', valueFormatter: (v: number | null) => (v == null ? '-' : formatPercent(v, 2, true)) },
          xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#78858d', fontSize: 11 }, axisLine: { lineStyle: { color: '#dfe4e7' } } },
          yAxis: { type: 'value', scale: true, axisLabel: { color: '#78858d', fontSize: 11, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { lineStyle: { color: '#eef1f3' } } },
          series: [{
            type: 'line', name: 'XIRR', data: points, smooth: true, showSymbol: false, connectNulls: true, lineStyle: { width: 2, color: '#2f6fed' },
            markLine: { silent: true, symbol: 'none', lineStyle: { color: '#b9c2c7', type: 'dashed' }, label: { show: false }, data: [{ yAxis: 0 }] },
          }],
        });
        charts.push(chart);
      } else {
        xirrHost.innerHTML = '<p class="portfolio-hint">持有满 7 天后开始显示 XIRR 曲线</p>';
      }
    }
  }

  function bind() {
    const pid = portfolioId;
    if (pid === null) return;
    const all = aggregate();
    if (!all) {
      shell.querySelectorAll<HTMLButtonElement>('[data-buy]').forEach(button => {
        button.onclick = async () => { if (await openBuyDialog(pid, cashBalances())) await load(); };
      });
      shell.querySelector<HTMLButtonElement>('[data-tx]')?.addEventListener('click', () => { void openTransactionsDialog(pid); });
    }
    shell.querySelector<HTMLButtonElement>('[data-refresh]')?.addEventListener('click', () => { void load(); });
    shell.querySelectorAll<HTMLButtonElement>('[data-tasks]').forEach(button => { button.onclick = () => { void openSchedulerDialog(); }; });
    shell.querySelector<HTMLButtonElement>('[data-refresh-stats]')?.addEventListener('click', async () => {
      const current = ++seq;
      loading = true;
      render();
      try {
        stats = await portfolioApi.refreshStats(pid);
        overview = await portfolioApi.holdings(pid, false);
      } catch (error) {
        onError(error);
      } finally {
        if (current === seq) { loading = false; render(); }
      }
    });
    if (all) return;  // 聚合视图只读，没有增减持 / 流水按钮
    shell.querySelectorAll<HTMLButtonElement>('[data-dividend]').forEach(button => {
      button.onclick = async () => {
        const cash = overview?.holdings.find(h => String(h.id) === button.dataset.dividend);
        const sources = (overview?.holdings ?? []).filter(h => h.market_code !== 'CASH');
        if (!cash) return;
        if (!sources.length) { onError(new Error('组合里还没有证券持仓，无法记录分红。')); return; }
        if (await openDividendDialog(pid, cash, sources)) await load();
      };
    });
    shell.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(button => {
      button.onclick = async () => {
        const holding = overview?.holdings.find(h => String(h.id) === button.dataset.holdingId);
        if (!holding) return;
        if (await openAdjustDialog(pid, holding, button.dataset.action as HoldingAction, cashBalances())) await load();
      };
    });
    shell.querySelectorAll<HTMLButtonElement>('[data-holding-tx]').forEach(button => {
      button.onclick = () => {
        const holding = overview?.holdings.find(h => String(h.id) === button.dataset.holdingTx);
        if (holding) void openTransactionsDialog(pid, holding);
      };
    });
  }

  return {
    async show(id, nextView) {
      const changed = id !== portfolioId;
      portfolioId = id;
      view = nextView;
      if (changed) { overview = null; stats = null; }
      await load();
    },
    refresh: () => load(),
    destroy() {
      disposeCharts();
      window.removeEventListener('resize', resize);
    },
  };
}
