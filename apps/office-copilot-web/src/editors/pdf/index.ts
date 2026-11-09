// Electron 33（Chromium ~130）尚无 Uint8Array.prototype.toHex/toBase64；须用 legacy 构建（含 polyfill）。
import {
  AnnotationMode, getDocument, GlobalWorkerOptions,
  type PDFDocumentProxy, type PDFDocumentLoadingTask,
} from 'pdfjs-dist/legacy/build/pdf.mjs';
import { EventBus, PDFLinkService, PDFViewer, ScrollMode } from 'pdfjs-dist/legacy/web/pdf_viewer.mjs';
import { Editor, Mark } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import { notebookPositionPatch, restoreNotebookPosition } from './notebook-position';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { createIcons, icons } from 'lucide';
import worker from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import 'pdfjs-dist/legacy/web/pdf_viewer.css';
import './style.css';
import type { Context, EditorAdapter } from '../../context/types';
import { persist, saved } from '../../context/types';
import { isAiAskContent, parseAskThread } from './ask-ai';
import { openAskAiDialog } from './ask-ai-dialog';
import { openPdfAnnotationDialog, pdfAnnotationPreview } from './annotation-dialog';
import { openOutlineDialog } from './outline-dialog';
import { matchTemplateOutline, outlineToTiptap, type PdfOutlineItem } from './outline';
import { bindPdfShots } from './shots';
import { openPdfSummaryDialog } from './summary-dialog';
import { openPdfSelectionTranslationDialog, openPdfTranslationDialog } from './translation-dialog';
import { circleBullet, collapsibleListItem, pdfIndentedParagraph, pdfNotebookTab } from '../rich-text-lists';
import { pdfFileRequest, type PdfFileOptions, type PdfFileProgress } from '../../office-client/client';
import { assetIndexExtensions } from '../../asset-index';
import { openTemplateDialog, type OutlineTemplateRecord } from './template-dialog';
import {
  annotationPdfBoxes, highlightInsertContent, pointInPdfBoxes, quadsToBoxes, textCoveredByHighlight, textItemsInBoxes, type PixelBox,
} from './highlight-note';

GlobalWorkerOptions.workerSrc = worker;

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 5;
const MAX_PDF = 200 * 1024 * 1024;
/** 超过这个大小就按页拉取，不再把整份文件放进内存。 */
const RANGE_OPEN_BYTES = 30 * 1024 * 1024;
/** 与 office-backend PdfAnnotBody / pdf_annot 对齐；前端先按行合并再截断。 */
const MAX_ANNOT_BOXES = 200;
const EMPTY_NOTEBOOK = { type: 'doc', content: [{ type: 'paragraph' }] };

const pdfOutlineReference = Mark.create({
  name: 'pdfOutlineReference',
  inclusive: false,
  addAttributes() {
    return {
      page: {
        default: null,
        parseHTML: element => Number((element as HTMLElement).dataset.pdfOutlinePage) || null,
        renderHTML: attributes => attributes.page ? { 'data-pdf-outline-page': String(attributes.page) } : {},
      },
    };
  },
  parseHTML: () => [{ tag: '[data-pdf-outline-page]' }],
  renderHTML: ({ HTMLAttributes }) => {
    const page = HTMLAttributes['data-pdf-outline-page'] ?? HTMLAttributes.page;
    return ['span', {
      ...HTMLAttributes,
      class: 'pdf-outline-reference',
      'data-pdf-outline-page': page,
      role: 'link',
      tabindex: '0',
      title: `跳转至 PDF 第 ${page} 页`,
    }, 0];
  },
});

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
type LiveAnnot = { xref: number; markXref?: number; kind: AnnotKind; page: number; boxes: number[][]; content: string; quote?: string; aiAsk?: boolean };
type FileHighlight = { page: number; xref: number; boxes: number[][]; title: string };
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
  translationWidth?: number;
  notebookScrollTop?: number;
  generatedOutline?: PdfOutlineItem[];
  readingHistory?: Array<{ page: number; scrollTop: number }>;
  readingHistoryIndex?: number;
  copyReadingHistory?: Array<{ page: number; scrollTop: number }>;
  copyReadingHistoryIndex?: number;
};

type PdfTranslationPage = {
  source: string;
  translation: string;
  content?: unknown;
  translatedAt: string;
  modelName?: string;
};

type PdfTranslation = {
  format: 'pdf-translation-v1';
  pages: Record<string, PdfTranslationPage>;
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

type PdfIndexStatus = {
  status: string;
  phase?: string;
  page_count?: number;
  chunk_count?: number;
  progress_done?: number;
  progress_total?: number;
  batch_size?: number;
  error?: string;
  empty?: boolean;
  asset_status?: string;
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
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
  fetchFile: (id: string, opts?: PdfFileOptions) => Promise<ArrayBuffer | null>;
  onAnnotate: (body: { kind: AnnotKind; page: number; boxes: number[][]; content?: string }) => Promise<{ xref: number; mark_xref?: number; revision?: number } | void>;
  onUpdateAnnot: (xref: number, content: string) => Promise<{ revision?: number } | void>;
  onDeleteAnnot: (xref: number) => Promise<{ deleted?: number[]; revision?: number } | void>;
  saveNotebook: (content: unknown, revision: number) => Promise<number>;
  saveTranslation: (content: PdfTranslation, revision: number) => Promise<number>;
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
  translatePage: (page: number, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  translateSelection: (body: { selection: string; page: number }, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  listOutlineTemplates: () => Promise<{ id: string; name: string }[]>;
  loadOutlineTemplate: (id: string) => Promise<OutlineTemplateRecord>;
  saveOutlineTemplate: (name: string, doc: unknown) => Promise<OutlineTemplateRecord>;
  updateOutlineTemplate: (id: string, name: string, doc: unknown, revision: number) => Promise<OutlineTemplateRecord>;
  indexStatus: (id: string) => Promise<PdfIndexStatus>;
}): EditorAdapter {
  let pdf: PDFDocumentProxy | undefined;
  let copyPdf: PDFDocumentProxy | undefined;
  let loadingTask: PDFDocumentLoadingTask | undefined;
  let copyLoadingTask: PDFDocumentLoadingTask | undefined;
  let fileBytes: ArrayBuffer | null = null;
  /** 大文件不保留字节，对照视图用同一地址按页拉取。 */
  let rangeSource: { url: string; headers: Record<string, string>; length: number } | null = null;
  let filename = '';
  let documentId = '';
  let loadToken = 0;
  let openedFingerprint = '';
  let assetRevision = 0;
  /** 同类 PDF 切换时保留已解析文档，回来直接挂回查看器，避免重新 getDocument。 */
  type ParkedPdf = {
    id: string;
    revision: number;
    name: string;
    bytes: ArrayBuffer;
    pdf: PDFDocumentProxy;
    task: PDFDocumentLoadingTask;
    fingerprint: string;
    usedAt: number;
  };
  const parkedPdfs = new Map<string, ParkedPdf>();
  const PARKED_PDF_LIMIT = 5;
  let notebookContent: unknown = EMPTY_NOTEBOOK;
  let notebookEditor: Editor | null = null;
  let notebookLoading = false;
  let notebookRestoreGen = 0;
  let notebookPending: unknown | null = null;
  let notebookSaving: Promise<void> = Promise.resolve();
  let notebookSaveTimer: ReturnType<typeof setTimeout> | undefined;
  let selected: Context | null = null;
  let selectionVersion = 0;
  let selectionTranslationEnabled = false;
  let selectionTranslationBusy = false;
  let selectionTranslationTarget: { page: number; text: string } | null = null;
  let busy = false;
  /** 当前这次打开要等首页画出来才收起进度条。 */
  let readingDoc: PDFDocumentProxy | null = null;
  let readingClosed = false;
  let indexWatch = 0;
  let outlineBusy = false;
  let summaryBusy = false;
  let translationBusy = false;
  let translationOpen = false;
  let translation: PdfTranslation = { format: 'pdf-translation-v1', pages: {} };
  let translationEditor: Editor | null = null;
  let translationEditorPage = 0;
  let translationEditorLoading = false;
  let translationPending: PdfTranslation | null = null;
  let translationSaving: Promise<void> = Promise.resolve();
  let translationSaveTimer: ReturnType<typeof setTimeout> | undefined;
  let generatedOutline: PdfOutlineItem[] = [];
  type ReadingHistory = { entries: Array<{ page: number; scrollTop: number }>; index: number };
  const readingHistories: Record<PaneId, ReadingHistory> = {
    main: { entries: [], index: -1 },
    copy: { entries: [], index: -1 },
  };
  let restoringReadingHistory = false;
  let notebookInsertAt: number | null = null;
  let opening = false;
  // pagesinit 里跳到上次页码后，pdf.js 还会同步 update()。这段时间不能把页码写回第 1 页。
  let restoringView = false;
  let viewEpoch = 0;
  let openingToken = 0;
  let inflightOpen: { fp: string; promise: Promise<boolean> } | null = null;
  let split = false;
  let pendingSplit: boolean | null = null;
  let activeId: PaneId = 'main';
  let liveAnnots: LiveAnnot[] = [];
  let fileHighlights: FileHighlight[] = [];

  const stage = container.querySelector<HTMLElement>('.pdf-stage')!;
  const mainRoot = container.querySelector<HTMLElement>('.pdf-pane[data-pane="main"]')!;
  const copyRoot = container.querySelector<HTMLElement>('.pdf-pane[data-pane="copy"]')!;
  const paneSplit = container.querySelector<HTMLElement>('.pdf-split')!;
  const mainScroller = mainRoot.querySelector<HTMLDivElement>('.pdf-scroll')!;
  const copyScroller = copyRoot.querySelector<HTMLDivElement>('.pdf-scroll')!;
  const empty = mainScroller.firstElementChild!;
  const downloadOverlay = document.createElement('div');
  downloadOverlay.className = 'pdf-download';
  downloadOverlay.hidden = true;
  downloadOverlay.innerHTML = [
    '<div class="pdf-download-card" role="status" aria-live="polite">',
    '<p class="pdf-download-title">正在打开 PDF</p>',
    '<p class="pdf-download-meta" data-pdf-download-meta>准备中…</p>',
    '<div class="pdf-download-track"><div class="pdf-download-bar" data-pdf-download-bar></div></div>',
    '</div>',
  ].join('');
  // 挂在 #pdf 内，只盖住 PDF 工作区；切到其它页时随 [hidden] 一起消失
  container.append(downloadOverlay);
  const downloadTitle = downloadOverlay.querySelector<HTMLElement>('.pdf-download-title')!;
  const downloadMeta = downloadOverlay.querySelector<HTMLElement>('[data-pdf-download-meta]')!;
  const downloadBar = downloadOverlay.querySelector<HTMLElement>('[data-pdf-download-bar]')!;
  const counter = container.querySelector<HTMLElement>('[data-page]')!;
  const indexLine = document.createElement('span');
  indexLine.className = 'pdf-index-status';
  indexLine.hidden = true;
  indexLine.setAttribute('role', 'status');
  indexLine.setAttribute('aria-live', 'polite');
  mainRoot.append(indexLine);
  const notebookToggle = container.querySelector<HTMLButtonElement>('[data-pdf-notebook]')!;
  const outlineToggle = container.querySelector<HTMLButtonElement>('[data-pdf-outline]')!;
  const templateButton = container.querySelector<HTMLButtonElement>('[data-pdf-template]')!;
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
  // 带 hidden 或放在 [hidden] 的 #pdf 里时，浏览器会直接忽略 click()，文件框不会出现。
  input.id = 'pdf-file-input';
  input.hidden = false;
  input.style.position = 'fixed';
  input.style.left = '0';
  input.style.top = '0';
  input.style.width = '1px';
  input.style.height = '1px';
  input.style.opacity = '0.01';
  input.style.pointerEvents = 'none';
  document.body.append(input);
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
  // 术语维护已下线；工具栏仅保留会作用于当前 PDF 的能力。
  container.querySelector<HTMLElement>('[data-pdf-terms]')?.remove();
  const shotButton = container.querySelector<HTMLButtonElement>('[data-pdf-shot]')!;
  shotButton.innerHTML = '<i data-lucide="scan-line"></i><span>截图</span>';
  const translationToggle = document.createElement('button');
  translationToggle.type = 'button';
  translationToggle.className = 'pdf-translation-toggle';
  translationToggle.dataset.pdfTranslation = '';
  translationToggle.title = '打开英中对照翻译';
  translationToggle.setAttribute('aria-label', '英中对照翻译');
  translationToggle.setAttribute('aria-pressed', 'false');
  translationToggle.innerHTML = '<i data-lucide="languages"></i><span>翻译</span>';
  askAiBtn.insertAdjacentElement('afterend', translationToggle);
  const selectionTranslationToggle = document.createElement('button');
  selectionTranslationToggle.type = 'button';
  selectionTranslationToggle.className = 'pdf-selection-translation-toggle';
  selectionTranslationToggle.title = '开启后，选中 PDF 文字可点击“译”进行翻译';
  selectionTranslationToggle.setAttribute('aria-label', '划词翻译');
  selectionTranslationToggle.setAttribute('role', 'switch');
  selectionTranslationToggle.setAttribute('aria-checked', 'false');
  selectionTranslationToggle.innerHTML = '<i data-lucide="text-select"></i><span>划词</span><i class="pdf-toggle-knob" aria-hidden="true"></i>';
  translationToggle.insertAdjacentElement('afterend', selectionTranslationToggle);
  const selectionTranslationAction = document.createElement('button');
  selectionTranslationAction.type = 'button';
  selectionTranslationAction.className = 'pdf-selection-translate-action';
  selectionTranslationAction.title = '翻译选中文本';
  selectionTranslationAction.setAttribute('aria-label', '翻译选中文本');
  selectionTranslationAction.textContent = '译';
  selectionTranslationAction.hidden = true;
  document.body.append(selectionTranslationAction);
  const translationPanel = document.createElement('aside');
  translationPanel.className = 'pdf-translation-panel';
  translationPanel.hidden = true;
  translationPanel.innerHTML = '<div class="pdf-translation-workspace-toolbar" role="toolbar" aria-label="译文工具"><button type="button" class="pdf-translate-page" data-translate-page title="翻译当前页"><i data-lucide="languages"></i><span>翻译此页</span></button><i class="pdf-translation-toolbar-divider" aria-hidden="true"></i><button type="button" data-translation-command="bold" title="加粗" aria-label="加粗"><i data-lucide="bold"></i></button><button type="button" data-translation-command="italic" title="斜体" aria-label="斜体"><i data-lucide="italic"></i></button><button type="button" data-translation-command="heading" title="标题" aria-label="标题"><i data-lucide="heading-3"></i></button><button type="button" data-translation-command="bulletList" title="无序列表" aria-label="无序列表"><i data-lucide="list"></i></button><button type="button" data-translation-command="orderedList" title="编号列表" aria-label="编号列表"><i data-lucide="list-ordered"></i></button></div><div class="pdf-translation-head"><strong data-translation-page>译文</strong><span data-translation-meta></span></div><p class="pdf-translation-empty" data-translation-empty>此页尚未翻译。点击工具栏“翻译此页”生成专业中文译文。</p><div class="pdf-translation-content" data-translation-content></div>';
  stage.append(translationPanel);
  const translationSplit = document.createElement('div');
  translationSplit.className = 'pdf-translation-split';
  translationSplit.setAttribute('role', 'separator');
  translationSplit.setAttribute('aria-orientation', 'vertical');
  translationSplit.setAttribute('aria-label', '调整译文区域宽度');
  translationSplit.tabIndex = 0;
  translationSplit.hidden = true;
  stage.insertBefore(translationSplit, translationPanel);
  const translatePageButton = translationPanel.querySelector<HTMLButtonElement>('[data-translate-page]')!;
  const translationPageLabel = translationPanel.querySelector<HTMLElement>('[data-translation-page]')!;
  const translationMeta = translationPanel.querySelector<HTMLElement>('[data-translation-meta]')!;
  const translationContent = translationPanel.querySelector<HTMLElement>('[data-translation-content]')!;
  const translationEmpty = translationPanel.querySelector<HTMLElement>('[data-translation-empty]')!;
  const createReadingHistoryToolbar = (pane: PaneId, root: HTMLElement) => {
    const toolbar = document.createElement('div');
    toolbar.className = 'pdf-reading-history';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', '阅读历史');
    toolbar.innerHTML = '<button type="button" data-reading-back title="返回上次阅读位置" aria-label="返回上次阅读位置" disabled><i data-lucide="arrow-left"></i></button><button type="button" data-reading-forward title="前进下次阅读位置" aria-label="前进下次阅读位置" disabled><i data-lucide="arrow-right"></i></button>';
    root.prepend(toolbar);
    const back = toolbar.querySelector<HTMLButtonElement>('[data-reading-back]')!;
    const forward = toolbar.querySelector<HTMLButtonElement>('[data-reading-forward]')!;
    back.onclick = () => restoreReadingPosition(pane, readingHistories[pane].index - 1);
    forward.onclick = () => restoreReadingPosition(pane, readingHistories[pane].index + 1);
    return { back, forward };
  };
  const mainHistoryControls = createReadingHistoryToolbar('main', mainRoot);
  const copyHistoryControls = createReadingHistoryToolbar('copy', copyRoot);
  createIcons({ icons });
  const annotateButtons = [...container.querySelectorAll<HTMLButtonElement>('[data-annotate]')];
  const error = (detail: string) => window.dispatchEvent(new CustomEvent('office-error', { detail }));
  const MIN_PANE = 120;
  const MIN_NOTEBOOK_WIDTH = 240;
  const MIN_TRANSLATION_WIDTH = 320;
  let notebookWidth = 360;
  let translationWidth = 460;
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

  function clampTranslationWidth(width: number) {
    const max = Math.max(MIN_TRANSLATION_WIDTH, Math.min(860, (stage.clientWidth || 1000) * 0.7));
    return Math.min(max, Math.max(MIN_TRANSLATION_WIDTH, width));
  }
  function applyTranslationWidth(width: number, save = false) {
    translationWidth = clampTranslationWidth(width);
    stage.style.setProperty('--pdf-translation-width', `${Math.round(translationWidth)}px`);
    translationSplit.setAttribute('aria-valuemin', String(MIN_TRANSLATION_WIDTH));
    translationSplit.setAttribute('aria-valuemax', String(Math.round(Math.max(MIN_TRANSLATION_WIDTH, Math.min(860, (stage.clientWidth || 1000) * 0.7)))));
    translationSplit.setAttribute('aria-valuenow', String(Math.round(translationWidth)));
    if (save) persistPdfViewState({ translationWidth });
  }
  applyTranslationWidth(translationWidth);
  let draggingTranslation = false;
  translationSplit.addEventListener('pointerdown', event => {
    if (translationPanel.hidden || event.button !== 0) return;
    event.preventDefault();
    draggingTranslation = true;
    container.classList.add('resizing-translation');
    try { translationSplit.setPointerCapture(event.pointerId); } catch { /* ignore */ }
  });
  const onTranslationMove = (event: PointerEvent) => {
    if (!draggingTranslation || translationPanel.hidden) return;
    const rect = stage.getBoundingClientRect();
    if (!(rect.width > 0)) return;
    applyTranslationWidth(rect.right - event.clientX, true);
  };
  translationSplit.addEventListener('pointermove', onTranslationMove);
  window.addEventListener('pointermove', onTranslationMove);
  const stopTranslationDrag = () => {
    if (!draggingTranslation) return;
    draggingTranslation = false;
    container.classList.remove('resizing-translation');
    refreshSplitPanes();
  };
  translationSplit.addEventListener('pointerup', stopTranslationDrag);
  translationSplit.addEventListener('pointercancel', stopTranslationDrag);
  window.addEventListener('pointerup', stopTranslationDrag);
  translationSplit.addEventListener('keydown', event => {
    if (translationPanel.hidden || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
    event.preventDefault();
    applyTranslationWidth(translationWidth + (event.key === 'ArrowLeft' ? 1 : -1) * (event.shiftKey ? 32 : 16), true);
    refreshSplitPanes();
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
    selectionTranslationTarget = null;
    selectionTranslationAction.hidden = true;
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
    if (command === 'paragraph') {
      if (notebookEditor.isActive('blockquote')) chain.toggleBlockquote();
      chain.setParagraph().run();
    }
    if (command === 'bullet') chain.toggleBulletList().run();
    if (command === 'ordered') chain.toggleOrderedList().run();
    if (command === 'quote') chain.toggleBlockquote().run();
  }
  function openNotebookPageLinkDialog() {
    const currentPdf = pdf;
    if (!notebookEditor || !currentPdf) return;
    const { from, to } = notebookEditor.state.selection;
    if (from === to) {
      alertMessage('请先在笔记中选中需要关联页码的文字。');
      return;
    }
    document.querySelector<HTMLElement>('.pdf-page-link-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay pdf-page-link-overlay';
    overlay.innerHTML = [
      '<form class="name-dialog pdf-page-link-dialog" role="dialog" aria-modal="true" aria-labelledby="pdf-page-link-title">',
      '<h2 id="pdf-page-link-title">关联 PDF 页码</h2>',
      `<p>输入页码（1 - ${currentPdf.numPages}），选中文字将可点击跳转。</p>`,
      `<input type="number" min="1" max="${currentPdf.numPages}" step="1" value="${main.viewer.currentPageNumber || 1}" aria-label="PDF 页码" required>`,
      '<div class="dialog-actions"><button type="button" data-cancel>取消</button><button type="submit" class="primary">添加链接</button></div>',
      '</form>',
    ].join('');
    const close = () => overlay.remove();
    const form = overlay.querySelector<HTMLFormElement>('form')!;
    const input = overlay.querySelector<HTMLInputElement>('input')!;
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    overlay.querySelector<HTMLButtonElement>('[data-cancel]')!.onclick = close;
    form.onsubmit = event => {
      event.preventDefault();
      const page = Number(input.value);
      if (!Number.isInteger(page) || page < 1 || page > currentPdf.numPages) {
        input.setCustomValidity(`请输入 1 至 ${currentPdf.numPages} 的整数页码。`);
        input.reportValidity();
        return;
      }
      input.setCustomValidity('');
      notebookEditor!.chain().focus().setTextSelection({ from, to })
        .setMark('pdfOutlineReference', { page }).run();
      queueNotebookSave(notebookEditor!.getJSON());
      close();
    };
    document.body.append(overlay);
    input.focus();
    input.select();
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
        StarterKit.configure({ bulletList: false, listItem: false, paragraph: false }),
        Link.configure({ openOnClick: true, autolink: true, defaultProtocol: 'https' }),
        pdfOutlineReference,
        circleBullet,
        collapsibleListItem,
        pdfIndentedParagraph,
        pdfNotebookTab,
        ...assetIndexExtensions(),
      ],
      content: notebookContent as any,
      editable: Boolean(documentId),
      editorProps: {
        handleClick: (_view, _position, event) => {
          const target = event.target instanceof Element
            ? event.target.closest<HTMLElement>('[data-pdf-outline-page]')
            : event.target instanceof Node
              ? event.target.parentElement?.closest<HTMLElement>('[data-pdf-outline-page]')
              : null;
          const page = Number(target?.dataset.pdfOutlinePage);
          if (!target || !Number.isInteger(page) || page < 1) return false;
          jumpToOutlinePage(page);
          return true;
        },
      },
      onUpdate: ({ editor }) => {
        if (!notebookLoading) queueNotebookSave(editor.getJSON());
      },
      onFocus: ({ editor }) => {
        notebookInsertAt = editor.state.selection.from;
      },
      onBlur: ({ editor }) => {
        notebookInsertAt = editor.state.selection.from;
      },
      onSelectionUpdate: ({ editor }) => {
        if (editor.isFocused) notebookInsertAt = editor.state.selection.from;
      },
    });
  }
  let notebookRestoring = false;
  let cancelNotebookRestore: (() => void) | undefined;
  function restoreNotebookScroll(id = documentId) {
    cancelNotebookRestore?.();
    const top = Number(pdfViewState(id).notebookScrollTop);
    const gen = ++notebookRestoreGen;
    notebookRestoring = true;
    cancelNotebookRestore = restoreNotebookPosition(notebookEditorHost, top,
      () => gen === notebookRestoreGen && id === documentId && !notebookPanel.hidden,
      () => { notebookRestoring = false; });
  }
  function rememberNotebookUi() {
    if (!documentId || notebookLoading) return;
    persistPdfViewState({
      notebookOpen: !notebookPanel.hidden,
      notebookWidth,
      ...notebookPositionPatch(notebookEditorHost, !notebookPanel.hidden && !container.hidden && !notebookRestoring),
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
    else {
      cancelNotebookRestore?.();
      notebookRestoring = false;
      notebookLoading = false;
    }
    requestAnimationFrame(() => refreshSplitPanes());
  }
  function loadNotebook(content: unknown, revision: number) {
    cancelNotebookRestore?.();
    notebookRestoring = false;
    notebookRestoreGen += 1;
    notebookLoading = true;
    notebookContent = notebookDocument(content);
    assetRevision = revision;
    notebookPending = null;
    notebookInsertAt = null;
    clearTimeout(notebookSaveTimer);
    notebookSaveTimer = undefined;
    clearTimeout(notebookScrollTimer);
    notebookScrollTimer = undefined;
    if (notebookEditor) {
      notebookEditor.commands.setContent(notebookContent as any);
      notebookEditor.setEditable(Boolean(documentId));
    }
    notebookLoading = false;
  }
  notebookEditorHost.addEventListener('scroll', () => {
    if (!documentId || notebookPanel.hidden || container.hidden || notebookLoading || notebookRestoring) {
      clearTimeout(notebookScrollTimer);
      notebookScrollTimer = undefined;
      return;
    }
    const id = documentId;
    const scrollTop = notebookEditorHost.scrollTop;
    clearTimeout(notebookScrollTimer);
    notebookScrollTimer = setTimeout(() => {
      notebookScrollTimer = undefined;
      if (id === documentId && !notebookRestoring) persistPdfViewState({ notebookScrollTop: scrollTop }, id);
    }, 120);
  }, { passive: true });
  const stopNotebookRestore = () => {
    if (!notebookRestoring) return;
    cancelNotebookRestore?.();
    notebookRestoring = false;
  };
  notebookEditorHost.addEventListener('wheel', stopNotebookRestore, { passive: true });
  notebookEditorHost.addEventListener('touchstart', stopNotebookRestore, { passive: true });
  notebookEditorHost.addEventListener('keydown', stopNotebookRestore);
  function appendOutline(outline: PdfOutlineItem[]) {
    generatedOutline = outline;
    persistPdfViewState({ generatedOutline });
    setNotebookOpen(true);
    ensureNotebookEditor();
    if (!notebookEditor) return;
    notebookEditor.chain().focus('end').insertContent(outlineToTiptap(outline)).run();
    queueNotebookSave(notebookEditor.getJSON());
    // Outline generation is a completed PDF action; persist it before a refresh can discard it.
    void flushNotebookSave();
    changed();
  }
  function insertNotebookNodes(nodes: unknown[]) {
    setNotebookOpen(true);
    ensureNotebookEditor();
    if (!notebookEditor || !nodes.length) return;
    notebookEditor.chain().focus('end').insertContent(nodes).run();
    queueNotebookSave(notebookEditor.getJSON());
    void flushNotebookSave();
    changed();
  }
  function replaceNotebook(doc: unknown) {
    setNotebookOpen(true);
    ensureNotebookEditor();
    if (!notebookEditor) return;
    notebookEditor.commands.setContent(doc as any);
    queueNotebookSave(notebookEditor.getJSON());
    void flushNotebookSave();
    changed();
  }
  function templateNodes(doc: unknown) {
    return doc && typeof doc === 'object' && Array.isArray((doc as { content?: unknown }).content)
      ? (doc as { content: unknown[] }).content
      : [];
  }
  function applyMatchedTemplate(template: unknown, replace: boolean) {
    if (!generatedOutline.length) {
      alertMessage('请先点击“生成大纲”，解析当前 PDF 后再套用模板。');
      return;
    }
    const result = matchTemplateOutline(template, generatedOutline);
    if (replace) replaceNotebook(result.doc);
    else insertNotebookNodes(templateNodes(result.doc));
    const unmatched = result.unmatched ? `，${result.unmatched} 项未定位` : '';
    alertMessage(`已匹配 ${result.matched} 项${unmatched}。`);
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
  function translationDocument(content: unknown): PdfTranslation {
    const raw = content && typeof content === 'object' && !Array.isArray(content)
      ? (content as { translation?: unknown }).translation
      : null;
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { format: 'pdf-translation-v1', pages: {} };
    const pages = (raw as { pages?: unknown }).pages;
    if (!pages || typeof pages !== 'object' || Array.isArray(pages)) return { format: 'pdf-translation-v1', pages: {} };
    const clean: Record<string, PdfTranslationPage> = {};
    for (const [key, value] of Object.entries(pages)) {
      const item = value as Partial<PdfTranslationPage>;
      if (!Number.isInteger(Number(key)) || Number(key) < 1 || !item || typeof item.source !== 'string' || typeof item.translation !== 'string') continue;
      clean[key] = {
        source: item.source,
        translation: item.translation,
        content: item.content && typeof item.content === 'object' && !Array.isArray(item.content) ? item.content : undefined,
        translatedAt: typeof item.translatedAt === 'string' ? item.translatedAt : '',
        modelName: typeof item.modelName === 'string' ? item.modelName : undefined,
      };
    }
    return { format: 'pdf-translation-v1', pages: clean };
  }
  function translationContentDocument(text: string) {
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    return {
      type: 'doc',
      content: lines.map(line => ({ type: 'paragraph', content: line ? [{ type: 'text', text: line }] : [] })),
    };
  }
  function flushTranslationSave() {
    clearTimeout(translationSaveTimer);
    translationSaveTimer = undefined;
    const pending = translationPending;
    if (!pending) return translationSaving;
    translationPending = null;
    translationSaving = translationSaving.then(async () => {
      try {
        const revision = await hooks.saveTranslation(pending, assetRevision);
        assetRevision = revision;
        window.dispatchEvent(new CustomEvent('office-saved'));
      } catch (err) {
        translationPending = pending;
        window.dispatchEvent(new CustomEvent('office-error', {
          detail: `PDF 译文保存失败：${err instanceof Error ? err.message : String(err)}`,
        }));
      } finally {
        controls();
      }
    });
    return translationSaving;
  }
  function queueTranslationSave() {
    translationPending = translation;
    clearTimeout(translationSaveTimer);
    translationSaveTimer = setTimeout(() => {
      translationSaveTimer = undefined;
      void flushTranslationSave();
    }, 700);
    controls();
  }
  function ensureTranslationEditor() {
    if (translationEditor) return;
    translationEditor = new Editor({
      element: translationContent,
      extensions: [StarterKit, Link.configure({ openOnClick: false, autolink: true, defaultProtocol: 'https' }), ...assetIndexExtensions()],
      content: EMPTY_NOTEBOOK,
      editable: false,
      onUpdate: ({ editor }) => {
        if (translationEditorLoading) return;
        const page = translationEditorPage;
        const current = translation.pages[String(page)];
        if (!current) return;
        const content = editor.getJSON();
        translation = {
          format: 'pdf-translation-v1',
          pages: {
            ...translation.pages,
            [String(page)]: { ...current, translation: editor.getText(), content },
          },
        };
        queueTranslationSave();
      },
    });
  }
  function renderTranslationPage(page = main.viewer.currentPageNumber || 1) {
    translationPageLabel.textContent = `第 ${page} 页译文`;
    const item = translation.pages[String(page)];
    ensureTranslationEditor();
    const content = item?.content || translationContentDocument(item?.translation || '');
    translationEditorLoading = true;
    translationEditorPage = page;
    translationEditor!.commands.setContent(content, { emitUpdate: false });
    translationEditorLoading = false;
    translationEditor!.setEditable(Boolean(item) && !translationBusy);
    translationContent.hidden = !item;
    translationEmpty.hidden = Boolean(item);
    translationMeta.textContent = item?.translatedAt ? `已译 · ${item.modelName || '模型'}` : '';
    translatePageButton.disabled = busy || translationBusy || !main.ready || !documentId;
    translatePageButton.querySelector('span')!.textContent = translationBusy ? '翻译中…' : '翻译此页';
  }
  async function setTranslationOpen(open: boolean) {
    if (open === translationOpen && translationPanel.hidden === !open) return;
    if (open) {
      await flushNotebookSave();
      if (split) await setSplit(false);
      if (!notebookPanel.hidden) setNotebookOpen(false);
    }
    translationOpen = open;
    translationPanel.hidden = !open;
    translationSplit.hidden = !open;
    stage.classList.toggle('is-translation-open', open);
    translationToggle.setAttribute('aria-pressed', open ? 'true' : 'false');
    translationToggle.title = open ? '关闭英中对照翻译' : '打开英中对照翻译';
    if (open) renderTranslationPage();
    requestAnimationFrame(() => refreshSplitPanes());
    if (!open) void flushTranslationSave();
  }
  async function translateCurrentPage() {
    const page = main.viewer.currentPageNumber || 1;
    if (!documentId || !main.ready || translationBusy) return;
    if (translation.pages[String(page)] && !window.confirm('此页已有译文。重新翻译将替换当前页已有译文，是否继续？')) return;
    await flushNotebookSave();
    await flushTranslationSave();
    translationBusy = true;
    renderTranslationPage(page);
    openPdfTranslationDialog({
      anchor: translationPanel,
      run: onEvent => hooks.translatePage(page, onEvent),
      onDone: async result => {
        translation = {
          format: 'pdf-translation-v1',
          pages: {
            ...translation.pages,
            [String(result.page)]: {
              source: result.source,
              translation: result.translation,
              content: translationContentDocument(result.translation),
              translatedAt: new Date().toISOString(),
              modelName: result.modelName,
            },
          },
        };
        const revision = await hooks.saveTranslation(translation, assetRevision);
        assetRevision = revision;
        renderTranslationPage(result.page);
        window.dispatchEvent(new CustomEvent('office-saved'));
      },
      onFinish: () => { translationBusy = false; renderTranslationPage(page); controls(); },
    });
  }
  function setSelectionTranslationEnabled(enabled: boolean) {
    selectionTranslationEnabled = enabled;
    selectionTranslationToggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
    selectionTranslationToggle.title = enabled
      ? '划词翻译已开启：选中 PDF 文字后点击“译”'
      : '开启后，选中 PDF 文字可点击“译”进行翻译';
    if (!enabled) {
      selectionTranslationTarget = null;
      selectionTranslationAction.hidden = true;
    } else {
      scheduleCapture();
    }
  }
  function translateSelection() {
    const target = selectionTranslationTarget;
    if (!target || selectionTranslationBusy || !documentId || !main.ready) return;
    selectionTranslationBusy = true;
    selectionTranslationAction.hidden = true;
    controls();
    openPdfSelectionTranslationDialog({
      source: target.text,
      run: onEvent => hooks.translateSelection({ selection: target.text, page: target.page }, onEvent),
      onFinish: () => {
        selectionTranslationBusy = false;
        controls();
      },
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
    counter.textContent = busy
      ? (downloadOverlay.hidden ? '正在读取…' : downloadTitle.textContent || '正在读取…')
      : filename || '未打开文件';
    counter.title = filename;
    zoomLabel.textContent = Math.round(scale * 100) + '%';
    zoomOut.disabled = busy || !pane.ready || scale <= MIN_ZOOM + 1e-6;
    zoomIn.disabled = busy || !pane.ready || scale >= MAX_ZOOM - 1e-6;
    fitWidth.disabled = busy || !pane.ready;
    annotateButtons.forEach(button => { button.disabled = busy || !main.ready || !documentId; });
    askAiBtn.disabled = busy || !main.ready || !documentId;
    translationToggle.disabled = busy || !main.ready || !documentId;
    selectionTranslationToggle.disabled = busy || selectionTranslationBusy || !main.ready || !documentId;
    notebookToggle.disabled = busy || !documentId;
    outlineToggle.disabled = busy || outlineBusy || !main.ready || !documentId;
    templateButton.disabled = busy || !documentId;
    summaryToggle.disabled = busy || summaryBusy || !main.ready || !documentId || !notebookEditor;
    splitBtn.disabled = busy || !main.ready || !documentId || (!fileBytes && !rangeSource);
    splitBtn.setAttribute('aria-pressed', split ? 'true' : 'false');
    splitBtn.textContent = split ? '关闭浏览' : '复制浏览';
    splitBtn.title = split ? '关闭左侧对照窗格' : '左右对照同一份 PDF';
    updateReadingHistoryControls();
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
  function updateReadingHistoryControls() {
    const update = (controls: typeof mainHistoryControls, history: ReadingHistory, enabled: boolean) => {
      controls.back.disabled = busy || !enabled || history.index <= 0;
      controls.forward.disabled = busy || !enabled || history.index < 0 || history.index >= history.entries.length - 1;
    };
    update(mainHistoryControls, readingHistories.main, main.ready);
    update(copyHistoryControls, readingHistories.copy, split && copy.ready);
  }
  function saveReadingHistory() {
    persistPdfViewState({
      readingHistory: readingHistories.main.entries,
      readingHistoryIndex: readingHistories.main.index,
      copyReadingHistory: readingHistories.copy.entries,
      copyReadingHistoryIndex: readingHistories.copy.index,
    });
    updateReadingHistoryControls();
  }
  function recordReadingPosition(pane: Pane = main) {
    if (restoringReadingHistory || !documentId || !pane.ready || busy || container.hidden) return;
    const history = readingHistories[pane.id];
    const page = pane.viewer.currentPageNumber || 1;
    const next = { page, scrollTop: Math.max(0, Math.round(pane.scroller.scrollTop)) };
    const current = history.entries[history.index];
    if (current?.page === next.page) {
      current.scrollTop = next.scrollTop;
      saveReadingHistory();
      return;
    }
    history.entries = history.entries.slice(0, history.index + 1);
    history.entries.push(next);
    if (history.entries.length > 5) history.entries.shift();
    history.index = history.entries.length - 1;
    saveReadingHistory();
  }
  function restoreReadingPosition(paneId: PaneId, index: number) {
    const pane = paneId === 'main' ? main : copy;
    const history = readingHistories[paneId];
    const target = history.entries[index];
    const loadedPdf = paneId === 'main' ? pdf : copyPdf;
    if (!target || !pane.ready || !loadedPdf) return;
    recordReadingPosition(pane);
    history.index = index;
    restoringReadingHistory = true;
    setActive(paneId);
    pane.viewer.currentPageNumber = target.page;
    pane.viewer.scrollPageIntoView({ pageNumber: target.page });
    requestAnimationFrame(() => requestAnimationFrame(() => {
      pane.scroller.scrollTop = target.scrollTop;
      restoringReadingHistory = false;
      saveReadingHistory();
      controls();
    }));
  }
  function restoreAssetUi(id = documentId) {
    const state = pdfViewState(id);
    const storedRatio = Number(state.splitRatio);
    const storedWidth = Number(state.notebookWidth);
    const storedTranslationWidth = Number(state.translationWidth);
    splitRatio = Number.isFinite(storedRatio) && storedRatio > 0 && storedRatio < 1 ? storedRatio : 0.5;
    notebookWidth = Number.isFinite(storedWidth) && storedWidth >= MIN_NOTEBOOK_WIDTH ? storedWidth : 360;
    translationWidth = Number.isFinite(storedTranslationWidth) && storedTranslationWidth >= MIN_TRANSLATION_WIDTH ? storedTranslationWidth : 460;
    generatedOutline = Array.isArray(state.generatedOutline)
      ? state.generatedOutline.filter((item): item is PdfOutlineItem => Boolean(item)
        && typeof item.id === 'string'
        && typeof item.title === 'string'
        && Number.isInteger(item.page)
        && Number.isInteger(item.level))
      : [];
    const restoreHistory = (entries: unknown, savedIndex: unknown): ReadingHistory => {
      const validEntries = Array.isArray(entries)
        ? entries.filter((item): item is { page: number; scrollTop: number } => Boolean(item)
          && Number.isInteger(item.page) && item.page > 0
          && Number.isFinite(item.scrollTop) && item.scrollTop >= 0).slice(-5)
        : [];
      const index = Number(savedIndex);
      return {
        entries: validEntries,
        index: validEntries.length
          ? Math.min(validEntries.length - 1, Math.max(0, Number.isInteger(index) ? index : validEntries.length - 1))
          : -1,
      };
    };
    readingHistories.main = restoreHistory(state.readingHistory, state.readingHistoryIndex);
    readingHistories.copy = restoreHistory(state.copyReadingHistory, state.copyReadingHistoryIndex);
    updateReadingHistoryControls();
    applySplitRatio(splitRatio);
    applyNotebookWidth(notebookWidth);
    applyTranslationWidth(translationWidth);
    setNotebookOpen(state.notebookOpen === true, false);
  }
  function scaleOf(pane: Pane) {
    const scaleValue = pane.viewer.currentScaleValue;
    return scaleValue === 'page-width' ? 'page-width' : pane.viewer.currentScale;
  }
  function rememberView(force = false) {
    const key = viewKey();
    // 恢复上次位置期间，强制保存也会把刚被 pdf.js 拉回的第 1 页写进去。
    if (restoringView) return;
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
    // 页码相同也会重新滚到该页。pdf.js 的 update() 可能只改了滚动位置、没改页码。
    pane.viewer.currentPageNumber = page as number;
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
        restoringView = true;
        const epoch = viewEpoch;
        const tokenAtInit = openingToken;
        applyStoredScale(pane, stored?.scale);
        if (stored?.split) pendingSplit = true;
        const storedPage = page;
        jumpToStoredPage(pane, storedPage, pdf?.numPages || 0);
        if (stored?.split) {
          const focus: PaneId = stored.active === 'main' ? 'main' : 'copy';
          void setSplit(true, focus).finally(() => { pendingSplit = null; });
        }
        // pdf.js 在 pagesinit 返回后会立刻 update()，此时页面高度可能还没排好，可见页会变回 1。
        // 等下一帧再跳一次，并在此之前禁止 rememberView。
        requestAnimationFrame(() => {
          if (epoch !== viewEpoch) return;
          applyStoredScale(pane, pdfViewState().scale);
          jumpToStoredPage(pane, storedPage, pdf?.numPages || 0);
          requestAnimationFrame(() => {
            if (epoch !== viewEpoch) return;
            jumpToStoredPage(pane, storedPage, pdf?.numPages || 0);
            // 滚动触发的 pdf.js update() 在下一帧。先别放开记录，否则会把第 1 页写回去。
            requestAnimationFrame(() => {
              if (epoch !== viewEpoch) return;
              restoringView = false;
              recordReadingPosition(main);
              if (openingToken === tokenAtInit) opening = false;
              controls();
            });
          });
        });
      } else {
        const stored = pdfViewState();
        applyStoredScale(pane, stored?.copyScale);
        jumpToStoredPage(pane, stored?.copyPage, copyPdf?.numPages || 0);
        requestAnimationFrame(() => recordReadingPosition(copy));
      }
      controls();
    });
    pane.eventBus.on('pagechanging', ({ pageNumber }: { pageNumber?: number }) => {
      if (active() === pane) { clearSelection(); controls(); }
      // PDF.js 在派发事件时 currentPageNumber 仍可能是前一页，必须使用事件页码。
      if (pane.id === 'main' && translationOpen) {
        renderTranslationPage(Number.isInteger(pageNumber) ? pageNumber : pane.viewer.currentPageNumber || 1);
      }
      rememberView();
      requestAnimationFrame(() => recordReadingPosition(pane));
    });
    pane.eventBus.on('scalechanging', () => {
      if (active() === pane) controls();
      rememberView();
    });
    pane.eventBus.on('pagerendered', ({ error: renderError }: { error?: Error }) => {
      if (renderError) error('PDF 页面渲染失败：' + renderError.message);
      if (pane.id === 'main' && readingDoc && pane.viewer.pdfDocument === readingDoc) showReading(null, true);
    });
    pane.eventBus.on('annotationlayerrendered', ({ source }: { source: { div: HTMLElement } }) => {
      void bindAnnotUi(source.div, pane).catch(e => error(String(e)));
      paintLive(source.div, Number(source.div.dataset.pageNumber), pane);
    });
    pane.pages.addEventListener('pointerdown', event => {
      if (event.button > 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('.pdf-live-note, .pdf-note-badge, .textAnnotation, .pdf-notebook-panel')) return;
      const pageDiv = target?.closest<HTMLElement>('.page') ?? null;
      if (!pageDiv || !pane.pages.contains(pageDiv)) return;
      const hit = highlightAtClick(pageDiv, pane, event.clientX, event.clientY, target);
      if (!hit) return;
      const caret = notebookCaret();
      if (caret === null) return;
      event.preventDefault();
      event.stopPropagation();
      setActive(pane.id);
      if (hit.text.trim()) {
        insertHighlightAtCursor(hit.text, caret);
        return;
      }
      void textFromPdfItems(hit.pageNumber, hit.boxes).then(text => {
        insertHighlightAtCursor(text, caret);
      }).catch(() => {});
    }, true);
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
      if (!pane.ready || opening || restoringView || container.hidden || pane.root.hidden || !pane.scroller.clientWidth) return;
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
      else selectionTranslationAction.hidden = true;
      return;
    }
    showSelectionTranslationAction(snap);
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
  function showSelectionTranslationAction(snap: { page: number; text: string; anchor?: DOMRect }) {
    if (!selectionTranslationEnabled || selectionTranslationBusy || !documentId || !snap.anchor) {
      selectionTranslationAction.hidden = true;
      return;
    }
    selectionTranslationTarget = { page: snap.page, text: snap.text.trim() };
    if (!selectionTranslationTarget.text) {
      selectionTranslationAction.hidden = true;
      return;
    }
    const width = 30;
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, snap.anchor.right + 8));
    const top = Math.max(8, Math.min(window.innerHeight - 36, snap.anchor.top - 2));
    selectionTranslationAction.style.left = `${left}px`;
    selectionTranslationAction.style.top = `${top}px`;
    selectionTranslationAction.hidden = false;
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
    const rects = Array.from(selection.getRangeAt(0).getClientRects())
        .filter(rect => rect.width > 0 && rect.height > 0)
    const boxes = mergeSelectionBoxes(
      rects.map(rect => {
          const a = viewport.convertToPdfPoint(rect.left - bounds.left, rect.top - bounds.top);
          const b = viewport.convertToPdfPoint(rect.right - bounds.left, rect.bottom - bounds.top);
          return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
      }),
    );
    if (!boxes.length) return null;
    return { page: number, boxes, text: selection.toString(), anchor: rects[rects.length - 1] };
  }
  container.addEventListener('keydown', e => {
    if (!active().ready || busy || !(e.ctrlKey || e.metaKey)) return;
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key.toLowerCase() === 's' && translationOpen) {
      e.preventDefault();
      void flushTranslationSave();
      return;
    }
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
  function quadsFrom(section: HTMLElement): PixelBox[] {
    const sectionBox = section.getBoundingClientRect();
    const quads = [...section.querySelectorAll('clipPath rect')].map(rect => ({
      x: Number(rect.getAttribute('x')) || 0,
      y: Number(rect.getAttribute('y')) || 0,
      width: Number(rect.getAttribute('width')) || 0,
      height: Number(rect.getAttribute('height')) || 0,
    }));
    return quadsToBoxes(sectionBox, quads);
  }
  function pageHighlightText(pageDiv: HTMLElement, anchor: HTMLElement) {
    const xref = Number(anchor.dataset.xref || anchor.dataset.annotationId?.replace(/R$/, '') || 0);
    const live = liveAnnots.find(item => item.xref === xref && item.kind === 'highlight');
    if (live?.quote) return live.quote;
    const marks = xref
      ? [...pageDiv.querySelectorAll<HTMLElement>(`.pdf-highlight-mark[data-xref="${xref}"]`)]
      : [];
    const quote = anchor.dataset.quote || marks.find(node => node.dataset.quote)?.dataset.quote;
    if (quote?.trim()) return quote;
    const rects = (marks.length
      ? marks.map(node => node.getBoundingClientRect())
      : quadsFrom(anchor.closest<HTMLElement>('.highlightAnnotation') || anchor)
    ).map(box => ({
      left: box.left - 3,
      top: box.top - 3,
      right: box.right + 3,
      bottom: box.bottom + 3,
      width: box.width + 6,
      height: box.height + 6,
    }));
    const layer = pageDiv.querySelector<HTMLElement>('.textLayer');
    if (!layer) return '';
    const spans = [...layer.querySelectorAll<HTMLElement>('span')].flatMap(span => {
      if (span.classList.contains('markedContent') || span.querySelector('span')) return [];
      return [{
        text: span.textContent || '',
        box: span.getBoundingClientRect(),
        rtl: getComputedStyle(span).direction === 'rtl',
      }];
    });
    return textCoveredByHighlight(spans, rects);
  }
  function highlightTextAt(pageDiv: HTMLElement, mark: HTMLElement, x: number, y: number) {
    const covered = pageHighlightText(pageDiv, mark);
    if (covered.trim()) return covered;
    const span = document.elementsFromPoint(x, y).find((node): node is HTMLElement => (
      node instanceof HTMLElement
      && node.tagName === 'SPAN'
      && !node.classList.contains('markedContent')
      && !!node.closest('.textLayer')
    ));
    return span?.textContent?.trim() || '';
  }
  function notebookCaret() {
    if (notebookPanel.hidden) return null;
    ensureNotebookEditor();
    const editor = notebookEditor;
    if (!editor || editor.isDestroyed) return null;
    if (editor.isFocused) return editor.state.selection.from;
    if (notebookInsertAt !== null) return notebookInsertAt;
    return editor.state.selection.from;
  }
  function insertHighlightAtCursor(text: string, caret: number) {
    const editor = notebookEditor;
    const content = highlightInsertContent(text);
    if (!content || !editor || editor.isDestroyed) return;
    const doc = editor.state.doc;
    const limit = Math.max(1, doc.content.size - 1);
    const clamped = Math.min(Math.max(1, caret), limit);
    let position = clamped;
    try {
      position = TextSelection.near(doc.resolve(clamped)).from;
    } catch { /* 夹紧后的位置 */ }
    const before = doc.textContent;
    try {
      editor.chain().insertContentAt(position, content, { updateSelection: true }).run();
    } catch { /* 下面改用纯文本 */ }
    if (editor.state.doc.textContent === before) {
      const plain = text.replace(/\s+/g, ' ').trim();
      if (!plain) return;
      try {
        editor.view.dispatch(editor.state.tr.insertText(plain, position).scrollIntoView());
      } catch {
        return;
      }
    }
    if (editor.state.doc.textContent === before) return;
    notebookInsertAt = editor.state.selection.from;
    try {
      editor.chain().setTextSelection(notebookInsertAt).scrollIntoView().run();
    } catch { /* 文字已经写入 */ }
    queueNotebookSave(editor.getJSON());
  }
  function highlightUnderPoint(pageDiv: HTMLElement, x: number, y: number) {
    let best: HTMLElement | null = null;
    let bestArea = Infinity;
    for (const mark of pageDiv.querySelectorAll<HTMLElement>('.pdf-highlight-mark, .highlightAnnotation, .pdf-live-highlight')) {
      const box = mark.getBoundingClientRect();
      if (box.width < 1 || box.height < 1) continue;
      if (x < box.left || x > box.right || y < box.top || y > box.bottom) continue;
      const area = box.width * box.height;
      if (area < bestArea) {
        best = mark;
        bestArea = area;
      }
    }
    return best;
  }
  function clientToPdf(pageDiv: HTMLElement, pane: Pane, clientX: number, clientY: number) {
    const pageNumber = Number(pageDiv.dataset.pageNumber);
    const pageView = pane.viewer.getPageView(pageNumber - 1);
    const viewport = pageView?.viewport as Viewport | undefined;
    const layer = pageDiv.querySelector<HTMLElement>('.textLayer') || pageDiv;
    if (!viewport || !pageNumber) return null;
    const bounds = layer.getBoundingClientRect();
    const [x, y] = viewport.convertToPdfPoint(clientX - bounds.left, clientY - bounds.top);
    return { pageNumber, viewport, bounds, x, y, layer };
  }
  function pdfBoxesToClient(viewport: Viewport, bounds: DOMRect, boxes: number[][]): PixelBox[] {
    return boxes.flatMap(box => {
      const a = viewport.convertToViewportPoint(box[0], box[1]);
      const b = viewport.convertToViewportPoint(box[2], box[3]);
      const left = bounds.left + Math.min(a[0], b[0]);
      const top = bounds.top + Math.min(a[1], b[1]);
      const right = bounds.left + Math.max(a[0], b[0]);
      const bottom = bounds.top + Math.max(a[1], b[1]);
      const width = right - left;
      const height = bottom - top;
      if (width < 0.5 || height < 0.5) return [];
      return [{ left, top, right, bottom, width, height }];
    });
  }
  function textFromClientRects(layer: HTMLElement | null, rects: PixelBox[]) {
    if (!layer || !rects.length) return '';
    const expanded = rects.map(box => ({
      left: box.left - 3,
      top: box.top - 3,
      right: box.right + 3,
      bottom: box.bottom + 3,
      width: box.width + 6,
      height: box.height + 6,
    }));
    const spans = [...layer.querySelectorAll<HTMLElement>('span')].flatMap(span => {
      if (span.classList.contains('markedContent') || span.querySelector('span')) return [];
      return [{
        text: span.textContent || '',
        box: span.getBoundingClientRect(),
        rtl: getComputedStyle(span).direction === 'rtl',
      }];
    });
    return textCoveredByHighlight(spans, expanded);
  }
  function highlightAtClick(pageDiv: HTMLElement, pane: Pane, clientX: number, clientY: number, target: Element | null) {
    const point = clientToPdf(pageDiv, pane, clientX, clientY);
    if (point) {
      const hits: Array<{ area: number; quote: string; boxes: number[][] }> = [];
      const consider = (boxes: number[][], quote: string) => {
        if (!boxes.length || !pointInPdfBoxes(point.x, point.y, boxes)) return;
        const area = boxes.reduce((sum, box) => sum + Math.abs(box[2] - box[0]) * Math.abs(box[3] - box[1]), 0);
        hits.push({ area, quote, boxes });
      };
      for (const annot of liveAnnots) {
        if (annot.page !== point.pageNumber) continue;
        consider(annot.boxes, annot.kind === 'highlight' ? (annot.quote || '') : '');
      }
      for (const annot of fileHighlights) {
        if (annot.page !== point.pageNumber) continue;
        consider(annot.boxes, '');
      }
      hits.sort((a, b) => Number(Boolean(b.quote)) - Number(Boolean(a.quote)) || (a.area - b.area));
      const best = hits[0];
      if (best) {
        const text = best.quote.trim() || textFromClientRects(point.layer, pdfBoxesToClient(point.viewport, point.bounds, best.boxes));
        return { pageNumber: point.pageNumber, boxes: best.boxes, text };
      }
    }
    const mark = target?.closest<HTMLElement>('.pdf-highlight-mark, .highlightAnnotation, .pdf-live-highlight')
      || highlightUnderPoint(pageDiv, clientX, clientY);
    if (!mark || !pageDiv.contains(mark)) return null;
    return {
      pageNumber: point?.pageNumber || Number(pageDiv.dataset.pageNumber) || 0,
      boxes: [],
      text: highlightTextAt(pageDiv, mark, clientX, clientY),
    };
  }
  async function textFromPdfItems(pageNumber: number, boxes: number[][]) {
    if (!pdf || !pageNumber || !boxes.length) return '';
    const page = await pdf.getPage(pageNumber);
    const content = await page.getTextContent();
    const items = content.items.flatMap(item => {
      if (!('str' in item) || !item.str || !item.transform) return [];
      const height = item.height || Math.abs(item.transform[3]) || 8;
      return [{ str: item.str, x: item.transform[4], y: item.transform[5], width: item.width || 0, height }];
    });
    return textItemsInBoxes(items, boxes);
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
          if (annot.quote) mark.dataset.quote = annot.quote;
          mark.title = '插入到笔记光标处，右键删除';
          mark.setAttribute('aria-label', '把高亮文字插入笔记');
          Object.assign(mark.style, boxStyle(viewport, box));
          mark.addEventListener('mousedown', e => e.preventDefault());
          mark.addEventListener('contextmenu', open);
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
    fileHighlights = fileHighlights.filter(item => !ids.includes(item.xref));
    for (const pane of panes()) {
      for (const xref of ids) {
        pane.pages.querySelectorAll(`[data-xref="${xref}"], [data-annotation-id="${xref}R"]`).forEach(node => node.remove());
      }
    }
  }
  async function createAnnot(kind: AnnotKind, target: NonNullable<ReturnType<typeof selectionBoxes>>, content: string) {
    const quote = kind === 'highlight' ? String(selected?.selection?.text || '').trim() : '';
    await withBusy(async () => {
      const result = await hooks.onAnnotate({ kind, page: target.page, boxes: target.boxes, content });
      if (!result?.xref) return;
      if (typeof result.revision === 'number') assetRevision = result.revision;
      liveAnnots.push({
        xref: result.xref,
        markXref: result.mark_xref && result.mark_xref > 0 ? result.mark_xref : undefined,
        kind, page: target.page, boxes: target.boxes, content,
        quote: quote || undefined,
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
    const highlightMeta = meta as Array<{
      id?: string; subtype?: string; annotationType?: number;
      quadPoints?: ArrayLike<number>; rect?: number[];
      title?: string; titleObj?: { str?: string };
    }>;
    const highlightIds = new Set(
      [...layer.querySelectorAll<HTMLElement>('.highlightAnnotation')].map(node => node.dataset.annotationId || ''),
    );
    fileHighlights = fileHighlights.filter(item => item.page !== pageNumber);
    for (const item of highlightMeta) {
      const id = String(item.id || '');
      const isHighlight = item.subtype === 'Highlight' || item.annotationType === 9 || highlightIds.has(id);
      if (!isHighlight) continue;
      const boxes = annotationPdfBoxes(item.quadPoints, item.rect);
      if (!boxes.length) continue;
      const title = (typeof item.title === 'string' && item.title) || item.titleObj?.str || '';
      fileHighlights.push({ page: pageNumber, xref: xrefOf(id) || 0, boxes, title });
    }
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
      if (titleOf(id) === 'office-text-mark') {
        section.classList.add('pdf-highlight-mark');
        section.dataset.pdfHighlightAction = '1';
        section.title = '插入到笔记光标处';
        return;
      }
      if (section.dataset.pdfHighlightAction === '1') return;
      section.dataset.pdfHighlightAction = '1';
      section.classList.add('pdf-highlight-mark');
      section.title = '插入到笔记光标处，右键删除';
      section.setAttribute('aria-label', '把高亮文字插入笔记');
      section.addEventListener('contextmenu', open, true);
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

  function pdfjsOptions(extra: Record<string, unknown>) {
    return {
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/',
      wasmUrl: '/pdfjs/wasm/',
      ...extra,
    };
  }
  function rangeOptions(source: NonNullable<typeof rangeSource>) {
    return pdfjsOptions({
      url: source.url,
      httpHeaders: source.headers,
      length: source.length,
      disableAutoFetch: true,
      disableStream: true,
      rangeChunkSize: 65536,
    });
  }
  async function openCopy() {
    const source = rangeSource;
    if (!fileBytes && !source) return;
    const task = source
      ? getDocument(rangeOptions(source))
      : getDocument(pdfjsOptions({ data: new Uint8Array(cloneBuffer(fileBytes!)) }));
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

  function beginReading() {
    readingClosed = false;
  }

  function showReading(progress?: PdfFileProgress | null, done = false, label = '正在读取 PDF', detail?: string) {
    if (done) {
      readingDoc = null;
      setDownloadProgress(null);
      return;
    }
    if (readingClosed) return;
    const total = progress?.total ?? null;
    const loaded = progress?.loaded ?? 0;
    const known = Boolean(total && total > 0 && loaded > 0);
    const complete = Boolean(known && loaded >= (total as number));
    if (!known || (complete && !detail)) {
      setDownloadProgress(
        { loaded: 0, total: null, source: progress?.source || 'network' },
        label,
        detail || (complete ? '正在解析首页…' : '正在读取…'),
      );
      return;
    }
    setDownloadProgress({ loaded, total, source: progress!.source }, label, detail);
  }

  function setDownloadProgress(progress: PdfFileProgress | null, label?: string, detail?: string) {
    if (!progress) {
      readingClosed = true;
      readingDoc = null;
      downloadOverlay.hidden = true;
      downloadOverlay.classList.remove('is-indeterminate');
      downloadBar.style.width = '0%';
      downloadBar.style.transform = '';
      controls();
      return;
    }
    downloadOverlay.hidden = false;
    downloadTitle.textContent = label || '正在读取 PDF';
    const total = progress.total;
    const ratio = total && total > 0 ? Math.min(1, progress.loaded / total) : null;
    if (ratio === null) {
      downloadOverlay.classList.add('is-indeterminate');
      downloadBar.style.width = '';
      downloadMeta.textContent = detail || (progress.loaded > 0
        ? `已接收 ${formatBytes(progress.loaded)}`
        : '正在读取…');
    } else {
      downloadOverlay.classList.remove('is-indeterminate');
      downloadBar.style.transform = '';
      downloadBar.style.width = `${Math.round(ratio * 100)}%`;
      downloadMeta.textContent = detail || `${formatBytes(progress.loaded)} / ${formatBytes(total!)}（${Math.round(ratio * 100)}%）`;
    }
    controls();
  }

  function indexStatusText(status: PdfIndexStatus, active: boolean) {
    if (status.status === 'failed') {
      const done = status.progress_done || 0;
      const total = status.progress_total || 0;
      const where = total > 0 ? `（已完成 ${done}/${total} 段）` : '';
      return `索引失败${where}：${status.error || '请稍后重试'}`;
    }
    const pending = status.status === 'indexing';
    if (!pending) {
      if (!active || status.status !== 'ready') return '';
      if (status.empty) return `索引完成：${status.error || '未提取到可检索文本'}`;
      return `索引完成：${status.page_count || 0} 页，${status.chunk_count || 0} 段`;
    }
    if ((status.phase || '') !== 'embedding' || !(status.progress_total || 0)) {
      const pages = status.page_count || 0;
      return pages > 0 ? `正在提取文本，已解析 ${pages} 页` : '正在提取文本…';
    }
    const batchSize = status.batch_size || 256;
    const total = status.progress_total || 0;
    const done = status.progress_done || 0;
    const batches = Math.max(1, Math.ceil(total / batchSize));
    const finished = Math.floor(done / batchSize);
    const current = Math.min(batches, finished + (done < total ? 1 : 0));
    const pages = status.page_count || 0;
    const pageText = pages > 0 ? `已解析 ${pages} 页` : '';
    return `${pageText}${pageText ? '，' : ''}正在建立索引：第 ${current}/${batches} 批，已完成 ${done}/${total} 段`;
  }

  function stopIndexWatch() {
    indexWatch += 1;
    indexLine.hidden = true;
    indexLine.textContent = '';
    indexLine.classList.remove('is-failed');
  }

  async function watchIndex(id: string, token: number) {
    const gen = ++indexWatch;
    let sawWork = false;
    let waitingChecks = 0;
    for (;;) {
      if (gen !== indexWatch || token !== loadToken || documentId !== id) return;
      let status: PdfIndexStatus;
      try {
        status = await hooks.indexStatus(id);
      } catch {
        if (gen === indexWatch) indexLine.hidden = true;
        return;
      }
      if (gen !== indexWatch || token !== loadToken || documentId !== id) return;
      const pending = status.status === 'indexing';
      if (pending) sawWork = true;
      const text = indexStatusText(status, sawWork);
      indexLine.classList.toggle('is-failed', status.status === 'failed');
      if (text) {
        indexLine.hidden = false;
        indexLine.textContent = text;
      } else {
        indexLine.hidden = true;
        indexLine.textContent = '';
      }
      // Legacy asset metadata can remain pending after restart without any index job.
      // Briefly allow a newly submitted worker to start, without claiming it is extracting.
      if (status.status === 'missing' && status.asset_status === 'pending' && waitingChecks++ < 10) {
        await new Promise(resolve => window.setTimeout(resolve, 1200));
        continue;
      }
      if (!pending) {
        if (status.status === 'ready' && sawWork) {
          window.setTimeout(() => {
            if (gen !== indexWatch) return;
            indexLine.hidden = true;
          }, 5000);
        }
        return;
      }
      await new Promise(resolve => window.setTimeout(resolve, 1200));
    }
  }

  function followIndex(id: string, token: number) {
    void watchIndex(id, token);
  }

  function fingerprint(id: string, data: ArrayBuffer) {
    return `${id}:${data.byteLength}`;
  }

  function bufferReady(fp: string) {
    return openedFingerprint === fp && !!pdf && documentId.length > 0 && main.ready && (pdf.numPages || 0) > 0;
  }

  function isParkedTask(task: PDFDocumentLoadingTask | undefined) {
    if (!task) return false;
    for (const item of parkedPdfs.values()) {
      if (item.task === task) return true;
    }
    return false;
  }

  function parkCurrentPdf() {
    if (!documentId || !pdf || !loadingTask || !fileBytes) return;
    parkedPdfs.set(documentId, {
      id: documentId,
      revision: assetRevision,
      name: filename,
      bytes: cloneBuffer(fileBytes),
      pdf,
      task: loadingTask,
      fingerprint: openedFingerprint || fingerprint(documentId, fileBytes),
      usedAt: Date.now(),
    });
    pdf = undefined;
    loadingTask = undefined;
    fileBytes = null;
    rangeSource = null;
    openedFingerprint = '';
    main.ready = false;
  }

  async function releaseOpenDocument() {
    // 小文件留在内存里，回来不用重新解析。大文件没有整份字节，必须先销毁，否则阅读器会继续画旧页。
    if (documentId && pdf && loadingTask && fileBytes) parkCurrentPdf();
    const task = loadingTask && !isParkedTask(loadingTask) ? loadingTask : undefined;
    pdf = undefined;
    loadingTask = undefined;
    fileBytes = null;
    rangeSource = null;
    openedFingerprint = '';
    main.ready = false;
    readingDoc = null;
    try { main.linkService.setDocument(null as never); } catch { /* 无文档 */ }
    try { main.viewer.setDocument(null as never); } catch { /* 无文档 */ }
    if (task) {
      try { await task.destroy(); } catch { /* 已释放 */ }
    }
    main.pages.replaceChildren();
  }

  async function evictParkedPdfs(keepId?: string) {
    while (parkedPdfs.size > PARKED_PDF_LIMIT) {
      let oldest: ParkedPdf | null = null;
      for (const item of parkedPdfs.values()) {
        if (item.id === keepId || item.id === documentId) continue;
        if (!oldest || item.usedAt < oldest.usedAt) oldest = item;
      }
      if (!oldest) break;
      parkedPdfs.delete(oldest.id);
      try { await oldest.task.destroy(); } catch { /* 已释放 */ }
    }
  }

  async function activateParkedPdf(session: ParkedPdf, token: number) {
    if (token !== loadToken) return;
    session.usedAt = Date.now();
    parkedPdfs.set(session.id, session);
    busy = true;
    opening = true;
    openingToken = token;
    input.disabled = true;
    liveAnnots = []; fileHighlights = [];
    clearSelection();
    selected = null;
    documentId = session.id;
    filename = session.name;
    assetRevision = session.revision;
    fileBytes = cloneBuffer(session.bytes);
    rangeSource = null;
    pdf = session.pdf;
    loadingTask = session.task;
    openedFingerprint = session.fingerprint;
    main.ready = false;
    empty.remove();
    viewEpoch += 1;
    setDownloadProgress(null);
    main.linkService.setDocument(session.pdf);
    main.viewer.setDocument(session.pdf);
    busy = false;
    input.disabled = false;
    controls();
    changed();
  }

  async function openBuffer(id: string, name: string, data: ArrayBuffer, token: number) {
    if (token !== loadToken) return;
    const fp = fingerprint(id, data);
    // 仅当页面已真正渲染就绪时跳过；否则缓存命中会短路导致白屏。
    if (bufferReady(fp) && documentId === id) {
      showReading(null, true);
      return;
    }
    // 本地缓存和网络会各打开一次。第二次 setDocument 会把阅读位置重置到第 1 页。
    if (inflightOpen?.fp === fp) {
      const opened = await inflightOpen.promise;
      if (token !== loadToken) return;
      if (opened && documentId === id && openedFingerprint === fp) {
        if (main.ready) showReading(null, true);
        else if (pdf) readingDoc = pdf;
        return;
      }
    }
    let finishOpen!: (opened: boolean) => void;
    const promise = new Promise<boolean>(resolve => { finishOpen = resolve; });
    inflightOpen = { fp, promise };
    busy = true; opening = true; openingToken = token; input.disabled = true; liveAnnots = []; fileHighlights = []; clearSelection(); controls();
    showReading(null);
    let task: PDFDocumentLoadingTask | undefined;
    try {
      // PDF.js 可能转移/改写缓冲；fileBytes 另存一份给对照窗格。
      fileBytes = cloneBuffer(data);
      rangeSource = null;
      task = getDocument(pdfjsOptions({ data: new Uint8Array(cloneBuffer(data)) }));
      task.onProgress = (progress: { loaded?: number; total?: number }) => {
        if (token !== loadToken) return;
        const loaded = Number(progress?.loaded) || 0;
        const total = Number(progress?.total) || 0;
        showReading(total > 0 ? { loaded, total, source: 'network' } : null);
      };
      const loaded = await task.promise;
      if (token !== loadToken) {
        await task.destroy();
        if (openingToken === token) {
          opening = false;
          restoringView = false;
        }
        finishOpen(false);
        return;
      }
      main.ready = false;
      const previous = loadingTask;
      loadingTask = task; pdf = loaded;
      filename = name; documentId = id;
      openedFingerprint = fingerprint(id, data);
      empty.remove();
      viewEpoch += 1;
      main.linkService.setDocument(loaded);
      readingDoc = loaded;
      showReading(null);
      main.viewer.setDocument(loaded);
      // 上一个文档若已挂入 parked，勿 destroy；否则才释放。
      if (previous && !isParkedTask(previous)) await previous.destroy();
      parkedPdfs.set(id, {
        id, revision: assetRevision, name, bytes: cloneBuffer(data),
        pdf: loaded, task, fingerprint: openedFingerprint, usedAt: Date.now(),
      });
      await evictParkedPdfs(id);
      finishOpen(true);
    } catch (e) {
      if (token !== loadToken) {
        if (openingToken === token) {
          opening = false;
          restoringView = false;
        }
        finishOpen(false);
        return;
      }
      if (openingToken === token) {
        opening = false;
        restoringView = false;
      }
      fileBytes = null;
      rangeSource = null;
      openedFingerprint = '';
      if (task && task !== loadingTask && !isParkedTask(task)) await task.destroy();
      error('PDF 打开失败：' + String(e));
      showReading(null, true);
      finishOpen(false);
    } finally {
      if (inflightOpen?.promise === promise) inflightOpen = null;
      if (token === loadToken) {
        busy = false; input.disabled = false; input.value = ''; controls();
      }
    }
  }
  async function openRange(id: string, name: string, revision: number, length: number, token: number) {
    if (token !== loadToken) return;
    const fp = `${id}:range:${revision}:${length}`;
    if (bufferReady(fp) && documentId === id) {
      showReading(null, true);
      return;
    }
    if (inflightOpen?.fp === fp) {
      const opened = await inflightOpen.promise;
      if (token !== loadToken) return;
      if (opened && documentId === id && openedFingerprint === fp) {
        if (main.ready) showReading(null, true);
        else if (pdf) readingDoc = pdf;
        return;
      }
    }
    let finishOpen!: (opened: boolean) => void;
    const promise = new Promise<boolean>(resolve => { finishOpen = resolve; });
    inflightOpen = { fp, promise };
    busy = true; opening = true; openingToken = token; input.disabled = true; liveAnnots = []; fileHighlights = []; clearSelection(); controls();
    beginReading();
    showReading(
      length > 0 ? { loaded: 0, total: length, source: 'network' } : null,
      false,
      '正在打开页面',
      '按页读取，当前页显示后即可阅读',
    );
    const source = { ...pdfFileRequest(id, revision), length };
    rangeSource = source;
    fileBytes = null;
    let task: PDFDocumentLoadingTask | undefined;
    try {
      task = getDocument(rangeOptions(source));
      task.onProgress = (progress: { loaded?: number; total?: number }) => {
        if (token !== loadToken) return;
        const loaded = Number(progress?.loaded) || 0;
        const total = Number(progress?.total) || length;
        const percent = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
        showReading(
          total > 0 ? { loaded, total, source: 'network' } : null,
          false,
          '正在打开页面',
          total > 0
            ? `已获取 ${formatBytes(loaded)} / ${formatBytes(total)}（${percent}%），按页读取，当前页显示后即可阅读`
            : undefined,
        );
      };
      const loaded = await task.promise;
      if (token !== loadToken) {
        await task.destroy();
        if (rangeSource?.url === source.url) rangeSource = null;
        if (openingToken === token) {
          opening = false;
          restoringView = false;
        }
        finishOpen(false);
        return;
      }
      main.ready = false;
      const previous = loadingTask;
      loadingTask = task; pdf = loaded;
      filename = name; documentId = id;
      openedFingerprint = fp;
      empty.remove();
      viewEpoch += 1;
      main.linkService.setDocument(loaded);
      readingDoc = loaded;
      showReading(null);
      main.viewer.setDocument(loaded);
      if (previous && !isParkedTask(previous)) await previous.destroy();
      finishOpen(true);
    } catch (e) {
      if (token !== loadToken) {
        if (openingToken === token) {
          opening = false;
          restoringView = false;
        }
        finishOpen(false);
        return;
      }
      if (openingToken === token) {
        opening = false;
        restoringView = false;
      }
      fileBytes = null;
      rangeSource = null;
      openedFingerprint = '';
      if (task && task !== loadingTask && !isParkedTask(task)) await task.destroy();
      error('PDF 打开失败：' + String(e));
      showReading(null, true);
      finishOpen(false);
    } finally {
      if (inflightOpen?.promise === promise) inflightOpen = null;
      if (token === loadToken) {
        busy = false; input.disabled = false; input.value = ''; controls();
      }
    }
  }
  async function showEmpty(id = '', name = '') {
    await releaseOpenDocument();
    filename = name; documentId = id; selected = null; liveAnnots = []; fileHighlights = [];
    setDownloadProgress(null);
    stopIndexWatch();
    if (!empty.isConnected) main.scroller.prepend(empty);
    controls(); changed();
  }
  window.addEventListener('office-pdf-upload', (event: Event) => {
    const detail = (event as CustomEvent<PdfFileProgress>).detail;
    if (!detail) return;
    beginReading();
    const total = detail.total;
    const loaded = detail.loaded;
    const percent = total && total > 0 ? Math.round((loaded / total) * 100) : 0;
    const text = total && total > 0
      ? `已发送 ${formatBytes(loaded)} / ${formatBytes(total)}（${percent}%）`
      : `已发送 ${formatBytes(loaded)}`;
    showReading(detail, false, '正在上传 PDF', text);
  });
  input.onchange = async () => {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > MAX_PDF) { error('PDF 不能超过 200 MB。'); return; }
    beginReading();
    showReading(null, false, '正在准备上传', '等待发送文件…');
    try { await hooks.onPickFile(file); } catch (e) {
      showReading(null, true);
      error(e instanceof Error ? e.message : String(e));
    }
  };
  function goTo(number: number) {
    const pane = active();
    if (!pane.ready || busy || !pdf) return;
    if (!Number.isInteger(number) || number < 1 || number > pdf.numPages) {
      pageInput.value = String(pane.viewer.currentPageNumber);
      error('页码应为 1 至 ' + pdf.numPages + ' 的整数。');
      return;
    }
    recordReadingPosition(pane);
    clearSelection();
    window.getSelection()?.removeAllRanges();
    pane.viewer.scrollPageIntoView({ pageNumber: number });
    controls();
  }
  function jumpToOutlinePage(page: number) {
    if (!main.ready || busy || !pdf) {
      error('PDF 尚未就绪，暂时无法定位。');
      return;
    }
    if (page > pdf.numPages) {
      error(`该大纲页码超出当前 PDF 的 ${pdf.numPages} 页范围。`);
      return;
    }
    recordReadingPosition(main);
    setActive('main');
    clearSelection();
    window.getSelection()?.removeAllRanges();
    // 显式设置当前页并滚动，避免 PDF.js 在连续滚动视图中忽略纯 scrollPageIntoView 调用。
    main.viewer.currentPageNumber = page;
    main.viewer.scrollPageIntoView({ pageNumber: page });
    requestAnimationFrame(() => main.viewer.scrollPageIntoView({ pageNumber: page }));
    controls();
  }
  const outlineTarget = (target: EventTarget | null) => {
    const element = target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
    return element?.closest<HTMLElement>('[data-pdf-outline-page]') || null;
  };
  const jumpToOutlineReference = (event: Event) => {
    const target = outlineTarget(event.target);
    const page = Number(target?.dataset.pdfOutlinePage);
    if (!target || !Number.isInteger(page) || page < 1) return;
    event.preventDefault();
    event.stopPropagation();
    jumpToOutlinePage(page);
  };
  document.addEventListener('click', jumpToOutlineReference, true);
  document.addEventListener('pointerup', jumpToOutlineReference, true);
  notebookEditorHost.addEventListener('click', jumpToOutlineReference, true);
  notebookEditorHost.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') jumpToOutlineReference(event);
  }, true);
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
  translationToggle.onclick = () => { void setTranslationOpen(translationPanel.hidden); };
  selectionTranslationToggle.onclick = () => setSelectionTranslationEnabled(!selectionTranslationEnabled);
  selectionTranslationAction.addEventListener('mousedown', event => event.preventDefault());
  selectionTranslationAction.onclick = () => translateSelection();
  translatePageButton.onclick = () => { void translateCurrentPage(); };
  translationPanel.querySelectorAll<HTMLButtonElement>('[data-translation-command]').forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = () => {
      if (!translationEditor || !translationEditor.isEditable) return;
      const chain = translationEditor.chain().focus();
      switch (button.dataset.translationCommand) {
        case 'bold': chain.toggleBold().run(); break;
        case 'italic': chain.toggleItalic().run(); break;
        case 'heading': chain.toggleHeading({ level: 3 }).run(); break;
        case 'bulletList': chain.toggleBulletList().run(); break;
        case 'orderedList': chain.toggleOrderedList().run(); break;
      }
    };
  });
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
  templateButton.addEventListener('mousedown', event => event.preventDefault());
  templateButton.onclick = () => {
    if (!documentId) return;
    setNotebookOpen(true);
    ensureNotebookEditor();
    openTemplateDialog({
      currentDoc: () => notebookEditor?.getJSON() || EMPTY_NOTEBOOK,
      list: () => hooks.listOutlineTemplates(),
      load: id => hooks.loadOutlineTemplate(id),
      create: (name, doc) => hooks.saveOutlineTemplate(name, doc),
      update: (id, name, doc, revision) => hooks.updateOutlineTemplate(id, name, doc, revision),
      onInsert: doc => applyMatchedTemplate(doc, false),
      onReplace: doc => applyMatchedTemplate(doc, true),
    });
  };
  container.querySelectorAll<HTMLButtonElement>('[data-pdf-note-command]').forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = () => runNotebookCommand(button.dataset.pdfNoteCommand || '');
  });
  const pageLinkButton = container.querySelector<HTMLButtonElement>('[data-pdf-note-page-link]')!;
  pageLinkButton.addEventListener('mousedown', event => event.preventDefault());
  pageLinkButton.onclick = openNotebookPageLinkDialog;
  window.addEventListener('pagehide', () => {
    void flushTranslationSave();
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
      await flushTranslationSave();
      rememberView(true);
      rememberNotebookUi();
    },
    async load(asset) {
      const token = ++loadToken;
      await flushNotebookSave();
      await flushTranslationSave();
      if (token !== loadToken) return;
      rememberView(true);
      rememberNotebookUi();
      clearTimeout(notebookScrollTimer);
      notebookScrollTimer = undefined;
      if (split) await setSplit(false, 'main', false);
      if (!asset.id) {
        documentId = '';
        translationEditorPage = 0;
        loadNotebook(EMPTY_NOTEBOOK, 0);
        translation = { format: 'pdf-translation-v1', pages: {} };
        await setTranslationOpen(false);
        setNotebookOpen(false, false);
        await showEmpty();
        return;
      }
      const fileName = asset.filename || `${asset.name}.pdf`;
      if (documentId !== asset.id) {
        translationEditorPage = 0;
      }
      const sameReady = documentId === asset.id
        && assetRevision === asset.revision
        && !!pdf
        && main.viewer.pdfDocument === pdf
        && main.ready
        && openedFingerprint.startsWith(`${asset.id}:`)
        && (pdf.numPages || 0) > 0;
      if (sameReady) {
        filename = asset.name;
        loadNotebook(asset.content, asset.revision);
        translation = translationDocument(asset.content);
        if (translationOpen) renderTranslationPage();
        restoreAssetUi(asset.id);
        showReading(null, true);
        followIndex(asset.id, token);
        return;
      }
      if (documentId && documentId !== asset.id) {
        await releaseOpenDocument();
        if (token !== loadToken) return;
      }
      const parked = parkedPdfs.get(asset.id);
      if (parked && parked.revision === asset.revision) {
        documentId = asset.id;
        loadNotebook(asset.content, asset.revision);
        translation = translationDocument(asset.content);
        if (translationOpen) renderTranslationPage();
        restoreAssetUi(asset.id);
        await whenVisible();
        if (token !== loadToken) return;
        await activateParkedPdf(parked, token);
        followIndex(asset.id, token);
        return;
      }
      documentId = asset.id;
      filename = asset.name;
      assetRevision = asset.revision;
      loadNotebook(asset.content, asset.revision);
      translation = translationDocument(asset.content);
      if (translationOpen) renderTranslationPage();
      restoreAssetUi(asset.id);
      followIndex(asset.id, token);
      busy = true;
      beginReading();
      showReading(null);
      try {
        await whenVisible();
        if (token !== loadToken) return;
        const size = asset.size || 0;
        if (size > RANGE_OPEN_BYTES) {
          showReading(null);
          await openRange(asset.id, fileName, asset.revision, size, token);
          return;
        }
        const data = await hooks.fetchFile(asset.id, {
          revision: asset.revision,
          expectedSize: asset.size,
          onProgress: progress => {
            if (token !== loadToken) return;
            showReading(progress);
          },
        });
        if (token !== loadToken) return;
        if (!data) {
          await showEmpty(asset.id, asset.name);
          return;
        }
        showReading(null);
        await openBuffer(asset.id, fileName, data, token);
      } catch (e) {
        if (token !== loadToken) return;
        showReading(null, true);
        error(e instanceof Error ? e.message : String(e));
        await showEmpty(asset.id, asset.name);
      }
    },
  };
}
