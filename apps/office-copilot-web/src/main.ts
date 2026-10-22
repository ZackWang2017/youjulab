import './polyfill';
import { createIcons, icons } from 'lucide';
import { mountSheet } from './editors/sheet';
import { mountDocument } from './editors/document';
import { mountPdf } from './editors/pdf';
import { openTermsDialog } from './editors/pdf/terms-dialog';
import { mountDiagram } from './editors/diagram';
import { mountMindmap } from './editors/mindmap';
import { mountResearchDiagram } from './editors/research';
import { mountCalendar } from './editors/calendar';
import { mountProjectPlan } from './editors/project-plan';
import { office, AuthError } from './office-client/client';
import {
  loadCaptcha, remoteBase, remoteEmail, remoteHealthy, remoteLogin, remoteRegister,
  remoteToken, setRemoteBase, setRemoteToken,
  RemoteAuthError, RemoteDownError,
} from './remote/client';
import { renderPatch } from './patches/preview';
import type { Context, EditorAdapter, Patch, Surface } from './context/types';
import { persist, saved } from './context/types';
import {
  bindLocalAccount, bootstrap, createAsset, createProject, flushSave, getAsset, hasUnsavedWork, logout, openAsset, queueSave,
  loadRecentAssets, rememberRevision, renameAsset, renameProject, reorderRecentAssets, retrySave, selectProject, updateAsset, uploadPdf,
  type Asset, type Kind, type Workspace,
} from './workspace/session';
import {
  alertSaveError, askConfirm, askDocumentCreate, askProjectPlanCreate, askScopedAssetEdit, askText,
  renderAuth, renderChrome, renderNav, type PortfolioNavState, type PortfolioNavView,
} from './workspace/nav';
import { mountPortfolio, type PortfolioController } from './portfolio';
import { ALL_PORTFOLIOS_ID, ALL_PORTFOLIOS_NAME, portfolioApi } from './portfolio/api';
import { openCalcWindow } from './workspace/calc-window';
import { openNotebookWindow, showNotebookLoadError, notebookContainsFocus, saveNotebookNow } from './workspace/notebook-window';
import { loadPdfFolders, placePdf, savePdfFolders } from './workspace/pdf-folders';
import {
  mountAssistantSessions, restoreLastSession, type AssistantSession, type SessionController,
} from './assistant-sessions';
import { markdownToHtml } from './editors/document/markdown';
import { appendThinkingBlock, mountThinkingStatus, type ThinkingStatus } from './ui/thinking-status';
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
/** 主界面模式：工作区（项目/资产）或持仓管理。 */
let appView: 'workspace' | 'portfolio' = 'workspace';
let portfolioController: PortfolioController | null = null;
const portfolioNav: PortfolioNavState = { active: false, items: [], currentId: null, view: 'holdings' };

window.addEventListener('wheel', event => {
  if (event.ctrlKey || event.metaKey) return;
  if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
  event.preventDefault();
}, { passive: false, capture: true });

const ALL_KINDS: Kind[] = ['sheet', 'document', 'pdf', 'mindmap', 'research', 'diagram', 'project_plan', 'calendar'];

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

function isStructuredAssistant() {
  return surface === 'sheet' || surface === 'mindmap';
}

function isSheetAssistant() {
  return surface === 'sheet';
}

function assistantContext(editorsRef: Record<Surface, EditorAdapter>): Context {
  if (surface === 'sheet') return sheetAssistantContext(editorsRef);
  const context = editorsRef.mindmap.anchor();
  if (!context) throw new Error('导图尚未就绪。');
  return context;
}

function syncTaskModes() {
  const app = document.querySelector<HTMLElement>('#app');
  app?.classList.toggle('sheet-assistant-mode', isStructuredAssistant());
  const bar = document.querySelector<HTMLElement>('#assistant-sessions-bar');
  if (bar) bar.hidden = !isStructuredAssistant();
  const generate = document.querySelector<HTMLInputElement>('#task-generate');
  if (!generate) return;
  generate.disabled = surface === 'pdf';
  if (surface === 'pdf') $<HTMLInputElement>('#task-explain').checked = true;
  if (isSheetAssistant()) {
    $<HTMLTextAreaElement>('#intent').placeholder = '默认按整张表的含义回答；提到所选单元格时才看当前选区';
    return;
  }
  if (surface === 'mindmap') {
    $<HTMLTextAreaElement>('#intent').placeholder = '粘贴内容后可让我新增、修改、删除节点，或整理父子关系';
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
    assistantStatus().clear();
  }
}

function loadSessionThread(session: AssistantSession) {
  sessionSummary = session.summary || '';
  const turns = (session.turns || []).map(turn => ({ role: turn.role, content: turn.content }));
  let anchor = session.anchor?.document_id ? session.anchor as Context : assistantThread?.anchor ?? null;
  if (!anchor?.document_id && editors && isStructuredAssistant()) {
    try { anchor = assistantContext(editors); } catch { /* 编辑器未就绪 */ }
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
  if (trimmed?.surrounding_truncated) parts.push('当前工作区结构已截断');
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
  if (!sessionController || !assistantThread || !workspace?.selected[surface]) return;
  await sessionController.save({
    anchor: assistantThread.anchor,
    turns: assistantThread.turns.slice(-40).map(turn => ({
      role: turn.role,
      content: clipTurn(turn.content),
    })),
    summary: sessionSummary,
  });
}

let panelThinking: ThinkingStatus | null = null;

function assistantStatus() {
  if (!panelThinking) panelThinking = mountThinkingStatus($('#result-meta'));
  return panelThinking;
}

function setAssistantBusy(busy: boolean) {
  if (busy && !isStructuredAssistant()) assistantStatus().begin();
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
  appendThinkingBlock(wrap, text, {
    live: opts.live,
    open: opts.open,
    onToggle: open => {
      if (opts.boundToLive && liveAssistant) liveAssistant.thinkingOpen = open;
    },
  });
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

function paintFooterUser() {
  const el = document.querySelector<HTMLElement>('#footer-user');
  if (!el) return;
  const email = remoteEmail();
  if (remoteToken() && email) {
    el.replaceChildren(document.createTextNode(email));
    return;
  }
  el.replaceChildren();
  el.append(document.createTextNode('本机<'));
  const link = document.createElement('button');
  link.type = 'button';
  link.className = 'footer-login-link';
  link.textContent = '未登录';
  link.onclick = () => openRemoteLogin();
  el.append(link, document.createTextNode('>'));
}

function footer() {
  if (appView === 'portfolio') {
    const name = portfolioNav.currentId === ALL_PORTFOLIOS_ID && portfolioNav.items.length
      ? ALL_PORTFOLIOS_NAME
      : portfolioNav.items.find(item => item.id === portfolioNav.currentId)?.name;
    $('#footer-surface').textContent = name ? `持仓 · ${name}` : '持仓管理';
    paintFooterUser();
    return;
  }
  const current = workspace?.selected[surface];
  const label = surface === 'calendar' && current
    ? current.name
    : current ? `${workspace!.project.name} · ${current.name}` : workspace?.project.name || '工作区';
  $('#footer-surface').textContent = label;
  paintFooterUser();
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
window.addEventListener('office-document-replaced', event => {
  const detail = (event as CustomEvent<{ id?: string; archived_name?: string }>).detail || {};
  if (!detail.id) return;
  void showWrittenDocument(detail.id, detail.archived_name ? `文档已归档为${detail.archived_name}` : '已换新文档');
});

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
  if (notebookContainsFocus()) {
    try {
      await saveNotebookNow();
      const box = document.querySelector<HTMLElement>('#error');
      if (box) box.hidden = true;
    } catch (e) {
      if (e instanceof AuthError) { void boot({ forceAuth: true }); return; }
      fail(e);
    }
    return;
  }
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
    if (e instanceof AuthError) { void boot({ forceAuth: true }); return; }
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
    if (e instanceof AuthError) { void boot({ forceAuth: true }); return; }
    fail(e);
  });
}

async function showWrittenDocument(id: string, note: string) {
  const asset = await getAsset(id);
  if (workspace) {
    const listed = await office.json<{ assets: Asset[] }>(`/projects/${workspace.project.id}/assets?kind=document`);
    workspace.assets.document = listed.assets;
    workspace.selected.document = asset;
    rememberRevision(asset.id, asset.revision);
    paintNav();
  }
  editors?.document.load(asset);
  noteSaved(note);
}

function paintNav() {
  if (!workspace) return;
  renderNav(workspace, surface, {
    onSurface: kind => { void switchSurface(kind); },
    onSelectAsset: (kind, id) => { void run(() => chooseAsset(kind, id)); },
    onSelectRecentAsset: id => { void run(() => chooseRecentAsset(id)); },
    onReorderRecentAssets: ids => { void run(async () => {
      if (!workspace) return;
      workspace.recentAssets = await reorderRecentAssets(workspace.project.id, ids);
      paintNav();
    }); },
    onNewAsset: kind => { void run(() => addAsset(kind)); },
    onRenameAsset: (kind, id) => { void run(() => editAsset(kind, id)); },
    onDeletePdf: async id => {
      try { return await deletePdf(id); }
      catch (e) { fail(e); return false; }
    },
    onDeleteAsset: (kind, id) => { void run(() => deleteAsset(kind, id)); },
    onSelectProject: id => { void run(() => chooseProject(id)); },
    onNewProject: () => { void run(() => addProject()); },
    onRenameProject: id => { void run(() => editProject(id)); },
    onLogout: () => { void flushSave().catch(() => {}).finally(() => { setRemoteToken(null); logout(); void boot(); }); },
    onLogin: () => { openRemoteLogin(); },
    onOpenCalc: () => {
      const calc = workspace?.calc;
      openCalcWindow(calc, calc ? content => queueSave(calc.id, content) : undefined);
    },
    onOpenNotebook: () => {
      void run(async () => {
        if (!workspace) return;
        let notebook = workspace.notebook;
        if (!notebook) {
          openNotebookWindow({ id: '', revision: 0 });
          try {
            const response = await office.json<{ assets: Asset[] }>(
              `/projects/${workspace.project.id}/assets?kind=notebook`,
            );
            let listed = response.assets.reduce<Asset | undefined>((best, item) => (
              !best || (item.revision || 0) > (best.revision || 0) ? item : best
            ), undefined);
            if (!listed) {
              listed = await office.json<Asset>(`/projects/${workspace.project.id}/assets`, {
                method: 'POST',
                body: JSON.stringify({ kind: 'notebook', name: '笔记本', visibility: 'user' }),
              });
            }
            notebook = await getAsset(listed.id);
            workspace.notebook = notebook;
          } catch (error) {
            showNotebookLoadError(error instanceof Error ? error.message : String(error));
            throw error;
          }
        }
        openNotebookWindow(notebook);
      });
    },
    onOpenPortfolio: () => { void run(() => enterPortfolio()); },
    onClosePortfolio: () => { if (appView === 'portfolio') void run(() => switchSurface(surface)); },
    onSelectPortfolio: id => { void run(() => showPortfolio(id, portfolioNav.view)); },
    onPortfolioView: view => { void run(() => showPortfolio(portfolioNav.currentId, view)); },
    onNewPortfolio: () => { void run(async () => {
      const name = await askText('新建投资组合', '组合名称，如：A股核心、港股打新');
      if (!name) return;
      const created = await portfolioApi.create(name);
      portfolioNav.items = await portfolioApi.list();
      await showPortfolio(created.id, 'holdings');
    }); },
    onRenamePortfolio: id => { void run(async () => {
      const item = portfolioNav.items.find(p => p.id === id);
      if (!item) return;
      const name = await askText('重命名投资组合', '组合名称', item.name);
      if (!name || name === item.name) return;
      await portfolioApi.rename(id, name);
      portfolioNav.items = await portfolioApi.list();
      paintNav(); footer();
    }); },
    onDeletePortfolio: id => { void run(async () => {
      const item = portfolioNav.items.find(p => p.id === id);
      if (!item) return;
      if (!await askConfirm(`删除投资组合「${item.name}」？其下所有持仓、流水与净值记录都会一并删除。`)) return;
      await portfolioApi.remove(id);
      portfolioNav.items = await portfolioApi.list();
      // 删掉当前组合则切到第一个；若删光了，「全部持仓统计」也不再有意义
      const next = portfolioNav.currentId === id || !portfolioNav.items.length ? (portfolioNav.items[0]?.id ?? null) : portfolioNav.currentId;
      await showPortfolio(next, portfolioNav.view);
    }); },
  }, { ...portfolioNav, active: appView === 'portfolio' });
}

/** 在当前工作区上弹出登录窗。登录成功后会整页重载资产；afterLogin 在重载后再跑（如进入持仓）。 */
function openRemoteLogin(afterLogin?: () => Promise<void>) {
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = '<div id="remote-login-host"></div>';
  overlay.addEventListener('click', event => { if (event.target === overlay) overlay.remove(); });
  document.body.append(overlay);
  renderAuth(overlay.querySelector('#remote-login-host')!);
  createIcons({ icons });
  bindAuth(afterLogin);
}

/** 进入持仓管理：隐藏资产编辑器，顶部导航切换为投资组合下拉与功能链接。 */
async function enterPortfolio() {
  closeAllMenus();
  if (!(await remoteHealthy())) {
    fail(new RemoteDownError('服务器不可用。'));
    return;
  }
  let items;
  try {
    items = await portfolioApi.list();
  } catch (e) {
    if (e instanceof RemoteAuthError) {
      openRemoteLogin(async () => { await enterPortfolio(); });
      return;
    }
    throw e;
  }
  portfolioNav.items = items;
  appView = 'portfolio';
  persist('office-view', 'portfolio');
  document.querySelectorAll<HTMLElement>('.surface').forEach(el => { el.hidden = el.id !== 'portfolio'; });
  if (!portfolioController) portfolioController = mountPortfolio($('#portfolio'), fail);
  const remembered = Number(saved('office-portfolio-id'));
  // 「全部持仓统计」只要还有组合就有效；否则回退到记住的 / 第一个组合
  const valid = (id: number | null) => id !== null && portfolioNav.items.length > 0
    && (id === ALL_PORTFOLIOS_ID || portfolioNav.items.some(item => item.id === id));
  const currentId = valid(portfolioNav.currentId) ? portfolioNav.currentId
    : valid(remembered) ? remembered
    : (portfolioNav.items[0]?.id ?? null);
  const view = saved('office-portfolio-view');
  await showPortfolio(currentId, view === 'stats' ? 'stats' : portfolioNav.view);
}

async function showPortfolio(id: number | null, view: PortfolioNavView) {
  portfolioNav.currentId = id;
  portfolioNav.view = view;
  persist('office-portfolio-id', id);
  persist('office-portfolio-view', view);
  paintNav(); footer();
  window.dispatchEvent(new Event('resize'));
  await portfolioController?.show(id, view);
}

function closeAllMenus() {
  document.querySelectorAll<HTMLElement>('.menu').forEach(menu => { menu.hidden = true; });
}

async function switchSurface(kind: Kind) {
  if (appView === 'portfolio') {
    appView = 'workspace';
    persist('office-view', 'workspace');
  }
  if (kind !== surface) resetAssistantThread();
  surface = kind;
  persist('office-surface', kind);
  const surfaceId = surface === 'project_plan' ? 'project-plan' : surface;
  document.querySelectorAll<HTMLElement>('.surface').forEach(el => { el.hidden = el.id !== surfaceId; });
  paintNav(); footer();
  syncTaskModes();
  // 等布局生效后再 resize，Univer 才能按真实尺寸重绘（配合 #sheet 隐藏时仍保留渲染）
  requestAnimationFrame(() => {
    window.dispatchEvent(new Event('resize'));
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
  });
  if ((kind === 'sheet' || kind === 'mindmap') && sessionController && workspace?.selected[kind]) {
    await sessionController.refreshList();
    await restoreLastSession(workspace.selected[kind]!.id, sessionController);
  }
  refresh();
}

async function loadEditor(kind: Kind) {
  const meta = workspace?.selected[kind];
  if (!editors || !meta) {
    if (kind === 'pdf' && editors) await editors.pdf.load({ id: '', name: '', revision: 0 });
    if (kind === 'project_plan' && editors) {
      await editors.project_plan.load({
        id: '', name: '', revision: 0,
        content: { format: 'invp-project-plan-v1', tasks: [] },
      });
    }
    return;
  }
  rememberRevision(meta.id, meta.revision);
  const hasBody = Boolean(meta.content && (
    meta.content.sheets || meta.content.sheetOrder || meta.content.nodeData || meta.content.type === 'doc' || meta.content.format === 'drawio-v1' || meta.content.format === 'mermaid-v1' || meta.content.format === 'invp-research-diagram-v1' || meta.content.format === 'invp-project-plan-v1'
  )) || (kind === 'pdf' && 'content' in meta);
  // Asset lists omit content; PDF notes live in content too, so always hydrate before loading.
  const asset = hasBody ? meta : await getAsset(meta.id);
  if (workspace) workspace.selected[kind] = asset;
  await editors[kind].load(asset);
}

async function chooseAsset(kind: Kind, id: string, trackRecent = true) {
  if (!workspace || !editors) return;
  // 已是当前打开的文件：只切到对应工作区，不再重新拉取/装载。
  if (workspace.selected[kind]?.id === id) {
    if (trackRecent) {
      void office.json(`/assets/${id}/select`, { method: 'POST' }).catch(() => {});
    }
    await switchSurface(kind);
    return;
  }
  const { asset } = await openAsset(id, trackRecent);
  resetAssistantThread();
  workspace.selected[kind] = asset;
  workspace.assets[kind] = workspace.assets[kind].map(item => item.id === asset.id ? asset : item);
  // 最近列表后台刷新，不挡住打开
  if (trackRecent) {
    void loadRecentAssets(workspace.project.id).then(list => {
      if (workspace) { workspace.recentAssets = list; paintNav(); }
    }).catch(() => {});
  }
  // PDF.js waits until its viewer is visible before opening a file.
  if (kind === 'pdf') {
    await switchSurface(kind);
    await editors[kind].load(asset);
    return;
  }
  await editors[kind].load(asset);
  await switchSurface(kind);
}

async function chooseRecentAsset(id: string) {
  if (!workspace) return;
  const recent = workspace.recentAssets.find(item => item.id === id);
  if (!recent) throw new Error('最近打开的资产已不存在。');
  if (recent.project_id && recent.project_id !== workspace.project.id) {
    workspace = await selectProject(recent.project_id);
  }
  await chooseAsset(recent.kind as Kind, recent.id, false);
}

async function addAsset(kind: Kind) {
  if (!workspace) return;
  if (kind === 'pdf') {
    $('#pdf input[type="file"]').click();
    return;
  }
  const scopedCreate = kind === 'document'
    ? await askDocumentCreate()
    : kind === 'project_plan'
      ? await askProjectPlanCreate()
      : null;
  if ((kind === 'document' || kind === 'project_plan') && !scopedCreate) return;
  const name = scopedCreate?.name || await askText(
    kind === 'sheet' ? '新建表格' : kind === 'document' ? '新建文档' : kind === 'research' ? '新建研究图' : kind === 'diagram' ? '新建绘图' : kind === 'project_plan' ? '新建项目计划' : '新建导图',
    kind === 'sheet' ? '表格名称' : kind === 'document' ? '文档名称' : kind === 'research' ? '研究图名称' : kind === 'diagram' ? '绘图名称' : kind === 'project_plan' ? '项目计划名称' : '导图名称',
  );
  if (!name) return;
  const asset = await createAsset(workspace.project.id, kind, name, scopedCreate?.visibility);
  workspace.assets[kind] = [asset, ...workspace.assets[kind]];
  workspace.selected[kind] = asset;
  workspace.recentAssets = await loadRecentAssets(workspace.project.id);
  paintNav();
  footer();
  await editors![kind].load(asset);
  await switchSurface(kind);
}

async function deleteAsset(kind: Kind, id: string) {
  if (!workspace || !editors || kind !== 'document') return;
  const current = workspace.assets[kind].find(item => item.id === id);
  if (!current) return;
  const ok = await askConfirm(`删除「${current.name}」？删除后无法恢复。`);
  if (!ok) return;
  await office.json(`/assets/${id}`, { method: 'DELETE' });
  workspace.assets[kind] = workspace.assets[kind].filter(item => item.id !== id);
  if (workspace.selected[kind]?.id === id) {
    const next = workspace.assets[kind][0] || null;
    workspace.selected[kind] = next;
    if (next) {
      const { asset } = await openAsset(next.id);
      workspace.selected[kind] = asset;
      workspace.assets[kind] = workspace.assets[kind].map(item => item.id === asset.id ? asset : item);
      await editors[kind].load(asset);
    }
  }
  paintNav();
  footer();
}

async function chooseProject(id: string) {
  workspace = await selectProject(id);
  paintNav();
  await Promise.all(ALL_KINDS.map(loadEditor));
  // 切到该项目最近打开的资产（含工作区类型），避免仍停留在上一项目的操作面
  const recent = workspace.recentAssets[0];
  if (recent?.id && recent.kind) {
    await chooseAsset(recent.kind as Kind, recent.id, false);
  } else {
    const fallback = ALL_KINDS.find(kind => workspace?.selected[kind]);
    if (fallback) await switchSurface(fallback);
  }
  footer(); refresh();
}

async function addProject() {
  const name = await askText('新建项目', '项目名称');
  if (!name) return;
  workspace = await createProject(name);
  paintNav();
  await Promise.all(ALL_KINDS.map(loadEditor));
  const recent = workspace.recentAssets[0];
  if (recent?.id && recent.kind) {
    await chooseAsset(recent.kind as Kind, recent.id, false);
  } else {
    const fallback = ALL_KINDS.find(kind => workspace?.selected[kind]);
    if (fallback) await switchSurface(fallback);
  }
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
      const asset = await getAsset(next.id);
      workspace.selected.pdf = asset;
      await editors.pdf.load(asset);
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
  const currentVisibility = current.placement === 'user' ? 'user' as const : 'project' as const;
  let updated: Asset;
  if (kind === 'document' || kind === 'project_plan') {
    const edited = await askScopedAssetEdit({
      title: kind === 'document' ? '编辑文档' : '编辑项目计划',
      nameLabel: kind === 'document' ? '文档名称' : '项目计划名称',
      name: current.name,
      visibility: currentVisibility,
    });
    if (!edited) return;
    if (edited.name === current.name && edited.visibility === currentVisibility) return;
    updated = await updateAsset(id, { name: edited.name, visibility: edited.visibility });
  } else {
    const name = await askText('重命名', '名称', current.name);
    if (!name || name === current.name) return;
    updated = await renameAsset(id, name);
  }
  workspace.assets[kind] = workspace.assets[kind].map(item => item.id === id ? { ...item, ...updated } : item);
  if (workspace.selected[kind]?.id === id) workspace.selected[kind] = { ...workspace.selected[kind]!, ...updated };
  // 改为「我可见」后仍在当前项目列表；改为「项目可见」且绑到其他项目时，可能离开当前列表
  if (kind === 'document' || kind === 'project_plan') {
    const listed = await office.json<{ assets: Asset[] }>(
      `/projects/${workspace.project.id}/assets?kind=${kind}`,
    );
    workspace.assets[kind] = listed.assets;
    const stillHere = listed.assets.find(item => item.id === id);
    if (!stillHere && workspace.selected[kind]?.id === id) {
      const next = listed.assets[0] || null;
      workspace.selected[kind] = next;
      if (next) {
        const { asset } = await openAsset(next.id);
        workspace.selected[kind] = asset;
        await editors?.[kind].load(asset);
      }
    } else if (stillHere) {
      workspace.assets[kind] = listed.assets.map(item => item.id === id ? { ...item, ...updated } : item);
      if (workspace.selected[kind]?.id === id) {
        workspace.selected[kind] = { ...workspace.selected[kind]!, ...updated };
      }
    }
  }
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
  return { annot, deleted, revision: asset.revision };
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
    ),
    pdf: mountPdf($('#pdf'), refresh, {
      onPickFile: handlePdfUpload,
      fetchFile: (id, opts) => office.file(id, {
        revision: opts?.revision ?? workspace?.selected.pdf?.revision,
        expectedSize: opts?.expectedSize ?? workspace?.selected.pdf?.size,
        onProgress: opts?.onProgress,
      }),
      onAnnotate: async body => {
        const result = await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations`, { method: 'POST', body: JSON.stringify(body) });
        return result?.annot ? { ...result.annot, revision: result.revision } : undefined;
      },
      onUpdateAnnot: async (xref, content) => {
        const result = await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations/${xref}`, { method: 'PATCH', body: JSON.stringify({ content }) });
        return { revision: result?.revision };
      },
      onDeleteAnnot: async xref => {
        const result = await savePdfAnnot(`/assets/${workspace!.selected.pdf!.id}/annotations/${xref}`, { method: 'DELETE' });
        return { deleted: result?.deleted, revision: result?.revision };
      },
      saveNotebook: async (content, revision) => {
        const asset = workspace?.selected.pdf;
        if (!asset) throw new Error('未打开 PDF。');
        const saved = await office.json<typeof asset>(`/assets/${asset.id}`, {
          method: 'PUT',
          body: JSON.stringify({ content: { notebook: content }, revision }),
        });
        Object.assign(asset, saved);
        rememberRevision(saved.id, saved.revision);
        return saved.revision;
      },
      userName: () => workspace?.user.email || '用户',
      modelName: () => {
        const label = $('#model-name')?.textContent || '';
        return label.replace(/^模型：/, '').trim() || '模型';
      },
      askAi: (body, onEvent) => office.stream('/assistant/pdf-ask', body, onEvent),
      generateOutline: onEvent => {
        const asset = workspace?.selected.pdf;
        if (!asset) return Promise.reject(new Error('未打开 PDF。'));
        return office.stream('/assets/' + asset.id + '/outline/generate', {}, onEvent);
      },
      generateSummary: (body, onEvent) => {
        const asset = workspace?.selected.pdf;
        if (!asset) return Promise.reject(new Error('未打开 PDF。'));
        return office.stream('/assistant/pdf-summary', { document_id: asset.id, ...body }, onEvent);
      },
    }),
    mindmap: mountMindmap($('#mindmap'), refresh, (id, content) => {
      queueSave(id, content);
    }),
    research: mountResearchDiagram($('#research'), refresh, (id, content) => {
      queueSave(id, content);
    }),
    diagram: mountDiagram($('#diagram'), refresh, (id, content) => {
      queueSave(id, content);
    }),
    project_plan: mountProjectPlan($('#project-plan'), refresh, (id, content) => {
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
    () => workspace?.selected[surface]?.id || null,
    {
      onLoad: loadSessionThread,
      onNew: () => resetAssistantThread(),
      onRefreshAnchor: async () => {
        if (surface === 'sheet') {
          await editors!.sheet.syncSheetCache?.({ regenerateProfile: true });
          updateStaleHint();
        }
        return assistantContext(editors!);
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
        if (sessionController && isStructuredAssistant()) await sessionController.startNew();
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
      const task = isStructuredAssistant() ? 'explain' : taskMode();
      const intent = $<HTMLTextAreaElement>('#intent').value.trim();
      if (!intent) throw new Error('请输入任务描述。');
      let context: Context;
      let history: AssistantTurn[] = [];
      const structuredChat = isStructuredAssistant();
      if (structuredChat && !sessionController?.currentId()) {
        await sessionController!.startNew({ resetUi: false });
      }
      if (structuredChat) {
        context = assistantContext(editors);
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
      const followUp = Boolean(structuredChat && history.length);
      if (!followUp) assistantStatus().clear();
      if (structuredChat) {
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
        summary: structuredChat ? sessionSummary : '',
        use_sheet_cache: isSheetAssistant(),
        history: history.map(turn => ({ role: turn.role, content: turn.content })),
      }, event => {
        if (event.type === 'delta' && event.text) {
          if (event.part === 'reasoning') {
            if (structuredChat) {
              if (!liveAssistant) {
                liveAssistant = { answer: '', thinking: '', thinkingOpen: true, answerStarted: false };
              }
              liveAssistant.thinking += String(event.text);
              renderAssistantThread();
            } else {
              assistantStatus().append(String(event.text));
            }
          } else {
            answer += event.text;
            if (structuredChat) {
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
              assistantStatus().finish();
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
            if (structuredChat) {
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
          } else if (structuredChat) {
            liveAssistant = null;
            assistantThread!.turns.push({ role: 'assistant', content: '（模型未返回内容）', thinking: thought || undefined });
            renderAssistantThread();
          } else {
            renderAssistantPlainAnswer('（模型未返回内容）');
          }
          if (!structuredChat) assistantStatus().finish();
          if (event.agent || event.context_trimmed) {
            assistantStatus().note(formatResultMeta(
              typeof event.agent === 'string' ? event.agent : '',
              event.context_trimmed as { dropped_turns?: number; surrounding_truncated?: boolean } | undefined,
            ));
          } else if (!structuredChat) {
            setAssistantBusy(false);
          }
          if (structuredChat) {
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
        if (isStructuredAssistant()) renderAssistantThread();
      } else if (streamStarted && isStructuredAssistant() && assistantThread?.turns.at(-1)?.role === 'user') {
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
      if (isStructuredAssistant() && assistantThread) {
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

function bindAuth(afterLogin?: () => Promise<void>) {
  const form = $<HTMLFormElement>('#auth');
  const error = form.querySelector<HTMLElement>('#auth-error')!;
  const email = form.querySelector<HTMLInputElement>('#auth-email')!;
  const password = form.querySelector<HTMLInputElement>('#auth-password')!;
  const captchaInput = form.querySelector<HTMLInputElement>('#auth-captcha-code')!;
  const captchaImg = form.querySelector<HTMLImageElement>('#auth-captcha')!;
  const loginBtn = form.querySelector<HTMLButtonElement>('#auth-login')!;
  const registerBtn = form.querySelector<HTMLButtonElement>('#auth-register')!;
  const closeBtn = form.querySelector<HTMLButtonElement>('#auth-close')!;
  let captchaId = '';
  const showError = (message: string) => { error.hidden = false; error.textContent = message; };
  const popup = form.closest('.dialog-overlay');
  if (popup) {
    closeBtn.hidden = false;
    closeBtn.onclick = () => popup.remove();
  }
  const setOnline = (online: boolean) => {
    for (const field of [email, password, captchaInput, loginBtn, registerBtn]) field.disabled = !online;
    captchaImg.style.opacity = online ? '1' : '0.35';
    captchaImg.style.pointerEvents = online ? 'auto' : 'none';
  };
  const refreshCaptcha = async () => {
    const captcha = await loadCaptcha();
    captchaId = captcha.captcha_id;
    captchaImg.src = `data:${captcha.image_type};base64,${captcha.image_base64}`;
    captchaInput.value = '';
  };
  const openLocalSettings = async () => {
    const prefs = await office.json<{ pdf_dir: string; pdf_dir_default: string }>('/local/preferences').catch(() => ({
      pdf_dir: '', pdf_dir_default: '',
    }));
    const desktop = await window.youju?.getConfig?.();
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const showDataMode = Boolean(window.youju?.isDesktop);
    overlay.innerHTML = `<form class="name-dialog auth-local-dialog">
      <h2>设置</h2>
      <label class="field-label" for="local-remote">远程服务地址</label>
      <input id="local-remote" spellcheck="false" placeholder="http://127.0.0.1:8000">
      <label class="field-label" for="local-pdf">本地 PDF 目录</label>
      <input id="local-pdf" spellcheck="false" placeholder="留空使用默认目录">
      ${showDataMode ? `<label class="field-label" for="local-data-mode">本机数据位置</label>
      <select id="local-data-mode">
        <option value="repo">与开发共用（仓库 data/）</option>
        <option value="userData">独立副本（Application Support）</option>
      </select>
      <p class="auth-local-note" data-data-path></p>` : ''}
      <p class="auth-local-note">不改则使用默认值。部署到服务器后，把远程地址改成服务器 URL。embedding 走该地址下的 /api/embeddings。切换数据位置后请重启桌面应用。</p>
      <div class="dialog-actions">
        <button type="button" data-reset>恢复默认</button>
        <button type="button" data-cancel>取消</button>
        <button class="primary" type="submit">保存</button>
      </div>
    </form>`;
    const remoteField = overlay.querySelector<HTMLInputElement>('#local-remote')!;
    const pdfField = overlay.querySelector<HTMLInputElement>('#local-pdf')!;
    const dataModeField = overlay.querySelector<HTMLSelectElement>('#local-data-mode');
    const dataPathNote = overlay.querySelector<HTMLElement>('[data-data-path]');
    remoteField.value = desktop?.remoteBaseUrl || remoteBase();
    const customPdf = desktop?.pdfDir || (prefs.pdf_dir && prefs.pdf_dir !== prefs.pdf_dir_default ? prefs.pdf_dir : '');
    pdfField.value = customPdf;
    if (prefs.pdf_dir_default) pdfField.placeholder = prefs.pdf_dir_default;
    if (dataModeField) {
      dataModeField.value = desktop?.dataMode === 'userData' ? 'userData' : 'repo';
      if (!desktop?.repoDataAvailable) {
        const repoOpt = dataModeField.querySelector('option[value="repo"]');
        if (repoOpt) repoOpt.setAttribute('disabled', 'true');
        dataModeField.value = 'userData';
      }
    }
    if (dataPathNote) {
      dataPathNote.textContent = desktop?.sqlitePath
        ? `当前库：${desktop.sqlitePath}`
        : '保存后重启应用生效。';
    }
    const close = () => overlay.remove();
    const save = async (reset: boolean) => {
      const remote = reset ? '' : remoteField.value.trim();
      const pdfDir = reset ? '' : pdfField.value.trim();
      const dataMode = reset ? (desktop?.repoDataAvailable ? 'repo' : 'userData') : (dataModeField?.value as 'repo' | 'userData' | undefined);
      setRemoteBase(remote);
      await office.json('/local/preferences', { method: 'PUT', body: JSON.stringify({ pdf_dir: pdfDir }) }).catch(() => {});
      await window.youju?.setConfig?.({ remoteBaseUrl: remoteBase(), pdfDir, dataMode });
      close();
      await probe();
    };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', close);
    overlay.querySelector('[data-reset]')!.addEventListener('click', () => { void save(true); });
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    overlay.querySelector('form')!.addEventListener('submit', event => { event.preventDefault(); void save(false); });
    document.body.append(overlay);
    remoteField.focus();
  };
  const probe = async () => {
    const online = await remoteHealthy();
    setOnline(online);
    if (!online) {
      showError('远程服务不可用，只能离线进入。');
      return;
    }
    error.hidden = true;
    try { await refreshCaptcha(); } catch (e) { showError(e instanceof Error ? e.message : String(e)); }
  };
  const submit = async (mode: 'login' | 'register') => {
    error.hidden = true;
    const body = {
      email: email.value.trim(),
      password: password.value,
      captcha_id: captchaId,
      captcha_code: captchaInput.value.trim(),
    };
    if (!body.email || body.password.length < 6 || !body.captcha_code) {
      showError('请填写邮箱、至少 6 位密码和验证码。');
      captchaInput.focus();
      return;
    }
    loginBtn.disabled = true;
    registerBtn.disabled = true;
    try {
      if (mode === 'register') await remoteRegister(body);
      else await remoteLogin(body);
      sessionStorage.setItem('youju-offline', '1');
      const popup = form.closest('.dialog-overlay');
      if (popup) popup.remove();
      // boot 内会 bindLocalAccount 一次；这里不再重复绑，避免连续打两次 /api/account/me
      await boot({ offline: true });
      if (afterLogin) await afterLogin();
    } catch (e) {
      showError(e instanceof Error ? e.message : String(e));
      await refreshCaptcha().catch(() => {});
      captchaInput.focus();
    } finally {
      const online = !email.disabled;
      loginBtn.disabled = !online;
      registerBtn.disabled = !online;
    }
  };
  form.onsubmit = e => { e.preventDefault(); void submit('login'); };
  registerBtn.onclick = () => { void submit('register'); };
  captchaImg.onclick = () => {
    if (captchaImg.style.pointerEvents === 'none') return;
    void refreshCaptcha().catch(e => showError(e instanceof Error ? e.message : String(e)));
  };
  form.querySelector<HTMLButtonElement>('#auth-offline')!.onclick = () => {
    if (popup) { popup.remove(); return; }
    sessionStorage.setItem('youju-offline', '1');
    void boot({ offline: true });
  };
  const settingsLink = form.parentElement?.querySelector<HTMLButtonElement>('#auth-local-settings');
  if (settingsLink) settingsLink.onclick = () => { void openLocalSettings(); };
  void (async () => {
    const config = await window.youju?.getConfig?.();
    if (config?.remoteBaseUrl) setRemoteBase(config.remoteBaseUrl);
    if (config?.pdfDir) {
      await office.json('/local/preferences', { method: 'PUT', body: JSON.stringify({ pdf_dir: config.pdfDir }) }).catch(() => {});
    }
    await probe();
  })();
}

async function boot(options?: { forceAuth?: boolean; offline?: boolean }) {
  ready = false;
  editors = null;
  workspace = null;
  pending = null;
  const offline = options?.offline || sessionStorage.getItem('youju-offline') === '1';
  if (options?.forceAuth || !offline) {
    renderAuth(root);
    createIcons({ icons });
    bindAuth();
    return;
  }
  if (remoteToken()) {
    try { await bindLocalAccount(); } catch { /* 远程暂不可用时继续用本机访客/已有令牌 */ }
  }
  try {
    workspace = await bootstrap();
  } catch (e) {
    if (e instanceof AuthError) { logout(); renderAuth(root); createIcons({ icons }); bindAuth(); return; }
    // 纯在线模式且服务不可用时仍回登录页
    if (!office.signedIn()) {
      renderAuth(root);
      createIcons({ icons });
      bindAuth();
      fail(e);
      return;
    }
    fail(e);
    return;
  }
  migrateLegacy(workspace);
  await flushSave().catch(() => {});
  renderChrome(root);
  createIcons({ icons });
  bindWorkspace();
  const last = saved('office-surface');
  await switchSurface(last === 'document' || last === 'pdf' || last === 'mindmap' || last === 'research' || last === 'diagram' || last === 'project_plan' || last === 'calendar' ? last : 'sheet');
  if (saved('office-view') === 'portfolio') await enterPortfolio().catch(fail);
  await Promise.all(ALL_KINDS.map(kind => loadEditor(kind).catch(fail)));
  if (sessionController && isStructuredAssistant() && workspace?.selected[surface]) {
    await sessionController.refreshList();
    await restoreLastSession(workspace.selected[surface]!.id, sessionController);
  }
  footer();
  refresh();
}

void boot().catch(fail);
