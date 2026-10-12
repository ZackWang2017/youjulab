import { historyForApi, isAiAskContent, parseAskThread, serializeAskThread, type AskThread, type AskTurn } from './ask-ai';
import { markdownToHtml } from '../document/markdown';
import { appendThinkingBlock } from '../../ui/thinking-status';

type AskAiHooks = {
  userName: string;
  modelName: string;
  ask: (body: {
    selection: string;
    question: string;
    page: number;
    document_id: string;
    filename: string;
    history: { role: 'user' | 'assistant'; content: string }[];
    translate?: boolean;
  }, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  save: (content: string) => Promise<{ xref: number; mark_xref?: number } | void>;
  update: (xref: number, content: string) => Promise<void>;
  remove: (xref: number) => Promise<{ deleted?: number[] } | void>;
};

export type AskAiOpenArgs = {
  selection: string;
  page: number;
  document_id: string;
  filename: string;
  boxes: number[][];
  xref?: number | null;
  markXref?: number | null;
  content?: string;
};

const POS_KEY = 'office-ask-ai-pos';

function speakerLabel(turn: AskTurn) {
  return turn.speaker || (turn.role === 'user' ? '用户' : '模型');
}

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n));
}

function loadPos(panel: HTMLElement): { left: number; top: number } {
  const w = panel.offsetWidth || 420;
  const h = panel.offsetHeight || 480;
  const margin = 12;
  let left = Math.max(margin, window.innerWidth - w - 24);
  let top = 72;
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { left?: number; top?: number };
      if (typeof parsed.left === 'number') left = parsed.left;
      if (typeof parsed.top === 'number') top = parsed.top;
    }
  } catch { /* ignore */ }
  return {
    left: clamp(left, margin, Math.max(margin, window.innerWidth - w - margin)),
    top: clamp(top, margin, Math.max(margin, window.innerHeight - Math.min(h, 120) - margin)),
  };
}

function savePos(left: number, top: number) {
  try { localStorage.setItem(POS_KEY, JSON.stringify({ left, top })); } catch { /* ignore */ }
}

function bindDrag(panel: HTMLElement, handle: HTMLElement) {
  let dragging = false;
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  const onMove = (e: PointerEvent) => {
    if (!dragging) return;
    const w = panel.offsetWidth;
    const h = panel.offsetHeight;
    const left = clamp(originLeft + e.clientX - startX, 8, Math.max(8, window.innerWidth - w - 8));
    const top = clamp(originTop + e.clientY - startY, 8, Math.max(8, window.innerHeight - Math.min(h, 80) - 8));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  };
  const onUp = (e: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    handle.releasePointerCapture(e.pointerId);
    handle.classList.remove('is-dragging');
    savePos(panel.offsetLeft, panel.offsetTop);
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };

  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('button')) return;
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    originLeft = panel.offsetLeft;
    originTop = panel.offsetTop;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('is-dragging');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    e.preventDefault();
  });
}

export function openAskAiDialog(args: AskAiOpenArgs, hooks: AskAiHooks) {
  return (async () => {
    const prev = document.querySelector('.ask-ai-float') as (HTMLElement & { __askAiClose?: () => Promise<void> }) | null;
    if (prev?.__askAiClose) {
      try { await prev.__askAiClose(); } catch { prev.remove(); }
    } else {
      prev?.remove();
    }
    return new Promise<{ action: 'saved' | 'deleted' | 'closed'; content?: string; xref?: number; markXref?: number; deleted?: number[] }>(resolve => {
    const existing = args.content && isAiAskContent(args.content) ? parseAskThread(args.content) : null;
    const thread: AskThread = {
      selection: (existing?.selection || args.selection || '').trim(),
      turns: existing?.turns ? [...existing.turns] : [],
    };
    let xref = args.xref && args.xref > 0 ? args.xref : null;
    let markXref = args.markXref && args.markXref > 0 ? args.markXref : null;
    let busy = false;
    let settled = false;
    let dirty = false;
    let lastSaved = serializeAskThread(thread);

    const panel = document.createElement('div');
    panel.className = 'ask-ai-float';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-labelledby', 'ask-ai-title');
    panel.setAttribute('aria-modal', 'false');
    panel.innerHTML = `
      <header class="ask-ai-drag" data-drag>
        <h2 id="ask-ai-title">问 AI</h2>
        <span class="ask-ai-drag-hint">拖动</span>
        <button type="button" class="ask-ai-close" data-cancel aria-label="关闭">关闭</button>
      </header>
      <section class="ask-ai-selection">
        <div class="ask-ai-label">选区：</div>
        <div class="ask-ai-selection-text"></div>
      </section>
      <div class="ask-ai-thread" aria-live="polite"></div>
      <form class="ask-ai-composer">
        <textarea rows="3" maxlength="2000" placeholder="结合选区与全文相关段落提问…"></textarea>
        <div class="ask-ai-composer-side">
          <label class="ask-ai-translate"><input type="checkbox" data-translate>英译中</label>
          <button type="submit" class="primary" data-ask>提问</button>
        </div>
      </form>
      <div class="dialog-actions ask-ai-actions">
        <button type="button" data-delete>删除</button>
        <button type="button" class="primary" data-save>加入批注</button>
      </div>`;
    const selectionEl = panel.querySelector<HTMLElement>('.ask-ai-selection-text')!;
    const threadEl = panel.querySelector<HTMLElement>('.ask-ai-thread')!;
    const form = panel.querySelector<HTMLFormElement>('.ask-ai-composer')!;
    const input = panel.querySelector<HTMLTextAreaElement>('textarea')!;
    const translateBox = panel.querySelector<HTMLInputElement>('[data-translate]')!;
    const askBtn = panel.querySelector<HTMLButtonElement>('[data-ask]')!;
    const saveBtn = panel.querySelector<HTMLButtonElement>('[data-save]')!;
    const deleteBtn = panel.querySelector<HTMLButtonElement>('[data-delete]')!;
    const dragHandle = panel.querySelector<HTMLElement>('[data-drag]')!;
    selectionEl.textContent = thread.selection || '（无选区文字）';

    const finish = (result: { action: 'saved' | 'deleted' | 'closed'; content?: string; xref?: number; markXref?: number; deleted?: number[] }) => {
      if (settled) return;
      settled = true;
      panel.remove();
      resolve(result);
    };

    function syncChrome() {
      const hasAnswer = thread.turns.some(turn => turn.role === 'assistant');
      saveBtn.disabled = busy || !hasAnswer || Boolean(xref && !dirty);
      saveBtn.textContent = xref ? (dirty ? '更新批注' : '已写入批注') : '加入批注';
      deleteBtn.disabled = busy || (!xref && !hasAnswer);
      askBtn.disabled = busy;
      input.disabled = busy;
      translateBox.disabled = busy;
    }

    function fillBody(el: HTMLElement, text: string, asMarkdown: boolean) {
      if (asMarkdown) {
        el.classList.add('ask-ai-md');
        el.innerHTML = markdownToHtml(text || '…');
      } else {
        el.classList.remove('ask-ai-md');
        el.textContent = text;
      }
    }

    function appendThinking(card: HTMLElement, text: string, live: boolean, open: boolean) {
      appendThinkingBlock(card, text, { live, open });
    }

    function renderThread(streaming?: { speaker: string; text: string; thinking?: string; live?: boolean }) {
      threadEl.replaceChildren();
      for (const turn of thread.turns) {
        const card = document.createElement('article');
        card.className = `ask-ai-turn ask-ai-${turn.role}`;
        const who = document.createElement('div');
        who.className = 'ask-ai-speaker';
        who.textContent = speakerLabel(turn);
        card.append(who);
        if (turn.role === 'assistant' && turn.thinking?.trim()) appendThinking(card, turn.thinking, false, false);
        if (turn.text.trim()) {
          const body = document.createElement('div');
          body.className = 'ask-ai-body';
          fillBody(body, turn.text, turn.role === 'assistant');
          card.append(body);
        }
        threadEl.append(card);
      }
      if (streaming) {
        const card = document.createElement('article');
        card.className = 'ask-ai-turn ask-ai-assistant is-streaming';
        const who = document.createElement('div');
        who.className = 'ask-ai-speaker';
        who.textContent = streaming.speaker;
        card.append(who);
        const thinking = streaming.thinking || '';
        const live = Boolean(streaming.live && !streaming.text.trim());
        if (thinking.trim() || live) appendThinking(card, thinking, live, live);
        if (streaming.text.trim()) {
          const body = document.createElement('div');
          body.className = 'ask-ai-body';
          fillBody(body, streaming.text, true);
          card.append(body);
        }
        threadEl.append(card);
      }
      threadEl.scrollTop = threadEl.scrollHeight;
      syncChrome();
    }

    function setBusy(next: boolean) {
      busy = next;
      renderThread();
    }

    async function persistThread() {
      const content = serializeAskThread(thread);
      if (!thread.turns.some(turn => turn.role === 'assistant')) {
        throw new Error('请先完成一轮问答。');
      }
      if (xref) {
        await hooks.update(xref, content);
      } else {
        const saved = await hooks.save(content);
        if (!saved?.xref) throw new Error('无法写入问 AI 批注。');
        xref = saved.xref;
        markXref = saved.mark_xref || null;
      }
      lastSaved = content;
      dirty = false;
      return content;
    }

    async function askQuestion(question: string) {
      const translate = translateBox.checked;
      const q = question.trim() || (translate ? '选区英译中' : '');
      if (!q || busy) return;
      const history = historyForApi(thread);
      thread.turns.push({ role: 'user', speaker: hooks.userName, text: q });
      dirty = true;
      input.value = '';
      setBusy(true);
      let answer = '';
      let thinking = '';
      let thinkingOpen = true;
      const model = hooks.modelName || '模型';
      try {
        renderThread({ speaker: model, text: '', thinking: '', live: true });
        await hooks.ask({
          selection: thread.selection,
          question: q,
          page: args.page,
          document_id: args.document_id,
          filename: args.filename,
          history,
          translate,
        }, event => {
          if (event.type === 'delta' && event.text) {
            if (event.part === 'reasoning') {
              thinking += event.text;
            } else {
              if (!answer) thinkingOpen = false;
              answer += event.text;
            }
            renderThread({ speaker: model, text: answer, thinking, live: thinkingOpen && !answer.trim() });
          }
          if (event.type === 'done') {
            if (typeof event.answer === 'string') answer = event.answer;
            if (typeof event.thinking === 'string' && event.thinking.trim()) thinking = event.thinking;
            thinkingOpen = false;
            renderThread({ speaker: model, text: answer, thinking, live: false });
          }
        });
        thread.turns.push({
          role: 'assistant',
          speaker: model,
          text: answer.trim() || '（模型未返回内容）',
          thinking: thinking.trim() || undefined,
        });
        dirty = true;
        if (xref) {
          await persistThread();
        }
      } catch (e) {
        thread.turns.pop();
        if (thread.turns.length && thread.turns[thread.turns.length - 1]?.role === 'user') {
          thread.turns.pop();
        }
        dirty = serializeAskThread(thread) !== lastSaved;
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
      } finally {
        setBusy(false);
      }
    }

    form.addEventListener('submit', e => {
      e.preventDefault();
      void askQuestion(input.value);
    });
    input.addEventListener('keydown', e => {
      const key = e as KeyboardEvent;
      if ((key.metaKey || key.ctrlKey) && key.key === 'Enter') {
        e.preventDefault();
        void askQuestion(input.value);
      }
    });

    async function closeDialog(opts?: { force?: boolean }) {
      if (busy && !opts?.force) return;
      if (busy && opts?.force) {
        finish({ action: 'closed' });
        return;
      }
      try {
        if (dirty && thread.turns.some(turn => turn.role === 'assistant')) {
          const content = await persistThread();
          finish({ action: 'saved', content, xref: xref || undefined, markXref: markXref || undefined });
          return;
        }
        if (xref && lastSaved) {
          finish({ action: 'saved', content: lastSaved, xref, markXref: markXref || undefined });
          return;
        }
      } catch (e) {
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
        return;
      }
      finish({ action: 'closed' });
    }

    (panel as HTMLElement & { __askAiClose?: () => Promise<void> }).__askAiClose = () => closeDialog({ force: true });

    panel.querySelector('[data-cancel]')!.addEventListener('click', () => { void closeDialog(); });
    saveBtn.addEventListener('click', async () => {
      if (busy || !thread.turns.some(turn => turn.role === 'assistant') || (xref && !dirty)) return;
      setBusy(true);
      try {
        await persistThread();
        renderThread();
      } catch (e) {
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
      } finally {
        setBusy(false);
      }
    });
    deleteBtn.addEventListener('click', async () => {
      if (busy) return;
      if (!xref) {
        finish({ action: 'deleted' });
        return;
      }
      if (!confirm('删除这条问 AI 批注及其高亮？')) return;
      setBusy(true);
      try {
        const result = await hooks.remove(xref);
        finish({ action: 'deleted', xref, deleted: result?.deleted });
      } catch (e) {
        setBusy(false);
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
      }
    });

    document.body.append(panel);
    const pos = loadPos(panel);
    panel.style.left = `${pos.left}px`;
    panel.style.top = `${pos.top}px`;
    bindDrag(panel, dragHandle);

    dirty = false;
    if (existing?.turns?.length) lastSaved = serializeAskThread(thread);
    renderThread();
    input.focus();
  });
  })();
}
