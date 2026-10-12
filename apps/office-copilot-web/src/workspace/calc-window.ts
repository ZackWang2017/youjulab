import jspreadsheet from 'jspreadsheet-ce';
import 'jspreadsheet-ce/dist/jspreadsheet.css';
import 'jsuites/dist/jsuites.css';
import { saved } from '../context/types';

const COLS = 6;
const ROWS = 12;
const STORAGE_KEY = 'office-calc';

type NumFormat = 'accounting' | 'number' | 'percent' | 'general';

const formatNames: NumFormat[] = ['accounting', 'number', 'percent', 'general'];
const formats = new Map<string, NumFormat>();
let windowEl: HTMLElement | null = null;
let sheet: jspreadsheet.WorksheetInstance | null = null;
let persistRemote: ((content: unknown) => void) | null = null;

function formatKey(x: number, y: number) {
  return `${x},${y}`;
}

function formatNumber(value: number, format: NumFormat) {
  if (!Number.isFinite(value)) return '#NUM';
  if (format === 'general') return String(Math.round(value * 1e10) / 1e10);
  if (format === 'percent') {
    return `${(value * 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  }
  const abs = Math.abs(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (format === 'number') return value < 0 ? `-${abs}` : abs;
  if (value < 0) return `(${abs})`;
  if (value === 0) return '-';
  return abs;
}

function paintCell(
  cell: HTMLTableCellElement | null,
  value: jspreadsheet.CellValue | undefined,
  x: number,
  y: number,
  instance: jspreadsheet.WorksheetInstance,
) {
  if (!cell) return;
  if (value == null || value === '') {
    cell.textContent = '';
    return;
  }
  const raw = String(value).trim();
  const format = formats.get(formatKey(x, y)) ?? 'accounting';
  if (raw.startsWith('=')) {
    try {
      const result = Number(instance.executeFormula(raw, x, y));
      cell.textContent = formatNumber(result, format);
    } catch {
      cell.textContent = '#ERR';
    }
    return;
  }
  const num = Number(raw.replace(/,/g, ''));
  cell.textContent = Number.isFinite(num) ? formatNumber(num, format) : raw;
}

function applyRef(formula: string, label: string, extend: boolean) {
  if (extend) {
    return formula.replace(/(\$?[A-Z]+\$?\d+)(?::\$?[A-Z]+\$?\d+)?\s*$/i, `$1:${label}`);
  }
  if (/[+\-*/(:,]\s*$/.test(formula) || /^=\s*$/.test(formula)) return formula + label;
  if (/\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?\s*$/i.test(formula)) {
    return formula.replace(/\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?\s*$/i, label);
  }
  return formula + label;
}

function editorInput(cell: HTMLTableCellElement) {
  const input = cell.querySelector('input, textarea');
  return input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement ? input : null;
}

function readGrid(raw: unknown) {
  if (!raw || typeof raw !== 'object') return { data: [] as string[][], formats: {} as Record<string, NumFormat> };
  const record = raw as { data?: unknown; formats?: unknown };
  const data = Array.isArray(record.data)
    ? record.data.map(row => (Array.isArray(row) ? row.map(cell => (cell == null ? '' : String(cell))) : []))
    : [];
  const storedFormats: Record<string, NumFormat> = {};
  if (record.formats && typeof record.formats === 'object') {
    for (const [key, value] of Object.entries(record.formats)) {
      if (formatNames.includes(value as NumFormat)) storedFormats[key] = value as NumFormat;
    }
  }
  return { data, formats: storedFormats };
}

function gridHasValues(data: string[][]) {
  return data.some(row => row.some(cell => cell !== ''));
}

function saveStore() {
  if (!sheet) return;
  const data = sheet.getData();
  const storedFormats: Record<string, NumFormat> = {};
  formats.forEach((value, key) => {
    if (value !== 'accounting') storedFormats[key] = value;
  });
  const body = { format: 'invp-calc-v1', data, formats: storedFormats };
  if (persistRemote) persistRemote(body);
  else localStorage.setItem(STORAGE_KEY, JSON.stringify({ data, formats: storedFormats }));
}

export function openCalcWindow(
  asset?: { content?: unknown } | null,
  onDirty?: (content: unknown) => void,
) {
  persistRemote = onDirty ?? null;
  if (windowEl) {
    windowEl.hidden = false;
    return;
  }
  const win = document.createElement('section');
  win.className = 'calc-window';
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-label', '表格计算器');
  win.innerHTML = `
    <header class="calc-title">
      <strong>计算器</strong>
      <button type="button" id="calc-close" aria-label="关闭">×</button>
    </header>
    <div class="calc-bar">
      <span class="calc-addr">A1</span>
      <select class="calc-format" aria-label="数字格式">
        <option value="accounting">会计数值</option>
        <option value="number">数值</option>
        <option value="percent">百分比</option>
        <option value="general">常规</option>
      </select>
      <button type="button" class="calc-clear" id="calc-clear">清空</button>
    </div>
    <div class="calc-sheet"></div>
    <p class="calc-hint">Jspreadsheet：输入 = 后点选单元格作为计算因子，Shift+点选形成区域</p>`;
  document.body.append(win);
  windowEl = win;

  const host = win.querySelector<HTMLDivElement>('.calc-sheet')!;
  const addr = win.querySelector<HTMLElement>('.calc-addr')!;
  const formatSelect = win.querySelector<HTMLSelectElement>('.calc-format')!;
  const remote = asset ? readGrid(asset.content) : null;
  const local = readGrid(saved(STORAGE_KEY));
  const useLocal = Boolean(remote && !gridHasValues(remote.data) && gridHasValues(local.data));
  const stored = useLocal || !remote ? local : remote;
  if (useLocal) localStorage.removeItem(STORAGE_KEY);
  formats.clear();
  for (const [key, value] of Object.entries(stored.formats)) formats.set(key, value);
  const columnCount = Math.max(COLS, ...stored.data.map(row => row.length), 0);
  const column = () => ({
    type: 'text' as const,
    width: 72,
    align: 'right' as const,
    render: paintCell,
  });
  let ready = false;
  const worksheets = jspreadsheet(host, {
    worksheets: [{
      data: stored.data,
      minDimensions: [columnCount, Math.max(ROWS, stored.data.length)],
      columns: Array.from({ length: columnCount }, column),
      columnSorting: false,
      tableOverflow: true,
      tableWidth: '100%',
      tableHeight: '320px',
    }],
    onselection: (instance, x, y) => {
      addr.textContent = jspreadsheet.helpers.getCellNameFromCoords(x, y);
      formatSelect.value = formats.get(formatKey(x, y)) ?? 'accounting';
      sheet = instance;
    },
    onchange: () => { if (ready) saveStore(); },
    onafterchanges: () => { if (ready) saveStore(); },
    oninsertrow: () => { if (ready) saveStore(); },
    ondeleterow: () => { if (ready) saveStore(); },
    oninsertcolumn: () => { if (ready) saveStore(); },
    ondeletecolumn: () => { if (ready) saveStore(); },
  });
  sheet = worksheets[0];
  ready = true;
  if (useLocal) saveStore();

  host.addEventListener('mousedown', event => {
    if (!sheet?.edition) return;
    const input = editorInput(sheet.edition[0]);
    if (!input) return;
    const draft = input.value.trim();
    if (!draft.startsWith('=')) return;
    const td = (event.target as HTMLElement).closest('td');
    if (!td || td === sheet.edition[0]) return;
    const x = td.getAttribute('data-x');
    const y = td.getAttribute('data-y');
    if (x == null || y == null) return;
    event.preventDefault();
    event.stopPropagation();
    const label = jspreadsheet.helpers.getCellNameFromCoords(Number(x), Number(y));
    const next = applyRef(input.value, label, event.shiftKey);
    input.value = next;
    input.focus();
    input.setSelectionRange(next.length, next.length);
  }, true);

  win.querySelector('#calc-clear')!.addEventListener('click', () => {
    if (!sheet) return;
    if (sheet.edition) sheet.closeEditor(sheet.edition[0], false);
    formats.clear();
    const current = sheet.getData();
    const width = Math.max(COLS, ...current.map(row => row.length), 0);
    const height = Math.max(ROWS, current.length);
    sheet.setData(Array.from({ length: height }, () => Array.from({ length: width }, () => '')));
    formatSelect.value = 'accounting';
    addr.textContent = 'A1';
    saveStore();
  });

  formatSelect.addEventListener('change', () => {
    if (!sheet?.selectedCell) return;
    const x = Number(sheet.selectedCell[0]);
    const y = Number(sheet.selectedCell[1]);
    formats.set(formatKey(x, y), formatSelect.value as NumFormat);
    const name = jspreadsheet.helpers.getCellNameFromCoords(x, y);
    sheet.setValue(name, sheet.getValue(name) ?? '');
    saveStore();
  });

  win.querySelector('#calc-close')!.addEventListener('click', () => {
    if (sheet?.edition) sheet.closeEditor(sheet.edition[0], true);
    saveStore();
    win.hidden = true;
  });
  const title = win.querySelector<HTMLElement>('.calc-title')!;
  let drag: { x: number; y: number; left: number; top: number } | null = null;
  title.addEventListener('pointerdown', event => {
    if ((event.target as HTMLElement).closest('button')) return;
    const rect = win.getBoundingClientRect();
    drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    title.setPointerCapture(event.pointerId);
  });
  title.addEventListener('pointermove', event => {
    if (!drag) return;
    win.style.left = `${Math.max(8, drag.left + event.clientX - drag.x)}px`;
    win.style.top = `${Math.max(8, drag.top + event.clientY - drag.y)}px`;
    win.style.right = 'auto';
  });
  title.addEventListener('pointerup', () => { drag = null; });

  const fit = () => {
    if (!sheet) return;
    const height = Math.max(160, host.clientHeight);
    sheet.options.tableHeight = `${height}px`;
    const content = host.querySelector<HTMLElement>('.jss_content');
    if (content) content.style.height = `${height}px`;
  };
  new ResizeObserver(fit).observe(host);
  fit();
  window.addEventListener('pagehide', () => {
    if (sheet?.edition) sheet.closeEditor(sheet.edition[0], true);
    saveStore();
  });
}
