/** Markdown 待办；归档状态用行末专用注释持久化，兼容已有数据。 */
import { createIcons, icons } from 'lucide';

export type TodoItem = { done: boolean; text: string; archived?: boolean };

const TASK_LINE = /^\s*[-*+]\s+\[([ xX])\]\s?(.*)$/;
const ARCHIVED_MARKER = ' <!-- youju-todo:archived -->';

export function parseTodoMarkdown(markdown: string): TodoItem[] {
  const items: TodoItem[] = [];
  for (const line of String(markdown || '').split(/\r?\n/)) {
    const matched = line.match(TASK_LINE);
    if (!matched) continue;
    const archived = matched[2].endsWith(ARCHIVED_MARKER);
    items.push({ done: matched[1].toLowerCase() === 'x',
      text: archived ? matched[2].slice(0, -ARCHIVED_MARKER.length) : matched[2],
      ...(archived ? { archived: true } : {}),
    });
  }
  return items;
}

export function serializeTodoMarkdown(items: TodoItem[]): string {
  return items
    .filter(item => item.text.trim())
    .map(item => `- [${item.done ? 'x' : ' '}] ${item.text.trim()}${item.archived ? ARCHIVED_MARKER : ''}`)
    .join('\n');
}

let windowEl: HTMLElement | null = null;
let listEl: HTMLElement | null = null;
let addInput: HTMLInputElement | null = null;
let items: TodoItem[] = [];
let onChangeCb: ((next: string) => void) | null = null;
let archivedView = false;

function persist() {
  onChangeCb?.(serializeTodoMarkdown(items));
}

/**
 * 待办窗口挂在 document.body 下，日历资产切换前必须关闭它。
 * 先写回当前回调，避免旧窗口的内容在切换后误写到新日历。
 */
export function closeTodoList() {
  if (!windowEl || windowEl.hidden) return;
  persist();
  windowEl.hidden = true;
  onChangeCb = null;
  items = [];
  archivedView = false;
}

function bindDrag(win: HTMLElement, handle: HTMLElement) {
  let drag: { x: number; y: number; left: number; top: number } | null = null;
  handle.addEventListener('pointerdown', event => {
    if ((event.target as HTMLElement).closest('button')) return;
    const rect = win.getBoundingClientRect();
    drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', event => {
    if (!drag) return;
    const maxLeft = Math.max(8, window.innerWidth - win.offsetWidth - 8);
    const maxTop = Math.max(8, window.innerHeight - win.offsetHeight - 8);
    const left = Math.min(maxLeft, Math.max(8, drag.left + event.clientX - drag.x));
    const top = Math.min(maxTop, Math.max(8, drag.top + event.clientY - drag.y));
    win.style.left = `${left}px`;
    win.style.top = `${top}px`;
    win.style.right = 'auto';
  });
  const stop = () => { drag = null; };
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
}

function renderList() {
  if (!listEl) return;
  windowEl?.querySelectorAll<HTMLButtonElement>('[data-todo-view]').forEach(button => {
    button.setAttribute('aria-selected', String((button.dataset.todoView === 'archived') === archivedView));
  });
  const addRow = windowEl?.querySelector<HTMLElement>('.cal-todo-add');
  if (addRow) addRow.hidden = archivedView;
  listEl.replaceChildren();
  if (!items.some(item => Boolean(item.archived) === archivedView)) {
    const empty = document.createElement('p');
    empty.className = 'cal-me-empty';
    empty.textContent = archivedView ? '暂无已归档事项' : '暂无待办';
    listEl.append(empty);
    return;
  }
  items.forEach((item, index) => {
    if (Boolean(item.archived) !== archivedView) return;
    const row = document.createElement('div');
    row.className = `cal-todo-row${item.done ? ' is-done' : ''}`;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = item.done;
    box.disabled = archivedView;
    box.setAttribute('aria-label', item.done ? '标为未完成' : '标为已完成');
    const text = document.createElement('textarea');
    text.rows = 1;
    text.maxLength = 200;
    text.value = item.text.replace(/\r?\n/g, '');
    text.readOnly = archivedView;
    text.setAttribute('aria-label', '待办内容');
    const fitText = () => {
      text.style.height = '0';
      text.style.height = `${text.scrollHeight}px`;
    };
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'cal-todo-delete';
    del.innerHTML = '<i data-lucide="trash-2"></i>';
    del.title = '删除';
    del.setAttribute('aria-label', '删除');
    const archive = document.createElement('button');
    archive.type = 'button';
    archive.className = 'cal-todo-archive';
    archive.title = archivedView ? '取消归档' : '归档';
    archive.setAttribute('aria-label', archive.title);
    archive.innerHTML = `<i data-lucide="${archivedView ? 'archive-restore' : 'archive'}"></i>`;
    archive.addEventListener('click', () => {
      const next = { ...items[index] };
      if (next.archived) delete next.archived; else next.archived = true;
      items[index] = next;
      persist();
      renderList();
    });
    box.addEventListener('change', () => {
      items[index] = { ...items[index], done: box.checked };
      persist();
      renderList();
    });
    text.addEventListener('input', () => {
      const next = text.value.replace(/\r?\n/g, '');
      if (next !== text.value) text.value = next;
      items[index] = { ...items[index], text: next };
      persist();
      fitText();
    });
    text.addEventListener('blur', () => {
      items[index] = { ...items[index], text: text.value };
      persist();
      if (!text.value.trim()) renderList();
    });
    text.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      text.blur();
    });
    del.addEventListener('click', () => {
      items = items.filter((_, i) => i !== index);
      persist();
      renderList();
    });
    row.append(box, text, archive, del);
    listEl!.append(row);
    fitText();
  });
  createIcons({ icons });
}

/** 其它窗口改了待办：窗口开着且没在编辑某条时，换成最新列表。 */
export function refreshTodoList(markdown: string) {
  if (!windowEl || windowEl.hidden || !listEl) return;
  const active = document.activeElement;
  if (active instanceof HTMLTextAreaElement && listEl.contains(active)) return;
  items = parseTodoMarkdown(markdown);
  renderList();
}

export function openTodoList(
  markdown: string,
  onChange: (next: string) => void,
): Promise<void> {
  onChangeCb = onChange;
  items = parseTodoMarkdown(markdown);

  if (windowEl) {
    windowEl.hidden = false;
    document.body.append(windowEl);
    renderList();
    addInput?.focus();
    return Promise.resolve();
  }

  const win = document.createElement('section');
  win.className = 'calendar-todo-window';
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-labelledby', 'calendar-todo-title');
  win.innerHTML = `
    <header class="calendar-todo-titlebar">
      <strong id="calendar-todo-title">待办事项</strong>
      <div class="cal-todo-tabs" role="tablist" aria-label="待办视图">
        <button type="button" role="tab" data-todo-view="active" aria-selected="true">待办</button>
        <button type="button" role="tab" data-todo-view="archived" aria-selected="false">已归档</button>
      </div>
      <button type="button" class="cal-todo-close" data-close title="关闭" aria-label="关闭">×</button>
    </header>
    <div class="cal-todo-list"></div>
    <form class="cal-todo-add">
      <input type="text" maxlength="200" placeholder="添加待办，回车确认" aria-label="新待办">
      <button type="submit" class="primary">添加</button>
    </form>`;

  document.body.append(win);
  windowEl = win;
  listEl = win.querySelector('.cal-todo-list');
  const addRow = win.querySelector<HTMLFormElement>('.cal-todo-add')!;
  addInput = addRow.querySelector('input');
  win.querySelectorAll<HTMLButtonElement>('[data-todo-view]').forEach(button => {
    button.onclick = () => {
      archivedView = button.dataset.todoView === 'archived';
      renderList();
    };
  });

  addRow.addEventListener('submit', e => {
    e.preventDefault();
    const text = addInput?.value.trim() || '';
    if (!text) return;
    items = [...items, { done: false, text }];
    if (addInput) addInput.value = '';
    persist();
    renderList();
    addInput?.focus();
  });

  win.querySelector<HTMLButtonElement>('[data-close]')!.onclick = closeTodoList;

  bindDrag(win, win.querySelector<HTMLElement>('.calendar-todo-titlebar')!);
  renderList();
  addInput?.focus();
  return Promise.resolve();
}
