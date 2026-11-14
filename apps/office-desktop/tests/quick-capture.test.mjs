import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_QUICK_SHORTCUTS, normalizeAccelerator, quickMenuItems, readQuickShortcuts, validateQuickShortcuts,
} from '../quick-capture.mjs';

test('normalizes accelerators and requires a modifier plus one key', () => {
  assert.equal(normalizeAccelerator('ctrl+option+n'), 'Control+Alt+N');
  assert.equal(normalizeAccelerator(' Cmd + Shift + F5 '), 'Command+Shift+F5');
  assert.equal(normalizeAccelerator(''), '');
  assert.throws(() => normalizeAccelerator('N'), /修饰键/);
  assert.throws(() => normalizeAccelerator('Control+Alt'), /主键/);
  assert.throws(() => normalizeAccelerator('Control+A+B'), /一个主键/);
  assert.throws(() => normalizeAccelerator('Control+Hyper'), /无法识别/);
});

test('reading config falls back to defaults for missing or invalid values, keeps disabled', () => {
  assert.deepEqual(readQuickShortcuts(undefined), DEFAULT_QUICK_SHORTCUTS);
  assert.deepEqual(readQuickShortcuts({ notebook: 'bogus', todos: '' }), { notebook: 'Control+Alt+N', todos: '' });
});

test('saving rejects duplicates and invalid input', () => {
  assert.deepEqual(validateQuickShortcuts({ notebook: 'Alt+Shift+N', todos: '' }), { notebook: 'Alt+Shift+N', todos: '' });
  assert.throws(() => validateQuickShortcuts({ notebook: 'Control+Alt+N', todos: 'ctrl+alt+n' }), /同一个/);
  assert.throws(() => validateQuickShortcuts({ notebook: 'N', todos: '' }), /修饰键/);
});

test('menu items show the shortcut, or a conflict note when registration failed', () => {
  const opened = [];
  const items = quickMenuItems(DEFAULT_QUICK_SHORTCUTS, ['todos'], mode => opened.push(mode));
  assert.equal(items[0].label, '记笔记');
  assert.equal(items[0].accelerator, 'Control+Alt+N');
  assert.equal(items[0].registerAccelerator, false);
  assert.equal(items[1].label, '待办事项（快捷键被占用）');
  assert.equal(items[1].accelerator, undefined);
  items[1].click();
  assert.deepEqual(opened, ['todos']);
});
