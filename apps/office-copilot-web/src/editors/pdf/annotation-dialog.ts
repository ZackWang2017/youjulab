import { Editor } from '@tiptap/core';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { createIcons, icons } from 'lucide';
import { circleBullet, fourLevelBulletTab } from '../rich-text-lists';

const RICH_NOTE_PREFIX = '[[office-pdf-note:v1]]';

type NoteResult = { action: 'deleted' | 'closed' };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function plainNote(content: string) {
  const lines = content.replace(/\r\n?/g, '\n').split('\n');
  return { type: 'doc', content: lines.map(text => ({ type: 'paragraph', content: text ? [{ type: 'text', text }] : [] })) };
}

function noteDocument(content: string) {
  if (!content.startsWith(RICH_NOTE_PREFIX)) return plainNote(content);
  try {
    const value = JSON.parse(content.slice(RICH_NOTE_PREFIX.length));
    return isRecord(value) && value.type === 'doc' ? value : plainNote(content);
  } catch {
    return plainNote(content);
  }
}

function textFromNode(node: unknown): string {
  if (!isRecord(node)) return '';
  const own = typeof node.text === 'string' ? node.text : '';
  const children = Array.isArray(node.content) ? node.content.map(textFromNode).join('') : '';
  const separator = ['paragraph', 'heading', 'listItem', 'blockquote'].includes(String(node.type)) ? '\n' : '';
  return `${own}${children}${separator}`;
}

export function pdfAnnotationPreview(content: string, limit = 240) {
  const text = textFromNode(noteDocument(content || '')).replace(/\n{2,}/g, '\n').trim();
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}...` : text;
}

function serializeNote(editor: Editor) {
  return `${RICH_NOTE_PREFIX}${JSON.stringify(editor.getJSON())}`;
}

export function openPdfAnnotationDialog(args: {
  content?: string;
  editing?: boolean;
  isNew?: boolean;
  onSave?: (content: string) => Promise<void>;
}) {
  return new Promise<NoteResult>(resolve => {
    let original = args.content || '';
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay pdf-annotation-overlay';
    overlay.innerHTML = `
      <section class="pdf-annotation-dialog" role="dialog" aria-modal="true" aria-labelledby="pdf-annotation-title">
        <header class="pdf-annotation-head"><div><i data-lucide="message-square-text"></i><h2 id="pdf-annotation-title">批注</h2></div><button type="button" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></header>
        <div class="pdf-annotation-toolbar" role="toolbar" aria-label="批注格式" hidden>
          <button type="button" data-command="bold" title="加粗" aria-label="加粗"><i data-lucide="bold"></i></button><button type="button" data-command="italic" title="斜体" aria-label="斜体"><i data-lucide="italic"></i></button><button type="button" data-command="heading" title="标题" aria-label="标题"><i data-lucide="heading-2"></i></button><button type="button" data-command="bullet" title="无序列表" aria-label="无序列表"><i data-lucide="list"></i></button><button type="button" data-command="ordered" title="编号列表" aria-label="编号列表"><i data-lucide="list-ordered"></i></button><button type="button" data-command="quote" title="引用" aria-label="引用"><i data-lucide="quote"></i></button><span aria-hidden="true"></span><button type="button" data-command="undo" title="撤销" aria-label="撤销"><i data-lucide="undo-2"></i></button><button type="button" data-command="redo" title="重做" aria-label="重做"><i data-lucide="redo-2"></i></button>
        </div>
        <div class="pdf-annotation-error" data-error role="alert" hidden></div>
        <div class="pdf-annotation-editor" data-editor></div>
        <footer class="pdf-annotation-actions"><span data-mode></span><div><button type="button" class="pdf-annotation-delete" data-delete ${args.isNew ? 'hidden' : ''}><i data-lucide="trash-2"></i><span>删除</span></button><button type="button" data-cancel>关闭</button><button type="button" class="primary" data-edit><i data-lucide="pencil"></i><span>修改</span></button><button type="button" class="primary" data-save hidden><i data-lucide="save"></i><span>保存</span></button></div></footer>
      </section>`;
    const dialog = overlay.querySelector<HTMLElement>('.pdf-annotation-dialog')!;
    const editorHost = overlay.querySelector<HTMLElement>('[data-editor]')!;
    const toolbar = overlay.querySelector<HTMLElement>('.pdf-annotation-toolbar')!;
    const mode = overlay.querySelector<HTMLElement>('[data-mode]')!;
    const error = overlay.querySelector<HTMLElement>('[data-error]')!;
    const editButton = overlay.querySelector<HTMLButtonElement>('[data-edit]')!;
    const saveButton = overlay.querySelector<HTMLButtonElement>('[data-save]')!;
    const cancelButton = overlay.querySelector<HTMLButtonElement>('[data-cancel]')!;
    let editing = Boolean(args.editing || args.isNew);
    let dirty = false;
    let saving = false;
    let settled = false;
    const editor = new Editor({
      element: editorHost,
      extensions: [StarterKit.configure({ bulletList: false }), Link.configure({ openOnClick: !editing, autolink: true, defaultProtocol: 'https' }), circleBullet, fourLevelBulletTab],
      content: noteDocument(original), editable: editing, onUpdate: () => { dirty = true; },
    });
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || settled) return;
      event.preventDefault();
      close();
    };
    const finish = (result: NoteResult) => {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKeydown);
      editor.destroy();
      overlay.remove();
      resolve(result);
    };
    const sync = () => {
      dialog.classList.toggle('is-editing', editing);
      toolbar.hidden = !editing;
      mode.textContent = editing ? '编辑中' : '只读';
      editButton.hidden = editing;
      saveButton.hidden = !editing;
      cancelButton.textContent = editing ? '取消修改' : '关闭';
      editor.setEditable(editing);
      saveButton.disabled = saving;
      if (editing) requestAnimationFrame(() => editor.commands.focus('end'));
    };
    const close = () => {
      if (editing && dirty && !window.confirm('放弃尚未保存的批注修改？')) return;
      finish({ action: 'closed' });
    };
    const runCommand = (command: string) => {
      const chain = editor.chain().focus();
      if (command === 'bold') chain.toggleBold().run();
      if (command === 'italic') chain.toggleItalic().run();
      if (command === 'heading') chain.toggleHeading({ level: 2 }).run();
      if (command === 'bullet') chain.toggleBulletList().run();
      if (command === 'ordered') chain.toggleOrderedList().run();
      if (command === 'quote') chain.toggleBlockquote().run();
      if (command === 'undo') chain.undo().run();
      if (command === 'redo') chain.redo().run();
    };
    overlay.querySelectorAll<HTMLButtonElement>('[data-command]').forEach(button => {
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => runCommand(button.dataset.command || ''));
    });
    editButton.addEventListener('click', () => { editing = true; sync(); });
    saveButton.addEventListener('click', async () => {
      if (!editor.getText().trim()) { editorHost.classList.add('has-error'); editor.commands.focus(); return; }
      const content = serializeNote(editor);
      if (!args.onSave) { finish({ action: 'closed' }); return; }
      saving = true;
      error.hidden = true;
      sync();
      try {
        await args.onSave(content);
        original = content;
        dirty = false;
        editing = false;
        editorHost.classList.remove('has-error');
        sync();
      } catch (err) {
        error.textContent = `保存失败：${err instanceof Error ? err.message : String(err)}`;
        error.hidden = false;
      } finally {
        saving = false;
        sync();
      }
    });
    cancelButton.addEventListener('click', () => {
      if (!editing) { finish({ action: 'closed' }); return; }
      if (dirty && !window.confirm('放弃尚未保存的批注修改？')) return;
      editor.commands.setContent(noteDocument(original));
      dirty = false;
      editing = false;
      sync();
    });
    overlay.querySelector<HTMLButtonElement>('[data-delete]')!.addEventListener('click', () => finish({ action: 'deleted' }));
    overlay.querySelector<HTMLButtonElement>('[data-close]')!.addEventListener('click', close);
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    document.addEventListener('keydown', onKeydown);
    document.body.append(overlay);
    createIcons({ icons });
    sync();
  });
}
