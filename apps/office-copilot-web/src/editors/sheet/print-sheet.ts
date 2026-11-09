/** 将当前工作表（或选区）渲染为可打印 HTML，绕开 Univer Pro 打印插件。 */
import { openSheetPrintPreview } from './print-preview';

export const PRINT_CELL_LIMIT = 20000;

export type PrintRangeBox = {
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
};

export type PrintableRange = {
  getRange(): PrintRangeBox;
  getA1Notation(): string;
  getDisplayValues(): string[][];
  getBackgrounds(): string[][];
  getHorizontalAlignments(): string[][];
  getVerticalAlignments(): string[][];
  getWraps(): boolean[][];
  getCellStyles(): Array<Array<PrintableTextStyle | null>>;
};

export type PrintableTextStyle = {
  bold?: boolean;
  italic?: boolean;
  fontSize?: number;
  fontFamily?: string | null | void;
  color?: { rgb?: string | null | void } | null | void;
};

export type PrintableSheet = {
  getSheetName(): string;
  getDataRange(): PrintableRange;
  getSelection?: () => { getActiveRange(): PrintableRange | null } | null;
  getMergeData(): PrintableRange[];
  getRowHeight(rowPosition: number): number;
  getColumnWidth(columnPosition: number): number;
};

export type SheetPrintModel = {
  title: string;
  sheetName: string;
  rangeLabel: string;
  startRow: number;
  startColumn: number;
  rows: number;
  cols: number;
  values: string[][];
  backgrounds: string[][];
  horizontalAlignments: string[][];
  verticalAlignments: string[][];
  wraps: boolean[][];
  styles: Array<Array<PrintableTextStyle | null>>;
  merges: PrintRangeBox[];
  columnWidths: number[];
  rowHeights: number[];
};

type BuildOptions = {
  title?: string;
  /** true：多格选区优先；否则始终用已用区域。默认 true。 */
  preferSelection?: boolean;
};

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function cellSize(box: PrintRangeBox): number {
  return Math.max(0, box.endRow - box.startRow + 1) * Math.max(0, box.endColumn - box.startColumn + 1);
}

function intersects(a: PrintRangeBox, b: PrintRangeBox): boolean {
  return !(a.endRow < b.startRow || a.startRow > b.endRow || a.endColumn < b.startColumn || a.startColumn > b.endColumn);
}

function padMatrix<T>(matrix: T[][] | undefined, rows: number, cols: number, fill: T): T[][] {
  const out: T[][] = [];
  for (let r = 0; r < rows; r++) {
    const src = matrix?.[r] || [];
    const row: T[] = [];
    for (let c = 0; c < cols; c++) {
      row.push(c < src.length ? src[c] : fill);
    }
    out.push(row);
  }
  return out;
}

function mapHorizontalAlign(value: string | undefined): string {
  switch (String(value || '').toLowerCase()) {
    case 'center':
      return 'center';
    case 'right':
      return 'right';
    case 'justify':
    case 'justified':
    case 'both':
    case 'distributed':
      return 'justify';
    default:
      return 'left';
  }
}

function mapVerticalAlign(value: string | undefined): string {
  switch (String(value || '').toLowerCase()) {
    case 'top':
      return 'top';
    case 'middle':
    case 'center':
      return 'middle';
    default:
      return 'bottom';
  }
}

function cssColor(value: string | null | undefined): string {
  const raw = String(value || '').trim();
  if (!raw || raw === 'transparent' || raw === 'null' || raw === 'undefined') return '';
  return raw;
}

/** 多格选区优先，否则打印已用区域。 */
export function resolvePrintRange(sheet: PrintableSheet, preferSelection = true): PrintableRange {
  if (preferSelection && typeof sheet.getSelection === 'function') {
    const active = sheet.getSelection()?.getActiveRange() || null;
    if (active && cellSize(active.getRange()) > 1) return active;
  }
  return sheet.getDataRange();
}

export function buildSheetPrintModel(sheet: PrintableSheet, options: BuildOptions = {}): SheetPrintModel {
  const range = resolvePrintRange(sheet, options.preferSelection !== false);
  const box = range.getRange();
  const rows = Math.max(0, box.endRow - box.startRow + 1);
  const cols = Math.max(0, box.endColumn - box.startColumn + 1);
  if (rows * cols > PRINT_CELL_LIMIT) {
    throw new Error(`打印区域过大（${rows * cols} 格），请缩小选区后再试。`);
  }

  const values = padMatrix(range.getDisplayValues(), rows, cols, '');
  const backgrounds = padMatrix(range.getBackgrounds(), rows, cols, '');
  const horizontalAlignments = padMatrix(range.getHorizontalAlignments(), rows, cols, 'left');
  const verticalAlignments = padMatrix(range.getVerticalAlignments(), rows, cols, 'bottom');
  const wraps = padMatrix(range.getWraps(), rows, cols, false);
  const styles = padMatrix(range.getCellStyles(), rows, cols, null);

  const merges = (sheet.getMergeData() || [])
    .map(merge => merge.getRange())
    .filter(merge => intersects(merge, box));

  const columnWidths: number[] = [];
  for (let c = 0; c < cols; c++) {
    const width = Number(sheet.getColumnWidth(box.startColumn + c));
    columnWidths.push(Number.isFinite(width) && width > 0 ? width : 88);
  }
  const rowHeights: number[] = [];
  for (let r = 0; r < rows; r++) {
    const height = Number(sheet.getRowHeight(box.startRow + r));
    rowHeights.push(Number.isFinite(height) && height > 0 ? height : 24);
  }

  const sheetName = sheet.getSheetName() || '工作表';
  return {
    title: options.title || sheetName,
    sheetName,
    rangeLabel: range.getA1Notation() || '',
    startRow: box.startRow,
    startColumn: box.startColumn,
    rows,
    cols,
    values,
    backgrounds,
    horizontalAlignments,
    verticalAlignments,
    wraps,
    styles,
    merges,
    columnWidths,
    rowHeights,
  };
}

function mergeLookup(model: SheetPrintModel): {
  skip: Set<string>;
  span: Map<string, { rowspan: number; colspan: number }>;
} {
  const skip = new Set<string>();
  const span = new Map<string, { rowspan: number; colspan: number }>();
  const { startRow, startColumn, rows, cols } = model;

  for (const merge of model.merges) {
    const top = Math.max(merge.startRow, startRow);
    const left = Math.max(merge.startColumn, startColumn);
    const bottom = Math.min(merge.endRow, startRow + rows - 1);
    const right = Math.min(merge.endColumn, startColumn + cols - 1);
    if (bottom < top || right < left) continue;

    const anchorKey = `${top - startRow},${left - startColumn}`;
    span.set(anchorKey, {
      rowspan: bottom - top + 1,
      colspan: right - left + 1,
    });
    for (let absR = top; absR <= bottom; absR++) {
      for (let absC = left; absC <= right; absC++) {
        const key = `${absR - startRow},${absC - startColumn}`;
        if (key !== anchorKey) skip.add(key);
      }
    }
  }
  return { skip, span };
}

export function renderSheetPrintHtml(model: SheetPrintModel): string {
  if (!model.rows || !model.cols) {
    return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escapeHtml(model.title)}</title></head>`
      + `<body><p>当前工作表没有可打印内容。</p></body></html>`;
  }

  const { skip, span } = mergeLookup(model);
  const colgroup = model.columnWidths
    .map(width => `<col style="width:${Math.round(width)}px">`)
    .join('');

  const bodyRows: string[] = [];
  for (let r = 0; r < model.rows; r++) {
    const cells: string[] = [];
    for (let c = 0; c < model.cols; c++) {
      const key = `${r},${c}`;
      if (skip.has(key)) continue;

      const style = model.styles[r]?.[c];
      const bg = cssColor(model.backgrounds[r]?.[c]);
      const color = cssColor(style?.color?.rgb || undefined);
      const css: string[] = [
        `text-align:${mapHorizontalAlign(model.horizontalAlignments[r]?.[c])}`,
        `vertical-align:${mapVerticalAlign(model.verticalAlignments[r]?.[c])}`,
        `white-space:${model.wraps[r]?.[c] ? 'pre-wrap' : 'pre'}`,
        'padding:2px 6px',
        'border:1px solid #bbb',
        'box-sizing:border-box',
      ];
      if (bg) css.push(`background:${bg}`);
      if (color) css.push(`color:${color}`);
      if (style?.bold) css.push('font-weight:700');
      if (style?.italic) css.push('font-style:italic');
      if (style?.fontSize) css.push(`font-size:${style.fontSize}pt`);
      if (style?.fontFamily) css.push(`font-family:${JSON.stringify(style.fontFamily)}`);

      const mergeSpan = span.get(key);
      const attrs = [
        `style="${escapeHtml(css.join(';'))}"`,
        mergeSpan && mergeSpan.rowspan > 1 ? `rowspan="${mergeSpan.rowspan}"` : '',
        mergeSpan && mergeSpan.colspan > 1 ? `colspan="${mergeSpan.colspan}"` : '',
      ].filter(Boolean).join(' ');

      const text = escapeHtml(String(model.values[r]?.[c] ?? '')).replace(/\n/g, '<br>');
      cells.push(`<td ${attrs}>${text}</td>`);
    }
    bodyRows.push(`<tr style="height:${Math.round(model.rowHeights[r] || 24)}px">${cells.join('')}</tr>`);
  }

  const subtitle = [model.sheetName, model.rangeLabel].filter(Boolean).join(' · ');
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>${escapeHtml(model.title)}</title>
<style>
  @page { margin: 12mm; }
  html, body { margin: 0; padding: 0; }
  body { font: 11pt/1.35 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; color: #111; }
  h1 { font-size: 14pt; font-weight: 600; margin: 0 0 4px; }
  .meta { color: #666; font-size: 9pt; margin: 0 0 12px; }
  table { border-collapse: collapse; table-layout: fixed; max-width: 100%; }
  @media print {
    .no-print { display: none !important; }
  }
</style>
</head>
<body>
  <h1>${escapeHtml(model.title)}</h1>
  <p class="meta">${escapeHtml(subtitle)}</p>
  <table>
    <colgroup>${colgroup}</colgroup>
    <tbody>${bodyRows.join('')}</tbody>
  </table>
</body>
</html>`;
}

export function buildSheetPrintHtml(sheet: PrintableSheet, options: BuildOptions = {}): string {
  return renderSheetPrintHtml(buildSheetPrintModel(sheet, options));
}

/** Preview stays inside the application; no blank popup or tab is created. */
export function printHtmlDocument(html: string): void {
  openSheetPrintPreview(html);
}

export function printActiveSheet(sheet: PrintableSheet, options: BuildOptions = {}): void {
  printHtmlDocument(buildSheetPrintHtml(sheet, options));
}
