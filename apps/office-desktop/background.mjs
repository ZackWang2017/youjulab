/** Window-close is hide; actual quit remains an explicit application action. */
export function hideOnClose(window, shouldQuit) {
  window.on('close', event => {
    if (shouldQuit()) return;
    event.preventDefault();
    window.hide();
  });
}

export function createBackgroundTray({ Tray, Menu, image, show, quit }) {
  const tray = new Tray(image);
  tray.setToolTip('有据 youjulab');
  const menu = Menu.buildFromTemplate([{ label: '退出程序', click: quit }]);
  // Do not set a context menu: on macOS that would also open it on a left click.
  tray.on('click', show);
  tray.on('double-click', show);
  tray.on('right-click', () => tray.popUpContextMenu(menu));
  return tray;
}
