import { office } from '../../office-client/client';
import { cacheAsset, clearFilePassword, setFilePassword, type Asset } from '../../workspace/session';

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] || ch));
}

function showError(form: HTMLFormElement, message: string) {
  const el = form.querySelector<HTMLElement>('[data-error]');
  if (!el) return;
  el.hidden = !message;
  el.textContent = message;
}

/** 不用 type=password / autocomplete=password，避免浏览器当成登录并弹出记住密码。 */
function secretInput(name: string, extra = '') {
  return `<input name="${name}" type="text" class="file-secret-input" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="text" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" ${extra}>`;
}

function secretField(caption: string, name: string, extra = '') {
  return `<label class="field-label"><span>${caption}</span>${secretInput(name, extra)}</label>`;
}

function mountDialog(html: string) {
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = html;
  document.body.append(overlay);
  const secrets = [...overlay.querySelectorAll<HTMLInputElement>('.file-secret-input')];
  for (const input of secrets) input.readOnly = true;
  window.setTimeout(() => {
    for (const input of secrets) input.readOnly = false;
    secrets[0]?.focus();
  }, 80);
  return overlay;
}

export function promptSheetPassword(name: string) {
  return new Promise<string>(resolve => {
    const overlay = mountDialog(`<form class="name-dialog file-secret-dialog" autocomplete="off" data-lpignore="true" data-1p-ignore="true"><h2>打开加密表格</h2>
      <p>「${escapeHtml(name)}」已加密，请输入文件密码。忘记密码无法恢复。</p>
      ${secretField('文件密码', 'file-secret', 'minlength="6" required')}
      <p class="backup-restore-error" data-error hidden></p>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">解锁</button></div></form>`);
    const form = overlay.querySelector('form')!;
    const finish = (value: string) => { overlay.remove(); resolve(value); };
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(''); });
    form.querySelector('[data-cancel]')?.addEventListener('click', () => finish(''));
    form.addEventListener('submit', event => {
      event.preventDefault();
      finish(String(new FormData(form).get('file-secret') || ''));
    });
  });
}

export function openSheetEncryptionDialog(asset: Asset) {
  return asset.encrypted ? manageEncrypted(asset) : enableEncryption(asset);
}

function enableEncryption(asset: Asset) {
  return new Promise<Asset | null>(resolve => {
    const overlay = mountDialog(`<form class="name-dialog file-secret-dialog" autocomplete="off" data-lpignore="true" data-1p-ignore="true"><h2>文件加密</h2>
      <p>开启后，表格内容加密存储，只有输入密码才能查看。忘记密码无法恢复，AI 与外部工具在解锁前也读不到明文。</p>
      ${secretField('文件密码', 'file-secret', 'minlength="6" required')}
      ${secretField('确认密码', 'file-secret-confirm', 'minlength="6" required')}
      <p class="backup-restore-error" data-error hidden></p>
      <div class="dialog-actions"><button type="button" data-cancel>取消</button><button class="primary" type="submit">开启加密</button></div></form>`);
    const form = overlay.querySelector('form')!;
    const finish = (value: Asset | null) => { overlay.remove(); resolve(value); };
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    form.querySelector('[data-cancel]')?.addEventListener('click', () => finish(null));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const password = String(new FormData(form).get('file-secret') || '');
      const confirm = String(new FormData(form).get('file-secret-confirm') || '');
      if (password !== confirm) {
        showError(form, '两次输入的文件密码不一致。');
        return;
      }
      try {
        const saved = await office.json<Asset>(`/assets/${asset.id}/encryption`, {
          method: 'POST',
          body: JSON.stringify({ action: 'enable', password, password_confirm: confirm }),
        });
        setFilePassword(asset.id, password);
        finish(cacheAsset(saved));
      } catch (err) {
        showError(form, err instanceof Error ? err.message : String(err));
      }
    });
  });
}

function manageEncrypted(asset: Asset) {
  return new Promise<Asset | null>(resolve => {
    const overlay = mountDialog(`<form class="name-dialog file-secret-dialog" autocomplete="off" data-lpignore="true" data-1p-ignore="true"><h2>文件加密</h2>
      <p>此表格已加密。可关闭加密或修改文件密码。</p>
      ${secretField('当前密码', 'file-secret', 'minlength="6" required')}
      ${secretField('新密码（修改时填写）', 'file-secret-next', 'minlength="6"')}
      ${secretField('确认新密码', 'file-secret-confirm', 'minlength="6"')}
      <p class="backup-restore-error" data-error hidden></p>
      <div class="dialog-actions">
        <button type="button" data-cancel>取消</button>
        <button type="submit" name="intent" value="disable">关闭加密</button>
        <button class="primary" type="submit" name="intent" value="change">修改密码</button>
      </div></form>`);
    const form = overlay.querySelector('form')!;
    const finish = (value: Asset | null) => { overlay.remove(); resolve(value); };
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    form.querySelector('[data-cancel]')?.addEventListener('click', () => finish(null));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const data = new FormData(form);
      const password = String(data.get('file-secret') || '');
      const next = String(data.get('file-secret-next') || '');
      const confirm = String(data.get('file-secret-confirm') || '');
      const intent = (event.submitter as HTMLButtonElement | null)?.value || 'disable';
      try {
        if (intent === 'change') {
          if (!next) throw new Error('请填写新密码。');
          if (next !== confirm) throw new Error('两次输入的文件密码不一致。');
          const saved = await office.json<Asset>(`/assets/${asset.id}/encryption`, {
            method: 'POST',
            body: JSON.stringify({ action: 'change', password, new_password: next, password_confirm: confirm }),
          });
          setFilePassword(asset.id, next);
          finish(cacheAsset(saved));
          return;
        }
        const saved = await office.json<Asset>(`/assets/${asset.id}/encryption`, {
          method: 'POST',
          body: JSON.stringify({ action: 'disable', password }),
        });
        clearFilePassword(asset.id);
        finish(cacheAsset(saved));
      } catch (err) {
        showError(form, err instanceof Error ? err.message : String(err));
      }
    });
  });
}
