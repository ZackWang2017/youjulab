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
  setBackupSession: token => ipcRenderer.invoke('youju:set-backup-session', token),
  backupStatus: email => ipcRenderer.invoke('youju:backup-status', email),
  setBackupDirectory: directory => ipcRenderer.invoke('youju:set-backup-directory', directory),
  setBackupScheduleEnabled: enabled => ipcRenderer.invoke('youju:set-backup-schedule-enabled', enabled),
  onBackupProgress: listener => {
    const handler = (_event, progress) => listener(progress);
    ipcRenderer.on('youju:backup-progress', handler);
    return () => ipcRenderer.removeListener('youju:backup-progress', handler);
  },
  createBackup: () => ipcRenderer.invoke('youju:create-backup'),
  retryBackupUpload: id => ipcRenderer.invoke('youju:retry-backup-upload', id),
  restoreBackup: payload => ipcRenderer.invoke('youju:restore-backup', payload),
  quitAfterRestore: () => ipcRenderer.invoke('youju:quit-after-restore'),
});
