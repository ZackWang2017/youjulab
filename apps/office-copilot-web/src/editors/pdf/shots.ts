import { persist, saved } from '../../context/types';
import { askConfirm } from '../../workspace/nav';

type Shot = { id: string; name: string; image: string };

export function bindPdfShots(options: {
  container: HTMLElement;
  documentId: () => string;
  activePane: () => HTMLElement;
  canCapture: () => boolean;
}) {
  let boundId = '';
  let snipping = false;
  let openId = '';
  let signature = '';
  let windowEl: HTMLElement | null = null;
  let mask: HTMLElement | null = null;
  let stopSnip: (() => void) | null = null;
  const foundButton = options.container.querySelector<HTMLButtonElement>('[data-pdf-shot]');
  const foundList = options.container.querySelector<HTMLElement>('[data-pdf-shots]');
  if (!foundButton || !foundList) return { sync() {} };
  const button = foundButton;
  const list = foundList;

  function storageKey(id = options.documentId()) {
    return id ? `office-pdf-shots:${id}` : '';
  }

  function load(): Shot[] {
    const key = storageKey();
    const raw = key ? saved(key) : null;
    if (!Array.isArray(raw)) return [];
    return raw.filter((item): item is Shot => (
      !!item && typeof item === 'object'
      && typeof item.id === 'string'
      && typeof item.name === 'string'
      && typeof item.image === 'string'
    ));
  }

  function save(shots: Shot[]) {
    const key = storageKey();
    if (!key) return;
    persist(key, shots);
  }

  function closeWindow() {
    openId = '';
    windowEl?.remove();
    windowEl = null;
  }

  function cancelSnip() {
    snipping = false;
    stopSnip?.();
    stopSnip = null;
    mask?.remove();
    mask = null;
    button.classList.remove('is-active');
  }

  function render() {
    const id = options.documentId();
    if (id !== boundId) {
      boundId = id;
      signature = '';
      cancelSnip();
      closeWindow();
    }
    const shots = id ? load() : [];
    if (openId && !shots.some(shot => shot.id === openId)) closeWindow();
    const next = `${id}|${openId}|${snipping}|${shots.map(shot => `${shot.id}\u0000${shot.name}`).join('\u0001')}`;
    button.disabled = snipping ? false : !options.canCapture();
    button.classList.toggle('is-active', snipping);
    button.setAttribute('aria-pressed', snipping ? 'true' : 'false');
    if (next === signature) return;
    signature = next;
    list.hidden = shots.length === 0;
    list.replaceChildren(...shots.map(shot => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'pdf-shot-name';
      item.dataset.shotId = shot.id;
      item.textContent = shot.name;
      item.title = shot.name;
      item.classList.toggle('is-open', shot.id === openId);
      item.addEventListener('click', () => show(shot.id));
      return item;
    }));
  }

  function show(id: string) {
    const shot = load().find(item => item.id === id);
    if (!shot) return;
    openId = id;
    const win = ensureWindow();
    setWindowTitle(win, shot.name);
    const image = win.querySelector('img')!;
    image.alt = shot.name;
    image.onload = () => fitShotWindow(win, image);
    image.src = shot.image;
    if (image.complete && image.naturalWidth) fitShotWindow(win, image);
    render();
  }

  function ensureWindow() {
    if (windowEl) return windowEl;
    const win = document.createElement('section');
    win.className = 'pdf-shot-window';
    win.setAttribute('role', 'dialog');
    win.setAttribute('aria-label', '截图');
    win.innerHTML = `
      <header class="pdf-shot-title">
        <strong></strong>
        <button type="button" class="pdf-shot-rename" aria-label="改名">改名</button>
        <button type="button" class="pdf-shot-delete" aria-label="删除">删除</button>
        <button type="button" class="pdf-shot-close" aria-label="关闭">×</button>
      </header>
      <img alt="">
      <button type="button" class="pdf-shot-resize" aria-label="调整大小"></button>`;
    document.body.append(win);
    win.querySelector('.pdf-shot-rename')!.addEventListener('click', () => beginRename());
    win.querySelector('.pdf-shot-delete')!.addEventListener('click', () => { void deleteOpen(); });
    win.querySelector('.pdf-shot-close')!.addEventListener('click', () => {
      closeWindow();
      render();
    });
    const title = win.querySelector<HTMLElement>('.pdf-shot-title')!;
    let drag: { x: number; y: number; left: number; top: number } | null = null;
    title.addEventListener('pointerdown', event => {
      if ((event.target as HTMLElement).closest('button, input')) return;
      const rect = win.getBoundingClientRect();
      drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      title.setPointerCapture(event.pointerId);
    });
    title.addEventListener('pointermove', event => {
      if (!drag) return;
      win.style.left = `${Math.max(8, drag.left + event.clientX - drag.x)}px`;
      win.style.top = `${Math.max(8, drag.top + event.clientY - drag.y)}px`;
      win.style.right = 'auto';
    });
    title.addEventListener('pointerup', () => { drag = null; });
    bindShotResize(win);
    windowEl = win;
    return win;
  }

  function setWindowTitle(win: HTMLElement, name: string) {
    const title = win.querySelector('.pdf-shot-title')!;
    const current = title.querySelector('strong, input');
    if (current instanceof HTMLInputElement) current.dataset.discard = '1';
    const heading = document.createElement('strong');
    heading.textContent = name;
    if (current) current.replaceWith(heading);
    else title.prepend(heading);
  }

  function beginRename() {
    const id = openId;
    const win = windowEl;
    if (!id || !win) return;
    const title = win.querySelector('.pdf-shot-title')!;
    if (title.querySelector('input')) return;
    const strong = title.querySelector('strong');
    if (!strong) return;
    const current = strong.textContent || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.maxLength = 80;
    input.setAttribute('aria-label', '截图名称');
    strong.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = (commit: boolean) => {
      if (done || input.dataset.discard || !input.isConnected) return;
      done = true;
      const name = commit ? input.value.trim() : '';
      const heading = document.createElement('strong');
      heading.textContent = name || current;
      input.replaceWith(heading);
      if (commit && name && name !== current) renameShot(id, name);
    };
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        finish(true);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(true));
  }

  function renameShot(id: string, name: string) {
    const shots = load();
    const shot = shots.find(item => item.id === id);
    if (!shot || shot.name === name) return;
    shot.name = name;
    save(shots);
    if (openId === id && windowEl) {
      setWindowTitle(windowEl, name);
      const image = windowEl.querySelector('img');
      if (image) image.alt = name;
    }
    render();
  }

  async function deleteOpen() {
    const id = openId;
    const shot = load().find(item => item.id === id);
    if (!shot) return;
    const ok = await askConfirm(`删除「${shot.name}」？`);
    if (!ok || openId !== id) return;
    save(load().filter(item => item.id !== id));
    closeWindow();
    render();
  }

  function nextName(shots: Shot[]) {
    const used = new Set(shots.map(shot => shot.name));
    let index = shots.length + 1;
    while (used.has(`截图${index}`)) index += 1;
    return `截图${index}`;
  }

  function addShot(image: string) {
    const shots = load();
    const shot = { id: crypto.randomUUID(), name: nextName(shots), image };
    shots.push(shot);
    save(shots);
    render();
  }

  function startSnip() {
    if (snipping) {
      cancelSnip();
      render();
      return;
    }
    if (!options.canCapture()) return;
    const pane = options.activePane();
    snipping = true;
    const layer = document.createElement('div');
    layer.className = 'pdf-shot-mask';
    const rect = document.createElement('div');
    rect.className = 'pdf-shot-rect';
    rect.hidden = true;
    layer.append(rect);
    pane.append(layer);
    mask = layer;
    let startX = 0;
    let startY = 0;
    let moved = false;
    let dragging = false;
    let settled = false;
    const finish = (commit: boolean) => {
      if (settled) return;
      settled = true;
      dragging = false;
      const box = {
        left: Math.min(startX, lastX),
        top: Math.min(startY, lastY),
        right: Math.max(startX, lastX),
        bottom: Math.max(startY, lastY),
      };
      cancelSnip();
      if (commit && moved) {
        const image = cropPage(pane, box);
        if (image) addShot(image);
        else window.dispatchEvent(new CustomEvent('office-error', { detail: '没有截到已渲染的页面。' }));
      }
      render();
    };
    let lastX = 0;
    let lastY = 0;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      finish(false);
    };
    window.addEventListener('keydown', onKey);
    stopSnip = () => window.removeEventListener('keydown', onKey);
    layer.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      startX = event.clientX;
      startY = event.clientY;
      moved = false;
      rect.hidden = false;
      rect.style.left = `${startX}px`;
      rect.style.top = `${startY}px`;
      rect.style.width = '0px';
      rect.style.height = '0px';
      dragging = true;
      try { layer.setPointerCapture(event.pointerId); } catch { /* 合成事件没有指针捕获 */ }
    });
    layer.addEventListener('pointermove', event => {
      if (!dragging) return;
      const left = Math.min(startX, event.clientX);
      const top = Math.min(startY, event.clientY);
      const width = Math.abs(event.clientX - startX);
      const height = Math.abs(event.clientY - startY);
      moved = width >= 8 || height >= 8;
      rect.style.left = `${left}px`;
      rect.style.top = `${top}px`;
      rect.style.width = `${width}px`;
      rect.style.height = `${height}px`;
    });
    layer.addEventListener('pointerup', event => {
      lastX = event.clientX;
      lastY = event.clientY;
      finish(true);
    });
    render();
  }

  button.addEventListener('click', startSnip);
  render();
  return { sync: render };
}

const FRAME_KEY = 'office-pdf-shot-width';

function savedFrameWidth() {
  const raw = saved(FRAME_KEY);
  const width = typeof raw === 'number' ? raw : Number.NaN;
  return Number.isFinite(width) && width >= 96 ? width : 0;
}

function fitShotWindow(win: HTMLElement, image: HTMLImageElement) {
  applyShotFrame(win, image, savedFrameWidth() || image.naturalWidth);
}

function applyShotFrame(win: HTMLElement, image: HTMLImageElement, imageWidth: number) {
  const naturalWidth = image.naturalWidth;
  const naturalHeight = image.naturalHeight;
  if (!naturalWidth || !naturalHeight) return;
  const ratio = naturalHeight / naturalWidth;
  const maxWidth = Math.max(96, window.innerWidth - 16);
  const maxHeight = Math.max(64, window.innerHeight - 88);
  const width = Math.max(96, Math.min(imageWidth, maxWidth, maxHeight / ratio));
  const height = Math.max(1, Math.round(width * ratio));
  image.style.width = `${Math.round(width)}px`;
  image.style.height = `${height}px`;
  const frame = getComputedStyle(win);
  const border = (Number.parseFloat(frame.borderLeftWidth) || 0) + (Number.parseFloat(frame.borderRightWidth) || 0);
  win.style.width = `${Math.round(width) + border}px`;
}

function bindShotResize(win: HTMLElement) {
  const handle = win.querySelector<HTMLElement>('.pdf-shot-resize')!;
  let drag: { x: number; y: number; width: number } | null = null;
  handle.addEventListener('pointerdown', event => {
    const image = win.querySelector('img');
    if (!image?.naturalWidth) return;
    event.preventDefault();
    event.stopPropagation();
    drag = { x: event.clientX, y: event.clientY, width: image.getBoundingClientRect().width };
    try { handle.setPointerCapture(event.pointerId); } catch { /* 合成事件没有指针捕获 */ }
  });
  handle.addEventListener('pointermove', event => {
    if (!drag) return;
    const image = win.querySelector('img');
    if (!image?.naturalWidth) return;
    const ratio = image.naturalHeight / image.naturalWidth;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    const next = Math.abs(dx) >= Math.abs(dy) ? drag.width + dx : drag.width + dy / ratio;
    applyShotFrame(win, image, next);
  });
  const stop = () => {
    if (!drag) return;
    const image = win.querySelector('img');
    const width = image ? Math.round(image.getBoundingClientRect().width) : 0;
    const changed = width >= 96 && Math.abs(width - drag.width) >= 1;
    drag = null;
    if (changed) persist(FRAME_KEY, width);
  };
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
}

function cropPage(pane: HTMLElement, box: { left: number; top: number; right: number; bottom: number }) {
  const page = [...pane.querySelectorAll<HTMLElement>('.page')].find(item => {
    const bounds = item.getBoundingClientRect();
    return box.left < bounds.right && box.right > bounds.left && box.top < bounds.bottom && box.bottom > bounds.top;
  });
  const canvas = page?.querySelector('canvas');
  if (!canvas) return '';
  const bounds = canvas.getBoundingClientRect();
  const left = Math.max(box.left, bounds.left);
  const top = Math.max(box.top, bounds.top);
  const right = Math.min(box.right, bounds.right);
  const bottom = Math.min(box.bottom, bounds.bottom);
  if (right - left < 4 || bottom - top < 4 || bounds.width <= 0 || bounds.height <= 0) return '';
  const scaleX = canvas.width / bounds.width;
  const scaleY = canvas.height / bounds.height;
  const out = document.createElement('canvas');
  out.width = Math.max(1, Math.round((right - left) * scaleX));
  out.height = Math.max(1, Math.round((bottom - top) * scaleY));
  const context = out.getContext('2d');
  if (!context) return '';
  context.drawImage(
    canvas,
    (left - bounds.left) * scaleX,
    (top - bounds.top) * scaleY,
    (right - left) * scaleX,
    (bottom - top) * scaleY,
    0,
    0,
    out.width,
    out.height,
  );
  const png = out.toDataURL('image/png');
  return png.length < 1_500_000 ? png : out.toDataURL('image/jpeg', 0.92);
}
