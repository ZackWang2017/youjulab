import './select-data.css';
import { computeSelectionStats, extractNumbers, formatStatsBrief } from './selection-stats';

export type SelectCell = string | number | boolean | null;
export type SelectLayout = 'row' | 'column';

/** 带绝对坐标的单元格。 */
export type SelectPoint = {
  row: number;
  col: number;
  a1: string;
  value: SelectCell;
};

export type SelectRangeBlock = {
  address: string;
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
  values: SelectCell[][];
};

export type SelectDataSnapshot = {
  id: string;
  savedAt: number;
  sheetId: string;
  sheetName: string;
  layout: SelectLayout;
  points: SelectPoint[];
  addresses: string[];
  /** @deprecated 兼容旧历史 */
  ranges?: SelectRangeBlock[];
};

export type SelectDataHost = {
  getActiveSheet(): {
    getSheetId(): string;
    getSheetName(): string;
    getRange?: (a: number | string, b?: number, c?: number, d?: number) => {
      setValues: (v: unknown[][]) => unknown;
      getA1Notation?: () => string;
    };
    getSelection(): {
      getActiveRangeList?: () => Array<{
        getA1Notation(): string;
        getRange(): { startRow: number; startColumn: number; endRow: number; endColumn: number };
        getValues(): unknown[][];
      }>;
      getActiveRange(): {
        getA1Notation(): string;
        getRange(): { startRow: number; startColumn: number; endRow: number; endColumn: number };
        getValues(): unknown[][];
      } | null;
    } | null;
  } | null;
  onSelectionChange: (cb: () => void) => () => void;
  onDirty: () => void;
};

const STORAGE_KEY = 'office.sheet.select-data.history';
const HISTORY_LIMIT = 3;
const CELL_SOFT_LIMIT = 800;

function normalizeCell(v: unknown): SelectCell {
  if (v == null) return null;
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
  if (typeof v === 'object' && v !== null && 'toPlainText' in v
    && typeof (v as { toPlainText: () => string }).toPlainText === 'function') {
    return (v as { toPlainText: () => string }).toPlainText();
  }
  return String(v);
}

function formatCell(v: SelectCell): string {
  if (v == null) return '';
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return String(v);
    if (Math.abs(v) >= 1e7 || (Math.abs(v) > 0 && Math.abs(v) < 1e-4)) {
      return v.toExponential(4);
    }
    return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  }
  return String(v);
}

function toA1(row: number, col: number): string {
  let n = col + 1;
  let letters = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return `${letters}${row + 1}`;
}

function cloneValues(matrix: unknown[][]): SelectCell[][] {
  return (matrix || []).map(row => (row || []).map(normalizeCell));
}

function pointKey(row: number, col: number) {
  return `${row}:${col}`;
}

function isBlank(value: SelectCell): boolean {
  return value == null || (typeof value === 'string' && value.trim() === '');
}

/** 只保留有值的单元格，避免默认空选区（如 A1:A3）进入列表。 */
function pointsFromRanges(ranges: SelectRangeBlock[]): SelectPoint[] {
  const map = new Map<string, SelectPoint>();
  for (const block of ranges) {
    block.values.forEach((row, r) => {
      (row || []).forEach((raw, c) => {
        const value = normalizeCell(raw);
        if (isBlank(value)) return;
        const rowIdx = block.startRow + r;
        const colIdx = block.startColumn + c;
        map.set(pointKey(rowIdx, colIdx), {
          row: rowIdx,
          col: colIdx,
          a1: toA1(rowIdx, colIdx),
          value,
        });
      });
    });
  }
  return [...map.values()];
}

/** 固定按行、列排序成一列。 */
function sortPoints(points: SelectPoint[]): SelectPoint[] {
  return [...points].sort((a, b) => (a.row !== b.row ? a.row - b.row : a.col - b.col));
}

function mergePoints(existing: SelectPoint[], incoming: SelectPoint[]): SelectPoint[] {
  const map = new Map<string, SelectPoint>();
  for (const p of existing) map.set(pointKey(p.row, p.col), { ...p });
  for (const p of incoming) map.set(pointKey(p.row, p.col), { ...p });
  return [...map.values()];
}

function readCurrentRanges(host: SelectDataHost): SelectRangeBlock[] {
  const sheet = host.getActiveSheet();
  if (!sheet) return [];
  const selection = sheet.getSelection();
  if (!selection) return [];
  const list = typeof selection.getActiveRangeList === 'function'
    ? selection.getActiveRangeList()
    : [];
  const ranges = list.length
    ? list
    : (selection.getActiveRange() ? [selection.getActiveRange()!] : []);
  return ranges.map(range => {
    const box = range.getRange();
    return {
      address: range.getA1Notation(),
      startRow: box.startRow,
      startColumn: box.startColumn,
      endRow: box.endRow,
      endColumn: box.endColumn,
      values: cloneValues(range.getValues() as unknown[][]),
    };
  }).filter(r => r.values.length > 0);
}

function formatTime(ts: number) {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function normalizeSnapshot(raw: SelectDataSnapshot): SelectDataSnapshot | null {
  if (!raw || typeof raw !== 'object') return null;
  let points = Array.isArray(raw.points) ? raw.points.map(p => ({
    row: Number(p.row),
    col: Number(p.col),
    a1: String(p.a1 || toA1(Number(p.row), Number(p.col))),
    value: normalizeCell(p.value),
  })) : [];
  if (!points.length && Array.isArray(raw.ranges)) {
    points = pointsFromRanges(raw.ranges as SelectRangeBlock[]);
  }
  if (!points.length) return null;
  points = points.filter(p => !isBlank(p.value));
  if (!points.length) return null;
  return {
    id: String(raw.id || `sel_${Date.now().toString(36)}`),
    savedAt: Number(raw.savedAt) || Date.now(),
    sheetId: String(raw.sheetId || ''),
    sheetName: String(raw.sheetName || ''),
    layout: 'column',
    points: sortPoints(points),
    addresses: Array.isArray(raw.addresses)
      ? raw.addresses.map(String)
      : (raw.ranges || []).map(r => r.address),
  };
}

export function loadSelectHistory(): SelectDataSnapshot[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeSnapshot).filter(Boolean).slice(0, HISTORY_LIMIT) as SelectDataSnapshot[];
  } catch {
    return [];
  }
}

function saveSelectHistory(items: SelectDataSnapshot[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, HISTORY_LIMIT)));
  } catch {
    window.dispatchEvent(new CustomEvent('office-error', { detail: '本地存储已满，无法保存选数据历史。' }));
  }
}

function pushHistory(snap: SelectDataSnapshot) {
  const prev = loadSelectHistory().filter(item => item.id !== snap.id);
  saveSelectHistory([snap, ...prev].slice(0, HISTORY_LIMIT));
}

let activePanel: HTMLElement | null = null;
let lockedSurroundingJson = '';
let lockedSurroundingSheetId = '';

function snapshotToSurrounding(snap: SelectDataSnapshot): string {
  const nums = extractNumbers([snap.points.map(p => p.value)] as unknown[][]);
  const stats = computeSelectionStats(nums);
  return JSON.stringify({
    scope: 'selection',
    source: 'select-data',
    sheet_id: snap.sheetId,
    sheet_name: snap.sheetName,
    addresses: snap.addresses,
    points: snap.points,
    stats,
    note: '选数据面板锁定结果；坐标为 A1，值为 points[].value',
  });
}

/** 当前 sheet 若已「选完了」，返回 surrounding JSON，供协作助手使用。 */
export function getLockedSelectSurrounding(sheetId: string): string {
  if (lockedSurroundingSheetId === sheetId && lockedSurroundingJson) return lockedSurroundingJson;
  return '';
}

export function closeSelectDataPanel() {
  activePanel?.remove();
  activePanel = null;
  lockedSurroundingJson = '';
  lockedSurroundingSheetId = '';
}

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n));
}

/** 标题栏拖动；非模态，不遮挡表格操作。 */
function mountPanelDrag(win: HTMLElement, handle: HTMLElement) {
  let sx = 0;
  let sy = 0;
  let ox = 0;
  let oy = 0;
  let dragging = false;

  const onMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - sx;
    const dy = e.clientY - sy;
    const rect = win.getBoundingClientRect();
    const nextLeft = clamp(ox + dx, 8, window.innerWidth - Math.min(rect.width, 120));
    const nextTop = clamp(oy + dy, 8, window.innerHeight - 48);
    win.style.left = `${nextLeft}px`;
    win.style.top = `${nextTop}px`;
    win.style.right = 'auto';
  };

  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    win.classList.remove('is-dragging');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };

  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button')) return;
    dragging = true;
    win.classList.add('is-dragging');
    sx = e.clientX;
    sy = e.clientY;
    const rect = win.getBoundingClientRect();
    ox = rect.left;
    oy = rect.top;
    win.style.left = `${ox}px`;
    win.style.top = `${oy}px`;
    win.style.right = 'auto';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    e.preventDefault();
  });
}

export function openSelectDataPanel(host: SelectDataHost) {
  closeSelectDataPanel();

  let picking = true;
  // 不预填当前默认选区，避免打开时带入空的 A1:A3
  let points: SelectPoint[] = [];
  let addresses: string[] = [];
  let unsub: (() => void) | null = null;
  let syncTimer: ReturnType<typeof setTimeout> | undefined;

  const panel = document.createElement('aside');
  panel.className = 'sheet-select-data-panel';
  panel.setAttribute('role', 'complementary');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-label', '选数据');

  panel.innerHTML = `
    <header class="ssd-head" data-drag-handle title="拖动窗口">
      <div class="ssd-title-wrap">
        <h2>选数据</h2>
        <span class="ssd-mode" data-mode></span>
      </div>
      <button type="button" class="ssd-icon-btn" data-close title="关闭" aria-label="关闭">×</button>
    </header>
    <p class="ssd-hint" data-hint></p>
    <div class="ssd-addresses" data-addresses></div>
    <div class="ssd-stats" data-stats></div>
    <div class="ssd-body" data-body></div>
    <div class="ssd-history" data-history></div>
    <footer class="ssd-actions">
      <button type="button" data-clear hidden>清空本次</button>
      <button type="button" data-resume hidden>继续选</button>
      <button type="button" class="primary" data-done>选完了</button>
    </footer>
  `;

  document.body.append(panel);
  activePanel = panel;

  const dragHandle = panel.querySelector<HTMLElement>('[data-drag-handle]')!;
  mountPanelDrag(panel, dragHandle);

  const modeEl = panel.querySelector<HTMLElement>('[data-mode]')!;
  const hintEl = panel.querySelector<HTMLElement>('[data-hint]')!;
  const addrEl = panel.querySelector<HTMLElement>('[data-addresses]')!;
  const statsEl = panel.querySelector<HTMLElement>('[data-stats]')!;
  const bodyEl = panel.querySelector<HTMLElement>('[data-body]')!;
  const historyEl = panel.querySelector<HTMLElement>('[data-history]')!;
  const doneBtn = panel.querySelector<HTMLButtonElement>('[data-done]')!;
  const resumeBtn = panel.querySelector<HTMLButtonElement>('[data-resume]')!;
  const clearBtn = panel.querySelector<HTMLButtonElement>('[data-clear]')!;

  function orderedPoints(): SelectPoint[] {
    return sortPoints(points);
  }

  function matrixForStats(): SelectCell[][] {
    return orderedPoints().map(p => [p.value]);
  }

  function writePointToSheet(point: SelectPoint, value: SelectCell) {
    point.value = value;
    const sheet = host.getActiveSheet();
    if (!sheet?.getRange) return;
    try {
      sheet.getRange(point.a1).setValues([[value == null ? null : value]]);
      host.onDirty();
    } catch {
      try {
        sheet.getRange!(point.row, point.col, 1, 1).setValues([[value == null ? null : value]]);
        host.onDirty();
      } catch {
        /* ignore */
      }
    }
  }

  function parseInputValue(rawText: string): SelectCell {
    const raw = rawText.trim();
    if (raw === '') return null;
    if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$/.test(raw.replace(/,/g, ''))) {
      const pct = /%$/.test(raw);
      const n = Number(raw.replace(/,/g, '').replace(/%$/, ''));
      if (Number.isFinite(n)) return pct ? n / 100 : n;
    }
    return raw;
  }

  function renderHistory() {
    historyEl.replaceChildren();
    const items = loadSelectHistory();
    if (!items.length) return;
    const label = document.createElement('div');
    label.className = 'ssd-history-label';
    label.textContent = '近三次选择';
    historyEl.append(label);
    const row = document.createElement('div');
    row.className = 'ssd-history-row';
    for (const item of items) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ssd-history-chip';
      btn.textContent = `${formatTime(item.savedAt)} · ${item.points.length}格`;
      btn.title = `${item.sheetName || ''} ${(item.addresses || []).join(', ')}`;
      btn.addEventListener('click', () => {
        picking = false;
        points = item.points.filter(p => !isBlank(p.value)).map(p => ({ ...p }));
        addresses = [...(item.addresses || [])];
        stopListen();
        render();
      });
      row.append(btn);
    }
    historyEl.append(row);
  }

  function renderUnifiedTable() {
    const list = orderedPoints();
    const wrap = document.createElement('section');
    wrap.className = 'ssd-range';

    const head = document.createElement('div');
    head.className = 'ssd-range-head';
    head.innerHTML = `<div class="ssd-range-meta"><strong>一列</strong><span>${list.length} 个单元格</span></div>`;
    wrap.append(head);

    const scroll = document.createElement('div');
    scroll.className = 'ssd-table-scroll';
    const table = document.createElement('table');
    table.className = 'ssd-table ssd-table-column';

    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    hr.innerHTML = '<th>坐标</th><th>值</th>';
    thead.append(hr);
    table.append(thead);

    const tbody = document.createElement('tbody');
    list.forEach((p) => {
      const tr = document.createElement('tr');
      const coord = document.createElement('td');
      coord.className = 'ssd-coord';
      coord.textContent = p.a1;
      const td = document.createElement('td');
      const numeric = typeof p.value === 'number';
      if (numeric) td.classList.add('is-num');
      if (picking) {
        const input = document.createElement('input');
        input.type = 'text';
        input.value = formatCell(p.value);
        input.className = numeric ? 'is-num' : '';
        input.addEventListener('change', () => {
          const next = parseInputValue(input.value);
          writePointToSheet(p, next);
          const i = points.findIndex(x => x.row === p.row && x.col === p.col);
          if (i >= 0) {
            if (isBlank(next)) points.splice(i, 1);
            else points[i] = { ...points[i], value: next };
          }
          if (isBlank(next)) render();
          else renderStatsOnly();
        });
        td.append(input);
      } else {
        td.textContent = formatCell(p.value);
      }
      tr.append(coord, td);
      tbody.append(tr);
    });
    table.append(tbody);

    scroll.append(table);
    wrap.append(scroll);
    return wrap;
  }

  function renderStatsOnly() {
    const nums = extractNumbers(matrixForStats() as unknown[][]);
    const stats = computeSelectionStats(nums);
    statsEl.textContent = points.length
      ? `${points.length} 格 · ${formatStatsBrief(stats)}`
      : '在表格中框选有内容的单元格；空单元格不会列入';
  }

  function render() {
    modeEl.textContent = picking ? '选择中' : '已锁定';
    modeEl.classList.toggle('is-locked', !picking);
    hintEl.textContent = picking
      ? '有值的单元格按坐标排成一列（坐标 / 值），可下拉查看。空单元格不收录。点「选完了」锁定。'
      : '已锁定，不可编辑。可点「继续选」继续累积。';

    addrEl.replaceChildren();
    if (!addresses.length && !points.length) {
      const empty = document.createElement('span');
      empty.className = 'ssd-addr-empty';
      empty.textContent = '尚未选择';
      addrEl.append(empty);
    } else {
      const chips = addresses.length ? addresses : [`${points.length}格`];
      for (const text of chips) {
        const chip = document.createElement('span');
        chip.className = 'ssd-addr-chip';
        chip.textContent = text;
        addrEl.append(chip);
      }
    }

    renderStatsOnly();
    bodyEl.replaceChildren();
    if (!points.length) {
      const empty = document.createElement('p');
      empty.className = 'ssd-empty';
      empty.textContent = '请在表格中选择数据';
      bodyEl.append(empty);
    } else if (points.length > CELL_SOFT_LIMIT) {
      const warn = document.createElement('p');
      warn.className = 'ssd-empty';
      warn.textContent = `已选 ${points.length} 格（超过 ${CELL_SOFT_LIMIT}），仅显示统计。请清空后缩小选区。`;
      bodyEl.append(warn);
    } else {
      bodyEl.append(renderUnifiedTable());
    }

    doneBtn.hidden = !picking;
    resumeBtn.hidden = picking;
    clearBtn.hidden = !picking || !points.length;
    renderHistory();
  }

  function absorbCurrentSelection() {
    if (!picking) return;
    const blocks = readCurrentRanges(host);
    if (!blocks.length) return;
    const incoming = pointsFromRanges(blocks);
    if (!incoming.length) return;
    points = mergePoints(points, incoming);
    for (const b of blocks) {
      if (!addresses.includes(b.address)) addresses.push(b.address);
    }
    render();
  }

  function syncFromSheet() {
    if (!picking) return;
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => absorbCurrentSelection(), 280);
  }

  function stopListen() {
    clearTimeout(syncTimer);
    unsub?.();
    unsub = null;
  }

  function startListen() {
    stopListen();
    unsub = host.onSelectionChange(syncFromSheet);
  }

  panel.querySelector('[data-close]')!.addEventListener('click', () => {
    stopListen();
    closeSelectDataPanel();
  });

  clearBtn.addEventListener('click', () => {
    if (!picking) return;
    points = [];
    addresses = [];
    render();
  });

  doneBtn.addEventListener('click', () => {
    absorbCurrentSelection();
    points = points.filter(p => !isBlank(p.value));
    if (!points.length) {
      hintEl.textContent = '请先选择至少一个有内容的单元格。';
      return;
    }
    picking = false;
    stopListen();
    const ordered = sortPoints(points);
    points = ordered;
    const sheet = host.getActiveSheet();
    const snap: SelectDataSnapshot = {
      id: `sel_${Date.now().toString(36)}`,
      savedAt: Date.now(),
      sheetId: sheet?.getSheetId() || '',
      sheetName: sheet?.getSheetName() || '',
      layout: 'column',
      points: ordered.map(p => ({ ...p })),
      addresses: [...addresses],
    };
    pushHistory(snap);
    lockedSurroundingJson = snapshotToSurrounding(snap);
    lockedSurroundingSheetId = snap.sheetId;
    render();
  });

  resumeBtn.addEventListener('click', () => {
    picking = true;
    startListen();
    absorbCurrentSelection();
  });

  startListen();
  render();
}
