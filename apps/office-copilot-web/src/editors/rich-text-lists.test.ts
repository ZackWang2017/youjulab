import { getExtensionField, getSchema } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { splitListItem } from '@tiptap/pm/schema-list';
import { EditorState, TextSelection } from '@tiptap/pm/state';
import { describe, expect, it, vi } from 'vitest';
import { circleBullet, collapsibleListItem, pdfIndentedParagraph, pdfNotebookTab } from './rich-text-lists';

const schema = getSchema([
  StarterKit.configure({ bulletList: false, listItem: false, paragraph: false }),
  circleBullet, collapsibleListItem, pdfIndentedParagraph, pdfNotebookTab,
]);

function paragraph(text: string) {
  return { type: 'paragraph', content: [{ type: 'text', text }] };
}

function enterShortcuts(editor: unknown) {
  return getExtensionField(collapsibleListItem, 'addKeyboardShortcuts', {
    name: 'listItem', editor,
  } as any)!.call({});
}

describe('PDF notebook list Enter', () => {
  it.each(['bulletList', 'orderedList'])('continues a %s with a sibling item', type => {
    const doc = schema.nodeFromJSON({ type: 'doc', content: [{
      type, content: [{ type: 'listItem', content: [paragraph('第一项')] }],
    }] });
    let state = EditorState.create({ doc, selection: TextSelection.create(doc, 6) });
    const split = vi.fn(() => splitListItem(schema.nodes.listItem)(state, tr => { state = state.apply(tr); }));
    const shortcuts = enterShortcuts({ commands: { splitListItem: split } });

    expect(shortcuts.Enter()).toBe(true);
    expect(split).toHaveBeenCalledWith('listItem');
    expect(state.doc.firstChild!.type.name).toBe(type);
    expect(state.doc.firstChild!.childCount).toBe(2);
    expect(state.doc.firstChild!.lastChild!.textContent).toBe('');
    expect(state.selection.$from.parent.type.name).toBe('paragraph');
  });

  it.each(['circle', 'square', 'hollow-square'])('keeps nested %s items at the same level', marker => {
    const doc = schema.nodeFromJSON({ type: 'doc', content: [{
      type: 'orderedList', content: [{ type: 'listItem', content: [
        paragraph('父项'),
        { type: 'bulletList', attrs: { marker }, content: [{ type: 'listItem', content: [paragraph('子项')] }] },
      ] }],
    }] });
    let end = 0;
    doc.descendants((node, pos) => { if (node.isText && node.text === '子项') end = pos + node.nodeSize; });
    let state = EditorState.create({ doc, selection: TextSelection.create(doc, end) });
    const shortcuts = enterShortcuts({ commands: {
      splitListItem: () => splitListItem(schema.nodes.listItem)(state, tr => { state = state.apply(tr); }),
    } });
    expect(shortcuts.Enter()).toBe(true);
    const nested = state.doc.firstChild!.firstChild!.child(1);
    expect(state.doc.firstChild!.childCount).toBe(1);
    expect(nested.childCount).toBe(2);
    expect(nested.attrs.marker).toBe(marker);
  });

  it('preserves native editing shortcuts along with folding shortcuts', () => {
    const shortcuts = enterShortcuts({});
    for (const key of ['Enter', 'Tab', 'Shift-Tab', 'Alt-ArrowLeft', 'Alt-ArrowRight']) {
      expect(shortcuts[key]).toBeTypeOf('function');
    }
  });
});
