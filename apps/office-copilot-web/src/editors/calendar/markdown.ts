import { getProcessor } from 'bytemd';
import type { BytemdPlugin } from 'bytemd';
import gfm from '@bytemd/plugin-gfm';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { circleBullet, nestedListTab } from '../rich-text-lists';

const plugins: BytemdPlugin[] = [gfm()];

let processor: ReturnType<typeof getProcessor> | null = null;

function mdProcessor() {
  if (!processor) processor = getProcessor({ plugins });
  return processor;
}

/** 将 Markdown 渲染为安全 HTML（由 bytemd/rehype-sanitize 处理）。 */
export function renderMarkdown(markdown: string): string {
  const raw = String(markdown || '');
  if (!raw.trim()) return '';
  try {
    const file = mdProcessor().processSync(raw);
    return String(file.value ?? file);
  } catch (err) {
    console.error('[calendar] markdown render failed', err);
    const div = document.createElement('div');
    div.textContent = raw;
    return div.innerHTML;
  }
}

export type MdEditorHandle = {
  getValue: () => string;
  focus: () => void;
  destroy: () => void;
};

/**
 * 轻量 Markdown 编辑器：编写 / 预览（点击预览时根据当前正文渲染）。
 */
export function mountMarkdownEditor(
  target: HTMLElement,
  initial: string,
  options?: { placeholder?: string; live?: boolean },
): MdEditorHandle {
  if (options?.live) return mountLiveMarkdownEditor(target, initial, options.placeholder);
  const root = document.createElement('div');
  root.className = 'plan-md-editor';
  root.dataset.mode = 'edit';
  root.innerHTML = `
    <div class="plan-md-toolbar" role="tablist" aria-label="编辑模式">
      <button type="button" data-mode="edit" class="is-active">编写</button>
      <button type="button" data-mode="preview">预览</button>
    </div>
    <div class="plan-md-panes">
      <textarea class="plan-md-input" spellcheck="false"></textarea>
      <div class="plan-md plan-md-preview" data-preview aria-live="polite"></div>
    </div>`;
  target.replaceChildren(root);

  const input = root.querySelector<HTMLTextAreaElement>('.plan-md-input')!;
  const preview = root.querySelector<HTMLElement>('[data-preview]')!;
  input.placeholder = options?.placeholder || '写下今天的内容…';
  input.value = initial;

  const setMode = (mode: 'edit' | 'preview') => {
    root.dataset.mode = mode;
    root.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(btn => {
      btn.classList.toggle('is-active', btn.dataset.mode === mode);
    });
    if (mode === 'preview') {
      const md = input.value;
      preview.innerHTML = md.trim()
        ? renderMarkdown(md)
        : '<p class="plan-md-empty">暂无内容</p>';
    } else {
      queueMicrotask(() => {
        input.focus();
        const len = input.value.length;
        input.setSelectionRange(len, len);
      });
    }
  };

  root.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      setMode((btn.dataset.mode === 'preview' ? 'preview' : 'edit'));
    });
  });

  return {
    getValue: () => input.value,
    focus: () => {
      if (root.dataset.mode === 'preview') return;
      input.focus();
      const len = input.value.length;
      input.setSelectionRange(len, len);
    },
    destroy: () => {
      root.remove();
    },
  };
}

function mountLiveMarkdownEditor(target: HTMLElement, initial: string, placeholder?: string): MdEditorHandle {
  const root = document.createElement('div');
  root.className = 'plan-md-editor plan-md-live';
  const input = document.createElement('div');
  input.className = 'plan-md plan-md-live-input';
  root.append(input);
  target.replaceChildren(root);
  const editor = new Editor({
    element: input,
    extensions: [StarterKit.configure({ bulletList: false }), circleBullet, nestedListTab],
    content: renderMarkdown(initial),
    editorProps: {
      attributes: { 'aria-label': placeholder || '事项内容', 'data-placeholder': placeholder || '写下今天的内容…' },
    },
  });
  return {
    getValue: () => {
      const html = document.createElement('div');
      html.innerHTML = editor.getHTML();
      const lines = document.createElement('div');
      lines.replaceChildren(...flattenPlan(html).map(row => createPlanLine(row.level, row.text)));
      return planLinesToMarkdown(lines);
    },
    focus: () => { editor.commands.focus('end'); },
    destroy: () => { editor.destroy(); root.remove(); },
  };
}

/** 月历格子里的序号来自样式，复制时改写成带序号和列表的剪贴板。 */
export function bindRenderedPlanCopy(root: HTMLElement) {
  root.addEventListener('copy', event => {
    const plan = renderedPlanFromSelection(root);
    if (!plan) return;
    writePlanClipboard(event, rowsFromRenderedSelection(plan));
  });
}


function flattenPlan(root: HTMLElement): { level: string; text: string }[] {
  const rows: { level: string; text: string }[] = [];
  const walkList = (list: Element, depth: number) => {
    const level = list.tagName === 'OL' ? '0' : (depth <= 1 ? '1' : '2');
    for (const li of list.querySelectorAll(':scope > li')) {
      const nested = [...li.children].filter(child => child.tagName === 'UL' || child.tagName === 'OL');
      const clone = li.cloneNode(true) as HTMLElement;
      clone.querySelectorAll('ul,ol').forEach(node => node.remove());
      rows.push({ level, text: (clone.textContent || '').trim() });
      nested.forEach(node => walkList(node, depth + 1));
    }
  };
  for (const child of root.children) {
    if (child.tagName === 'OL') walkList(child, 0);
    else if (child.tagName === 'UL') walkList(child, 1);
    else rows.push({ level: '', text: (child.textContent || '').trim() });
  }
  return rows;
}

function createPlanLine(level: string, text = '') {
  const line = document.createElement('div');
  line.className = 'plan-line';
  if (level === '0' || level === '1' || level === '2') line.dataset.level = level;
  line.textContent = text;
  if (!text) line.innerHTML = '<br>';
  return line;
}




function planLevel(line: HTMLElement) {
  const level = line.dataset.level;
  return level === '0' || level === '1' || level === '2' ? Number(level) : -1;
}


function planLinesToMarkdown(input: HTMLElement) {
  const chunks: string[] = [];
  let list: string[] = [];
  let number = 0;
  const flush = () => {
    if (!list.length) return;
    chunks.push(list.join('\n'));
    list = [];
  };
  for (const line of input.querySelectorAll<HTMLElement>(':scope > .plan-line')) {
    const text = (line.textContent || '').trim();
    const level = planLevel(line);
    if (level < 0) {
      flush();
      if (text) chunks.push(text);
      continue;
    }
    if (!text) continue;
    if (level === 0) list.push(`${++number}. ${text}`);
    else if (level === 1) list.push(`    - ${text}`);
    else list.push(`        - ${text}`);
  }
  flush();
  return chunks.join('\n\n');
}

type PlanRow = { level: string; text: string; num?: string };

function writePlanClipboard(event: ClipboardEvent, rows: PlanRow[] | null) {
  if (!rows?.length || !event.clipboardData) return false;
  event.preventDefault();
  event.clipboardData.setData('text/plain', rowsToPlain(rows));
  event.clipboardData.setData('text/html', rowsToHtml(rows));
  return true;
}






function rowsToPlain(rows: PlanRow[]) {
  return rows.map(row => {
    if (row.level === '0') return `${row.num || '1'}. ${row.text}`;
    if (row.level === '1') return `    - ${row.text}`;
    if (row.level === '2') return `        - ${row.text}`;
    return row.text;
  }).join('\n');
}

function rowsToHtml(rows: PlanRow[]) {
  let html = '';
  let index = 0;
  while (index < rows.length) {
    const level = rows[index].level;
    if (level !== '0' && level !== '1' && level !== '2') {
      html += `<p>${escapeHtml(rows[index].text)}</p>`;
      index += 1;
      continue;
    }
    if (level === '0') {
      const start = rows[index].num;
      html += start && start !== '1' ? `<ol start="${escapeHtml(start)}">` : '<ol>';
      while (index < rows.length && (rows[index].level === '0' || rows[index].level === '1' || rows[index].level === '2')) {
        if (rows[index].level !== '0') break;
        const text = escapeHtml(rows[index].text);
        index += 1;
        const nested = nestedListHtml(rows, index);
        index = nested.index;
        html += `<li>${text}${nested.html}</li>`;
      }
      html += '</ol>';
      continue;
    }
    const nested = nestedListHtml(rows, index);
    html += nested.html;
    index = nested.index;
  }
  return html;
}

function nestedListHtml(rows: PlanRow[], start: number) {
  if (start >= rows.length || (rows[start].level !== '1' && rows[start].level !== '2')) {
    return { html: '', index: start };
  }
  let html = '<ul>';
  let index = start;
  while (index < rows.length && (rows[index].level === '1' || rows[index].level === '2')) {
    if (rows[index].level === '2') {
      html += `<li>${escapeHtml(rows[index].text)}</li>`;
      index += 1;
      continue;
    }
    const text = escapeHtml(rows[index].text);
    index += 1;
    let deeper = '';
    if (index < rows.length && rows[index].level === '2') {
      deeper = '<ul>';
      while (index < rows.length && rows[index].level === '2') {
        deeper += `<li>${escapeHtml(rows[index].text)}</li>`;
        index += 1;
      }
      deeper += '</ul>';
    }
    html += `<li>${text}${deeper}</li>`;
  }
  html += '</ul>';
  return { html, index };
}

function renderedPlanFromSelection(root: HTMLElement) {
  const range = selectionRange();
  if (!range || range.collapsed) return null;
  const node = range.commonAncestorContainer;
  const el = node instanceof Element ? node : node.parentElement;
  const plan = el?.closest('.fc-day-plan');
  return plan && root.contains(plan) ? plan as HTMLElement : null;
}

function rowsFromRenderedSelection(plan: HTMLElement): PlanRow[] | null {
  const range = selectionRange();
  if (!range) return null;
  const rows: PlanRow[] = [];
  const visitList = (list: Element, depth: number) => {
    const level = list.tagName === 'OL' ? '0' : (depth <= 1 ? '1' : '2');
    for (const li of list.querySelectorAll(':scope > li')) {
      const nested = [...li.children].filter(child => child.tagName === 'UL' || child.tagName === 'OL');
      const own = [...li.childNodes].filter(node => !(node instanceof Element && (node.tagName === 'UL' || node.tagName === 'OL')));
      if (own.some(node => range.intersectsNode(node))) {
        const clone = li.cloneNode(true) as HTMLElement;
        clone.querySelectorAll('ul,ol').forEach(node => node.remove());
        const text = (clone.textContent || '').trim();
        if (text) rows.push({ level, text, num: level === '0' ? visibleNumber(li as HTMLElement) : undefined });
      }
      nested.forEach(node => visitList(node, depth + 1));
    }
  };
  for (const child of plan.children) {
    if (!range.intersectsNode(child)) continue;
    if (child.tagName === 'OL') visitList(child, 0);
    else if (child.tagName === 'UL') visitList(child, 1);
    else {
      const text = range.intersectsNode(child) ? selectedNodeText(child, range) : '';
      if (text) rows.push({ level: '', text });
    }
  }
  return rows.length ? rows : null;
}

function visibleNumber(li: HTMLElement) {
  const content = getComputedStyle(li, '::before').content;
  const marker = content.match(/"(\d+)\.?\s*"$/);
  if (marker) return marker[1];
  const siblings = [...(li.parentElement?.children || [])].filter(el => el.tagName === 'LI');
  return String(siblings.indexOf(li) + 1);
}


function selectionRange() {
  const sel = window.getSelection();
  if (!sel?.rangeCount) return null;
  return sel.getRangeAt(0);
}



function selectedNodeText(node: Node, range: Range) {
  const probe = document.createRange();
  probe.selectNodeContents(node);
  const slice = range.cloneRange();
  if (slice.compareBoundaryPoints(Range.START_TO_START, probe) < 0) slice.setStart(probe.startContainer, probe.startOffset);
  if (slice.compareBoundaryPoints(Range.END_TO_END, probe) > 0) slice.setEnd(probe.endContainer, probe.endOffset);
  return slice.toString().trim();
}

function escapeHtml(text: string) {
  return text.replace(/[&<>"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char] || char));
}
