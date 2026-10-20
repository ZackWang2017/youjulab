import { createIcons, icons } from 'lucide';
import { gantt } from 'dhtmlx-gantt';
import 'dhtmlx-gantt/codebase/dhtmlxgantt.css';
import type { EditorAdapter, Patch } from '../../context/types';
import { download } from '../../context/types';
import {
  PROJECT_PLAN_FORMAT, addDays, addSiblingTask, addTask, cascadeDelete, moveTask, normalizePlan, parseDate,
  type ProjectPlanContent, type ProjectPlanTask,
} from './model';
import './style.css';

type GanttTask = {
  id: string;
  text: string;
  parent: string | number;
  start_date: string;
  end_date: string;
  progress: number;
  order: number;
  open: boolean;
};

const escapeHtml = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value));

function asGanttTask(task: ProjectPlanTask): GanttTask {
  return {
    id: task.id,
    text: task.name,
    parent: task.parentId || 0,
    start_date: task.startDate,
    // DHTMLX treats end_date as exclusive for task duration; persist an inclusive date in the asset.
    end_date: addDays(task.endDate, 1),
    progress: task.progress / 100,
    order: task.order,
    open: true,
  };
}

function endFromGantt(value: unknown, fallback: string) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) return fallback;
  const date = new Date(value);
  date.setDate(date.getDate() - 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function mountProjectPlan(
  container: HTMLElement,
  changed: () => void,
  onDirty: (assetId: string, content: unknown) => void,
): EditorAdapter {
  let assetId = '';
  let revision = 0;
  let model: ProjectPlanContent = normalizePlan(null);
  let selectedId = '';
  let loading = false;
  let history: ProjectPlanContent[] = [];
  let zoomLevel = 'week';
  const treeWidthKey = 'office-project-plan-tree-width-v4';
  const treeCollapsedKey = 'office-project-plan-tree-collapsed';
  const fallbackTreeWidth = 600;
  let treeWidth = Number(localStorage.getItem(treeWidthKey)) || fallbackTreeWidth;
  let treeCollapsed = localStorage.getItem(treeCollapsedKey) === '1';

  container.innerHTML = `<div class="project-plan-shell">
    <div class="project-plan-toolbar">
      <button type="button" data-add-root><i data-lucide="plus"></i><span>添加事项</span></button>
      <div class="project-plan-zoom" role="group" aria-label="时间轴缩放">
        <button type="button" data-zoom="day">日</button><button type="button" data-zoom="week">周</button><button type="button" data-zoom="month">月</button>
      </div>
      <button type="button" data-export title="导出 JSON" aria-label="导出 JSON"><i data-lucide="download"></i></button>
    </div>
    <div class="project-plan-body"><div class="project-plan-gantt"><div class="project-plan-gantt-host"></div><div class="project-plan-tree-split" role="separator" aria-orientation="vertical" aria-label="调整或隐藏事项列表" tabindex="0"></div></div><aside class="project-plan-inspector" aria-label="事项详情" aria-hidden="true"></aside></div>
  </div>`;
  const stage = container.querySelector<HTMLElement>('.project-plan-gantt')!;
  const ganttHost = container.querySelector<HTMLElement>('.project-plan-gantt-host')!;
  const treeSplit = container.querySelector<HTMLElement>('.project-plan-tree-split')!;
  const body = container.querySelector<HTMLElement>('.project-plan-body')!;
  const inspector = container.querySelector<HTMLElement>('.project-plan-inspector')!;

  const clampTreeWidth = (value: number) => {
    if (stage.clientWidth <= 0) return Math.max(240, Math.round(value));
    const max = Math.max(240, Math.min(960, stage.clientWidth - 240));
    return Math.min(max, Math.max(240, Math.round(value)));
  };
  const refreshGantt = () => {
    gantt.render();
    gantt.setSizes();
  };
  const applyTreeLayout = (save = false) => {
    treeWidth = clampTreeWidth(treeWidth);
    gantt.config.grid_width = treeWidth;
    gantt.config.show_grid = !treeCollapsed;
    stage.style.setProperty('--project-plan-tree-width', `${treeWidth}px`);
    stage.classList.toggle('tree-collapsed', treeCollapsed);
    treeSplit.setAttribute('aria-expanded', String(!treeCollapsed));
    treeSplit.setAttribute('aria-label', treeCollapsed ? '显示事项列表' : '调整或隐藏事项列表');
    if (save) {
      localStorage.setItem(treeWidthKey, String(treeWidth));
      localStorage.setItem(treeCollapsedKey, treeCollapsed ? '1' : '0');
    }
    refreshGantt();
  };
  const setInspectorOpen = (open: boolean) => {
    body.classList.toggle('inspector-open', open);
    inspector.setAttribute('aria-hidden', String(!open));
    requestAnimationFrame(() => gantt.setSizes());
  };
  const closeInspector = () => {
    if (!selectedId) return;
    selectedId = '';
    gantt.unselectTask();
    renderInspector();
    setInspectorOpen(false);
  };

  gantt.config.xml_date = '%Y-%m-%d';
  gantt.config.date_format = '%Y-%m-%d';
  gantt.config.grid_width = treeWidth;
  gantt.config.row_height = 34;
  gantt.config.bar_height = 18;
  gantt.config.drag_move = true;
  gantt.config.drag_resize = true;
  gantt.config.drag_progress = true;
  gantt.config.order_branch = true;
  gantt.config.order_branch_free = true;
  gantt.config.show_links = false;
  gantt.config.auto_scheduling = false;
  gantt.config.details_on_dblclick = false;
  gantt.config.open_tree_initially = true;
  gantt.config.columns = [
    { name: 'text', label: '事项', tree: true, width: '*' },
    { name: 'start_date', label: '开始', align: 'center', width: 90, template: task => {
      const date = task.start_date as Date;
      return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    } },
    { name: 'end_date', label: '结束', align: 'center', width: 90, template: task => {
      const date = task.end_date as Date;
      const inclusiveEnd = new Date(date);
      inclusiveEnd.setDate(inclusiveEnd.getDate() - 1);
      return `${inclusiveEnd.getFullYear()}-${String(inclusiveEnd.getMonth() + 1).padStart(2, '0')}-${String(inclusiveEnd.getDate()).padStart(2, '0')}`;
    } },
    { name: 'progress', label: '进度', align: 'center', width: 54, template: task => `${Math.round((Number(task.progress) || 0) * 100)}%` },
  ];
  gantt.templates.task_text = (_start, _end, task) => task.text;
  gantt.templates.task_class = (_start, _end, task) => model.tasks.some(item => item.id === String(task.id) && item.parentId === null) ? 'project-plan-root' : '';
  gantt.ext.zoom.init({
    activeLevelIndex: 1,
    trigger: null,
    levels: [
      { name: 'day', scale_height: 54, min_column_width: 46, scales: [{ unit: 'month', step: 1, format: '%Y年 %m月' }, { unit: 'day', step: 1, format: '%d' }] },
      { name: 'week', scale_height: 54, min_column_width: 42, scales: [{ unit: 'month', step: 1, format: '%Y年 %m月' }, { unit: 'week', step: 1, format: '第%W周' }] },
      { name: 'month', scale_height: 54, min_column_width: 72, scales: [{ unit: 'year', step: 1, format: '%Y年' }, { unit: 'month', step: 1, format: '%m月' }] },
    ],
  });

  const snapshot = () => copy(model);
  const emit = () => {
    if (!assetId || loading) return;
    onDirty(assetId, snapshot());
    changed();
  };
  const remember = () => {
    history.push(snapshot());
    if (history.length > 50) history.shift();
  };
  const commit = (tasks: ProjectPlanTask[], select = selectedId) => {
    remember();
    model = normalizePlan({ tasks });
    selectedId = select;
    render();
    emit();
  };

  const renderInspector = () => {
    const task = model.tasks.find(item => item.id === selectedId);
    if (!task) {
      inspector.innerHTML = `<div class="project-plan-empty"><i data-lucide="list-tree"></i><p>选择一个事项进行编辑</p></div>`;
      createIcons({ icons });
      return;
    }
    const parent = model.tasks.some(item => item.parentId === task.id);
    inspector.innerHTML = `<div class="project-plan-inspector-head"><strong>事项详情</strong><div><span>${parent ? '由子计划汇总' : '可编辑'}</span><button type="button" data-close-inspector title="关闭事项详情" aria-label="关闭事项详情"><i data-lucide="x"></i></button></div></div>
      <label>名称<input data-field="name" maxlength="160" value="${escapeHtml(task.name)}"></label>
      <div class="project-plan-date-row"><label>开始日期<input data-field="startDate" type="date" value="${task.startDate}" ${parent ? 'disabled' : ''}></label><label>截止日期<input data-field="endDate" type="date" value="${task.endDate}" ${parent ? 'disabled' : ''}></label></div>
      <label>进度<input data-field="progress" type="number" min="0" max="100" step="1" value="${task.progress}" ${parent ? 'disabled' : ''}></label>
      <label>备注<textarea data-field="note" maxlength="4000" rows="6" placeholder="记录进展、风险或待办事项">${escapeHtml(task.note)}</textarea></label>
      ${parent ? '<p class="project-plan-derived">父事项的日期与进度会根据直接子计划自动更新。</p>' : ''}
      <div class="project-plan-actions"><button type="button" data-add-child title="添加子计划"><i data-lucide="plus"></i><span>添加子项</span></button><button type="button" data-add-sibling title="添加同级计划"><i data-lucide="plus"></i><span>添加同级</span></button><button type="button" data-delete class="project-plan-danger"><i data-lucide="trash-2"></i><span>删除</span></button></div>`;
    createIcons({ icons });
  };

  const render = () => {
    loading = true;
    gantt.clearAll();
    gantt.parse({ data: model.tasks.map(asGanttTask), links: [] });
    if (selectedId && gantt.isTaskExists(selectedId)) gantt.selectTask(selectedId);
    loading = false;
    renderInspector();
    setInspectorOpen(Boolean(selectedId));
    container.querySelectorAll<HTMLButtonElement>('[data-zoom]').forEach(button => button.classList.toggle('is-active', button.dataset.zoom === zoomLevel));
  };

  gantt.attachEvent('onTaskSelected', id => {
    if (loading) return true;
    selectedId = String(id);
    renderInspector();
    setInspectorOpen(true);
    return true;
  });
  gantt.attachEvent('onEmptyClick', () => closeInspector());
  gantt.attachEvent('onBeforeTaskMove', (id, parent) => {
    if (loading) return true;
    return moveTask(model.tasks, String(id), parent ? String(parent) : null, 0) !== null;
  });
  gantt.attachEvent('onAfterTaskMove', (id, parent, index) => {
    if (loading) return;
    const moved = moveTask(model.tasks, String(id), parent ? String(parent) : null, index);
    if (moved) commit(moved, String(id));
  });
  gantt.attachEvent('onAfterTaskUpdate', (id, task) => {
    if (loading) return;
    const current = model.tasks.find(item => item.id === String(id));
    if (!current) return;
    const tasks = model.tasks.map(item => item.id !== current.id ? item : {
      ...item,
      name: String(task.text || item.name).trim() || item.name,
      startDate: task.start_date instanceof Date ? `${task.start_date.getFullYear()}-${String(task.start_date.getMonth() + 1).padStart(2, '0')}-${String(task.start_date.getDate()).padStart(2, '0')}` : item.startDate,
      endDate: endFromGantt(task.end_date, item.endDate),
      progress: Math.round(Math.min(100, Math.max(0, (Number(task.progress) || 0) * 100))),
    });
    commit(tasks, current.id);
  });
  gantt.init(ganttHost);
  applyTreeLayout(false);

  let resizingTree = false;
  treeSplit.addEventListener('pointerdown', event => {
    if (event.button !== 0 || treeCollapsed) return;
    event.preventDefault();
    resizingTree = true;
    stage.classList.add('resizing-tree');
    try { treeSplit.setPointerCapture(event.pointerId); } catch { /* ignore */ }
  });
  const moveTreeSplit = (event: PointerEvent) => {
    if (!resizingTree) return;
    treeWidth = event.clientX - stage.getBoundingClientRect().left;
    applyTreeLayout(true);
  };
  const stopTreeResize = () => {
    if (!resizingTree) return;
    resizingTree = false;
    stage.classList.remove('resizing-tree');
  };
  treeSplit.addEventListener('pointermove', moveTreeSplit);
  treeSplit.addEventListener('pointerup', stopTreeResize);
  treeSplit.addEventListener('pointercancel', stopTreeResize);
  window.addEventListener('pointermove', moveTreeSplit);
  window.addEventListener('pointerup', stopTreeResize);
  treeSplit.addEventListener('dblclick', () => {
    treeCollapsed = !treeCollapsed;
    applyTreeLayout(true);
  });
  treeSplit.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      treeCollapsed = !treeCollapsed;
      applyTreeLayout(true);
      return;
    }
    if (treeCollapsed && event.key === 'ArrowRight') {
      event.preventDefault();
      treeCollapsed = false;
      applyTreeLayout(true);
      return;
    }
    if (treeCollapsed) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      treeWidth += event.key === 'ArrowLeft' ? -16 : 16;
      applyTreeLayout(true);
    }
  });
  window.addEventListener('resize', () => applyTreeLayout(false));

  container.addEventListener('click', event => {
    const target = event.target as HTMLElement;
    if (target.closest('[data-close-inspector]')) {
      closeInspector();
      return;
    }
    if (target.closest('[data-add-root]')) {
      const task = addTask(model.tasks, null);
      commit([...model.tasks, task], task.id);
      return;
    }
    const zoom = target.closest<HTMLElement>('[data-zoom]')?.dataset.zoom;
    if (zoom) {
      gantt.ext.zoom.setLevel(zoom);
      zoomLevel = zoom;
      container.querySelectorAll<HTMLButtonElement>('[data-zoom]').forEach(button => button.classList.toggle('is-active', button.dataset.zoom === zoom));
      return;
    }
    if (target.closest('[data-export]')) {
      download('project-plan.invp-project-plan-v1.json', JSON.stringify(model, null, 2));
      return;
    }
    if (target.closest('[data-add-child]') && selectedId) {
      const task = addTask(model.tasks, selectedId);
      commit([...model.tasks, task], task.id);
      return;
    }
    if (target.closest('[data-add-sibling]') && selectedId) {
      const result = addSiblingTask(model.tasks, selectedId);
      if (result) commit(result.tasks, result.task.id);
      return;
    }
    if (target.closest('[data-delete]') && selectedId) {
      const result = cascadeDelete(model.tasks, selectedId);
      const count = result.deleted.length;
      if (window.confirm(count === 1 ? '删除该事项？此操作无法恢复。' : `删除该事项及其 ${count - 1} 个子计划？此操作无法恢复。`)) commit(result.tasks, '');
    }
  });
  container.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeInspector();
  });
  inspector.addEventListener('change', event => {
    const input = event.target as HTMLInputElement;
    const field = input.dataset.field as keyof Pick<ProjectPlanTask, 'name' | 'startDate' | 'endDate' | 'progress' | 'note'> | undefined;
    const current = model.tasks.find(item => item.id === selectedId);
    if (!field || !current) return;
    let value: string | number = input.value;
    if (field === 'progress') value = Math.round(Math.min(100, Math.max(0, Number(value) || 0)));
    if ((field === 'startDate' || field === 'endDate') && !parseDate(value)) return;
    const tasks = model.tasks.map(item => item.id !== current.id ? item : { ...item, [field]: value });
    const edited = tasks.find(item => item.id === current.id)!;
    if (edited.endDate < edited.startDate) {
      if (field === 'startDate') edited.endDate = edited.startDate;
      else edited.startDate = edited.endDate;
    }
    commit(tasks, current.id);
  });

  return {
    context: () => null,
    anchor: () => null,
    apply: (_patch: Patch) => {},
    snapshot,
    undo() {
      const previous = history.pop();
      if (!previous) return;
      model = previous;
      render();
      emit();
    },
    export() { download('project-plan.invp-project-plan-v1.json', JSON.stringify(model, null, 2)); },
    load(asset) {
      assetId = asset.id;
      revision = asset.revision;
      history = [];
      const normalized = normalizePlan(asset.content);
      const wasNormalized = JSON.stringify(asset.content || {}) !== JSON.stringify(normalized);
      model = normalized;
      selectedId = '';
      render();
      if (wasNormalized && assetId) onDirty(assetId, snapshot());
    },
  };
}
