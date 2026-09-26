import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  ToolboxComponent,
} from 'echarts/components';
import { LabelLayout, UniversalTransition } from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsType } from 'echarts/core';
import './custom-chart.css';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  ToolboxComponent,
  DatasetComponent,
  LabelLayout,
  UniversalTransition,
  CanvasRenderer,
]);

export type ChartKind = 'bar' | 'line' | 'pie';
export type UnitKind = 'finance' | 'percent' | 'integer' | 'decimal';

export interface SeriesConfig {
  name: string;
  kind: ChartKind;
  unit: UnitKind;
  visible: boolean;
}

export interface AmountMomYoyConfig {
  rangeA1: string;
  groupSize: number;
  series: SeriesConfig[];
  title: string;
}

export type SheetValueReader = {
  getActiveRangeA1: () => string;
  readNumbersByA1: (a1: string) => number[];
};

const UNIT_LABEL: Record<UnitKind, string> = {
  finance: '财务数值',
  percent: '百分比',
  integer: '整数',
  decimal: '小数',
};

const KIND_LABEL: Record<ChartKind, string> = {
  bar: '柱状图',
  line: '折线图',
  pie: '饼状图',
};

const SERIES_COLORS = ['#168274', '#c0392b', '#1a4f8c', '#d68910', '#7d3c98'];

const DEFAULT_NAMES = ['金额', '环比增长', '同比增长', '系列4', '系列5'];

function defaultSeries(groupSize: number): SeriesConfig[] {
  return Array.from({ length: groupSize }, (_, i) => {
    if (i === 0) {
      return { name: DEFAULT_NAMES[0], kind: 'bar' as const, unit: 'finance' as const, visible: true };
    }
    if (i === 1) {
      return { name: DEFAULT_NAMES[1], kind: 'line' as const, unit: 'percent' as const, visible: true };
    }
    if (i === 2) {
      return { name: DEFAULT_NAMES[2], kind: 'line' as const, unit: 'percent' as const, visible: true };
    }
    return {
      name: DEFAULT_NAMES[i] || `系列${i + 1}`,
      kind: 'line' as const,
      unit: 'decimal' as const,
      visible: true,
    };
  });
}

function toNumber(raw: unknown): number | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'boolean') return raw ? 1 : 0;
  if (typeof raw === 'string') {
    const s = raw.trim().replace(/,/g, '').replace(/%$/, '');
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** 解析 A1 / A1:B3 / A1,B2 等，返回各矩形范围的 A1（由调用方读值）。 */
export function splitRangeSpecs(input: string): string[] {
  return String(input || '')
    .split(/[,，;\s]+/)
    .map(s => s.trim())
    .filter(Boolean);
}

export function flattenMatrixNumbers(matrix: unknown[][]): number[] {
  const out: number[] = [];
  for (const row of matrix || []) {
    for (const cell of row || []) {
      const n = toNumber(cell);
      if (n !== null) out.push(n);
    }
  }
  return out;
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function formatByUnit(value: number, unit: UnitKind): string {
  if (unit === 'percent') {
    const pct = Math.abs(value) <= 1 ? value * 100 : value;
    return `${pct.toFixed(2)}%`;
  }
  if (unit === 'integer') return String(Math.round(value));
  if (unit === 'finance') {
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  return value.toFixed(2);
}

function validateConfig(cfg: AmountMomYoyConfig, numbers: number[]): string | null {
  if (!cfg.rangeA1.trim()) return '请选择或录入纵坐标数据区域。';
  if (cfg.groupSize < 1 || cfg.groupSize > 5) return '每组数字个数须为 1～5。';
  if (cfg.series.length !== cfg.groupSize) return '系列配置与每组数字个数不一致。';
  if (cfg.series.some(s => s.kind === 'pie') && cfg.groupSize !== 1) {
    return '饼状图仅在每组数字个数为 1 时可选。';
  }
  const units = [...new Set(cfg.series.map(s => s.unit))];
  if (units.length > 2) return '单位最多两种（对应左右两个纵轴）。';
  if (!numbers.length) return '所选区域没有可用的数字。';
  if (numbers.length < cfg.groupSize) return '数字个数不足一组，请扩大选区。';
  return null;
}

function buildChartOption(cfg: AmountMomYoyConfig, numbers: number[]) {
  const groups = chunk(numbers, cfg.groupSize).filter(g => g.length === cfg.groupSize);
  const categories = groups.map((_, i) => String(i + 1));
  const units = [...new Set(cfg.series.map(s => s.unit))];
  const unitAxisIndex = new Map(units.map((u, i) => [u, i]));

  const pieOnly = cfg.groupSize === 1 && cfg.series[0]?.kind === 'pie';
  if (pieOnly) {
    const series0 = cfg.series[0];
    return {
      title: { text: cfg.title || '自定义图表', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, selected: { [series0.name]: series0.visible } },
      series: [{
        name: series0.name,
        type: 'pie',
        radius: ['35%', '62%'],
        data: groups.map((g, i) => ({
          name: categories[i],
          value: g[0],
          itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
        })),
        label: {
          show: true,
          formatter: (p: { name: string; value: number }) =>
            `${p.name}\n${formatByUnit(p.value, series0.unit)}`,
        },
      }],
    };
  }

  const yAxis = units.map((unit, idx) => ({
    type: 'value' as const,
    name: UNIT_LABEL[unit],
    position: idx === 0 ? 'left' as const : 'right' as const,
    alignTicks: true,
    axisLabel: {
      formatter: (v: number) => formatByUnit(v, unit),
    },
    splitLine: { show: idx === 0 },
  }));

  const series = cfg.series.map((s, idx) => {
    const data = groups.map(g => g[idx] ?? null);
    const type = s.kind === 'pie' ? 'bar' : s.kind;
    return {
      name: s.name,
      type,
      yAxisIndex: unitAxisIndex.get(s.unit) ?? 0,
      data,
      itemStyle: { color: SERIES_COLORS[idx % SERIES_COLORS.length] },
      lineStyle: { width: 2, color: SERIES_COLORS[idx % SERIES_COLORS.length] },
      label: {
        show: true,
        position: 'top' as const,
        formatter: (p: { value: number | null }) =>
          p.value == null ? '' : formatByUnit(Number(p.value), s.unit),
      },
      emphasis: { focus: 'series' as const },
    };
  });

  const selected: Record<string, boolean> = {};
  for (const s of cfg.series) selected[s.name] = s.visible;

  return {
    title: { text: cfg.title || '自定义图表', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: unknown) => {
        const n = Number(v);
        return Number.isFinite(n) ? String(n) : '';
      },
    },
    legend: { bottom: 0, selected },
    grid: { left: 56, right: units.length > 1 ? 56 : 24, top: 48, bottom: 48 },
    xAxis: {
      type: 'category',
      data: categories,
      name: '序号',
      nameLocation: 'middle',
      nameGap: 28,
    },
    yAxis,
    series,
  };
}

function mountDraggable(win: HTMLElement, handle: HTMLElement) {
  let sx = 0;
  let sy = 0;
  let ox = 0;
  let oy = 0;
  let dragging = false;

  const onMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - sx;
    const dy = e.clientY - sy;
    win.style.left = `${ox + dx}px`;
    win.style.top = `${oy + dy}px`;
    win.style.right = 'auto';
    win.style.bottom = 'auto';
  };
  const onUp = () => {
    dragging = false;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };
  handle.addEventListener('pointerdown', e => {
    if ((e.target as HTMLElement).closest('button')) return;
    dragging = true;
    sx = e.clientX;
    sy = e.clientY;
    const rect = win.getBoundingClientRect();
    ox = rect.left;
    oy = rect.top;
    win.style.left = `${ox}px`;
    win.style.top = `${oy}px`;
    win.style.right = 'auto';
    win.style.transform = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  });
}

export function openChartWindow(
  cfg: AmountMomYoyConfig,
  numbers: number[],
  hooks: {
    onEdit: (current: AmountMomYoyConfig) => void;
  },
) {
  document.querySelectorAll('.sheet-chart-window').forEach(el => el.remove());

  const win = document.createElement('div');
  win.className = 'sheet-chart-window';
  win.style.left = '120px';
  win.style.top = '120px';

  const head = document.createElement('div');
  head.className = 'sheet-chart-window-head';
  const title = document.createElement('strong');
  title.textContent = cfg.title || '自定义图表';
  const ops = document.createElement('div');
  ops.className = 'sheet-chart-window-ops';
  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.textContent = '编辑';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.textContent = '关闭';
  ops.append(editBtn, closeBtn);
  head.append(title, ops);

  const toggles = document.createElement('div');
  toggles.className = 'sheet-chart-toggles';

  const body = document.createElement('div');
  body.className = 'sheet-chart-window-body';
  const canvas = document.createElement('div');
  canvas.className = 'sheet-chart-canvas';
  body.append(canvas);

  win.append(head, toggles, body);
  document.body.append(win);
  mountDraggable(win, head);

  let chart: EChartsType | null = echarts.init(canvas);
  let live = { ...cfg, series: cfg.series.map(s => ({ ...s })) };

  const render = () => {
    chart?.setOption(buildChartOption(live, numbers), true);
  };

  const rebuildToggles = () => {
    toggles.replaceChildren();
    live.series.forEach((s, idx) => {
      const label = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = s.visible;
      box.addEventListener('change', () => {
        live.series[idx] = { ...live.series[idx], visible: box.checked };
        render();
      });
      const text = document.createElement('span');
      text.textContent = s.name;
      label.append(box, text);
      toggles.append(label);
    });
  };

  rebuildToggles();
  render();

  const onResize = () => chart?.resize();
  window.addEventListener('resize', onResize);

  const destroy = () => {
    window.removeEventListener('resize', onResize);
    chart?.dispose();
    chart = null;
    win.remove();
  };

  closeBtn.addEventListener('click', destroy);
  editBtn.addEventListener('click', () => {
    const snapshot = { ...live, series: live.series.map(s => ({ ...s })) };
    destroy();
    hooks.onEdit(snapshot);
  });

  requestAnimationFrame(() => chart?.resize());
}

function renderSeriesEditors(
  host: HTMLElement,
  groupSize: number,
  series: SeriesConfig[],
  onChange: (next: SeriesConfig[]) => void,
) {
  host.replaceChildren();
  const next = series.slice(0, groupSize);
  while (next.length < groupSize) {
    next.push(...defaultSeries(groupSize).slice(next.length, groupSize));
  }
  next.splice(groupSize);

  next.forEach((s, idx) => {
    const row = document.createElement('div');
    row.className = 'sheet-chart-series-row';

    const name = document.createElement('input');
    name.type = 'text';
    name.value = s.name;
    name.placeholder = `系列${idx + 1}`;
    name.addEventListener('change', () => {
      next[idx] = { ...next[idx], name: name.value.trim() || `系列${idx + 1}` };
      onChange(next.map(x => ({ ...x })));
    });

    const kind = document.createElement('select');
    (['bar', 'line', 'pie'] as ChartKind[]).forEach(k => {
      const opt = document.createElement('option');
      opt.value = k;
      opt.textContent = KIND_LABEL[k];
      if (k === 'pie' && groupSize !== 1) opt.disabled = true;
      kind.append(opt);
    });
    kind.value = s.kind === 'pie' && groupSize !== 1 ? 'bar' : s.kind;
    kind.addEventListener('change', () => {
      next[idx] = { ...next[idx], kind: kind.value as ChartKind };
      onChange(next.map(x => ({ ...x })));
    });

    const unit = document.createElement('select');
    (Object.keys(UNIT_LABEL) as UnitKind[]).forEach(u => {
      const opt = document.createElement('option');
      opt.value = u;
      opt.textContent = UNIT_LABEL[u];
      unit.append(opt);
    });
    unit.value = s.unit;
    unit.addEventListener('change', () => {
      next[idx] = { ...next[idx], unit: unit.value as UnitKind };
      onChange(next.map(x => ({ ...x })));
    });

    const idxLabel = document.createElement('span');
    idxLabel.className = 'sheet-chart-series-idx';
    idxLabel.textContent = `第${idx + 1}个数`;

    row.append(idxLabel, name, kind, unit);
    host.append(row);
  });

  onChange(next.map(x => ({ ...x })));
}

export function openAmountMomYoyConfig(
  reader: SheetValueReader,
  initial: AmountMomYoyConfig | null,
  onGenerated: (cfg: AmountMomYoyConfig, numbers: number[]) => void,
): Promise<void> {
  return new Promise(resolve => {
    const state: AmountMomYoyConfig = initial
      ? {
          ...initial,
          series: initial.series.map(s => ({ ...s })),
        }
      : {
          title: '自定义图表',
          rangeA1: reader.getActiveRangeA1() || '',
          groupSize: 3,
          series: defaultSeries(3),
        };

    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay sheet-chart-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'sheet-chart-dialog';
    dialog.setAttribute('role', 'dialog');

    dialog.innerHTML = `
      <h2>自定义图表</h2>
      <p class="sheet-chart-hint">纵坐标取选区或坐标中的数字；按「每组个数」分组，横坐标自动标为 1、2、3…。单位最多两种，对应左右纵轴。默认 3 个一组可作金额 / 环比 / 同比组合图。</p>
      <label class="sheet-chart-field">
        <span>纵坐标数据（单元格坐标）</span>
        <div class="sheet-chart-range-row">
          <input data-range type="text" placeholder="如 B2:D20 或 B2,C3,D4" />
          <button type="button" data-use-sel>使用当前选区</button>
        </div>
      </label>
      <label class="sheet-chart-field">
        <span>每组数字个数</span>
        <select data-group-size>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
          <option value="5">5</option>
        </select>
      </label>
      <div class="sheet-chart-field">
        <span>各维度系列（图表类型 / 单位）</span>
        <div data-series-host class="sheet-chart-series-host"></div>
      </div>
      <p class="sheet-chart-note" hidden></p>
      <div class="dialog-actions">
        <button type="button" data-cancel>取消</button>
        <button type="button" class="primary" data-generate>生成</button>
      </div>
    `;

    overlay.append(dialog);
    document.body.append(overlay);

    const rangeInput = dialog.querySelector<HTMLInputElement>('[data-range]')!;
    const groupSelect = dialog.querySelector<HTMLSelectElement>('[data-group-size]')!;
    const seriesHost = dialog.querySelector<HTMLElement>('[data-series-host]')!;
    const note = dialog.querySelector<HTMLElement>('.sheet-chart-note')!;

    rangeInput.value = state.rangeA1;
    groupSelect.value = String(state.groupSize);

    const showNote = (text: string, ok = false) => {
      note.hidden = !text;
      note.textContent = text;
      note.classList.toggle('ok', ok);
    };

    const syncSeriesUi = () => {
      renderSeriesEditors(seriesHost, state.groupSize, state.series, next => {
        state.series = next;
      });
    };
    syncSeriesUi();

    dialog.querySelector('[data-use-sel]')!.addEventListener('click', () => {
      const a1 = reader.getActiveRangeA1();
      if (!a1) {
        showNote('请先在表格中选中数据区域。');
        return;
      }
      rangeInput.value = a1;
      state.rangeA1 = a1;
      showNote(`已填入选区 ${a1}`, true);
    });

    rangeInput.addEventListener('change', () => {
      state.rangeA1 = rangeInput.value.trim();
    });

    groupSelect.addEventListener('change', () => {
      const size = Math.min(5, Math.max(1, Number(groupSelect.value) || 1));
      state.groupSize = size;
      // 切到默认推荐：3 时金额柱 + 环比/同比折线
      if (!initial || initial.groupSize !== size) {
        state.series = defaultSeries(size);
      } else {
        state.series = defaultSeries(size).map((d, i) => state.series[i] ? { ...d, ...state.series[i], kind: size === 1 ? state.series[i].kind : (state.series[i].kind === 'pie' ? 'line' : state.series[i].kind) } : d);
      }
      syncSeriesUi();
    });

    const close = () => {
      overlay.remove();
      resolve();
    };

    dialog.querySelector('[data-cancel]')!.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

    dialog.querySelector('[data-generate]')!.addEventListener('click', () => {
      state.rangeA1 = rangeInput.value.trim();
      state.groupSize = Math.min(5, Math.max(1, Number(groupSelect.value) || 1));
      let numbers: number[] = [];
      try {
        numbers = reader.readNumbersByA1(state.rangeA1);
      } catch (err) {
        showNote(err instanceof Error ? err.message : '读取单元格失败。');
        return;
      }
      const err = validateConfig(state, numbers);
      if (err) {
        showNote(err);
        return;
      }
      onGenerated({
        ...state,
        series: state.series.map(s => ({ ...s })),
      }, numbers);
      close();
    });
  });
}

export function launchAmountMomYoyChart(reader: SheetValueReader, seed: AmountMomYoyConfig | null = null) {
  void openAmountMomYoyConfig(reader, seed, (cfg, numbers) => {
    openChartWindow(cfg, numbers, {
      onEdit: current => {
        void launchAmountMomYoyChart(reader, current);
      },
    });
  });
}
