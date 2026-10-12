import type { Patch } from '../context/types';
import { formattedInsertHtml } from '../editors/document/markdown';

export function renderPatch(container: HTMLElement, patch: Patch) {
  const original = patch.selection.values
    ?? patch.selection.text
    ?? (patch.surface === 'calendar' ? patch.selection.date : null);
  const format = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const insert = patch.task === 'generate';
  const formatOnly = patch.replacement == null && Array.isArray(patch.format_ops) && patch.format_ops.length > 0;
  container.replaceChildren();
  if (patch.surface === 'mindmap' && Array.isArray(patch.mindmap_ops) && patch.mindmap_ops.length) {
    const labels = { add: '新增', update: '修改', delete: '删除', move: '调整层级' };
    const section = document.createElement('section');
    section.className = 'after';
    const title = document.createElement('strong');
    title.textContent = '导图修改';
    const pre = document.createElement('pre');
    pre.textContent = patch.mindmap_ops.map(op => {
      const name = op.topic ? `：${op.topic}` : '';
      const parent = op.parent_id ? `（父节点 ${op.parent_id}）` : '';
      return `${labels[op.op]} ${op.id || ''}${name}${parent}`.trim();
    }).join('\n');
    section.append(title, pre);
    container.append(section);
    return;
  }
  if (formatOnly) {
    const section = document.createElement('section');
    section.className = 'after';
    const title = document.createElement('strong');
    title.textContent = '数字格式';
    const pre = document.createElement('pre');
    pre.textContent = format((patch.format_ops || []).map(op => `${op.address} → ${op.number_format}`));
    section.append(title, pre);
    container.append(section);
    return;
  }
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
  if (Array.isArray(patch.format_ops) && patch.format_ops.length) {
    const section = document.createElement('section');
    section.className = 'after';
    const title = document.createElement('strong');
    title.textContent = '数字格式';
    const pre = document.createElement('pre');
    pre.textContent = format(patch.format_ops.map(op => `${op.address} → ${op.number_format}`));
    section.append(title, pre);
    container.append(section);
  }
}
