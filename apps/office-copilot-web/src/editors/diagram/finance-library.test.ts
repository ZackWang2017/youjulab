import { describe, expect, it } from 'vitest';
import { ACCOUNT_TEMPLATES, accountTemplateXml, financeLibraryConfig, financeLibraryEntries } from './finance-library';

describe('financial account shape library', () => {
  it('uses compact geometry and a bold 13px title', () => {
    for (const entry of financeLibraryEntries()) {
      expect(entry.w).toBe(240);
      expect(entry.h).toBe(105);
      expect(entry.xml).toContain('width="240" height="105"');
      expect(entry.xml).toContain('fontSize=13;fontStyle=1;align=center;verticalAlign=middle;');
      expect(entry.xml).not.toContain('fontSize=16');
      expect(entry.xml).toContain('x="120" y="105"');
    }
  });
  it('provides distinct editable grouped templates without stencils or images', () => {
    expect(new Set(ACCOUNT_TEMPLATES.map(t => t.id)).size).toBe(15);
    for (const template of ACCOUNT_TEMPLATES) {
      const xml = accountTemplateXml(template);
      expect(xml).toContain('style="group;"');
      expect(xml).toContain(`id="account-${template.id}-debit"`);
      expect(xml).toContain(`id="account-${template.id}-credit"`);
      expect(xml).toContain('as="sourcePoint"');
      expect(xml).not.toMatch(/stencil|image=|<script|https?:/i);
    }
  });

  it('uses different debit/credit directions for assets, liabilities and contra assets', () => {
    const xml = (id: string) => accountTemplateXml(ACCOUNT_TEMPLATES.find(t => t.id === id)!);
    expect(xml('cash')).toContain('借方 · 增加');
    expect(xml('payables')).toContain('借方 · 减少');
    expect(xml('depreciation')).toContain('贷方 · 增加');
    expect(xml('revenue')).toContain('借方 · 减少／结转');
    expect(xml('expenses')).toContain('借方 · 增加');
    expect(xml('blank')).not.toMatch(/· 增加|· 减少/);
  });

  it('escapes editable account labels', () => {
    expect(accountTemplateXml({ id: 'test', name: 'A&B"<科目>', category: 'asset' }))
      .toContain('A&amp;B&quot;&lt;科目&gt;');
  });

  it('loads the inline library by default while keeping general and flowchart libraries', () => {
    const config = financeLibraryConfig();
    expect(config.defaultLibraries).toBe('general;flowchart;youju-finance');
    expect(config.libraries[0].entries[0].libs[0].data).toEqual(financeLibraryEntries());
    expect(config.libraries[0].entries[0].libs[0].expand).toBe(true);
  });
});
