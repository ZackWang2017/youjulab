import { app, BrowserWindow, dialog, ipcMain } from 'electron';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = process.argv.includes('--dev') || !app.isPackaged;

let mainWindow = null;
let backend = null;
let backendPort = 0;

const DEFAULT_REMOTE = 'http://127.0.0.1:8000';
/** repo = 与开发 .env 共用仓库 data/；userData = 独立副本（外发安装包默认）。 */
const DATA_MODES = new Set(['repo', 'userData']);

function findRepoRoot() {
  if (process.env.YOUJU_REPO_ROOT) return path.resolve(process.env.YOUJU_REPO_ROOT);
  const starts = [
    __dirname,
    process.resourcesPath || '',
    process.cwd(),
    path.dirname(process.execPath),
  ].filter(Boolean);
  for (const start of starts) {
    let cur = path.resolve(start);
    for (let i = 0; i < 12; i++) {
      if (fs.existsSync(path.join(cur, 'apps', 'office-backend', 'server.py'))) return cur;
      const parent = path.dirname(cur);
      if (parent === cur) break;
      cur = parent;
    }
  }
  return null;
}

const repoRoot = findRepoRoot();

function userDataPaths() {
  const root = app.getPath('userData');
  return {
    root,
    sqlite: path.join(root, 'office.sqlite'),
    vectors: path.join(root, 'office-vectors.sqlite'),
    files: path.join(root, 'office-files'),
    config: path.join(root, 'app-config.json'),
  };
}

function repoDataPaths() {
  if (!repoRoot) return null;
  const local = path.join(repoRoot, 'data', 'office-local');
  return {
    root: path.join(repoRoot, 'data'),
    sqlite: path.join(local, 'office.sqlite'),
    vectors: path.join(local, 'office-vectors.sqlite'),
    files: path.join(repoRoot, 'data', 'office-files'),
  };
}

function repoDataAvailable() {
  const repo = repoDataPaths();
  return Boolean(repo && fs.existsSync(repo.sqlite));
}

function defaultDataMode() {
  if (process.env.YOUJU_DATA_MODE === 'userData') return 'userData';
  if (process.env.YOUJU_DATA_MODE === 'repo') return 'repo';
  // 本机从仓库打的包：默认与开发共用 data/；外发安装无仓库则用 userData。
  return repoDataAvailable() ? 'repo' : 'userData';
}

function normalizeDataMode(value) {
  const mode = String(value || '').trim();
  if (DATA_MODES.has(mode)) return mode;
  return defaultDataMode();
}

function readConfig() {
  const paths = userDataPaths();
  try {
    const saved = JSON.parse(fs.readFileSync(paths.config, 'utf8'));
    return {
      remoteBaseUrl: String(saved.remoteBaseUrl || DEFAULT_REMOTE).replace(/\/$/, ''),
      pdfDir: String(saved.pdfDir || ''),
      dataMode: normalizeDataMode(saved.dataMode),
    };
  } catch {
    return {
      remoteBaseUrl: DEFAULT_REMOTE,
      pdfDir: '',
      dataMode: defaultDataMode(),
    };
  }
}

function writeConfig(config) {
  const paths = userDataPaths();
  const prev = readConfig();
  const next = {
    remoteBaseUrl: String((config?.remoteBaseUrl ?? prev.remoteBaseUrl) || DEFAULT_REMOTE).replace(/\/$/, '') || DEFAULT_REMOTE,
    pdfDir: String(config?.pdfDir ?? prev.pdfDir ?? ''),
    dataMode: normalizeDataMode(config?.dataMode ?? prev.dataMode),
  };
  fs.mkdirSync(paths.root, { recursive: true });
  fs.writeFileSync(paths.config, JSON.stringify(next, null, 2));
  return { ...next, ...publicDataInfo(next) };
}

function resolveDataPaths(config = readConfig()) {
  const mode = normalizeDataMode(config.dataMode);
  if (mode === 'repo') {
    const repo = repoDataPaths();
    if (!repo || !fs.existsSync(repo.sqlite)) {
      throw new Error('dataMode=repo 但找不到仓库 data/office-local/office.sqlite');
    }
    return {
      mode: 'repo',
      sqlite: repo.sqlite,
      vectors: repo.vectors,
      files: config.pdfDir || repo.files,
      root: repo.root,
    };
  }
  const ud = userDataPaths();
  return {
    mode: 'userData',
    sqlite: ud.sqlite,
    vectors: ud.vectors,
    files: config.pdfDir || ud.files,
    root: ud.root,
  };
}

function publicDataInfo(config = readConfig()) {
  try {
    const data = resolveDataPaths(config);
    return {
      dataMode: data.mode,
      dataRoot: data.root,
      sqlitePath: data.sqlite,
      vectorsPath: data.vectors,
      filesPath: data.files,
      repoDataAvailable: repoDataAvailable(),
    };
  } catch (err) {
    return {
      dataMode: config.dataMode,
      dataRoot: '',
      sqlitePath: '',
      vectorsPath: '',
      filesPath: config.pdfDir || '',
      repoDataAvailable: repoDataAvailable(),
      dataError: String(err?.message || err),
    };
  }
}

function waitHealth(port, tries = 120) {
  return new Promise((resolve, reject) => {
    let left = tries;
    const tick = () => {
      const req = http.get(`http://127.0.0.1:${port}/health`, res => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else if (--left <= 0) reject(new Error('office-backend 健康检查失败'));
        else setTimeout(tick, 250);
      });
      req.on('error', () => {
        if (--left <= 0) reject(new Error('office-backend 未启动'));
        else setTimeout(tick, 250);
      });
    };
    tick();
  });
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      server.close(err => (err ? reject(err) : resolve(port)));
    });
    server.on('error', reject);
  });
}

/** 打包进 Resources 的 PyInstaller onedir 可执行文件。 */
function resolveSidecarBinary() {
  if (process.env.YOUJU_SIDECAR) {
    const explicit = path.resolve(process.env.YOUJU_SIDECAR);
    if (fs.existsSync(explicit)) return explicit;
  }
  const name = process.platform === 'win32' ? 'youju-office-backend.exe' : 'youju-office-backend';
  const candidates = [
    path.join(process.resourcesPath || '', 'sidecar', 'youju-office-backend', name),
    // 开发时直接跑已构建的 sidecar
    path.join(__dirname, 'sidecar', 'dist', 'youju-office-backend', name),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function resolvePython() {
  if (process.env.YOUJU_PYTHON) return process.env.YOUJU_PYTHON;
  if (repoRoot) {
    const venvPy = path.join(repoRoot, '.venv', 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
    if (fs.existsSync(venvPy)) return venvPy;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function webDistDir() {
  const bundled = path.join(process.resourcesPath || '', 'web');
  if (app.isPackaged && fs.existsSync(path.join(bundled, 'index.html'))) return bundled;
  if (repoRoot) {
    const fromRepo = path.join(repoRoot, 'apps', 'office-copilot-web', 'dist');
    if (fs.existsSync(path.join(fromRepo, 'index.html'))) return fromRepo;
  }
  // 开发态未 build 前端时仍可走 Vite；打包态必须有 web
  if (!isDev) throw new Error('找不到前端产物。请先构建 office-copilot-web，或检查 sidecar/web 是否打进安装包。');
  return '';
}

function startBackend(port) {
  const config = readConfig();
  const data = resolveDataPaths(config);
  fs.mkdirSync(path.dirname(data.sqlite), { recursive: true });
  fs.mkdirSync(path.dirname(data.vectors), { recursive: true });
  fs.mkdirSync(data.files, { recursive: true });
  const webDist = webDistDir();
  const env = {
    ...process.env,
    OFFICE_STORE: 'sqlite',
    OFFICE_SQLITE_PATH: data.sqlite,
    OFFICE_VECTOR_BACKEND: 'sqlite',
    OFFICE_VECTOR_SQLITE_PATH: data.vectors,
    OFFICE_FILE_ROOT: data.files,
    OFFICE_API_HOST: '127.0.0.1',
    OFFICE_API_PORT: String(port),
    OFFICE_DESKTOP: '1',
    MCP_REMOTE_BASE_URL: config.remoteBaseUrl,
  };
  if (webDist) env.OFFICE_WEB_DIST = webDist;

  const sidecar = resolveSidecarBinary();
  let command;
  let args;
  let cwd;
  if (sidecar) {
    console.log(`[youju] backend=sidecar ${sidecar}`);
    command = sidecar;
    args = [];
    cwd = path.dirname(sidecar);
  } else {
    if (!repoRoot) {
      throw new Error('未找到 Python sidecar，也找不到仓库。请先运行 npm run build:sidecar，或设置 YOUJU_REPO_ROOT / YOUJU_PYTHON。');
    }
    const python = resolvePython();
    const serverPy = path.join(repoRoot, 'apps', 'office-backend', 'server.py');
    console.log(`[youju] backend=python ${python}`);
    command = python;
    args = [serverPy];
    cwd = repoRoot;
  }
  console.log(`[youju] dataMode=${data.mode} sqlite=${data.sqlite}`);

  backend = spawn(command, args, {
    cwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout?.on('data', chunk => process.stdout.write(`[office-backend] ${chunk}`));
  backend.stderr?.on('data', chunk => process.stderr.write(`[office-backend] ${chunk}`));
  backend.on('exit', code => {
    console.error(`office-backend exited: ${code}`);
    backend = null;
  });
  return backend;
}

ipcMain.on('youju:version', event => { event.returnValue = app.getVersion(); });
ipcMain.on('youju:office-api-base', event => {
  event.returnValue = backendPort ? `http://127.0.0.1:${backendPort}` : '';
});
ipcMain.handle('youju:get-config', () => {
  const config = readConfig();
  return { ...config, ...publicDataInfo(config) };
});
ipcMain.handle('youju:set-config', (_event, config) => writeConfig(config));

async function checkUpdate(remoteBase) {
  try {
    const res = await fetch(`${remoteBase}/version.json`);
    if (!res.ok) return;
    const data = await res.json();
    const remote = String(data.version || '');
    if (remote && remote !== app.getVersion()) {
      await dialog.showMessageBox({
        type: 'info',
        title: '发现新版本',
        message: `远程版本 ${remote}，当前 ${app.getVersion()}。请到发布页下载更新。`,
      });
    }
  } catch {
    // 远程不可达时不打断启动。
  }
}

async function createWindow() {
  // 首次启动写入默认配置，便于确认 dataMode（不会动仓库 data/ 本身）。
  const paths = userDataPaths();
  if (!fs.existsSync(paths.config)) writeConfig(readConfig());

  backendPort = await pickPort();
  startBackend(backendPort);
  await waitHealth(backendPort);

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    webPreferences: {
      // CJS preload：Electron sandbox 下 ESM preload 可能静默失败，导致 youju 未注入。
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.once('ready-to-show', () => mainWindow?.show());

  void checkUpdate(readConfig().remoteBaseUrl);
  if (isDev) {
    process.env.OFFICE_API_PORT = String(backendPort);
    await mainWindow.loadURL(`http://127.0.0.1:5173/?officePort=${backendPort}`);
  } else {
    // 与 office-backend 同源 HTTP，才能直连远程 mcp（file:// 会被 CSP 拦截）。
    await mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
  }
}

function stopBackend() {
  if (!backend) return;
  backend.kill('SIGTERM');
  backend = null;
}

app.whenReady().then(() => createWindow().catch(err => {
  console.error(err);
  dialog.showErrorBox('youjulab 启动失败', String(err?.message || err));
  app.quit();
}));

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => stopBackend());

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void createWindow();
  }
});
