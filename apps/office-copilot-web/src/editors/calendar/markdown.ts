import { getProcessor } from 'bytemd';
import type { BytemdPlugin } from 'bytemd';
import gfm from '@bytemd/plugin-gfm';

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
  input.contentEditable = 'true';
  input.dataset.placeholder = placeholder || '写下今天的内容…';
  fillPlanLines(input, initial);
  refreshPlanNumbers(input);
  root.append(input);
  target.replaceChildren(root);

  const onKeydown = (e: KeyboardEvent) => {
    if (e.isComposing) return;
    const line = currentPlanLine(input);
    if (e.key === 'Tab') {
      e.preventDefault();
      if (!line) return;
      const level = planLevel(line);
      const next = e.shiftKey
        ? Math.max(0, level < 0 ? 0 : level - 1)
        : (level < 0 ? 0 : Math.min(2, level + 1));
      line.dataset.level = String(next);
      if (!line.textContent) line.innerHTML = '<br>';
      refreshPlanNumbers(input);
      placeCaretEnd(line);
      return;
    }
    if (e.key !== 'Enter' || e.shiftKey) return;
    e.preventDefault();
    const current = line || ensurePlanLine(input);
    const level = planLevel(current);
    if (!(current.textContent || '').trim()) {
      if (level > 0) current.dataset.level = String(level - 1);
      else delete current.dataset.level;
      refreshPlanNumbers(input);
      placeCaretEnd(current);
      return;
    }
    const next = createPlanLine(level < 0 ? '' : String(level));
    current.after(next);
    refreshPlanNumbers(input);
    placeCaretEnd(next);
  };
  const onInput = () => {
    const line = currentPlanLine(input);
    if (!line || planLevel(line) >= 0) return;
    const raw = line.textContent || '';
    if (!/^\d+\.\s?/.test(raw) || caretOffset(line) > (raw.match(/^\d+\./)?.[0].length || 0)) return;
    line.dataset.level = '0';
    line.textContent = raw.replace(/^\d+\.\s?/, '');
    if (!line.textContent) line.innerHTML = '<br>';
    refreshPlanNumbers(input);
    placeCaretEnd(line);
  };
  input.addEventListener('keydown', onKeydown);
  input.addEventListener('input', onInput);

  return {
    getValue: () => planLinesToMarkdown(input),
    focus: () => {
      const last = input.querySelector<HTMLElement>('.plan-line:last-child') || ensurePlanLine(input);
      last.focus();
      placeCaretEnd(last);
    },
    destroy: () => {
      input.removeEventListener('keydown', onKeydown);
      input.removeEventListener('input', onInput);
      root.remove();
    },
  };
}

function fillPlanLines(input: HTMLElement, markdown: string) {
  const tmp = document.createElement('div');
  tmp.innerHTML = renderMarkdown(markdown);
  const rows = flattenPlan(tmp);
  input.replaceChildren(...(rows.length ? rows.map(row => createPlanLine(row.level, row.text)) : [createPlanLine('')]));
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

function ensurePlanLine(input: HTMLElement) {
  const line = createPlanLine('');
  input.append(line);
  return line;
}

function currentPlanLine(input: HTMLElement) {
  const node = window.getSelection()?.anchorNode;
  const el = node instanceof Element ? node : node?.parentElement;
  const line = el?.closest('.plan-line');
  return line && input.contains(line) ? line as HTMLElement : null;
}

function refreshPlanNumbers(input: HTMLElement) {
  let number = 0;
  input.querySelectorAll<HTMLElement>(':scope > .plan-line').forEach(line => {
    if (line.dataset.level !== '0') {
      delete line.dataset.num;
      return;
    }
    number += 1;
    line.dataset.num = String(number);
  });
}

function planLevel(line: HTMLElement) {
  const level = line.dataset.level;
  return level === '0' || level === '1' || level === '2' ? Number(level) : -1;
}

function caretOffset(line: HTMLElement) {
  const sel = window.getSelection();
  if (!sel?.anchorNode || !line.contains(sel.anchorNode)) return 0;
  const range = sel.getRangeAt(0).cloneRange();
  range.selectNodeContents(line);
  range.setEnd(sel.anchorNode, sel.anchorOffset);
  return range.toString().length;
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

function placeCaretEnd(el: HTMLElement) {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}
