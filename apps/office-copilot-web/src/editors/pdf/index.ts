import { AnnotationMode, getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type PDFDocumentLoadingTask } from 'pdfjs-dist';
import { EventBus, PDFLinkService, PDFViewer, ScrollMode } from 'pdfjs-dist/web/pdf_viewer.mjs';
import { Editor } from '@tiptap/core';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { createIcons, icons } from 'lucide';
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import 'pdfjs-dist/web/pdf_viewer.css';
import './style.css';
import type { Context, EditorAdapter } from '../../context/types';
import { persist, saved } from '../../context/types';
import { isAiAskContent, parseAskThread } from './ask-ai';
import { openAskAiDialog } from './ask-ai-dialog';
import { openPdfAnnotationDialog, pdfAnnotationPreview } from './annotation-dialog';
import { openOutlineDialog } from './outline-dialog';
import { outlineToTiptap, type PdfOutlineItem } from './outline';
import { bindPdfShots } from './shots';
import { openPdfSummaryDialog } from './summary-dialog';
import { circleBullet, collapsibleListItem, fourLevelBulletTab } from '../rich-text-lists';

GlobalWorkerOptions.workerSrc = worker;

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 5;
const MAX_PDF = 30 * 1024 * 1024;
/** 与 office-backend PdfAnnotBody / pdf_annot 对齐；前端先按行合并再截断。 */
const MAX_ANNOT_BOXES = 200;
const EMPTY_NOTEBOOK = { type: 'doc', content: [{ type: 'paragraph' }] };

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
type PdfAssetViewState = {
  page?: number;
  scale?: number | 'page-width';
  split?: boolean;
  active?: PaneId;
  copyPage?: number;
  copyScale?: number | 'page-width';
  splitRatio?: number;
  notebookOpen?: boolean;
  notebookWidth?: number;
  notebookScrollTop?: number;
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
  onAnnotate: (body: { kind: AnnotKind; page: number; boxes: number[][]; content?: string }) => Promise<{ xref: number; mark_xref?: number; revision?: number } | void>;
  onUpdateAnnot: (xref: number, content: string) => Promise<{ revision?: number } | void>;
  onDeleteAnnot: (xref: number) => Promise<{ deleted?: number[]; revision?: number } | void>;
  saveNotebook: (content: unknown, revision: number) => Promise<number>;
  userName: () => string;
  modelName: () => string;
  askAi: (body: {
    selection: string;
    question: string;
    page: number;
    document_id: string;
    filename: string;
    history: { role: 'user' | 'assistant'; content: string }[];
  }, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  generateOutline: (onEvent: (event: Record<string, any>) => void) => Promise<void>;
  generateSummary: (body: { selection: string; page: number }, onEvent: (event: Record<string, any>) => void) => Promise<void>;
}): EditorAdapter {
  let pdf: PDFDocumentProxy | undefined;
  let copyPdf: PDFDocumentProxy | undefined;
  let loadingTask: PDFDocumentLoadingTask | undefined;
  let copyLoadingTask: PDFDocumentLoadingTask | undefined;
  let fileBytes: ArrayBuffer | null = null;
  let filename = '';
  let documentId = '';
  let assetRevision = 0;
  let notebookContent: unknown = EMPTY_NOTEBOOK;
  let notebookEditor: Editor | null = null;
  let notebookLoading = false;
  let notebookPending: unknown | null = null;
  let notebookSaving: Promise<void> = Promise.resolve();
  let notebookSaveTimer: ReturnType<typeof setTimeout> | undefined;
  let selected: Context | null = null;
  let selectionVersion = 0;
  let busy = false;
  let outlineBusy = false;
  let summaryBusy = false;
  let notebookInsertAt: number | null = null;
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
  const notebookToggle = container.querySelector<HTMLButtonElement>('[data-pdf-notebook]')!;
  const outlineToggle = container.querySelector<HTMLButtonElement>('[data-pdf-outline]')!;
  container.querySelector<HTMLButtonElement>('[data-pdf-note-command="quote"]')?.remove();
  const summaryToggle = document.createElement('button');
  summaryToggle.type = 'button';
  summaryToggle.className = 'pdf-summary-toggle';
  summaryToggle.title = '整理选中文本摘要';
  summaryToggle.setAttribute('aria-label', '整理选中文本摘要');
  summaryToggle.innerHTML = '<i data-lucide="list-collapse"></i>';
  outlineToggle.insertAdjacentElement('afterend', summaryToggle);
  createIcons({ icons });
  const notebookPanel = container.querySelector<HTMLElement>('[data-pdf-notebook-panel]')!;
  const notebookEditorHost = container.querySelector<HTMLElement>('[data-pdf-notebook-editor]')!;
  const notebookSplit = document.createElement('div');
  notebookSplit.className = 'pdf-notebook-split';
  notebookSplit.setAttribute('role', 'separator');
  notebookSplit.setAttribute('aria-orientation', 'vertical');
  notebookSplit.setAttribute('aria-label', '调整笔记本宽度');
  notebookSplit.tabIndex = 0;
  notebookSplit.hidden = true;
  notebookPanel.insertAdjacentElement('afterend', notebookSplit);
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
  const MIN_PANE = 120;
  const MIN_NOTEBOOK_WIDTH = 240;
  let notebookWidth = 360;
  let splitRatio = 0.5;
  let notebookScrollTimer: ReturnType<typeof setTimeout> | undefined;

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
    if (save) persistPdfViewState({ splitRatio });
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

  function clampNotebookWidth(width: number) {
    const max = Math.max(MIN_NOTEBOOK_WIDTH, Math.min(620, (stage.clientWidth || 1000) * 0.55));
    return Math.min(max, Math.max(MIN_NOTEBOOK_WIDTH, width));
  }
  function applyNotebookWidth(width: number, save = false) {
    notebookWidth = clampNotebookWidth(width);
    stage.style.setProperty('--pdf-notebook-width', `${Math.round(notebookWidth)}px`);
    notebookSplit.setAttribute('aria-valuemin', String(MIN_NOTEBOOK_WIDTH));
    notebookSplit.setAttribute('aria-valuemax', String(Math.round(Math.max(MIN_NOTEBOOK_WIDTH, Math.min(620, (stage.clientWidth || 1000) * 0.55)))));
    notebookSplit.setAttribute('aria-valuenow', String(Math.round(notebookWidth)));
    if (save) persistPdfViewState({ notebookWidth });
  }
  applyNotebookWidth(notebookWidth);
  let draggingNotebook = false;
  notebookSplit.addEventListener('pointerdown', event => {
    if (notebookPanel.hidden || event.button !== 0) return;
    event.preventDefault();
    draggingNotebook = true;
    container.classList.add('resizing-notebook');
    try { notebookSplit.setPointerCapture(event.pointerId); } catch { /* ignore */ }
  });
  const onNotebookMove = (event: PointerEvent) => {
    if (!draggingNotebook || notebookPanel.hidden) return;
    const rect = stage.getBoundingClientRect();
    if (!(rect.width > 0)) return;
    applyNotebookWidth(event.clientX - rect.left, true);
  };
  notebookSplit.addEventListener('pointermove', onNotebookMove);
  window.addEventListener('pointermove', onNotebookMove);
  const stopNotebookDrag = () => {
    if (!draggingNotebook) return;
    draggingNotebook = false;
    container.classList.remove('resizing-notebook');
    refreshSplitPanes();
  };
  notebookSplit.addEventListener('pointerup', stopNotebookDrag);
  notebookSplit.addEventListener('pointercancel', stopNotebookDrag);
  window.addEventListener('pointerup', stopNotebookDrag);
  notebookSplit.addEventListener('dblclick', () => setNotebookOpen(false));
  notebookSplit.addEventListener('keydown', event => {
    if (notebookPanel.hidden) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      applyNotebookWidth(notebookWidth + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 32 : 16), true);
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
  function notebookDocument(content: unknown) {
    if (!content || typeof content !== 'object' || Array.isArray(content)) return EMPTY_NOTEBOOK;
    const value = (content as { notebook?: unknown }).notebook;
    return value && typeof value === 'object' ? value : EMPTY_NOTEBOOK;
  }
  function runNotebookCommand(command: string) {
    if (!notebookEditor) return;
    const chain = notebookEditor.chain().focus();
    if (command === 'bold') chain.toggleBold().run();
    if (command === 'italic') chain.toggleItalic().run();
    if (command === 'heading') chain.toggleHeading({ level: 2 }).run();
    if (command === 'heading3') chain.toggleHeading({ level: 3 }).run();
    if (command === 'heading4') chain.toggleHeading({ level: 4 }).run();
    if (command === 'heading5') chain.toggleHeading({ level: 5 }).run();
    if (command === 'bullet') chain.toggleBulletList().run();
    if (command === 'ordered') chain.toggleOrderedList().run();
    if (command === 'quote') chain.toggleBlockquote().run();
    if (command === 'undo') chain.undo().run();
    if (command === 'redo') chain.redo().run();
  }
  function flushNotebookSave() {
    clearTimeout(notebookSaveTimer);
    notebookSaveTimer = undefined;
    const content = notebookPending;
    notebookPending = null;
    if (!content || !documentId) return notebookSaving;
    const id = documentId;
    notebookSaving = notebookSaving.then(async () => {
      try {
        const revision = await hooks.saveNotebook(content, assetRevision);
        if (id !== documentId) return;
        assetRevision = revision;
        window.dispatchEvent(new CustomEvent('office-saved'));
      } catch (err) {
        if (!notebookPending && id === documentId) notebookPending = content;
        window.dispatchEvent(new CustomEvent('office-error', {
          detail: `PDF 笔记保存失败：${err instanceof Error ? err.message : String(err)}`,
        }));
      }
    });
    return notebookSaving;
  }
  function queueNotebookSave(content: unknown) {
    notebookPending = content;
    clearTimeout(notebookSaveTimer);
    notebookSaveTimer = setTimeout(() => {
      notebookSaveTimer = undefined;
      void flushNotebookSave();
    }, 700);
  }
  function ensureNotebookEditor() {
    if (notebookEditor) return;
    notebookEditor = new Editor({
      element: notebookEditorHost,
      extensions: [
        StarterKit.configure({ bulletList: false, listItem: false }),
        Link.configure({ openOnClick: true, autolink: true, defaultProtocol: 'https' }),
        circleBullet,
        collapsibleListItem,
        fourLevelBulletTab,
      ],
      content: notebookContent as any,
      editable: Boolean(documentId),
      onUpdate: ({ editor }) => {
        if (!notebookLoading) queueNotebookSave(editor.getJSON());
      },
      onSelectionUpdate: ({ editor }) => {
        if (editor.isFocused) notebookInsertAt = editor.state.selection.from;
      },
    });
  }
  function restoreNotebookScroll(id = documentId) {
    const top = Number(pdfViewState(id).notebookScrollTop);
    const scrollTop = Number.isFinite(top) && top >= 0 ? top : 0;
    requestAnimationFrame(() => {
      if (id === documentId && !notebookPanel.hidden) notebookEditorHost.scrollTop = scrollTop;
    });
  }
  function rememberNotebookUi() {
    if (!documentId) return;
    persistPdfViewState({
      notebookOpen: !notebookPanel.hidden,
      notebookWidth,
      notebookScrollTop: notebookEditorHost.scrollTop,
      splitRatio,
    });
  }
  function setNotebookOpen(open: boolean, save = true) {
    if (open && !documentId) return;
    if (!open && save) rememberNotebookUi();
    if (open) ensureNotebookEditor();
    notebookPanel.hidden = !open;
    notebookSplit.hidden = !open;
    notebookToggle.setAttribute('aria-pressed', open ? 'true' : 'false');
    notebookToggle.title = open ? '关闭 PDF 笔记本' : '打开 PDF 笔记本';
    if (open) applyNotebookWidth(notebookWidth);
    if (save) persistPdfViewState({ notebookOpen: open });
    if (open) restoreNotebookScroll();
    requestAnimationFrame(() => refreshSplitPanes());
  }
  function loadNotebook(content: unknown, revision: number) {
    notebookLoading = true;
    notebookContent = notebookDocument(content);
    assetRevision = revision;
    notebookPending = null;
    notebookInsertAt = null;
    clearTimeout(notebookSaveTimer);
    notebookSaveTimer = undefined;
    notebookEditorHost.scrollTop = 0;
    if (notebookEditor) {
      notebookEditor.commands.setContent(notebookContent as any);
      notebookEditor.setEditable(Boolean(documentId));
    }
    notebookLoading = false;
  }
  notebookEditorHost.addEventListener('scroll', () => {
    if (!documentId || notebookPanel.hidden) return;
    const id = documentId;
    const scrollTop = notebookEditorHost.scrollTop;
    clearTimeout(notebookScrollTimer);
    notebookScrollTimer = setTimeout(() => {
      notebookScrollTimer = undefined;
      if (id === documentId) persistPdfViewState({ notebookScrollTop: scrollTop }, id);
    }, 120);
  }, { passive: true });
  function appendOutline(outline: PdfOutlineItem[]) {
    setNotebookOpen(true);
    ensureNotebookEditor();
    if (!notebookEditor) return;
    notebookEditor.chain().focus('end').insertContent(outlineToTiptap(outline)).run();
    queueNotebookSave(notebookEditor.getJSON());
    // Outline generation is a completed PDF action; persist it before a refresh can discard it.
    void flushNotebookSave();
    changed();
  }
  function summaryToTiptap(raw: unknown, depth = 0): unknown[] {
    if (!Array.isArray(raw) || depth >= 4) return [];
    const content = raw.flatMap(item => {
      if (!item || typeof item !== 'object') return [];
      const value = item as { text?: unknown; children?: unknown };
      const text = typeof value.text === 'string' ? value.text.trim().slice(0, 56) : '';
      if (!text) return [];
      const children = summaryToTiptap(value.children, depth + 1);
      return [{ type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }, ...children] }];
    });
    return content.length ? [{ type: 'bulletList', content }] : [];
  }
  async function summarizeSelection() {
    const dom = readDomSelection();
    const text = (dom?.text || String(selected?.selection?.text || '')).trim();
    const page = dom?.page || Number(selected?.selection?.page || 0);
    if (!text || !page) { alertMessage('请先在 PDF 中选中需要整理的文字。'); return; }
    if (!notebookEditor || notebookInsertAt === null) { alertMessage('请先在笔记文本区选择摘要插入位置。'); return; }
    summaryBusy = true;
    controls();
    openPdfSummaryDialog({
      run: onEvent => hooks.generateSummary({ selection: text, page }, onEvent),
      onInsert: async items => {
        const content = summaryToTiptap(items);
        if (!content.length) throw new Error('未生成可插入的层级摘要。');
        const position = Math.min(Math.max(1, notebookInsertAt!), notebookEditor!.state.doc.content.size);
        notebookEditor!.chain().focus().insertContentAt(position, content).run();
        notebookInsertAt = notebookEditor!.state.selection.from;
        queueNotebookSave(notebookEditor!.getJSON());
      },
      onFinish: () => { summaryBusy = false; controls(); },
    });
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
    notebookToggle.disabled = busy || !documentId;
    outlineToggle.disabled = busy || outlineBusy || !main.ready || !documentId;
    summaryToggle.disabled = busy || summaryBusy || !main.ready || !documentId || !notebookEditor;
    splitBtn.disabled = busy || !main.ready || !documentId || !fileBytes;
    splitBtn.setAttribute('aria-pressed', split ? 'true' : 'false');
    splitBtn.textContent = split ? '关闭浏览' : '复制浏览';
    splitBtn.title = split ? '关闭左侧对照窗格' : '左右对照同一份 PDF';
    shots.sync();
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
  function pdfViewState(id = documentId): PdfAssetViewState {
    const value = saved(viewKey(id));
    return value && typeof value === 'object' && !Array.isArray(value) ? value as PdfAssetViewState : {};
  }
  function persistPdfViewState(patch: Partial<PdfAssetViewState>, id = documentId) {
    const key = viewKey(id);
    if (!key) return;
    persist(key, { ...pdfViewState(id), ...patch });
  }
  function restoreAssetUi(id = documentId) {
    const state = pdfViewState(id);
    const storedRatio = Number(state.splitRatio);
    const storedWidth = Number(state.notebookWidth);
    splitRatio = Number.isFinite(storedRatio) && storedRatio > 0 && storedRatio < 1 ? storedRatio : 0.5;
    notebookWidth = Number.isFinite(storedWidth) && storedWidth >= MIN_NOTEBOOK_WIDTH ? storedWidth : 360;
    applySplitRatio(splitRatio);
    applyNotebookWidth(notebookWidth);
    setNotebookOpen(state.notebookOpen === true, false);
  }
  function scaleOf(pane: Pane) {
    const scaleValue = pane.viewer.currentScaleValue;
    return scaleValue === 'page-width' ? 'page-width' : pane.viewer.currentScale;
  }
  function rememberView(force = false) {
    const key = viewKey();
    if (!key || !main.ready || (!force && (opening || busy || container.hidden))) return;
    const prev = pdfViewState();
    persistPdfViewState({
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
        const stored = pdfViewState();
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
        const stored = pdfViewState();
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
          applyStoredScale(pane, pdfViewState().scale);
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
          const mark = document.createElement('button');
          mark.type = 'button';
          mark.className = 'pdf-live-annot pdf-live-highlight pdf-highlight-mark';
          mark.dataset.xref = String(annot.xref);
          mark.title = '删除高亮';
          mark.setAttribute('aria-label', '删除高亮');
          Object.assign(mark.style, boxStyle(viewport, box));
          mark.addEventListener('mousedown', e => e.preventDefault());
          mark.addEventListener('click', open);
          pageDiv.append(mark);
        });
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
      note.title = pdfAnnotationPreview(annot.content) || (annot.aiAsk ? '查看或继续问 AI' : '查看批注');
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
  async function createAnnot(kind: AnnotKind, target: NonNullable<ReturnType<typeof selectionBoxes>>, content: string) {
    await withBusy(async () => {
      const result = await hooks.onAnnotate({ kind, page: target.page, boxes: target.boxes, content });
      if (!result?.xref) return;
      if (typeof result.revision === 'number') assetRevision = result.revision;
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
    if (kind === 'text') {
      await openPdfAnnotationDialog({
        isNew: true,
        editing: true,
        onSave: content => createAnnot(kind, target, content),
      });
      return;
    }
    await createAnnot(kind, target, '');
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
      save: async content => {
        const saved = await hooks.onAnnotate({ kind: 'text', page: opts.page, boxes: opts.boxes, content });
        if (typeof saved?.revision === 'number') assetRevision = saved.revision;
        return saved;
      },
      update: async (xref, content) => {
        const saved = await hooks.onUpdateAnnot(xref, content);
        if (typeof saved?.revision === 'number') assetRevision = saved.revision;
      },
      remove: async xref => {
        const saved = await hooks.onDeleteAnnot(xref);
        if (typeof saved?.revision === 'number') assetRevision = saved.revision;
        return saved ? { deleted: saved.deleted } : undefined;
      },
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
        if (typeof result?.revision === 'number') assetRevision = result.revision;
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
    const nextValue = await openPdfAnnotationDialog({
      content: current,
      onSave: async content => {
        await withBusy(async () => {
          const saved = await hooks.onUpdateAnnot(xref, content);
          if (typeof saved?.revision === 'number') assetRevision = saved.revision;
          const item = liveAnnots.find(entry => entry.xref === xref);
          if (item) item.content = content;
          for (const pane of panes()) {
            pane.pages.querySelectorAll<HTMLElement>(`[data-xref="${xref}"], [data-annotation-id="${xref}R"]`).forEach(node => {
              node.title = pdfAnnotationPreview(content);
            });
          }
        });
      },
    });
    if (nextValue.action === 'closed') return;
    if (nextValue.action === 'deleted') {
      await withBusy(async () => {
        if (!await askDelete('删除这个批注？')) return;
        const marks = await linkedMarkXrefs(id, pageNumber);
        const result = await hooks.onDeleteAnnot(xref);
        if (typeof result?.revision === 'number') assetRevision = result.revision;
        removeAnnotDom(xref, ...marks, ...(result?.deleted || []));
      });
      return;
    }
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
        section.title = aiAsk ? '查看或继续问 AI' : (pdfAnnotationPreview(contentsOf(id)) || '查看批注');
        section.addEventListener('click', open, true);
        return;
      }
      if (titleOf(id) === 'office-text-mark') return;
      section.classList.add('pdf-highlight-mark');
      section.title = '删除高亮';
      section.setAttribute('aria-label', '删除高亮');
      section.addEventListener('click', open, true);
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

  async function setSplit(on: boolean, focus: PaneId = 'copy', remember = true) {
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
        if (remember) rememberView();
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
    if (remember) rememberView();
    controls();
  }

  async function openBuffer(id: string, name: string, data: ArrayBuffer) {
    busy = true; opening = true; input.disabled = true; liveAnnots = []; clearSelection(); controls();
    let task: PDFDocumentLoadingTask | undefined;
    try {
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
  notebookToggle.onclick = () => setNotebookOpen(notebookPanel.hidden);
  outlineToggle.onclick = () => {
    if (outlineBusy || !documentId) return;
    outlineBusy = true;
    controls();
    openOutlineDialog({
      run: async onEvent => {
        try {
          await hooks.generateOutline(onEvent);
        } finally {
          outlineBusy = false;
          controls();
        }
      },
      onSuccess: appendOutline,
    });
  };
  summaryToggle.onclick = () => { void summarizeSelection(); };
  container.querySelectorAll<HTMLButtonElement>('[data-pdf-note-command]').forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = () => runNotebookCommand(button.dataset.pdfNoteCommand || '');
  });
  window.addEventListener('pagehide', () => {
    rememberView(true);
    rememberNotebookUi();
  });
  askAiBtn.addEventListener('mousedown', e => e.preventDefault());
  askAiBtn.onclick = () => { void startAskAi().catch(e => error(String(e))); };
  annotateButtons.forEach(button => {
    button.addEventListener('mousedown', e => e.preventDefault());
    button.onclick = () => { void annotate(button.dataset.annotate as AnnotKind); };
  });
  const shots = bindPdfShots({
    container,
    documentId: () => documentId,
    activePane: () => active().root,
    canCapture: () => !busy && !!documentId && active().ready,
  });
  controls();
  return {
    context: () => selected,
    anchor: () => null,
    apply() { throw new Error('PDF 不支持把助手修改写回正文，请用工具栏添加批注。'); },
    undo() {},
    export() {},
    persist: async () => {
      if (notebookEditor) queueNotebookSave(notebookEditor.getJSON());
      await flushNotebookSave();
      rememberView(true);
      rememberNotebookUi();
    },
    async load(asset) {
      await flushNotebookSave();
      rememberView(true);
      rememberNotebookUi();
      clearTimeout(notebookScrollTimer);
      notebookScrollTimer = undefined;
      if (split) await setSplit(false, 'main', false);
      if (!asset.id) {
        documentId = '';
        loadNotebook(EMPTY_NOTEBOOK, 0);
        setNotebookOpen(false, false);
        await showEmpty();
        return;
      }
      documentId = asset.id;
      filename = asset.name;
      loadNotebook(asset.content, asset.revision);
      restoreAssetUi(asset.id);
      const [data] = await Promise.all([hooks.fetchFile(asset.id), whenVisible()]);
      if (!data) { await showEmpty(asset.id, asset.name); return; }
      await openBuffer(asset.id, asset.filename || `${asset.name}.pdf`, data);
    },
  };
}
