import { app, BrowserWindow, dialog, globalShortcut, ipcMain, nativeImage, safeStorage, screen, Tray, Menu, shell } from 'electron';
import { DEFAULT_QUICK_SHORTCUTS, QUICK_LABELS, QUICK_MODES, quickMenuItems, readQuickShortcuts, validateQuickShortcuts } from './quick-capture.mjs';
import { execFile } from 'node:child_process';
import { ServiceProcesses } from './service-processes.mjs';
import { DEFAULT_REMOTE, remoteAddress, savedRemoteAddress } from './remote-config.mjs';
import { BackupSession, backupUserKey } from './backup-session.mjs';
import { createBackgroundTray, hideOnClose } from './background.mjs';
import { UpdateController } from './update-controller.mjs';
import { fileDigest, remoteBackupList, uploadBackup, downloadBackup, chooseBackup } from './backup-transfer.mjs';
import { randomBytes } from 'node:crypto';
import { promisify } from 'node:util';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = process.argv.includes('--dev') || !app.isPackaged;
// Keep development and installed builds on the same stable OS user-data directory.
app.setPath('userData', path.join(app.getPath('appData'), 'youjulab-desktop'));
const singleInstance = app.requestSingleInstanceLock();
const exitOnly = process.argv.includes('--quit');
if (!singleInstance || exitOnly) app.quit();

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
let tray = null;
/** 托盘快速记录小窗：mode -> BrowserWindow */
const quickWindows = new Map();
let shortcutFailures = [];
let updateController = null;
let openingWindow = null;
let backend = null;
let backendPort = 0;
let backupTimer = null;
const serviceProcesses = new ServiceProcesses();
let stoppingServices = null;
let quitRequested = false;
let quitReady = false;
let startupGeneration = 0;

const execFileAsync = promisify(execFile);

/** 固定本机 office-backend 端口，保证打包态同源，localStorage（上次页面）才能跨重启保留。 */
const DEFAULT_OFFICE_PORT = 8100;
const BACKUP_VERSION = 2;
const backupSession = new BackupSession(() => readConfig().remoteBaseUrl);
let backupBusy = false;
let restoreBusy = false;
let backupProgress = null;

function emitBackupProgress(progress) {
  backupProgress = { userKey: backupSession.identity ? backupUserKey(backupSession.identity) : '', progress };
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('youju:backup-progress', progress);
}
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

function backupPreferences(identity, config = readConfig()) {
  const saved = config.backupUsers?.[backupUserKey(identity)];
  return { directory: saved?.directory || backupDirectory(config),
    enabled: saved?.enabled ?? (config.backupEnabled !== false) };
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

function normalizeOfficePort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : DEFAULT_OFFICE_PORT;
}

function newServiceSecret() {
  return randomBytes(32).toString('base64url');
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
      remoteBaseUrl: savedRemoteAddress(saved),
      remoteServiceVersion: 1,
      deviceId: /^[a-f0-9]{32}$/.test(saved.deviceId || '') ? saved.deviceId : randomBytes(16).toString('hex'),
      pdfDir: String(saved.pdfDir || ''),
      dataMode: normalizeDataMode(saved.dataMode),
      // 仅主进程读取此字段；密码密文不会经 publicConfig 发送到渲染进程。
      lastLoginEmail: String(saved.lastLoginEmail || '').trim().toLowerCase(),
      autoLogin: Boolean(saved.autoLogin),
      autoLoginPassword: String(saved.autoLoginPassword || ''),
      officePort: normalizeOfficePort(saved.officePort),
      quickShortcuts: readQuickShortcuts(saved.quickShortcuts),
      backupDir: String(saved.backupDir || ''),
      backupEnabled: saved.backupEnabled !== false,
      backupIdentity: saved.backupIdentity && typeof saved.backupIdentity === 'object'
        ? { email: String(saved.backupIdentity.email || '').trim().toLowerCase(), accountId: String(saved.backupIdentity.accountId || '') }
        : (saved.backupCredential?.email ? { email: String(saved.backupCredential.email).trim().toLowerCase(), accountId: '' } : null),
      lastBackupAt: String(saved.lastBackupAt || ''),
      backupTimes: saved.backupTimes && typeof saved.backupTimes === 'object' ? saved.backupTimes : {},
      backupUsers: saved.backupUsers && typeof saved.backupUsers === 'object' ? saved.backupUsers : {},
      // 仅主进程使用；不进入 publicConfig。
      modelCredentialsKey: String(saved.modelCredentialsKey || '').trim(),
    };
  } catch {
    return {
      remoteBaseUrl: DEFAULT_REMOTE,
      deviceId: randomBytes(16).toString('hex'),
      pdfDir: '',
      dataMode: defaultDataMode(),
      lastLoginEmail: '',
      autoLogin: false,
      autoLoginPassword: '',
      officePort: DEFAULT_OFFICE_PORT,
      quickShortcuts: { ...DEFAULT_QUICK_SHORTCUTS },
      backupDir: '',
      backupEnabled: true,
      backupIdentity: null,
      lastBackupAt: '',
      backupTimes: {},
      backupUsers: {},
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
    backupEnabled: config.backupEnabled !== false,
    lastBackupAt: String(config.lastBackupAt || ''),
  };
}

function writeConfig(config) {
  const paths = userDataPaths();
  const prev = readConfig();
  const next = {
    remoteBaseUrl: remoteAddress(config?.remoteBaseUrl ?? prev.remoteBaseUrl),
    remoteServiceVersion: 1,
    deviceId: config?.deviceId ?? prev.deviceId,
    pdfDir: String(config?.pdfDir ?? prev.pdfDir ?? ''),
    dataMode: normalizeDataMode(config?.dataMode ?? prev.dataMode),
    lastLoginEmail: String(config?.lastLoginEmail ?? prev.lastLoginEmail ?? '').trim().toLowerCase(),
    autoLogin: Boolean(config?.autoLogin ?? prev.autoLogin),
    // 只由 saveLoginPreference 写入；普通设置保存始终沿用已有密文。
    autoLoginPassword: String(config?.autoLoginPassword ?? prev.autoLoginPassword ?? ''),
    officePort: normalizeOfficePort(config?.officePort ?? prev.officePort),
    quickShortcuts: readQuickShortcuts(config?.quickShortcuts ?? prev.quickShortcuts),
    backupDir: String(config?.backupDir ?? prev.backupDir ?? '').trim(),
    backupEnabled: config?.backupEnabled !== false,
    backupIdentity: config?.backupIdentity ?? prev.backupIdentity ?? null,
    lastBackupAt: String(config?.lastBackupAt ?? prev.lastBackupAt ?? ''),
    backupTimes: config?.backupTimes ?? prev.backupTimes ?? {},
    backupUsers: config?.backupUsers ?? prev.backupUsers ?? {},
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
  return `youjulab-backup-${backupStamp()}-${randomBytes(4).toString('hex')}${suffix}${process.platform === 'win32' ? '.zip' : '.tar.gz'}`;
}

async function userBackupCommand(operation, data, identity, directory) {
  const params = [operation, '--office', data.sqlite, '--vectors', data.vectors,
    '--files', data.files, '--email', identity.email, '--account-id', identity.id, '--directory', directory];
  const sidecar = resolveSidecarBinary();
  const command = sidecar || resolvePython();
  const args = sidecar ? ['--user-backup', ...params]
    : [path.join(repoRoot, 'apps', 'office-backend', 'user_backup.py'), ...params];
  const result = await execFileAsync(command, args, { timeout: 600000 });
  return JSON.parse(result.stdout);
}

function userBackupDirectory(identity) {
  return path.join(backupPreferences(identity).directory, backupUserKey(identity));
}


async function backupArchiveCommand(flag, source, target) {
  const sidecar = resolveSidecarBinary();
  const args = sidecar ? [flag, source, target]
    : [path.join(repoRoot, 'apps', 'office-backend', 'desktop_data.py'), flag, source, target];
  await execFileAsync(sidecar || resolvePython(), args, { timeout: 600000 });
}

async function archiveDirectory(source, target) {
  await backupArchiveCommand('--archive-backup', source, target);
}

async function extractArchive(source, target) {
  await backupArchiveCommand('--extract-backup', source, target);
}

function clearDir(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
}

function adminBase() {
  const metadata = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8'));
  return metadata.adminBaseUrl || metadata.releaseBaseUrl
    || (isDev ? process.env.YOUJU_ADMIN_BASE_URL || readRepoDotEnv('YOUJU_ADMIN_BASE_URL') : '') || '';
}

async function catalog(identity, operation, record = {}) {
  const data = resolveDataPaths();
  const payload = { ...record, userKey: backupUserKey(identity), deviceId: readConfig().deviceId };
  const args = ['--database', data.sqlite, '--operation', operation, '--payload', JSON.stringify(payload)];
  const sidecar = resolveSidecarBinary();
  const result = await execFileAsync(sidecar || resolvePython(), sidecar ? ['--backup-catalog', ...args]
    : [path.join(repoRoot, 'apps', 'office-backend', 'backup_catalog.py'), ...args], { timeout: 60000 });
  return JSON.parse(result.stdout);
}

async function syncBackupRecords(identity, report = () => {}) {
  let records = await catalog(identity, 'list');
  // Index existing local archives once, without uploading or rewriting legacy archives.
  const directory = userBackupDirectory(identity);
  if (fs.existsSync(directory)) {
    const known = new Set(records.filter(r => ['local','synced','remote','upload_failed'].includes(r.state)).map(r => r.name));
    const files = fs.readdirSync(directory).filter(n => n.startsWith('youjulab-backup-')
      && /\\.(zip|tar\\.gz)$/.test(n) && !known.has(n)).sort();
    for (const name of files) {
      try {
        const old = await readBackupRecord(path.join(directory, name));
        if (old.email !== identity.email || old.accountId !== identity.id || old.server !== identity.server) continue;
        const digest = await fileDigest(old.path);
        const row = old.backupId && old.clientVersion
          ? { ...old, id: old.backupId, version: old.clientVersion, legacy: false }
          : { ...old, ...await catalog(identity, 'reserve', { id: 'legacy-' + randomBytes(16).toString('hex') }), legacy: true };
        await catalog(identity, 'save', { ...row, ...digest, state: 'local', remote: false });
      } catch { /* Invalid or foreign archives are not offered for restore. */ }
    }
  }
  let warning = '';
  if (adminBase()) {
    report({ phase: 'checking-remote', percent: 8, message: '正在核对服务器备份版本…' });
    try {
      const remote = await remoteBackupList(adminBase(), backupSession.token, readConfig().deviceId);
      records = await catalog(identity, 'list');
      for (const row of remote) {
        if (row.device_id !== readConfig().deviceId) throw new Error('服务器返回了其他设备的备份记录。');
        const local = records.find(r => r.id === row.id);
        if (local && (local.version !== row.client_version || local.sha256 !== row.sha256 || local.size !== row.size)) {
          throw new Error('本地记录与服务器校验值不一致，请联系管理员。');
        }
        await catalog(identity, 'save', { ...local, id: row.id, name: row.name, version: row.client_version,
          createdAt: row.created_at, kind: row.kind, size: row.size, sha256: row.sha256,
          remote: true, state: local?.path && fs.existsSync(local.path) ? 'synced' : 'remote' });
      }
    } catch (error) { warning = '暂时无法核对服务器备份：' + String(error.message || error); }
  } else { warning = '尚未配置在线管理地址，目前仅使用本地备份。'; }
  records = (await catalog(identity, 'list')).filter(r => ['local','synced','remote','upload_failed'].includes(r.state));
  return { records, warning };
}

async function syncOneBackup(identity, record, report = () => {}) {
  if (record.legacy) throw new Error('旧备份保留为本地历史记录，请新建一次备份后上传。');
  const actual = await fileDigest(record.path);
  if (actual.sha256 !== record.sha256 || actual.size !== record.size) throw new Error('本地备份文件已变化，请重新创建备份。');
  await uploadBackup(adminBase(), backupSession.token, record, percent => report({
    phase: 'uploading', percent: 72 + percent * .26, message: '本地备份已保存，正在上传服务器（' + Math.round(percent) + '%）…' }));
  return catalog(identity, 'save', { ...record, state: 'synced', remote: true, uploadError: '' });
}

async function createBackup(kind = 'scheduled', report = emitBackupProgress, identity = null) {
  if (backupBusy || (restoreBusy && kind !== 'pre-restore')) throw new Error('备份或恢复正在进行，请稍候。');
  identity ||= await backupSession.current();
  if (backupBusy || (restoreBusy && kind !== 'pre-restore')) throw new Error('备份或恢复正在进行，请稍候。');
  backupBusy = true;
  const data = resolveDataPaths();
  const stage = path.join(backupPaths().work, 'stage-' + randomBytes(12).toString('hex'));
  const destination = userBackupDirectory(identity);
  const archive = path.join(destination, backupFilename(kind));
  let record, localSaved = false;
  try {
    await syncBackupRecords(identity, report);
    record = await catalog(identity, 'reserve', { id: randomBytes(16).toString('hex'),
      name: path.basename(archive), path: archive, createdAt: new Date().toISOString(), kind });
    fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
    clearDir(stage);
    report({ phase: 'office', percent: 15, message: '正在整理当前账号的本地数据、PDF 和索引…' });
    const result = await userBackupCommand('export', data, identity, path.join(stage, 'data'));
    const manifest = { version: BACKUP_VERSION, scope: 'local-user', createdAt: record.createdAt,
      kind, server: identity.server, account: { email: identity.email, accountId: identity.id },
      localUserId: result.localUserId, backupId: record.id, deviceId: record.deviceId, clientVersion: record.version };
    fs.writeFileSync(path.join(stage, 'manifest.json'), JSON.stringify(manifest, null, 2));
    report({ phase: 'archiving', percent: 45, message: '正在压缩备份并保存到本地目录…' });
    await archiveDirectory(stage, archive);
    fs.chmodSync(archive, 0o600);
    const current = await backupSession.current();
    if (backupUserKey(current) !== backupUserKey(identity)) throw new Error('账号已切换，本次备份已取消。');
    report({ phase: 'verifying-local', percent: 65, message: '本地文件已保存，正在计算文件校验值…' });
    localSaved = true;
    record = await catalog(identity, 'save', { ...record, ...await fileDigest(archive), state: 'local', remote: false });
    const latest = readConfig();
    writeConfig({ ...latest, backupTimes: { ...latest.backupTimes, [backupUserKey(identity)]: record.createdAt } });
    let warning = '';
    try {
      record = await syncOneBackup(identity, record, report);
    } catch (error) {
      warning = '本地备份已成功保存，但上传尚未完成。' + String(error.message || error);
      record = await catalog(identity, 'save', { ...record, state: 'upload_failed', uploadError: warning });
    }
    report({ phase: warning ? 'warning' : 'done', percent: 100,
      message: warning || '备份完成：本地与服务器均已保存。' });
    return { ...record, warning };
  } catch (error) {
    if (!localSaved) {
      fs.rmSync(archive, { force: true });
      if (record) await catalog(identity, 'save', { ...record, state: 'failed' }).catch(() => {});
    }
    report({ phase: 'error', percent: 0, message: String(error?.message || error) });
    throw error;
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
    backupBusy = false;
  }
}

async function readBackupRecord(file) {
  const work = backupPaths().work;
  const stage = path.join(work, `inspect-${randomBytes(8).toString('hex')}`);
  try {
    await extractArchive(file, stage);
    const manifest = JSON.parse(fs.readFileSync(path.join(stage, 'manifest.json'), 'utf8'));
    if (manifest?.version !== BACKUP_VERSION || manifest.scope !== 'local-user' || !manifest?.account?.email) throw new Error('不是用户隔离备份。');
    return { id: path.basename(file), name: path.basename(file), createdAt: String(manifest.createdAt || ''), kind: String(manifest.kind || 'scheduled'), email: String(manifest.account.email || ''), accountId: String(manifest.account.accountId || ''), server: String(manifest.server || ''), path: file, backupId: manifest.backupId, clientVersion: manifest.clientVersion };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

function scheduleBackups() {
  if (backupTimer) clearTimeout(backupTimer);
  const config = readConfig();
  const identity = backupSession.identity;
  if (!identity || !backupSession.token || !backupPreferences(identity, config).enabled) return;
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

async function restoreBackup(value, report = () => {}) {
  if (backupBusy || restoreBusy) throw new Error('备份或恢复正在进行，请稍候。');
  report({ phase: 'authenticating', percent: 2, message: '正在验证当前账号…' });
  const identity = await backupSession.current();
  const confirmed = await backupSession.verify(String(value?.token || ''));
  if (confirmed.id !== identity.id || confirmed.email !== identity.email || confirmed.server !== identity.server) {
    throw new Error('恢复验证账号与当前登录账号不一致。');
  }
  const status = await syncBackupRecords(identity, report);
  if (value?.id === 'latest' && adminBase() && status.warning) {
    throw new Error('暂时无法确认服务器最新备份，请联网后重试；也可以明确选择一份本地历史备份。');
  }
  let record = chooseBackup(status.records, String(value?.id || 'latest'));
  const stage = path.join(backupPaths().work, 'restore-' + randomBytes(12).toString('hex'));
  if (restoreBusy || backupBusy) throw new Error('备份或恢复正在进行，请稍候。');
  restoreBusy = true;
  try {
    report({ phase: 'checking-local', percent: 15, message: '正在核对本地备份文件是否完整…' });
    let usable = false;
    if (record.path && fs.existsSync(record.path)) {
      const digest = await fileDigest(record.path);
      usable = digest.sha256 === record.sha256 && digest.size === record.size;
    }
    if (!usable) {
      if (!record.remote) throw new Error('本地备份缺失或损坏，且没有服务器副本，已停止恢复。');
      const directory = userBackupDirectory(identity);
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
      const target = path.join(directory, record.name);
      report({ phase: 'downloading', percent: 20, message: '本地没有完整的所选版本，正在从服务器下载…' });
      await downloadBackup(adminBase(), backupSession.token, record, target, percent => report({
        phase: 'downloading', percent: 20 + percent * .3, message: '正在下载并校验服务器备份（' + Math.round(percent) + '%）…' }));
      record = await catalog(identity, 'save', { ...record, path: target, state: 'synced' });
    } else {
      report({ phase: 'using-local', percent: 50, message: '本地备份已通过校验，将直接使用，无需重复下载。' });
    }
    report({ phase: 'extracting', percent: 55, message: '正在解压并核对备份归属…' });
    await extractArchive(record.path, stage);
    const manifest = JSON.parse(fs.readFileSync(path.join(stage, 'manifest.json'), 'utf8'));
    if (manifest.version !== BACKUP_VERSION || manifest.scope !== 'local-user'
      || manifest.account.email !== identity.email || manifest.account.accountId !== identity.id
      || manifest.server !== identity.server) throw new Error('备份不属于当前登录用户。');
    if (!record.legacy && (manifest.backupId !== record.id || manifest.clientVersion !== record.version || manifest.deviceId !== readConfig().deviceId)) {
      throw new Error('备份内容与本地记录不一致，已停止恢复。');
    }
    report({ phase: 'pre-restore', percent: 62, message: '正在保存恢复前备份，保障当前数据可回退…' });
    await createBackup('pre-restore', progress => report({ ...progress, percent: 62 + progress.percent * .2,
      message: '恢复前保护：' + progress.message }), identity);
    const current = await backupSession.current();
    if (backupUserKey(current) !== backupUserKey(identity)) throw new Error('账号已切换，本次恢复已取消。');
    backupBusy = true;
    report({ phase: 'restoring', percent: 85, message: '正在停止本地服务并恢复当前账号数据，其他账号不受影响…' });
    await stopServices();
    await userBackupCommand('restore', resolveDataPaths(), identity, path.join(stage, 'data'));
    report({ phase: 'done', percent: 100, message: '恢复完成，应用即将重新启动。' });
    return { ok: true, restartRequired: true };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
    backupBusy = false;
    restoreBusy = false;
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

/** 打包进 Resources 的 PyInstaller onedir 可执行文件。开发态（--dev）默认跳过，直接跑仓库源码。 */
function resolveSidecarBinary() {
  if (process.env.YOUJU_SIDECAR) {
    const explicit = path.resolve(process.env.YOUJU_SIDECAR);
    if (fs.existsSync(explicit)) return explicit;
  }
  // 纯壳开发：改 Python 立刻生效；需要验证 sidecar 时设 YOUJU_USE_SIDECAR=1
  if (isDev && process.env.YOUJU_USE_SIDECAR !== '1') return null;
  const name = process.platform === 'win32' ? 'youju-office-backend.exe' : 'youju-office-backend';
  const candidates = [
    path.join(process.resourcesPath || '', 'sidecar', 'youju-office-backend', name),
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
    const venvPy = path.join(repoRoot, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
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


async function selectOfficePort(config) {
  const requested = normalizeOfficePort(config.officePort);
  if (await portAvailable(requested)) return requested;
  const fallback = await pickPort();
  console.warn(`[youju] office port ${requested} is occupied, using ${fallback}`);
  writeConfig({ ...config, officePort: fallback });
  config.officePort = fallback;
  return fallback;
}

function startBackend(port, config) {
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
    OFFICE_DESKTOP_CONFIG: userDataPaths().config,
    LLM_CONFIG_SET: llmMode,
    OFFICE_MODEL_CREDENTIALS_KEY: modelKey,
    MCP_REMOTE_BASE_URL: config.remoteBaseUrl,
    MCP_LOCAL_BASE_URL: config.remoteBaseUrl,
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

  backend = serviceProcesses.spawn(command, args, {
    cwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout?.on('data', chunk => process.stdout.write(`[office-backend] ${chunk}`));
  backend.stderr?.on('data', chunk => process.stderr.write(`[office-backend] ${chunk}`));
  const child = backend;
  backend.on('exit', code => {
    console.error(`office-backend exited: ${code}`);
    if (backend === child) backend = null;
  });
  return backend;
}

ipcMain.on('youju:version', event => { event.returnValue = app.getVersion(); });
ipcMain.on('youju:office-api-base', event => {
  event.returnValue = backendPort ? `http://127.0.0.1:${backendPort}` : '';
});
ipcMain.on('youju:mcp-api-base', event => {
  event.returnValue = readConfig().remoteBaseUrl;
});
ipcMain.handle('youju:get-config', () => {
  const config = readConfig();
  return { ...publicConfig(config), ...publicDataInfo(config) };
});
ipcMain.handle('youju:set-config', (_event, config) => writeConfig(config));
ipcMain.handle('youju:get-login-preference', () => loginPreference());
ipcMain.handle('youju:save-login-preference', (_event, preference) => saveLoginPreference(preference));
ipcMain.handle('youju:set-backup-session', async (_event, token) => {
  backupSession.set(token);
  if (token) await backupSession.current();
  scheduleBackups();
});
ipcMain.handle('youju:backup-status', async () => {
  const identity = await backupSession.current();
  const preferences = backupPreferences(identity);
  const status = await syncBackupRecords(identity);
  return { directory: preferences.directory, enabled: preferences.enabled,
    lastBackupAt: String(readConfig().backupTimes[backupUserKey(identity)] || ''),
    records: status.records.map(row => ({...row,localAvailable: Boolean(row.path && fs.existsSync(row.path))})), warning: status.warning,
    progress: backupProgress?.userKey === backupUserKey(identity) ? backupProgress.progress : null };
});
ipcMain.handle('youju:set-backup-directory', async (_event, directory) => {
  const identity = await backupSession.current();
  const value = String(directory || '').trim();
  if (!value) throw new Error('请填写数据备份目录。');
  const config = readConfig();
  const next = writeConfig({ ...config, backupUsers: { ...config.backupUsers,
    [backupUserKey(identity)]: { ...backupPreferences(identity), directory: path.resolve(value) } } });
  scheduleBackups();
  return next;
});
ipcMain.handle('youju:set-backup-schedule-enabled', async (_event, enabled) => {
  const identity = await backupSession.current();
  const config = readConfig();
  const next = writeConfig({ ...config, backupUsers: { ...config.backupUsers,
    [backupUserKey(identity)]: { ...backupPreferences(identity), enabled: Boolean(enabled) } } });
  scheduleBackups();
  return next;
});
ipcMain.handle('youju:create-backup', async () => createBackup('manual'));
ipcMain.handle('youju:retry-backup-upload', async (_event, id) => {
  if (backupBusy || restoreBusy) throw new Error('已有备份操作正在进行，请稍候。');
  const identity = await backupSession.current();
  const record = (await catalog(identity,'list')).find(row => row.id === id);
  if (!record) throw new Error('未找到当前账号的本地备份。');
  if (backupBusy || restoreBusy) throw new Error('已有备份操作正在进行，请稍候。');
  backupBusy = true;
  try {
    const result = await syncOneBackup(identity,record,emitBackupProgress);
    emitBackupProgress({phase:'done',percent:100,message:'上传成功，本地与服务器备份已一致。'});
    return result;
  } catch(error) {
    emitBackupProgress({phase:'error',percent:0,message:'上传未完成，本地文件仍保留。'+String(error.message || error)});
    throw error;
  } finally { backupBusy = false; }
});
ipcMain.handle('youju:restore-backup', async (_event, value) => {
  try { return await restoreBackup(value,emitBackupProgress); }
  catch(error) {
    emitBackupProgress({phase:'error',percent:0,message:String(error.message || error)});
    throw error;
  }
});
ipcMain.handle('youju:quit-after-restore', () => {
  // 恢复会替换正在使用的 SQLite 文件；由新进程重新启动全部本地服务。
  app.relaunch();
  app.quit();
});

// ---- 托盘快速记录：小窗、跨窗口同步、全局快捷键 ----

function quickWindowUrl(mode) {
  const query = `mode=${encodeURIComponent(mode)}`;
  return isDev
    ? `http://127.0.0.1:5173/quick.html?${query}&officePort=${backendPort}`
    : `http://127.0.0.1:${backendPort}/quick.html?${query}`;
}

function quickWindowBounds(width, height) {
  const trayBounds = tray?.getBounds?.();
  const anchor = trayBounds && trayBounds.width
    ? { x: trayBounds.x + Math.round(trayBounds.width / 2), y: trayBounds.y + Math.round(trayBounds.height / 2) }
    : screen.getCursorScreenPoint();
  const area = screen.getDisplayNearestPoint(anchor).workArea;
  if (!trayBounds?.width) {
    return { x: Math.round(area.x + (area.width - width) / 2), y: Math.round(area.y + (area.height - height) / 3) };
  }
  // 托盘在顶部（macOS）则窗口贴在图标下方，在底部（Windows）则在上方
  const below = anchor.y < area.y + area.height / 2;
  const x = Math.min(Math.max(anchor.x - Math.round(width / 2), area.x + 8), area.x + area.width - width - 8);
  const y = below ? area.y + 8 : area.y + area.height - height - 8;
  return { x, y };
}

function openQuickWindow(mode, { toggle = false } = {}) {
  if (!app.isReady() || quitRequested || !QUICK_MODES.includes(mode)) return;
  // 本机服务尚未就绪（启动中或已停止）时先走主程序启动流程
  if (!backendPort) {
    showApplication();
    return;
  }
  const existing = quickWindows.get(mode);
  if (existing && !existing.isDestroyed()) {
    if (toggle && existing.isVisible() && existing.isFocused()) {
      existing.close();
      return;
    }
    if (process.platform === 'darwin') app.focus({ steal: true });
    existing.show();
    existing.focus();
    return;
  }
  const width = 420;
  const height = mode === 'todos' ? 520 : 420;
  const icon = appIcon();
  const win = new BrowserWindow({
    width, height, minWidth: 320, minHeight: 280,
    ...quickWindowBounds(width, height),
    title: `${QUICK_LABELS[mode]} · 有据`,
    show: false,
    alwaysOnTop: true,
    fullscreenable: false,
    maximizable: false,
    minimizable: false,
    skipTaskbar: true,
    autoHideMenuBar: true,
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  if (process.platform === 'darwin') win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  quickWindows.set(mode, win);
  win.on('closed', () => { if (quickWindows.get(mode) === win) quickWindows.delete(mode); });
  win.once('ready-to-show', () => {
    if (process.platform === 'darwin') app.focus({ steal: true });
    win.show();
    win.focus();
  });
  void win.loadURL(quickWindowUrl(mode)).catch(error => console.error('[youju] quick window failed', error));
}

function sendToMainWindow(channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const contents = mainWindow.webContents;
  if (contents.isLoading()) contents.once('did-finish-load', () => contents.send(channel, payload));
  else contents.send(channel, payload);
}

function shortcutState(config = readConfig()) {
  return { shortcuts: config.quickShortcuts, defaults: { ...DEFAULT_QUICK_SHORTCUTS }, failed: [...shortcutFailures] };
}

function registerQuickShortcuts(config = readConfig()) {
  globalShortcut.unregisterAll();
  const failed = [];
  for (const mode of QUICK_MODES) {
    const accelerator = config.quickShortcuts?.[mode];
    if (!accelerator) continue;
    let ok = false;
    try { ok = globalShortcut.register(accelerator, () => openQuickWindow(mode, { toggle: true })); }
    catch (error) { console.warn('[youju] shortcut register failed', accelerator, error); }
    if (!ok) failed.push(mode);
  }
  shortcutFailures = failed;
  return shortcutState(config);
}

function trayQuickItems() {
  const items = quickMenuItems(readConfig().quickShortcuts, shortcutFailures, mode => openQuickWindow(mode));
  return [...items, { type: 'separator' }, { label: '打开有据', click: showApplication }];
}

ipcMain.on('youju:notify-asset-changed', (event, change) => {
  if (!change || typeof change.id !== 'string' || !['notebook', 'calendar'].includes(change.kind)) return;
  const payload = { id: change.id, kind: change.kind, revision: Number(change.revision) || 0 };
  for (const win of BrowserWindow.getAllWindows()) {
    if (win.isDestroyed() || win.webContents === event.sender) continue;
    win.webContents.send('youju:asset-changed', payload);
  }
});
ipcMain.on('youju:open-main-tool', (_event, tool) => {
  showApplication();
  if (tool === 'notebook' || tool === 'todos') sendToMainWindow('youju:open-main-tool', tool);
});
ipcMain.handle('youju:get-shortcuts', () => shortcutState());
ipcMain.handle('youju:set-shortcuts', (_event, input) => {
  const quickShortcuts = validateQuickShortcuts(input);
  writeConfig({ ...readConfig(), quickShortcuts });
  return registerQuickShortcuts();
});


async function createWindow() {
  if (stoppingServices) await stoppingServices;
  if (quitRequested) return;
  const generation = ++startupGeneration;
  const waitForService = async (port, name) => {
    try { await waitHealth(port, name); }
    catch (error) {
      if (generation !== startupGeneration || quitRequested) return false;
      throw error;
    }
    return generation === startupGeneration && !quitRequested;
  };
  // Persist the remote server address without modifying business data.
  writeConfig(readConfig());
  let config = readConfig();

  backendPort = await selectOfficePort(config);
  if (generation !== startupGeneration || quitRequested) return;
  startBackend(backendPort, config);
  if (!await waitForService(backendPort, 'office-backend')) return;
  scheduleBackups();

  const icon = appIcon();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    autoHideMenuBar: true,
    ...(icon ? { icon } : {}),
    webPreferences: {
      // CJS preload：Electron sandbox 下 ESM preload 可能静默失败，导致 youju 未注入。
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  hideOnClose(mainWindow, () => quitRequested || quitReady || !tray);
  mainWindow.on('closed', () => { mainWindow = null; });
  if (icon && process.platform === 'darwin' && app.dock) {
    app.dock.setIcon(icon);
  }
  mainWindow.once('ready-to-show', () => {
    if (!updateController?.hidden) mainWindow?.show();
    if (updateController?.finished && !updateController.window?.isDestroyed()) updateController.window?.destroy();
  });
  if (isDev) {
    process.env.OFFICE_API_PORT = String(backendPort);
    await mainWindow.loadURL(`http://127.0.0.1:5173/?officePort=${backendPort}`);
  } else {
    // 旧版首页没有 no-cache，会被磁盘缓存沿用旧 CSP / 旧脚本；本机服务清缓存几乎无代价。
    await mainWindow.webContents.session.clearCache().catch(() => {});
    // 与 office-backend 同源 HTTP，才能直连远程 mcp（file:// 会被 CSP 拦截）。
    await mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
  }
}

function stopServices() {
  if (stoppingServices) return stoppingServices;
  startupGeneration++;
  if (backupTimer) {
    clearTimeout(backupTimer);
    backupTimer = null;
  }
  stoppingServices = serviceProcesses.stopAll().then(() => {
    backend = null;
    backendPort = 0;
  }).finally(() => { stoppingServices = null; });
  return stoppingServices;
}

async function openApplication() {
  if (openingWindow || quitRequested) return openingWindow;
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show(); mainWindow.focus();
    return;
  }
  if (updateController?.show()) return;
  openingWindow = (async () => {
    let config = {};
    try { config = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8')); }
    catch { console.info('[youju] release endpoint not configured; update check disabled'); }
    updateController = new UpdateController({ app, BrowserWindow, ipcMain, shell, directory: __dirname,
      base: config.releaseBaseUrl || '', stopServices, beforeInstall: () => {} });
    await updateController.run();
    if (!quitRequested) await createWindow();
  })().finally(() => { openingWindow = null; });
  return openingWindow;
}

function showApplication() {
  if (!app.isReady() || quitRequested) return;
  // Check visible windows first, including a startup update gate still in flight.
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show(); mainWindow.focus();
  } else if (!updateController?.show()) {
    void openApplication().catch(error => dialog.showErrorBox('打开失败', String(error.message || error)));
  }
}

app.whenReady().then(async () => {
  if (!singleInstance || exitOnly) return;
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.youjulab.desktop');
    if (app.isPackaged) app.setJumpList([{ type: 'tasks', items: [{ type: 'task', title: '退出程序',
      description: '退出有据及本地后台服务', program: process.execPath, args: '--quit',
      iconPath: process.execPath, iconIndex: 0 }] }]);
  }
  const image = appIcon();
  if (!image) throw new Error('找不到程序图标，无法创建后台托盘入口。');
  tray = createBackgroundTray({ Tray, Menu,
    image: image.resize({ width: process.platform === 'darwin' ? 18 : 24, height: process.platform === 'darwin' ? 18 : 24 }),
    show: showApplication, quit: () => app.quit(), items: trayQuickItems });
  registerQuickShortcuts();
  if (process.platform === 'win32') Menu.setApplicationMenu(null);
  else if (process.platform === 'darwin') Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: '有据', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }] },
    { role: 'editMenu' }, { role: 'windowMenu' },
  ]));
  await openApplication();
}).catch(err => {
  console.error(err);
  dialog.showErrorBox('youjulab 启动失败', String(err?.message || err));
  app.quit();
});

app.on('will-quit', () => globalShortcut.unregisterAll());

app.on('window-all-closed', () => {
  // Closing a window is not quitting. Keep Office and scheduled backups alive.
  if (quitRequested || quitReady) return;
  if (!tray) app.quit();
});

app.on('before-quit', event => {
  if (quitReady) return;
  event.preventDefault();
  if (quitRequested) return;
  quitRequested = true;
  updateController?.closeForQuit();
  void stopServices().then(() => {
    quitReady = true;
    tray?.destroy();
    tray = null;
    app.quit();
  }).catch(error => {
    quitRequested = false;
    console.error('[youju] service shutdown failed', error);
    dialog.showErrorBox('退出失败', '后台服务未能全部停止，请重试退出。\n' + String(error?.message || error));
  });
});

process.once('SIGINT', () => app.quit());
process.once('SIGTERM', () => app.quit());
process.once('exit', () => serviceProcesses.forceStopSync());

app.on('activate', showApplication);
app.on('second-instance', (_event, argv) => { if (argv.includes('--quit')) app.quit(); else showApplication(); });
