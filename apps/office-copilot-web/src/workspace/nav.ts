import { createIcons, icons } from 'lucide';
import {
  addPdfFolder, deletePdfFolder, folderMatches, folderPaths, loadPdfFolders, pdfsInFolder,
  placePdf, renamePdfFolder, savePdfFolders, type PdfFolder,
} from './pdf-folders';
import { office } from '../office-client/client';
import type { Asset, Kind, Workspace } from './session';
import { openCalcWindow } from './calc-window';

const LABELS: Record<Kind, string> = { sheet: '表格', document: '文档', pdf: 'PDF', mindmap: '导图', research: '研究图', diagram: '绘图', calendar: '计划' };
const NEW_LABELS: Record<Kind, string> = { sheet: '新建表格', document: '新建文档', pdf: '新建 PDF', mindmap: '新建导图', research: '新建研究图', diagram: '新建绘图', calendar: '新建计划' };

function kindIcon(kind: Kind) {
  if (kind === 'sheet') return 'table-2';
  if (kind === 'document') return 'file-text';
  if (kind === 'pdf') return 'book-open';
  if (kind === 'calendar') return 'calendar-days';
  if (kind === 'diagram') return 'workflow';
  if (kind === 'research') return 'chart-no-axes-combined';
  return 'git-fork';
}

export function renderAuth(root: HTMLElement) {
  root.innerHTML = `<div class="auth-page" id="auth-page">
    <form id="auth" class="auth-card">
      <h1>AI学研助手</h1>
      <p>登录后进入你的项目工作区</p>
      <label class="field-label" for="auth-email">邮箱</label>
      <input id="auth-email" name="email" type="email" autocomplete="username" required>
      <label class="field-label" for="auth-password">密码</label>
      <input id="auth-password" name="password" type="password" autocomplete="current-password" required minlength="6">
      <div class="auth-actions">
        <button class="primary" id="auth-login" type="submit">登录</button>
        <button id="auth-register" type="button">注册</button>
      </div>
      <div id="error" role="alert" hidden></div>
    </form>
  </div>`;
}

export function renderChrome(root: HTMLElement) {
  root.innerHTML = `
  <header>
    <nav class="workspace-nav" aria-label="工作区"></nav>
    <nav class="account-nav" aria-label="项目与账户"></nav>
  </header>
  <main><div class="workspace">
    <section id="sheet" class="surface" aria-label="表格编辑器"></section>
    <section id="document" class="surface" aria-label="文档编辑器" hidden><div class="format-bar"><button data-format="bold" title="加粗" aria-label="加粗"><i data-lucide="bold"></i></button><button data-format="italic" title="斜体" aria-label="斜体"><i data-lucide="italic"></i></button><button data-format="strike" title="删除线" aria-label="删除线"><i data-lucide="strikethrough"></i></button><button data-format="highlight" title="高亮" aria-label="高亮"><i data-lucide="highlighter"></i></button><button data-format="code" title="行内代码" aria-label="行内代码"><i data-lucide="code"></i></button><button data-format="h1" title="一级标题" aria-label="一级标题"><i data-lucide="heading-1"></i></button><button data-format="heading" title="二级标题" aria-label="二级标题"><i data-lucide="heading-2"></i></button><button data-format="h3" title="三级标题" aria-label="三级标题"><i data-lucide="heading-3"></i></button><button data-format="list" title="无序列表" aria-label="无序列表"><i data-lucide="list"></i></button><button data-format="ordered" title="有序列表" aria-label="有序列表"><i data-lucide="list-ordered"></i></button><button data-format="quote" title="引用" aria-label="引用"><i data-lucide="quote"></i></button><button data-format="codeblock" title="代码块" aria-label="代码块"><i data-lucide="square-code"></i></button><button data-format="rule" title="分隔线" aria-label="分隔线"><i data-lucide="minus"></i></button><span class="format-sep" aria-hidden="true"></span><button type="button" data-digest-job title="定时任务" aria-label="定时任务"><i data-lucide="clock"></i><span>定时任务</span></button><div class="skill-menu-wrap"><button type="button" data-my-skills title="我的技能" aria-label="我的技能" aria-haspopup="menu" aria-expanded="false"><span class="skill-tool-icon" aria-hidden="true"><svg viewBox="0 -1 738 717" fill="currentColor"><g transform="translate(0 715) scale(1 -1)"><path d="M729.08 113.84L504.32 342.24C493.72 352.84 476.62 352.84 466.01 342.24L449.64 327.19L410.87 366.43C410.87 366.43 472.91 434.57 475.96 433.18C542.11 403.68 588.97 416.94 642.78 469.3C690.17 516.69 700.18 585.09 675.53 638.84L582.73 547.71C573.39 538.36 558.21 538.36 548.93 547.71L509.36 587.21C500.02 596.55 500.02 611.74 509.36 621.08L598.04 709.16C547.07 725.34 486.76 711.42 443.74 668.4C373.48 601.06 384.29 552.94 408.61 499.32C409.61 497.07 346.38 429.53 346.38 429.53L342.2 429.53L139.06 634.14C139.06 634.14 147.94 660.91 132.69 676.22L99.82 707.64L37.58 645.4L70.45 613.99C70.45 613.99 95.44 598.74 113.47 607.49L311.71 406.86L315.69 399.9L56.14 163.41L22.27 129.48C-7.42 99.85 -7.42 51.8 22.27 22.17C51.9 -7.39 99.88 -7.39 129.58 22.17L163.44 56.17C163.44 56.17 315.83 253.09 382.44 332.5L418.23 296.44L398.61 273.97C388 263.5 388 246.33 398.61 235.73L621.97 10.31C632.51 -0.3 649.68 -0.3 660.22 10.31L729.08 75.59C739.69 86.13 739.69 103.37 729.08 113.84ZM110.02 78.71C100.74 69.36 85.63 69.36 76.28 78.71C66.94 87.99 66.94 103.1 76.28 112.51C85.63 121.86 100.74 121.86 110.02 112.51C119.37 103.1 119.37 87.92 110.02 78.71ZM652.26 44.11C647.75 39.67 640.47 39.67 636.02 44.11L435.86 254.29C431.41 258.73 431.41 266.08 435.86 270.52C440.36 274.96 447.65 274.96 452.09 270.52L652.33 60.35C656.77 55.91 656.77 48.55 652.26 44.11ZM693.82 85.6C689.38 81.16 682.09 81.16 677.58 85.6L477.41 295.84C472.91 300.35 472.91 307.57 477.41 312.08C481.86 316.52 489.14 316.52 493.59 312.08L693.82 101.91C698.32 97.4 698.32 85.6Z"/></g></svg></span><span>我的技能</span></button><div class="skill-menu" data-skill-menu hidden></div></div></div><div id="rich-editor"></div></section>
    <section id="pdf" class="surface" aria-label="PDF 阅读器" hidden><div class="pdf-toolbar"><input type="file" accept="application/pdf,.pdf" hidden><div class="pdf-annotate" role="toolbar" aria-label="PDF 批注"><button type="button" data-annotate="highlight" title="先划选文字，再点击高亮" aria-label="高亮" disabled><i data-lucide="highlighter"></i><span>高亮</span></button><button type="button" data-annotate="text" title="先划选文字，再添加批注" aria-label="文字批注" disabled><i data-lucide="message-square-plus"></i><span>批注</span></button><button type="button" data-ask-ai title="先划选文字，再向 AI 提问" aria-label="问 AI" disabled><i data-lucide="sparkles"></i><span>问AI</span></button><button type="button" data-pdf-terms title="维护本项目的财报译法" aria-label="术语">术语</button><button type="button" data-pdf-shot title="在页面上拖拽截取区域" aria-label="截图" aria-pressed="false" disabled>截图</button></div><div class="pdf-shots" data-pdf-shots hidden></div><span data-page>未打开文件</span><div class="pdf-nav"><button data-prev title="上一页" aria-label="上一页"><i data-lucide="chevron-left"></i></button><input data-page-input type="number" min="1" step="1" value="1" aria-label="跳转到页码" title="输入页码后回车跳转"><span data-page-total>/ 0</span><button data-next title="下一页" aria-label="下一页"><i data-lucide="chevron-right"></i></button></div><div class="pdf-zoom"><button data-zoom-out title="缩小" aria-label="缩小" disabled><i data-lucide="zoom-out"></i></button><span data-zoom>100%</span><button data-zoom-in title="放大" aria-label="放大" disabled><i data-lucide="zoom-in"></i></button><button data-fit-width title="适应宽度" aria-label="适应宽度" disabled><i data-lucide="unfold-horizontal"></i></button></div><button type="button" data-split-view aria-pressed="false" title="左右对照同一份 PDF" disabled>复制浏览</button></div><div class="pdf-stage"><div class="pdf-pane" data-pane="copy" hidden><div class="pdf-scroll" tabindex="0" data-scroll="copy"></div></div><div class="pdf-split" role="separator" aria-orientation="vertical" aria-label="调整对照窗格宽度" tabindex="0" hidden></div><div class="pdf-pane is-active" data-pane="main"><div class="pdf-scroll" tabindex="0" data-scroll="main"><div class="pdf-page"><div class="empty"><i data-lucide="file-search"></i><p>未打开 PDF</p></div></div></div></div></div></section>
    <section id="mindmap" class="surface" aria-label="思维导图" hidden></section>
    <section id="research" class="surface" aria-label="研究图" hidden></section>
    <section id="diagram" class="surface" aria-label="绘图" hidden></section>
    <section id="calendar" class="surface" aria-label="工作计划" hidden></section>
  </div><div id="workspace-split" class="workspace-split" role="separator" aria-orientation="vertical" aria-expanded="true" aria-label="隐藏或调整协作助手" tabindex="0"></div><aside><div class="panel-title"><div class="panel-heading"><i data-lucide="sparkles"></i><h1>协作助手</h1></div><span id="model-name" class="model-name">模型：未配置</span></div>
    <div id="assistant-sessions-bar" class="assistant-sessions-bar" hidden>
      <label class="assistant-session-label"><select id="assistant-session-select"></select></label>
      <div class="assistant-session-actions">
        <button type="button" id="rename-assistant-session" title="修改会话名"><i data-lucide="pencil"></i></button>
        <button type="button" id="refresh-sheet-context" title="刷新当前工作区上下文"><i data-lucide="refresh-cw"></i></button>
        <button type="button" id="delete-assistant-session" title="删除当前会话"><i data-lucide="trash-2"></i></button>
      </div>
    </div>
    <div class="assistant-scroll">
      <div id="result" aria-live="polite"><div class="result-meta" id="result-meta"></div><div id="answer" class="assistant-thread"></div><div id="patch-preview"></div><div id="patch-actions" hidden><button class="primary" id="apply"><i data-lucide="check"></i>接受修改</button><button id="discard">放弃</button></div></div>
    </div>
    <form id="ask" class="assistant-composer"><div id="error" role="alert" hidden></div><div class="task-modes" role="radiogroup" aria-label="任务类型"><label class="task-mode"><input type="radio" name="task-mode" id="task-explain" value="explain" checked>选区解释</label><label class="task-mode"><input type="radio" name="task-mode" id="task-generate" value="generate">生成内容</label></div><div class="assistant-composer-row"><textarea id="intent" rows="3" required placeholder="请输入您的任务描述"></textarea><div class="assistant-composer-actions"><button class="primary" id="run" type="submit" title="运行（Ctrl/⌘+Enter）"><i data-lucide="play"></i><span>运行</span></button><button type="button" id="new-assistant-thread" title="新对话"><i data-lucide="message-square-plus"></i><span>新对话</span></button></div></div></form>
  </aside></main><footer><span class="footer-left"><span id="footer-surface">工作区</span><span id="save-status" hidden></span></span><span id="footer-user">本地工作区</span></footer>`;
}

export function askConfirm(title: string, confirmLabel = '删除') {
  return new Promise<boolean>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog"><h2></h2><div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" data-ok></button></div></form>`;
    overlay.querySelector('h2')!.textContent = title;
    overlay.querySelector<HTMLButtonElement>('[data-ok]')!.textContent = confirmLabel;
    const finish = (value: boolean) => { overlay.remove(); resolve(value); };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
    overlay.querySelector('form')!.addEventListener('submit', e => { e.preventDefault(); finish(true); });
    document.body.append(overlay);
    overlay.querySelector<HTMLButtonElement>('[data-cancel]')!.focus();
  });
}

export function askText(title: string, placeholder: string, initial = '', maxLength = 80, multiline = false) {
  return new Promise<string | null>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = multiline
      ? `<form class="name-dialog name-dialog-note"><h2></h2><textarea required rows="6"></textarea><div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary">确定</button></div></form>`
      : `<form class="name-dialog"><h2></h2><input required><div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary">确定</button></div></form>`;
    overlay.querySelector('h2')!.textContent = title;
    const input = overlay.querySelector<HTMLInputElement | HTMLTextAreaElement>(multiline ? 'textarea' : 'input')!;
    input.placeholder = placeholder;
    input.value = initial;
    input.maxLength = maxLength;
    const finish = (value: string | null) => { overlay.remove(); resolve(value); };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    overlay.querySelector('form')!.addEventListener('submit', e => {
    e.preventDefault();
      const name = input.value.trim();
      if (name) finish(name);
    });
    if (multiline) {
      input.addEventListener('keydown', e => {
        const key = e as KeyboardEvent;
        if ((key.metaKey || key.ctrlKey) && key.key === 'Enter') {
          e.preventDefault();
          overlay.querySelector('form')!.requestSubmit();
        }
      });
    }
    document.body.append(overlay);
    input.focus();
    if (initial && !multiline) (input as HTMLInputElement).select();
    else if (initial && multiline) input.setSelectionRange(0, input.value.length);
  });
}

export function askDocumentCreate() {
  return new Promise<{ name: string; visibility: 'project' | 'user' } | null>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog document-create-dialog">
      <h2>新建文档</h2>
      <label class="field-label" for="document-create-name">文档名称</label>
      <input id="document-create-name" required maxlength="80" placeholder="文档名称">
      <fieldset><legend>可见范围</legend><label><input type="radio" name="visibility" value="project" checked><span>项目可见</span></label><label><input type="radio" name="visibility" value="user"><span>我可见</span></label></fieldset>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary">创建</button></div>
    </form>`;
    const finish = (value: { name: string; visibility: 'project' | 'user' } | null) => { overlay.remove(); resolve(value); };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    overlay.addEventListener('click', event => { if (event.target === overlay) finish(null); });
    overlay.querySelector('form')!.addEventListener('submit', event => {
      event.preventDefault();
      const name = overlay.querySelector<HTMLInputElement>('#document-create-name')!.value.trim();
      const visibility = overlay.querySelector<HTMLInputElement>('input[name="visibility"]:checked')!.value as 'project' | 'user';
      if (name) finish({ name, visibility });
    });
    document.body.append(overlay);
    overlay.querySelector<HTMLInputElement>('#document-create-name')!.focus();
  });
}

/** 保存失败弹窗：阻断式提示，可选重试。 */
export function alertSaveError(message: string) {
  return new Promise<'retry' | 'dismiss'>(resolve => {
    if (document.querySelector('.save-error-overlay')) {
      resolve('dismiss');
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay save-error-overlay';
    overlay.innerHTML = `<div class="name-dialog save-error-dialog" role="alertdialog" aria-labelledby="save-error-title" aria-describedby="save-error-body">
      <h2 id="save-error-title">保存失败</h2>
      <p id="save-error-body" class="save-error-body"></p>
      <p class="save-error-hint">内容仍保留在当前编辑器中。请确认 office-backend / 数据库已启动后重试，勿直接关闭页面以免丢失未同步内容。</p>
      <div class="dialog-actions">
        <button type="button" data-dismiss>知道了</button>
        <button type="button" class="primary" data-retry>重试保存</button>
      </div>
    </div>`;
    overlay.querySelector('#save-error-body')!.textContent = message || '无法写入工作区服务。';
    const finish = (action: 'retry' | 'dismiss') => {
      overlay.remove();
      resolve(action);
    };
    overlay.querySelector('[data-dismiss]')!.addEventListener('click', () => finish('dismiss'));
    overlay.querySelector('[data-retry]')!.addEventListener('click', () => finish('retry'));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish('dismiss'); });
    document.body.append(overlay);
    (overlay.querySelector('[data-retry]') as HTMLButtonElement).focus();
  });
}

export async function openAccountDialog(email: string) {
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = `<div class="account-dialog" role="dialog" aria-labelledby="account-title">
    <h2 id="account-title">账号</h2>
    <p class="account-email"></p>
    <label class="field-label" for="access-token">访问令牌</label>
    <div class="token-row">
      <input id="access-token" readonly spellcheck="false">
      <button type="button" data-copy>复制</button>
    </div>
    <p class="account-hint">令牌是外部 Agent 访问的身份验证方式，请妥善保管，重新生成令牌后，需更新外部 Agent Skill 。</p>
    <div class="dialog-actions account-actions">
      <button type="button" data-rotate>重新生成</button>
    </div>
    <h3>下载 Skill</h3>
    <div class="skill-downloads"></div>
    <p class="account-note" hidden></p>
    <div class="dialog-actions"><button type="button" class="primary" data-close>关闭</button></div>
  </div>`;
  overlay.querySelector('.account-email')!.textContent = email;
  const input = overlay.querySelector<HTMLInputElement>('#access-token')!;
  const note = overlay.querySelector<HTMLElement>('.account-note')!;
  const skillsBox = overlay.querySelector<HTMLElement>('.skill-downloads')!;
  const skills: { id: string; name: string; filename: string }[] = [];
  const showNote = (text: string) => { note.hidden = false; note.textContent = text; };
  const setToken = (token: string) => { input.value = token; };
  const close = () => overlay.remove();
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  overlay.querySelector('[data-close]')!.addEventListener('click', close);
  overlay.querySelector('[data-copy]')!.addEventListener('click', async () => {
    if (!input.value) return;
    try {
      await navigator.clipboard.writeText(input.value);
      showNote('已复制令牌。');
    } catch {
      input.select();
      showNote('请手动复制令牌。');
    }
  });
  overlay.querySelector('[data-rotate]')!.addEventListener('click', async () => {
    if (!confirm('重新生成后，旧令牌立即失效。将重新下载写入新令牌的 skill。确定吗？')) return;
    try {
      const data = await office.json<{ access_token: string }>('/auth/access-token/rotate', { method: 'POST' });
      setToken(data.access_token);
      for (const skill of skills) {
        await office.download(`/auth/skills/${skill.id}`, skill.filename);
      }
      showNote(skills.length ? '已生成新令牌，并下载了写入新令牌的 skill，请替换 Agent 里的旧文件。' : '已生成新令牌。');
    } catch (e) {
      showNote(e instanceof Error ? e.message : String(e));
    }
  });
  document.body.append(overlay);
  try {
    const data = await office.json<{ access_token: string }>('/auth/access-token');
    setToken(data.access_token);
    const catalog = await office.json<{ skills: { id: string; name: string; filename: string }[] }>('/auth/skills');
    skills.push(...catalog.skills);
    for (const skill of skills) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `下载 ${skill.name}`;
      button.addEventListener('click', async () => {
        try {
          await office.download(`/auth/skills/${skill.id}`, skill.filename);
          showNote(`已下载 ${skill.filename}（含当前令牌）。`);
        } catch (e) {
          showNote(e instanceof Error ? e.message : String(e));
        }
      });
      skillsBox.append(button);
    }
  } catch (e) {
    showNote(e instanceof Error ? e.message : String(e));
  }
}

export interface NavHandlers {
  onSurface: (kind: Kind) => void;
  onSelectAsset: (kind: Kind, id: string) => void;
  onNewAsset: (kind: Kind) => void;
  onRenameAsset: (kind: Kind, id: string) => void;
  onDeletePdf: (id: string) => Promise<boolean>;
  onDeleteAsset: (kind: Kind, id: string) => void;
  onSelectProject: (id: string) => void;
  onNewProject: () => void;
  onRenameProject: (id: string) => void;
  onLogout: () => void;
  onOpenCalc: () => void;
  onOpenNotebook: () => void;
}

function closeMenus(event?: Event) {
  if (event?.target instanceof Element && event.target.closest('.split-tab')) return;
  document.querySelectorAll<HTMLElement>('.menu').forEach(menu => { menu.hidden = true; });
  document.querySelectorAll<HTMLButtonElement>('[data-caret], #settings-btn').forEach(btn => btn.setAttribute('aria-expanded', 'false'));
}
if (!('officeMenuBound' in document)) {
  document.addEventListener('click', closeMenus);
  Object.assign(document, { officeMenuBound: true });
}

let pdfQuery = '';
let movingPdfId = '';
let pdfNameBubble: HTMLElement | null = null;

function showPdfNameBubble(anchor: HTMLElement, text: string) {
  if (!pdfNameBubble) {
    pdfNameBubble = document.createElement('div');
    pdfNameBubble.className = 'pdf-name-bubble';
    pdfNameBubble.setAttribute('role', 'tooltip');
    document.body.append(pdfNameBubble);
  }
  pdfNameBubble.textContent = text;
  pdfNameBubble.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const width = pdfNameBubble.offsetWidth;
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  pdfNameBubble.style.left = `${left}px`;
  pdfNameBubble.style.top = `${rect.bottom + 6}px`;
}

function hidePdfNameBubble() {
  if (pdfNameBubble) pdfNameBubble.hidden = true;
}

function reopenPdfMenu(workspace: Workspace, surface: Kind, handlers: NavHandlers) {
  renderNav(workspace, surface, handlers);
  const caret = document.querySelector<HTMLButtonElement>('[data-caret="pdf"]');
  const menu = caret?.parentElement?.querySelector<HTMLElement>('.menu');
  if (!caret || !menu) return;
  menu.hidden = false;
  caret.setAttribute('aria-expanded', 'true');
  const search = menu.querySelector<HTMLInputElement>('.pdf-tree-search');
  if (search && pdfQuery) {
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
  }
}

function fillPdfMenu(
  menu: HTMLElement,
  assets: Asset[],
  currentId: string,
  projectId: string,
  refresh: () => void,
  onDelete: (id: string) => Promise<boolean>,
) {
  hidePdfNameBubble();
  const store = loadPdfFolders(projectId);
  const byId = new Map(assets.map(asset => [asset.id, asset]));
  const assetName = (id: string) => byId.get(id)?.name || '';

  const search = document.createElement('input');
  search.className = 'pdf-tree-search';
  search.type = 'search';
  search.placeholder = '查找 PDF 或文件夹';
  search.value = pdfQuery;
  search.setAttribute('aria-label', '查找 PDF 或文件夹');
  search.addEventListener('click', e => e.stopPropagation());
  search.addEventListener('input', () => {
    pdfQuery = search.value;
    refresh();
  });
  menu.append(search);

  const query = pdfQuery.trim().toLowerCase();
  const visible = (asset: Asset) => !query || asset.name.toLowerCase().includes(query);

  const appendPdf = (parent: HTMLElement, asset: Asset, depth: number, force = false) => {
    if (!force && !visible(asset)) return;
    const row = document.createElement('div');
    row.className = 'menu-row pdf-tree-row';
    row.style.paddingLeft = `${depth * 14}px`;
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.assetId = asset.id;
    button.className = currentId === asset.id ? 'active' : '';
    button.innerHTML = '<i data-lucide="file-text"></i>';
    const name = document.createElement('span');
    name.textContent = asset.name;
    button.append(name);
    const fullName = asset.filename || asset.name;
    button.addEventListener('mouseenter', () => showPdfNameBubble(button, fullName));
    button.addEventListener('mouseleave', hidePdfNameBubble);
    const move = document.createElement('button');
    move.type = 'button';
    move.className = 'menu-edit';
    move.title = '移动到文件夹';
    move.setAttribute('aria-label', `移动 ${asset.name}`);
    move.innerHTML = '<i data-lucide="folder-input"></i>';
    move.addEventListener('click', e => {
      e.stopPropagation();
      movingPdfId = movingPdfId === asset.id ? '' : asset.id;
      refresh();
    });
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'menu-edit';
    edit.dataset.renameAsset = asset.id;
    edit.title = '重命名';
    edit.setAttribute('aria-label', `重命名 ${asset.name}`);
    edit.innerHTML = '<i data-lucide="pencil"></i>';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'menu-edit';
    remove.title = '删除';
    remove.setAttribute('aria-label', `删除 ${asset.name}`);
    remove.innerHTML = '<i data-lucide="trash-2"></i>';
    remove.addEventListener('click', async e => {
      e.stopPropagation();
      if (await onDelete(asset.id)) refresh();
    });
    row.append(button, move, edit, remove);
    parent.append(row);
    if (movingPdfId !== asset.id) return;
    const picker = document.createElement('div');
    picker.className = 'pdf-tree-move';
    const choices: { id: string | null; path: string }[] = [
      { id: null, path: '根目录' },
      ...folderPaths(store.folders),
    ];
    for (const choice of choices) {
      const item = document.createElement('button');
      item.type = 'button';
      item.textContent = choice.path;
      item.addEventListener('click', e => {
        e.stopPropagation();
        const next = loadPdfFolders(projectId);
        placePdf(next, asset.id, choice.id);
        savePdfFolders(projectId, next);
        movingPdfId = '';
        refresh();
      });
      picker.append(item);
    }
    parent.append(picker);
  };

  const appendFolder = (parent: HTMLElement, folder: PdfFolder, depth: number) => {
    if (query && !folderMatches(folder, query, assetName, store)) return;
    const named = !query || folder.name.toLowerCase().includes(query);
    const open = query ? true : !store.collapsed[folder.id];
    const row = document.createElement('div');
    row.className = 'menu-row pdf-tree-row pdf-tree-folder';
    row.style.paddingLeft = `${depth * 14}px`;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'pdf-tree-folder-name';
    toggle.innerHTML = `<i data-lucide="${open ? 'chevron-down' : 'chevron-right'}"></i><i data-lucide="folder"></i>`;
    const label = document.createElement('span');
    label.textContent = folder.name;
    toggle.append(label);
    toggle.addEventListener('click', e => {
      e.stopPropagation();
      if (query) return;
      const next = loadPdfFolders(projectId);
      next.collapsed[folder.id] = !next.collapsed[folder.id];
      savePdfFolders(projectId, next);
      refresh();
    });
    const add = iconButton('folder-plus', `在 ${folder.name} 下新建文件夹`, async () => {
      const name = await askText('新建文件夹', '文件夹名称');
      if (!name) return;
      const next = loadPdfFolders(projectId);
      addPdfFolder(next, folder.id, name);
      savePdfFolders(projectId, next);
      refresh();
    });
    const edit = iconButton('pencil', `重命名 ${folder.name}`, async () => {
      const name = await askText('重命名文件夹', '文件夹名称', folder.name);
      if (!name) return;
      const next = loadPdfFolders(projectId);
      renamePdfFolder(next, folder.id, name);
      savePdfFolders(projectId, next);
      refresh();
    });
    const remove = iconButton('trash-2', `删除文件夹 ${folder.name}`, () => {
      const next = loadPdfFolders(projectId);
      deletePdfFolder(next, folder.id);
      savePdfFolders(projectId, next);
      refresh();
    });
    row.append(toggle, add, edit, remove);
    parent.append(row);
    if (!open) return;
    for (const child of folder.children) appendFolder(parent, child, depth + 1);
    for (const id of pdfsInFolder(store, folder.id, assets.map(asset => asset.id))) {
      const asset = byId.get(id);
      if (asset) appendPdf(parent, asset, depth + 1, named);
    }
  };

  for (const folder of store.folders) appendFolder(menu, folder, 0);
  for (const id of pdfsInFolder(store, null, assets.map(asset => asset.id))) {
    const asset = byId.get(id);
    if (asset) appendPdf(menu, asset, 0);
  }

  const addFolder = document.createElement('button');
  addFolder.type = 'button';
  addFolder.className = 'menu-new';
  addFolder.textContent = '新建文件夹';
  addFolder.addEventListener('click', async e => {
    e.stopPropagation();
    const name = await askText('新建文件夹', '文件夹名称');
    if (!name) return;
    const next = loadPdfFolders(projectId);
    addPdfFolder(next, null, name);
    savePdfFolders(projectId, next);
    refresh();
  });
  menu.append(addFolder);
}

function iconButton(icon: string, label: string, run: () => void) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'menu-edit';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.innerHTML = `<i data-lucide="${icon}"></i>`;
  button.addEventListener('click', e => {
    e.stopPropagation();
    run();
  });
  return button;
}

let titleCopyNotice: HTMLElement | null = null;
let titleCopyNoticeTimer: ReturnType<typeof window.setTimeout> | undefined;

function showTitleCopyNotice(text: string) {
  if (!titleCopyNotice) {
    titleCopyNotice = document.createElement('div');
    titleCopyNotice.className = 'title-copy-notice';
    titleCopyNotice.setAttribute('role', 'status');
    titleCopyNotice.setAttribute('aria-live', 'polite');
    document.body.append(titleCopyNotice);
  }

  titleCopyNotice.textContent = `已复制：${text}`;
  titleCopyNotice.hidden = false;

  if (titleCopyNoticeTimer !== undefined) {
    window.clearTimeout(titleCopyNoticeTimer);
  }
  titleCopyNoticeTimer = window.setTimeout(() => {
    if (titleCopyNotice) titleCopyNotice.hidden = true;
  }, 1800);
}

async function copyTitlePath(button: HTMLButtonElement, text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = document.createElement('textarea');
      input.value = text;
      input.setAttribute('readonly', '');
      input.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
      document.body.append(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) throw new Error('浏览器未允许复制。');
    }
    const title = button.title;
    button.title = '已复制';
    showTitleCopyNotice(text);
    window.setTimeout(() => { button.title = title; }, 1200);
  } catch (error) {
    window.dispatchEvent(new CustomEvent('office-error', {
      detail: error instanceof Error ? `复制失败：${error.message}` : '复制失败。',
    }));
  }
}

function titleCopyButton(text: string, label: string) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'menu-edit';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.innerHTML = '<i data-lucide="copy"></i>';
  button.addEventListener('click', event => {
    event.stopPropagation();
    void copyTitlePath(button, text);
  });
  return button;
}

export function renderNav(workspace: Workspace, surface: Kind, handlers: NavHandlers) {
  const left = document.querySelector('.workspace-nav')!;
  const right = document.querySelector('.account-nav')!;
  left.replaceChildren(...(['pdf', 'sheet', 'mindmap', 'document', 'research', 'diagram'] as Kind[]).map(kind => {
    const wrap = document.createElement('div');
    wrap.className = 'split-tab';
    wrap.dataset.kind = kind;
    const items = workspace.assets[kind];
    const current = workspace.selected[kind];
    const label = current?.name || LABELS[kind];
    wrap.innerHTML = `<button role="tab" data-tab="${kind}" aria-selected="${surface === kind}" aria-label="${LABELS[kind]}"><i data-lucide="${kindIcon(kind)}"></i><span class="tab-label"></span></button><button data-caret="${kind}" aria-label="${LABELS[kind]}列表" aria-expanded="false"><i data-lucide="chevron-down"></i></button>
      <div class="menu" hidden></div>`;
    const tab = wrap.querySelector<HTMLButtonElement>('[data-tab]')!;
    tab.querySelector('.tab-label')!.textContent = label;
    if (current?.name) tab.title = current.name;
    const menu = wrap.querySelector<HTMLElement>('.menu')!;
    if (kind === 'pdf') {
      fillPdfMenu(menu, items, current?.id || '', workspace.project.id, () => reopenPdfMenu(workspace, surface, handlers), id => handlers.onDeletePdf(id));
    } else items.forEach(asset => {
      const row = document.createElement('div');
      row.className = 'menu-row';
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.assetId = asset.id;
      button.className = current?.id === asset.id ? 'active' : '';
      button.textContent = asset.name;
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'menu-edit';
      edit.dataset.renameAsset = asset.id;
      edit.title = '重命名';
      edit.setAttribute('aria-label', `重命名 ${asset.name}`);
      edit.innerHTML = '<i data-lucide="pencil"></i>';
      const copy = (kind === 'sheet' || kind === 'document' || kind === 'mindmap')
        ? titleCopyButton(`${workspace.project.name}-${asset.name}`, `复制 ${LABELS[kind]}定位标题`)
        : null;
      if (kind === 'document') {
        const name = document.createElement('span');
        name.textContent = asset.name;
        button.textContent = '';
        button.append(name);
        const scope = document.createElement('small');
        scope.className = 'asset-visibility';
        scope.textContent = asset.placement === 'user' ? '我可见' : '项目可见';
        button.append(scope);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'menu-edit menu-delete';
        remove.dataset.deleteAsset = asset.id;
        remove.title = '删除';
        remove.setAttribute('aria-label', `删除 ${asset.name}`);
        remove.innerHTML = '<i data-lucide="trash-2"></i>';
        row.append(button, edit);
        if (copy) row.append(copy);
        row.append(remove);
      } else {
        row.append(button, edit);
        if (copy) row.append(copy);
      }
      menu.append(row);
    });
    const create = document.createElement('button');
    create.type = 'button';
    create.dataset.new = kind;
    create.className = 'menu-new';
    create.textContent = NEW_LABELS[kind];
    menu.append(create);
    return wrap;
  }));
  right.innerHTML = `<div class="tool-group"><button type="button" id="open-calc" title="计算器" aria-label="计算器"><i data-lucide="calculator"></i></button><button type="button" id="open-calendar" data-tab="calendar" title="计划" aria-label="计划" aria-selected="${surface === 'calendar'}"><i data-lucide="calendar-days"></i></button><button type="button" id="open-notebook" title="笔记本" aria-label="笔记本"><i data-lucide="notebook-pen"></i></button></div>
    <div class="split-tab project-tab">
      <button id="project-current" type="button" title="当前项目"></button><button data-caret="project" aria-label="项目列表" aria-expanded="false"><i data-lucide="chevron-down"></i></button>
      <div class="menu menu-right" hidden></div>
    </div>
    <div class="split-tab settings-tab">
      <button id="settings-btn" type="button" aria-haspopup="menu" aria-expanded="false"><i data-lucide="settings"></i><span>设置</span><i data-lucide="chevron-down" class="settings-chevron"></i></button>
      <div class="menu menu-right settings-menu" role="menu" hidden>
        <button type="button" id="account-btn" role="menuitem"><i data-lucide="user-round"></i><span class="settings-item-copy"><span>账号</span><small class="user-email"></small></span></button>
        <button id="logout" type="button" role="menuitem" class="settings-logout"><i data-lucide="log-out"></i><span>退出</span></button>
      </div>
    </div>`;
  right.querySelector<HTMLButtonElement>('#open-notebook')!.onclick = event => {
    event.stopPropagation();
    closeMenus();
    handlers.onOpenNotebook();
  };
  right.querySelector('#project-current')!.textContent = workspace.project.name;
  const account = right.querySelector<HTMLButtonElement>('#account-btn')!;
  account.title = `账号：${workspace.user.email}`;
  account.querySelector<HTMLElement>('.user-email')!.textContent = workspace.user.email;
  const projectMenu = right.querySelector<HTMLElement>('.project-tab .menu')!;
  workspace.projects.forEach(project => {
    const row = document.createElement('div');
    row.className = 'menu-row';
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.projectId = project.id;
    button.className = project.id === workspace.project.id ? 'active' : '';
    button.textContent = project.name;
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'menu-edit';
    edit.dataset.renameProject = project.id;
    edit.title = '重命名';
    edit.setAttribute('aria-label', `重命名 ${project.name}`);
    edit.innerHTML = '<i data-lucide="pencil"></i>';
    const copy = titleCopyButton(project.name, '复制项目定位标题');
    row.append(button, edit, copy);
    projectMenu.append(row);
  });
  const addProject = document.createElement('button');
  addProject.type = 'button';
  addProject.id = 'new-project';
  addProject.className = 'menu-new';
  addProject.textContent = '新建项目';
  projectMenu.append(addProject);
  createIcons({ icons });

  document.querySelectorAll<HTMLButtonElement>('header [data-tab]').forEach(button => {
    button.onclick = () => handlers.onSurface(button.dataset.tab as Kind);
  });
  document.querySelectorAll<HTMLButtonElement>('[data-caret]').forEach(button => {
    button.onclick = e => {
      e.stopPropagation();
      const menu = button.parentElement!.querySelector<HTMLElement>('.menu')!;
      const open = menu.hidden;
      closeMenus();
      menu.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
    };
  });
  document.querySelectorAll<HTMLButtonElement>('header [data-asset-id]').forEach(button => {
    button.onclick = () => {
      const kind = button.closest<HTMLElement>('.split-tab')!.dataset.kind as Kind;
      closeMenus();
      handlers.onSelectAsset(kind, button.dataset.assetId!);
    };
  });
  document.querySelectorAll<HTMLButtonElement>('header [data-new]').forEach(button => {
    button.onclick = () => { closeMenus(); handlers.onNewAsset(button.dataset.new as Kind); };
  });
  document.querySelectorAll<HTMLButtonElement>('header [data-rename-asset]').forEach(button => {
    button.onclick = e => {
      e.stopPropagation();
      const kind = button.closest<HTMLElement>('.split-tab')!.dataset.kind as Kind;
      closeMenus();
      handlers.onRenameAsset(kind, button.dataset.renameAsset!);
    };
  });
  document.querySelectorAll<HTMLButtonElement>('header [data-delete-asset]').forEach(button => {
    button.onclick = e => {
      e.stopPropagation();
      const kind = button.closest<HTMLElement>('.split-tab')!.dataset.kind as Kind;
      closeMenus();
      handlers.onDeleteAsset(kind, button.dataset.deleteAsset!);
    };
  });
  right.querySelectorAll<HTMLButtonElement>('[data-project-id]').forEach(button => {
    button.onclick = () => { closeMenus(); handlers.onSelectProject(button.dataset.projectId!); };
  });
  right.querySelectorAll<HTMLButtonElement>('[data-rename-project]').forEach(button => {
    button.onclick = e => {
      e.stopPropagation();
      closeMenus();
      handlers.onRenameProject(button.dataset.renameProject!);
    };
  });
  right.querySelector<HTMLButtonElement>('#open-calc')!.onclick = () => { closeMenus(); handlers.onOpenCalc(); };
  right.querySelector<HTMLButtonElement>('#project-current')!.onclick = () => {
    right.querySelector<HTMLButtonElement>('[data-caret="project"]')!.click();
  };
  right.querySelector<HTMLButtonElement>('#new-project')!.onclick = () => { closeMenus(); handlers.onNewProject(); };
  const settings = right.querySelector<HTMLButtonElement>('#settings-btn')!;
  settings.onclick = e => {
    e.stopPropagation();
    const menu = right.querySelector<HTMLElement>('.settings-menu')!;
    const open = menu.hidden;
    closeMenus();
    menu.hidden = !open;
    settings.setAttribute('aria-expanded', String(open));
  };
  account.onclick = () => { closeMenus(); void openAccountDialog(workspace.user.email); };
  right.querySelector<HTMLButtonElement>('#logout')!.onclick = () => { closeMenus(); handlers.onLogout(); };
}
