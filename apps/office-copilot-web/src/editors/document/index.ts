import { Editor, Mark } from '@tiptap/core';
import { createIcons, icons } from 'lucide';
import Link from '@tiptap/extension-link';
import Paragraph from '@tiptap/extension-paragraph';
import StarterKit from '@tiptap/starter-kit';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { download, type EditorAdapter } from '../../context/types';
import { circleBullet, listStack, nestedListTab, nestUnderNumber } from '../rich-text-lists';
import { formattedInsertHtml } from './markdown';

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
      author: { default: '我' },
      createdAt: { default: null },
    };
  },
  parseHTML: () => [{
    tag: 'span.document-comment[data-comment-id]',
    getAttrs: element => ({
      id: (element as HTMLElement).dataset.commentId || null,
      text: (element as HTMLElement).dataset.commentText || '',
      author: (element as HTMLElement).dataset.commentAuthor || '我',
      createdAt: (element as HTMLElement).dataset.commentCreatedAt || null,
    }),
  }],
  renderHTML: ({ HTMLAttributes }) => ['span', {
    class: 'document-comment',
    'data-comment-id': HTMLAttributes.id,
    'data-comment-text': HTMLAttributes.text,
    'data-comment-author': HTMLAttributes.author || '我',
    'data-comment-created-at': HTMLAttributes.createdAt || '',
    title: '批注',
  }, 0],
});

const documentTextStyle = Mark.create({
  name: 'documentTextStyle',
  addAttributes() {
    return {
      fontFamily: { default: null },
      fontSize: { default: null },
    };
  },
  parseHTML: () => [{
    tag: 'span',
    getAttrs: element => {
      const style = (element as HTMLElement).style;
      return style.fontFamily || style.fontSize
        ? { fontFamily: style.fontFamily || null, fontSize: style.fontSize || null }
        : false;
    },
  }],
  renderHTML: ({ HTMLAttributes }) => {
    const styles = [
      HTMLAttributes.fontFamily && `font-family:${HTMLAttributes.fontFamily}`,
      HTMLAttributes.fontSize && `font-size:${HTMLAttributes.fontSize}`,
    ].filter(Boolean).join(';');
    return ['span', styles ? { style: styles } : {}, 0];
  },
});

const indentedParagraph = Paragraph.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      textIndent: {
        default: null,
        parseHTML: element => (element as HTMLElement).style.textIndent || null,
        renderHTML: attributes => attributes.textIndent ? { style: `text-indent:${attributes.textIndent}` } : {},
      },
    };
  },
});

export function mountDocument(
  container: HTMLElement,
  changed: () => void,
  onDirty: (content: unknown) => void,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  let fontFamilyControl: HTMLSelectElement | null = null;
  let fontSizeControl: HTMLSelectElement | null = null;
  const syncTextStyleControls = () => {
    const attributes = editor.getAttributes('documentTextStyle') as { fontFamily?: string | null; fontSize?: string | null };
    if (fontFamilyControl) fontFamilyControl.value = attributes.fontFamily || '';
    if (fontSizeControl) fontSizeControl.value = attributes.fontSize || '';
  };
  const editor = new Editor({
    element: container, extensions: [
      StarterKit.configure({ bulletList: false, paragraph: false }),
      indentedParagraph,
      Link.configure({ openOnClick: true, autolink: true, defaultProtocol: 'https', HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' } }),
      smallText,
      documentHighlight,
      documentComment,
      documentTextStyle,
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader,
      TableCell,
      circleBullet,
      nestedListTab,
    ], content: FALLBACK,
    onSelectionUpdate() {
      changed();
      syncTextStyleControls();
    },
    onUpdate() {
      if (loading || !assetId) return;
      revision++;
      onDirty(editor.getJSON());
      changed();
    },
  });
  type CommentData = { id?: string; text?: string; author?: string; createdAt?: string | null };
  type CommentRange = { from: number; to: number };
  let commentPanel: HTMLElement | null = null;
  const currentComment = () => editor.getAttributes('comment') as CommentData;
  const commentExcerpt = (range: CommentRange) => editor.state.doc.textBetween(range.from, range.to, ' ').replace(/\s+/g, ' ').trim();
  const commentTime = (value?: string | null) => {
    const time = value ? new Date(value) : null;
    if (!time || Number.isNaN(time.getTime())) return '刚刚';
    return `${time.getFullYear()}-${String(time.getMonth() + 1).padStart(2, '0')}-${String(time.getDate()).padStart(2, '0')}`;
  };
  const openCommentDialog = (existing?: CommentData, anchor?: HTMLElement) => {
    if (editor.state.selection.empty) {
      window.dispatchEvent(new CustomEvent('office-error', { detail: '请先选择需要批注的文字。' }));
      return;
    }
    commentPanel?.remove();
    const range: CommentRange = { from: editor.state.selection.from, to: editor.state.selection.to };
    const panel = document.createElement('aside');
    panel.className = 'document-comment-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', existing?.id ? '文档批注' : '添加批注');
    const close = () => {
      panel.remove();
      if (commentPanel === panel) commentPanel = null;
      window.removeEventListener('resize', position);
    };
    const position = () => {
      const panelWidth = panel.offsetWidth || 340;
      const rect = anchor?.getBoundingClientRect() || editor.view.coordsAtPos(range.to);
      const rightSpace = window.innerWidth - rect.right;
      const left = rightSpace >= panelWidth + 20
        ? rect.right + 12
        : Math.max(12, rect.left - panelWidth - 12);
      panel.style.left = `${Math.min(window.innerWidth - panelWidth - 12, left)}px`;
      panel.style.top = `${Math.max(12, Math.min(window.innerHeight - panel.offsetHeight - 12, rect.top - 8))}px`;
    };
    const preserveRange = () => editor.chain().focus().setTextSelection(range);
    const makeButton = (label: string, className = '') => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = className;
      button.textContent = label;
      button.addEventListener('mousedown', event => event.preventDefault());
      return button;
    };
    const header = () => {
      const head = document.createElement('header');
      head.className = 'document-comment-head';
      const identity = document.createElement('div');
      identity.className = 'document-comment-author';
      const avatar = document.createElement('span');
      avatar.className = 'document-comment-avatar';
      avatar.textContent = (existing?.author || '我').slice(0, 1);
      const meta = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = existing?.author || '我';
      const time = document.createElement('time');
      time.textContent = commentTime(existing?.createdAt);
      meta.append(name, time);
      identity.append(avatar, meta);
      const dismiss = makeButton('×', 'document-comment-close');
      dismiss.title = '关闭批注';
      dismiss.setAttribute('aria-label', '关闭批注');
      dismiss.onclick = close;
      head.append(identity, dismiss);
      return head;
    };
    const quote = () => {
      const reference = document.createElement('blockquote');
      reference.className = 'document-comment-reference';
      reference.textContent = commentExcerpt(range) || '所选文字';
      return reference;
    };
    const showRead = () => {
      panel.replaceChildren(header(), quote());
      const body = document.createElement('div');
      body.className = 'document-comment-body';
      body.textContent = existing?.text || '';
      const actions = document.createElement('footer');
      actions.className = 'document-comment-actions';
      const remove = makeButton('删除', 'document-comment-delete');
      remove.onclick = () => {
        preserveRange().unsetMark('comment').run();
        close();
      };
      const edit = makeButton('编辑', 'primary');
      edit.onclick = showEdit;
      actions.append(remove, edit);
      panel.append(body, actions);
      position();
    };
    const showEdit = () => {
      panel.replaceChildren(header(), quote());
      const input = document.createElement('textarea');
      input.className = 'document-comment-input';
      input.maxLength = 2000;
      input.placeholder = '写下你的批注';
      input.setAttribute('aria-label', '批注内容');
      input.value = existing?.text || '';
      const actions = document.createElement('footer');
      actions.className = 'document-comment-actions';
      const cancel = makeButton(existing?.id ? '取消' : '放弃');
      cancel.onclick = () => existing?.id ? showRead() : close();
      const save = makeButton('保存', 'primary');
      save.onclick = () => {
        const text = input.value.trim();
        if (!text) {
          input.setCustomValidity('请输入批注内容。');
          input.reportValidity();
          return;
        }
        const next = {
          id: existing?.id || `comment-${crypto.randomUUID()}`,
          text,
          author: existing?.author || '我',
          createdAt: existing?.createdAt || new Date().toISOString(),
        };
        preserveRange().setMark('comment', next).run();
        existing = next;
        showRead();
      };
      actions.append(cancel, save);
      panel.append(input, actions);
      position();
      input.focus();
    };
    document.body.append(panel);
    commentPanel = panel;
    window.addEventListener('resize', position);
    if (existing?.id) showRead();
    else showEdit();
  };
  editor.view.dom.addEventListener('click', event => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('.document-comment[data-comment-id]');
    if (!target) return;
    window.setTimeout(() => {
      editor.chain().focus().extendMarkRange('comment').run();
      openCommentDialog({
        id: target.dataset.commentId,
        text: target.dataset.commentText,
        author: target.dataset.commentAuthor || '我',
        createdAt: target.dataset.commentCreatedAt || null,
      }, target);
    });
  });
  const openTableDialog = () => {
    const inTable = editor.isActive('table');
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay document-table-overlay';
    const dialog = document.createElement('form');
    dialog.className = 'document-table-dialog';
    dialog.innerHTML = inTable ? `
      <h2>编辑表格</h2>
      <div class="document-table-actions">
        <button type="button" data-table-action="addRowBefore">上方插入行</button>
        <button type="button" data-table-action="addRowAfter">下方插入行</button>
        <button type="button" data-table-action="deleteRow">删除当前行</button>
        <button type="button" data-table-action="addColumnBefore">左侧插入列</button>
        <button type="button" data-table-action="addColumnAfter">右侧插入列</button>
        <button type="button" data-table-action="deleteColumn">删除当前列</button>
      </div>
      <div class="dialog-actions">
        <button type="button" data-delete-table>删除表格</button>
        <button type="button" data-cancel>关闭</button>
      </div>` : `
      <h2>插入投研表格</h2>
      <p>默认首行为表头，末行为汇总行。</p>
      <label>行数<input name="rows" type="number" min="2" max="30" value="4" required></label>
      <label>列数<input name="cols" type="number" min="1" max="12" value="3" required></label>
      <div class="dialog-actions">
        <button type="button" data-cancel>取消</button>
        <button type="submit" class="primary">插入表格</button>
      </div>`;
    const close = () => overlay.remove();
    dialog.querySelector<HTMLButtonElement>('[data-cancel]')!.onclick = close;
    dialog.querySelector<HTMLButtonElement>('[data-delete-table]')?.addEventListener('click', () => {
      editor.chain().focus().deleteTable().run();
      close();
    });
    dialog.querySelectorAll<HTMLButtonElement>('[data-table-action]').forEach(button => {
      button.addEventListener('click', () => {
        const action = button.dataset.tableAction as 'addRowBefore' | 'addRowAfter' | 'deleteRow' | 'addColumnBefore' | 'addColumnAfter' | 'deleteColumn';
        editor.chain().focus()[action]().run();
      });
    });
    dialog.onsubmit = event => {
      event.preventDefault();
      const data = new FormData(dialog);
      const rows = Number(data.get('rows'));
      const cols = Number(data.get('cols'));
      if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 2 || rows > 30 || cols < 1 || cols > 12) return;
      editor.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
      close();
    };
    overlay.onclick = event => { if (event.target === overlay) close(); };
    overlay.append(dialog);
    document.body.append(overlay);
  };
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
  if (toolbar && !toolbar.querySelector('[data-format="table"]')) {
    const tableButton = document.createElement('button');
    tableButton.type = 'button';
    tableButton.dataset.format = 'table';
    tableButton.title = '插入或编辑表格';
    tableButton.setAttribute('aria-label', '表格');
    tableButton.innerHTML = '<i data-lucide="table-2"></i>';
    toolbar.querySelector('[data-format="rule"]')?.after(tableButton);
    createIcons({ icons });
  }
  if (toolbar && !toolbar.querySelector('[data-text-style]')) {
    const createStyleSelect = (kind: 'fontFamily' | 'fontSize', label: string, options: Array<[string, string]>) => {
      const select = document.createElement('select');
      select.className = 'document-text-style';
      select.dataset.textStyle = kind;
      select.title = label;
      select.setAttribute('aria-label', label);
      for (const [value, text] of options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        select.append(option);
      }
      select.addEventListener('change', () => {
        const current = editor.getAttributes('documentTextStyle') as { fontFamily?: string | null; fontSize?: string | null };
        const next = { ...current, [kind]: select.value || null };
        const chain = editor.chain().focus();
        if (next.fontFamily || next.fontSize) chain.setMark('documentTextStyle', next).run();
        else chain.unsetMark('documentTextStyle').run();
      });
      return select;
    };
    const fontFamily = createStyleSelect('fontFamily', '字体', [
      ['', '字体'],
      ['"PingFang SC","Microsoft YaHei",sans-serif', '无衬线'],
      ['SimSun,STSong,serif', '宋体'],
      ['SimHei,"Microsoft YaHei",sans-serif', '黑体'],
      ['Arial,sans-serif', 'Arial'],
      ['"Times New Roman",serif', 'Times New Roman'],
    ]);
    const fontSize = createStyleSelect('fontSize', '字号', [
      ['', '字号'],
      ['12px', '12'],
      ['14px', '14'],
      ['16px', '16'],
      ['18px', '18'],
      ['20px', '20'],
      ['24px', '24'],
      ['28px', '28'],
    ]);
    const indentText = document.createElement('button');
    indentText.type = 'button';
    indentText.dataset.format = 'indent-text';
    indentText.title = '缩进单元格文本';
    indentText.setAttribute('aria-label', '缩进文本');
    indentText.innerHTML = '<i data-lucide="indent-increase"></i>';
    fontFamilyControl = fontFamily;
    fontSizeControl = fontSize;
    toolbar.querySelector('.format-sep')?.before(fontFamily, fontSize, indentText);
    createIcons({ icons });
    syncTextStyleControls();
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
      if (format === 'table') openTableDialog();
      if (format === 'indent-text') {
        if (!editor.isActive('table')) {
          window.dispatchEvent(new CustomEvent('office-error', { detail: '请将光标放在表格单元格内，再使用缩进文本。' }));
          return;
        }
        const paragraph = editor.getAttributes('paragraph') as { textIndent?: string | null };
        chain.updateAttributes('paragraph', { textIndent: paragraph.textIndent ? null : '1em' }).run();
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
