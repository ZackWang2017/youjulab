/** 大模型思考过程：提示区和对话里共用这一套样式。 */

export type ThinkingStatus = {
  note: (message: string) => void;
  begin: () => void;
  append: (chunk: string) => void;
  finish: () => void;
  clear: () => void;
};

export function mountThinkingStatus(host: HTMLElement): ThinkingStatus {
  host.classList.add('thinking-status');
  host.replaceChildren();
  const note = document.createElement('p');
  note.className = 'thinking-status-note';
  note.hidden = true;
  const block = document.createElement('div');
  block.className = 'assistant-thinking';
  block.dataset.live = 'false';
  block.dataset.open = 'false';
  block.hidden = true;
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'assistant-thinking-toggle';
  const chevron = document.createElement('span');
  chevron.className = 'assistant-thinking-chevron';
  chevron.textContent = '›';
  const dot = document.createElement('span');
  dot.className = 'assistant-thinking-dot';
  const label = document.createElement('span');
  label.textContent = 'Thinking';
  toggle.append(chevron, dot, label);
  const body = document.createElement('div');
  body.className = 'assistant-thinking-body';
  block.append(toggle, body);
  host.append(note, block);

  let text = '';
  let live = false;

  const scroll = () => {
    body.scrollTop = body.scrollHeight;
  };
  toggle.onclick = () => {
    const open = block.dataset.open !== 'true';
    block.dataset.open = open ? 'true' : 'false';
    if (open) scroll();
  };

  return {
    note(message: string) {
      note.hidden = !message.trim();
      note.textContent = message;
    },
    begin() {
      live = true;
      note.hidden = true;
      block.hidden = false;
      block.dataset.live = 'true';
      block.dataset.open = 'true';
      label.textContent = 'Thinking';
      body.hidden = !text.trim();
    },
    append(chunk: string) {
      if (!live) this.begin();
      text += chunk;
      body.hidden = !text.trim();
      body.textContent = text;
      scroll();
    },
    finish() {
      live = false;
      block.dataset.live = 'false';
      label.textContent = text.trim() ? 'Thought' : 'Thinking';
      block.hidden = !text.trim();
      if (text.trim()) scroll();
    },
    clear() {
      text = '';
      live = false;
      note.hidden = true;
      note.textContent = '';
      body.textContent = '';
      body.hidden = true;
      block.hidden = true;
      block.dataset.live = 'false';
      block.dataset.open = 'false';
      label.textContent = 'Thinking';
    },
  };
}

export function appendThinkingBlock(
  parent: HTMLElement,
  text: string,
  opts: { live: boolean; open: boolean; onToggle?: (open: boolean) => void },
) {
  const block = document.createElement('div');
  block.className = 'assistant-thinking';
  block.dataset.live = opts.live ? 'true' : 'false';
  block.dataset.open = opts.open ? 'true' : 'false';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'assistant-thinking-toggle';
  const chevron = document.createElement('span');
  chevron.className = 'assistant-thinking-chevron';
  chevron.textContent = '›';
  const dot = document.createElement('span');
  dot.className = 'assistant-thinking-dot';
  const label = document.createElement('span');
  label.textContent = opts.live ? 'Thinking' : 'Thought';
  toggle.append(chevron, dot, label);
  const body = document.createElement('div');
  body.className = 'assistant-thinking-body';
  body.textContent = text;
  toggle.onclick = () => {
    const open = block.dataset.open !== 'true';
    block.dataset.open = open ? 'true' : 'false';
    opts.onToggle?.(open);
    if (open) body.scrollTop = body.scrollHeight;
  };
  block.append(toggle);
  if (text.trim()) block.append(body);
  parent.append(block);
  if (opts.open && text.trim()) body.scrollTop = body.scrollHeight;
}
