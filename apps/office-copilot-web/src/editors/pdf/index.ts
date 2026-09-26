import { AnnotationMode, getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type PDFDocumentLoadingTask } from 'pdfjs-dist';
import { EventBus, PDFLinkService, PDFViewer, ScrollMode } from 'pdfjs-dist/web/pdf_viewer.mjs';
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import 'pdfjs-dist/web/pdf_viewer.css';
import './style.css';
import type { Context, EditorAdapter } from '../../context/types';
import { persist, saved } from '../../context/types';
import { askText } from '../../workspace/nav';
import { isAiAskContent, parseAskThread } from './ask-ai';
import { openAskAiDialog } from './ask-ai-dialog';

GlobalWorkerOptions.workerSrc = worker;

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 5;
const MAX_PDF = 30 * 1024 * 1024;
/** 与 office-api PdfAnnotBody / pdf_annot 对齐；前端先按行合并再截断。 */
const MAX_ANNOT_BOXES = 200;

/** 合并同行相邻矩形，减少大选区 getClientRects 数量。 */
function mergeSelectionBoxes(boxes: number[][], yTol = 3, xGap = 6): number[][] {
  if (boxes.length <= 1) return boxes.map(b => [...b]);
  const sorted = boxes
    .map(b => [...b] as number[])
    .sort((a, b) => {
      const ay = (a[1] + a[3]) / 2;
      const by = (b[1] + b[3]) / 2;
      if (Math.abs(ay - by) > yTol) return ay - by;
      return a[0] - b[0];
    });
  const out: number[][] = [];
  for (const box of sorted) {
    const last = out[out.length - 1];
    if (!last) {
      out.push(box);
      continue;
    }
    const ly = (last[1] + last[3]) / 2;
    const by = (box[1] + box[3]) / 2;
    if (Math.abs(ly - by) <= yTol && box[0] <= last[2] + xGap) {
      last[0] = Math.min(last[0], box[0]);
      last[1] = Math.min(last[1], box[1]);
      last[2] = Math.max(last[2], box[2]);
      last[3] = Math.max(last[3], box[3]);
    } else {
      out.push(box);
    }
  }
  if (out.length <= MAX_ANNOT_BOXES) return out;
  const group = Math.ceil(out.length / MAX_ANNOT_BOXES);
  const capped: number[][] = [];
  for (let i = 0; i < out.length; i += group) {
    const chunk = out.slice(i, i + group);
    capped.push([
      Math.min(...chunk.map(b => b[0])),
      Math.min(...chunk.map(b => b[1])),
      Math.max(...chunk.map(b => b[2])),
      Math.max(...chunk.map(b => b[3])),
    ]);
  }
  return capped.slice(0, MAX_ANNOT_BOXES);
}

type AnnotKind = 'highlight' | 'text';
type LiveAnnot = { xref: number; markXref?: number; kind: AnnotKind; page: number; boxes: number[][]; content: string; aiAsk?: boolean };
type PaneId = 'main' | 'copy';
type Viewport = {
  width: number;
  height: number;
  convertToViewportPoint: (x: number, y: number) => number[];
  convertToPdfPoint: (x: number, y: number) => number[];
};
type Pane = {
  id: PaneId;
  root: HTMLElement;
  scroller: HTMLDivElement;
  pages: HTMLDivElement;
  viewer: PDFViewer;
  eventBus: EventBus;
  linkService: PDFLinkService;
  lastWidth: number;
  ready: boolean;
};

function xrefOf(id: string) {
  const xref = Number.parseInt(id, 10);
  return Number.isInteger(xref) && xref > 0 ? xref : null;
}

function alertMessage(title: string) {
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = `<div class="name-dialog" role="alertdialog"><h2></h2><div class="dialog-actions"><button type="button" class="primary" data-ok>确定</button></div></div>`;
  overlay.querySelector('h2')!.textContent = title;
  const close = () => overlay.remove();
  overlay.querySelector('[data-ok]')!.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  document.body.append(overlay);
  overlay.querySelector<HTMLButtonElement>('[data-ok]')!.focus();
}

function askDelete(title: string) {
  return new Promise<boolean>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog"><h2></h2><div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" data-delete>删除</button></div></form>`;
    overlay.querySelector('h2')!.textContent = title;
    const finish = (value: boolean) => { overlay.remove(); resolve(value); };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(false));
    overlay.querySelector('[data-delete]')!.addEventListener('click', () => finish(true));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
    overlay.querySelector('form')!.addEventListener('submit', e => { e.preventDefault(); finish(true); });
    document.body.append(overlay);
  });
}

function askAnnot(title: string, initial: string) {
  return new Promise<'delete' | string | null>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<form class="name-dialog name-dialog-note"><h2></h2><textarea required rows="6"></textarea><div class="dialog-actions"><button type="button" data-delete>删除</button><button type="button" data-cancel>取消</button><button class="primary">保存</button></div></form>`;
    overlay.querySelector('h2')!.textContent = title;
    const input = overlay.querySelector('textarea')!;
    input.placeholder = '批注内容';
    input.value = initial;
    input.maxLength = 500;
    const finish = (value: 'delete' | string | null) => { overlay.remove(); resolve(value); };
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => finish(null));
    overlay.querySelector('[data-delete]')!.addEventListener('click', () => finish('delete'));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    overlay.querySelector('form')!.addEventListener('submit', e => {
      e.preventDefault();
      const name = input.value.trim();
      if (name) finish(name);
    });
    input.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        overlay.querySelector('form')!.requestSubmit();
      }
    });
    document.body.append(overlay);
    input.focus();
    if (initial) input.setSelectionRange(0, input.value.length);
  });
}

function cloneBuffer(data: ArrayBuffer) {
  return data.slice(0);
}

function createPane(id: PaneId, root: HTMLElement, scroller: HTMLDivElement): Pane {
  const pages = document.createElement('div');
  pages.className = 'pdfViewer';
  scroller.append(pages);
  const eventBus = new EventBus();
  const linkService = new PDFLinkService({ eventBus });
  const viewer = new PDFViewer({
    container: scroller, viewer: pages, eventBus, linkService,
    annotationMode: AnnotationMode.ENABLE, imageResourcesPath: '/pdfjs/images/', enableAutoLinking: false,
  });
  linkService.setViewer(viewer);
  return { id, root, scroller, pages, viewer, eventBus, linkService, lastWidth: 0, ready: false };
}

export function mountPdf(container: HTMLElement, changed: () => void, hooks: {
  onPickFile: (file: File) => Promise<void>;
  fetchFile: (id: string) => Promise<ArrayBuffer | null>;
  onAnnotate: (body: { kind: AnnotKind; page: number; boxes: number[][]; content?: string }) => Promise<{ xref: number; mark_xref?: number } | void>;
  onUpdateAnnot: (xref: number, content: string) => Promise<void>;
  onDeleteAnnot: (xref: number) => Promise<{ deleted?: number[] } | void>;
  userName: () => string;
  modelName: () => string;
  askAi: (body: {
    selection: string;
    question: string;
    page: number;
    document_id: string;
    filename: string;
    history: { role: 'user' | 'assistant'; content: string }[];
    translate?: boolean;
  }, onEvent: (event: Record<string, any>) => void) => Promise<void>;
}): EditorAdapter {
  let pdf: PDFDocumentProxy | undefined;
  let copyPdf: PDFDocumentProxy | undefined;
  let loadingTask: PDFDocumentLoadingTask | undefined;
  let copyLoadingTask: PDFDocumentLoadingTask | undefined;
  let fileBytes: ArrayBuffer | null = null;
  let filename = '';
  let documentId = '';
  let selected: Context | null = null;
  let selectionVersion = 0;
  let busy = false;
  let opening = false;
  let split = false;
  let pendingSplit: boolean | null = null;
  let activeId: PaneId = 'main';
  let liveAnnots: LiveAnnot[] = [];

  const stage = container.querySelector<HTMLElement>('.pdf-stage')!;
  const mainRoot = container.querySelector<HTMLElement>('.pdf-pane[data-pane="main"]')!;
  const copyRoot = container.querySelector<HTMLElement>('.pdf-pane[data-pane="copy"]')!;
  const paneSplit = container.querySelector<HTMLElement>('.pdf-split')!;
  const mainScroller = mainRoot.querySelector<HTMLDivElement>('.pdf-scroll')!;
  const copyScroller = copyRoot.querySelector<HTMLDivElement>('.pdf-scroll')!;
  const empty = mainScroller.firstElementChild!;
  const counter = container.querySelector<HTMLElement>('[data-page]')!;
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const pageInput = container.querySelector<HTMLInputElement>('[data-page-input]')!;
  const total = container.querySelector<HTMLElement>('[data-page-total]')!;
  const prev = container.querySelector<HTMLButtonElement>('[data-prev]')!;
  const next = container.querySelector<HTMLButtonElement>('[data-next]')!;
  const zoomOut = container.querySelector<HTMLButtonElement>('[data-zoom-out]')!;
  const zoomIn = container.querySelector<HTMLButtonElement>('[data-zoom-in]')!;
  const zoomLabel = container.querySelector<HTMLElement>('[data-zoom]')!;
  const fitWidth = container.querySelector<HTMLButtonElement>('[data-fit-width]')!;
  const splitBtn = container.querySelector<HTMLButtonElement>('[data-split-view]')!;
  const askAiBtn = container.querySelector<HTMLButtonElement>('[data-ask-ai]')!;
  const annotateButtons = [...container.querySelectorAll<HTMLButtonElement>('[data-annotate]')];
  const error = (detail: string) => window.dispatchEvent(new CustomEvent('office-error', { detail }));
  const SPLIT_RATIO_KEY = 'office-pdf-split-ratio';
  const MIN_PANE = 120;
  let splitRatio = (() => {
    const raw = Number(localStorage.getItem(SPLIT_RATIO_KEY));
    return Number.isFinite(raw) && raw > 0 && raw < 1 ? raw : 0.5;
  })();

  function clampSplitRatio(ratio: number) {
    const total = stage.clientWidth || 1;
    const gutter = split ? (paneSplit.clientWidth || 6) : 6;
    const minRatio = Math.min(0.45, MIN_PANE / total);
    const maxRatio = Math.max(minRatio, 1 - minRatio - gutter / total);
    return Math.min(maxRatio, Math.max(minRatio, ratio));
  }
  function refreshSplitPanes() {
    for (const pane of [main, copy]) {
      if (!pane.ready || pane.root.hidden) continue;
      if (pane.viewer.currentScaleValue === 'page-width') fitToWidth(pane);
      else pane.viewer.update();
    }
  }
  function applySplitRatio(ratio: number, save = false) {
    splitRatio = clampSplitRatio(ratio);
    stage.style.setProperty('--pdf-copy-width', `${(splitRatio * 100).toFixed(2)}%`);
    paneSplit.setAttribute('aria-valuenow', String(Math.round(splitRatio * 100)));
    if (save) {
      try { localStorage.setItem(SPLIT_RATIO_KEY, String(splitRatio)); } catch { /* ignore */ }
    }
    return splitRatio;
  }
  applySplitRatio(splitRatio);
  paneSplit.setAttribute('aria-valuemin', '15');
  paneSplit.setAttribute('aria-valuemax', '85');
  let draggingSplit = false;
  paneSplit.addEventListener('pointerdown', event => {
    if (!split || event.button !== 0) return;
    event.preventDefault();
    draggingSplit = true;
    container.classList.add('resizing-split');
    try { paneSplit.setPointerCapture(event.pointerId); } catch { /* ignore */ }
  });
  const onSplitMove = (event: PointerEvent) => {
    if (!draggingSplit || !split) return;
    const rect = stage.getBoundingClientRect();
    if (!(rect.width > 0)) return;
    applySplitRatio((event.clientX - rect.left) / rect.width, true);
  };
  paneSplit.addEventListener('pointermove', onSplitMove);
  window.addEventListener('pointermove', onSplitMove);
  const stopSplitDrag = () => {
    if (!draggingSplit) return;
    draggingSplit = false;
    container.classList.remove('resizing-split');
    if (split) refreshSplitPanes();
  };
  paneSplit.addEventListener('pointerup', stopSplitDrag);
  paneSplit.addEventListener('pointercancel', stopSplitDrag);
  window.addEventListener('pointerup', stopSplitDrag);
  paneSplit.addEventListener('dblclick', () => {
    if (!split) return;
    applySplitRatio(0.5, true);
    refreshSplitPanes();
  });
  paneSplit.addEventListener('keydown', event => {
    if (!split) return;
    const step = event.shiftKey ? 0.05 : 0.02;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      applySplitRatio(splitRatio - step, true);
      refreshSplitPanes();
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      applySplitRatio(splitRatio + step, true);
      refreshSplitPanes();
    }
  });

  const main = createPane('main', mainRoot, mainScroller);
  const copy = createPane('copy', copyRoot, copyScroller);
  const panes = () => (split ? [main, copy] : [main]);
  const active = () => (activeId === 'copy' && split ? copy : main);
  const paneOf = (node: Node | null) => {
    const el = node instanceof Element ? node : node?.parentElement;
    const root = el?.closest<HTMLElement>('.pdf-pane');
    if (root?.dataset.pane === 'copy') return copy;
    if (root?.dataset.pane === 'main') return main;
    return null;
  };

  function setActive(id: PaneId) {
    if (!split && id === 'copy') return;
    activeId = id;
    main.root.classList.toggle('is-active', activeId === 'main');
    copy.root.classList.toggle('is-active', split && activeId === 'copy');
    controls();
  }

  function selectionBoxes() {
    const boxes = selected?.selection?.boxes as number[][] | undefined;
    const page = selected?.selection?.page as number | undefined;
    if (!selected || !page || !boxes?.length) return null;
    return { page, boxes };
  }
  function clearSelection() {
    selectionVersion++;
    selected = null;
    changed();
  }
  function fitToWidth(pane = active()) {
    if (!pane.ready || !pane.scroller.clientWidth) return;
    pane.viewer.currentScaleValue = 'page-width';
  }
  function zoomTo(scale: number, origin?: number[], pane = active()) {
    if (!pane.ready || busy) return;
    const nextScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
    const current = pane.viewer.currentScale;
    if (!(current > 0) || Math.abs(nextScale - current) < 1e-4) { controls(); return; }
    pane.viewer.updateScale({ scaleFactor: nextScale / current, origin, drawingDelay: 80 });
  }
  function controls() {
    const pane = active();
    const current = pane.viewer.currentPageNumber || 1;
    const count = pdf?.numPages ?? 0;
    const scale = pane.ready ? pane.viewer.currentScale : 1;
    prev.disabled = busy || !pane.ready || current <= 1;
    next.disabled = busy || !pane.ready || current >= count;
    pageInput.disabled = busy || !pane.ready;
    pageInput.max = String(count || 1);
    pageInput.value = String(current);
    total.textContent = '/ ' + count;
    counter.textContent = busy ? '正在打开…' : filename || '未打开文件';
    counter.title = filename;
    zoomLabel.textContent = Math.round(scale * 100) + '%';
    zoomOut.disabled = busy || !pane.ready || scale <= MIN_ZOOM + 1e-6;
    zoomIn.disabled = busy || !pane.ready || scale >= MAX_ZOOM - 1e-6;
    fitWidth.disabled = busy || !pane.ready;
    annotateButtons.forEach(button => { button.disabled = busy || !main.ready || !documentId; });
    askAiBtn.disabled = busy || !main.ready || !documentId;
    splitBtn.disabled = busy || !main.ready || !documentId || !fileBytes;
    splitBtn.setAttribute('aria-pressed', split ? 'true' : 'false');
    splitBtn.textContent = split ? '关闭浏览' : '复制浏览';
    splitBtn.title = split ? '关闭左侧对照窗格' : '左右对照同一份 PDF';
  }
  function whenVisible() {
    if (!container.hidden && main.scroller.clientWidth >= 8) return Promise.resolve();
    return new Promise<void>(resolve => {
      const done = () => {
        if (container.hidden || main.scroller.clientWidth < 8) return;
        obs.disconnect();
        ro.disconnect();
        resolve();
      };
      const obs = new MutationObserver(done);
      obs.observe(container, { attributes: true, attributeFilter: ['hidden'] });
      const ro = new ResizeObserver(done);
      ro.observe(main.scroller);
    });
  }
  function viewKey(id = documentId) {
    return id ? `office-pdf-view:${id}` : '';
  }
  function scaleOf(pane: Pane) {
    const scaleValue = pane.viewer.currentScaleValue;
    return scaleValue === 'page-width' ? 'page-width' : pane.viewer.currentScale;
  }
  function rememberView() {
    const key = viewKey();
    if (!key || opening || busy || !main.ready || container.hidden) return;
    const prev = saved(key) || {};
    persist(key, {
      page: main.viewer.currentPageNumber || 1,
      scale: scaleOf(main),
      split: pendingSplit ?? split,
      active: split ? activeId : 'main',
      copyPage: split && copy.ready ? (copy.viewer.currentPageNumber || 1) : prev.copyPage,
      copyScale: split && copy.ready ? scaleOf(copy) : prev.copyScale,
    });
  }
  function applyStoredScale(pane: Pane, scale: unknown) {
    if (scale === 'page-width' || !(typeof scale === 'number' && scale >= MIN_ZOOM && scale <= MAX_ZOOM)) {
      fitToWidth(pane);
      return;
    }
    if (Math.abs(pane.viewer.currentScale - scale) > 1e-4) pane.viewer.currentScale = scale;
  }
  function jumpToStoredPage(pane: Pane, page: unknown, pageCount: number) {
    if (!Number.isInteger(page) || (page as number) < 1 || (page as number) > pageCount) return;
    if (page !== pane.viewer.currentPageNumber) pane.viewer.currentPageNumber = page as number;
  }

  function wirePane(pane: Pane) {
    pane.eventBus.on('pagesinit', () => {
      pane.ready = true;
      pane.viewer.scrollMode = ScrollMode.VERTICAL;
      if (pane.id === 'main') {
        const stored = saved(viewKey());
        const legacyPage = Number(saved(documentId ? `office-pdf-page:${documentId}` : ''));
        const page = typeof stored?.page === 'number' ? stored.page
          : (Number.isInteger(legacyPage) ? legacyPage : 0);
        applyStoredScale(pane, stored?.scale);
        if (stored?.split) pendingSplit = true;
        jumpToStoredPage(pane, page, pdf?.numPages || 0);
        opening = false;
        if (stored?.split) {
          const focus: PaneId = stored.active === 'main' ? 'main' : 'copy';
          void setSplit(true, focus).finally(() => { pendingSplit = null; });
        }
      } else {
        const stored = saved(viewKey());
        applyStoredScale(pane, stored?.copyScale);
        jumpToStoredPage(pane, stored?.copyPage, copyPdf?.numPages || 0);
      }
      controls();
    });
    pane.eventBus.on('pagechanging', () => {
      if (active() === pane) { clearSelection(); controls(); }
      rememberView();
    });
    pane.eventBus.on('scalechanging', () => {
      if (active() === pane) controls();
      rememberView();
    });
    pane.eventBus.on('pagerendered', ({ error: renderError }: { error?: Error }) => {
      if (renderError) error('PDF 页面渲染失败：' + renderError.message);
    });
    pane.eventBus.on('annotationlayerrendered', ({ source }: { source: { div: HTMLElement } }) => {
      void bindAnnotUi(source.div, pane).catch(e => error(String(e)));
      paintLive(source.div, Number(source.div.dataset.pageNumber), pane);
    });
    pane.scroller.addEventListener('pointerdown', () => setActive(pane.id));
    pane.scroller.addEventListener('focusin', () => setActive(pane.id));
    pane.scroller.addEventListener('mouseup', scheduleCapture);
    pane.scroller.addEventListener('keyup', scheduleCapture);
    pane.scroller.addEventListener('dblclick', scheduleCapture);
    pane.scroller.addEventListener('wheel', e => {
      if (!pane.ready || busy || !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setActive(pane.id);
      zoomTo(pane.viewer.currentScale * (e.deltaY < 0 ? 1.1 : 1 / 1.1), [e.clientX, e.clientY], pane);
    }, { passive: false });
    new ResizeObserver(() => {
      if (!pane.ready || container.hidden || pane.root.hidden || !pane.scroller.clientWidth) return;
      const width = pane.scroller.clientWidth;
      const appeared = pane.lastWidth < 8 && width >= 8;
      pane.lastWidth = width;
      if (pane.id === 'main') {
        if (appeared) {
          applyStoredScale(pane, saved(viewKey())?.scale);
          pane.viewer.update();
        } else if (pane.viewer.currentScaleValue === 'page-width') {
          fitToWidth(pane);
        }
        return;
      }
      if (appeared) {
        if (pane.viewer.currentScaleValue === 'page-width') fitToWidth(pane);
        pane.viewer.update();
      } else if (pane.viewer.currentScaleValue === 'page-width') {
        fitToWidth(pane);
      }
    }).observe(pane.scroller);
  }
  wirePane(main);
  wirePane(copy);

  new MutationObserver(() => {
    if (container.hidden) {
      main.lastWidth = 0;
      copy.lastWidth = 0;
    }
  }).observe(container, { attributes: true, attributeFilter: ['hidden'] });

  async function captureSelection() {
    const snap = readDomSelection();
    if (!snap) {
      if (!window.getSelection()?.toString().trim()) clearSelection();
      return;
    }
    selectionVersion++;
    const version = selectionVersion;
    selected = {
      document_id: documentId, surface: 'pdf' as const, revision: 0,
      selection: { page: snap.page, filename, text: snap.text, boxes: snap.boxes },
      surrounding: selected?.selection?.page === snap.page ? (selected.surrounding || '') : '',
    };
    changed();
    try {
      const content = await (await pdf!.getPage(snap.page)).getTextContent();
      if (version !== selectionVersion || !content || !selected) return;
      selected = {
        ...selected,
        surrounding: content.items.map(item => 'str' in item ? item.str : '').join(' ').slice(0, 20000),
      };
      changed();
    } catch (e) {
      error(e instanceof Error ? e.message : String(e));
    }
  }
  function scheduleCapture() {
    requestAnimationFrame(() => { void captureSelection().catch(e => error(String(e))); });
  }
  function readDomSelection() {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !selection.toString().trim()) return null;
    const start = selection.anchorNode instanceof Element
      ? selection.anchorNode.closest<HTMLElement>('.page')
      : selection.anchorNode?.parentElement?.closest<HTMLElement>('.page');
    const end = selection.focusNode instanceof Element
      ? selection.focusNode.closest<HTMLElement>('.page')
      : selection.focusNode?.parentElement?.closest<HTMLElement>('.page');
    if (!start || !end) return null;
    const pane = paneOf(start);
    if (!pane || !pane.pages.contains(start)) return null;
    if (start !== end) { error('请在同一页内选取文字，以便准确引用页码。'); return null; }
    setActive(pane.id);
    const number = Number(start.dataset.pageNumber);
    const pageView = pane.viewer.getPageView(number - 1);
    const layer = start.querySelector<HTMLElement>('.textLayer');
    if (!layer || !layer.contains(selection.anchorNode) || !layer.contains(selection.focusNode)) return null;
    const bounds = layer.getBoundingClientRect();
    const viewport = pageView.viewport as Viewport;
    const boxes = mergeSelectionBoxes(
      Array.from(selection.getRangeAt(0).getClientRects())
        .filter(rect => rect.width > 0 && rect.height > 0)
        .map(rect => {
          const a = viewport.convertToPdfPoint(rect.left - bounds.left, rect.top - bounds.top);
          const b = viewport.convertToPdfPoint(rect.right - bounds.left, rect.bottom - bounds.top);
          return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
        }),
    );
    if (!boxes.length) return null;
    return { page: number, boxes, text: selection.toString() };
  }
  container.addEventListener('keydown', e => {
    if (!active().ready || busy || !(e.ctrlKey || e.metaKey)) return;
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key === '=' || e.key === '+') { e.preventDefault(); zoomTo(active().viewer.currentScale * 1.1); }
    if (e.key === '-') { e.preventDefault(); zoomTo(active().viewer.currentScale / 1.1); }
    if (e.key === '0') { e.preventDefault(); fitToWidth(); }
  });

  async function withBusy(task: () => Promise<void>) {
    try {
      busy = true; controls();
      await task();
    } catch (e) { error(e instanceof Error ? e.message : String(e)); }
    finally { busy = false; controls(); }
  }
  function boxStyle(viewport: Viewport, box: number[]) {
    const a = viewport.convertToViewportPoint(box[0], box[1]);
    const b = viewport.convertToViewportPoint(box[2], box[3]);
    const left = Math.min(a[0], b[0]);
    const top = Math.min(a[1], b[1]);
    return {
      left: `${(left / viewport.width) * 100}%`,
      top: `${(top / viewport.height) * 100}%`,
      width: `${(Math.abs(b[0] - a[0]) / viewport.width) * 100}%`,
      height: `${(Math.abs(b[1] - a[1]) / viewport.height) * 100}%`,
    };
  }
  function layoutNoteBadges(pageDiv: HTMLElement) {
    const notes = [...pageDiv.querySelectorAll<HTMLElement>('.pdf-live-note, .annotationLayer .textAnnotation.pdf-note-badge')];
    if (!notes.length) return;
    const rows = new Map<number, HTMLElement[]>();
    for (const note of notes) {
      const top = Math.round(note.offsetTop / 14) * 14;
      const row = rows.get(top) || [];
      row.push(note);
      rows.set(top, row);
    }
    for (const row of rows.values()) {
      row.sort((a, b) => {
        const ax = Number(a.dataset.xref || a.dataset.annotationId?.replace(/R$/, '') || 0);
        const bx = Number(b.dataset.xref || b.dataset.annotationId?.replace(/R$/, '') || 0);
        return ax - bx;
      });
      let left = 8;
      const top = Math.min(...row.map(note => note.offsetTop));
      for (const note of row) {
        const width = Math.max(note.offsetWidth || 0, note.textContent === '问AI' ? 40 : 36);
        note.style.left = `${left}px`;
        note.style.top = `${top}px`;
        left += width + 4;
      }
    }
  }
  function paintLive(pageDiv: HTMLElement, pageNumber: number, pane: Pane) {
    if (!pageNumber) return;
    pageDiv.querySelectorAll('.pdf-live-annot').forEach(node => node.remove());
    const pageView = pane.viewer.getPageView(pageNumber - 1);
    const viewport = pageView?.viewport as Viewport | undefined;
    if (!viewport) return;
    for (const annot of liveAnnots.filter(item => item.page === pageNumber)) {
      const open = (event: Event) => {
        event.preventDefault();
        event.stopPropagation();
        setActive(pane.id);
        void manageAnnot(String(annot.xref), annot.kind, annot.page).catch(e => error(String(e)));
      };
      if (annot.kind === 'highlight') {
        annot.boxes.forEach(box => {
          const mark = document.createElement('div');
          mark.className = 'pdf-live-annot pdf-live-highlight';
          mark.dataset.xref = String(annot.xref);
          Object.assign(mark.style, boxStyle(viewport, box));
          pageDiv.append(mark);
        });
        const first = boxStyle(viewport, annot.boxes[0]);
        const handle = document.createElement('button');
        handle.type = 'button';
        handle.className = 'pdf-live-annot pdf-annot-handle';
        handle.dataset.xref = String(annot.xref);
        handle.title = '删除高亮';
        handle.setAttribute('aria-label', '删除高亮');
        handle.style.left = `calc(${first.left} - 16px)`;
        handle.style.top = first.top;
        handle.addEventListener('mousedown', e => e.preventDefault());
        handle.addEventListener('click', open);
        pageDiv.append(handle);
        continue;
      }
      const first = boxStyle(viewport, annot.boxes[0]);
      annot.boxes.forEach(box => {
        const mark = document.createElement('div');
        mark.className = 'pdf-live-annot pdf-live-highlight';
        mark.dataset.xref = String(annot.xref);
        Object.assign(mark.style, boxStyle(viewport, box));
        pageDiv.append(mark);
      });
      const note = document.createElement('button');
      note.type = 'button';
      note.className = 'pdf-live-annot pdf-live-note' + (annot.aiAsk ? ' pdf-ai-badge' : '');
      note.dataset.xref = String(annot.xref);
      note.textContent = annot.aiAsk ? '问AI' : '批注';
      note.title = annot.content || (annot.aiAsk ? '查看或继续问 AI' : '编辑或删除批注');
      note.setAttribute('aria-label', annot.aiAsk ? '查看或继续问 AI' : '编辑或删除批注');
      note.style.left = '8px';
      note.style.top = first.top;
      note.addEventListener('mousedown', e => e.preventDefault());
      note.addEventListener('click', open);
      pageDiv.append(note);
    }
    layoutNoteBadges(pageDiv);
  }
  function paintLiveEverywhere(pageNumber: number) {
    for (const pane of panes()) {
      const pageDiv = pane.viewer.getPageView(pageNumber - 1)?.div as HTMLElement | undefined;
      if (pageDiv) paintLive(pageDiv, pageNumber, pane);
    }
  }
  function removeAnnotDom(...xrefs: number[]) {
    const ids = [...new Set(xrefs.filter(xref => Number.isInteger(xref) && xref > 0))];
    if (!ids.length) return;
    liveAnnots = liveAnnots.filter(item => !ids.includes(item.xref) && !(item.markXref && ids.includes(item.markXref)));
    for (const pane of panes()) {
      for (const xref of ids) {
        pane.pages.querySelectorAll(`[data-xref="${xref}"], [data-annotation-id="${xref}R"]`).forEach(node => node.remove());
      }
    }
  }
  async function annotate(kind: AnnotKind) {
    if (!selectionBoxes()) {
      const snap = readDomSelection();
      if (snap) {
        selected = {
          document_id: documentId, surface: 'pdf' as const, revision: 0,
          selection: { page: snap.page, filename, text: snap.text, boxes: snap.boxes },
          surrounding: '',
        };
        changed();
      }
    }
    const target = selectionBoxes();
    if (!target) { error('请先在 PDF 中划选文字。'); return; }
    let content = '';
    if (kind === 'text') {
      const note = await askText('文字批注', '批注内容', '', 500, true);
      if (!note) return;
      content = note;
    }
    await withBusy(async () => {
      const result = await hooks.onAnnotate({ kind, page: target.page, boxes: target.boxes, content });
      if (!result?.xref) return;
      liveAnnots.push({
        xref: result.xref,
        markXref: result.mark_xref && result.mark_xref > 0 ? result.mark_xref : undefined,
        kind, page: target.page, boxes: target.boxes, content,
        aiAsk: isAiAskContent(content),
      });
      paintLiveEverywhere(target.page);
      window.getSelection()?.removeAllRanges();
      clearSelection();
    });
  }
  async function annotContents(id: string, pageNumber: number) {
    const live = liveAnnots.find(item => String(item.xref) === id || item.xref === xrefOf(id));
    if (live?.content) return live.content;
    const page = await pdf!.getPage(pageNumber);
    const annots = await page.getAnnotations();
    const found = annots.find(item => item.id === id || item.id === `${xrefOf(id)}R`) as { contents?: string; contentsObj?: { str?: string } } | undefined;
    if (typeof found?.contents === 'string' && found.contents.trim()) return found.contents;
    return found?.contentsObj?.str || '';
  }
  async function linkedMarkXrefs(id: string, pageNumber: number) {
    const xref = xrefOf(id);
    if (!xref) return [] as number[];
    const live = liveAnnots.find(item => item.xref === xref);
    if (live?.markXref) return [live.markXref];
    if (!pdf) return [];
    try {
      const page = await pdf.getPage(pageNumber);
      const annots = await page.getAnnotations() as Array<{ id?: string; subject?: string; title?: string; titleObj?: { str?: string } }>;
      const note = annots.find(item => item.id === id || item.id === `${xref}R`);
      const subject = typeof note?.subject === 'string' ? note.subject : '';
      if (subject.startsWith('hl:')) {
        const mark = Number.parseInt(subject.slice(3), 10);
        return Number.isInteger(mark) && mark > 0 ? [mark] : [];
      }
      // PDF.js 可能不暴露 subject：删文字批注时顺带清掉同页 office-text-mark 高亮由服务端 deleted 列表负责
      return [];
    } catch {
      return [];
    }
  }
  async function openAskAi(opts: {
    selection: string;
    page: number;
    boxes: number[][];
    xref?: number | null;
    markXref?: number | null;
    content?: string;
  }) {
    const result = await openAskAiDialog({
      selection: opts.selection,
      page: opts.page,
      document_id: documentId,
      filename,
      boxes: opts.boxes,
      xref: opts.xref,
      markXref: opts.markXref,
      content: opts.content,
    }, {
      userName: hooks.userName() || '用户',
      modelName: hooks.modelName() || '模型',
      ask: hooks.askAi,
      save: async content => hooks.onAnnotate({ kind: 'text', page: opts.page, boxes: opts.boxes, content }),
      update: hooks.onUpdateAnnot,
      remove: hooks.onDeleteAnnot,
    });
    if (result.action === 'closed') return;
    if (result.action === 'deleted') {
      if (result.xref) removeAnnotDom(result.xref, ...(result.deleted || []));
      return;
    }
    if (result.action === 'saved' && result.xref && result.content) {
      const existing = liveAnnots.find(item => item.xref === result.xref);
      if (existing) {
        existing.content = result.content;
        existing.aiAsk = true;
        if (result.markXref) existing.markXref = result.markXref;
        paintLiveEverywhere(opts.page);
        return;
      }
      const box = opts.boxes?.[0];
      const realBoxes = Boolean(box && box[2] - box[0] > 1 && box[3] - box[1] > 0.5);
      if (realBoxes) {
        liveAnnots.push({
          xref: result.xref,
          markXref: result.markXref,
          kind: 'text',
          page: opts.page,
          boxes: opts.boxes,
          content: result.content,
          aiAsk: true,
        });
        paintLiveEverywhere(opts.page);
      }
    }
  }
  async function startAskAi() {
    if (!selectionBoxes()) {
      const snap = readDomSelection();
      if (snap) {
        selected = {
          document_id: documentId, surface: 'pdf' as const, revision: 0,
          selection: { page: snap.page, filename, text: snap.text, boxes: snap.boxes },
          surrounding: '',
        };
        changed();
      }
    }
    const target = selectionBoxes();
    const text = String(selected?.selection?.text || '').trim();
    if (!target || !text) {
      alertMessage('请选中您要提问的文字');
      return;
    }
    await openAskAi({ selection: text, page: target.page, boxes: target.boxes });
  }
  async function manageAnnot(id: string, kind: AnnotKind, pageNumber: number) {
    const xref = xrefOf(id);
    if (!xref || busy) return;
    if (kind === 'highlight') {
      if (!await askDelete('删除这个高亮？')) return;
      await withBusy(async () => {
        const result = await hooks.onDeleteAnnot(xref);
        removeAnnotDom(xref, ...(result?.deleted || []));
      });
      return;
    }
    const live = liveAnnots.find(item => item.xref === xref);
    const current = live?.content || await annotContents(id, pageNumber);
    if (live?.aiAsk || isAiAskContent(current)) {
      const parsed = isAiAskContent(current) ? parseAskThread(current) : null;
      await openAskAi({
        selection: parsed?.selection || String(selected?.selection?.text || ''),
        page: pageNumber,
        boxes: live?.boxes || [[0, 0, 1, 1]],
        xref,
        markXref: live?.markXref,
        content: current,
      });
      return;
    }
    const nextValue = await askAnnot('编辑批注', current);
    if (nextValue === null) return;
    await withBusy(async () => {
      if (nextValue === 'delete') {
        const marks = await linkedMarkXrefs(id, pageNumber);
        const result = await hooks.onDeleteAnnot(xref);
        removeAnnotDom(xref, ...marks, ...(result?.deleted || []));
        return;
      }
      await hooks.onUpdateAnnot(xref, nextValue);
      const item = liveAnnots.find(entry => entry.xref === xref);
      if (item) item.content = nextValue;
      for (const pane of panes()) {
        pane.pages.querySelectorAll<HTMLElement>(`[data-xref="${xref}"], [data-annotation-id="${xref}R"]`).forEach(node => {
          node.title = nextValue;
        });
      }
    });
  }
  async function bindAnnotUi(pageDiv: HTMLElement, pane: Pane) {
    pageDiv.querySelectorAll('.pdf-annot-handle:not(.pdf-live-annot)').forEach(node => node.remove());
    const layer = pageDiv.querySelector('.annotationLayer');
    if (!layer || !pdf) return;
    const pageNumber = Number(pageDiv.dataset.pageNumber);
    if (!pageNumber) return;
    const page = await pdf.getPage(pageNumber);
    const meta = await page.getAnnotations();
    const byId = new Map(meta.map(item => [item.id, item as {
      title?: string; titleObj?: { str?: string }; contents?: string; contentsObj?: { str?: string };
    }]));
    const titleOf = (id: string) => {
      const item = byId.get(id);
      if (!item) return '';
      if (typeof item.title === 'string' && item.title) return item.title;
      return item.titleObj?.str || '';
    };
    const contentsOf = (id: string) => {
      const item = byId.get(id);
      if (!item) return '';
      if (typeof item.contents === 'string' && item.contents.trim()) return item.contents;
      return item.contentsObj?.str || '';
    };
    layer.querySelectorAll<HTMLElement>('[data-annotation-id]').forEach(section => {
      if (section.classList.contains('popupAnnotation')) return;
      const id = section.dataset.annotationId;
      if (!id) return;
      const xref = xrefOf(id);
      if (xref && liveAnnots.some(item => item.xref === xref)) {
        section.remove();
        return;
      }
      const kind: AnnotKind | null = section.classList.contains('textAnnotation') ? 'text'
        : section.classList.contains('highlightAnnotation') ? 'highlight' : null;
      if (!kind) return;
      const open = (event: Event) => {
        event.preventDefault();
        event.stopPropagation();
        setActive(pane.id);
        void manageAnnot(id, kind, pageNumber).catch(e => error(String(e)));
      };
      if (kind === 'text') {
        const aiAsk = titleOf(id) === 'office-ai-ask' || isAiAskContent(contentsOf(id));
        section.classList.add('pdf-note-badge');
        if (aiAsk) section.classList.add('pdf-ai-badge');
        section.replaceChildren();
        section.textContent = aiAsk ? '问AI' : '批注';
        section.title = aiAsk ? '查看或继续问 AI' : '编辑或删除批注';
        section.addEventListener('click', open, true);
        return;
      }
      if (titleOf(id) === 'office-text-mark') return;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'pdf-annot-handle';
      handle.dataset.xref = xref ? String(xref) : '';
      handle.title = '删除高亮';
      handle.setAttribute('aria-label', '删除高亮');
      handle.style.left = `${Math.max(2, section.offsetLeft - 18)}px`;
      handle.style.top = `${section.offsetTop + 2}px`;
      handle.addEventListener('mousedown', e => e.preventDefault());
      handle.addEventListener('click', open);
      pageDiv.append(handle);
    });
    layoutNoteBadges(pageDiv);
  }

  async function destroyCopy() {
    copy.ready = false;
    const previous = copyLoadingTask;
    copyLoadingTask = undefined;
    copyPdf = undefined;
    try { copy.viewer.setDocument(null as never); } catch { /* viewer 无文档 */ }
    copy.pages.replaceChildren();
    await previous?.destroy();
  }

  async function openCopy() {
    if (!fileBytes) return;
    const task = getDocument({
      data: new Uint8Array(cloneBuffer(fileBytes)),
      cMapUrl: '/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/',
    });
    const previous = copyLoadingTask;
    copyLoadingTask = task;
    copy.ready = false;
    try {
      const loaded = await task.promise;
      if (copyLoadingTask !== task) {
        await task.destroy();
        return;
      }
      copyPdf = loaded;
      copy.linkService.setDocument(loaded);
      copy.viewer.setDocument(loaded);
    } catch (e) {
      if (copyLoadingTask === task) copyLoadingTask = undefined;
      await task.destroy();
      throw e;
    } finally {
      await previous?.destroy();
    }
  }

  async function setSplit(on: boolean, focus: PaneId = 'copy') {
    if (on === split) return;
    if (on) {
      if (!main.ready || !fileBytes) return;
      split = true;
      stage.classList.add('is-split');
      copyRoot.hidden = false;
      paneSplit.hidden = false;
      applySplitRatio(splitRatio);
      setActive(focus);
      controls();
      try {
        await openCopy();
        refreshSplitPanes();
        rememberView();
      } catch (e) {
        split = false;
        stage.classList.remove('is-split');
        copyRoot.hidden = true;
        paneSplit.hidden = true;
        setActive('main');
        error('无法打开对照浏览：' + String(e));
      }
      controls();
      return;
    }
    split = false;
    stage.classList.remove('is-split');
    copyRoot.hidden = true;
    paneSplit.hidden = true;
    container.classList.remove('resizing-split');
    draggingSplit = false;
    setActive('main');
    await destroyCopy();
    main.lastWidth = 0;
    if (main.ready) main.viewer.update();
    rememberView();
    controls();
  }

  async function openBuffer(id: string, name: string, data: ArrayBuffer) {
    busy = true; opening = true; input.disabled = true; liveAnnots = []; clearSelection(); controls();
    let task: PDFDocumentLoadingTask | undefined;
    try {
      if (split) await setSplit(false);
      fileBytes = cloneBuffer(data);
      task = getDocument({ data: new Uint8Array(cloneBuffer(fileBytes)),
        cMapUrl: '/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/' });
      const loaded = await task.promise;
      main.ready = false;
      const previous = loadingTask;
      loadingTask = task; pdf = loaded;
      filename = name; documentId = id;
      empty.remove();
      main.linkService.setDocument(loaded);
      main.viewer.setDocument(loaded);
      await previous?.destroy();
    } catch (e) {
      opening = false;
      fileBytes = null;
      if (task !== loadingTask) await task?.destroy();
      error('PDF 打开失败：' + String(e));
    } finally { busy = false; input.disabled = false; input.value = ''; controls(); }
  }
  async function showEmpty(id = '', name = '') {
    if (split) await setSplit(false);
    main.ready = false; pdf = undefined; fileBytes = null; filename = name; documentId = id; selected = null; liveAnnots = [];
    const previous = loadingTask; loadingTask = undefined;
    try { main.viewer.setDocument(null as never); } catch { /* viewer 无文档 */ }
    if (!empty.isConnected) main.scroller.prepend(empty);
    await previous?.destroy();
    controls(); changed();
  }
  input.onchange = async () => {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > MAX_PDF) { error('MVP 支持 30 MB 以内的 PDF。'); return; }
    try { await hooks.onPickFile(file); } catch (e) { error(e instanceof Error ? e.message : String(e)); }
  };
  function goTo(number: number) {
    const pane = active();
    if (!pane.ready || busy || !pdf) return;
    if (!Number.isInteger(number) || number < 1 || number > pdf.numPages) {
      pageInput.value = String(pane.viewer.currentPageNumber);
      error('页码应为 1 至 ' + pdf.numPages + ' 的整数。');
      return;
    }
    clearSelection();
    window.getSelection()?.removeAllRanges();
    pane.viewer.scrollPageIntoView({ pageNumber: number });
    controls();
  }
  prev.onclick = () => goTo(active().viewer.currentPageNumber - 1);
  next.onclick = () => goTo(active().viewer.currentPageNumber + 1);
  pageInput.onchange = () => goTo(Number(pageInput.value));
  pageInput.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); goTo(Number(pageInput.value)); }
    if (e.key === 'Escape') pageInput.value = String(active().viewer.currentPageNumber);
  };
  zoomOut.onclick = () => zoomTo(active().viewer.currentScale / 1.1);
  zoomIn.onclick = () => zoomTo(active().viewer.currentScale * 1.1);
  fitWidth.onclick = () => fitToWidth();
  splitBtn.onclick = () => { void setSplit(!split); };
  window.addEventListener('pagehide', () => rememberView());
  askAiBtn.addEventListener('mousedown', e => e.preventDefault());
  askAiBtn.onclick = () => { void startAskAi().catch(e => error(String(e))); };
  annotateButtons.forEach(button => {
    button.addEventListener('mousedown', e => e.preventDefault());
    button.onclick = () => { void annotate(button.dataset.annotate as AnnotKind); };
  });
  controls();
  return {
    context: () => selected,
    anchor: () => null,
    apply() { throw new Error('PDF 不支持把助手修改写回正文，请用工具栏添加批注。'); },
    undo() {},
    export() {},
    async load(asset) {
      if (!asset.id) { await showEmpty(); return; }
      documentId = asset.id;
      filename = asset.name;
      const [data] = await Promise.all([hooks.fetchFile(asset.id), whenVisible()]);
      if (!data) { await showEmpty(asset.id, asset.name); return; }
      await openBuffer(asset.id, asset.filename || `${asset.name}.pdf`, data);
    },
  };
}
