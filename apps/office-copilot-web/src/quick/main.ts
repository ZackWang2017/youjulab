/** 托盘快速记录小窗：?mode=notebook 追加笔记，?mode=todos 管理待办。 */
import { createIcons, icons } from 'lucide';
import { parseTodoMarkdown, type TodoItem } from '../editors/calendar/todos';
import { NotSignedInError, quickApi, signedIn } from './api';
import { appendNoteToDoc, applyTodoOpToCalendar, saveWithRetry, type QuickAsset, type TodoOp } from './write';
import './quick.css';

type Mode = 'notebook' | 'todos';

const mode: Mode = new URLSearchParams(location.search).get('mode') === 'todos' ? 'todos' : 'notebook';
const mac = window.youju?.platform ? window.youju.platform === 'darwin' : /mac/i.test(navigator.platform);
const MOD = mac ? '⌘' : 'Ctrl+';
const SHIFT = mac ? '⇧' : 'Shift+';
const root = document.querySelector<HTMLElement>('#quick')!;
document.title = mode === 'todos' ? '待办事项 · 有据' : '记笔记 · 有据';

function shell(title: string, icon: string, body: string, footer = '') {
  root.innerHTML = `<header class="quick-head"><span><i data-lucide="${icon}"></i><strong>${title}</strong></span><small data-status></small></header>
    <main class="quick-body">${body}</main>
    <footer class="quick-foot">${footer}</footer>`;
  createIcons({ icons });
}

function setStatus(text: string, kind: '' | 'ok' | 'error' = '') {
  const el = root.querySelector<HTMLElement>('[data-status]');
  if (!el) return;
  el.textContent = text;
  el.className = kind ? `is-${kind}` : '';
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function notify(asset: QuickAsset, kind: 'notebook' | 'calendar') {
  window.youju?.notifyAssetChanged?.({ id: asset.id, kind, revision: asset.revision });
}

function renderSignedOut() {
  shell(mode === 'todos' ? '待办事项' : '记笔记', 'log-in',
    '<div class="quick-empty"><p>请先在主窗口登录有据，再使用快速记录。</p><button type="button" class="primary" data-open-main>打开有据</button></div>');
  root.querySelector<HTMLButtonElement>('[data-open-main]')!.onclick = () => window.youju?.openMainTool?.();
}

function handleFatal(error: unknown) {
  if (error instanceof NotSignedInError) {
    renderSignedOut();
    return;
  }
  setStatus(errorText(error), 'error');
}

async function loadTool(kind: 'notebook' | 'calendar', cached: QuickAsset | null) {
  if (cached) return quickApi.asset(cached.id);
  const tools = await quickApi.tools();
  const asset = tools[kind];
  if (!asset) throw new Error(kind === 'notebook' ? '找不到笔记本。' : '找不到日历。');
  return asset;
}

function mountNotebook() {
  shell('记笔记', 'notebook-pen',
    `<textarea class="quick-note" maxlength="20000" placeholder="随手记下想法&#10;${MOD}Enter 保存继续记，${MOD}${SHIFT}Enter 保存并关闭" aria-label="笔记内容"></textarea>`,
    `<button type="button" data-open-full>打开完整笔记本</button><span class="quick-foot-gap"></span><button type="button" data-save title="${MOD}Enter / ${MOD}S">保存</button><button type="button" class="primary" data-save-close title="${MOD}${SHIFT}Enter">保存并关闭</button>`);
  const input = root.querySelector<HTMLTextAreaElement>('.quick-note')!;
  const saveBtn = root.querySelector<HTMLButtonElement>('[data-save]')!;
  const saveCloseBtn = root.querySelector<HTMLButtonElement>('[data-save-close]')!;
  let notebook: QuickAsset | null = null;
  let saving = false;

  /** 返回是否已安全落盘（空内容视为无需保存）。 */
  const save = async (): Promise<boolean> => {
    const text = input.value;
    if (!text.trim()) return true;
    if (saving) return false;
    saving = true;
    saveBtn.disabled = true;
    saveCloseBtn.disabled = true;
    setStatus('正在保存…');
    const at = new Date();
    try {
      notebook = await saveWithRetry(
        () => loadTool('notebook', notebook),
        content => appendNoteToDoc(content, text, at),
        quickApi.save,
      );
      notify(notebook, 'notebook');
      if (input.value === text) input.value = '';
      setStatus(`已追加到笔记本 ${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`, 'ok');
      return true;
    } catch (error) {
      handleFatal(error);
      return false;
    } finally {
      saving = false;
      saveBtn.disabled = false;
      saveCloseBtn.disabled = false;
      input.focus();
    }
  };
  // 保存失败时保留窗口和内容，避免丢稿
  const saveAndClose = async () => {
    if (await save()) window.close();
  };

  saveBtn.onclick = () => { void save(); };
  saveCloseBtn.onclick = () => { void saveAndClose(); };
  document.addEventListener('keydown', event => {
    const mod = event.metaKey || event.ctrlKey;
    if (!mod || event.altKey) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      void (event.shiftKey ? saveAndClose() : save());
    } else if (event.key.toLowerCase() === 's' && !event.shiftKey) {
      event.preventDefault();
      void save();
    }
  });
  root.querySelector<HTMLButtonElement>('[data-open-full]')!.onclick = () => window.youju?.openMainTool?.('notebook');
  window.addEventListener('focus', () => input.focus());
  input.focus();
}

function mountTodos() {
  shell('待办事项', 'list-todo',
    '<div class="quick-todos" aria-live="polite"></div>',
    `<form class="quick-add"><input type="text" maxlength="200" placeholder="添加待办，回车确认；${MOD}${SHIFT}Enter 保存并关闭" aria-label="新待办"><button type="submit" class="primary">添加</button></form>`);
  const list = root.querySelector<HTMLElement>('.quick-todos')!;
  const form = root.querySelector<HTMLFormElement>('.quick-add')!;
  const addInput = form.querySelector('input')!;
  let calendar: QuickAsset | null = null;
  let items: TodoItem[] = [];
  let queue: Promise<void> = Promise.resolve();
  let lastFailed = false;
  let archivedView = false;
  const tabs = document.createElement('div');
  tabs.className = 'quick-todo-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', '待办视图');
  tabs.innerHTML = '<button type="button" role="tab" data-todo-view="active" aria-selected="true">待办</button><button type="button" role="tab" data-todo-view="archived" aria-selected="false">已归档</button>';
  root.querySelector('.quick-head')!.insertBefore(tabs, root.querySelector('[data-status]'));
  tabs.querySelectorAll<HTMLButtonElement>('button').forEach(button => {
    button.onclick = () => { archivedView = button.dataset.todoView === 'archived'; render(); };
  });

  const show = (asset: QuickAsset) => {
    calendar = asset;
    const content = asset.content as { todos?: unknown } | undefined;
    items = parseTodoMarkdown(typeof content?.todos === 'string' ? content.todos : '');
    render();
  };

  // 串行提交：连续点选时按顺序写入，每次都基于最新版本重做
  const apply = (op: TodoOp) => {
    queue = queue.then(async () => {
      setStatus('正在保存…');
      try {
        const saved = await saveWithRetry(
          () => loadTool('calendar', calendar),
          content => applyTodoOpToCalendar(content, op),
          quickApi.save,
        );
        show(saved);
        notify(saved, 'calendar');
        setStatus('已保存', 'ok');
        lastFailed = false;
      } catch (error) {
        lastFailed = true;
        handleFatal(error);
      }
    });
    return queue;
  };

  /** 提交输入框里未添加的待办和正在编辑的那条，全部写入成功后关窗；失败则留窗。 */
  const saveAndClose = async () => {
    const active = document.activeElement;
    if (active instanceof HTMLInputElement && list.contains(active)) active.blur();
    const text = addInput.value.trim();
    if (text) {
      addInput.value = '';
      void apply({ type: 'add', text });
    }
    await queue;
    if (!lastFailed) window.close();
  };
  document.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && event.shiftKey && !event.altKey) {
      event.preventDefault();
      void saveAndClose();
    }
  });

  function render() {
    tabs.querySelectorAll<HTMLButtonElement>('button').forEach(button => {
      button.setAttribute('aria-selected', String((button.dataset.todoView === 'archived') === archivedView));
    });
    root.querySelector<HTMLElement>('.quick-foot')!.hidden = archivedView;
    list.replaceChildren();
    const visibleItems = items.filter(item => Boolean(item.archived) === archivedView);
    if (!visibleItems.length) {
      const empty = document.createElement('p');
      empty.className = 'quick-empty-line';
      empty.textContent = archivedView ? '暂无已归档事项' : '暂无待办';
      list.append(empty);
      return;
    }
    for (const item of visibleItems) {
      const row = document.createElement('div');
      row.className = `quick-todo${item.done ? ' is-done' : ''}`;
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = item.done;
      box.disabled = archivedView;
      box.setAttribute('aria-label', item.done ? '标为未完成' : '标为已完成');
      const text = document.createElement('input');
      text.type = 'text';
      text.maxLength = 200;
      text.value = item.text;
      text.readOnly = archivedView;
      text.setAttribute('aria-label', '待办内容');
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'quick-delete';
      del.title = '删除';
      del.setAttribute('aria-label', '删除');
      del.innerHTML = '<i data-lucide="x"></i>';
      const archive = document.createElement('button');
      archive.type = 'button';
      archive.className = 'quick-archive';
      archive.title = archivedView ? '取消归档' : '归档';
      archive.setAttribute('aria-label', archive.title);
      archive.innerHTML = `<i data-lucide="${archivedView ? 'archive-restore' : 'archive'}"></i>`;
      archive.onclick = () => { void apply({ type: 'archive', text: text.value.trim(), archived: !item.archived }); };
      box.onchange = () => { void apply({ type: 'toggle', text: text.value.trim(), done: box.checked }); };
      const commitEdit = () => {
        const next = text.value.trim();
        if (next === item.text) return;
        void apply({ type: 'edit', text: item.text, next });
      };
      text.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); text.blur(); }
        if (event.key === 'Escape') { text.value = item.text; text.blur(); event.stopPropagation(); }
      });
      text.addEventListener('blur', commitEdit);
      del.onclick = () => { void apply({ type: 'delete', text: text.value.trim(), archived: Boolean(item.archived) }); };
      row.append(box, text, archive, del);
      list.append(row);
    }
    createIcons({ icons });
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    const text = addInput.value.trim();
    if (!text) return;
    addInput.value = '';
    void apply({ type: 'add', text });
    addInput.focus();
  });

  const reload = async () => {
    try {
      show(await loadTool('calendar', calendar));
      setStatus('');
    } catch (error) {
      handleFatal(error);
    }
  };
  // 主窗口改了待办时刷新；正在编辑某条时不打断
  window.youju?.onAssetChanged?.(change => {
    if (change.kind !== 'calendar' || (calendar && change.id !== calendar.id)) return;
    if (calendar && change.revision <= calendar.revision) return;
    const active = document.activeElement;
    if (active instanceof HTMLInputElement && list.contains(active)) return;
    void reload();
  });
  window.addEventListener('focus', () => { void reload(); if (!archivedView) addInput.focus(); });
  void reload();
  addInput.focus();
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  // 笔记还没保存时不因误按 Esc 关窗丢内容
  const note = root.querySelector<HTMLTextAreaElement>('.quick-note');
  if (note?.value.trim()) return;
  window.close();
});

if (!signedIn()) renderSignedOut();
else if (mode === 'todos') mountTodos();
else mountNotebook();
