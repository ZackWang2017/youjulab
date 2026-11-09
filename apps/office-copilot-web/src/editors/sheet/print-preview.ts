import { createIcons, icons } from 'lucide';
import './print-preview.css';

export function openSheetPrintPreview(html: string) {
  document.querySelector<HTMLDialogElement>('.sheet-print-preview')?.close();
  const previousFocus = document.activeElement as HTMLElement | null;
  const dialog = document.createElement('dialog');
  dialog.className = 'sheet-print-preview';
  dialog.setAttribute('aria-labelledby', 'sheet-print-preview-title');
  dialog.innerHTML = `
    <header class="sheet-print-preview-header">
      <div class="sheet-print-preview-heading"><i data-lucide="printer"></i><h2 id="sheet-print-preview-title">打印预览</h2></div>
      <button type="button" data-close title="关闭" aria-label="关闭打印预览"><i data-lucide="x"></i></button>
    </header>
    <div class="sheet-print-preview-tools">
      <div class="sheet-print-preview-info"><strong data-name></strong><span data-range></span></div>
      <label>纸张方向 <select data-direction aria-label="纸张方向"><option value="portrait">纵向 A4</option><option value="landscape">横向 A4</option></select></label>
    </div>
    <div class="sheet-print-preview-stage"><iframe title="工作表打印内容" sandbox="allow-same-origin allow-modals"></iframe></div>
    <footer><span data-status role="status">正在准备预览…</span><div><button type="button" data-cancel>取消</button><button type="button" class="sheet-print-preview-submit" data-print disabled><i data-lucide="printer"></i>打印</button></div></footer>`;
  const frame = dialog.querySelector('iframe')!;
  const print = dialog.querySelector<HTMLButtonElement>('[data-print]')!;
  const direction = dialog.querySelector<HTMLSelectElement>('[data-direction]')!;
  const status = dialog.querySelector<HTMLElement>('[data-status]')!;
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (previousFocus?.isConnected) previousFocus.focus();
  }, { once: true });
  dialog.querySelector('[data-close]')!.addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-cancel]')!.addEventListener('click', () => dialog.close());
  let pageStyle: HTMLStyleElement;
  const updateDirection = () => {
    if (!pageStyle) return;
    const landscape = direction.value === 'landscape';
    pageStyle.textContent = `@page { size: A4 ${direction.value}; margin: 12mm; }
      @media screen { html { background: #e9edef; padding: 24px; box-sizing: border-box; min-height: 100%; }
        body { background: white; box-sizing: border-box; padding: 12mm; width: ${landscape ? '297mm' : '210mm'};
          min-height: ${landscape ? '210mm' : '297mm'}; margin: 0 auto; box-shadow: 0 2px 12px #00000012; } }
      table { width: 100%; }`;
  };
  direction.addEventListener('change', updateDirection);
  frame.addEventListener('load', async () => {
    const doc = frame.contentDocument;
    if (!doc) { status.textContent = '预览加载失败，请关闭后重试。'; return; }
    doc.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        dialog.close();
      }
    });
    dialog.querySelector('[data-name]')!.textContent = doc.title || '工作表';
    dialog.querySelector('[data-range]')!.textContent = doc.querySelector('.meta')?.textContent || '';
    pageStyle = doc.createElement('style');
    doc.head.append(pageStyle);
    updateDirection();
    await doc.fonts?.ready;
    if (!dialog.isConnected) return;
    status.textContent = '预览已就绪';
    print.disabled = !doc.querySelector('td');
    if (print.disabled) status.textContent = '当前工作表没有可打印内容。';
  }, { once: true });
  print.addEventListener('click', async () => {
    const win = frame.contentWindow;
    if (!win || print.disabled) return;
    print.disabled = true;
    status.textContent = '正在打开系统打印窗口…';
    try {
      await frame.contentDocument?.fonts?.ready;
      win.focus();
      win.print();
      status.textContent = '预览已就绪';
    } catch (error) {
      status.textContent = error instanceof Error ? `打印失败：${error.message}` : '无法打开系统打印窗口。';
    } finally {
      print.disabled = false;
    }
  });
  frame.srcdoc = html;
  document.body.append(dialog);
  createIcons({ icons });
  dialog.showModal();
}
