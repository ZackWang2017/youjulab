export function sheetAssetPath(assetPath: string | undefined, sheetName: string) {
  if (!assetPath || !sheetName) throw new Error('当前工作表尚未完成加载，请重新打开后复制。');
  return `${assetPath}-${sheetName}`;
}

export async function copySheetPath(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // LAN HTTP and some embedded browsers require the legacy clipboard path.
    }
  }
  const input = document.createElement('textarea');
  input.value = text;
  input.setAttribute('readonly', '');
  input.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
  document.body.append(input);
  try {
    input.select();
    if (!document.execCommand('copy')) throw new Error('浏览器未允许复制，请检查剪贴板权限。');
  } finally {
    input.remove();
  }
}
