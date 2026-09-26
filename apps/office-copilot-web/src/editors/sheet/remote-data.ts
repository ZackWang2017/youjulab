import { office } from '../../office-client/client';
import { persist, saved } from '../../context/types';

const STORE_KEY = 'office-remote-data';
const CELL_LIMIT = 10000;

type Cell = string | number | boolean | null;
type Matrix = Cell[][];

export interface RemoteApiConfig {
  name: string;
  url: string;
  method: 'GET' | 'POST' | 'PUT';
  function: string;
  headers: string;
}

interface Store {
  last?: RemoteApiConfig;
  saved: RemoteApiConfig[];
}

export interface RemoteDataHost {
  selectionValues(): Matrix;
  selectionAddress(): string;
  writeFromCursor(values: Matrix): { address: string; rows: number; cols: number };
}

function loadStore(): Store {
  const data = saved(STORE_KEY);
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { saved: [] };
  const savedItems = Array.isArray((data as Store).saved) ? (data as Store).saved : [];
  return { last: (data as Store).last, saved: savedItems.filter(item => item && item.url) };
}

function saveStore(store: Store) {
  persist(STORE_KEY, { last: store.last, saved: store.saved.slice(0, 12) });
}

function configFromForm(form: HTMLFormElement): RemoteApiConfig {
  const read = (name: string) => (form.elements.namedItem(name) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value.trim();
  const method = read('method').toUpperCase();
  return {
    name: read('name'),
    url: read('url'),
    method: method === 'GET' || method === 'PUT' ? method : 'POST',
    function: read('function'),
    headers: read('headers'),
  };
}

function fillForm(form: HTMLFormElement, config?: RemoteApiConfig) {
  const set = (name: string, value: string) => {
    const el = form.elements.namedItem(name) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null;
    if (el) el.value = value;
  };
  set('name', config?.name || '');
  set('url', config?.url || '');
  set('method', config?.method || 'POST');
  set('function', config?.function || '');
  set('headers', config?.headers || '');
}

function remember(config: RemoteApiConfig) {
  const store = loadStore();
  store.last = config;
  if (config.name) {
    store.saved = [config, ...store.saved.filter(item => item.name !== config.name)].slice(0, 12);
  }
  saveStore(store);
}

function preview(values: Matrix) {
  if (!values.length) return '空选区';
  const rows = values.length;
  const cols = Math.max(...values.map(row => row.length), 0);
  const head = values.slice(0, 4).map(row => row.map(cell => cell == null ? '' : String(cell)).join('\t')).join('\n');
  return `${rows} × ${cols}\n${head}${rows > 4 ? '\n…' : ''}`;
}

export function openRemoteDataDialog(host: RemoteDataHost) {
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay remote-data-overlay';
  overlay.innerHTML = `<form class="remote-data-dialog" role="dialog" aria-labelledby="remote-data-title">
    <h2 id="remote-data-title">动态数据</h2>
    <p class="remote-data-hint">用当前选区作为参数调用远程接口，把返回的二维数组写入当前光标单元格起的区域。</p>
    <div class="remote-data-meta">
      <div><span>当前选区</span><strong data-selection></strong></div>
      <pre data-preview></pre>
    </div>
    <label class="field-label" for="remote-saved">已保存接口</label>
    <select id="remote-saved" name="saved"></select>
    <label class="field-label" for="remote-name">名称</label>
    <input id="remote-name" name="name" maxlength="80" placeholder="可选，保存后方便下次选用">
    <label class="field-label" for="remote-url">接口地址</label>
    <input id="remote-url" name="url" required maxlength="2000" placeholder="https://example.com/api">
    <div class="remote-data-row">
      <div>
        <label class="field-label" for="remote-method">方法</label>
        <select id="remote-method" name="method">
          <option value="POST">POST</option>
          <option value="GET">GET</option>
          <option value="PUT">PUT</option>
        </select>
      </div>
      <div>
        <label class="field-label" for="remote-function">函数名</label>
        <input id="remote-function" name="function" maxlength="120" placeholder="可选">
      </div>
    </div>
    <label class="field-label" for="remote-headers">请求头 JSON</label>
    <textarea id="remote-headers" name="headers" rows="2" placeholder='{"Authorization":"Bearer …"}'></textarea>
    <p class="remote-data-hint">POST/PUT 请求体为 {"values":选定二维数组,"function":函数名}。远程需返回二维数组，或 {"values":[[]]}。</p>
    <p class="remote-data-note" hidden></p>
    <div class="dialog-actions">
      <button type="button" data-cancel>取消</button>
      <button class="primary" data-run type="submit">调用并写入</button>
    </div>
  </form>`;
  const form = overlay.querySelector('form')!;
  const note = overlay.querySelector<HTMLElement>('.remote-data-note')!;
  const runBtn = overlay.querySelector<HTMLButtonElement>('[data-run]')!;
  const savedSelect = overlay.querySelector<HTMLSelectElement>('[name="saved"]')!;
  const store = loadStore();
  const values = host.selectionValues();
  overlay.querySelector('[data-selection]')!.textContent = host.selectionAddress() || '当前单元格';
  overlay.querySelector('[data-preview]')!.textContent = preview(values);
  savedSelect.replaceChildren(new Option('选择已保存的接口', ''));
  store.saved.forEach((item, index) => {
    savedSelect.append(new Option(item.name || item.url, String(index)));
  });
  fillForm(form, store.last);
  const close = () => overlay.remove();
  const show = (text: string, ok = false) => {
    note.hidden = false;
    note.textContent = text;
    note.classList.toggle('ok', ok);
  };
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  overlay.querySelector('[data-cancel]')!.addEventListener('click', close);
  savedSelect.addEventListener('change', () => {
    const item = store.saved[Number(savedSelect.value)];
    if (item) fillForm(form, item);
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const config = configFromForm(form);
    if (!config.url) {
      show('请填写接口地址。');
      return;
    }
    runBtn.disabled = true;
    show('正在调用远程接口…');
    try {
      const result = await office.json<{ values: Matrix; rows: number; cols: number }>('/sheets/remote-data', {
        method: 'POST',
        body: JSON.stringify({
          url: config.url,
          method: config.method,
          function: config.function,
          headers: config.headers || undefined,
          values,
        }),
      });
      if (!result.values?.length) throw new Error('远程接口没有返回数据。');
      const written = host.writeFromCursor(result.values);
      remember(config);
      show(`已写入 ${written.address} 起 ${written.rows} 行 ${written.cols} 列。`, true);
      setTimeout(close, 700);
    } catch (err) {
      show(err instanceof Error ? err.message : String(err));
    } finally {
      runBtn.disabled = false;
    }
  });
  document.body.append(overlay);
  form.querySelector<HTMLInputElement>('[name="url"]')?.focus();
}

export function selectionMatrix(range: { getRange(): { startRow: number; startColumn: number; endRow: number; endColumn: number }; getCellDatas(): unknown }): Matrix {
  const target = range.getRange();
  const rows = target.endRow - target.startRow + 1;
  const cols = target.endColumn - target.startColumn + 1;
  if (rows * cols > CELL_LIMIT) throw new Error(`请将选区缩小到 ${CELL_LIMIT} 个单元格以内。`);
  const cells = range.getCellDatas() as Array<Array<{ f?: string | null; v?: Cell } | null | undefined>>;
  return Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => {
    const cell = cells[r]?.[c];
    if (!cell) return null;
    if (typeof cell.f === 'string' && cell.f) return cell.f;
    if (cell.v === 0 || cell.v === false) return cell.v as Cell;
    return (cell.v ?? null) as Cell;
  }));
}

export function toUniverCells(values: Matrix) {
  const width = Math.max(...values.map(row => row.length), 1);
  return values.map(row => {
    const line = row.slice();
    while (line.length < width) line.push(null);
    return line.map(value => {
      if (typeof value === 'string' && value.startsWith('=')) return { f: value };
      if (typeof value === 'number') return { v: value, t: 2, f: null };
      if (typeof value === 'boolean') return { v: value, t: 3, f: null };
      if (typeof value === 'string') {
        const coerced = coerceNumericString(value);
        if (coerced !== null) return { v: coerced, t: 2, f: null };
      }
      return { v: value, f: null };
    });
  });
}

/** 纯数字文本 → 数值；保留前导零编号。支持千分位与百分号。 */
export function coerceNumericString(value: string): number | null {
  const raw = value.trim();
  if (!raw) return null;
  const isPercent = /%$/.test(raw);
  const s = raw.replace(/,/g, '').replace(/%$/, '').trim();
  if (!s) return null;
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(s)) return null;
  // 00123 这类编号保持文本（百分号除外）
  if (!isPercent && /^[+-]?0\d+$/.test(s)) return null;
  const num = Number(s);
  if (!Number.isFinite(num)) return null;
  return isPercent ? num / 100 : num;
}

/** 有原格式则保留；否则按文本形态推断百分比 / 财务 / 小数。 */
export function inferOrKeepNumberFormat(rawText: string, existing: string | null | undefined): string {
  const cur = String(existing || '').trim();
  if (cur && cur.toLowerCase() !== 'general') return cur;

  const s = rawText.trim();
  if (/%$/.test(s)) return '0.00%';

  const hasComma = s.includes(',');
  const cleaned = s.replace(/,/g, '').replace(/%$/, '');
  const frac = cleaned.includes('.') ? (cleaned.split('.')[1] || '').replace(/e.*/i, '') : '';
  const decimals = frac.length;

  if (hasComma && decimals > 0) return `#,##0.${'0'.repeat(Math.min(decimals, 6))}`;
  if (hasComma) return '#,##0';
  if (decimals > 0) return `0.${'0'.repeat(Math.min(decimals, 6))}`;
  return cur || 'General';
}
