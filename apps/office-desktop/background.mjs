/** Window-close is hide; actual quit remains an explicit application action. */
export function hideOnClose(window, shouldQuit) {
  window.on('close', event => {
    if (shouldQuit()) return;
    event.preventDefault();
    window.hide();
  });
}

/**
 * items() 返回退出项之前的菜单项（记笔记、待办等），每次右键时重新生成，
 * 以便快捷键修改或注册失败后菜单立即反映当前状态。
 */
export function createBackgroundTray({ Tray, Menu, image, show, quit, items = () => [] }) {
  const tray = new Tray(image);
  tray.setToolTip('有据 youjulab');
  const menu = () => {
    const extra = items();
    return Menu.buildFromTemplate([...extra, ...(extra.length ? [{ type: 'separator' }] : []), { label: '退出程序', click: quit }]);
  };
  // Do not set a context menu: on macOS that would also open it on a left click.
  tray.on('click', show);
  tray.on('double-click', show);
  tray.on('right-click', () => tray.popUpContextMenu(menu()));
  return tray;
}
