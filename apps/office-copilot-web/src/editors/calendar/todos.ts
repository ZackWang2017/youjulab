/** Markdown 待办：`- [ ]` 未完成，`- [x]` 已完成。 */

export type TodoItem = { done: boolean; text: string };

const TASK_LINE = /^\s*[-*+]\s+\[([ xX])\]\s?(.*)$/;

export function parseTodoMarkdown(markdown: string): TodoItem[] {
  const items: TodoItem[] = [];
  for (const line of String(markdown || '').split(/\r?\n/)) {
    const matched = line.match(TASK_LINE);
    if (!matched) continue;
    items.push({ done: matched[1].toLowerCase() === 'x', text: matched[2] });
  }
  return items;
}

export function serializeTodoMarkdown(items: TodoItem[]): string {
  return items
    .filter(item => item.text.trim())
    .map(item => `- [${item.done ? 'x' : ' '}] ${item.text.trim()}`)
    .join('\n');
}

export function openTodoList(
  markdown: string,
  onChange: (next: string) => void,
): Promise<void> {
  return new Promise(resolve => {
    let items = parseTodoMarkdown(markdown);
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog calendar-todo-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-labelledby', 'calendar-todo-title');

    const title = document.createElement('h2');
    title.id = 'calendar-todo-title';
    title.textContent = '待办事项';

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.id = 'calendar-todo-close';
    closeBtn.className = 'cal-todo-close';
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.title = '关闭';
    closeBtn.textContent = '×';

    const list = document.createElement('div');
    list.className = 'cal-todo-list';

    const addRow = document.createElement('form');
    addRow.className = 'cal-todo-add';
    addRow.innerHTML = `
      <input type="text" maxlength="200" placeholder="添加待办，回车确认" aria-label="新待办">
      <button type="submit" class="primary">添加</button>`;

    dialog.append(closeBtn, title, list, addRow);
    overlay.append(dialog);
    document.body.append(overlay);

    const input = addRow.querySelector('input')!;
    const persist = () => {
      onChange(serializeTodoMarkdown(items));
    };

    const render = () => {
      list.replaceChildren();
      if (!items.length) {
        const empty = document.createElement('p');
        empty.className = 'cal-me-empty';
        empty.textContent = '暂无待办';
        list.append(empty);
        return;
      }
      items.forEach((item, index) => {
        const row = document.createElement('div');
        row.className = `cal-todo-row${item.done ? ' is-done' : ''}`;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = item.done;
        box.setAttribute('aria-label', item.done ? '标为未完成' : '标为已完成');
        const text = document.createElement('textarea');
        text.rows = 1;
        text.maxLength = 200;
        text.value = item.text.replace(/\r?\n/g, '');
        text.setAttribute('aria-label', '待办内容');
        const fitText = () => {
          text.style.height = '0';
          text.style.height = `${text.scrollHeight}px`;
        };
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'cal-todo-delete';
        del.textContent = '删除';
        box.addEventListener('change', () => {
          items[index] = { ...items[index], done: box.checked };
          persist();
          render();
        });
        text.addEventListener('input', () => {
          const next = text.value.replace(/\r?\n/g, '');
          if (next !== text.value) text.value = next;
          items[index] = { ...items[index], text: next };
          fitText();
        });
        text.addEventListener('blur', () => {
          items[index] = { ...items[index], text: text.value };
          persist();
          if (!text.value.trim()) render();
        });
        text.addEventListener('keydown', e => {
          if (e.key !== 'Enter') return;
          e.preventDefault();
          text.blur();
        });
        del.addEventListener('click', () => {
          items = items.filter((_, i) => i !== index);
          persist();
          render();
        });
        row.append(box, text, del);
        list.append(row);
        fitText();
      });
    };

    addRow.addEventListener('submit', e => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      items = [...items, { done: false, text }];
      input.value = '';
      persist();
      render();
      input.focus();
    });

    const close = () => {
      persist();
      overlay.remove();
      resolve();
    };
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', e => {
      if (e.target === overlay) close();
    });
    render();
    input.focus();
  });
}
