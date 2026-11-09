import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { checkRelease, downloadMac, installVerifiedUpdate, updateBase, verifyFile } from './update-policy.mjs';

const require = createRequire(import.meta.url);

/** Startup-only gate: no Office service or editor exists while this window is open. */
export class UpdateController {
  constructor({ app, BrowserWindow, ipcMain, shell, directory, base, stopServices, beforeInstall }) {
    Object.assign(this, { app, BrowserWindow, ipcMain, shell, directory, base: updateBase(base), stopServices, beforeInstall });
    this.state = { phase: 'checking', currentVersion: app.getVersion(), platform: process.platform, percent: 0 };
    this.busy = false;
  }

  show() {
    if (!this.window || this.window.isDestroyed()) return false;
    if (this.window.isMinimized()) this.window.restore();
    this.hidden = false;
    this.window.show(); this.window.focus();
    return true;
  }

  send(next) {
    Object.assign(this.state, next);
    if (this.window && !this.window.isDestroyed()) this.window.webContents.send('youju:update-state', this.state);
  }

  async run() {
    if (!this.base || !this.app.isPackaged || !['darwin', 'win32'].includes(process.platform)
      || !['arm64', 'x64'].includes(process.arch) || (process.platform === 'win32' && process.arch !== 'x64')) return;
    this.window = new this.BrowserWindow({ width: 620, height: 600, minWidth: 400, minHeight: 480,
      title: '有据 · 软件更新', show: false, autoHideMenuBar: true,
      webPreferences: { preload: path.join(this.directory, 'update-window', 'preload.cjs'),
        contextIsolation: true, sandbox: true, nodeIntegration: false } });
    this.window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    this.window.webContents.on('will-navigate', event => event.preventDefault());
    const valid = event => event.sender === this.window?.webContents;
    this.ipcMain.handle('youju:update-state', event => { if (valid(event)) return this.state; });
    this.ipcMain.handle('youju:update-action', async (event, action) => {
      if (!valid(event)) throw new Error('更新窗口无效。');
      return this.action(action);
    });
    this.window.on('close', event => {
      if (!this.finished) { event.preventDefault(); this.hidden = true; this.window.hide(); }
    });
    this.wait = new Promise(resolve => { this.resolve = resolve; });
    await this.window.loadFile(path.join(this.directory, 'update-window', 'index.html'));
    this.window.show();
    await this.check();
    return this.wait;
  }

  finish() {
    this.finished = true;
    this.ipcMain.removeHandler('youju:update-state');
    this.ipcMain.removeHandler('youju:update-action');
    this.resolve?.();
  }

  async check() {
    this.send({ phase: 'checking', error: '', message: '正在检查版本…' });
    const result = await checkRelease({ base: this.base, version: this.app.getVersion(),
      platform: process.platform, arch: process.arch, cacheFile: path.join(this.app.getPath('userData'), 'update-state.json') });
    if (!result.release) {
      if (result.error) console.warn('[youju] release check unavailable; starting existing version');
      this.finish();
      return;
    }
    this.send({ phase: 'available', release: result.release, percent: 0, error: result.offline ? '版本服务暂不可达，已保留上次确认的强更要求。' : '',
      message: result.release.mandatory ? '此版本需要更新后才能进入工作区。' : '有新版本可用，你也可以暂不更新。' });
  }

  async action(action) {
    if (action === 'quit' && this.state.phase !== 'installing') { this.app.quit(); return; }
    if (action === 'cancel' && this.state.phase === 'downloading') { this.cancel?.(); return; }
    if (this.busy) return;
    if (action === 'skip' && this.state.release && !this.state.release.mandatory) { this.finish(); return; }
    if (action === 'open' && this.state.platform === 'darwin' && this.state.phase === 'downloaded') {
      await verifyFile(this.filename, this.state.release);
      const error = await this.shell.openPath(this.filename);
      if (error) throw new Error(error);
      return;
    }
    if (action === 'retry') { this.busy = true; try { await this.check(); } finally { this.busy = false; } return; }
    if (action !== 'download' || !['available', 'error'].includes(this.state.phase)) return;
    this.busy = true;
    const release = this.state.release;
    this.send({ phase: 'downloading', percent: 0, error: '', message: '正在下载并校验安装包…' });
    try {
      if (process.platform === 'darwin') {
        const controller = new AbortController();
        this.cancel = () => controller.abort();
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(30 * 60 * 1000)]);
        this.filename = await downloadMac(release, path.join(this.app.getPath('userData'), 'updates'),
          { signal, progress: percent => this.send({ percent }) });
        this.send({ phase: 'downloaded', percent: 100, message: '下载校验完成，请打开安装包完成安装。' });
      } else {
        const { NsisUpdater, CancellationToken } = require('electron-updater');
        // CancellationToken is provided by builder-util-runtime in electron-updater 6.
        const Token = CancellationToken || require('builder-util-runtime').CancellationToken;
        const updater = new NsisUpdater({ provider: 'generic', url: release.update_feed_url, useMultipleRangeRequest: false });
        updater.autoDownload = false;
        updater.autoInstallOnAppQuit = false;
        updater.autoRunAppAfterInstall = true;
        updater.allowDowngrade = false;
        updater.disableDifferentialDownload = true;
        updater.disableWebInstaller = true;
        updater.on('error', error => {
          if (this.state.phase === 'installing') this.send({ phase: 'error', error: '安装失败，请重试或退出。' });
          console.error('[youju] updater:', error.code || error.name);
        });
        updater.on('download-progress', info => this.send({ percent: info.percent }));
        let cancelled = false, token;
        this.cancel = () => { cancelled = true; token?.cancel(); };
        const checked = await updater.checkForUpdates();
        if (cancelled) throw new Error('已取消下载。');
        if (checked?.updateInfo?.version !== release.version) throw new Error('发布版本发生变化，请重新检查。');
        token = new Token();
        const files = await updater.downloadUpdate(token);
        this.send({ phase: 'installing', percent: 100, message: '正在安装，完成后将重新启动…' });
        await installVerifiedUpdate(files[0], release, { stopServices: this.stopServices,
          install: () => { this.beforeInstall(); updater.quitAndInstall(false, true); } });
      }
    } catch (error) {
      this.send({ phase: 'error', error: String(error.message || '下载失败，请重试。'), message: '未修改当前程序和本地数据。' });
    } finally { this.busy = false; this.cancel = null; }
  }

  closeForQuit() { this.cancel?.(); this.finished = true; }
}
