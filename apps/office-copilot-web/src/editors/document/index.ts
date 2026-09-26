import { Editor, Extension } from '@tiptap/core';
import { BulletList } from '@tiptap/extension-list';
import StarterKit from '@tiptap/starter-kit';
import { download, type EditorAdapter } from '../../context/types';
import { formattedInsertHtml } from './markdown';
import { openDigestJobDialog, type DigestJob } from './digest-job-dialog';

const FALLBACK = { type: 'doc', content: [{ type: 'paragraph' }] };

function listStack(editor: Editor) {
  const names: string[] = [];
  const $from = editor.state.selection.$from;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const name = $from.node(depth).type.name;
    if (name === 'orderedList' || name === 'bulletList') names.push(name);
  }
  return names.reverse();
}

const circleBullet = BulletList.extend({
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
    if (node.type.name === 'bulletList') return { depth, node };
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

/** 编号为第一级；Tab 变成实心圆点，再 Tab 变成空心圆。 */
function nestUnderNumber(editor: Editor) {
  const stack = listStack(editor);
  if (!stack.length) return false;
  const depth = stack.length;
  const inner = stack[depth - 1];
  if (inner === 'orderedList' && depth === 1) {
    return editor.chain().sinkListItem('listItem').command(({ tr, state }) => {
      const $from = tr.selection.$from;
      for (let d = $from.depth; d > 0; d -= 1) {
        if ($from.node(d).type.name !== 'orderedList') continue;
        tr.setNodeMarkup($from.before(d), state.schema.nodes.bulletList);
        return true;
      }
      return false;
    }).run();
  }
  if (inner === 'bulletList' && stack.filter(name => name === 'bulletList').length === 1) {
    if (innermostBullet(editor)?.node.attrs.marker === 'circle') return true;
    return setBulletMarker(editor, 'circle');
  }
  return true;
}

function liftNestedList(editor: Editor) {
  const bullet = innermostBullet(editor);
  if (bullet?.node.attrs.marker === 'circle') return setBulletMarker(editor, null);
  return editor.commands.liftListItem('listItem');
}

const nestedListTab = Extension.create({
  name: 'nestedListTab',
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      Tab: () => nestUnderNumber(this.editor),
      'Shift-Tab': () => liftNestedList(this.editor),
    };
  },
});

export type DocumentDigestHooks = {
  getJob: (documentId: string) => Promise<DigestJob>;
  saveJob: (documentId: string, job: DigestJob) => Promise<DigestJob>;
  runJob: (documentId: string) => Promise<{ title?: string; warnings?: string[] }>;
  reloadDocument: (documentId: string) => Promise<void>;
};

export function mountDocument(
  container: HTMLElement,
  changed: () => void,
  onDirty: (content: unknown) => void,
  digest?: DocumentDigestHooks,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  const editor = new Editor({
    element: container, extensions: [StarterKit.configure({ bulletList: false }), circleBullet, nestedListTab], content: FALLBACK,
    onSelectionUpdate: changed,
    onUpdate() {
      if (loading || !assetId) return;
      revision++;
      onDirty(editor.getJSON());
      changed();
    },
  });
  container.parentElement!.querySelectorAll<HTMLButtonElement>('[data-format]').forEach(button => {
    button.addEventListener('mousedown', e => e.preventDefault());
    button.onclick = () => {
      const chain = editor.chain().focus();
      const format = button.dataset.format;
      if (format === 'bold') chain.toggleBold().run();
      if (format === 'italic') chain.toggleItalic().run();
      if (format === 'strike') chain.toggleStrike().run();
      if (format === 'code') chain.toggleCode().run();
      if (format === 'h1') chain.toggleHeading({ level: 1 }).run();
      if (format === 'heading') chain.toggleHeading({ level: 2 }).run();
      if (format === 'h3') chain.toggleHeading({ level: 3 }).run();
      if (format === 'list') {
        if (listStack(editor).length) nestUnderNumber(editor);
        else chain.toggleOrderedList().run();
      }
      if (format === 'ordered') chain.toggleOrderedList().run();
      if (format === 'quote') chain.toggleBlockquote().run();
      if (format === 'codeblock') chain.toggleCodeBlock().run();
      if (format === 'rule') chain.setHorizontalRule().run();
    };
  });
  const digestBtn = container.parentElement!.querySelector<HTMLButtonElement>('[data-digest-job]');
  if (digestBtn && digest) {
    digestBtn.addEventListener('mousedown', e => e.preventDefault());
    digestBtn.onclick = () => {
      if (!assetId) {
        window.dispatchEvent(new CustomEvent('office-error', { detail: '请先打开一篇文档。' }));
        return;
      }
      void openDigestJobDialog({
        load: () => digest.getJob(assetId),
        save: job => digest.saveJob(assetId, job),
        run: () => digest.runJob(assetId),
        reloadDocument: async () => {
          await digest.reloadDocument(assetId);
        },
      }).catch(e => {
        window.dispatchEvent(new CustomEvent('office-error', { detail: e instanceof Error ? e.message : String(e) }));
      });
    };
  }
  return {
    load(asset) {
      loading = true;
      assetId = asset.id;
      revision = asset.revision;
      editor.commands.setContent(asset.content || FALLBACK);
      loading = false;
      changed();
    },
    context() {
      if (!assetId) return null;
      let { from, to } = editor.state.selection;
      const doc = editor.state.doc;
      if (from === 0 && to === doc.content.size && doc.childCount === 1 && doc.firstChild?.isTextblock) {
        from = 1; to = doc.content.size - 1;
      }
      const $from = doc.resolve(from), $to = doc.resolve(to);
      if (from === to) return null;
      return { document_id: assetId, surface: 'document', revision,
        selection: { from, to, text: editor.state.doc.textBetween(from, to, '\n'),
          single_block: $from.sameParent($to) && $from.parent.isTextblock },
        surrounding: editor.state.doc.textBetween(Math.max(0, from - 500), Math.min(editor.state.doc.content.size, to + 500), '\n'),
      };
    },
    anchor() {
      if (!assetId) return null;
      const pos = editor.state.selection.head;
      const $pos = editor.state.doc.resolve(pos);
      return { document_id: assetId, surface: 'document', revision,
        selection: { from: pos, to: pos, text: '', single_block: $pos.parent.isTextblock },
        surrounding: editor.state.doc.textBetween(Math.max(0, pos - 500), Math.min(editor.state.doc.content.size, pos + 500), '\n'),
      };
    },
    apply(patch) {
      if (patch.revision !== revision) throw new Error('文档已更改，请重新生成建议。');
      const { from, to, text } = patch.selection;
      if (from !== to && editor.state.doc.textBetween(from, to, '\n') !== text) throw new Error('选区内容已更改。');
      const replacement = String(patch.replacement ?? '');
      const rich = patch.task === 'generate' || !patch.selection.single_block
        || /^(#{1,3}\s|[-*]\s|\d+\.\s|```)/m.test(replacement);
      if (rich) {
        editor.chain().insertContentAt({ from, to }, formattedInsertHtml(replacement)).focus().run();
        return;
      }
      editor.view.dispatch(editor.state.tr.insertText(replacement, from, to === from ? from : to));
    },
    snapshot() { return editor.getJSON(); },
    undo() { editor.commands.undo(); },
    export() { download('document.tiptap.json', JSON.stringify(editor.getJSON(), null, 2)); },
  };
}
