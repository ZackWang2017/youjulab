export const ACCOUNT_GROUPS = { asset: '资产', liability: '负债', equity: '权益', income: '收入', expense: '费用' } as const;
export const ACCOUNTING_STANDARDS = { CAS: '中国企业会计准则', IFRS: '国际财务报告准则（IFRS）', HKFRS: '香港财务报告准则（HKFRS）', US_GAAP: '美国会计准则（US GAAP）' } as const;
export type AccountGroup = keyof typeof ACCOUNT_GROUPS;
export type FinancialAccount = { id: string; name: string; group: AccountGroup; normal: 'debit' | 'credit'; note: string; position: { x: number; y: number } };
export type AccountingRule = { id: string; name: string; condition: string; tag: string; source: string; debits: string[]; credits: string[]; next: string[] };
export type AccountsDiagram = {
  format: 'invp-research-diagram-v1'; type: 'financial_accounts'; title: string;
  stock: { ticker_code: string; name: string }; standard: keyof typeof ACCOUNTING_STANDARDS;
  accounts: FinancialAccount[]; rules: AccountingRule[];
};
export const accountId = () => crypto.randomUUID();
export function createAccountsDiagram(title: string, stock: AccountsDiagram['stock'], standard: AccountsDiagram['standard']): AccountsDiagram {
  const groups: Array<[AccountGroup, string[]]> = [
    ['asset', ['现金及现金等价物', '应收账款', '存货', '固定资产', '累计折旧', '无形资产', '长期股权投资', '坏账准备']],
    ['liability', ['应付账款', '合同负债', '借款', '应交税费']],
    ['equity', ['股本及资本公积', '留存收益']],
    ['income', ['营业收入', '投资收益']],
    ['expense', ['营业成本', '研发费用', '销售及管理费用', '信用减值损失', '所得税费用', '财务费用']],
  ];
  const accounts = groups.flatMap(([group, names], column) => names.map((name, row): FinancialAccount => ({
    id: `${group}-${row}`, name, group, normal: ['liability', 'equity', 'income'].includes(group) || ['累计折旧', '坏账准备'].includes(name) ? 'credit' : 'debit',
    note: '', position: { x: column * 340, y: row * 300 },
  })));
  const find = (name: string) => accounts.find(a => a.name === name)!.id;
  const rule = (id: string, name: string, debits: string[], credits: string[], condition: string, tag: string, next: string[] = []): AccountingRule => ({ id, name, debits: debits.map(find), credits: credits.map(find), condition, tag, source: '', next });
  return { format: 'invp-research-diagram-v1', type: 'financial_accounts', title, stock, standard, accounts, rules: [
    rule('sale', '赊销确认收入', ['应收账款'], ['营业收入'], '满足收入确认条件，尚未收到客户付款；此基础模板未拆分税项。', '销售', ['collection']),
    rule('collection', '客户回款', ['现金及现金等价物'], ['应收账款'], '收到客户对已确认应收款的支付。', '销售'),
    rule('purchase', '赊购存货', ['存货'], ['应付账款'], '存货已取得控制权，供应商款项尚未支付。', '采购', ['supplier', 'cost']),
    rule('supplier', '支付供应商', ['应付账款'], ['现金及现金等价物'], '支付已确认的采购应付款。', '采购'),
    rule('cost', '结转销售成本', ['营业成本'], ['存货'], '对应商品收入确认时结转账面成本。', '销售'),
    rule('advance', '收到客户预付款', ['现金及现金等价物'], ['合同负债'], '收到客户付款，但尚未履行对应履约义务。', '预收', ['recognition']),
    rule('recognition', '履约确认收入', ['合同负债'], ['营业收入'], '履行对应义务，将预收款确认为收入。', '预收'),
    rule('capex', '购建设备', ['固定资产'], ['现金及现金等价物'], '设备达到资本化确认条件；按实际政策补充在建工程等账户。', '资本开支', ['depreciation']),
    rule('depreciation', '计提折旧', ['营业成本', '销售及管理费用'], ['累计折旧'], '按资产用途分配折旧；借方项目为分配关系，按公司实际情况调整。', '资本开支'),
    rule('impairment', '计提信用减值', ['信用减值损失'], ['坏账准备'], '按适用准则确认应收款的预期信用损失。', '减值'),
    rule('borrow', '取得借款', ['现金及现金等价物'], ['借款'], '收到借款本金。', '融资', ['repay']),
    rule('repay', '偿还借款本金', ['借款'], ['现金及现金等价物'], '支付借款本金，利息另行定义。', '融资'),
    rule('capital', '股东投入资本', ['现金及现金等价物'], ['股本及资本公积'], '收到股东出资，股本与溢价按公司情况细分。', '融资'),
    rule('tax', '确认当期所得税', ['所得税费用'], ['应交税费'], '仅定义当期所得税，递延所得税需另设账户及规则。', '税务'),
  ] };
}

export function normalizeAccounts(raw: Record<string, unknown>): AccountsDiagram {
  if (!raw.stock || typeof raw.stock !== 'object' || !('ticker_code' in raw.stock) || !('name' in raw.stock)) throw new Error('财务账户分析缺少股票信息。');
  if (!(String(raw.standard) in ACCOUNTING_STANDARDS)) throw new Error('财务账户分析的会计准则无效。');
  const accounts: FinancialAccount[] = [];
  const ids = new Set<string>();
  for (const value of Array.isArray(raw.accounts) ? raw.accounts : []) {
    if (!value || typeof value !== 'object') continue;
    const a = value as FinancialAccount;
    if (!a.id || ids.has(a.id) || !(a.group in ACCOUNT_GROUPS)) continue;
    ids.add(a.id);
    accounts.push({ id: a.id, name: String(a.name || '未命名账户'), group: a.group, normal: a.normal === 'credit' ? 'credit' : 'debit', note: String(a.note || ''), position: { x: Number.isFinite(a.position?.x) ? a.position.x : 0, y: Number.isFinite(a.position?.y) ? a.position.y : 0 } });
  }
  const rules: AccountingRule[] = [];
  const ruleIds = new Set<string>();
  for (const value of Array.isArray(raw.rules) ? raw.rules : []) {
    if (!value || typeof value !== 'object') continue;
    const r = value as AccountingRule;
    if (!r.id || ruleIds.has(r.id)) continue;
    ruleIds.add(r.id);
    rules.push({ id: r.id, name: String(r.name || ''), condition: String(r.condition || ''), tag: String(r.tag || ''), source: String(r.source || ''), debits: [...new Set((Array.isArray(r.debits) ? r.debits : []).filter(id => ids.has(id)))], credits: [...new Set((Array.isArray(r.credits) ? r.credits : []).filter(id => ids.has(id)))], next: Array.isArray(r.next) ? r.next : [] });
  }
  rules.forEach(r => { r.next = [...new Set(r.next.filter(id => id !== r.id && ruleIds.has(id)))]; });
  return { format: 'invp-research-diagram-v1', type: 'financial_accounts', title: String(raw.title || ''), stock: { ticker_code: String(raw.stock.ticker_code), name: String(raw.stock.name) }, standard: raw.standard as AccountsDiagram['standard'], accounts, rules };
}
