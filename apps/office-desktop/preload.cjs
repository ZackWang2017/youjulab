const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('youju', {
  isDesktop: true,
  platform: process.platform,
  appVersion: ipcRenderer.sendSync('youju:version'),
  officeApiBase: ipcRenderer.sendSync('youju:office-api-base') || '',
  mcpApiBase: ipcRenderer.sendSync('youju:mcp-api-base') || '',
  getConfig: () => ipcRenderer.invoke('youju:get-config'),
  setConfig: config => ipcRenderer.invoke('youju:set-config', config),
  getLoginPreference: () => ipcRenderer.invoke('youju:get-login-preference'),
  saveLoginPreference: preference => ipcRenderer.invoke('youju:save-login-preference', preference),
  backupStatus: email => ipcRenderer.invoke('youju:backup-status', email),
  setBackupDirectory: directory => ipcRenderer.invoke('youju:set-backup-directory', directory),
  createBackup: () => ipcRenderer.invoke('youju:create-backup'),
  restoreBackup: payload => ipcRenderer.invoke('youju:restore-backup', payload),
  quitAfterRestore: () => ipcRenderer.invoke('youju:quit-after-restore'),
});
