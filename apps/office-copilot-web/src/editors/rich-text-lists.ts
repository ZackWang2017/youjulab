import { Editor, Extension } from '@tiptap/core';
import { BulletList, ListItem } from '@tiptap/extension-list';
import { Plugin } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export function listStack(editor: Editor) {
  const names: string[] = [];
  const $from = editor.state.selection.$from;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const name = $from.node(depth).type.name;
    if (name === 'orderedList' || name === 'bulletList') names.push(name);
  }
  return names.reverse();
}

export const circleBullet = BulletList.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      marker: {
        default: null,
        parseHTML: element => element.getAttribute('data-marker'),
        renderHTML: attributes => (attributes.marker ? { 'data-marker': attributes.marker } : {}),
      },
    };
  },
});

function innermostBullet(editor: Editor) {
  const $from = editor.state.selection.$from;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const node = $from.node(depth);
    if (node.type.name === 'bulletList') return node;
  }
  return null;
}

type BulletMarker = 'circle' | 'square' | 'hollow-square' | null;

function setBulletMarker(editor: Editor, marker: BulletMarker) {
  return editor.commands.command(({ tr, state }) => {
    const $from = state.selection.$from;
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      const node = $from.node(depth);
      if (node.type.name !== 'bulletList') continue;
      tr.setNodeMarkup(tr.mapping.map($from.before(depth)), undefined, { ...node.attrs, marker });
      return true;
    }
    return false;
  });
}

/** 顶层为连续编号；第一次 Tab 变为实心圆点，第二次 Tab 仅本行嵌套为空心圆点。 */
export function nestUnderNumber(editor: Editor) {
  const stack = listStack(editor);
  if (!stack.length) return false;
  const inner = stack[stack.length - 1];
  if (inner === 'orderedList' && stack.length === 1) {
    return editor.chain().sinkListItem('listItem').command(({ tr, state }) => {
      const $from = tr.selection.$from;
      for (let depth = $from.depth; depth > 0; depth -= 1) {
        if ($from.node(depth).type.name !== 'orderedList') continue;
        tr.setNodeMarkup($from.before(depth), state.schema.nodes.bulletList);
        return true;
      }
      return false;
    }).run();
  }
  if (inner === 'bulletList' && stack.filter(name => name === 'bulletList').length === 1) {
    if (innermostBullet(editor)?.attrs.marker === 'circle') return true;
    // 缩进当前 listItem，只给新嵌套的 ul 打 circle，勿改父级黑点列表
    return editor.chain().sinkListItem('listItem').command(({ tr }) => {
      const $from = tr.selection.$from;
      for (let depth = $from.depth; depth > 0; depth -= 1) {
        const node = $from.node(depth);
        if (node.type.name !== 'bulletList') continue;
        tr.setNodeMarkup($from.before(depth), undefined, { ...node.attrs, marker: 'circle' });
        return true;
      }
      return false;
    }).run();
  }
  return true;
}

function liftNestedList(editor: Editor) {
  const stack = listStack(editor);
  const bulletDepth = stack.filter(name => name === 'bulletList').length;
  // 旧数据：单层 ul 被整体标成 circle 时，Shift-Tab 还原为黑点
  if (bulletDepth === 1 && innermostBullet(editor)?.attrs.marker === 'circle') {
    return setBulletMarker(editor, null);
  }
  return editor.commands.liftListItem('listItem');
}

export const nestedListTab = Extension.create({
  name: 'nestedListTab',
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      Tab: () => nestUnderNumber(this.editor),
      'Shift-Tab': () => liftNestedList(this.editor),
    };
  },
});

/** PDF 笔记的无序列表限定为四层：实心圆、空心圆、实心方块、空心方块。 */
export function nestFourLevelBullet(editor: Editor) {
  const stack = listStack(editor);
  if (!stack.length) return false;
  if (stack[stack.length - 1] !== 'bulletList') return editor.commands.sinkListItem('listItem');

  const bulletDepth = stack.filter(name => name === 'bulletList').length;
  if (bulletDepth >= 4) return true;
  const nextMarker: Exclude<BulletMarker, null> = ['circle', 'square', 'hollow-square'][bulletDepth - 1] as Exclude<BulletMarker, null>;

  return editor.chain().sinkListItem('listItem').command(({ tr }) => {
    const $from = tr.selection.$from;
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      const node = $from.node(depth);
      if (node.type.name !== 'bulletList') continue;
      tr.setNodeMarkup($from.before(depth), undefined, { ...node.attrs, marker: nextMarker });
      return true;
    }
    return false;
  }).run();
}

export const fourLevelBulletTab = Extension.create({
  name: 'fourLevelBulletTab',
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      Tab: () => nestFourLevelBullet(this.editor),
      'Shift-Tab': () => this.editor.commands.liftListItem('listItem'),
    };
  },
});

function nestedListItem(node: { type: { name: string } }) {
  return node.type.name === 'bulletList' || node.type.name === 'orderedList';
}

function setListCollapsed(editor: Editor, collapsed: boolean) {
  return editor.commands.command(({ state, tr }) => {
    const $from = state.selection.$from;
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      const node = $from.node(depth);
      if (node.type.name !== 'listItem' || !node.content.content.some(nestedListItem)) continue;
      tr.setNodeMarkup($from.before(depth), undefined, { ...node.attrs, collapsed });
      return true;
    }
    return false;
  });
}

/** 含子列表的项目可折叠；状态保存在 listItem JSON 属性中。 */
export const collapsibleListItem = ListItem.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      collapsed: {
        default: false,
        parseHTML: element => element.getAttribute('data-collapsed') === 'true',
        renderHTML: attributes => attributes.collapsed ? { 'data-collapsed': 'true' } : {},
      },
    };
  },
  addKeyboardShortcuts() {
    return {
      'Alt-ArrowLeft': () => setListCollapsed(this.editor, true),
      'Alt-ArrowRight': () => setListCollapsed(this.editor, false),
    };
  },
  addProseMirrorPlugins() {
    return [new Plugin({
      props: {
        decorations: state => {
          const widgets: Decoration[] = [];
          state.doc.descendants((node, pos) => {
            if (node.type.name !== 'listItem' || !node.content.content.some(nestedListItem)) return;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'pdf-list-fold-toggle';
            const collapsed = Boolean(node.attrs.collapsed);
            button.setAttribute('aria-label', collapsed ? '展开子列表' : '折叠子列表');
            button.title = collapsed ? '展开子列表' : '折叠子列表';
            button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            button.dataset.listFoldPos = String(pos);
            widgets.push(Decoration.widget(pos + 1, button, { side: -1, key: `list-fold-${pos}-${collapsed}` }));
          });
          return DecorationSet.create(state.doc, widgets);
        },
        handleDOMEvents: {
          mousedown: (_view, event) => Boolean((event.target as HTMLElement).closest('.pdf-list-fold-toggle')),
          click: (view, event) => {
            const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.pdf-list-fold-toggle');
            if (!button) return false;
            const pos = Number(button.dataset.listFoldPos);
            const node = Number.isInteger(pos) ? view.state.doc.nodeAt(pos) : null;
            if (!node) return true;
            event.preventDefault();
            view.dispatch(view.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, collapsed: !node.attrs.collapsed }));
            return true;
          },
        },
      },
    })];
  },
});
