const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('updater', {
  action: action => ipcRenderer.invoke('youju:update-action', action),
  ready: () => ipcRenderer.invoke('youju:update-state'),
  onState: callback => ipcRenderer.on('youju:update-state', (_event, state) => callback(state)),
});
