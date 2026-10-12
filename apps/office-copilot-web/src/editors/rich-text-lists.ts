import { Editor, Extension } from '@tiptap/core';
import { BulletList } from '@tiptap/extension-list';

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

function setBulletMarker(editor: Editor, marker: 'circle' | null) {
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
