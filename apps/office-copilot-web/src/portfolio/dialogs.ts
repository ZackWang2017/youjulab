import { createIcons, icons } from 'lucide';
import {
  portfolioApi, type BuyBody, type Holding, type HoldingAction, type ScheduledTask, type SecurityHit, type Transaction, type TransactionBody,
} from './api';
import { formatDateTime, formatMoney, formatMoneyWithCurrency, formatShares, localDateInputValue, toNumber } from './format';

const esc = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));

const CASH_OPTIONS = [
  { ticker_code: 'CNY.CASH', name: '人民币现金 (CNY)' },
  { ticker_code: 'HKD.CASH', name: '港元现金 (HKD)' },
  { ticker_code: 'USD.CASH', name: '美元现金 (USD)' },
];

function currencyOfTicker(ticker: string): string {
  const [code, market] = ticker.toUpperCase().split('.');
  if (market === 'CASH') return code;
  if (market === 'US') return 'USD';
  if (market === 'HK') return 'HKD';
  if (market === 'SH' && code.startsWith('9')) return 'USD';
  if (market === 'SZ' && code.startsWith('200')) return 'HKD';
  return 'CNY';
}

/** 搜索结果里的市场标签：A 股 / B 股 / 港股 / 美股 / 美股ETF。 */
function marketTag(item: SecurityHit): string {
  const [code, market] = item.ticker_code.toUpperCase().split('.');
  if (market === 'CASH') return '现金';
  if (market === 'US') return item.security_type === '基金' ? '美股ETF' : '美股';
  if (market === 'HK') return '港股';
  if (item.security_type === '基金') return '场内基金';
  if ((market === 'SH' && code.startsWith('9')) || (market === 'SZ' && code.startsWith('200'))) return 'B 股';
  return 'A 股';
}

function overlay(html: string) {
  const el = document.createElement('div');
  el.className = 'dialog-overlay portfolio-dialog-overlay';
  el.innerHTML = html;
  document.body.append(el);
  createIcons({ icons });
  return el;
}

function showError(form: HTMLElement, message: string) {
  const box = form.querySelector<HTMLElement>('[data-error]');
  if (!box) return;
  box.hidden = !message;
  box.textContent = message;
}

function busy(form: HTMLFormElement, on: boolean) {
  form.querySelectorAll<HTMLButtonElement>('button').forEach(button => { button.disabled = on; });
}

function occurredAt(dateValue: string): string | undefined {
  if (!dateValue) return undefined;
  // 以当天本地 15:30 记账，保证落在交易日内，且时区信息完整
  const d = new Date(`${dateValue}T15:30:00`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

/** 买入 / 存入现金。cashCurrencies：组合里已有的现金账户币种，用来默认勾选「用组合现金结算」。 */
export function openBuyDialog(portfolioId: number, cashBalances: Record<string, number>): Promise<boolean> {
  return new Promise(resolve => {
    const el = overlay(`<form class="name-dialog portfolio-dialog portfolio-buy-dialog">
      <h2>买入 / 存入</h2>
      <label class="field-label">证券或现金</label>
      <div class="portfolio-combobox">
        <input data-search autocomplete="off" placeholder="输入代码或名称搜索（支持 A 股、B 股、港股、美股及 ETF），或选择现金" required>
        <div class="portfolio-suggest" data-suggest hidden></div>
      </div>
      <div class="portfolio-picked" data-picked hidden></div>
      <div class="portfolio-grid-2">
        <label><span data-price-label>买入价格</span><input data-price type="number" step="any" min="0" placeholder="0.00"></label>
        <label><span data-shares-label>数量</span><input data-shares type="number" step="any" min="0" required placeholder="0"></label>
      </div>
      <div class="portfolio-grid-2">
        <label><span>发生日期</span><input data-date type="date" value="${localDateInputValue()}"></label>
        <label><span>目标占比 %（可选）</span><input data-target type="number" step="0.01" min="0" max="100" placeholder="如 20"></label>
      </div>
      <label class="portfolio-check" data-settle-wrap hidden><input data-settle type="checkbox"><span>用组合现金结算（从 <b data-settle-ccy></b> 现金账户扣款，余额 <b data-settle-balance></b>）</span></label>
      <label><span>备注（可选）</span><input data-note maxlength="200"></label>
      <p class="portfolio-hint" data-hint>买入证券时若不勾选「用组合现金结算」，这笔金额会记为外部资金投入，用于净值法与 XIRR 计算。</p>
      <div class="portfolio-dialog-error" data-error role="alert" hidden></div>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">确认买入</button></div>
    </form>`);
    const form = el.querySelector('form')!;
    const search = el.querySelector<HTMLInputElement>('[data-search]')!;
    const suggest = el.querySelector<HTMLElement>('[data-suggest]')!;
    const picked = el.querySelector<HTMLElement>('[data-picked]')!;
    const price = el.querySelector<HTMLInputElement>('[data-price]')!;
    const shares = el.querySelector<HTMLInputElement>('[data-shares]')!;
    const settleWrap = el.querySelector<HTMLElement>('[data-settle-wrap]')!;
    const settle = el.querySelector<HTMLInputElement>('[data-settle]')!;
    let selected: SecurityHit | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let seq = 0;

    const finish = (changed: boolean) => { el.remove(); resolve(changed); };

    const renderSuggest = (items: SecurityHit[], note = '') => {
      suggest.innerHTML = '';
      if (note) {
        const p = document.createElement('div');
        p.className = 'portfolio-suggest-note';
        p.textContent = note;
        suggest.append(p);
      }
      items.forEach(item => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.ticker = item.ticker_code;
        button.innerHTML = `<span>${esc(item.name)}</span><small><em>${esc(marketTag(item))}</em>${esc(item.ticker_code)}</small>`;
        button.addEventListener('click', () => pick(item));
        suggest.append(button);
      });
      suggest.hidden = suggest.childElementCount === 0;
    };

    const pick = (item: SecurityHit) => {
      selected = item;
      const isCash = item.ticker_code.endsWith('.CASH');
      search.value = `${item.name}（${item.ticker_code}）`;
      suggest.hidden = true;
      picked.hidden = false;
      picked.textContent = isCash ? '现金账户：金额按 1:1 记账' : `计价币种 ${currencyOfTicker(item.ticker_code)}`;
      el.querySelector('[data-price-label]')!.textContent = isCash ? '价格（现金固定为 1）' : '买入价格';
      el.querySelector('[data-shares-label]')!.textContent = isCash ? '存入金额' : '数量';
      price.disabled = isCash;
      price.required = !isCash;
      if (isCash) price.value = '1';
      else if (price.value === '1') price.value = '';
      form.querySelector<HTMLButtonElement>('button[type="submit"]')!.textContent = isCash ? '确认存入' : '确认买入';
      const ccy = currencyOfTicker(item.ticker_code);
      const balance = cashBalances[ccy];
      if (!isCash && balance !== undefined) {
        settleWrap.hidden = false;
        settle.checked = true;
        el.querySelector('[data-settle-ccy]')!.textContent = ccy;
        el.querySelector('[data-settle-balance]')!.textContent = formatMoney(balance, 2);
      } else {
        settleWrap.hidden = true;
        settle.checked = false;
      }
      el.querySelector<HTMLElement>('[data-hint]')!.hidden = isCash;
      (isCash ? shares : price).focus();
    };

    search.addEventListener('input', () => {
      selected = null;
      picked.hidden = true;
      settleWrap.hidden = true;
      const text = search.value.trim();
      clearTimeout(timer);
      if (!text) { renderSuggest(CASH_OPTIONS, '现金账户'); return; }
      const cashHits = CASH_OPTIONS.filter(c => c.name.toLowerCase().includes(text.toLowerCase()) || c.ticker_code.toLowerCase().includes(text.toLowerCase()));
      const current = ++seq;
      timer = setTimeout(async () => {
        let items: SecurityHit[] = [];
        try { items = await portfolioApi.searchSecurities(text); } catch { items = []; }
        if (current !== seq) return;
        // 美股代码在证券表里查不到时（尚未同步 US 清单），允许手动输入
        const usCode = text.toUpperCase().replace(/\.US$/, '');
        const manual: SecurityHit[] = /^[A-Za-z]{1,6}(\.US)?$/i.test(text) && !items.some(i => i.ticker_code === `${usCode}.US`)
          ? [{ ticker_code: `${usCode}.US`, name: `${usCode}（美股，手动输入）` }]
          : [];
        renderSuggest([...cashHits, ...items, ...manual], items.length || cashHits.length || manual.length ? '' : '没有匹配的证券');
      }, 220);
    });
    search.addEventListener('focus', () => { if (!selected && !search.value.trim()) renderSuggest(CASH_OPTIONS, '现金账户'); });
    el.addEventListener('click', e => { if (e.target === el) finish(false); if (!(e.target as Element).closest('.portfolio-combobox')) suggest.hidden = true; });
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));

    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (!selected) { showError(form, '请先从搜索结果中选择证券或现金。'); search.focus(); return; }
      const isCash = selected.ticker_code.endsWith('.CASH');
      const qty = toNumber(shares.value);
      if (!qty || qty <= 0) { showError(form, isCash ? '请输入存入金额。' : '请输入数量。'); return; }
      const body: BuyBody = {
        ticker_code: selected.ticker_code,
        name: isCash ? undefined : selected.name.replace(/（.*$/, ''),
        shares: shares.value.trim(),
        price: isCash ? '1' : price.value.trim(),
        occurred_at: occurredAt(el.querySelector<HTMLInputElement>('[data-date]')!.value),
        note: el.querySelector<HTMLInputElement>('[data-note]')!.value.trim() || undefined,
        target_allocation_pct: el.querySelector<HTMLInputElement>('[data-target]')!.value.trim() || undefined,
        settle_with_cash: !isCash && !settleWrap.hidden && settle.checked,
      };
      if (!isCash && !(toNumber(body.price) && toNumber(body.price)! > 0)) { showError(form, '请输入买入价格。'); return; }
      showError(form, '');
      busy(form, true);
      try {
        await portfolioApi.buy(portfolioId, body);
        finish(true);
      } catch (error) {
        busy(form, false);
        showError(form, error instanceof Error ? error.message : String(error));
      }
    });
    search.focus();
  });
}

const ACTION_LABEL: Record<HoldingAction, string> = { add: '增持', reduce: '减持', close: '清仓', deposit: '存入', withdraw: '取出', dividend: '分红' };

/** 分红入账：金额记入该现金账户，并归属到组合里的一只证券，供分红统计使用。 */
export function openDividendDialog(portfolioId: number, cash: Holding, sources: Holding[]): Promise<boolean> {
  return new Promise(resolve => {
    const options = sources.map(h => `<option value="${esc(h.ticker_code)}">${esc(h.name)}（${esc(h.ticker_code)}）</option>`).join('');
    const el = overlay(`<form class="name-dialog portfolio-dialog">
      <h2>分红 · ${esc(cash.name)}</h2>
      <p class="portfolio-dialog-sub">分红记入 ${esc(cash.currency)} 现金账户，现有余额 ${formatMoney(cash.shares, 2)}。分红是持仓收益，不计入外部资金投入，不影响净值。</p>
      <label><span>分红来源标的</span><select data-source required>${options || '<option value="">组合里还没有证券</option>'}</select></label>
      <div class="portfolio-grid-2">
        <label><span>分红金额（${esc(cash.currency)}）</span><input data-amount type="number" step="any" min="0" required placeholder="0.00"></label>
        <label><span>到账日期</span><input data-date type="date" value="${localDateInputValue()}"></label>
      </div>
      <label><span>备注（可选）</span><input data-note maxlength="120" placeholder="如：2025 年度分红"></label>
      <div class="portfolio-dialog-error" data-error role="alert" hidden></div>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">确认入账</button></div>
    </form>`);
    const form = el.querySelector('form')!;
    const finish = (changed: boolean) => { el.remove(); resolve(changed); };
    el.addEventListener('click', e => { if (e.target === el) finish(false); });
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const amount = el.querySelector<HTMLInputElement>('[data-amount]')!.value.trim();
      const source = el.querySelector<HTMLSelectElement>('[data-source]')!.value;
      if (!source) { showError(form, '请选择分红来源标的。'); return; }
      if (!(toNumber(amount) && toNumber(amount)! > 0)) { showError(form, '请输入分红金额。'); return; }
      showError(form, '');
      busy(form, true);
      try {
        await portfolioApi.transact(portfolioId, cash.id, {
          action: 'dividend',
          shares: amount,
          source_ticker: source,
          occurred_at: occurredAt(el.querySelector<HTMLInputElement>('[data-date]')!.value),
          note: el.querySelector<HTMLInputElement>('[data-note]')!.value.trim() || undefined,
        });
        finish(true);
      } catch (error) {
        busy(form, false);
        showError(form, error instanceof Error ? error.message : String(error));
      }
    });
    el.querySelector<HTMLInputElement>('[data-amount]')!.focus();
  });
}

/** 对已有持仓的增持 / 减持 / 清仓，或现金账户的存入 / 取出。 */
export function openAdjustDialog(portfolioId: number, holding: Holding, action: HoldingAction, cashBalances: Record<string, number>): Promise<boolean> {
  return new Promise(resolve => {
    const isCash = holding.market_code === 'CASH';
    const label = ACTION_LABEL[action];
    const currentShares = toNumber(holding.shares) ?? 0;
    const balance = cashBalances[holding.currency];
    const canSettle = !isCash && balance !== undefined;
    const settleText = action === 'add'
      ? `用组合现金结算（从 ${holding.currency} 现金账户扣款，余额 ${formatMoney(balance ?? 0, 2)}）`
      : `卖出所得记入 ${holding.currency} 现金账户`;
    const el = overlay(`<form class="name-dialog portfolio-dialog">
      <h2>${label} · ${esc(holding.name)}</h2>
      <p class="portfolio-dialog-sub">${esc(holding.ticker_code)} · 现有 ${isCash ? formatMoney(holding.shares, 2) : formatShares(holding.shares)}${isCash ? ` ${esc(holding.currency)}` : ' 股'} · 均价 ${formatMoneyWithCurrency(holding.avg_cost, holding.currency, 4)}</p>
      <div class="portfolio-grid-2">
        ${isCash ? '' : `<label><span>${action === 'close' ? '清仓价格' : `${label}价格`}</span><input data-price type="number" step="any" min="0" required value="${esc(holding.current_price ?? '')}"></label>`}
        <label><span>${isCash ? `${label}金额` : action === 'close' ? '数量（全部）' : `${label}数量`}</span><input data-shares type="number" step="any" min="0" required ${action === 'close' ? `value="${currentShares}" readonly` : ''}></label>
      </div>
      <div class="portfolio-grid-2">
        <label><span>发生日期</span><input data-date type="date" value="${localDateInputValue()}"></label>
        <label><span>目标占比 %（可选）</span><input data-target type="number" step="0.01" min="0" max="100" value="${esc(holding.target_allocation_pct ?? '')}"></label>
      </div>
      ${canSettle ? `<label class="portfolio-check"><input data-settle type="checkbox" checked><span>${esc(settleText)}</span></label>` : ''}
      <label><span>备注（可选）</span><input data-note maxlength="200"></label>
      ${isCash ? `<p class="portfolio-hint">${action === 'deposit' ? '存入' : '取出'}会记为外部资金${action === 'deposit' ? '投入' : '取出'}，用于净值法与 XIRR 计算。</p>` : `<p class="portfolio-hint">不勾选现金结算时，${action === 'add' ? '增持金额记为外部资金投入' : '卖出金额记为外部资金取出'}。</p>`}
      <div class="portfolio-dialog-error" data-error role="alert" hidden></div>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary ${action === 'close' ? 'portfolio-danger' : ''}" type="submit">确认${label}</button></div>
    </form>`);
    const form = el.querySelector('form')!;
    const finish = (changed: boolean) => { el.remove(); resolve(changed); };
    el.addEventListener('click', e => { if (e.target === el) finish(false); });
    el.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const shares = el.querySelector<HTMLInputElement>('[data-shares]')!.value.trim();
      const priceInput = el.querySelector<HTMLInputElement>('[data-price]');
      const body: TransactionBody = {
        action,
        shares,
        price: isCash ? '1' : priceInput?.value.trim(),
        occurred_at: occurredAt(el.querySelector<HTMLInputElement>('[data-date]')!.value),
        note: el.querySelector<HTMLInputElement>('[data-note]')!.value.trim() || undefined,
        target_allocation_pct: el.querySelector<HTMLInputElement>('[data-target]')!.value.trim() || undefined,
        settle_with_cash: canSettle ? el.querySelector<HTMLInputElement>('[data-settle]')!.checked : false,
      };
      if (!(toNumber(shares) && toNumber(shares)! > 0)) { showError(form, isCash ? '请输入金额。' : '请输入数量。'); return; }
      if (!isCash && !(toNumber(body.price) && toNumber(body.price)! > 0)) { showError(form, '请输入价格。'); return; }
      showError(form, '');
      busy(form, true);
      try {
        await portfolioApi.transact(portfolioId, holding.id, body);
        finish(true);
      } catch (error) {
        busy(form, false);
        showError(form, error instanceof Error ? error.message : String(error));
      }
    });
    (el.querySelector<HTMLInputElement>('[data-price]') ?? el.querySelector<HTMLInputElement>('[data-shares]'))!.focus();
  });
}

const TX_LABEL: Record<string, string> = {
  buy: '买入', add: '增持', reduce: '减持', close: '清仓', deposit: '存入', withdraw: '取出', dividend: '分红', cash_in: '现金入账', cash_out: '现金扣款',
};

export async function openTransactionsDialog(portfolioId: number, holding?: Holding) {
  const el = overlay(`<div class="name-dialog portfolio-dialog portfolio-tx-dialog" role="dialog">
    <h2>${holding ? `交易流水 · ${esc(holding.name)}` : '组合流水'}</h2>
    <div class="portfolio-tx-body" data-body><p class="portfolio-hint">正在加载…</p></div>
    <div class="dialog-actions"><button type="button" class="primary" data-close>关闭</button></div>
  </div>`);
  const finish = () => el.remove();
  el.addEventListener('click', e => { if (e.target === el) finish(); });
  el.querySelector('[data-close]')!.addEventListener('click', finish);
  const body = el.querySelector<HTMLElement>('[data-body]')!;
  try {
    const rows = await portfolioApi.transactions(portfolioId, holding?.id);
    body.innerHTML = rows.length ? renderTxTable(rows) : '<p class="portfolio-hint">暂无流水。</p>';
  } catch (error) {
    body.innerHTML = `<p class="portfolio-dialog-error">${esc(error instanceof Error ? error.message : String(error))}</p>`;
  }
}

function renderTxTable(rows: Transaction[]) {
  return `<table class="portfolio-table portfolio-tx-table"><thead><tr><th>时间</th><th>品种</th><th>动作</th><th class="num">价格</th><th class="num">数量/金额</th><th class="num">金额</th><th class="num">外部资金</th><th>备注</th></tr></thead><tbody>${rows.map(t => `<tr>
    <td>${formatDateTime(t.occurred_at)}</td>
    <td>${esc(t.ticker_code)}</td>
    <td>${esc(TX_LABEL[t.action] ?? t.action)}</td>
    <td class="num">${formatMoney(t.price, 4)}</td>
    <td class="num">${formatShares(t.shares)}</td>
    <td class="num">${formatMoneyWithCurrency(t.amount, t.currency, 2)}</td>
    <td class="num ${toNumber(t.external_flow) ? (toNumber(t.external_flow)! > 0 ? 'is-up' : 'is-down') : ''}">${toNumber(t.external_flow) ? formatMoneyWithCurrency(t.external_flow, t.currency, 2) : '-'}</td>
    <td>${esc(t.note ?? '')}</td>
  </tr>`).join('')}</tbody></table>`;
}

const STATUS_LABEL: Record<string, string> = { success: '已完成', failed: '失败', running: '执行中' };

export async function openSchedulerDialog() {
  const el = overlay(`<div class="name-dialog portfolio-dialog portfolio-scheduler-dialog" role="dialog">
    <h2>收盘后计划任务</h2>
    <p class="portfolio-dialog-sub">工作日固定时间自动执行：快照汇率 → 补收盘价 → 按净值法重算每日净值。服务晚于触发时间启动时会自动补跑。</p>
    <div data-body><p class="portfolio-hint">正在加载…</p></div>
    <div class="dialog-actions"><button type="button" class="primary" data-close>关闭</button></div>
  </div>`);
  const finish = () => el.remove();
  el.addEventListener('click', e => { if (e.target === el) finish(); });
  el.querySelector('[data-close]')!.addEventListener('click', finish);
  const body = el.querySelector<HTMLElement>('[data-body]')!;
  const load = async () => {
    try {
      const data = await portfolioApi.tasks();
      body.innerHTML = renderTasks(data.tasks, data.history);
      body.querySelectorAll<HTMLButtonElement>('[data-run]').forEach(button => {
        button.onclick = async () => {
          button.disabled = true;
          button.textContent = '执行中…';
          try { await portfolioApi.runTask(button.dataset.run!); }
          catch (error) { window.dispatchEvent(new CustomEvent('office-error', { detail: error instanceof Error ? error.message : String(error) })); }
          await load();
        };
      });
    } catch (error) {
      body.innerHTML = `<p class="portfolio-dialog-error">${esc(error instanceof Error ? error.message : String(error))}</p>`;
    }
  };
  await load();
}

function renderTasks(tasks: ScheduledTask[], history: Array<Record<string, string | null>>) {
  const taskRows = tasks.map(t => `<tr>
    <td><div class="portfolio-task-name">${esc(t.name)}</div><small>${esc(t.description)}</small></td>
    <td>${esc(t.at)} <small>${esc(t.timezone)}${t.weekdays_only ? ' · 工作日' : ''}</small></td>
    <td>${t.depends_on.length ? t.depends_on.map(esc).join('<br>') : '-'}</td>
    <td><span class="portfolio-badge ${t.today_status === 'success' ? 'is-ok' : t.today_status === 'failed' ? 'is-bad' : ''}">${t.running ? '执行中' : t.today_status ? STATUS_LABEL[t.today_status] ?? t.today_status : '今日未执行'}</span>${t.today_detail ? `<div class="portfolio-task-detail" title="${esc(t.today_detail)}">${esc(t.today_detail.slice(0, 120))}</div>` : ''}</td>
    <td><button type="button" data-run="${esc(t.name)}" ${t.running ? 'disabled' : ''}>立即执行</button></td>
  </tr>`).join('');
  const historyRows = history.slice(0, 15).map(h => `<tr><td>${esc(h.run_date ?? '')}</td><td>${esc(h.name ?? '')}</td><td><span class="portfolio-badge ${h.status === 'success' ? 'is-ok' : h.status === 'failed' ? 'is-bad' : ''}">${esc(STATUS_LABEL[h.status ?? ''] ?? h.status ?? '')}</span></td><td>${formatDateTime(h.finished_at)}</td><td class="portfolio-task-detail" title="${esc(h.detail ?? '')}">${esc((h.detail ?? '').slice(0, 90))}</td></tr>`).join('');
  return `<table class="portfolio-table portfolio-task-table"><thead><tr><th>任务</th><th>触发</th><th>依赖</th><th>今日状态</th><th></th></tr></thead><tbody>${taskRows}</tbody></table>
    <h3 class="portfolio-subtitle">最近运行</h3>
    ${historyRows ? `<table class="portfolio-table portfolio-task-table"><thead><tr><th>日期</th><th>任务</th><th>状态</th><th>完成时间</th><th>结果</th></tr></thead><tbody>${historyRows}</tbody></table>` : '<p class="portfolio-hint">还没有运行记录。</p>'}`;
}
