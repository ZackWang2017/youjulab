type Numeric = number | string | null | undefined;

export function toNumber(value: Numeric): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(n) ? n : null;
}

export function formatMoney(value: Numeric, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return '-';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 小数比率（0.0123 → 1.23%）。 */
export function formatPercent(value: Numeric, digits = 2, signed = false): string {
  const n = toNumber(value);
  if (n === null) return '-';
  const text = `${(n * 100).toFixed(digits)}%`;
  return signed && n > 0 ? `+${text}` : text;
}

/** 已经是百分点（12.3 → 12.30%）。 */
export function formatPercentPoint(value: Numeric, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return '-';
  return `${n.toFixed(digits)}%`;
}

const SYMBOL: Record<string, string> = { CNY: '¥', HKD: 'HK$', USD: 'US$' };

export function currencySymbol(currency: string | null | undefined): string {
  if (!currency) return '';
  return SYMBOL[currency.toUpperCase()] ?? currency.toUpperCase();
}

export function formatMoneyWithCurrency(value: Numeric, currency: string | null | undefined, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return '-';
  return `${currencySymbol(currency)}${formatMoney(n, digits)}`;
}

export function formatShares(value: Numeric): string {
  const n = toNumber(value);
  if (n === null) return '-';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function signClass(value: Numeric): string {
  const n = toNumber(value);
  if (n === null || n === 0) return '';
  return n > 0 ? 'is-up' : 'is-down';
}

export function localDateInputValue(date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
