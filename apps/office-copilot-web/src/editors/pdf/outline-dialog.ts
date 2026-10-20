import { mountThinkingStatus } from '../../ui/thinking-status';
import type { PdfOutlineItem } from './outline';

type OutlineResult = { status: 'success' | 'unsupported'; outline?: PdfOutlineItem[]; message?: string };
type Hooks = {
  run: (onEvent: (event: Record<string, any>) => void) => Promise<void>;
  onSuccess: (outline: PdfOutlineItem[]) => void;
};

const POS_KEY = 'office-pdf-outline-pos';

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function bindDrag(panel: HTMLElement, handle: HTMLElement) {
  let dragging = false;
  let startX = 0; let startY = 0; let left = 0; let top = 0;
  const move = (event: PointerEvent) => {
    if (!dragging) return;
    panel.style.left = String(clamp(left + event.clientX - startX, 8, Math.max(8, window.innerWidth - panel.offsetWidth - 8))) + 'px';
    panel.style.top = String(clamp(top + event.clientY - startY, 8, Math.max(8, window.innerHeight - panel.offsetHeight - 8))) + 'px';
  };
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    try { localStorage.setItem(POS_KEY, JSON.stringify({ left: panel.offsetLeft, top: panel.offsetTop })); } catch { /* ignore */ }
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', stop);
  };
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return;
    dragging = true; startX = event.clientX; startY = event.clientY; left = panel.offsetLeft; top = panel.offsetTop;
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop);
    event.preventDefault();
  });
}

export function openOutlineDialog(hooks: Hooks) {
  document.querySelector<HTMLElement>('.pdf-outline-float')?.remove();
  const panel = document.createElement('section');
  panel.className = 'ask-ai-float pdf-outline-float';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '生成文档大纲');
  panel.innerHTML = '<header class="ask-ai-drag" data-drag><h2>生成大纲</h2><span class="ask-ai-drag-hint">拖动</span><button type="button" class="ask-ai-close" data-close aria-label="关闭">关闭</button></header><div class="pdf-outline-content"><div data-status></div><ol class="pdf-outline-log" data-log></ol></div>';
  let left = window.innerWidth - 444;
  let top = 74;
  try {
    const value = JSON.parse(localStorage.getItem(POS_KEY) || '{}');
    if (typeof value.left === 'number') left = value.left;
    if (typeof value.top === 'number') top = value.top;
  } catch { /* ignore */ }
  panel.style.left = String(clamp(left, 8, Math.max(8, window.innerWidth - 420))) + 'px';
  panel.style.top = String(clamp(top, 8, Math.max(8, window.innerHeight - 120))) + 'px';
  document.body.append(panel);
  bindDrag(panel, panel.querySelector<HTMLElement>('[data-drag]')!);
  panel.querySelector<HTMLButtonElement>('[data-close]')!.onclick = () => panel.remove();
  const thinking = mountThinkingStatus(panel.querySelector<HTMLElement>('[data-status]')!);
  const log = panel.querySelector<HTMLOListElement>('[data-log]')!;
  const note = (message: string) => {
    const item = document.createElement('li');
    item.textContent = message;
    log.append(item);
    log.scrollTop = log.scrollHeight;
  };
  thinking.begin();
  note('正在准备大纲生成。');
  void hooks.run(event => {
    if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
    if (event.type === 'progress') {
      thinking.note(String(event.message || '正在处理…'));
      if (event.message) note(String(event.message));
    }
    if (event.type === 'error') {
      thinking.finish();
      thinking.note(String(event.error || '生成失败。'));
      note(String(event.error || '生成失败。'));
    }
    if (event.type === 'done') {
      thinking.finish();
      const result = event as OutlineResult;
      if (result.status === 'success' && Array.isArray(result.outline)) {
        hooks.onSuccess(result.outline);
        const message = '已生成并追加到 PDF 笔记本。';
        thinking.note(message); note(message);
      } else {
        const message = result.message || '文档结构复杂，暂时无法生成。';
        thinking.note(message); note(message);
      }
    }
  }).catch(error => {
    thinking.finish();
    const message = error instanceof Error ? error.message : String(error);
    thinking.note(message);
    note(message);
  });
  return panel;
}
