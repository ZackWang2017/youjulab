export type SheetBinding = { assetId: string; generation: number };

export function assertSheetContentOwner(assetId: string, content: unknown) {
  if (!assetId || !content || typeof content !== 'object' || Array.isArray(content)) return;
  const id = (content as { id?: unknown }).id;
  if (id !== undefined && id !== '' && id !== assetId) {
    throw new Error('工作簿归属与当前资产不一致，已阻止编辑和保存。请先核对原始数据，不要直接覆盖。');
  }
}

export function sheetBindings() {
  let generation = 0;
  let activeId = '';
  return {
    begin(assetId: string): SheetBinding { activeId = assetId; return { assetId, generation: ++generation }; },
    current(binding: SheetBinding) { return binding.generation === generation && binding.assetId === activeId; },
  };
}
