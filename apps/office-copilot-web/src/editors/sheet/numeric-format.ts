/** Pure numeric text to number; preserves identifiers with leading zeroes. */
export function coerceNumericString(value: string): number | null {
  const raw = value.trim();
  if (!raw) return null;
  const isPercent = /%$/.test(raw);
  const normalized = raw.replace(/,/g, '').replace(/%$/, '').trim();
  if (!normalized || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(normalized)) return null;
  if (!isPercent && /^[+-]?0\d+$/.test(normalized)) return null;
  const number = Number(normalized);
  return Number.isFinite(number) ? (isPercent ? number / 100 : number) : null;
}

/** Keeps an existing format, otherwise infers a suitable number format from text. */
export function inferOrKeepNumberFormat(rawText: string, existing: string | null | undefined): string {
  const current = String(existing || '').trim();
  if (current && current.toLowerCase() !== 'general') return current;
  const text = rawText.trim();
  if (/%$/.test(text)) return '0.00%';
  const hasComma = text.includes(',');
  const normalized = text.replace(/,/g, '').replace(/%$/, '');
  const fraction = normalized.includes('.') ? (normalized.split('.')[1] || '').replace(/e.*/i, '') : '';
  if (hasComma && fraction) return `#,##0.${'0'.repeat(Math.min(fraction.length, 6))}`;
  if (hasComma) return '#,##0';
  if (fraction) return `0.${'0'.repeat(Math.min(fraction.length, 6))}`;
  return current || 'General';
}
