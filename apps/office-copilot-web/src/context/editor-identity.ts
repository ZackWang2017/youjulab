import type { EditorAdapter } from './types';

/** A navigation selection is not proof that the editor has loaded that asset. */
export function trackEditorIdentity(editor: EditorAdapter): EditorAdapter {
  let loadedId = '';
  let generation = 0;
  return {
    ...editor,
    loadedAssetId: () => loadedId,
    async load(asset) {
      const token = ++generation;
      loadedId = '';
      await editor.load(asset);
      if (generation === token) loadedId = asset.id;
    },
  };
}

export function assertEditorIdentity(selectedId: string, editor: EditorAdapter) {
  if (editor.loadedAssetId?.() !== selectedId) {
    throw new Error('当前资产尚未完成加载，已阻止保存，避免写入其他资产。');
  }
}
