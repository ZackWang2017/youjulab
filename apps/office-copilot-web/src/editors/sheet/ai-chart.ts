import { createIcons, icons } from 'lucide';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsType } from 'echarts/core';
import { mountThinkingStatus } from '../../ui/thinking-status';

echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export type AiChartType = 'bar' | 'line' | 'pie' | 'scatter';
export type AiChartPlan = {
  title: string;
  chart_type: AiChartType;
  source_range: string;
  x_axis: { name: string; values: Array<string | number> };
  y_axes: Array<{ name: string; format: 'number' | 'percent' | 'currency' | 'integer' }>;
  series: Array<{ name: string; type: AiChartType; data: Array<number | null>; y_axis: 0 | 1 }>;
  summary: string;
};

export type ChartResponse = {
  model_name: string;
  context_source: 'shared_sheet_cache';
  profile_reused: boolean;
  chart: AiChartPlan;
};

export type SavedSheetChart = {
  id: string;
  sheet_id: string;
  name: string;
  description: string;
  chart: AiChartPlan;
  created_at?: string;
  updated_at?: string;
};

type Hooks = {
  selectionAddress: () => string;
  getModelStatus: () => Promise<{ configured: boolean; model_name: string }>;
  generate: (description: string, onEvent: (event: Record<string, unknown>) => void) => Promise<ChartResponse>;
  list: () => Promise<SavedSheetChart[]>;
  save: (name: string, description: string, chart: AiChartPlan) => Promise<SavedSheetChart>;
  rename: (id: string, name: string) => Promise<SavedSheetChart>;
};

const COLORS = ['#176b5b', '#2864a5', '#b84a3a', '#b77a18', '#7457a6', '#14828f', '#8d5662', '#52713d'];

function formatValue(value: number, format: string) {
  if (format === 'percent') return `${(Math.abs(value) <= 1 ? value * 100 : value).toFixed(2)}%`;
  if (format === 'currency') return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  if (format === 'integer') return String(Math.round(value));
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

function chartOption(plan: AiChartPlan) {
  const x = plan.x_axis.values;
  const mainType = plan.chart_type;
  if (mainType === 'pie') {
    const series = plan.series[0];
    return {
      color: COLORS,
      tooltip: { trigger: 'item', valueFormatter: (value: unknown) => formatValue(Number(value), plan.y_axes[0]?.format || 'number') },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        name: series.name,
        type: 'pie',
        radius: ['34%', '66%'],
        data: x.map((name, index) => ({ name: String(name), value: series.data[index] })),
        label: { formatter: '{b}\n{d}%' },
      }],
    };
  }
  const axes = plan.y_axes.length ? plan.y_axes : [{ name: '数值', format: 'number' as const }];
  const scatterUsesValueAxis = mainType === 'scatter'
    && x.length > 0
    && x.every(value => value !== '' && Number.isFinite(Number(value)));
  return {
    color: COLORS,
    tooltip: { trigger: mainType === 'scatter' ? 'item' : 'axis' },
    legend: { bottom: 0, type: 'scroll' },
    grid: { left: 62, right: axes.length > 1 ? 62 : 24, top: 28, bottom: 58, containLabel: true },
    xAxis: scatterUsesValueAxis ? {
      type: 'value', name: plan.x_axis.name, nameLocation: 'middle', nameGap: 34,
      axisLabel: { hideOverlap: true },
    } : {
      type: 'category', name: plan.x_axis.name, nameLocation: 'middle', nameGap: 34, data: x,
      axisLabel: { hideOverlap: true },
    },
    yAxis: axes.map((axis, index) => ({
      type: 'value', name: axis.name, position: index === 0 ? 'left' : 'right', alignTicks: true,
      splitLine: { show: index === 0 },
      axisLabel: { formatter: (value: number) => formatValue(value, axis.format) },
    })),
    series: plan.series.map((series, index) => ({
      name: series.name,
      type: series.type,
      yAxisIndex: Math.min(series.y_axis, axes.length - 1),
      data: series.type === 'scatter'
        ? series.data.map((value, point) => [scatterUsesValueAxis ? Number(x[point]) : x[point], value])
        : series.data,
      symbolSize: series.type === 'scatter' ? 10 : undefined,
      smooth: series.type === 'line',
      itemStyle: { color: COLORS[index % COLORS.length] },
      lineStyle: { color: COLORS[index % COLORS.length], width: 2 },
      emphasis: { focus: 'series' },
    })),
  };
}

function makeDraggable(win: HTMLElement, handle: HTMLElement) {
  let startX = 0;
  let startY = 0;
  let originX = 0;
  let originY = 0;
  const move = (event: PointerEvent) => {
    const maxX = Math.max(8, window.innerWidth - win.offsetWidth - 8);
    const maxY = Math.max(8, window.innerHeight - 48);
    win.style.left = `${Math.min(maxX, Math.max(8, originX + event.clientX - startX))}px`;
    win.style.top = `${Math.min(maxY, Math.max(8, originY + event.clientY - startY))}px`;
  };
  const stop = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', stop);
  };
  handle.addEventListener('pointerdown', event => {
    if ((event.target as HTMLElement).closest('button, input, textarea')) return;
    const rect = win.getBoundingClientRect();
    startX = event.clientX;
    startY = event.clientY;
    originX = rect.left;
    originY = rect.top;
    handle.setPointerCapture?.(event.pointerId);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  });
}

export function openAiChartAssistant(hooks: Hooks) {
  const existing = document.querySelector<HTMLElement>('.sheet-ai-chart-window');
  const existingClose = existing?.querySelector<HTMLButtonElement>('[data-close]');
  if (existingClose) existingClose.click();
  else existing?.remove();

  const win = document.createElement('section');
  win.className = 'sheet-ai-chart-window';
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-label', 'AI 生成图表');
  win.innerHTML = `
    <header class="sheet-ai-chart-head">
      <div class="sheet-ai-chart-title"><i data-lucide="chart-no-axes-combined"></i><strong>生成图表</strong></div>
      <button type="button" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button>
    </header>
    <div class="sheet-ai-chart-model">
      <span class="sheet-ai-chart-dot" aria-hidden="true"></span>
      <span data-model>正在连接模型…</span>
      <span data-context>共享表结构</span>
    </div>
    <div class="sheet-ai-chart-workspace">
      <aside class="sheet-ai-chart-library">
        <div class="sheet-ai-chart-library-head">
          <strong>已生成图表</strong>
          <button type="button" data-new title="新建图表" aria-label="新建图表"><i data-lucide="plus"></i></button>
        </div>
        <div class="sheet-ai-chart-list" data-list><span class="sheet-ai-chart-empty">正在读取…</span></div>
      </aside>
      <main class="sheet-ai-chart-content">
        <form class="sheet-ai-chart-form">
          <label><span>提示词</span><textarea rows="3" maxlength="2000" placeholder="例如：选区第一列作为 X 轴，收入用柱状图，同比增速用折线图并放在右轴"></textarea></label>
          <div class="sheet-ai-chart-form-row">
            <span data-selection></span>
            <button type="submit" class="primary"><i data-lucide="sparkles"></i><span>生成</span></button>
          </div>
        </form>
        <div class="sheet-ai-chart-thinking" data-thinking></div>
        <div class="sheet-ai-chart-result" data-result hidden>
          <div class="sheet-ai-chart-meta"><strong data-chart-title></strong><span data-source></span></div>
          <div class="sheet-ai-chart-canvas"></div>
          <p data-summary></p>
          <div class="sheet-ai-chart-save">
            <label><span>图表名称</span><input type="text" maxlength="80" data-name></label>
            <button type="button" class="primary" data-save><i data-lucide="save"></i><span>保存图表</span></button>
          </div>
        </div>
      </main>
    </div>`;
  document.body.append(win);
  createIcons({ icons });
  makeDraggable(win, win.querySelector<HTMLElement>('.sheet-ai-chart-head')!);

  const model = win.querySelector<HTMLElement>('[data-model]')!;
  const context = win.querySelector<HTMLElement>('[data-context]')!;
  const form = win.querySelector<HTMLFormElement>('form')!;
  const input = win.querySelector<HTMLTextAreaElement>('textarea')!;
  const submit = win.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  const submitLabel = submit.querySelector<HTMLElement>('span')!;
  const selection = win.querySelector<HTMLElement>('[data-selection]')!;
  const list = win.querySelector<HTMLElement>('[data-list]')!;
  const result = win.querySelector<HTMLElement>('[data-result]')!;
  const canvas = win.querySelector<HTMLElement>('.sheet-ai-chart-canvas')!;
  const nameInput = win.querySelector<HTMLInputElement>('[data-name]')!;
  const saveButton = win.querySelector<HTMLButtonElement>('[data-save]')!;
  const saveRow = win.querySelector<HTMLElement>('.sheet-ai-chart-save')!;
  const thinking = mountThinkingStatus(win.querySelector<HTMLElement>('[data-thinking]')!);
  let chart: EChartsType | null = null;
  let currentPlan: AiChartPlan | null = null;
  let savedCharts: SavedSheetChart[] = [];
  let closed = false;
  let modelReady = false;
  let generatedPrompt: string | null = null;
  submit.disabled = true;

  const normalizePrompt = (value: string) => value.trim().replace(/\s+/g, ' ');

  const refreshGenerateState = () => {
    const unchanged = currentPlan !== null
      && generatedPrompt !== null
      && normalizePrompt(input.value) === generatedPrompt;
    submit.disabled = !modelReady || unchanged;
    submitLabel.textContent = unchanged ? '已生成' : currentPlan ? '重新生成' : '生成';
  };

  const updateSelection = () => {
    const address = hooks.selectionAddress();
    selection.textContent = address ? `当前选区：${address}` : '未选择区域，将由模型读取整表结构';
  };

  const showPlan = (plan: AiChartPlan, savedName = '', canSave = true) => {
    currentPlan = plan;
    generatedPrompt = normalizePrompt(input.value);
    win.querySelector<HTMLElement>('[data-chart-title]')!.textContent = plan.title;
    win.querySelector<HTMLElement>('[data-source]')!.textContent = plan.source_range ? `数据：${plan.source_range}` : '';
    win.querySelector<HTMLElement>('[data-summary]')!.textContent = plan.summary;
    nameInput.value = savedName || plan.title || '未命名图表';
    saveRow.hidden = !canSave;
    result.hidden = false;
    chart?.dispose();
    chart = echarts.init(canvas);
    chart.setOption(chartOption(plan), true);
    requestAnimationFrame(() => chart?.resize());
    refreshGenerateState();
  };

  const beginRename = (item: SavedSheetChart, row: HTMLElement) => {
    row.classList.add('is-editing');
    const edit = row.querySelector<HTMLInputElement>('input')!;
    edit.value = item.name;
    edit.focus();
    edit.select();
    let committing = false;
    const commit = async () => {
      if (committing) return;
      const name = edit.value.trim();
      if (!name || name === item.name) {
        row.classList.remove('is-editing');
        return;
      }
      committing = true;
      edit.disabled = true;
      try {
        const updated = await hooks.rename(item.id, name);
        savedCharts = savedCharts.map(chartItem => chartItem.id === item.id ? updated : chartItem);
        renderList();
        if (currentPlan === item.chart) nameInput.value = updated.name;
      } catch (err) {
        thinking.clear();
        thinking.note(err instanceof Error ? err.message : String(err));
        edit.disabled = false;
        committing = false;
      }
    };
    edit.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); void commit(); }
      if (event.key === 'Escape') row.classList.remove('is-editing');
    };
    edit.onblur = () => void commit();
  };

  function renderList() {
    list.replaceChildren();
    if (!savedCharts.length) {
      const empty = document.createElement('span');
      empty.className = 'sheet-ai-chart-empty';
      empty.textContent = '当前工作表暂无图表';
      list.append(empty);
      return;
    }
    for (const item of savedCharts) {
      const row = document.createElement('div');
      row.className = 'sheet-ai-chart-list-item';
      row.innerHTML = `
        <button type="button" class="sheet-ai-chart-open"><i data-lucide="chart-column"></i><span></span></button>
        <input type="text" maxlength="80" aria-label="图表名称">
        <button type="button" class="sheet-ai-chart-rename" title="修改名称" aria-label="修改名称"><i data-lucide="pencil"></i></button>`;
      row.querySelector<HTMLElement>('.sheet-ai-chart-open span')!.textContent = item.name;
      row.querySelector<HTMLButtonElement>('.sheet-ai-chart-open')!.onclick = () => {
        input.value = item.description;
        showPlan(item.chart, item.name, false);
        thinking.clear();
        thinking.note('已打开保存的图表。');
      };
      row.querySelector<HTMLButtonElement>('.sheet-ai-chart-rename')!.onclick = () => beginRename(item, row);
      list.append(row);
    }
    createIcons({ icons });
  }

  const loadLibrary = async () => {
    try {
      savedCharts = await hooks.list();
      if (!closed) renderList();
    } catch (err) {
      if (!closed) {
        list.replaceChildren();
        const empty = document.createElement('span');
        empty.className = 'sheet-ai-chart-empty';
        empty.textContent = err instanceof Error ? err.message : String(err);
        list.append(empty);
      }
    }
  };

  const reset = () => {
    input.value = '';
    nameInput.value = '';
    currentPlan = null;
    generatedPrompt = null;
    result.hidden = true;
    chart?.dispose();
    chart = null;
    thinking.clear();
    refreshGenerateState();
    input.focus();
  };

  updateSelection();
  void loadLibrary();
  win.querySelector<HTMLButtonElement>('[data-new]')!.onclick = reset;

  const close = () => {
    closed = true;
    window.removeEventListener('resize', resize);
    chart?.dispose();
    chart = null;
    win.remove();
  };
  win.querySelector('[data-close]')!.addEventListener('click', close);
  window.setTimeout(() => input.focus(), 0);

  void hooks.getModelStatus().then(info => {
    if (closed) return;
    modelReady = info.configured;
    model.textContent = info.configured ? `模型：${info.model_name}` : '模型未配置';
    win.classList.toggle('is-disconnected', !info.configured);
    refreshGenerateState();
  }).catch(err => {
    if (closed) return;
    model.textContent = err instanceof Error ? err.message : '模型连接失败';
    win.classList.add('is-disconnected');
    submit.disabled = true;
  });

  input.addEventListener('input', refreshGenerateState);

  form.addEventListener('submit', event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    saveButton.disabled = true;
    result.hidden = true;
    currentPlan = null;
    generatedPrompt = null;
    chart?.dispose();
    chart = null;
    thinking.clear();
    thinking.begin();
    updateSelection();
    void hooks.generate(input.value.trim(), streamEvent => {
      if (streamEvent.type === 'delta' && streamEvent.part === 'reasoning' && streamEvent.text) {
        thinking.append(String(streamEvent.text));
      }
    }).then(response => {
      if (closed) return;
      model.textContent = `模型：${response.model_name}`;
      context.textContent = response.profile_reused ? '已复用协作助手表结构' : '已读取共享表结构';
      showPlan(response.chart);
      thinking.finish();
      thinking.note('图表已生成，可命名后保存。');
    }).catch(err => {
      if (closed) return;
      thinking.finish();
      thinking.note(err instanceof Error ? err.message : String(err));
    }).finally(() => {
      if (!closed) {
        refreshGenerateState();
        saveButton.disabled = false;
      }
    });
  });

  saveButton.onclick = () => {
    if (!currentPlan || saveButton.disabled) return;
    const name = nameInput.value.trim() || currentPlan.title || '未命名图表';
    saveButton.disabled = true;
    thinking.note('正在保存图表…');
    void hooks.save(name, input.value.trim(), currentPlan).then(saved => {
      savedCharts = [saved, ...savedCharts.filter(item => item.id !== saved.id)];
      renderList();
      nameInput.value = saved.name;
      saveRow.hidden = true;
      thinking.note(`已保存：${saved.name}`);
    }).catch(err => {
      thinking.note(err instanceof Error ? err.message : String(err));
    }).finally(() => {
      if (!closed) saveButton.disabled = false;
    });
  };

  function resize() { chart?.resize(); }
  window.addEventListener('resize', resize);
}
