import MindElixir from 'mind-elixir';
import type { MindElixirData, NodeObj } from 'mind-elixir';
import { zh_CN } from 'mind-elixir/i18n';
import 'mind-elixir/style.css';
import './style.css';
import { download, type EditorAdapter, type Patch } from '../../context/types';

type MapData = MindElixirData & { format?: string };

const FALLBACK: MapData = {
  format: 'mind-elixir-v1',
  nodeData: { id: 'root', topic: '中心主题', children: [] },
  arrows: [],
  summaries: [],
  direction: MindElixir.SIDE,
};

function isMapData(content: unknown): content is MapData {
  return Boolean(
    content
    && typeof content === 'object'
    && !Array.isArray(content)
    && (content as MapData).nodeData
    && typeof (content as MapData).nodeData === 'object'
    && typeof (content as MapData).nodeData.id === 'string'
    && typeof (content as MapData).nodeData.topic === 'string',
  );
}

function findPath(root: NodeObj, id: string, trail: string[] = []): string[] | null {
  const next = [...trail, root.topic || ''];
  if (root.id === id) return next;
  for (const child of root.children || []) {
    const hit = findPath(child, id, next);
    if (hit) return hit;
  }
  return null;
}

function findParentId(root: NodeObj, id: string): string | null {
  for (const child of root.children || []) {
    if (child.id === id) return root.id;
    const hit = findParentId(child, id);
    if (hit) return hit;
  }
  return null;
}

function findNode(root: NodeObj, id: string): NodeObj | null {
  if (root.id === id) return root;
  for (const child of root.children || []) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
}

function plainTopic(raw: string) {
  return String(raw || '')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function topicEl(id: string) {
  return (MindElixir as unknown as { E: (nodeId: string) => HTMLElement | null }).E(id);
}

function mindmapTree(node: NodeObj): Record<string, unknown> {
  return {
    id: node.id,
    topic: plainTopic(node.topic),
    ...(node.note?.trim() ? { note: node.note.trim() } : {}),
    children: (node.children || []).map(mindmapTree),
  };
}

function cloneMapData(data: MapData): MapData {
  return JSON.parse(JSON.stringify(data)) as MapData;
}

function makeNodeId() {
  return typeof crypto.randomUUID === 'function'
    ? `ai-${crypto.randomUUID()}`
    : `ai-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function mountMindmap(
  container: HTMLElement,
  changed: () => void,
  onDirty: (assetId: string, content: unknown) => void,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  let selectedId = '';

  container.innerHTML = `<div class="mindmap-stage" data-mindmap-host></div>`;
  const host = container.querySelector<HTMLElement>('[data-mindmap-host]')!;

  const selectedTopic = () => mind.currentNode;

  const patchSelected = (patch: Partial<NodeObj>) => {
    const topic = selectedTopic();
    if (!topic) return;
    host.querySelectorAll<HTMLElement>('.context-menu').forEach(menu => { menu.hidden = true; });
    void mind.reshapeNode(topic, patch);
  };

  const openNote = (topic = selectedTopic()) => {
    if (!topic) return;
    host.querySelectorAll<HTMLElement>('.context-menu').forEach(menu => { menu.hidden = true; });
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('form');
    dialog.className = 'mindmap-note-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.innerHTML = `
      <h2>备注</h2>
      <textarea maxlength="8000" placeholder="写下这个节点的说明" aria-label="备注"></textarea>
      <div class="dialog-actions">
        <button type="button" data-cancel>取消</button>
        <button type="submit" class="primary">保存</button>
      </div>`;
    const input = dialog.querySelector('textarea')!;
    input.value = topic.nodeObj.note || '';
    overlay.append(dialog);
    document.body.append(overlay);
    const close = () => overlay.remove();
    dialog.querySelector('[data-cancel]')!.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    dialog.addEventListener('submit', e => {
      e.preventDefault();
      const note = input.value.replace(/^\uFEFF/, '').trim();
      close();
      void mind.reshapeNode(topic, { note });
    });
    input.focus();
  };

  const mind = new MindElixir({
    el: host,
    direction: MindElixir.SIDE,
    editable: true,
    contextMenu: {
      locale: zh_CN,
      focus: true,
      link: true,
      extend: [
        { name: '备注', onclick: () => openNote() },
        {
          name: '超链接',
          onclick: () => {
            const topic = selectedTopic();
            if (!topic) return;
            const current = topic.nodeObj.hyperLink || '';
            const next = window.prompt('节点超链接，留空则去掉', current);
            if (next === null) return;
            patchSelected({ hyperLink: next.trim() });
          },
        },
        {
          name: '标签',
          onclick: () => {
            const topic = selectedTopic();
            if (!topic) return;
            const current = (topic.nodeObj.tags || []).map(tag => typeof tag === 'string' ? tag : tag.text).join('，');
            const next = window.prompt('标签，用逗号分隔，留空则去掉', current);
            if (next === null) return;
            const tags = next.split(/[,，]/).map(item => item.trim()).filter(Boolean);
            patchSelected({ tags });
          },
        },
        {
          name: '图片',
          onclick: () => {
            const topic = selectedTopic();
            if (!topic) return;
            const current = topic.nodeObj.image?.url || '';
            const next = window.prompt('图片地址，留空则去掉', current);
            if (next === null) return;
            const url = next.trim();
            patchSelected({
              image: url ? { url, width: 160, height: 90, fit: 'contain' } : undefined,
            });
          },
        },
      ],
    },
    toolBar: true,
    keypress: true,
    allowUndo: true,
    newTopicName: '新节点',
    scaleMin: 0.2,
    scaleMax: 2.5,
    scaleSensitivity: 0.15,
  });

  /** 聚焦时 getData() 返回进入聚焦前的整棵树。换文档时必须丢掉这份备份，否则会写进下一张导图。 */
  const leaveFocus = () => {
    mind.isFocusMode = false;
    mind.tempDirection = null;
    delete (mind as { nodeDataBackup?: NodeObj }).nodeDataBackup;
  };

  const emitDirty = () => {
    if (loading || !assetId) return;
    const id = assetId;
    revision += 1;
    onDirty(id, { format: 'mind-elixir-v1', ...mind.getData() });
    changed();
  };

  let openNoteId = '';
  let noteBubble: HTMLElement | null = null;

  const closeNoteBubble = () => {
    openNoteId = '';
    noteBubble?.remove();
    noteBubble = null;
    host.querySelectorAll<HTMLButtonElement>('.mindmap-note-mark.is-open').forEach(mark => {
      mark.classList.remove('is-open');
      mark.setAttribute('aria-expanded', 'false');
    });
  };

  const placeNoteBubble = () => {
    if (!noteBubble || !openNoteId) return;
    const mark = host.querySelector<HTMLButtonElement>(
      `.mindmap-note-mark[data-node-id="${CSS.escape(openNoteId)}"]`,
    );
    if (!mark?.isConnected) {
      closeNoteBubble();
      return;
    }
    const rect = mark.getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();
    const margin = 8;
    const width = noteBubble.offsetWidth;
    const height = noteBubble.offsetHeight;
    const below = rect.top - hostRect.top < height + margin + 12;
    let left = rect.right - hostRect.left - width;
    if (left < margin) left = rect.left - hostRect.left;
    left = Math.max(margin, Math.min(left, hostRect.width - width - margin));
    let top = below ? rect.bottom - hostRect.top + margin : rect.top - hostRect.top - margin - height;
    top = Math.max(margin, Math.min(top, hostRect.height - height - margin));
    noteBubble.style.left = `${left}px`;
    noteBubble.style.top = `${top}px`;
  };

  const syncNoteBubble = () => {
    if (!openNoteId) return;
    const mark = host.querySelector<HTMLButtonElement>(
      `.mindmap-note-mark[data-node-id="${CSS.escape(openNoteId)}"]`,
    );
    const node = (mark?.parentElement as (HTMLElement & { nodeObj?: NodeObj }) | null)?.nodeObj;
    const note = String(node?.note || '').trim();
    if (!mark || !note) {
      closeNoteBubble();
      return;
    }
    if (!noteBubble) {
      noteBubble = document.createElement('div');
      noteBubble.className = 'mindmap-note-bubble';
      noteBubble.setAttribute('role', 'tooltip');
      noteBubble.addEventListener('pointerdown', e => e.stopPropagation());
      host.append(noteBubble);
    }
    noteBubble.textContent = note;
    host.querySelectorAll<HTMLButtonElement>('.mindmap-note-mark.is-open').forEach(item => {
      if (item !== mark) {
        item.classList.remove('is-open');
        item.setAttribute('aria-expanded', 'false');
      }
    });
    mark.classList.add('is-open');
    mark.setAttribute('aria-expanded', 'true');
    placeNoteBubble();
  };

  const markNotes = () => {
    host.querySelectorAll<HTMLElement>('.me-tpc').forEach(el => {
      const node = (el as HTMLElement & { nodeObj?: NodeObj }).nodeObj;
      const note = String(node?.note || '').trim();
      let mark = el.querySelector<HTMLButtonElement>(':scope > .mindmap-note-mark');
      if (!note || !node?.id) {
        mark?.remove();
        return;
      }
      if (!mark) {
        mark = document.createElement('button');
        mark.type = 'button';
        mark.className = 'mindmap-note-mark';
        mark.textContent = '注';
        mark.setAttribute('aria-expanded', 'false');
        mark.addEventListener('pointerdown', e => e.stopPropagation());
        mark.addEventListener('click', e => {
          e.preventDefault();
          e.stopPropagation();
          const id = mark?.dataset.nodeId || '';
          if (!id) return;
          if (openNoteId === id) closeNoteBubble();
          else {
            openNoteId = id;
            syncNoteBubble();
          }
        });
        el.append(mark);
      }
      mark.dataset.nodeId = node.id;
      mark.setAttribute('aria-label', '备注');
    });
    syncNoteBubble();
  };

  mind.bus.addListener('operation', () => { markNotes(); emitDirty(); });
  mind.bus.addListener('linkDiv', () => { markNotes(); });
  mind.bus.addListener('scale', () => { placeNoteBubble(); });
  mind.bus.addListener('move', () => { placeNoteBubble(); });
  mind.bus.addListener('changeDirection', () => { emitDirty(); });
  mind.bus.addListener('selectNodes', (nodes) => {
    selectedId = nodes?.[0]?.id || '';
    changed();
  });
  mind.bus.addListener('selectNewNode', (node) => {
    selectedId = node?.id || '';
    changed();
  });
  mind.bus.addListener('unselectNodes', () => {
    selectedId = '';
    changed();
  });

  let mapReady = false;
  const redrawLines = () => {
    if (!mapReady || container.hidden || host.clientWidth < 8) return;
    mind.linkDiv();
  };
  new ResizeObserver(() => { redrawLines(); }).observe(host);

  void mind.init(FALLBACK).then(() => {
    mapReady = true;
    redrawLines();
    const bar = document.createElement('div');
    bar.className = 'mindmap-native-tools';
    const add = (label: string, title: string, run: () => void) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.title = title;
      button.addEventListener('click', run);
      bar.append(button);
      return button;
    };
    add('向下', '向下布局', () => { mind.initDown(); });
    const compactBtn = add('紧凑', '切换紧凑间距', () => {
      mind.changeCompact(!mind.compact);
      compactBtn.classList.toggle('is-on', Boolean(mind.compact));
      emitDirty();
    });
    const themeBtn = add('深色', '切换浅色或深色主题', () => {
      const dark = mind.theme?.name === MindElixir.DARK_THEME.name;
      mind.changeTheme(dark ? MindElixir.THEME : MindElixir.DARK_THEME);
      themeBtn.textContent = dark ? '深色' : '浅色';
      themeBtn.classList.toggle('is-on', !dark);
      emitDirty();
    });
    add('适应', '缩放到适合窗口', () => { mind.scaleFit(); });
    host.append(bar);
  });

  /** `refresh` 只应用 nodeData/arrows/summaries/meta，theme/direction/compact 需单独恢复。 */
  function applyMapData(raw: MapData) {
    if (typeof raw.direction === 'number') {
      mind.direction = raw.direction as 0 | 1 | 2 | 3;
    }
    if (typeof raw.compact === 'boolean') {
      mind.compact = raw.compact;
    }
    if (raw.theme) {
      mind.changeTheme(raw.theme, false);
    }
    mind.refresh({
      nodeData: raw.nodeData,
      arrows: raw.arrows || [],
      summaries: raw.summaries || [],
      meta: raw.meta,
    });
  }

  function currentSelection() {
    if (!assetId) return null;
    const data = mind.getData();
    const root = data.nodeData;
    const id = selectedId || root.id;
    const node = findNode(root, id) || root;
    const path = findPath(root, node.id) || [node.topic];
    const parentId = node.id === root.id ? null : findParentId(root, node.id);
    const siblings = (() => {
      if (!parentId) return (root.children || []).map(c => plainTopic(c.topic)).slice(0, 12);
      const parent = findNode(root, parentId);
      return (parent?.children || []).map(c => plainTopic(c.topic)).slice(0, 12);
    })();
    return {
      document_id: assetId,
      surface: 'mindmap' as const,
      revision,
      selection: {
        id: node.id,
        text: plainTopic(node.topic),
        path,
        parent_id: parentId,
        is_root: node.id === root.id,
      },
      surrounding: JSON.stringify({
        format: 'mind-elixir-v1',
        selected_id: node.id,
        selected_path: path,
        siblings,
        tree: mindmapTree(root),
      }),
    };
  }

  function applyMindmapOps(patch: Patch) {
    const ops = patch.mindmap_ops || [];
    if (!ops.length) throw new Error('导图修改为空。');
    const raw = cloneMapData({ format: 'mind-elixir-v1', ...mind.getData() });
    const root = raw.nodeData;
    const detach = (id: string): NodeObj | null => {
      const parentId = findParentId(root, id);
      if (!parentId) return null;
      const parent = findNode(root, parentId);
      const children = parent?.children || [];
      const index = children.findIndex(child => child.id === id);
      return index < 0 ? null : children.splice(index, 1)[0];
    };
    for (const op of ops) {
      if (op.op === 'add') {
        const parent = findNode(root, String(op.parent_id || ''));
        const topic = String(op.topic || '').trim();
        if (!parent || !topic) throw new Error('新增节点缺少父节点或标题。');
        const child = { id: makeNodeId(), topic, children: [] } as NodeObj;
        const index = Math.max(0, Math.min(Number.isInteger(op.index) ? Number(op.index) : parent.children?.length || 0, parent.children?.length || 0));
        (parent.children ||= []).splice(index, 0, child);
        selectedId = child.id;
      } else if (op.op === 'update') {
        const node = findNode(root, String(op.id || ''));
        const topic = String(op.topic || '').trim();
        if (!node || !topic) throw new Error('修改节点不存在或标题为空。');
        node.topic = topic;
        selectedId = node.id;
      } else if (op.op === 'delete') {
        const id = String(op.id || '');
        if (!id || id === root.id) throw new Error('不能删除根节点。');
        if (!detach(id)) throw new Error('删除节点不存在。');
        selectedId = root.id;
      } else if (op.op === 'move') {
        const id = String(op.id || '');
        const node = findNode(root, id);
        const parent = findNode(root, String(op.parent_id || ''));
        if (!node || !parent || id === root.id || node === parent || findNode(node, parent.id)) {
          throw new Error('导图父子关系调整无效。');
        }
        const moved = detach(id);
        if (!moved) throw new Error('调整节点不存在。');
        const index = Math.max(0, Math.min(Number.isInteger(op.index) ? Number(op.index) : parent.children?.length || 0, parent.children?.length || 0));
        (parent.children ||= []).splice(index, 0, moved);
        selectedId = moved.id;
      }
    }
    applyMapData(raw);
    markNotes();
    emitDirty();
  }

  return {
    async load(asset) {
      loading = true;
      closeNoteBubble();
      leaveFocus();
      assetId = asset.id;
      revision = asset.revision;
      selectedId = '';
      const raw = isMapData(asset.content) ? asset.content : {
        ...FALLBACK,
        nodeData: {
          ...FALLBACK.nodeData,
          topic: asset.name || FALLBACK.nodeData.topic,
        },
      };
      applyMapData(raw);
      mind.clearHistory?.();
      mind.toCenter();
      requestAnimationFrame(() => { redrawLines(); });
      selectedId = raw.nodeData.id;
      loading = false;
      if (!isMapData(asset.content)) emitDirty();
      else changed();
    },
    context() {
      const snap = currentSelection();
      if (!snap || !String(snap.selection.text || '').trim()) return null;
      return snap;
    },
    anchor() {
      return currentSelection();
    },
    apply(patch: Patch) {
      if (patch.revision !== revision) throw new Error('导图已更改，请重新生成建议。');
      if (patch.mindmap_ops?.length) {
        applyMindmapOps(patch);
        return;
      }
      const id = String(patch.selection?.id || '');
      if (!id) throw new Error('缺少导图节点。');
      const el = topicEl(id);
      if (!el) throw new Error('导图节点不存在，请重新选择。');
      const text = String(patch.replacement ?? '').trim();
      if (patch.task === 'generate') {
        if (!text) throw new Error('生成内容不能为空。');
        const child = mind.generateNewObj();
        child.topic = text;
        void mind.addChild(el as never, child);
        return;
      }
      void mind.setNodeTopic(el as never, text);
    },
    snapshot() {
      return { format: 'mind-elixir-v1', ...mind.getData() };
    },
    undo() { mind.undo(); },
    export() {
      download('mindmap.mind-elixir.json', JSON.stringify({ format: 'mind-elixir-v1', ...mind.getData() }, null, 2));
    },
  };
}
