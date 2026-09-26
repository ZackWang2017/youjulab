import './polyfill';
import { createIcons, icons } from 'lucide';
import { mountSheet } from './editors/sheet';
import { mountDocument } from './editors/document';
import { mountPdf } from './editors/pdf';
import { openTermsDialog } from './editors/pdf/terms-dialog';
import { mountMindmap } from './editors/mindmap';
import { askCalendarCreate, mountCalendar } from './editors/calendar';
import { office, AuthError } from './office-client/client';
import { renderPatch } from './patches/preview';
import type { Context, EditorAdapter, Patch, Surface } from './context/types';
import { persist, saved } from './context/types';
import {
  bootstrap, createAsset, createProject, flushSave, getAsset, hasUnsavedWork, login, logout, openAsset, queueSave,
  register, rememberRevision, renameAsset, renameProject, retrySave, selectProject, uploadPdf, type Asset, type Kind, type Workspace,
} from './workspace/session';
import { alertSaveError, askConfirm, askText, renderAuth, renderChrome, renderNav } from './workspace/nav';
import { loadPdfFolders, placePdf, savePdfFolders } from './workspace/pdf-folders';
import {
  mountAssistantSessions, restoreLastSession, type AssistantSession, type SessionController,
} from './assistant-sessions';
import { markdownToHtml } from './editors/document/markdown';
import './style.css';

const $ = <T extends HTMLElement = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const root = document.querySelector<HTMLElement>('#app')!;
let surface: Surface = 'sheet';
let ready = false;
let workspace: Workspace | null = null;
let pending: { patch: Patch; context: Context } | null = null;
let running = false;
let editors: Record<Surface, EditorAdapter> | null = null;
let lastContext: Context | null = null;
type AssistantTurn = { role: 'user' | 'assistant'; content: string; thinking?: string };

type LiveAssistant = {
  answer: string;
  thinking: string;
  thinkingOpen: boolean;
  answerStarted: boolean;
};
const MAX_API_HISTORY = 20;
let assistantThread: { anchor: Context; turns: AssistantTurn[] } | null = null;
let liveAssistant: LiveAssistant | null = null;
let sessionController: SessionController | null = null;
let sessionSummary = '';

window.addEventListener('wheel', event => {
  if (event.ctrlKey || event.metaKey) return;
  if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
  event.preventDefault();
}, { passive: false, capture: true });

const ALL_KINDS: Kind[] = ['sheet', 'document', 'pdf', 'mindmap', 'calendar'];

function fail(e: unknown) {
  const box = document.querySelector<HTMLElement>('#error');
  if (!box) return;
  box.hidden = false;
  box.textContent = e instanceof Error ? e.message : String(e);
}

function hasTaskContent(ctx: Context | null) {
  if (!ctx) return false;
  if (ctx.surface === 'sheet') {
    return (ctx.selection.values as unknown[][]).some(row => row.some(v => v != null && String(v).trim() !== ''));
  }
  if (ctx.surface === 'calendar') {
    return Boolean(String(ctx.selection.text || '').trim());
  }
  return Boolean(String(ctx.selection.text || '').trim());
}

function taskMode(): 'explain' | 'generate' {
  return $<HTMLInputElement>('#task-generate')?.checked ? 'generate' : 'explain';
}

function isSheetAssistant() {
  return surface === 'sheet';
}

function syncTaskModes() {
  const app = document.querySelector<HTMLElement>('#app');
  app?.classList.toggle('sheet-assistant-mode', isSheetAssistant());
  const bar = document.querySelector<HTMLElement>('#assistant-sessions-bar');
  if (bar) bar.hidden = !isSheetAssistant();
  const generate = document.querySelector<HTMLInputElement>('#task-generate');
  if (!generate) return;
  generate.disabled = surface === 'pdf';
  if (surface === 'pdf') $<HTMLInputElement>('#task-explain').checked = true;
  if (isSheetAssistant()) {
    $<HTMLTextAreaElement>('#intent').placeholder = '默认按整张表的含义回答；提到所选单元格时才看当前选区';
    return;
  }
  $<HTMLTextAreaElement>('#intent').placeholder = taskMode() === 'generate'
    ? '请描述要生成的内容'
    : '请描述如何解释或处理选区';
}

function resetAssistantThread(clearAnswer = true) {
  assistantThread = null;
  sessionSummary = '';
  pending = null;
  $('#patch-actions').hidden = true;
  $('#patch-preview').replaceChildren();
  document.querySelector('.assistant-stale-hint')?.remove();
  if (clearAnswer) {
    $('#answer').replaceChildren();
    $('#result-meta').textContent = '';
  }
}

function loadSessionThread(session: AssistantSession) {
  sessionSummary = session.summary || '';
  const turns = (session.turns || []).map(turn => ({ role: turn.role, content: turn.content }));
  let anchor = session.anchor?.document_id ? session.anchor as Context : assistantThread?.anchor ?? null;
  if (!anchor?.document_id && editors && isSheetAssistant()) {
    try { anchor = sheetAssistantContext(editors); } catch { /* 表格未就绪 */ }
  }
  if (!turns.length && !anchor?.document_id) {
    assistantThread = null;
  } else {
    assistantThread = {
      anchor: anchor as Context,
      turns,
    };
  }
  renderAssistantThread();
  updateStaleHint();
}

function updateStaleHint() {
  document.querySelector('.assistant-stale-hint')?.remove();
  if (!isSheetAssistant() || !editors?.sheet.cacheStale?.()) return;
  const hint = document.createElement('p');
  hint.className = 'assistant-stale-hint';
  hint.textContent = '表格已修改或表含义已过期。可点「刷新表结构」重新生成。';
  $('#answer').prepend(hint);
}

function formatResultMeta(base: string, trimmed?: { dropped_turns?: number; surrounding_truncated?: boolean }) {
  const parts = [base].filter(Boolean);
  if (trimmed?.dropped_turns) parts.push(`已省略较早 ${trimmed.dropped_turns} 轮对话`);
  if (trimmed?.surrounding_truncated) parts.push('表结构已截断');
  return parts.join(' · ');
}

function clipTurn(text: string, limit = 24000) {
  const cleaned = stripThinking(text);
  return cleaned.length > limit ? cleaned.slice(0, limit) : cleaned;
}

function stripThinking(raw: string) {
  let text = raw.replace(/<think>[\s\S]*?<\/think>/gi, '');
  const open = text.search(/<think>/i);
  if (open >= 0 && !/<\/think>/i.test(text.slice(open))) text = text.slice(0, open);
  const think = /^##\s*思考过程\s*$/m.exec(text);
  const answer = /^##\s*回答\s*$/m.exec(text);
  if (think && answer && think.index < answer.index) {
    text = text.slice(answer.index + answer[0].length).replace(/^\n+/, '');
  } else if (think && !answer) {
    text = text.slice(0, think.index);
  }
  return text.trim();
}

async function persistAssistantSession() {
  if (!sessionController || !assistantThread || !workspace?.selected.sheet) return;
  await sessionController.save({
    anchor: assistantThread.anchor,
    turns: assistantThread.turns.slice(-40).map(turn => ({
      role: turn.role,
      content: clipTurn(turn.content),
    })),
    summary: sessionSummary,
  });
}

function setAssistantBusy(busy: boolean, hint = '') {
  const meta = $('#result-meta');
  if (busy && isSheetAssistant()) {
    if (meta.textContent === '正在思考…') meta.textContent = '';
    return;
  }
  if (busy) {
    meta.textContent = hint || '正在思考…';
  } else if (meta.textContent === '正在思考…') {
    meta.textContent = '';
  }
}

function normalizeAssistantAnswer(raw: string) {
  let text = stripThinking(raw);
  if (!text.startsWith('{')) return text;
  try {
    const parsed = JSON.parse(text) as { answer?: unknown };
    if (typeof parsed.answer === 'string') return stripThinking(parsed.answer);
  } catch { /* 流式片段或非 JSON */ }
  return text;
}

function fillTurnBody(el: HTMLElement, role: 'user' | 'assistant', text: string) {
  const content = role === 'assistant' ? normalizeAssistantAnswer(text) : text;
  if (!content.trim()) {
    el.textContent = '';
    return;
  }
  if (role === 'assistant') {
    el.className = 'assistant-turn-body assistant-md';
    el.innerHTML = markdownToHtml(content);
  } else {
    el.className = 'assistant-turn-body';
    el.textContent = content;
  }
}

function renderAssistantPlainAnswer(text: string) {
  const box = $('#answer');
  box.replaceChildren();
  if (!text.trim()) return;
  const body = document.createElement('div');
  body.className = 'assistant-turn-body assistant-md';
  body.innerHTML = markdownToHtml(normalizeAssistantAnswer(text));
  box.append(body);
}

function appendThinking(wrap: HTMLElement, text: string, opts: { live: boolean; open: boolean; boundToLive: boolean }) {
  const block = document.createElement('div');
  block.className = 'assistant-thinking';
  block.dataset.live = opts.live ? 'true' : 'false';
  block.dataset.open = opts.open ? 'true' : 'false';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'assistant-thinking-toggle';
  const chevron = document.createElement('span');
  chevron.className = 'assistant-thinking-chevron';
  chevron.textContent = '›';
  const dot = document.createElement('span');
  dot.className = 'assistant-thinking-dot';
  const label = document.createElement('span');
  label.textContent = opts.live ? 'Thinking' : 'Thought';
  toggle.append(chevron, dot, label);
  const body = document.createElement('div');
  body.className = 'assistant-thinking-body';
  body.textContent = text;
  toggle.onclick = () => {
    const open = block.dataset.open !== 'true';
    block.dataset.open = open ? 'true' : 'false';
    if (opts.boundToLive && liveAssistant) liveAssistant.thinkingOpen = open;
    if (open) body.scrollTop = body.scrollHeight;
  };
  block.append(toggle);
  if (text.trim()) block.append(body);
  wrap.append(block);
  if (opts.open && text.trim()) body.scrollTop = body.scrollHeight;
}

function renderAssistantThread(streaming = '', streamingRole: 'user' | 'assistant' = 'assistant') {
  const box = $('#answer');
  const staleHint = box.querySelector('.assistant-stale-hint');
  box.replaceChildren();
  if (staleHint) box.append(staleHint);
  const appendTurn = (
    role: 'user' | 'assistant',
    text: string,
    live = false,
    thinking = '',
    thinkingOpen = false,
  ) => {
    const content = role === 'assistant' ? normalizeAssistantAnswer(text) : text;
    const showThinking = role === 'assistant' && (thinking.trim() || (live && !content.trim()));
    if (!content.trim() && !showThinking) return;
    const wrap = document.createElement('div');
    wrap.className = 'assistant-turn';
    const label = document.createElement('p');
    label.className = 'assistant-turn-label';
    label.textContent = role === 'user' ? '你' : '助手';
    wrap.append(label);
    if (showThinking) appendThinking(wrap, thinking, { live: live && !content.trim(), open: thinkingOpen, boundToLive: live });
    if (content.trim()) {
      const body = document.createElement('div');
      fillTurnBody(body, role, text);
      if (live) body.dataset.live = 'true';
      wrap.append(body);
    }
    box.append(wrap);
  };
  for (const turn of assistantThread?.turns || []) {
    appendTurn(turn.role, turn.content, false, turn.thinking || '', false);
  }
  if (liveAssistant) {
    appendTurn('assistant', liveAssistant.answer, true, liveAssistant.thinking, liveAssistant.thinkingOpen);
  } else if (streaming.trim()) {
    appendTurn(streamingRole, streaming, true);
  }
}

function sheetAssistantContext(editorsRef: Record<Surface, EditorAdapter>): Context {
  const asked = editorsRef.sheet.askContext?.(true);
  if (asked) return asked;
  const resolved = editorsRef.sheet.resolveContext?.();
  if (!resolved) throw new Error('表格尚未就绪。');
  return resolved;
}

function refresh() {
  if (!ready || !editors) return;
  try {
    lastContext = editors[surface].context();
    if (isSheetAssistant()) updateStaleHint();
    console.log('[office] 当前选区', lastContext?.selection ?? null);
    const run = document.querySelector<HTMLButtonElement>('#run');
    if (run) run.disabled = running;
  } catch (e) {
    fail(e);
    const run = document.querySelector<HTMLButtonElement>('#run');
    if (run) run.disabled = running;
  }
}

function footer() {
  const current = workspace?.selected[surface];
  $('#footer-surface').textContent = current ? `${workspace!.project.name} · ${current.name}` : workspace?.project.name || '工作区';
  $('#footer-user').textContent = workspace?.user.email || '本地工作区';
}

function formatSavedAt(at = new Date()) {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`;
}

function noteSaved(text?: string) {
  const el = document.querySelector<HTMLElement>('#save-status');
  if (!el) return;
  el.textContent = text ?? `已保存 ${formatSavedAt()}`;
  el.hidden = false;
  el.classList.toggle('is-error', Boolean(text && text.includes('失败')));
}

window.addEventListener('office-saved', () => noteSaved());

window.addEventListener('office-save-error', e => {
  const detail = String((e as CustomEvent).detail || '无法写入工作区服务。');
  noteSaved('保存失败');
  void alertSaveError(detail).then(async action => {
    if (action !== 'retry') return;
    noteSaved('正在重试…');
    await retrySave();
  });
});

window.addEventListener('beforeunload', e => {
  if (!hasUnsavedWork()) return;
  e.preventDefault();
  e.returnValue = '';
});

async function saveNow() {
  if (!ready || !workspace || !editors) return;
  const current = workspace.selected[surface];
  if (!current) return;
  try {
    const persist = editors[surface].persist;
    if (persist) await persist();
    else {
      const content = editors[surface].snapshot?.();
      if (content !== undefined) queueSave(current.id, content);
      await flushSave();
    }
    const box = document.querySelector<HTMLElement>('#error');
    if (box) box.hidden = true;
    noteSaved();
  } catch (e) {
    if (e instanceof AuthError) { logout(); void boot(); return; }
    fail(e);
  }
}

window.addEventListener('keydown', e => {
  if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 's' || e.altKey) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.repeat) return;
  void saveNow();
}, true);

function run(action: () => Promise<void>) {
  return action().catch(e => {
    if (e instanceof AuthError) { logout(); void boot(); return; }
    fail(e);
  });
}

function paintNav() {
  if (!workspace) return;
  renderNav(workspace, surface, {
    onSurface: kind => { void switchSurface(kind); },
    onSelectAsset: (kind, id) => { void run(() => chooseAsset(kind, id)); },
    onNewAsset: kind => { void run(() => addAsset(kind)); },
    onRenameAsset: (kind, id) => { void run(() => editAsset(kind, id)); },
    onDeletePdf: async id => {
      try { return await deletePdf(id); }
      catch (e) { fail(e); return false; }
    },
    onSelectProject: id => { void run(() => chooseProject(id)); },
    onNewProject: () => { void run(() => addProject()); },
    onRenameProject: id => { void run(() => editProject(id)); },
    onLogout: () => { void flushSave().catch(() => {}).finally(() => { logout(); void boot(); }); },
  });
}

async function switchSurface(kind: Kind) {
  if (kind !== surface) resetAssistantThread();
  surface = kind;
  persist('office-surface', kind);
  document.querySelectorAll<HTMLElement>('.surface').forEach(el => { el.hidden = el.id !== surface; });
  paintNav(); footer();
  syncTaskModes();
  if (kind === 'sheet' && sessionController && workspace?.selected.sheet) {
    await sessionController.refreshList();
    await restoreLastSession(workspace.selected.sheet.id, sessionController);
  }
  window.dispatchEvent(new Event('resize'));
  refresh();
}

async function loadEditor(kind: Kind) {
  const meta = workspace?.selected[kind];
  if (!editors || !meta) {
    if (kind === 'pdf' && editors) await editors.pdf.load({ id: '', name: '', revision: 0 });
    return;
  }
  rememberRevision(meta.id, meta.revision);
  const hasBody = Boolean(meta.content && (
    meta.content.sheets || meta.content.sheetOrder || meta.content.nodeData || meta.content.type === 'doc'
  ));
  const asset = kind === 'pdf' ? meta : (hasBody ? meta : await getAsset(meta.id));
  if (workspace) workspace.selected[kind] = asset;
  await editors[kind].load(asset);
}

async function chooseAsset(kind: Kind, id: string) {
  if (!workspace || !editors) return;
  const { asset } = await openAsset(id);
  resetAssistantThread();
  workspace.selected[kind] = asset;
  workspace.assets[kind] = workspace.assets[kind].map(item => item.id === asset.id ? asset : item);
  await editors[kind].load(asset);
  await switchSurface(kind);
}

async function addAsset(kind: Kind) {
  if (!workspace) return;
  if (kind === 'pdf') {
    $('#pdf input[type="file"]').click();
    return;
  }
  if (kind === 'calendar') {
    const created = await askCalendarCreate('日常工作');
    if (!created) return;
    const asset = await createAsset(workspace.project.id, kind, created.name, { scope: created.scope });
    workspace.assets[kind] = [asset, ...workspace.assets[kind]];
    workspace.selected[kind] = asset;
    paintNav();
    footer();
    await editors![kind].load(asset);
    await switchSurface(kind);
    return;
  }
  const name = await askText(
    kind === 'sheet' ? '新建表格' : kind === 'document' ? '新建文档' : '新建导图',
    kind === 'sheet' ? '表格名称' : kind === 'document' ? '文档名称' : '导图名称',
  );
  if (!name) return;
  const asset = await createAsset(workspace.project.id, kind, name);
  workspace.assets[kind] = [asset, ...workspace.assets[kind]];
  workspace.selected[kind] = asset;
  paintNav();
  footer();
  await editors![kind].load(asset);
  await switchSurface(kind);
}

async function chooseProject(id: string) {
  workspace = await selectProject(id);
  paintNav();
  await Promise.all(ALL_KINDS.map(loadEditor));
  footer(); refresh();
}

async function addProject() {
  const name = await askText('新建项目', '项目名称');
  if (!name) return;
  workspace = await createProject(name);
  paintNav();
  await Promise.all(ALL_KINDS.map(loadEditor));
  footer(); refresh();
}

async function deletePdf(id: string) {
  if (!workspace || !editors) return false;
  const current = workspace.assets.pdf.find(item => item.id === id);
  if (!current) return false;
  const ok = await askConfirm(`删除「${current.name}」？删除后无法恢复。`);
  if (!ok) return false;
  await office.json(`/assets/${id}`, { method: 'DELETE' });
  const folders = loadPdfFolders(workspace.project.id);
  placePdf(folders, id, null);
  savePdfFolders(workspace.project.id, folders);
  workspace.assets.pdf = workspace.assets.pdf.filter(item => item.id !== id);
  if (workspace.selected.pdf?.id === id) {
    const next = workspace.assets.pdf[0] || null;
    workspace.selected.pdf = next;
    if (next) {
      await office.json(`/assets/${next.id}/select`, { method: 'POST' });
      await editors.pdf.load(next);
    } else {
      await editors.pdf.load({ id: '', name: '', revision: 0 });
    }
  }
  footer();
  return true;
}

async function editAsset(kind: Kind, id: string) {
  if (!workspace) return;
  const current = workspace.assets[kind].find(item => item.id === id);
  if (!current) return;
  const name = await askText('重命名', '名称', current.name);
  if (!name || name === current.name) return;
  const updated = await renameAsset(id, name);
  workspace.assets[kind] = workspace.assets[kind].map(item => item.id === id ? { ...item, ...updated } : item);
  if (workspace.selected[kind]?.id === id) workspace.selected[kind] = { ...workspace.selected[kind]!, ...updated };
  paintNav();
  footer();
}

async function editProject(id: string) {
  if (!workspace) return;
  const current = workspace.projects.find(item => item.id === id);
  if (!current) return;
  const name = await askText('重命名项目', '项目名称', current.name);
  if (!name || name === current.name) return;
  const updated = await renameProject(id, name);
  workspace.projects = workspace.projects.map(item => item.id === id ? { ...item, ...updated } : item);
  if (workspace.project.id === id) workspace.project = { ...workspace.project, ...updated };
  paintNav();
  footer();
}

async function handlePdfUpload(file: File) {
  if (!workspace || !editors) return;
  const asset = await uploadPdf(workspace.project.id, file);
  workspace.assets.pdf = [asset, ...workspace.assets.pdf.filter(item => item.id !== asset.id)];
  workspace.selected.pdf = asset;
  await editors.pdf.load(asset);
  await switchSurface('pdf');
}

async function savePdfAnnot(path: string, init: RequestInit) {
  if (!workspace?.selected.pdf) return;
  const uploaded = await office.json<Asset & { annot?: { xref: number; mark_xref?: number }; deleted?: number[] }>(path, init);
  const { annot, deleted, ...asset } = uploaded;
  rememberRevision(asset.id, asset.revision);
  workspace.selected.pdf = { ...workspace.selected.pdf, ...asset };
  workspace.assets.pdf = workspace.assets.pdf.map(item => item.id === asset.id ? { ...item, ...asset } : item);
  window.dispatchEvent(new CustomEvent('office-saved'));
  return { annot, deleted };
}

function migrateLegacy(_ws: Workspace) {
  if (localStorage.getItem('office-migrated')) return;
  localStorage.removeItem('office-document');
  localStorage.removeItem('office-sheet');
  localStorage.setItem('office-migrated', '1');
}

function bindAssistantResize() {
  const split = document.querySelector<HTMLElement>('#workspace-split');
  const main = document.querySelector<HTMLElement>('#app > main');
  const app = document.querySelector<HTMLElement>('#app');
  if (!split || !main || !app) return;
  const key = 'office-assistant-width';
  const collapsedKey = 'office-assistant-collapsed';
  const minAside = 240;
  const minWorkspace = 360;
  const fallback = 348;
  const collapsed = () => app.classList.contains('assistant-collapsed');
  const clamp = (width: number) => {
    const max = Math.max(minAside, main.clientWidth - minWorkspace - split.clientWidth);
    return Math.min(max, Math.max(minAside, Math.round(width)));
  };
  const apply = (width: number, save = false, notify = true) => {
    const value = clamp(width);
    document.documentElement.style.setProperty('--assistant-width', `${value}px`);
    split.setAttribute('aria-valuenow', String(value));
    if (save) localStorage.setItem(key, String(value));
    if (notify) window.dispatchEvent(new Event('resize'));
    return value;
  };
  const current = () => Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue('--assistant-width'), 10) || fallback;
  const setCollapsed = (hide: boolean) => {
    app.classList.toggle('assistant-collapsed', hide);
    localStorage.setItem(collapsedKey, hide ? '1' : '0');
    split.setAttribute('aria-expanded', String(!hide));
    split.setAttribute('aria-label', hide ? '显示协作助手' : '隐藏或调整协作助手');
    window.dispatchEvent(new Event('resize'));
  };
  const saved = Number(localStorage.getItem(key));
  apply(Number.isFinite(saved) && saved > 0 ? saved : fallback, false, false);
  setCollapsed(localStorage.getItem(collapsedKey) === '1');
  split.setAttribute('aria-valuemin', String(minAside));
  let dragging = false;
  split.addEventListener('pointerdown', event => {
    if (event.button !== 0 || collapsed()) return;
    event.preventDefault();
    dragging = true;
    document.body.classList.add('resizing-assistant');
    try { split.setPointerCapture(event.pointerId); } catch { /* ignore */ }
  });
  const onMove = (event: PointerEvent) => {
    if (!dragging) return;
    apply(main.getBoundingClientRect().right - event.clientX, true);
  };
  split.addEventListener('pointermove', onMove);
  window.addEventListener('pointermove', onMove);
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing-assistant');
  };
  split.addEventListener('pointerup', stop);
  split.addEventListener('pointercancel', stop);
  window.addEventListener('pointerup', stop);
  split.addEventListener('dblclick', () => setCollapsed(!collapsed()));
  split.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setCollapsed(!collapsed());
      return;
    }
    if (collapsed()) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); apply(current() + 16, true); }
    if (event.key === 'ArrowRight') { event.preventDefault(); apply(current() - 16, true); }
    if (event.key === 'Home') { event.preventDefault(); apply(minAside, true); }
    if (event.key === 'End') { event.preventDefault(); apply(main.clientWidth - minWorkspace, true); }
  });
  window.addEventListener('resize', () => {
    if (!collapsed()) apply(current(), false, false);
  });
}

function bindWorkspace() {
  ready = true;
  bindAssistantResize();
  editors = {
    sheet: mountSheet($('#sheet'), refresh, content => { if (workspace?.selected.sheet) queueSave(workspace.selected.sheet.id, content); }),
    document: mountDocument(
      $('#rich-editor'),
      refresh,
      content => { if (workspace?.selected.document) queueSave(workspace.selected.document.id, content); },
      {
        getJob: id => office.json(`/assets/${id}/digest-job`),
        saveJob: (id, job) => office.json(`/assets/${id}/digest-job`, {
          method: 'PUT',
          body: JSON.stringify({
            enabled: job.enabled,
            hour: job.hour,
            minute: job.minute,
            output_order: job.output_order,
            topics: job.topics,
          }),
        }),
        runJob: id => office.json(`/assets/${id}/digest-job/run`, { method: 'POST', body: '{}' }),
        reloadDocument: async id => {
          const asset = await getAsset(id);
          if (workspace) {
            workspace.selected.document = { ...workspace.selected.document!, ...asset };
            rememberRevision(asset.id, asset.revision);
          }
          editors?.document.load(asset);
          noteSaved('资讯已写入文档');
        },
      },
    ),
    pdf: mountPdf($('#pdf'), refresh, {
      onPickFile: handlePdfUpload,
      fetchFile: id => office.file(id),
      onAnnotate: async body => {
        const result = await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations`, { method: 'POST', body: JSON.stringify(body) });
        return result?.annot;
      },
      onUpdateAnnot: async (xref, content) => { await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations/${xref}`, { method: 'PATCH', body: JSON.stringify({ content }) }); },
      onDeleteAnnot: async xref => {
        const result = await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations/${xref}`, { method: 'DELETE' });
        return { deleted: result?.deleted };
      },
      userName: () => workspace?.user.email || '用户',
      modelName: () => {
        const label = $('#model-name')?.textContent || '';
        return label.replace(/^模型：/, '').trim() || '模型';
      },
      askAi: (body, onEvent) => office.stream('/assistant/pdf-ask', body, onEvent),
    }),
    mindmap: mountMindmap($('#mindmap'), refresh, (id, content) => {
      queueSave(id, content);
    }),
    calendar: mountCalendar($('#calendar'), refresh, content => {
      if (workspace?.selected.calendar) queueSave(workspace.selected.calendar.id, content);
    }),
  };
  document.querySelector<HTMLButtonElement>('[data-pdf-terms]')?.addEventListener('click', () => {
    const projectId = workspace?.selected.pdf?.project_id || workspace?.user.last_project_id;
    if (!projectId) return;
    openTermsDialog({
      load: async () => (await office.json<{ terms: { en: string; zh: string; forbidden: string[] }[] }>(`/projects/${projectId}/terms`)).terms,
      save: term => office.json(`/projects/${projectId}/terms`, { method: 'POST', body: JSON.stringify(term) }),
      remove: en => office.json(`/projects/${projectId}/terms?en=${encodeURIComponent(en)}`, { method: 'DELETE' }),
    });
  });
  sessionController = mountAssistantSessions(
    document.querySelector<HTMLElement>('#assistant-sessions-bar')!,
    () => workspace?.selected.sheet?.id || null,
    {
      onLoad: loadSessionThread,
      onNew: () => resetAssistantThread(),
      onRefreshAnchor: async () => {
        await editors!.sheet.syncSheetCache?.({ regenerateProfile: true });
        updateStaleHint();
        return sheetAssistantContext(editors!);
      },
      currentTurns: () => assistantThread?.turns || [],
      isStreaming: () => running,
    },
  );
  window.addEventListener('office-session-anchor-refreshed', e => {
    const anchor = (e as CustomEvent<Context>).detail;
    if (!assistantThread) assistantThread = { anchor, turns: [] };
    else assistantThread = { ...assistantThread, anchor };
    updateStaleHint();
  });
  document.querySelectorAll<HTMLInputElement>('input[name="task-mode"]').forEach(input => {
    input.onchange = () => syncTaskModes();
  });
  $('#discard').onclick = () => { pending = null; $('#patch-preview').replaceChildren(); $('#patch-actions').hidden = true; };
  $('#new-assistant-thread').onclick = () => {
    void (async () => {
      try {
        if (sessionController && isSheetAssistant()) await sessionController.startNew();
        else resetAssistantThread();
      } catch (err) { fail(err); }
    })();
  };
  $<HTMLTextAreaElement>('#intent').addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing || e.shiftKey || e.altKey) return;
    if (!e.metaKey && !e.ctrlKey) return;
    e.preventDefault();
    if ($<HTMLButtonElement>('#run').disabled) return;
    $<HTMLFormElement>('#ask').requestSubmit();
  });
  $('#ask').onsubmit = async e => {
    e.preventDefault();
    if (!editors || !ready) {
      fail(new Error('工作区尚未就绪，请稍候再试。'));
      return;
    }
    if (running) {
      fail(new Error('上一个问题仍在处理中，请稍候。'));
      return;
    }
    $('#error').hidden = true;
    let streamStarted = false;
    let answer = '';
    try {
      const task = isSheetAssistant() ? 'explain' : taskMode();
      const intent = $<HTMLTextAreaElement>('#intent').value.trim();
      if (!intent) throw new Error('请输入任务描述。');
      let context: Context;
      let history: AssistantTurn[] = [];
      const sheetChat = isSheetAssistant();
      if (sheetChat && !sessionController?.currentId()) {
        await sessionController!.startNew({ resetUi: false });
      }
      if (sheetChat) {
        context = sheetAssistantContext(editors);
        if (assistantThread) {
          history = assistantThread.turns
            .filter(turn => turn.content.trim())
            .slice(-MAX_API_HISTORY)
            .map(turn => ({ ...turn, content: clipTurn(turn.content) }));
          assistantThread = { ...assistantThread, anchor: context };
        } else {
          assistantThread = { anchor: context, turns: [] };
        }
      } else {
        const snapshot = (task === 'generate' ? editors[surface].anchor() : editors[surface].context()) ?? lastContext;
        if (task === 'explain' && !hasTaskContent(snapshot)) throw new Error('未选择任务内容。');
        if (task === 'generate' && !snapshot) throw new Error('请先将光标放到要插入的位置。');
        if (!snapshot) throw new Error('未选择任务内容。');
        context = snapshot;
        resetAssistantThread(false);
      }
      running = true; refresh(); pending = null;
      $('#patch-actions').hidden = true; $('#patch-preview').replaceChildren();
      const followUp = Boolean(sheetChat && history.length);
      if (!followUp) $('#result-meta').textContent = '';
      if (sheetChat) {
        assistantThread!.turns.push({ role: 'user', content: intent });
        liveAssistant = { answer: '', thinking: '', thinkingOpen: true, answerStarted: false };
        renderAssistantThread();
      } else if (!followUp) {
        $('#answer').replaceChildren();
      }
      setAssistantBusy(true);
      const scroll = document.querySelector('.assistant-scroll');
      streamStarted = true;
      await office.stream('/assistant/run', {
        context,
        intent,
        task,
        summary: sheetChat ? sessionSummary : '',
        use_sheet_cache: sheetChat,
        history: history.map(turn => ({ role: turn.role, content: turn.content })),
      }, event => {
        if (event.type === 'delta' && event.text) {
          if (sheetChat && event.part === 'reasoning') {
            if (!liveAssistant) {
              liveAssistant = { answer: '', thinking: '', thinkingOpen: true, answerStarted: false };
            }
            liveAssistant.thinking += String(event.text);
            renderAssistantThread();
          } else {
            answer += event.text;
            if (sheetChat) {
              if (!liveAssistant) {
                liveAssistant = { answer: '', thinking: '', thinkingOpen: false, answerStarted: true };
              }
              if (!liveAssistant.answerStarted) {
                liveAssistant.answerStarted = true;
                liveAssistant.thinkingOpen = false;
              }
              liveAssistant.answer = answer;
              renderAssistantThread();
            } else {
              renderAssistantPlainAnswer(answer);
            }
          }
          scroll?.scrollTo({ top: scroll.scrollHeight });
        }
        if (event.type === 'done') {
          const thought = liveAssistant?.thinking.trim() || '';
          liveAssistant = null;
          const finalAnswer = normalizeAssistantAnswer(
            (typeof event.answer === 'string' && event.answer.trim()) ? event.answer : answer,
          );
          if (finalAnswer) {
            answer = finalAnswer;
            if (sheetChat) {
              const last = assistantThread!.turns.at(-1);
              const stored = clipTurn(finalAnswer);
              if (last?.role === 'assistant') {
                last.content = stored;
                if (thought) last.thinking = thought;
              } else {
                assistantThread!.turns.push({ role: 'assistant', content: stored, thinking: thought || undefined });
              }
              renderAssistantThread();
            } else {
              renderAssistantPlainAnswer(finalAnswer);
            }
          } else if (sheetChat) {
            liveAssistant = null;
            assistantThread!.turns.push({ role: 'assistant', content: '（模型未返回内容）', thinking: thought || undefined });
            renderAssistantThread();
          } else {
            renderAssistantPlainAnswer('（模型未返回内容）');
          }
          if (event.agent || event.context_trimmed) {
            $('#result-meta').textContent = formatResultMeta(
              typeof event.agent === 'string' ? event.agent : '',
              event.context_trimmed as { dropped_turns?: number; surrounding_truncated?: boolean } | undefined,
            );
          } else if (!sheetChat) {
            setAssistantBusy(false);
          }
          if (sheetChat) {
            void persistAssistantSession().then(() => updateStaleHint()).catch(fail);
          }
          if (event.patch) {
            pending = { patch: event.patch, context };
            renderPatch($('#patch-preview'), event.patch);
            $('#patch-actions').hidden = false;
          }
          scroll?.scrollTo({ top: scroll.scrollHeight });
        }
      });
      $<HTMLTextAreaElement>('#intent').value = '';
    } catch (e) {
      if (!streamStarted && assistantThread?.turns.at(-1)?.role === 'user') {
        assistantThread.turns.pop();
        if (isSheetAssistant()) renderAssistantThread();
      } else if (streamStarted && isSheetAssistant() && assistantThread?.turns.at(-1)?.role === 'user') {
        const streamed = answer.trim();
        const thought = liveAssistant?.thinking.trim() || '';
        liveAssistant = null;
        assistantThread.turns.push({
          role: 'assistant',
          content: streamed || `请求失败：${e instanceof Error ? e.message : String(e)}`,
          thinking: thought || undefined,
        });
        renderAssistantThread();
        if (streamed) void persistAssistantSession().catch(() => {});
      }
      if (!(streamStarted && answer.trim())) fail(e);
    }
    finally {
      running = false;
      liveAssistant = null;
      setAssistantBusy(false);
      refresh();
    }
  };
  $('#apply').onclick = async () => {
    if (!pending || !editors) return;
    const proposal = pending;
    $<HTMLButtonElement>('#apply').disabled = true;
    try {
      if (surface !== proposal.patch.surface) throw new Error('请切回生成建议时的工作区。');
    const active = editors;
    const read = () => (proposal.patch.task === 'generate' ? active[surface].anchor() : active[surface].context()) ?? lastContext;
      const current = read();
      if (!current) throw new Error('选区已改变，请重新选择并生成建议。');
      await office.json('/assistant/validate', { method: 'POST', body: JSON.stringify({ context: current, patch: proposal.patch }) });
      const latest = read();
      if (JSON.stringify(latest) !== JSON.stringify(current)) throw new Error('等待期间文档已改变，请重新生成建议。');
      editors[surface].apply(proposal.patch);
      pending = null; $('#patch-actions').hidden = true;
      if (isSheetAssistant() && assistantThread) {
        assistantThread.turns.push({ role: 'assistant', content: '已接受修改。' });
        renderAssistantThread();
      } else {
        const box = $('#answer');
        const note = document.createElement('div');
        note.className = 'assistant-turn-body assistant-md';
        note.innerHTML = markdownToHtml('已接受修改。');
        box.append(note);
      }
      $('#error').hidden = true;
    } catch (e) { fail(e); }
    finally { $<HTMLButtonElement>('#apply').disabled = false; refresh(); }
  };
  window.addEventListener('office-error', e => fail((e as CustomEvent).detail));
  office.json<{ model_name?: string }>('/assistant/status').then(status => {
    $('#model-name').textContent = `模型：${status.model_name || '未配置'}`;
  }).catch(e => fail(e));
}

function bindAuth() {
  const form = $<HTMLFormElement>('#auth');
  const submit = async (mode: 'login' | 'register') => {
    if (!form.reportValidity()) return;
    $('#error').hidden = true;
    const email = $<HTMLInputElement>('#auth-email').value.trim();
    const password = $<HTMLInputElement>('#auth-password').value;
    try {
      if (mode === 'register') await register(email, password);
      else await login(email, password);
      await boot();
    } catch (e) { fail(e); }
  };
  form.onsubmit = e => { e.preventDefault(); void submit('login'); };
  $('#auth-register').onclick = () => { void submit('register'); };
}

async function boot() {
  ready = false;
  editors = null;
  workspace = null;
  pending = null;
  if (!office.signedIn()) {
    renderAuth(root);
    createIcons({ icons });
    bindAuth();
    return;
  }
  try {
    workspace = await bootstrap();
  } catch (e) {
    if (e instanceof AuthError) { logout(); renderAuth(root); createIcons({ icons }); bindAuth(); return; }
    renderAuth(root);
    createIcons({ icons });
    bindAuth();
    fail(e);
    return;
  }
  migrateLegacy(workspace);
  await flushSave().catch(() => {});
  renderChrome(root);
  createIcons({ icons });
  bindWorkspace();
  const last = saved('office-surface');
  await switchSurface(last === 'document' || last === 'pdf' || last === 'mindmap' || last === 'calendar' ? last : 'sheet');
  await Promise.all(ALL_KINDS.map(kind => loadEditor(kind).catch(fail)));
  if (sessionController && workspace?.selected.sheet) {
    await sessionController.refreshList();
    await restoreLastSession(workspace.selected.sheet.id, sessionController);
  }
  footer();
  refresh();
}

void boot().catch(fail);
