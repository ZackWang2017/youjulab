import { describe, expect, it } from 'vitest';
import { acceleratorFromEvent, formatAccelerator, type KeyLike } from './shortcut-keys';

const key = (code: string, mods: Partial<KeyLike> = {}): KeyLike => ({
  code, key: '', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...mods,
});

describe('acceleratorFromEvent', () => {
  it('uses physical key codes so Option-modified letters still map to the letter', () => {
    expect(acceleratorFromEvent({ ...key('KeyN', { ctrlKey: true, altKey: true }), key: '˜' }, true)).toBe('Control+Alt+N');
    expect(acceleratorFromEvent(key('Digit5', { metaKey: true, shiftKey: true }), true)).toBe('Command+Shift+5');
    expect(acceleratorFromEvent(key('Space', { altKey: true }), false)).toBe('Alt+Space');
  });

  it('requires a non-shift modifier and a real key', () => {
    expect(acceleratorFromEvent(key('KeyN'), true)).toBeNull();
    expect(acceleratorFromEvent(key('KeyN', { shiftKey: true }), true)).toBeNull();
    expect(acceleratorFromEvent(key('ControlLeft', { ctrlKey: true }), true)).toBeNull();
  });

  it('maps Meta to Super outside macOS', () => {
    expect(acceleratorFromEvent(key('KeyT', { metaKey: true }), false)).toBe('Super+T');
  });
});

describe('formatAccelerator', () => {
  it('shows symbols on macOS and words elsewhere', () => {
    expect(formatAccelerator('Control+Alt+N', true)).toBe('⌃⌥N');
    expect(formatAccelerator('Command+Shift+T', true)).toBe('⇧⌘T');
    expect(formatAccelerator('Control+Alt+N', false)).toBe('Ctrl+Alt+N');
    expect(formatAccelerator('', true)).toBe('');
  });
});
