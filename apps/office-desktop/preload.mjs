import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('youju', {
  isDesktop: true,
  platform: process.platform,
  appVersion: ipcRenderer.sendSync('youju:version'),
  officeApiBase: ipcRenderer.sendSync('youju:office-api-base') || '',
  getConfig: () => ipcRenderer.invoke('youju:get-config'),
  setConfig: config => ipcRenderer.invoke('youju:set-config', config),
});
