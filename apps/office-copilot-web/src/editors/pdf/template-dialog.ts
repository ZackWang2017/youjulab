import { Editor } from '@tiptap/core';
import Link from '@tiptap/extension-link';
import StarterKit from '@tiptap/starter-kit';
import { createIcons, icons } from 'lucide';
import { circleBullet, collapsibleListItem, fourLevelBulletTab } from '../rich-text-lists';
import { stripOutlineReferences } from './outline';

export type OutlineTemplateMeta = { id: string; name: string };
export type OutlineTemplateRecord = OutlineTemplateMeta & { revision: number; doc: unknown };

const EMPTY_DOC = { type: 'doc', content: [{ type: 'paragraph' }] };

let closeActive: (() => void) | null = null;

function docOf(value: unknown) {
  return value && typeof value === 'object' ? value : EMPTY_DOC;
}

function hasText(doc: unknown): boolean {
  const walk = (node: unknown): boolean => {
    if (!node || typeof node !== 'object') return false;
    const value = node as { type?: string; text?: string; content?: unknown[] };
    if (value.type === 'text' && String(value.text || '').trim()) return true;
    return Array.isArray(value.content) && value.content.some(walk);
  };
  return walk(doc);
}

function mountEditor(host: HTMLElement, doc: unknown, editable: boolean) {
  return new Editor({
    element: host,
    extensions: [
      StarterKit.configure({ bulletList: false, listItem: false }),
      Link.configure({ openOnClick: false, autolink: true, defaultProtocol: 'https' }),
      circleBullet,
      collapsibleListItem,
      fourLevelBulletTab,
    ],
    content: docOf(doc) as any,
    editable,
  });
}

function fillSelect(select: HTMLSelectElement, items: OutlineTemplateMeta[], selectedId: string, placeholder: string) {
  select.replaceChildren();
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = placeholder;
  select.append(blank);
  for (const item of items) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.name;
    select.append(option);
  }
  select.value = items.some(item => item.id === selectedId) ? selectedId : '';
}

export function openTemplateDialog(hooks: {
  currentDoc: () => unknown;
  list: () => Promise<OutlineTemplateMeta[]>;
  load: (id: string) => Promise<OutlineTemplateRecord>;
  create: (name: string, doc: unknown) => Promise<OutlineTemplateRecord>;
  update: (id: string, name: string, doc: unknown, revision: number) => Promise<OutlineTemplateRecord>;
  onInsert: (doc: unknown) => void;
  onReplace: (doc: unknown) => void;
}) {
  closeActive?.();
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = `<form id="pdf-template-dialog" class="name-dialog pdf-template-dialog" role="dialog" aria-modal="true">
    <header>
      <div class="pdf-template-tabs" role="tablist">
        <button type="button" role="tab" data-tab="save" aria-selected="true">存模板</button>
        <button type="button" role="tab" data-tab="pick" aria-selected="false">选模板</button>
      </div>
      <button type="button" data-close title="关闭" aria-label="关闭"><i data-lucide="x"></i></button>
    </header>
    <div data-panel="save">
      <label class="pdf-template-field">名称<input data-name maxlength="80" placeholder="模板名称"></label>
      <div class="pdf-template-editor is-readonly" data-editor="save"></div>
      <div class="dialog-actions"><button type="button" data-create hidden>新建</button><button type="button" data-edit>编辑</button><button type="button" class="primary" data-save hidden>保存</button><button type="button" data-cancel hidden>取消</button></div>
    </div>
    <div data-panel="pick" hidden>
      <label class="pdf-template-field">已存模板<select data-saved><option value="">选择已存模板</option></select></label>
      <div class="pdf-template-editor is-readonly" data-editor="pick"></div>
      <div class="dialog-actions"><button type="button" class="primary" data-insert disabled>插入并定位</button><button type="button" data-replace disabled>替换并定位</button></div>
    </div>
    <p class="pdf-template-error" data-error hidden></p>
  </form>`;
  const error = overlay.querySelector<HTMLElement>('[data-error]')!;
  const showError = (message: string) => {
    error.hidden = !message;
    error.textContent = message;
  };
  const savePanel = overlay.querySelector<HTMLElement>('[data-panel="save"]')!;
  const pickPanel = overlay.querySelector<HTMLElement>('[data-panel="pick"]')!;
  const saveTab = overlay.querySelector<HTMLButtonElement>('[data-tab="save"]')!;
  const pickTab = overlay.querySelector<HTMLButtonElement>('[data-tab="pick"]')!;
  const nameInput = overlay.querySelector<HTMLInputElement>('[data-name]')!;
  const saveHost = overlay.querySelector<HTMLElement>('[data-editor="save"]')!;
  const pickHost = overlay.querySelector<HTMLElement>('[data-editor="pick"]')!;
  const editButton = overlay.querySelector<HTMLButtonElement>('[data-edit]')!;
  const createButton = overlay.querySelector<HTMLButtonElement>('[data-create]')!;
  const saveButton = overlay.querySelector<HTMLButtonElement>('[data-save]')!;
  const cancelButton = overlay.querySelector<HTMLButtonElement>('[data-cancel]')!;
  const saved = overlay.querySelector<HTMLSelectElement>('[data-saved]')!;
  const insertButton = overlay.querySelector<HTMLButtonElement>('[data-insert]')!;
  const replaceButton = overlay.querySelector<HTMLButtonElement>('[data-replace]')!;
  const saveEditor = mountEditor(saveHost, EMPTY_DOC, false);
  const pickEditor = mountEditor(pickHost, EMPTY_DOC, false);
  let tab: 'save' | 'pick' = 'save';
  let items: OutlineTemplateMeta[] = [];
  let current: OutlineTemplateRecord | null = null;
  let picked: OutlineTemplateRecord | null = null;
  let viewedId = '';
  let snapshot = { name: '', doc: EMPTY_DOC as unknown };
  let creating = false;
  let editing = false;

  const dirty = () => (creating || editing) && (
    nameInput.value.trim() !== snapshot.name.trim()
    || JSON.stringify(saveEditor.getJSON()) !== JSON.stringify(snapshot.doc)
  );
  const paint = () => {
    const writable = creating || editing;
    nameInput.readOnly = !writable;
    saveEditor.setEditable(writable);
    saveHost.classList.toggle('is-readonly', !writable);
    editButton.hidden = writable || !current;
    createButton.hidden = writable;
    saveButton.hidden = !writable;
    cancelButton.hidden = !editing && !(creating && items.length > 0);
  };
  const showSaved = (record: OutlineTemplateRecord | null, doc: unknown, mode: 'read' | 'edit' | 'create') => {
    current = record;
    if (record) viewedId = record.id;
    creating = mode === 'create';
    editing = mode === 'edit';
    nameInput.value = record?.name || '';
    saveEditor.commands.setContent(stripOutlineReferences(docOf(doc)) as any);
    snapshot = { name: nameInput.value, doc: saveEditor.getJSON() };
    paint();
    showError('');
  };
  const revertSave = () => {
    if (creating) {
      const id = viewedId || items[0]?.id;
      if (!id) return Promise.resolve();
      return hooks.load(id).then(record => showSaved(record, record.doc, 'read'));
    }
    if (current) showSaved(current, snapshot.doc, 'read');
    return Promise.resolve();
  };
  const showTab = (next: 'save' | 'pick') => {
    if (next === tab) return;
    if (tab === 'save' && dirty() && !window.confirm('放弃尚未保存的模板修改？')) return;
    const leave = tab === 'save' && dirty() ? revertSave() : Promise.resolve();
    void leave.then(() => {
      tab = next;
      savePanel.hidden = next !== 'save';
      pickPanel.hidden = next !== 'pick';
      saveTab.setAttribute('aria-selected', String(next === 'save'));
      pickTab.setAttribute('aria-selected', String(next === 'pick'));
      showError('');
      if (next === 'pick') void refreshPick();
    }).catch(err => showError(err instanceof Error ? err.message : String(err)));
  };
  const armPick = (record: OutlineTemplateRecord | null) => {
    picked = record;
    const ready = Boolean(record && hasText(record.doc));
    insertButton.disabled = !ready;
    replaceButton.disabled = !ready;
  };
  const refreshPick = async () => {
    items = await hooks.list();
    const selected = saved.value;
    fillSelect(saved, items, selected, items.length ? '选择已存模板' : '还没有模板');
    if (!items.length) {
      pickEditor.commands.setContent(EMPTY_DOC as any);
      armPick(null);
      showError('还没有保存过的大纲模板。');
      return;
    }
    if (!saved.value) armPick(null);
  };

  saveTab.onclick = () => showTab('save');
  pickTab.onclick = () => showTab('pick');
  editButton.onclick = () => {
    if (!current) return;
    snapshot = { name: nameInput.value, doc: saveEditor.getJSON() };
    editing = true;
    paint();
    nameInput.focus();
  };
  createButton.onclick = () => {
    showSaved(null, hooks.currentDoc(), 'create');
    nameInput.focus();
  };
  cancelButton.onclick = () => {
    if (dirty() && !window.confirm('放弃尚未保存的模板修改？')) return;
    void revertSave().catch(err => showError(err instanceof Error ? err.message : String(err)));
  };
  saveButton.onclick = () => {
    void (async () => {
      const name = nameInput.value.trim();
      const doc = stripOutlineReferences(saveEditor.getJSON());
      if (!name) { showError('请填写模板名称。'); return; }
      if (!hasText(doc)) { showError('模板内容是空的。'); return; }
      saveButton.disabled = true;
      try {
        const savedRecord = creating || !current
          ? await hooks.create(name, doc)
          : await hooks.update(current.id, name, doc, current.revision);
        items = await hooks.list();
        showSaved(savedRecord, savedRecord.doc, 'read');
      } catch (err) {
        showError(err instanceof Error ? err.message : String(err));
      } finally {
        saveButton.disabled = false;
      }
    })();
  };
  saved.onchange = () => {
    const id = saved.value;
    armPick(null);
    showError('');
    if (!id) {
      pickEditor.commands.setContent(EMPTY_DOC as any);
      return;
    }
    void hooks.load(id).then(record => {
      if (saved.value !== id) return;
      pickEditor.commands.setContent(stripOutlineReferences(docOf(record.doc)) as any);
      armPick(record);
      if (!hasText(record.doc)) showError('这个模板没有可插入的内容。');
    }).catch(err => showError(err instanceof Error ? err.message : String(err)));
  };
  insertButton.onclick = () => {
    if (!picked) return;
    hooks.onInsert(picked.doc);
    close();
  };
  replaceButton.onclick = () => {
    if (!picked) return;
    hooks.onReplace(picked.doc);
    close();
  };

  let requestClose = () => close();
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    requestClose();
  };
  const close = () => {
    document.removeEventListener('keydown', onKey);
    saveEditor.destroy();
    pickEditor.destroy();
    overlay.remove();
    if (closeActive === close) closeActive = null;
  };
  closeActive = close;
  requestClose = () => {
    if (dirty() && !window.confirm('放弃尚未保存的模板修改？')) return;
    close();
  };
  overlay.querySelector<HTMLButtonElement>('[data-close]')!.onclick = () => requestClose();
  overlay.addEventListener('click', event => { if (event.target === overlay) requestClose(); });
  overlay.querySelector('form')!.addEventListener('submit', event => event.preventDefault());
  document.addEventListener('keydown', onKey);
  document.body.append(overlay);
  createIcons({ icons });

  void hooks.list().then(async listed => {
    items = listed;
    if (!items.length) {
      showSaved(null, hooks.currentDoc(), 'create');
      nameInput.focus();
      return;
    }
    const record = await hooks.load(items[0].id);
    showSaved(record, record.doc, 'read');
  }).catch(err => showError(err instanceof Error ? err.message : String(err)));
}
