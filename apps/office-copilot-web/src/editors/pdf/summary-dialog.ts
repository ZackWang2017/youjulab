import { mountThinkingStatus } from '../../ui/thinking-status';

export function openPdfSummaryDialog(hooks: {
  run: (onEvent: (event: Record<string, any>) => void) => Promise<void>;
  onInsert: (items: unknown) => Promise<void>;
  onFinish: () => void;
}) {
  document.querySelector<HTMLElement>('.pdf-summary-float')?.remove();
  const panel = document.createElement('section');
  panel.className = 'ask-ai-float pdf-outline-float pdf-summary-float';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '整理选中文本摘要');
  panel.innerHTML = '<header class="ask-ai-drag"><h2>整理摘要</h2><span class="ask-ai-drag-hint">处理中</span><button type="button" class="ask-ai-close" data-close aria-label="关闭">关闭</button></header><div class="pdf-outline-content"><div data-status></div><ol class="pdf-outline-log" data-log></ol><div class="pdf-summary-preview" data-preview hidden></div><div class="pdf-summary-actions" data-actions hidden><button type="button" data-discard>放弃</button><button type="button" class="primary" data-insert>插入笔记</button></div></div>';
  panel.style.right = '24px';
  panel.style.top = '74px';
  document.body.append(panel);
  let finished = false;
  const close = () => { if (!finished) { finished = true; hooks.onFinish(); } panel.remove(); };
  panel.querySelector<HTMLButtonElement>('[data-close]')!.onclick = close;
  const thinking = mountThinkingStatus(panel.querySelector<HTMLElement>('[data-status]')!);
  const log = panel.querySelector<HTMLOListElement>('[data-log]')!;
  const preview = panel.querySelector<HTMLElement>('[data-preview]')!;
  const actions = panel.querySelector<HTMLElement>('[data-actions]')!;
  const insert = panel.querySelector<HTMLButtonElement>('[data-insert]')!;
  const note = (text: string) => { const item = document.createElement('li'); item.textContent = text; log.append(item); };
  thinking.begin();
  thinking.note('正在整理选中文本。');
  void hooks.run(event => {
    if (event.type === 'thinking' && event.text) thinking.append(String(event.text));
    if (event.type === 'error') { thinking.finish(); thinking.note(String(event.error || '摘要生成失败。')); note(String(event.error || '摘要生成失败。')); close(); }
    if (event.type === 'done') {
      thinking.finish();
      const render = (items: unknown, parent: HTMLElement) => {
        if (!Array.isArray(items)) return;
        const list = document.createElement('ul');
        for (const item of items) {
          if (!item || typeof item !== 'object') continue;
          const value = item as { text?: unknown; children?: unknown };
          if (typeof value.text !== 'string' || !value.text.trim()) continue;
          const row = document.createElement('li'); row.textContent = value.text.trim();
          render(value.children, row); list.append(row);
        }
        if (list.childElementCount) parent.append(list);
      };
      render(event.items, preview);
      preview.hidden = !preview.childElementCount;
      log.hidden = true;
      actions.hidden = false;
      insert.onclick = async () => {
        insert.disabled = true;
        try { await hooks.onInsert(event.items); close(); }
        catch (err) { insert.disabled = false; thinking.note(String(err)); note(String(err)); }
      };
    }
  }).catch(err => { thinking.finish(); thinking.note(String(err)); note(String(err)); close(); });
  panel.querySelector<HTMLButtonElement>('[data-discard]')!.onclick = close;
  return panel;
}
