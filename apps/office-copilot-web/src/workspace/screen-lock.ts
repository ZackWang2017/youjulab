import { createIcons, icons } from 'lucide';
import { office } from '../office-client/client';
import './screen-lock.css';

type Preferences = { background: 'orbit' | 'stars' | 'lake'; password_enabled: boolean };
const backgrounds = [
  { id: 'orbit', name: '轨道', image: '/screen-lock/orbit.webp' },
  { id: 'stars', name: '星空', image: '/screen-lock/stars.webp' },
  { id: 'lake', name: '山湖', image: '/screen-lock/lake.webp' },
] as const;
let active: HTMLElement | null = null;

export function screenLockSettingsMarkup() {
  return `<section class="settings-section screen-lock-settings" data-settings-panel="screen-lock" role="tabpanel" aria-labelledby="settings-screen-lock-title" hidden>
    <div class="settings-section-title"><i data-lucide="lock-keyhole"></i><div><h3 id="settings-screen-lock-title">屏保</h3></div></div>
    <form data-screen-lock-settings>
      <fieldset class="screen-lock-backgrounds"><legend>背景</legend>${backgrounds.map(item => `<label><input type="radio" name="screen-background" value="${item.id}" ${item.id === 'stars' ? 'checked' : ''}><img src="${item.image}" alt="" loading="lazy" width="180" height="100"><span>${item.name}</span></label>`).join('')}</fieldset>
      <label class="screen-lock-password-toggle"><input type="checkbox" name="screen-password-enabled"><span>使用屏保密码</span></label>
      <label class="settings-field" data-current-password hidden><span>当前屏保密码</span><input type="password" name="screen-current-password" autocomplete="off" maxlength="128"></label>
      <label class="settings-field" data-new-password hidden><span>新屏保密码</span><input type="password" name="screen-new-password" autocomplete="new-password" maxlength="128" placeholder="至少 4 位；留空保留原密码"></label>
      <label class="settings-field" data-confirm-password hidden><span>确认新密码</span><input type="password" name="screen-confirm-password" autocomplete="new-password" maxlength="128"></label>
      <p class="settings-note screen-lock-settings-note" data-screen-lock-note role="status"></p>
      <div class="settings-inline-actions"><button type="submit" class="primary">保存屏保设置</button></div>
    </form>
  </section>`;
}

export function bindScreenLockSettings(root: HTMLElement) {
  const form = root.querySelector<HTMLFormElement>('[data-screen-lock-settings]')!;
  const checkbox = form.elements.namedItem('screen-password-enabled') as HTMLInputElement;
  const current = form.elements.namedItem('screen-current-password') as HTMLInputElement;
  const password = form.elements.namedItem('screen-new-password') as HTMLInputElement;
  const confirm = form.elements.namedItem('screen-confirm-password') as HTMLInputElement;
  const note = form.querySelector<HTMLElement>('[data-screen-lock-note]')!;
  const save = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
  let prefs: Preferences | null = null;
  let loading = false;
  const fields = () => {
    form.querySelector<HTMLElement>('[data-current-password]')!.hidden = !prefs?.password_enabled;
    for (const name of ['new', 'confirm']) form.querySelector<HTMLElement>(`[data-${name}-password]`)!.hidden = !checkbox.checked;
    current.required = Boolean(prefs?.password_enabled && (!checkbox.checked || password.value));
    password.required = checkbox.checked && !prefs?.password_enabled;
    password.minLength = password.value ? 4 : 0;
  };
  const load = async () => {
    if (prefs || loading) return;
    loading = true; save.disabled = true;
    try {
      prefs = await office.json<Preferences>('/local/screen-lock');
      (form.querySelector<HTMLInputElement>(`input[name="screen-background"][value="${prefs.background}"]`) || form.querySelector<HTMLInputElement>('input[name="screen-background"]'))!.checked = true;
      checkbox.checked = prefs.password_enabled;
      fields(); note.textContent = '';
    } catch (error) { note.textContent = error instanceof Error ? error.message : String(error); }
    finally { loading = false; save.disabled = !prefs; }
  };
  root.querySelector('[data-settings-category="screen-lock"]')!.addEventListener('click', () => { void load(); });
  checkbox.addEventListener('change', fields);
  password.addEventListener('input', fields);
  save.disabled = true;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!prefs) return;
    if (checkbox.checked && password.value !== confirm.value) { note.textContent = '两次输入的新密码不一致。'; return; }
    save.disabled = true;
    try {
      prefs = await office.json<Preferences>('/local/screen-lock', { method: 'PUT', body: JSON.stringify({
        background: form.querySelector<HTMLInputElement>('input[name="screen-background"]:checked')!.value,
        password_enabled: checkbox.checked, current_password: current.value,
        new_password: checkbox.checked ? password.value : '',
      }) });
      current.value = password.value = confirm.value = '';
      fields(); note.textContent = '屏保设置已保存。';
    } catch (error) { note.textContent = error instanceof Error ? error.message : String(error); }
    finally { save.disabled = false; }
  });
}

export async function showScreenLock() {
  if (active) { active.querySelector<HTMLElement>('input,button')?.focus(); return; }
  const previousFocus = document.activeElement as HTMLElement | null;
  const overlay = document.createElement('div');
  overlay.className = 'screen-lock-overlay';
  overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', '屏保');
  overlay.tabIndex = -1;
  overlay.innerHTML = `<div class="screen-lock-shade"></div><div class="screen-lock-heading"><i data-lucide="lock-keyhole"></i><span>有据</span></div>
    <form class="screen-lock-unlock"><label data-password-label hidden><span>屏保密码</span><input type="password" autocomplete="off" maxlength="128" aria-label="屏保密码" placeholder="输入屏保密码"></label>
    <p data-message role="status">正在开启屏保…</p><button type="submit" disabled><i data-lucide="unlock-keyhole"></i><span>取消屏保</span></button></form>`;
  active = overlay;
  const inert = new Map<HTMLElement, boolean>();
  const conceal = (element: Element) => {
    if (element instanceof HTMLElement && element !== overlay && !inert.has(element)) {
      inert.set(element, element.inert); element.inert = true;
    }
  };
  [...document.body.children].forEach(conceal);
  document.body.append(overlay);
  createIcons({ icons });
  overlay.focus();
  const observer = new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
    if (node instanceof Element) conceal(node);
  })));
  observer.observe(document.body, { childList: true });
  const form = overlay.querySelector<HTMLFormElement>('form')!;
  const input = form.querySelector<HTMLInputElement>('input')!;
  const button = form.querySelector<HTMLButtonElement>('button')!;
  const message = form.querySelector<HTMLElement>('[data-message]')!;
  let prefs: Preferences | null = null;
  let pending = false;
  const close = () => {
    observer.disconnect();
    window.removeEventListener('keydown', keyboard, true);
    for (const [element, wasInert] of inert) element.inert = wasInert;
    input.value = '';
    overlay.remove(); active = null;
    if (previousFocus?.isConnected) previousFocus.focus();
  };
  const keyboard = (event: KeyboardEvent) => {
    event.stopImmediatePropagation();
    if ((event.ctrlKey || event.metaKey) && event.key !== 'v' && event.key !== 'a') event.preventDefault();
    if (event.key === 'Escape' && prefs && !prefs.password_enabled && !pending) { event.preventDefault(); close(); }
    if (event.key === 'Tab') {
      event.preventDefault();
      if (prefs?.password_enabled && !pending) (document.activeElement === input ? button : input).focus();
      else if (!button.disabled) button.focus();
    }
  };
  window.addEventListener('keydown', keyboard, true);
  const load = async () => {
    pending = true; button.disabled = true;
    try {
      prefs = await office.json<Preferences>('/local/screen-lock');
      const background = backgrounds.find(item => item.id === prefs!.background) || backgrounds[1];
      overlay.style.backgroundImage = `url("${background.image}")`;
      form.querySelector<HTMLElement>('[data-password-label]')!.hidden = !prefs.password_enabled;
      input.required = prefs.password_enabled;
      message.textContent = '';
      button.querySelector('span')!.textContent = '取消屏保';
      if (prefs.password_enabled) input.focus(); else button.focus();
    } catch (error) {
      message.textContent = error instanceof Error ? error.message : String(error);
      button.querySelector('span')!.textContent = '重试';
    } finally { pending = false; button.disabled = false; }
  };
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending) return;
    if (!prefs) { await load(); return; }
    if (!prefs.password_enabled) { close(); return; }
    pending = true; button.disabled = true;
    try {
      await office.json('/local/screen-lock/unlock', { method: 'POST', body: JSON.stringify({ password: input.value }) });
      close();
    } catch (error) {
      input.value = ''; message.textContent = error instanceof Error ? error.message : String(error); input.focus();
    } finally { pending = false; button.disabled = false; }
  });
  await load();
}
