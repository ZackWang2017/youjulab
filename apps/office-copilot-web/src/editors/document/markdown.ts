const ALLOWED = new Set(['P', 'H1', 'H2', 'H3', 'STRONG', 'B', 'EM', 'I', 'UL', 'OL', 'LI', 'BR', 'CODE', 'PRE']);

function unwrapFence(src: string) {
  return src.trim().replace(/^```(?:markdown|md|html)?\s*\n([\s\S]*?)\n```$/i, '$1').trim();
}

function escapeText(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function inline(value: string) {
  return escapeText(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/(^|[^_])_(.+?)_([^_]|$)/g, '$1<em>$2</em>$3');
}

export function markdownToHtml(src: string) {
  const text = unwrapFence(src).replace(/\r\n/g, '\n');
  if (!text.trim()) return '<p></p>';
  const lines = text.split('\n');
  const blocks: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (/^```/.test(line)) {
      const lang = line.replace(/^```/, '').trim();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      const cls = lang ? ` class="language-${escapeText(lang)}"` : '';
      blocks.push(`<pre><code${cls}>${escapeText(code.join('\n'))}</code></pre>`);
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^[-*]\s+/, ''))}</li>`);
        i += 1;
      }
      blocks.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\d+\.\s+/, ''))}</li>`);
        i += 1;
      }
      blocks.push(`<ol>${items.join('')}</ol>`);
      continue;
    }
    const para = [line];
    i += 1;
    while (i < lines.length && lines[i].trim()
      && !/^(#{1,3})\s+/.test(lines[i]) && !/^[-*]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i])
      && !/^```/.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(`<p>${para.map(inline).join('<br>')}</p>`);
  }
  return blocks.join('') || `<p>${inline(text)}</p>`;
}

function serializeAllowed(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return escapeText(node.textContent || '');
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const el = node as HTMLElement;
  const tag = el.tagName === 'B' ? 'STRONG' : el.tagName === 'I' ? 'EM' : el.tagName;
  if (tag === 'BR') return '<br>';
  if (!ALLOWED.has(tag)) return Array.from(el.childNodes).map(serializeAllowed).join('');
  const inner = Array.from(el.childNodes).map(serializeAllowed).join('');
  const name = tag.toLowerCase();
  return inner ? `<${name}>${inner}</${name}>` : (name === 'p' ? '<p></p>' : '');
}

export function formattedInsertHtml(src: string) {
  const text = unwrapFence(src);
  if (!text) return '<p></p>';
  if (/<[a-z][\s\S]*>/i.test(text) && typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    const html = Array.from(doc.body.childNodes).map(serializeAllowed).join('').trim();
    if (html) return html;
  }
  return markdownToHtml(text);
}
