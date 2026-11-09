import { describe, expect, it } from 'vitest';
import {
  PRINT_CELL_LIMIT,
  buildSheetPrintHtml,
  buildSheetPrintModel,
  renderSheetPrintHtml,
  resolvePrintRange,
  type PrintRangeBox,
  type PrintableRange,
  type PrintableSheet,
  type PrintableTextStyle,
} from './print-sheet';

function matrix<T>(rows: T[][]): T[][] {
  return rows.map(row => [...row]);
}

function makeRange(
  box: PrintRangeBox,
  label: string,
  values: string[][],
  extras: Partial<{
    backgrounds: string[][];
    horizontalAlignments: string[][];
    verticalAlignments: string[][];
    wraps: boolean[][];
    styles: Array<Array<PrintableTextStyle | null>>;
  }> = {},
): PrintableRange {
  return {
    getRange: () => ({ ...box }),
    getA1Notation: () => label,
    getDisplayValues: () => matrix(values),
    getBackgrounds: () => matrix(extras.backgrounds || values.map(row => row.map(() => ''))),
    getHorizontalAlignments: () => matrix(extras.horizontalAlignments || values.map(row => row.map(() => 'left'))),
    getVerticalAlignments: () => matrix(extras.verticalAlignments || values.map(row => row.map(() => 'bottom'))),
    getWraps: () => matrix(extras.wraps || values.map(row => row.map(() => false))),
    getCellStyles: () => matrix(extras.styles || values.map(row => row.map(() => null))),
  };
}

function makeSheet(opts: {
  data: PrintableRange;
  selection?: PrintableRange | null;
  merges?: PrintableRange[];
  columnWidths?: number[];
  rowHeights?: number[];
}): PrintableSheet {
  return {
    getSheetName: () => '持仓',
    getDataRange: () => opts.data,
    getSelection: () => ({ getActiveRange: () => opts.selection ?? null }),
    getMergeData: () => opts.merges || [],
    getColumnWidth: index => opts.columnWidths?.[index] ?? 96,
    getRowHeight: index => opts.rowHeights?.[index] ?? 22,
  };
}

describe('sheet print', () => {
  it('prefers multi-cell selection over data range', () => {
    const data = makeRange(
      { startRow: 0, startColumn: 0, endRow: 2, endColumn: 2 },
      'A1:C3',
      [['a', 'b', 'c'], ['d', 'e', 'f'], ['g', 'h', 'i']],
    );
    const selection = makeRange(
      { startRow: 0, startColumn: 0, endRow: 0, endColumn: 1 },
      'A1:B1',
      [['a', 'b']],
    );
    const sheet = makeSheet({ data, selection });
    expect(resolvePrintRange(sheet).getA1Notation()).toBe('A1:B1');
    expect(resolvePrintRange(sheet, false).getA1Notation()).toBe('A1:C3');
  });

  it('renders merges, styles and escapes html', () => {
    const data = makeRange(
      { startRow: 1, startColumn: 1, endRow: 2, endColumn: 2 },
      'B2:C3',
      [['标题 <x>', '右'], ['下', '尾']],
      {
        backgrounds: [['#ffeeee', ''], ['', '#eef6ff']],
        horizontalAlignments: [['center', 'right'], ['left', 'left']],
        verticalAlignments: [['middle', 'top'], ['bottom', 'bottom']],
        wraps: [[true, false], [false, false]],
        styles: [
          [{ bold: true, fontSize: 14, color: { rgb: '#c00' } }, null],
          [null, { italic: true }],
        ],
      },
    );
    const merge = makeRange(
      { startRow: 1, startColumn: 1, endRow: 1, endColumn: 2 },
      'B2:C2',
      [['标题 <x>', '']],
    );
    const sheet = makeSheet({
      data,
      merges: [merge],
      columnWidths: [0, 120, 80],
      rowHeights: [0, 28, 24],
    });
    const model = buildSheetPrintModel(sheet, { title: '测试表' });
    expect(model.rows).toBe(2);
    expect(model.cols).toBe(2);
    expect(model.merges).toHaveLength(1);

    const html = renderSheetPrintHtml(model);
    expect(html).toContain('<title>测试表</title>');
    expect(html).toContain('持仓 · B2:C3');
    expect(html).not.toContain('rowspan=');
    expect(html).toContain('colspan="2"');
    expect(html).toContain('标题 &lt;x&gt;');
    expect(html).toContain('font-weight:700');
    expect(html).toContain('text-align:center');
    expect(html).toContain('background:#ffeeee');
    expect(html).toContain('width:120px');
    // 合并后第二列锚点单元格被跳过，不应再输出「右」
    expect(html).not.toContain('>右<');
    expect(html).toContain('>下<');
    expect(html).toContain('font-style:italic');
  });

  it('rejects oversized print ranges', () => {
    const side = Math.ceil(Math.sqrt(PRINT_CELL_LIMIT)) + 1;
    const values = Array.from({ length: side }, () => Array.from({ length: side }, () => ''));
    const data = makeRange(
      { startRow: 0, startColumn: 0, endRow: side - 1, endColumn: side - 1 },
      `A1`,
      values,
    );
    expect(() => buildSheetPrintHtml(makeSheet({ data }))).toThrow(/打印区域过大/);
  });

  it('renders empty-sheet placeholder', () => {
    const data = makeRange(
      { startRow: 0, startColumn: 0, endRow: -1, endColumn: -1 },
      '',
      [],
    );
    const html = buildSheetPrintHtml(makeSheet({ data }));
    expect(html).toContain('没有可打印内容');
  });
});
