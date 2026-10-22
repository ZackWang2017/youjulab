import { app, BrowserWindow, dialog, ipcMain, nativeImage, safeStorage } from 'electron';
import { execFile, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { promisify } from 'node:util';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = process.argv.includes('--dev') || !app.isPackaged;

function appIcon() {
  const candidates = [
    path.join(__dirname, 'build', 'icon.png'),
    path.join(__dirname, 'build', 'icon.icns'),
  ];
  for (const file of candidates) {
    if (!fs.existsSync(file)) continue;
    const image = nativeImage.createFromPath(file);
    if (!image.isEmpty()) return image;
  }
  return null;
}

let mainWindow = null;
let backend = null;
let backendPort = 0;
let mcp = null;
let mcpPort = 0;
let backupTimer = null;

const execFileAsync = promisify(execFile);

const DEFAULT_REMOTE = 'http://127.0.0.1:8000';
const DEFAULT_MCP_PORT = 8000;
const BACKUP_VERSION = 1;
/** repo = 与开发 .env 共用仓库 data/；userData = 独立副本（外发安装包默认）。 */

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
    finance: path.join(root, 'finresearch.sqlite'),
    files: path.join(root, 'office-files'),
    config: path.join(root, 'app-config.json'),
  };
}

function backupPaths() {
  const root = userDataPaths().root;
  return {
    work: path.join(root, 'backup-work'),
    defaultDir: path.join(root, 'backups'),
  };
}

function backupDirectory(config = readConfig()) {
  return path.resolve(String(config.backupDir || backupPaths().defaultDir));
}

function repoDataPaths() {
  if (!repoRoot) return null;
  const local = path.join(repoRoot, 'data', 'office-local');
  return {
    root: path.join(repoRoot, 'data'),
    sqlite: path.join(local, 'office.sqlite'),
    vectors: path.join(local, 'office-vectors.sqlite'),
    finance: path.join(repoRoot, 'data', 'finance-local', 'finresearch.sqlite'),
    files: path.join(repoRoot, 'data', 'office-files'),
  };
}

function repoDataAvailable() {
  const repo = repoDataPaths();
  return Boolean(repo && fs.existsSync(repo.sqlite));
}

function defaultDataMode() {
  // 真实工作数据无论开发态或打包态都落在 Electron userData，避免两套库分叉。
  // repo 只留给显式指定的隔离调试/测试场景。
  if (process.env.YOUJU_DATA_MODE === 'repo') return 'repo';
  return 'userData';
}

function normalizeDataMode(value) {
  // 不继承旧 app-config.json 中的 repo 设置；只有启动环境显式要求才允许它生效。
  if (process.env.YOUJU_DATA_MODE === 'repo') return 'repo';
  return 'userData';
}

function normalizeMcpPort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : DEFAULT_MCP_PORT;
}

function newServiceSecret() {
  return randomBytes(32).toString('base64url');
}

function normalizeServiceSecret(value) {
  const secret = String(value || '').trim();
  return secret.length >= 32 ? secret : newServiceSecret();
}

/** 从仓库 .env 读取单项（打包态找不到仓库时返回空）。 */
function readRepoDotEnv(key) {
  const root = findRepoRoot();
  if (!root) return '';
  try {
    const text = fs.readFileSync(path.join(root, '.env'), 'utf8');
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eq = trimmed.indexOf('=');
      if (eq <= 0) continue;
      if (trimmed.slice(0, eq).trim() !== key) continue;
      return trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '');
    }
  } catch {
    /* ignore */
  }
  return '';
}

/** 自有模型密钥加密主密钥：优先已保存配置，其次环境变量 / 仓库 .env，否则生成并持久化。 */
function ensureModelCredentialsKey(config) {
  const existing = String(config?.modelCredentialsKey || '').trim();
  if (existing) return existing;
  const fromEnv = String(process.env.OFFICE_MODEL_CREDENTIALS_KEY || '').trim()
    || readRepoDotEnv('OFFICE_MODEL_CREDENTIALS_KEY');
  const key = fromEnv || newServiceSecret();
  writeConfig({ ...config, modelCredentialsKey: key });
  return key;
}

function autoLoginAvailable() {
  return safeStorage.isEncryptionAvailable();
}

function encryptedLoginPassword(value) {
  return safeStorage.encryptString(String(value || '')).toString('base64');
}

function decryptedLoginPassword(value) {
  if (!value || !autoLoginAvailable()) return '';
  try {
    return safeStorage.decryptString(Buffer.from(String(value), 'base64'));
  } catch {
    return '';
  }
}

function readConfig() {
  const paths = userDataPaths();
  try {
    const saved = JSON.parse(fs.readFileSync(paths.config, 'utf8'));
    return {
      remoteBaseUrl: String(saved.remoteBaseUrl || DEFAULT_REMOTE).replace(/\/$/, ''),
      pdfDir: String(saved.pdfDir || ''),
      dataMode: normalizeDataMode(saved.dataMode),
      // 仅主进程读取此字段；密码密文不会经 publicConfig 发送到渲染进程。
      lastLoginEmail: String(saved.lastLoginEmail || '').trim().toLowerCase(),
      autoLogin: Boolean(saved.autoLogin),
      autoLoginPassword: String(saved.autoLoginPassword || ''),
      mcpPort: normalizeMcpPort(saved.mcpPort),
      mcpAccessToken: normalizeServiceSecret(saved.mcpAccessToken),
      mcpJwtSecret: normalizeServiceSecret(saved.mcpJwtSecret),
      backupDir: String(saved.backupDir || ''),
      backupIdentity: saved.backupIdentity && typeof saved.backupIdentity === 'object'
        ? { email: String(saved.backupIdentity.email || '').trim().toLowerCase(), accountId: String(saved.backupIdentity.accountId || '') }
        : (saved.backupCredential?.email ? { email: String(saved.backupCredential.email).trim().toLowerCase(), accountId: '' } : null),
      lastBackupAt: String(saved.lastBackupAt || ''),
      // 仅主进程使用；不进入 publicConfig。
      modelCredentialsKey: String(saved.modelCredentialsKey || '').trim(),
    };
  } catch {
    return {
      remoteBaseUrl: DEFAULT_REMOTE,
      pdfDir: '',
      dataMode: defaultDataMode(),
      lastLoginEmail: '',
      autoLogin: false,
      autoLoginPassword: '',
      mcpPort: DEFAULT_MCP_PORT,
      mcpAccessToken: newServiceSecret(),
      mcpJwtSecret: newServiceSecret(),
      backupDir: '',
      backupIdentity: null,
      lastBackupAt: '',
      modelCredentialsKey: '',
    };
  }
}

function publicConfig(config) {
  return {
    remoteBaseUrl: config.remoteBaseUrl,
    pdfDir: config.pdfDir,
    dataMode: config.dataMode,
    lastLoginEmail: String(config.lastLoginEmail || ''),
    autoLogin: Boolean(config.autoLogin && config.autoLoginPassword && autoLoginAvailable()),
    autoLoginAvailable: autoLoginAvailable(),
    backupDir: backupDirectory(config),
    lastBackupAt: String(config.lastBackupAt || ''),
  };
}

function writeConfig(config) {
  const paths = userDataPaths();
  const prev = readConfig();
  const next = {
    remoteBaseUrl: String((config?.remoteBaseUrl ?? prev.remoteBaseUrl) || DEFAULT_REMOTE).replace(/\/$/, '') || DEFAULT_REMOTE,
    pdfDir: String(config?.pdfDir ?? prev.pdfDir ?? ''),
    dataMode: normalizeDataMode(config?.dataMode ?? prev.dataMode),
    lastLoginEmail: String(config?.lastLoginEmail ?? prev.lastLoginEmail ?? '').trim().toLowerCase(),
    autoLogin: Boolean(config?.autoLogin ?? prev.autoLogin),
    // 只由 saveLoginPreference 写入；普通设置保存始终沿用已有密文。
    autoLoginPassword: String(config?.autoLoginPassword ?? prev.autoLoginPassword ?? ''),
    mcpPort: normalizeMcpPort(config?.mcpPort ?? prev.mcpPort),
    // 仅 Electron 主进程写入；不会透传给渲染进程或前端配置界面。
    mcpAccessToken: normalizeServiceSecret(config?.mcpAccessToken ?? prev.mcpAccessToken),
    mcpJwtSecret: normalizeServiceSecret(config?.mcpJwtSecret ?? prev.mcpJwtSecret),
    backupDir: String(config?.backupDir ?? prev.backupDir ?? '').trim(),
    backupIdentity: config?.backupIdentity ?? prev.backupIdentity ?? null,
    lastBackupAt: String(config?.lastBackupAt ?? prev.lastBackupAt ?? ''),
    modelCredentialsKey: String(config?.modelCredentialsKey ?? prev.modelCredentialsKey ?? '').trim(),
  };
  fs.mkdirSync(paths.root, { recursive: true });
  fs.writeFileSync(paths.config, JSON.stringify(next, null, 2));
  return { ...publicConfig(next), ...publicDataInfo(next) };
}

function loginPreference() {
  const config = readConfig();
  const requested = Boolean(config.autoLogin && config.autoLoginPassword);
  const password = requested && autoLoginAvailable()
    ? decryptedLoginPassword(config.autoLoginPassword)
    : '';
  return {
    available: autoLoginAvailable(),
    enabled: Boolean(requested && password),
    email: String(config.lastLoginEmail || ''),
    password,
    unavailableReason: requested && !password
      ? '已保存的自动登录信息无法读取，请重新输入密码后登录。'
      : '',
  };
}

function saveLoginPreference(value) {
  const email = String(value?.email || '').trim().toLowerCase();
  const enabled = Boolean(value?.enabled);
  const password = String(value?.password || '');
  const accountId = String(value?.accountId || '');
  if (!email) throw new Error('请填写登录邮箱。');
  if (enabled && !autoLoginAvailable()) {
    throw new Error('当前系统无法使用安全凭据存储，不能启用自动登录。');
  }
  if (enabled && !password) throw new Error('自动登录需要保存密码。');
  const previous = readConfig();
  const backupIdentity = (password || accountId)
    ? { email, accountId: accountId || previous.backupIdentity?.accountId || '' }
    : previous.backupIdentity || { email, accountId: '' };
  const next = writeConfig({
    ...previous,
    lastLoginEmail: email,
    autoLogin: enabled,
    autoLoginPassword: enabled ? encryptedLoginPassword(password) : '',
    // 备份绑定稳定的账户身份，密码变更不会影响历史备份。
    backupIdentity,
  });
  scheduleBackups();
  return next;
}

function backupStamp(now = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function backupFilename(kind = 'scheduled') {
  const suffix = kind === 'pre-restore' ? '-恢复前' : '';
  return `youjulab-backup-${backupStamp()}${suffix}${process.platform === 'win32' ? '.zip' : '.tar.gz'}`;
}

async function sqliteSnapshot(source, target) {
  if (!fs.existsSync(source)) return false;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  // sqlite .backup 会处理 WAL，避免直接复制活跃数据库导致快照不一致。
  await execFileAsync('sqlite3', [source, `.backup '${target.replace(/'/g, "''")}'`]);
  return true;
}

async function archiveDirectory(source, target) {
  if (process.platform === 'win32') {
    await execFileAsync('powershell.exe', ['-NoProfile', '-Command', `Compress-Archive -Path '${source.replace(/'/g, "''")}\\*' -DestinationPath '${target.replace(/'/g, "''")}' -Force`]);
    return;
  }
  await execFileAsync('tar', ['-czf', target, '-C', source, '.']);
}

async function extractArchive(source, target) {
  fs.mkdirSync(target, { recursive: true });
  if (process.platform === 'win32') {
    await execFileAsync('powershell.exe', ['-NoProfile', '-Command', `Expand-Archive -Path '${source.replace(/'/g, "''")}' -DestinationPath '${target.replace(/'/g, "''")}' -Force`]);
    return;
  }
  await execFileAsync('tar', ['-xzf', source, '-C', target]);
}

function clearDir(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
  fs.mkdirSync(directory, { recursive: true });
}

async function createBackup(kind = 'scheduled') {
  const config = readConfig();
  const identity = config.backupIdentity;
  if (!identity?.email) throw new Error('请先成功登录一次，再启用数据备份。');
  const data = resolveDataPaths(config);
  const destination = backupDirectory(config);
  const work = backupPaths().work;
  const stage = path.join(work, `stage-${backupStamp()}`);
  const archive = path.join(destination, backupFilename(kind));
  fs.mkdirSync(destination, { recursive: true });
  clearDir(stage);
  try {
    const dataDir = path.join(stage, 'data');
    fs.mkdirSync(dataDir, { recursive: true });
    const files = {
      office: await sqliteSnapshot(data.sqlite, path.join(dataDir, 'office.sqlite')),
      vectors: await sqliteSnapshot(data.vectors, path.join(dataDir, 'office-vectors.sqlite')),
      finance: await sqliteSnapshot(data.finance, path.join(dataDir, 'finresearch.sqlite')),
    };
    if (fs.existsSync(data.files)) fs.cpSync(data.files, path.join(dataDir, 'office-files'), { recursive: true, force: true });
    const manifest = {
      version: BACKUP_VERSION,
      createdAt: new Date().toISOString(),
      kind,
      account: { email: identity.email, accountId: identity.accountId || '' },
      files,
    };
    fs.writeFileSync(path.join(stage, 'manifest.json'), JSON.stringify(manifest, null, 2));
    await archiveDirectory(stage, archive);
    writeConfig({ ...config, lastBackupAt: manifest.createdAt });
    return { id: path.basename(archive), name: path.basename(archive), createdAt: manifest.createdAt, kind, path: archive };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

async function readBackupRecord(file) {
  const work = backupPaths().work;
  const stage = path.join(work, `inspect-${randomBytes(8).toString('hex')}`);
  try {
    await extractArchive(file, stage);
    const manifest = JSON.parse(fs.readFileSync(path.join(stage, 'manifest.json'), 'utf8'));
    if (manifest?.version !== BACKUP_VERSION || !manifest?.account?.email) throw new Error('不是有效的有据数据备份。');
    return { id: path.basename(file), name: path.basename(file), createdAt: String(manifest.createdAt || ''), kind: String(manifest.kind || 'scheduled'), email: String(manifest.account.email || ''), path: file };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

async function listBackups(email = '') {
  const directory = backupDirectory();
  if (!fs.existsSync(directory)) return [];
  const requested = String(email || '').trim().toLowerCase();
  const entries = fs.readdirSync(directory, { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.startsWith('youjulab-backup-') && (entry.name.endsWith('.tar.gz') || entry.name.endsWith('.zip')))
    .map(entry => path.join(directory, entry.name));
  const records = [];
  for (const file of entries) {
    try {
      const record = await readBackupRecord(file);
      if (!requested || record.email === requested) records.push(record);
    } catch {
      // 无法读取的手工文件不作为可恢复记录展示。
    }
  }
  return records.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function scheduleBackups() {
  if (backupTimer) clearTimeout(backupTimer);
  const config = readConfig();
  if (!config.backupIdentity?.email || !config.backupDir) return;
  const now = new Date();
  const next = new Date(now);
  next.setSeconds(0, 0);
  const hours = [12, 18];
  const hour = hours.find(value => now.getHours() < value);
  if (hour === undefined) next.setDate(next.getDate() + 1);
  next.setHours(hour ?? 12, 0, 0, 0);
  backupTimer = setTimeout(() => {
    void createBackup('scheduled').catch(error => console.error('[youju] scheduled backup failed', error));
    scheduleBackups();
  }, Math.max(1_000, next.getTime() - Date.now()));
}

async function restoreBackup(value) {
  const id = path.basename(String(value?.id || ''));
  const email = String(value?.email || '').trim().toLowerCase();
  const accountId = String(value?.accountId || '');
  const records = await listBackups(email);
  const record = records.find(item => item.id === id);
  if (!record) throw new Error('找不到可恢复的备份记录。');
  const work = backupPaths().work;
  const stage = path.join(work, `restore-${randomBytes(8).toString('hex')}`);
  try {
    await extractArchive(record.path, stage);
    const manifest = JSON.parse(fs.readFileSync(path.join(stage, 'manifest.json'), 'utf8'));
    if (manifest?.account?.email !== email || (manifest?.account?.accountId && manifest.account.accountId !== accountId)) {
      throw new Error('当前账号与该备份不一致，无法恢复。');
    }
    await createBackup('pre-restore');
    const data = resolveDataPaths(readConfig());
    stopServices();
    const source = path.join(stage, 'data');
    for (const name of ['office.sqlite', 'office-vectors.sqlite', 'finresearch.sqlite']) {
      const from = path.join(source, name);
      const to = name === 'office.sqlite' ? data.sqlite : name === 'office-vectors.sqlite' ? data.vectors : data.finance;
      if (!fs.existsSync(from)) throw new Error(`备份缺少 ${name}。`);
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to);
      for (const suffix of ['-wal', '-shm']) fs.rmSync(`${to}${suffix}`, { force: true });
    }
    fs.rmSync(data.files, { recursive: true, force: true });
    if (fs.existsSync(path.join(source, 'office-files'))) fs.cpSync(path.join(source, 'office-files'), data.files, { recursive: true, force: true });
    return { ok: true, restartRequired: true };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
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
      finance: repo.finance,
      files: config.pdfDir || repo.files,
      root: repo.root,
    };
  }
  const ud = userDataPaths();
  return {
    mode: 'userData',
    sqlite: ud.sqlite,
    vectors: ud.vectors,
    finance: ud.finance,
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

function waitHealth(port, name, tries = 120) {
  return new Promise((resolve, reject) => {
    let left = tries;
    const tick = () => {
      const req = http.get(`http://127.0.0.1:${port}/health`, res => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else if (--left <= 0) reject(new Error(`${name} 健康检查失败`));
        else setTimeout(tick, 250);
      });
      req.on('error', () => {
        if (--left <= 0) reject(new Error(`${name} 未启动`));
        else setTimeout(tick, 250);
      });
    };
    tick();
  });
}

function portAvailable(port) {
  return new Promise(resolve => {
    const server = http.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => {
      server.close(() => resolve(true));
    });
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

function resolveMcpSidecarBinary() {
  if (process.env.YOUJU_MCP_SIDECAR) {
    const explicit = path.resolve(process.env.YOUJU_MCP_SIDECAR);
    if (fs.existsSync(explicit)) return explicit;
  }
  const name = process.platform === 'win32' ? 'youju-mcp-server.exe' : 'youju-mcp-server';
  const candidates = [
    path.join(process.resourcesPath || '', 'mcp-sidecar', 'youju-mcp-server', name),
    path.join(__dirname, 'mcp-sidecar', 'dist', 'youju-mcp-server', name),
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

function startMcp(port, officePort, config) {
  const data = resolveDataPaths(config);
  fs.mkdirSync(path.dirname(data.finance), { recursive: true });
  const env = {
    ...process.env,
    FINANCE_MCP_TRANSPORT: 'http',
    FINANCE_MCP_HOST: '127.0.0.1',
    FINANCE_MCP_PORT: String(port),
    OFFICE_API_URL: `http://127.0.0.1:${officePort}`,
    MCP_ACCESS_TOKEN: config.mcpAccessToken,
    ACCOUNT_JWT_SECRET: config.mcpJwtSecret,
    MCP_DESKTOP: '1',
    FINANCE_STORE: 'sqlite',
    FINANCE_SQLITE_PATH: data.finance,
  };
  const sidecar = resolveMcpSidecarBinary();
  let command;
  let args;
  let cwd;
  if (sidecar) {
    console.log(`[youju] mcp=sidecar ${sidecar}`);
    command = sidecar;
    args = [];
    cwd = path.dirname(sidecar);
  } else {
    if (!repoRoot) {
      throw new Error('未找到 MCP Python sidecar，也找不到仓库。请先运行 npm run build:mcp-sidecar，或设置 YOUJU_REPO_ROOT / YOUJU_PYTHON。');
    }
    command = resolvePython();
    args = [path.join(repoRoot, 'mcp-server', 'server.py')];
    cwd = repoRoot;
    console.log(`[youju] mcp=python ${command}`);
  }
  mcp = spawn(command, args, {
    cwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  mcp.stdout?.on('data', chunk => process.stdout.write(`[mcp-server] ${chunk}`));
  mcp.stderr?.on('data', chunk => process.stderr.write(`[mcp-server] ${chunk}`));
  mcp.on('exit', code => {
    console.error(`mcp-server exited: ${code}`);
    mcp = null;
  });
  return mcp;
}

async function selectMcpPort(config) {
  const requested = normalizeMcpPort(config.mcpPort);
  if (await portAvailable(requested)) return requested;
  const fallback = await pickPort();
  console.warn(`[youju] MCP port ${requested} is occupied, using ${fallback}`);
  writeConfig({ ...config, mcpPort: fallback });
  config.mcpPort = fallback;
  return fallback;
}

function startBackend(port, config, localMcpBase) {
  const data = resolveDataPaths(config);
  fs.mkdirSync(path.dirname(data.sqlite), { recursive: true });
  fs.mkdirSync(path.dirname(data.vectors), { recursive: true });
  fs.mkdirSync(data.files, { recursive: true });
  const webDist = webDistDir();
  const modelKey = ensureModelCredentialsKey(config);
  // 桌面默认走用户自有模型（设置页可配）。若启动环境已显式设置则尊重之。
  const llmMode = String(process.env.LLM_CONFIG_SET || readRepoDotEnv('LLM_CONFIG_SET') || '1').trim() || '1';
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
    LLM_CONFIG_SET: llmMode,
    OFFICE_MODEL_CREDENTIALS_KEY: modelKey,
    // 桌面版的账户、持仓和行情服务都由同一 MCP 子进程提供，不能回落到历史默认的 8000。
    MCP_REMOTE_BASE_URL: localMcpBase,
    MCP_LOCAL_BASE_URL: localMcpBase,
    MCP_ACCESS_TOKEN: config.mcpAccessToken,
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
ipcMain.on('youju:mcp-api-base', event => {
  event.returnValue = mcpPort ? `http://127.0.0.1:${mcpPort}` : '';
});
ipcMain.handle('youju:get-config', () => {
  const config = readConfig();
  return { ...publicConfig(config), ...publicDataInfo(config) };
});
ipcMain.handle('youju:set-config', (_event, config) => writeConfig(config));
ipcMain.handle('youju:get-login-preference', () => loginPreference());
ipcMain.handle('youju:save-login-preference', (_event, preference) => saveLoginPreference(preference));
ipcMain.handle('youju:backup-status', async (_event, email) => ({
  directory: backupDirectory(),
  lastBackupAt: String(readConfig().lastBackupAt || ''),
  records: await listBackups(email),
}));
ipcMain.handle('youju:set-backup-directory', (_event, directory) => {
  const value = String(directory || '').trim();
  if (!value) throw new Error('请填写数据备份目录。');
  const next = writeConfig({ ...readConfig(), backupDir: path.resolve(value) });
  scheduleBackups();
  return next;
});
ipcMain.handle('youju:create-backup', async () => createBackup('manual'));
ipcMain.handle('youju:restore-backup', async (_event, value) => restoreBackup(value));
ipcMain.handle('youju:quit-after-restore', () => app.quit());

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
  // 首次启动及旧版配置迁移时写入 MCP 端口与本机服务密钥；不会动业务数据。
  writeConfig(readConfig());
  let config = readConfig();

  backendPort = await pickPort();
  mcpPort = await selectMcpPort(config);
  const localMcpBase = `http://127.0.0.1:${mcpPort}`;
  startMcp(mcpPort, backendPort, config);
  await waitHealth(mcpPort, 'mcp-server');
  startBackend(backendPort, config, localMcpBase);
  await waitHealth(backendPort, 'office-backend');
  scheduleBackups();

  const icon = appIcon();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    ...(icon ? { icon } : {}),
    webPreferences: {
      // CJS preload：Electron sandbox 下 ESM preload 可能静默失败，导致 youju 未注入。
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  if (icon && process.platform === 'darwin' && app.dock) {
    app.dock.setIcon(icon);
  }
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

function stopServices() {
  if (backupTimer) {
    clearTimeout(backupTimer);
    backupTimer = null;
  }
  if (backend) {
    backend.kill('SIGTERM');
    backend = null;
  }
  if (mcp) {
    mcp.kill('SIGTERM');
    mcp = null;
  }
  backendPort = 0;
  mcpPort = 0;
}

app.whenReady().then(() => createWindow().catch(err => {
  console.error(err);
  dialog.showErrorBox('youjulab 启动失败', String(err?.message || err));
  app.quit();
}));

app.on('window-all-closed', () => {
  stopServices();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => stopServices());

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void createWindow();
  }
});
