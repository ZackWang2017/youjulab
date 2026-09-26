/** 整表结构解析：合并区左上角填充 + 行列/A1 定位，供 AI surrounding 使用。 */

export const STRUCTURE_CELL_LIMIT = 8000;
export const STRUCTURE_JSON_MAX = 100000;

export type StructureCell = string | number | boolean | null;

export interface SheetStructure {
  sheet_id: string;
  sheet_name: string;
  range: string;
  origin: { row: number; col: number };
  rows: number;
  cols: number;
  merges: string[];
  values: StructureCell[][];
  note: string;
  truncated?: boolean;
}

type RangeLike = {
  getRange(): { startRow: number; startColumn: number; endRow: number; endColumn: number };
  getA1Notation(): string;
  getValues(): unknown[][];
  getValue?: () => unknown;
};

export type StructureSheet = {
  getSheetId(): string;
  getSheetName(): string;
  getDataRange(): RangeLike;
  getMergeData(): RangeLike[];
};

const NOTE = 'values 已按合并左上角填充；矩阵下标 (r,c) → 绝对行 origin.row+r、列 origin.col+c；A1 由绝对行列换算；查值读 values[r][c]';

export function toA1(row: number, col: number): string {
  let n = col + 1;
  let letters = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return `${letters}${row + 1}`;
}

function isBlank(v: StructureCell): boolean {
  return v == null || (typeof v === 'string' && v.trim() === '');
}

function normalizeCell(v: unknown): StructureCell {
  if (v == null) return null;
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
  if (typeof v === 'object' && v !== null && 'toPlainText' in v && typeof (v as { toPlainText: () => string }).toPlainText === 'function') {
    return (v as { toPlainText: () => string }).toPlainText();
  }
  return String(v);
}

function cloneMatrix(raw: unknown[][]): StructureCell[][] {
  return raw.map(row => (row || []).map(normalizeCell));
}

function trimEmptyEdges(values: StructureCell[][]): StructureCell[][] {
  if (!values.length) return values;
  let endRow = values.length - 1;
  while (endRow >= 0 && values[endRow].every(isBlank)) endRow -= 1;
  if (endRow < 0) return [];

  let endCol = -1;
  for (let r = 0; r <= endRow; r++) {
    for (let c = values[r].length - 1; c >= 0; c--) {
      if (!isBlank(values[r][c])) {
        endCol = Math.max(endCol, c);
        break;
      }
    }
  }
  if (endCol < 0) return [];

  return values.slice(0, endRow + 1).map(row => {
    const next = row.slice(0, endCol + 1);
    while (next.length <= endCol) next.push(null);
    return next;
  });
}

function intersects(
  a: { startRow: number; startColumn: number; endRow: number; endColumn: number },
  b: { startRow: number; startColumn: number; endRow: number; endColumn: number },
): boolean {
  return !(a.endRow < b.startRow || a.startRow > b.endRow || a.endColumn < b.startColumn || a.startColumn > b.endColumn);
}

function fillMerges(
  values: StructureCell[][],
  origin: { row: number; col: number },
  dataBox: { startRow: number; startColumn: number; endRow: number; endColumn: number },
  merges: RangeLike[],
): string[] {
  const mergeLabels: string[] = [];
  const rows = values.length;
  const cols = values[0]?.length || 0;

  for (const merge of merges) {
    const box = merge.getRange();
    if (!intersects(box, dataBox)) continue;
    mergeLabels.push(merge.getA1Notation());

    const topR = Math.max(box.startRow, dataBox.startRow);
    const topC = Math.max(box.startColumn, dataBox.startColumn);
    const bottomR = Math.min(box.endRow, dataBox.endRow);
    const bottomC = Math.min(box.endColumn, dataBox.endColumn);

    const anchorR = box.startRow - origin.row;
    const anchorC = box.startColumn - origin.col;
    let fill: StructureCell = null;
    if (anchorR >= 0 && anchorR < rows && anchorC >= 0 && anchorC < cols) {
      fill = values[anchorR][anchorC];
    } else if (typeof merge.getValue === 'function') {
      fill = normalizeCell(merge.getValue());
    }

    for (let absR = topR; absR <= bottomR; absR++) {
      const r = absR - origin.row;
      if (r < 0 || r >= rows) continue;
      for (let absC = topC; absC <= bottomC; absC++) {
        const c = absC - origin.col;
        if (c < 0 || c >= cols) continue;
        values[r][c] = fill;
      }
    }
  }
  return mergeLabels;
}

export function parseSheetStructure(sheet: StructureSheet): SheetStructure {
  const dataRange = sheet.getDataRange();
  const box = dataRange.getRange();
  const origin = { row: box.startRow, col: box.startColumn };
  const raw = dataRange.getValues() || [];
  let height = Math.max(0, box.endRow - box.startRow + 1);
  let width = Math.max(0, box.endColumn - box.startColumn + 1);
  let truncated = false;
  if (width > STRUCTURE_CELL_LIMIT) {
    width = STRUCTURE_CELL_LIMIT;
    height = Math.min(height, 1);
    truncated = true;
  } else if (width > 0 && height * width > STRUCTURE_CELL_LIMIT) {
    height = Math.floor(STRUCTURE_CELL_LIMIT / width);
    truncated = true;
  }
  const dataBox = {
    startRow: box.startRow,
    startColumn: box.startColumn,
    endRow: box.startRow + Math.max(height - 1, 0),
    endColumn: box.startColumn + Math.max(width - 1, 0),
  };

  if (!raw.length || !width || !height) {
    return {
      sheet_id: sheet.getSheetId(),
      sheet_name: sheet.getSheetName(),
      range: '',
      origin,
      rows: 0,
      cols: 0,
      merges: [],
      values: [],
      note: NOTE,
    };
  }

  const values = cloneMatrix(raw).slice(0, height).map(row => row.slice(0, width));
  // 对齐矩形
  for (const row of values) {
    while (row.length < width) row.push(null);
    if (row.length > width) row.length = width;
  }
  while (values.length < height) values.push(Array.from({ length: width }, () => null));
  if (values.length > height) values.length = height;

  const merges = fillMerges(values, origin, dataBox, sheet.getMergeData?.() || []);
  const trimmed = trimEmptyEdges(values);
  const rows = trimmed.length;
  const cols = trimmed[0]?.length || 0;
  const endRow = origin.row + Math.max(rows - 1, 0);
  const endCol = origin.col + Math.max(cols - 1, 0);
  const range = rows && cols
    ? (rows === 1 && cols === 1 ? toA1(origin.row, origin.col) : `${toA1(origin.row, origin.col)}:${toA1(endRow, endCol)}`)
    : '';

  return {
    sheet_id: sheet.getSheetId(),
    sheet_name: sheet.getSheetName(),
    range,
    origin,
    rows,
    cols,
    merges,
    values: trimmed,
    note: truncated ? `${NOTE}；已用区域超过 ${STRUCTURE_CELL_LIMIT} 个单元格，仅保留前 ${rows} 行` : NOTE,
    truncated: truncated || undefined,
  };
}

/** 序列化为 surrounding 字符串；超限则截断 values 并标记 truncated。 */
export function serializeSheetStructure(structure: SheetStructure, maxLen = STRUCTURE_JSON_MAX): string {
  const full = JSON.stringify(structure);
  if (full.length <= maxLen) return full;

  const copy: SheetStructure = {
    ...structure,
    values: [],
    truncated: true,
    note: `${NOTE}；已截断 values 以适配 surrounding 上限`,
  };
  // 逐行塞入直到接近上限
  for (const row of structure.values) {
    copy.values.push(row);
    copy.rows = copy.values.length;
    copy.cols = copy.values[0]?.length || 0;
    const next = JSON.stringify(copy);
    if (next.length > maxLen) {
      copy.values.pop();
      copy.rows = copy.values.length;
      copy.cols = copy.values[0]?.length || 0;
      break;
    }
  }
  return JSON.stringify(copy);
}
