import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { assetIndexExtensions, getAssetIndexDeps } from './bind';

export type IntentEditorHandle = {
  editor: Editor;
  getText: () => string;
  setText: (text: string) => void;
  setPlaceholder: (text: string) => void;
  focus: () => void;
  destroy: () => void;
  isEmpty: () => boolean;
};

/** 助手输入框：迷你 TipTap，与文档共用 assetReference / `#/` 索引。 */
export function attachIntentEditor(host: HTMLElement): IntentEditorHandle {
  if (!getAssetIndexDeps()) {
    throw new Error('请先 configureAssetIndex。');
  }
  host.classList.add('assistant-intent-editor');
  const syncEmpty = (editor: Editor) => {
    host.classList.toggle('is-empty', editor.isEmpty);
  };
  const editor = new Editor({
    element: host,
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        codeBlock: false,
        horizontalRule: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
      }),
      ...assetIndexExtensions(),
    ],
    content: { type: 'doc', content: [{ type: 'paragraph' }] },
    editorProps: {
      attributes: {
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': '任务描述',
      },
      handleKeyDown: (_view, event) => {
        if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey)) return false;
        event.preventDefault();
        host.dispatchEvent(new CustomEvent('intent-submit', { bubbles: true }));
        return true;
      },
    },
    onCreate: ({ editor: instance }) => syncEmpty(instance),
    onUpdate: ({ editor: instance }) => syncEmpty(instance),
  });

  return {
    editor,
    getText: () => editor.getText({ blockSeparator: '\n' }).trim(),
    setText: text => {
      const value = String(text || '').trim();
      editor.commands.setContent(
        value
          ? { type: 'doc', content: value.split(/\n+/).map(line => ({ type: 'paragraph', content: line ? [{ type: 'text', text: line }] : [] })) }
          : { type: 'doc', content: [{ type: 'paragraph' }] },
      );
      syncEmpty(editor);
    },
    setPlaceholder: text => {
      host.dataset.placeholder = text;
    },
    focus: () => { editor.commands.focus('end'); },
    destroy: () => editor.destroy(),
    isEmpty: () => editor.isEmpty,
  };
}
