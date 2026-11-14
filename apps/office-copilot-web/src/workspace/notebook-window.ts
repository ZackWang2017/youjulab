import { Editor } from '@tiptap/core';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { createIcons, icons } from 'lucide';
import { assetIndexExtensions } from '../asset-index';
import { office } from '../office-client/client';
import { circleBullet, listStack, nestedListTab, nestUnderNumber } from '../editors/rich-text-lists';
import { mergeAppendedTail } from '../quick/write';

const EMPTY_DOCUMENT = { type: 'doc', content: [{ type: 'paragraph' }] };

type NotebookAsset = {
  id: string;
  revision: number;
  content?: unknown;
};

let windowEl: HTMLElement | null = null;
let editor: Editor | null = null;
let currentAssetId = '';
let currentRevision = 0;
let loading = false;
let pending: unknown | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> = Promise.resolve();
let lastSavedAt: Date | null = null;
/** 最近一次与服务端一致的内容；托盘快速记录只追加，冲突时据此找出追加的尾部。 */
let baseContent: unknown = null;

class SaveConflict extends Error {}

async function putNotebook(assetId: string, content: unknown, revision: number) {
  const response = await office.request(`/assets/${assetId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, revision }),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 409) throw new SaveConflict(typeof data.error === 'string' ? data.error : '内容已在其它窗口更新。');
  if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `HTTP ${response.status}`);
  return data as { revision: number };
}

/** 本地有未保存修改时撞上快速记录的追加：把追加的尾部接到本地内容后再保存。 */
async function saveMergingQuickNotes(assetId: string, content: unknown) {
  try {
    return { saved: await putNotebook(assetId, content, currentRevision), content };
  } catch (error) {
    if (!(error instanceof SaveConflict)) throw error;
    const latest = await office.json<{ revision: number; content?: unknown }>(`/assets/${assetId}`);
    const merged = mergeAppendedTail(baseContent, content, latest.content);
    if (!merged) throw new Error('笔记本已在其它窗口修改，请复制当前内容后重新打开笔记本。');
    const saved = await putNotebook(assetId, merged, latest.revision);
    if (editor && currentAssetId === assetId) {
      loading = true;
      editor.commands.setContent(merged);
      loading = false;
    }
    return { saved, content: merged };
  }
}

function formatSavedAt(at: Date) {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`;
}

function setStatus(text: string, title = '') {
  const status = windowEl?.querySelector<HTMLElement>('[data-status]');
  if (!status) return;
  status.textContent = text;
  status.title = title || text;
}

function statusAfterSave() {
  if (!lastSavedAt) {
    setStatus('自动保存', '内容变更后会自动保存；也可按 ⌘/Ctrl+S 立即保存');
    return;
  }
  setStatus(`已保存 ${formatSavedAt(lastSavedAt)}`, '最后保存时间；⌘/Ctrl+S 可立即保存');
}

function flushSave() {
  clearTimeout(saveTimer);
  const content = pending;
  pending = null;
  if (!content || !currentAssetId) return saving;
  const assetId = currentAssetId;
  setStatus('正在保存…');
  saving = saving.then(async () => {
    try {
      const result = await saveMergingQuickNotes(assetId, content);
      currentRevision = result.saved.revision;
      baseContent = result.content;
      lastSavedAt = new Date();
      statusAfterSave();
      window.dispatchEvent(new CustomEvent('office-saved'));
      window.youju?.notifyAssetChanged?.({ id: assetId, kind: 'notebook', revision: currentRevision });
    } catch (err) {
      if (!pending) pending = content;
      setStatus('保存失败', err instanceof Error ? err.message : String(err));
      window.dispatchEvent(new CustomEvent('office-error', {
        detail: `笔记本保存失败：${err instanceof Error ? err.message : String(err)}`,
      }));
    }
  });
  return saving;
}

function queueSave(content: unknown) {
  pending = content;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { void flushSave(); }, 400);
}

export function notebookContainsFocus() {
  return Boolean(windowEl && !windowEl.hidden && windowEl.contains(document.activeElement));
}

/**
 * 其它窗口（托盘快速记录）保存了笔记本：没有未保存修改就直接换成最新内容；
 * 有未保存修改则留到保存时按「追加尾部」合并。
 */
export function applyRemoteNotebook(asset: NotebookAsset) {
  if (!editor || !windowEl || asset.id !== currentAssetId || asset.revision <= currentRevision) return;
  if (pending) return;
  loading = true;
  editor.commands.setContent(asset.content || EMPTY_DOCUMENT);
  loading = false;
  currentRevision = asset.revision;
  baseContent = asset.content || EMPTY_DOCUMENT;
}

export async function saveNotebookNow() {
  if (!editor || !currentAssetId || loading) return;
  pending = editor.getJSON();
  await flushSave();
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

function runCommand(command: string) {
  if (!editor) return;
  const chain = editor.chain().focus();
  if (command === 'bold') chain.toggleBold().run();
  if (command === 'italic') chain.toggleItalic().run();
  if (command === 'heading') chain.toggleHeading({ level: 2 }).run();
  if (command === 'heading3') chain.toggleHeading({ level: 3 }).run();
  if (command === 'bullet') {
    if (listStack(editor).length) nestUnderNumber(editor);
    else chain.toggleOrderedList().run();
  }
  if (command === 'ordered') chain.toggleOrderedList().run();
  if (command === 'quote') chain.toggleBlockquote().run();
  if (command === 'undo') chain.undo().run();
  if (command === 'redo') chain.redo().run();
}

export function openNotebookWindow(asset: NotebookAsset) {
  if (windowEl && editor) {
    if (currentAssetId !== asset.id) {
      loading = true;
      currentAssetId = asset.id;
      currentRevision = asset.revision;
      baseContent = asset.content || EMPTY_DOCUMENT;
      lastSavedAt = null;
      editor.commands.setContent(asset.content || EMPTY_DOCUMENT);
      loading = false;
    } else {
      applyRemoteNotebook(asset);
    }
    editor.setEditable(Boolean(asset.id));
    if (!asset.id) setStatus('正在加载');
    else statusAfterSave();
    document.body.append(windowEl);
    windowEl.hidden = false;
    if (asset.id) editor.commands.focus('end');
    return;
  }

  const win = document.createElement('section');
  win.className = 'notebook-window';
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-label', '笔记本');
  win.innerHTML = `
    <header class="notebook-title">
      <span><i data-lucide="notebook-pen"></i><strong>笔记本</strong></span>
      <small data-status title="内容变更后会自动保存；也可按 ⌘/Ctrl+S 立即保存">${asset.id ? '自动保存' : '正在加载'}</small>
      <button type="button" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button>
    </header>
    <div class="notebook-toolbar" role="toolbar" aria-label="笔记本格式">
      <button type="button" data-command="bold" title="加粗" aria-label="加粗"><i data-lucide="bold"></i></button>
      <button type="button" data-command="italic" title="斜体" aria-label="斜体"><i data-lucide="italic"></i></button>
      <button type="button" data-command="heading" title="标题 H2" aria-label="标题 H2"><i data-lucide="heading-2"></i></button>
      <button type="button" data-command="heading3" title="标题 H3" aria-label="标题 H3"><i data-lucide="heading-3"></i></button>
      <button type="button" data-command="bullet" title="无序列表" aria-label="无序列表"><i data-lucide="list"></i></button>
      <button type="button" data-command="ordered" title="有序列表" aria-label="有序列表"><i data-lucide="list-ordered"></i></button>
      <button type="button" data-command="quote" title="引用" aria-label="引用"><i data-lucide="quote"></i></button>
      <span aria-hidden="true"></span>
      <button type="button" data-command="undo" title="撤销" aria-label="撤销"><i data-lucide="undo-2"></i></button>
      <button type="button" data-command="redo" title="重做" aria-label="重做"><i data-lucide="redo-2"></i></button>
    </div>
    <div class="notebook-editor"></div>`;
  document.body.append(win);
  windowEl = win;
  currentAssetId = asset.id;
  currentRevision = asset.revision;
  baseContent = asset.content || EMPTY_DOCUMENT;
  lastSavedAt = null;
  createIcons({ icons });

  editor = new Editor({
    element: win.querySelector<HTMLElement>('.notebook-editor')!,
    extensions: [
      StarterKit.configure({ bulletList: false }),
      Link.configure({ openOnClick: true, autolink: true, defaultProtocol: 'https' }),
      circleBullet,
      nestedListTab,
      ...assetIndexExtensions(),
    ],
    content: asset.content || EMPTY_DOCUMENT,
    editable: Boolean(asset.id),
    onUpdate: ({ editor: instance }) => {
      if (!loading) queueSave(instance.getJSON());
    },
  });

  win.querySelectorAll<HTMLButtonElement>('[data-command]').forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = () => runCommand(button.dataset.command || '');
  });
  win.querySelector<HTMLButtonElement>('[data-close]')!.onclick = () => {
    queueSave(editor?.getJSON() || EMPTY_DOCUMENT);
    void flushSave();
    win.hidden = true;
  };
  win.addEventListener('keydown', event => {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 's' || event.altKey) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat) return;
    void saveNotebookNow();
  });
  bindDrag(win, win.querySelector<HTMLElement>('.notebook-title')!);
  if (asset.id) editor.commands.focus('end');
  window.addEventListener('pagehide', () => { void flushSave(); });
}

export function showNotebookLoadError(message: string) {
  if (!windowEl || !editor) return;
  editor.setEditable(false);
  setStatus('加载失败', message);
}
