import { createIcons, icons } from 'lucide';
import { download, type EditorAdapter, type Patch } from '../../context/types';
import './style.css';

type ResearchEvent = { id: string; date: string; title: string; detail?: string; source?: string };
type FishboneItem = { id: string; label: string; evidence?: string; source?: string };
type FishboneCategory = { id: string; label: string; items: FishboneItem[] };
type TimelineDiagram = {
  format: 'invp-research-diagram-v1';
  type: 'timeline';
  title: string;
  events: ResearchEvent[];
};
type FishboneDiagram = {
  format: 'invp-research-diagram-v1';
  type: 'fishbone';
  title: string;
  problem: string;
  categories: FishboneCategory[];
};
type ResearchDiagram = TimelineDiagram | FishboneDiagram;

const FORMAT = 'invp-research-diagram-v1' as const;

function uid(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function defaultTimeline(): TimelineDiagram {
  return {
    format: FORMAT,
    type: 'timeline',
    title: '公司关键事件',
    events: [
      { id: uid('event'), date: '2026 Q1', title: '事件或公告', detail: '记录影响与判断', source: '' },
      { id: uid('event'), date: '2026 Q2', title: '后续催化剂', detail: '补充验证条件', source: '' },
    ],
  };
}

function defaultFishbone(): FishboneDiagram {
  return {
    format: FORMAT,
    type: 'fishbone',
    title: '核心问题归因',
    problem: '利润增速低于预期',
    categories: [
      { id: uid('category'), label: '收入', items: [{ id: uid('cause'), label: '销量变化', evidence: '', source: '' }] },
      { id: uid('category'), label: '成本', items: [{ id: uid('cause'), label: '原材料价格', evidence: '', source: '' }] },
      { id: uid('category'), label: '费用', items: [{ id: uid('cause'), label: '销售投入', evidence: '', source: '' }] },
      { id: uid('category'), label: '外部因素', items: [{ id: uid('cause'), label: '行业需求', evidence: '', source: '' }] },
    ],
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : '';
}

function normalize(content: unknown): ResearchDiagram {
  if (!content || typeof content !== 'object' || Array.isArray(content)) return defaultTimeline();
  const raw = content as Record<string, unknown>;
  if (raw.format !== FORMAT) return defaultTimeline();
  if (raw.type === 'fishbone') {
    const categories = Array.isArray(raw.categories) ? raw.categories.map((entry, categoryIndex) => {
      const category = entry && typeof entry === 'object' ? entry as Record<string, unknown> : {};
      const items = Array.isArray(category.items) ? category.items.map((cause, itemIndex) => {
        const item = cause && typeof cause === 'object' ? cause as Record<string, unknown> : {};
        return {
          id: stringValue(item.id) || `cause-${categoryIndex + 1}-${itemIndex + 1}`,
          label: stringValue(item.label),
          evidence: stringValue(item.evidence),
          source: stringValue(item.source),
        };
      }) : [];
      return { id: stringValue(category.id) || `category-${categoryIndex + 1}`, label: stringValue(category.label), items };
    }) : [];
    return { format: FORMAT, type: 'fishbone', title: stringValue(raw.title), problem: stringValue(raw.problem), categories };
  }
  if (raw.type !== 'timeline') throw new Error('研究图类型必须是 timeline 或 fishbone。');
  const events = Array.isArray(raw.events) ? raw.events.map((entry, index) => {
    const event = entry && typeof entry === 'object' ? entry as Record<string, unknown> : {};
    return {
      id: stringValue(event.id) || `event-${index + 1}`,
      date: stringValue(event.date),
      title: stringValue(event.title),
      detail: stringValue(event.detail),
      source: stringValue(event.source),
    };
  }) : [];
  return { format: FORMAT, type: 'timeline', title: stringValue(raw.title), events };
}

function parseReplacement(value: Patch['replacement']): ResearchDiagram {
  if (typeof value !== 'string') throw new Error('研究图建议必须是完整 JSON。');
  try {
    return normalize(JSON.parse(value));
  } catch (error) {
    if (error instanceof Error && error.message.includes('研究图类型')) throw error;
    throw new Error('研究图建议不是有效 JSON，请重新生成。');
  }
}

function escapeMermaid(value: string) {
  return value.replace(/[\r\n]+/g, ' ').replace(/:/g, '：').replace(/#/g, '＃').trim();
}

function timelineSource(model: TimelineDiagram) {
  const lines = ['timeline'];
  if (model.title.trim()) lines.push(`  title ${escapeMermaid(model.title)}`);
  for (const event of model.events) {
    const date = escapeMermaid(event.date) || '待定';
    const title = escapeMermaid(event.title) || '未命名事件';
    lines.push(`  ${date} : ${title}`);
    if (event.detail?.trim()) lines.push(`           : ${escapeMermaid(event.detail)}`);
  }
  return `${lines.join('\n')}\n`;
}

function field(label: string, value: string, attrs: string, area = false) {
  const tag = area ? `<textarea ${attrs}></textarea>` : `<input ${attrs}>`;
  return `<label class="research-field"><span>${label}</span>${tag.replace(area ? '</textarea>' : '>', area ? `${escapeHtml(value)}</textarea>` : ` value="${escapeHtml(value)}">`)}</label>`;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]!);
}

function wrapText(value: string, max = 13) {
  const text = value.trim() || '未命名';
  const parts: string[] = [];
  for (let i = 0; i < text.length; i += max) parts.push(text.slice(i, i + max));
  return parts.slice(0, 3);
}

function svgText(lines: string[], x: number, y: number, options: { anchor?: string; className?: string; lineHeight?: number } = {}) {
  const anchor = options.anchor || 'start';
  const lineHeight = options.lineHeight || 18;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" class="${options.className || ''}">${lines.map((line, index) => `<tspan x="${x}" dy="${index ? lineHeight : 0}">${escapeHtml(line)}</tspan>`).join('')}</text>`;
}

function fishboneSvg(model: FishboneDiagram) {
  const count = Math.max(model.categories.length, 1);
  const width = Math.max(960, count * 175 + 250);
  const height = 620;
  const spineY = 310;
  const startX = 70;
  const headX = width - 150;
  const usable = headX - startX - 55;
  const ribs = model.categories.map((category, index) => {
    const x = startX + usable * ((index + 1) / (count + 1));
    const top = index % 2 === 0;
    const tipX = x - 92;
    const tipY = top ? 92 : 528;
    const categoryY = top ? tipY - 18 : tipY + 31;
    const itemStep = 44;
    const items = category.items.slice(0, 5).map((item, itemIndex) => {
      const ratio = (itemIndex + 1) / (Math.max(category.items.length, 1) + 1);
      const itemX = x + (tipX - x) * ratio;
      const itemY = spineY + (tipY - spineY) * ratio;
      const dir = top ? -1 : 1;
      return `<circle cx="${itemX}" cy="${itemY}" r="4" class="fishbone-dot"/>${svgText(wrapText(item.label, 11), itemX + 10, itemY + dir * 8, { className: 'fishbone-item', lineHeight: 15 })}`;
    }).join('');
    return `<g class="fishbone-rib"><line x1="${x}" y1="${spineY}" x2="${tipX}" y2="${tipY}"/><circle cx="${tipX}" cy="${tipY}" r="5"/>${svgText(wrapText(category.label, 12), tipX, categoryY, { anchor: 'middle', className: 'fishbone-category' })}${items}</g>`;
  }).join('');
  const problemLines = wrapText(model.problem, 10);
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(model.title || '鱼骨图')}">
    <defs><marker id="research-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 Z"/></marker></defs>
    <line class="fishbone-spine" x1="${startX}" y1="${spineY}" x2="${headX}" y2="${spineY}" marker-end="url(#research-arrow)"/>
    ${ribs}
    <rect class="fishbone-problem" x="${headX + 22}" y="${spineY - 61}" width="125" height="122" rx="6"/>
    ${svgText(problemLines, headX + 84, spineY - ((problemLines.length - 1) * 9), { anchor: 'middle', className: 'fishbone-problem-text', lineHeight: 20 })}
  </svg>`;
}

export function mountResearchDiagram(
  container: HTMLElement,
  changed: () => void,
  onDirty: (assetId: string, content: unknown) => void,
): EditorAdapter {
  let assetId = '';
  let revision = 0;
  let loading = false;
  let model: ResearchDiagram = defaultTimeline();
  let renderToken = 0;
  let history: ResearchDiagram[] = [];

  container.innerHTML = `<div class="research-shell">
    <div class="research-toolbar">
      <div class="research-types" role="group" aria-label="研究图类型">
        <button type="button" data-type="timeline"><i data-lucide="milestone"></i><span>时间轴</span></button>
        <button type="button" data-type="fishbone"><i data-lucide="git-branch"></i><span>鱼骨图</span></button>
      </div>
      <label class="research-title"><span>标题</span><input data-model-title maxlength="80"></label>
      <button type="button" data-export title="导出 JSON" aria-label="导出 JSON"><i data-lucide="download"></i></button>
    </div>
    <div class="research-body">
      <aside class="research-inspector" aria-label="研究图内容"></aside>
      <div class="research-canvas"><div class="research-preview"></div></div>
    </div>
  </div>`;
  const inspector = container.querySelector<HTMLElement>('.research-inspector')!;
  const preview = container.querySelector<HTMLElement>('.research-preview')!;
  const titleInput = container.querySelector<HTMLInputElement>('[data-model-title]')!;

  const snapshot = () => clone(model);
  const remember = () => {
    history.push(snapshot());
    if (history.length > 50) history.shift();
  };
  const emitDirty = () => {
    if (!assetId || loading) return;
    onDirty(assetId, snapshot());
    changed();
  };

  const paintTimeline = async (current: TimelineDiagram) => {
    const token = ++renderToken;
    const mermaid = (await import('mermaid')).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'inherit' });
    try {
      const { svg } = await mermaid.render(`research-timeline-${token}`, timelineSource(current));
      if (token !== renderToken || model.type !== 'timeline') return;
      preview.innerHTML = svg;
    } catch (error) {
      if (token !== renderToken) return;
      preview.innerHTML = `<p class="research-error">${escapeHtml(error instanceof Error ? error.message.split('\n')[0] : '时间轴无法绘制。')}</p>`;
    }
  };

  const paintPreview = () => {
    if (model.type === 'fishbone') {
      ++renderToken;
      preview.innerHTML = fishboneSvg(model);
      return;
    }
    void paintTimeline(model);
  };

  const renderInspector = () => {
    if (model.type === 'timeline') {
      inspector.innerHTML = `<div class="research-inspector-head"><div><strong>事件</strong><span>${model.events.length} 项</span></div><button type="button" data-add-event><i data-lucide="plus"></i><span>添加事件</span></button></div>
        <div class="research-list">${model.events.map((event, index) => `<article class="research-item" data-event="${index}">
          <header><span>${escapeHtml(event.date || `事件 ${index + 1}`)}</span><button type="button" data-remove-event="${index}" title="删除事件" aria-label="删除事件"><i data-lucide="trash-2"></i></button></header>
          ${field('时间', event.date, `data-event-index="${index}" data-field="date" maxlength="40"`)}
          ${field('事件', event.title, `data-event-index="${index}" data-field="title" maxlength="120"`)}
          ${field('影响与判断', event.detail || '', `data-event-index="${index}" data-field="detail" maxlength="400"`, true)}
          ${field('来源', event.source || '', `data-event-index="${index}" data-field="source" maxlength="500" placeholder="链接或文档位置"`)}
        </article>`).join('')}</div>`;
    } else {
      inspector.innerHTML = `${field('核心问题', model.problem, 'data-problem maxlength="120"')}
        <div class="research-inspector-head"><div><strong>原因分类</strong><span>${model.categories.length} 项</span></div><button type="button" data-add-category><i data-lucide="plus"></i><span>添加分类</span></button></div>
        <div class="research-list">${model.categories.map((category, categoryIndex) => `<article class="research-item" data-category="${categoryIndex}">
          <header><input aria-label="分类名称" data-category-index="${categoryIndex}" data-category-field="label" maxlength="60" value="${escapeHtml(category.label)}"><button type="button" data-remove-category="${categoryIndex}" title="删除分类" aria-label="删除分类"><i data-lucide="trash-2"></i></button></header>
          <div class="research-causes">${category.items.map((item, itemIndex) => `<section class="research-cause">
            <div class="research-cause-head"><strong>原因 ${itemIndex + 1}</strong><button type="button" data-remove-cause="${categoryIndex}:${itemIndex}" title="删除原因" aria-label="删除原因"><i data-lucide="x"></i></button></div>
            ${field('原因', item.label, `data-category-index="${categoryIndex}" data-item-index="${itemIndex}" data-item-field="label" maxlength="100"`)}
            ${field('证据', item.evidence || '', `data-category-index="${categoryIndex}" data-item-index="${itemIndex}" data-item-field="evidence" maxlength="400"`, true)}
            ${field('来源', item.source || '', `data-category-index="${categoryIndex}" data-item-index="${itemIndex}" data-item-field="source" maxlength="500" placeholder="链接或文档位置"`)}
          </section>`).join('')}</div>
          <button type="button" class="research-add-cause" data-add-cause="${categoryIndex}"><i data-lucide="plus"></i><span>添加原因</span></button>
        </article>`).join('')}</div>`;
    }
    createIcons({ icons });
  };

  const renderAll = () => {
    container.querySelectorAll<HTMLButtonElement>('[data-type]').forEach(button => {
      const active = button.dataset.type === model.type;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    titleInput.value = model.title;
    renderInspector();
    paintPreview();
  };

  container.querySelector('.research-types')!.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-type]');
    if (!button || button.dataset.type === model.type) return;
    remember();
    model = button.dataset.type === 'fishbone' ? defaultFishbone() : defaultTimeline();
    renderAll();
    emitDirty();
  });

  titleInput.addEventListener('input', () => {
    model.title = titleInput.value;
    paintPreview();
    emitDirty();
  });
  titleInput.addEventListener('focus', remember);

  inspector.addEventListener('focusin', event => {
    if ((event.target as HTMLElement).matches('input, textarea')) remember();
  });
  inspector.addEventListener('input', event => {
    const target = event.target as HTMLInputElement | HTMLTextAreaElement;
    if (model.type === 'timeline' && target.dataset.eventIndex !== undefined) {
      const item = model.events[Number(target.dataset.eventIndex)];
      const key = target.dataset.field as 'date' | 'title' | 'detail' | 'source';
      if (item && key) item[key] = target.value;
    } else if (model.type === 'fishbone') {
      if (target.hasAttribute('data-problem')) model.problem = target.value;
      const category = model.categories[Number(target.dataset.categoryIndex)];
      if (category && target.dataset.categoryField === 'label') category.label = target.value;
      const item = category?.items[Number(target.dataset.itemIndex)];
      const key = target.dataset.itemField as 'label' | 'evidence' | 'source';
      if (item && key) item[key] = target.value;
    }
    paintPreview();
    emitDirty();
  });

  inspector.addEventListener('click', event => {
    const target = event.target as HTMLElement;
    const action = target.closest('[data-add-event], [data-remove-event], [data-add-category], [data-remove-category], [data-add-cause], [data-remove-cause]');
    if (!action) return;
    remember();
    if (model.type === 'timeline') {
      if (target.closest('[data-add-event]')) model.events.push({ id: uid('event'), date: '', title: '', detail: '', source: '' });
      const remove = target.closest<HTMLElement>('[data-remove-event]');
      if (remove) model.events.splice(Number(remove.dataset.removeEvent), 1);
    } else {
      if (target.closest('[data-add-category]')) model.categories.push({ id: uid('category'), label: '新分类', items: [] });
      const removeCategory = target.closest<HTMLElement>('[data-remove-category]');
      if (removeCategory) model.categories.splice(Number(removeCategory.dataset.removeCategory), 1);
      const addCause = target.closest<HTMLElement>('[data-add-cause]');
      if (addCause) model.categories[Number(addCause.dataset.addCause)]?.items.push({ id: uid('cause'), label: '', evidence: '', source: '' });
      const removeCause = target.closest<HTMLElement>('[data-remove-cause]');
      if (removeCause) {
        const [categoryIndex, itemIndex] = removeCause.dataset.removeCause!.split(':').map(Number);
        model.categories[categoryIndex]?.items.splice(itemIndex, 1);
      }
    }
    renderAll();
    emitDirty();
  });

  container.querySelector('[data-export]')!.addEventListener('click', () => {
    download('research-diagram.json', JSON.stringify(model, null, 2));
  });
  createIcons({ icons });

  return {
    load(asset) {
      loading = true;
      assetId = asset.id;
      revision = asset.revision;
      model = normalize(asset.content);
      history = [];
      renderAll();
      loading = false;
      if (!asset.content || (asset.content as { format?: unknown }).format !== FORMAT) emitDirty();
      else changed();
    },
    context() {
      if (!assetId) return null;
      const text = JSON.stringify(model, null, 2);
      return {
        document_id: assetId,
        surface: 'research',
        revision,
        selection: { text, diagram_type: model.type },
        surrounding: text,
      };
    },
    anchor() { return this.context(); },
    apply(patch) {
      if (patch.revision !== revision) throw new Error('研究图已更改，请重新生成建议。');
      remember();
      model = parseReplacement(patch.replacement);
      renderAll();
      emitDirty();
    },
    snapshot,
    undo() {
      const previous = history.pop();
      if (!previous) return;
      model = previous;
      renderAll();
      emitDirty();
    },
    export() { download('research-diagram.json', JSON.stringify(model, null, 2)); },
  };
}
