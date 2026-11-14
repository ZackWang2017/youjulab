/** 托盘快速记录：快捷键配置校验与托盘菜单项。纯函数，便于单测。 */

export const QUICK_MODES = ['notebook', 'todos'];
export const QUICK_LABELS = { notebook: '记笔记', todos: '待办事项' };
// macOS 上 Control/Alt 即 ⌃/⌥，Windows 上为 Ctrl/Alt
export const DEFAULT_QUICK_SHORTCUTS = { notebook: 'Control+Alt+N', todos: 'Control+Alt+T' };

const MODIFIERS = new Map([
  ['command', 'Command'], ['cmd', 'Command'], ['control', 'Control'], ['ctrl', 'Control'],
  ['commandorcontrol', 'CommandOrControl'], ['cmdorctrl', 'CommandOrControl'],
  ['alt', 'Alt'], ['option', 'Alt'], ['altgr', 'AltGr'], ['shift', 'Shift'], ['super', 'Super'], ['meta', 'Super'],
]);
const NAMED_KEYS = new Set([
  'Space', 'Tab', 'Backspace', 'Delete', 'Insert', 'Return', 'Enter', 'Up', 'Down', 'Left', 'Right',
  'Home', 'End', 'PageUp', 'PageDown', 'Escape', 'Esc', 'Plus',
]);

/** 规范化一个 accelerator；空串表示禁用。无效时抛错，提示用户重新录入。 */
export function normalizeAccelerator(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  const parts = raw.split('+').map(part => part.trim()).filter(Boolean);
  const modifiers = [];
  let key = '';
  for (const part of parts) {
    const modifier = MODIFIERS.get(part.toLowerCase());
    if (modifier) {
      if (!modifiers.includes(modifier)) modifiers.push(modifier);
      continue;
    }
    if (key) throw new Error(`快捷键「${raw}」只能包含一个主键。`);
    if (/^[a-z0-9]$/i.test(part)) key = part.toUpperCase();
    else if (/^F([1-9]|1[0-9]|2[0-4])$/i.test(part)) key = part.toUpperCase();
    else if (NAMED_KEYS.has(part)) key = part;
    else if (/^[`\-=[\];',./\\]$/.test(part)) key = part;
    else throw new Error(`快捷键「${raw}」包含无法识别的按键「${part}」。`);
  }
  if (!key) throw new Error(`快捷键「${raw}」缺少主键。`);
  if (!modifiers.length) throw new Error(`快捷键「${raw}」至少需要一个修饰键（⌃ / ⌥ / ⇧ / ⌘）。`);
  return [...modifiers, key].join('+');
}

/** 读取配置时容错：无效值回退默认值，不阻断启动。 */
export function readQuickShortcuts(saved) {
  const out = {};
  for (const mode of QUICK_MODES) {
    const value = saved && typeof saved === 'object' && mode in saved ? saved[mode] : DEFAULT_QUICK_SHORTCUTS[mode];
    try { out[mode] = normalizeAccelerator(value); }
    catch { out[mode] = DEFAULT_QUICK_SHORTCUTS[mode]; }
  }
  return out;
}

/** 保存设置时严格校验：格式错误或两项相同都拒绝。 */
export function validateQuickShortcuts(input) {
  const out = {};
  for (const mode of QUICK_MODES) out[mode] = normalizeAccelerator(input?.[mode]);
  if (out.notebook && out.notebook === out.todos) throw new Error('「记笔记」和「待办事项」不能使用同一个快捷键。');
  return out;
}

/** 托盘菜单里的两项；registerAccelerator=false 只显示快捷键，不额外注册。 */
export function quickMenuItems(shortcuts, failed, open) {
  return QUICK_MODES.map(mode => ({
    label: failed.includes(mode) ? `${QUICK_LABELS[mode]}（快捷键被占用）` : QUICK_LABELS[mode],
    ...(shortcuts[mode] && !failed.includes(mode) ? { accelerator: shortcuts[mode], registerAccelerator: false } : {}),
    click: () => open(mode),
  }));
}
