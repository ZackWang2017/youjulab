import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createBackgroundTray, hideOnClose } from '../background.mjs';

test('close hides without terminating; explicit quit permits destruction', () => {
  const window = new EventEmitter();
  let quitting = false, hidden = 0, prevented = 0;
  window.hide = () => hidden++;
  hideOnClose(window, () => quitting);
  window.emit('close', { preventDefault: () => prevented++ });
  assert.equal(hidden, 1); assert.equal(prevented, 1);
  quitting = true;
  window.emit('close', { preventDefault: () => prevented++ });
  assert.equal(hidden, 1); assert.equal(prevented, 1);
});
test('tray left click opens; right click exposes only Quit', () => {
  class Tray extends EventEmitter { setToolTip() {} popUpContextMenu(menu) { this.menu = menu; } }
  let opened = 0, quit = 0;
  const tray = createBackgroundTray({ Tray, Menu: { buildFromTemplate: items => items }, image: {},
    show: () => opened++, quit: () => quit++ });
  tray.emit('click'); assert.equal(opened, 1); assert.equal(tray.menu, undefined);
  tray.emit('right-click'); assert.equal(tray.menu.length, 1); assert.equal(tray.menu[0].label, '退出程序');
  tray.menu[0].click(); assert.equal(quit, 1);
});
test('tray menu rebuilds extra items on every right click, before Quit', () => {
  class Tray extends EventEmitter { setToolTip() {} popUpContextMenu(menu) { this.menu = menu; } }
  let label = '记笔记';
  const tray = createBackgroundTray({ Tray, Menu: { buildFromTemplate: items => items }, image: {},
    show: () => {}, quit: () => {}, items: () => [{ label }] });
  tray.emit('right-click');
  assert.deepEqual(tray.menu.map(item => item.label ?? item.type), ['记笔记', 'separator', '退出程序']);
  label = '记笔记（快捷键被占用）';
  tray.emit('right-click');
  assert.equal(tray.menu[0].label, '记笔记（快捷键被占用）');
});
