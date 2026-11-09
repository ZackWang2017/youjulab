type AccountCategory = 'asset' | 'liability' | 'equity' | 'income' | 'expense' | 'contraAsset' | 'blank';
type AccountTemplate = { id: string; name: string; category: AccountCategory };

export const ACCOUNT_TEMPLATES: AccountTemplate[] = [
  { id: 'blank', name: '空白丁字账', category: 'blank' },
  { id: 'cash', name: '货币资金', category: 'asset' },
  { id: 'receivables', name: '应收账款', category: 'asset' },
  { id: 'inventory', name: '存货', category: 'asset' },
  { id: 'prepayments', name: '预付款项', category: 'asset' },
  { id: 'fixed-assets', name: '固定资产', category: 'asset' },
  { id: 'depreciation', name: '累计折旧', category: 'contraAsset' },
  { id: 'payables', name: '应付账款', category: 'liability' },
  { id: 'advances', name: '合同负债', category: 'liability' },
  { id: 'borrowings', name: '借款', category: 'liability' },
  { id: 'capital', name: '实收资本／股本', category: 'equity' },
  { id: 'earnings', name: '未分配利润', category: 'equity' },
  { id: 'revenue', name: '营业收入', category: 'income' },
  { id: 'cost', name: '营业成本', category: 'expense' },
  { id: 'expenses', name: '期间费用', category: 'expense' },
];

const CATEGORIES: Record<AccountCategory, { color: string; debit: string; credit: string }> = {
  asset: { color: '#EAF4F0', debit: '借方 · 增加', credit: '贷方 · 减少' },
  liability: { color: '#EDF3FC', debit: '借方 · 减少', credit: '贷方 · 增加' },
  equity: { color: '#F1EEFA', debit: '借方 · 减少', credit: '贷方 · 增加' },
  income: { color: '#EAF4F0', debit: '借方 · 减少／结转', credit: '贷方 · 增加' },
  expense: { color: '#FFF1EB', debit: '借方 · 增加', credit: '贷方 · 减少／结转' },
  contraAsset: { color: '#EDF3FC', debit: '借方 · 减少', credit: '贷方 · 增加' },
  blank: { color: '#F1F4F6', debit: '借方', credit: '贷方' },
};

function escapeXml(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Ordinary grouped mxCells: no stencil, image, plugin, or executable content. */
export function accountTemplateXml(template: AccountTemplate) {
  const group = `account-${template.id}`;
  const category = CATEGORIES[template.category];
  const text = (id: string, value: string, x: number, y: number, w: number, h: number, extra = '') =>
    `<mxCell id="${group}-${id}" value="${escapeXml(value)}" style="text;html=0;whiteSpace=wrap;overflow=hidden;align=left;verticalAlign=top;fontFamily=Arial;fontSize=13;fontColor=#35454D;spacing=4;${extra}" vertex="1" parent="${group}"><mxGeometry x="${x}" y="${y}" width="${w}" height="${h}" as="geometry"/></mxCell>`;
  const line = (id: string, x1: number, y1: number, x2: number, y2: number) =>
    `<mxCell id="${group}-${id}" style="endArrow=none;startArrow=none;strokeColor=#61767E;strokeWidth=1.5;" edge="1" parent="${group}"><mxGeometry relative="1" as="geometry"><mxPoint x="${x1}" y="${y1}" as="sourcePoint"/><mxPoint x="${x2}" y="${y2}" as="targetPoint"/></mxGeometry></mxCell>`;
  return `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
    <mxCell id="${group}" value="" style="group;" vertex="1" connectable="0" parent="1"><mxGeometry width="240" height="105" as="geometry"/></mxCell>
    ${text('title', template.category === 'blank' ? '科目名称' : template.name, 0, 0, 240, 24, `fillColor=${category.color};fontSize=13;fontStyle=1;align=center;verticalAlign=middle;`)}
    ${text('debit-label', category.debit, 0, 26, 120, 25, 'fontStyle=1;align=center;')}
    ${text('credit-label', category.credit, 120, 26, 120, 25, 'fontStyle=1;align=center;')}
    ${text('debit', '业务事项／金额', 0, 51, 120, 54)}
    ${text('credit', '业务事项／金额', 120, 51, 120, 54)}
    ${line('horizontal', 0, 25, 240, 25)}
    ${line('vertical', 120, 25, 120, 105)}
    </root></mxGraphModel>`;
}

export function financeLibraryEntries() {
  return ACCOUNT_TEMPLATES.map(template => ({
    title: template.name,
    w: 240,
    h: 105,
    xml: accountTemplateXml(template),
  }));
}

export function financeLibraryConfig() {
  return {
    defaultLibraries: 'general;flowchart;youju-finance',
    libraries: [{
      title: { main: '有据财务' },
      entries: [{
        id: 'youju-finance',
        title: { main: '财务科目' },
        desc: { main: '可编辑丁字账；按科目性质显示借贷增减，具体科目应按适用会计准则调整。' },
        libs: [{ title: { main: '财务科目 · 丁字账' }, expand: true, data: financeLibraryEntries() }],
      }],
    }],
  };
}
