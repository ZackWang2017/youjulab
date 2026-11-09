import { describe, expect, it } from 'vitest';
import { createAccountsDiagram, normalizeAccounts } from './accounts-model';

describe('financial account definitions', () => {
  const create = () => createAccountsDiagram('账户图', { ticker_code: '00700.HK', name: '腾讯控股' }, 'HKFRS');
  it('keeps every template reference valid and supports compound entries', () => {
    const model = create();
    const ids = new Set(model.accounts.map(a => a.id));
    expect(ids.size).toBe(model.accounts.length);
    expect(model.rules.every(r => r.debits.length && r.credits.length && [...r.debits, ...r.credits].every(id => ids.has(id)))).toBe(true);
    expect(model.rules.some(r => r.debits.length > 1)).toBe(true);
    expect(normalizeAccounts(JSON.parse(JSON.stringify(model)))).toEqual(model);
  });
  it('removes duplicate accounts and dangling references on loading', () => {
    const model = create();
    model.accounts.push(model.accounts[0]);
    model.rules[0].debits.push('missing');
    model.rules[0].next.push('missing', model.rules[0].id);
    const clean = normalizeAccounts(model);
    expect(clean.accounts.length).toBe(model.accounts.length - 1);
    expect(clean.rules[0].debits).not.toContain('missing');
    expect(clean.rules[0].next).toEqual(['collection']);
  });
  it('requires a supported accounting standard', () => {
    expect(() => normalizeAccounts({ ...create(), standard: 'unknown' })).toThrow('会计准则');
  });
});
