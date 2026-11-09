import { createIcons, icons } from 'lucide';
import { mountThinkingStatus } from '../../ui/thinking-status';

type Hooks = {
  anchor?: HTMLElement;
  run: (onEvent: (event: Record<string, unknown>) => void) => Promise<void>;
  onDone: (result: { page: number; source: string; translation: string; modelName: string }) => Promise<void>;
  onFinish: () => void;
};

function bindDialogDrag(panel: HTMLElement, handle: HTMLElement) {
  let dragging = false;
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;
  const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
  const move = (event: PointerEvent) => {
    if (!dragging) return;
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    panel.style.left = `${clamp(originLeft + event.clientX - startX, 8, Math.max(8, window.innerWidth - width - 8))}px`;
    panel.style.top = `${clamp(originTop + event.clientY - startY, 8, Math.max(8, window.innerHeight - Math.min(height, 80) - 8))}px`;
  };
  const up = (event: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    handle.releasePointerCapture(event.pointerId);
    handle.classList.remove('is-dragging');
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return;
    const bounds = panel.getBoundingClientRect();
    panel.style.left = `${bounds.left}px`;
    panel.style.top = `${bounds.top}px`;
    panel.style.right = 'auto';
    dragging = true;
    startX = event.clientX;
    startY = event.clientY;
    originLeft = bounds.left;
    originTop = bounds.top;
    handle.setPointerCapture(event.pointerId);
    handle.classList.add('is-dragging');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    event.preventDefault();
  });
}

export function openPdfTranslationDialog(hooks: Hooks) {
  document.querySelector<HTMLElement>('.pdf-translation-float')?.remove();
  const panel = document.createElement('section');
  panel.className = 'ask-ai-float pdf-outline-float pdf-translation-float';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '翻译此页');
  panel.innerHTML = '<header class="ask-ai-drag"><h2>翻译此页</h2><span class="ask-ai-drag-hint">处理中</span><button type="button" class="ask-ai-close" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></header><div class="pdf-outline-content"><div data-status></div><ol class="pdf-outline-log" data-log></ol></div>';
  panel.style.right = '24px';
  panel.style.left = 'auto';
  panel.style.top = '74px';
  document.body.append(panel);
  if (hooks.anchor) {
    const bounds = hooks.anchor.getBoundingClientRect();
    panel.style.right = `${Math.max(8, window.innerWidth - bounds.right + 12)}px`;
    panel.style.top = `${Math.max(8, Math.min(bounds.top + 48, window.innerHeight - panel.offsetHeight - 8))}px`;
  }
  createIcons({ icons });
  bindDialogDrag(panel, panel.querySelector<HTMLElement>('.ask-ai-drag')!);
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    hooks.onFinish();
  };
  const close = () => {
    finish();
    panel.remove();
  };
  panel.querySelector<HTMLButtonElement>('[data-close]')!.onclick = close;
  const thinking = mountThinkingStatus(panel.querySelector<HTMLElement>('[data-status]')!);
  const log = panel.querySelector<HTMLOListElement>('[data-log]')!;
  const note = (message: string) => {
    const item = document.createElement('li');
    item.textContent = message;
    log.append(item);
    log.scrollTop = log.scrollHeight;
  };
  thinking.begin();
  thinking.note('正在准备本页翻译。');
  void hooks.run(event => {
    if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
    if (event.type === 'progress') {
      const message = String(event.message || '正在处理…');
      thinking.note(message); note(message);
    }
    if (event.type === 'error') {
      const message = String(event.error || '翻译失败。');
      thinking.finish(); thinking.note(message); note(message);
      finish();
    }
    if (event.type === 'done') {
      const source = String(event.source || '');
      const translation = String(event.translation || '');
      const page = Number(event.page || 0);
      if (!source || !translation || !page) {
        const message = '模型没有返回可保存的译文。';
        thinking.finish(); thinking.note(message); note(message);
        return;
      }
      thinking.finish();
      void hooks.onDone({ page, source, translation, modelName: String(event.model_name || '模型') })
        .then(() => { thinking.note('本页译文已保存。'); note('本页译文已保存。'); finish(); })
        .catch(error => { const message = error instanceof Error ? error.message : String(error); thinking.note(message); note(message); finish(); });
    }
  }).catch(error => {
    const message = error instanceof Error ? error.message : String(error);
    thinking.finish(); thinking.note(message); note(message);
    finish();
  });
  return panel;
}

export function openPdfSelectionTranslationDialog(hooks: {
  source: string;
  run: (onEvent: (event: Record<string, unknown>) => void) => Promise<void>;
  onFinish: () => void;
}) {
  document.querySelector<HTMLElement>('.pdf-selection-translation-float')?.remove();
  const panel = document.createElement('section');
  panel.className = 'ask-ai-float pdf-outline-float pdf-selection-translation-float';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '划词翻译');
  panel.innerHTML = '<header class="ask-ai-drag"><h2>划词翻译</h2><span class="ask-ai-drag-hint">处理中</span><button type="button" class="ask-ai-close" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></header><div class="pdf-outline-content"><section class="pdf-selection-translation-source"><strong>原文</strong><p data-source></p></section><div data-status></div><ol class="pdf-outline-log" data-log></ol><section class="pdf-selection-translation-result" data-result hidden><strong>译文</strong><p data-translation></p></section></div>';
  panel.style.right = '24px';
  panel.style.top = '74px';
  document.body.append(panel);
  createIcons({ icons });
  bindDialogDrag(panel, panel.querySelector<HTMLElement>('.ask-ai-drag')!);
  panel.querySelector<HTMLElement>('[data-source]')!.textContent = hooks.source;
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    hooks.onFinish();
  };
  const close = () => {
    finish();
    panel.remove();
  };
  panel.querySelector<HTMLButtonElement>('[data-close]')!.onclick = close;
  const thinking = mountThinkingStatus(panel.querySelector<HTMLElement>('[data-status]')!);
  const log = panel.querySelector<HTMLOListElement>('[data-log]')!;
  const result = panel.querySelector<HTMLElement>('[data-result]')!;
  const translated = panel.querySelector<HTMLElement>('[data-translation]')!;
  const note = (message: string) => {
    const item = document.createElement('li');
    item.textContent = message;
    log.append(item);
    log.scrollTop = log.scrollHeight;
  };
  thinking.begin();
  thinking.note('正在准备选中文本翻译。');
  void hooks.run(event => {
    if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
    if (event.type === 'progress') {
      const message = String(event.message || '正在处理…');
      thinking.note(message);
      note(message);
    }
    if (event.type === 'error') {
      const message = String(event.error || '划词翻译失败。');
      thinking.finish();
      thinking.note(message);
      note(message);
      finish();
    }
    if (event.type === 'done') {
      const value = String(event.translation || '');
      if (!value) {
        const message = '模型没有返回可显示的译文。';
        thinking.finish();
        thinking.note(message);
        note(message);
        finish();
        return;
      }
      thinking.finish();
      translated.textContent = value;
      result.hidden = false;
      log.hidden = true;
      finish();
    }
  }).catch(error => {
    const message = error instanceof Error ? error.message : String(error);
    thinking.finish();
    thinking.note(message);
    note(message);
    finish();
  });
  return panel;
}
