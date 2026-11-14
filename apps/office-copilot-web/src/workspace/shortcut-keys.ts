/** 设置页录入全局快捷键：把按键事件转成 Electron accelerator，并按平台显示。 */

export type KeyLike = Pick<KeyboardEvent, 'code' | 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>;

const NAMED: Record<string, string> = {
  Space: 'Space', Tab: 'Tab', Enter: 'Return', NumpadEnter: 'Return', Backspace: 'Backspace', Delete: 'Delete',
  Insert: 'Insert', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
  Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown',
  Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']', Semicolon: ';', Quote: "'",
  Comma: ',', Period: '.', Slash: '/', Backslash: '\\', Backquote: '`',
};

/** 用 code 而不是 key：macOS 上按住 ⌥ 时 key 会变成特殊字符（⌥N 得到「˜」）。 */
function keyFromCode(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  return NAMED[code] ?? null;
}

/**
 * 返回 accelerator；只按了修饰键、或没有 ⌃/⌥/⌘ 这类主修饰键时返回 null（单独 ⇧+字母等同于打字）。
 */
export function acceleratorFromEvent(event: KeyLike, mac: boolean): string | null {
  const key = keyFromCode(event.code);
  if (!key) return null;
  if (!event.ctrlKey && !event.altKey && !event.metaKey) return null;
  const parts: string[] = [];
  if (event.metaKey) parts.push(mac ? 'Command' : 'Super');
  if (event.ctrlKey) parts.push('Control');
  if (event.altKey) parts.push('Alt');
  if (event.shiftKey) parts.push('Shift');
  return [...parts, key].join('+');
}

const MAC_SYMBOLS: Record<string, string> = { Command: '⌘', Control: '⌃', Alt: '⌥', Shift: '⇧', Super: '⌘', CommandOrControl: '⌘' };

/** 显示用：macOS 用 ⌃⌥⇧⌘ 符号，其它平台用 Ctrl+Alt+N。 */
export function formatAccelerator(accelerator: string, mac: boolean): string {
  if (!accelerator) return '';
  const parts = accelerator.split('+');
  if (mac) {
    const order = ['Control', 'Alt', 'Shift', 'Command', 'Super', 'CommandOrControl'];
    const mods = parts.slice(0, -1).sort((a, b) => order.indexOf(a) - order.indexOf(b));
    return mods.map(m => MAC_SYMBOLS[m] ?? m).join('') + parts[parts.length - 1];
  }
  return parts.map(part => ({ Control: 'Ctrl', Super: 'Win', CommandOrControl: 'Ctrl' }[part] ?? part)).join('+');
}
