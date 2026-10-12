import { Editor, Mark } from '@tiptap/core';
import { createIcons, icons } from 'lucide';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { download, type EditorAdapter } from '../../context/types';
import { circleBullet, listStack, nestedListTab, nestUnderNumber } from '../rich-text-lists';
import { formattedInsertHtml } from './markdown';
import { openDigestJobDialog, type DigestJob } from './digest-job-dialog';
import { bindSkillMenu, type DocumentSkill, type SkillType } from './skill-menu';

const FALLBACK = { type: 'doc', content: [{ type: 'paragraph' }] };

const smallText = Mark.create({
  name: 'small',
  parseHTML: () => [{ tag: 'span.doc-small' }],
  renderHTML: () => ['span', { class: 'doc-small' }, 0],
});

const documentHighlight = Mark.create({
  name: 'highlight',
  parseHTML: () => [{ tag: 'mark.document-highlight' }],
  renderHTML: () => ['mark', { class: 'document-highlight' }, 0],
});

const documentComment = Mark.create({
  name: 'comment',
  addAttributes() {
    return {
      id: { default: null },
      text: { default: '' },
    };
  },
  parseHTML: () => [{
    tag: 'span.document-comment[data-comment-id]',
    getAttrs: element => ({
      id: (element as HTMLElement).dataset.commentId || null,
      text: (element as HTMLElement).dataset.commentText || '',
    }),
  }],
  renderHTML: ({ HTMLAttributes }) => ['span', {
    class: 'document-comment',
    'data-comment-id': HTMLAttributes.id,
    'data-comment-text': HTMLAttributes.text,
    title: '批注',
  }, 0],
});

export type DocumentSkillHooks = {
  types: () => Promise<SkillType[]>;
  list: (documentId: string) => Promise<DocumentSkill[]>;
  save: (documentId: string, skill: { id?: string; name: string; type: string; params: Record<string, string> }) => Promise<DocumentSkill>;
  remove: (documentId: string, skillId: string) => Promise<void>;
  run: (documentId: string, skillId: string, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  reloadDocument: (documentId: string) => Promise<void>;
  searchSecurities: (query: string) => Promise<{ items: { ticker_code: string; name: string }[] }>;
};

export type DocumentDigestHooks = {
  getJob: (documentId: string) => Promise<DigestJob>;
  saveJob: (documentId: string, job: DigestJob) => Promise<DigestJob>;
  runJob: (documentId: string, onEvent: (event: Record<string, any>) => void) => Promise<void>;
  reloadDocument: (documentId: string) => Promise<void>;
};

export function mountDocument(
  container: HTMLElement,
  changed: () => void,
  onDirty: (content: unknown) => void,
  digest?: DocumentDigestHooks,
  skills?: DocumentSkillHooks,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  const editor = new Editor({
    element: container, extensions: [
      StarterKit.configure({ bulletList: false }),
      Link.configure({ openOnClick: true, autolink: true, defaultProtocol: 'https', HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' } }),
      smallText,
      documentHighlight,
      documentComment,
      circleBullet,
      nestedListTab,
    ], content: FALLBACK,
    onSelectionUpdate: changed,
    onUpdate() {
      if (loading || !assetId) return;
      revision++;
      onDirty(editor.getJSON());
      changed();
    },
  });
  const currentComment = () => editor.getAttributes('comment') as { id?: string; text?: string };
  const openCommentDialog = (existing?: { id?: string; text?: string }) => {
    if (editor.state.selection.empty) {
      window.dispatchEvent(new CustomEvent('office-error', { detail: '请先选择需要批注的文字。' }));
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay document-comment-overlay';
    const dialog = document.createElement('form');
    dialog.className = 'document-comment-dialog';
    dialog.innerHTML = `
      <h2>批注</h2>
      <textarea maxlength="2000" placeholder="输入批注内容" aria-label="批注内容"></textarea>
      <div class="dialog-actions">
        <button type="button" data-remove ${existing?.id ? '' : 'hidden'}>删除批注</button>
        <button type="button" data-cancel>取消</button>
        <button type="submit" class="primary">保存</button>
      </div>`;
    const input = dialog.querySelector<HTMLTextAreaElement>('textarea')!;
    input.value = existing?.text || '';
    const close = () => overlay.remove();
    dialog.querySelector<HTMLButtonElement>('[data-cancel]')!.onclick = close;
    dialog.querySelector<HTMLButtonElement>('[data-remove]')!.onclick = () => {
      editor.chain().focus().unsetMark('comment').run();
      close();
    };
    dialog.onsubmit = event => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) {
        input.setCustomValidity('请输入批注内容。');
        input.reportValidity();
        return;
      }
      editor.chain().focus().setMark('comment', {
        id: existing?.id || `comment-${crypto.randomUUID()}`,
        text,
      }).run();
      close();
    };
    overlay.onclick = event => { if (event.target === overlay) close(); };
    overlay.append(dialog);
    document.body.append(overlay);
    input.focus();
  };
  editor.view.dom.addEventListener('click', event => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('.document-comment[data-comment-id]');
    if (!target) return;
    window.setTimeout(() => {
      editor.chain().focus().extendMarkRange('comment').run();
      openCommentDialog({ id: target.dataset.commentId, text: target.dataset.commentText });
    });
  });
  const toolbar = container.parentElement!.querySelector<HTMLElement>('.format-bar');
  if (toolbar && !toolbar.querySelector('[data-format="comment"]')) {
    const commentButton = document.createElement('button');
    commentButton.type = 'button';
    commentButton.dataset.format = 'comment';
    commentButton.title = '添加或编辑批注';
    commentButton.setAttribute('aria-label', '批注');
    commentButton.innerHTML = '<i data-lucide="message-square-plus"></i>';
    toolbar.querySelector('[data-format="highlight"]')?.after(commentButton);
    createIcons({ icons });
  }
  container.parentElement!.querySelectorAll<HTMLButtonElement>('[data-format]').forEach(button => {
    button.addEventListener('mousedown', e => e.preventDefault());
    button.onclick = () => {
      const chain = editor.chain().focus();
      const format = button.dataset.format;
      if (format === 'bold') chain.toggleBold().run();
      if (format === 'italic') chain.toggleItalic().run();
      if (format === 'strike') chain.toggleStrike().run();
      if (format === 'highlight') chain.toggleMark('highlight').run();
      if (format === 'comment') {
        const comment = currentComment();
        openCommentDialog(comment.id ? comment : undefined);
      }
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
  const skillBtn = container.parentElement!.querySelector<HTMLButtonElement>('[data-my-skills]');
  if (skillBtn && skills) {
    bindSkillMenu(skillBtn, {
      types: () => skills.types(),
      list: () => {
        if (!assetId) throw new Error('请先打开一篇文档。');
        return skills.list(assetId);
      },
      save: body => {
        if (!assetId) throw new Error('请先打开一篇文档。');
        return skills.save(assetId, body);
      },
      remove: id => {
        if (!assetId) throw new Error('请先打开一篇文档。');
        return skills.remove(assetId, id);
      },
      run: (id, onEvent) => {
        if (!assetId) throw new Error('请先打开一篇文档。');
        return skills.run(assetId, id, onEvent);
      },
      reloadDocument: async (nextId?: string) => {
        const target = nextId || assetId;
        if (!target) return;
        await skills.reloadDocument(target);
      },
      searchSecurities: query => skills.searchSecurities(query),
    });
  }
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
        run: onEvent => digest.runJob(assetId, onEvent),
        reloadDocument: async (nextId?: string) => {
          await digest.reloadDocument(nextId || assetId);
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
