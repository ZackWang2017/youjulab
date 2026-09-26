import type { Patch } from '../context/types';
import { formattedInsertHtml } from '../editors/document/markdown';

export function renderPatch(container: HTMLElement, patch: Patch) {
  const original = patch.selection.values
    ?? patch.selection.text
    ?? (patch.surface === 'calendar' ? patch.selection.date : null);
  const format = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const insert = patch.task === 'generate';
  container.replaceChildren();
  if (insert && patch.surface === 'document' && typeof patch.replacement === 'string') {
    const section = document.createElement('section');
    section.className = 'after';
    const title = document.createElement('strong');
    title.textContent = '插入内容';
    const rich = document.createElement('div');
    rich.className = 'patch-rich';
    rich.innerHTML = formattedInsertHtml(patch.replacement);
    section.append(title, rich);
    container.append(section);
    return;
  }
  for (const [label, value, className] of insert
    ? [['插入内容', patch.replacement, 'after']]
    : [['修改前', original, 'before'], ['修改后', patch.replacement, 'after']]) {
    const section = document.createElement('section'); section.className = String(className);
    const title = document.createElement('strong'); title.textContent = String(label);
    const pre = document.createElement('pre'); pre.textContent = format(value);
    section.append(title, pre); container.append(section);
  }
}
