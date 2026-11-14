import { clearPdfFileCache, office, type PdfFileProgress } from '../office-client/client';
import { remoteToken, setOfflineMode } from '../remote/client';
import type { Surface } from '../context/types';
import { informationApi, type InformationKind } from '../information/api';

export type Kind = Surface;
export interface User {
  id: string;
  email: string;
  last_project_id: string | null;
  last_assets: Record<Kind, string | null>;
}
export interface Project { id: string; name: string; created_at: string; updated_at: string; }
export interface Asset {
  id: string;
  project_id: string | null;
  kind: Kind | 'calc' | 'notebook';
  name: string;
  revision: number;
  updated_at: string;
  filename?: string | null;
  size?: number | null;
  content?: any;
  placement?: 'user' | 'project';
  encrypted?: boolean;
  locked?: boolean;
  information_kind?: InformationKind;
}
export interface RecentAsset extends Asset {
  project_name: string;
}
export interface Workspace {
  user: User;
  projects: Project[];
  project: Project;
  assets: Record<Kind, Asset[]>;
  selected: Record<Kind, Asset | null>;
  calc: Asset | null;
  notebook: Asset | null;
  recentAssets: RecentAsset[];
}

const KINDS: Kind[] = ['sheet', 'document', 'pdf', 'strategy', 'mindmap', 'research', 'diagram', 'project_plan', 'calendar', 'information'];
const serverRevision = new Map<string, number>();
/** 已拉取过正文的资产，切换文件时优先直接打开，避免每次重新请求。 */
const assetCache = new Map<string, Asset>();
/** 当前标签页内记住的文件密码，关页即忘。 */
const filePasswords = new Map<string, string>();
let queued: { id: string; content: unknown } | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> = Promise.resolve();
let saveFailed = false;
let conflictId: string | null = null;

export function cacheAsset(asset: Asset) {
  assetCache.set(asset.id, asset);
  rememberRevision(asset.id, asset.revision);
  return asset;
}

function hasDecryptedSheetContent(asset: Asset | null | undefined) {
  if (!asset || asset.locked) return false;
  const content = asset.content as Record<string, unknown> | undefined;
  if (!content || typeof content !== 'object') return false;
  if (content.format === 'youju-sheet-encrypted-v1') return false;
  return Boolean(content.sheets || content.sheetOrder);
}

function assetHasBody(asset: Asset | null | undefined) {
  if (!asset) return false;
  if (asset.locked || asset.encrypted) return false;
  // PDF 正文在文件流里；缓存命中需至少带过一次详情（含笔记 content 字段）。
  if (asset.kind === 'pdf') return 'content' in asset;
  const content = asset.content as Record<string, unknown> | undefined;
  if (!content || typeof content !== 'object') return false;
  if (content.format === 'youju-sheet-encrypted-v1') return false;
  return Boolean(
    content.sheets || content.sheetOrder || content.nodeData || content.type === 'doc'
    || content.format === 'drawio-v1' || content.format === 'mermaid-v1'
    || content.format === 'invp-research-diagram-v1' || content.format === 'invp-project-plan-v1'
    || content.format === 'invp-calendar-v1' || content.format === 'mind-elixir-v1' || content.format === 'youju-strategy-v1',
  );
}

export function assetNeedsUnlock(asset: Asset | null | undefined) {
  if (!asset || asset.kind !== 'sheet') return false;
  if (asset.locked) return true;
  return Boolean(asset.encrypted && !hasDecryptedSheetContent(asset));
}

export function discardSave(id?: string) {
  if (id && queued && queued.id !== id) return;
  clearTimeout(saveTimer);
  queued = null;
  saveFailed = false;
  if (!id || conflictId === id) conflictId = null;
}

function canSaveEncrypted(id: string) {
  const cached = assetCache.get(id);
  if (!cached?.encrypted && !cached?.locked) return true;
  return Boolean(filePasswords.get(id));
}

/** 离开加密表时丢掉明文缓存和标签页里的密码，下次打开必须重新输入。 */
export function lockEncryptedAsset(id: string) {
  discardSave(id);
  clearFilePassword(id);
  const cached = assetCache.get(id);
  if (!cached || (!cached.encrypted && !cached.locked)) return cached;
  const next: Asset = { ...cached, encrypted: true, locked: true };
  delete next.content;
  assetCache.set(id, next);
  return next;
}

export function setFilePassword(id: string, password: string) {
  if (password) filePasswords.set(id, password);
  else filePasswords.delete(id);
}

export function clearFilePassword(id: string) {
  filePasswords.delete(id);
}

export function filePasswordHeader(id: string): HeadersInit | undefined {
  const password = filePasswords.get(id);
  return password ? { 'X-File-Password': encodeURIComponent(password) } : undefined;
}

export function rememberRevision(id: string, revision: number) {
  serverRevision.set(id, revision);
}

export function hasUnsavedWork() {
  return Boolean(queued) || saveFailed;
}

/** 该资产是否有排队未提交的保存（含上次失败待重试）。 */
export function hasQueuedSave(id: string) {
  return queued?.id === id;
}

export function knownRevision(id: string) {
  return serverRevision.get(id) ?? 0;
}

export async function flushSave() {
  clearTimeout(saveTimer);
  if (conflictId) return saving;
  const job = queued;
  queued = null;
  if (!job) return saving;
  if (!canSaveEncrypted(job.id)) return saving;
  const headers = filePasswordHeader(job.id);
  saving = saving.then(async () => {
    if (conflictId) {
      if (!queued || queued.id === job.id) queued = job;
      return;
    }
    try {
      const saved = await office.json<Asset & { archived_name?: string }>(`/assets/${job.id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ content: job.content, revision: serverRevision.get(job.id) ?? 0 }),
      });
      const prev = assetCache.get(saved.id);
      cacheAsset({
        ...(prev || saved),
        ...saved,
        content: job.content,
        kind: saved.kind || prev?.kind || 'document',
      });
      saveFailed = false;
      if (saved.id !== job.id) {
        queued = null;
        window.dispatchEvent(new CustomEvent('office-document-replaced', {
          detail: { id: saved.id, archived_name: saved.archived_name || '' },
        }));
        return;
      }
      window.dispatchEvent(new CustomEvent('office-saved'));
      if (saved.kind === 'calendar' || saved.kind === 'notebook') {
        window.youju?.notifyAssetChanged?.({ id: saved.id, kind: saved.kind, revision: saved.revision });
      }
    } catch (e) {
      // 没有更新的排队内容时，把失败任务放回，便于重试、避免丢稿
      if (!queued) queued = job;
      saveFailed = true;
      if ((e as { status?: number }).status === 409) {
        conflictId = job.id;
        window.dispatchEvent(new CustomEvent('office-save-conflict', { detail: { id: job.id } }));
        return;
      }
      const detail = e instanceof Error ? e.message : String(e);
      window.dispatchEvent(new CustomEvent('office-save-error', { detail }));
      window.dispatchEvent(new CustomEvent('office-error', { detail: `保存失败：${detail}` }));
    }
  });
  return saving;
}

export function queueSave(id: string, content: unknown) {
  if (!canSaveEncrypted(id)) return;
  if (conflictId && conflictId !== id) {
    window.dispatchEvent(new CustomEvent('office-error', { detail: '请先处理当前文件的保存冲突，再编辑其他文件。' }));
    return;
  }
  if (queued && queued.id !== id) void flushSave();
  queued = { id, content };
  if (conflictId) return;
  saveFailed = false;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { void flushSave(); }, 400);
}

export function retrySave() {
  if (!queued) return Promise.resolve();
  return flushSave();
}

export async function resolveSaveConflict(id: string, action: 'overwrite' | 'discard' | 'copy') {
  await saving;
  if (conflictId !== id || queued?.id !== id) throw new Error('待处理的修改已变化，请重新保存。');
  const job = queued;
  const headers = filePasswordHeader(id);
  const latest = await office.json<Asset>(`/assets/${id}`, { headers });
  let result = latest;
  if (action === 'copy') {
    result = await office.json<Asset>(`/assets/${id}/copy`, {
      method: 'POST', headers, body: JSON.stringify({ content: job.content }),
    });
    const password = filePasswords.get(id);
    if (password && result.encrypted) setFilePassword(result.id, password);
  } else if (action === 'overwrite') {
    result = await office.json<Asset>(`/assets/${id}`, {
      method: 'PUT', headers, body: JSON.stringify({ content: job.content, revision: latest.revision }),
    });
  }
  // A failed request leaves both the local draft and the paused conflict intact.
  discardSave(id);
  cacheAsset(latest);
  return cacheAsset(result);
}

async function loadAssets(projectId: string) {
  const groups = await informationApi.list(projectId);
  const assets = {
    sheet: [] as Asset[],
    document: [] as Asset[],
    pdf: [] as Asset[],
    strategy: [] as Asset[],
    mindmap: [] as Asset[],
    research: [] as Asset[],
    diagram: [] as Asset[],
    project_plan: [] as Asset[],
    calendar: [] as Asset[],
    information: [] as Asset[],
  };
  await Promise.all(KINDS.map(async kind => {
    assets[kind] = (await office.json<{ assets: Asset[] }>(`/projects/${projectId}/assets?kind=${kind}`)).assets;
  }));
  assets.information = assets.information.map(asset => ({
    ...asset, name: groups.find(group => group.asset_id === asset.id)?.name || asset.name,
    information_kind: groups.find(group => group.asset_id === asset.id)?.kind,
  }));
  return assets;
}

async function loadUserTool(projectId: string, kind: 'calc' | 'notebook') {
  try {
    const listed = (await office.json<{ assets: Asset[] }>(`/projects/${projectId}/assets?kind=${kind}`)).assets;
    if (!listed.length) return null;
    // 同用户可能因绑定堆积多本空笔记本；优先打开 revision 最高的
    const best = listed.reduce((a, b) => ((b.revision || 0) > (a.revision || 0) ? b : a));
    return getAsset(best.id);
  } catch {
    return null;
  }
}

export async function loadRecentAssets(projectId: string) {
  return (await office.json<{ assets: RecentAsset[] }>(`/assets/recent?project_id=${encodeURIComponent(projectId)}`)).assets;
}

export async function reorderRecentAssets(projectId: string, assetIds: string[]) {
  return (await office.json<{ assets: RecentAsset[] }>('/assets/recent', {
    method: 'PUT',
    body: JSON.stringify({ project_id: projectId, asset_ids: assetIds }),
  })).assets;
}

export async function dismissRecentAsset(projectId: string, assetId: string) {
  return (await office.json<{ assets: RecentAsset[] }>(
    `/assets/recent/${encodeURIComponent(assetId)}?project_id=${encodeURIComponent(projectId)}`,
    { method: 'DELETE' },
  )).assets;
}

function pick(list: Asset[], last: string | null) {
  return list.find(item => item.id === last) || list[0] || null;
}

function selectedFrom(assets: Record<Kind, Asset[]>, last: Record<Kind, string | null>) {
  return {
    sheet: pick(assets.sheet, last.sheet),
    document: pick(assets.document, last.document),
    pdf: pick(assets.pdf, last.pdf),
    strategy: pick(assets.strategy, last.strategy),
    mindmap: pick(assets.mindmap, last.mindmap),
    research: pick(assets.research, last.research),
    diagram: pick(assets.diagram, last.diagram),
    project_plan: pick(assets.project_plan, last.project_plan),
    calendar: pick(assets.calendar, last.calendar),
    information: pick(assets.information, last.information),
  };
}

export async function bootstrap(): Promise<Workspace> {
  const user = await office.json<User>('/auth/me');
  const { projects } = await office.json<{ projects: Project[] }>('/projects');
  const project = projects.find(item => item.id === user.last_project_id) || projects[0];
  if (!project) throw new Error('没有可用项目。');
  if (user.last_project_id !== project.id) {
    Object.assign(user, await office.json<User>(`/projects/${project.id}/select`, { method: 'POST' }));
  }
  const assets = await loadAssets(project.id);
  return {
    user, projects, project, assets,
    selected: selectedFrom(assets, user.last_assets),
    calc: await loadUserTool(project.id, 'calc'),
    notebook: await loadUserTool(project.id, 'notebook'),
    recentAssets: await loadRecentAssets(project.id),
  };
}

export async function getAsset(id: string, opts?: { force?: boolean; unlock?: boolean }) {
  if (!opts?.force) {
    const cached = assetCache.get(id);
    if (cached && assetHasBody(cached)) return cached;
  }
  const headers = opts?.unlock ? filePasswordHeader(id) : undefined;
  const asset = await office.json<Asset>(`/assets/${id}`, headers ? { headers } : {});
  return cacheAsset(asset);
}

async function flushSaveSoft() {
  await flushSave();
  if (saveFailed) {
    throw new Error('当前内容尚未保存到服务器，请先重试保存后再切换。');
  }
}

export async function openAsset(id: string, trackRecent = true) {
  await flushSaveSoft();
  const suffix = trackRecent ? '' : '?track_recent=false';
  const cached = assetCache.get(id);
  const [asset, user] = await Promise.all([
    cached && assetHasBody(cached) && cached.kind !== 'research' ? Promise.resolve(cached) : getAsset(id, { force: true }),
    office.json<User>(`/assets/${id}/select${suffix}`, { method: 'POST' }),
  ]);
  return { asset: cacheAsset(asset), user };
}

export async function selectProject(id: string) {
  await flushSaveSoft();
  for (const assetId of [...assetCache.keys()]) lockEncryptedAsset(assetId);
  const user = await office.json<User>(`/projects/${id}/select`, { method: 'POST' });
  const { projects } = await office.json<{ projects: Project[] }>('/projects');
  const project = projects.find(item => item.id === id);
  if (!project) throw new Error('项目不存在。');
  const [assets, calc, notebook, recentAssets] = await Promise.all([
    loadAssets(id),
    loadUserTool(id, 'calc'),
    loadUserTool(id, 'notebook'),
    loadRecentAssets(id),
  ]);
  return {
    user, projects, project, assets,
    selected: selectedFrom(assets, user.last_assets),
    calc, notebook, recentAssets,
  } satisfies Workspace;
}

export async function renameProject(id: string, name: string) {
  await flushSaveSoft();
  return office.json<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
}

export async function renameAsset(id: string, name: string) {
  return updateAsset(id, { name });
}

export async function updateAsset(
  id: string,
  fields: { name?: string; visibility?: 'project' | 'user' },
) {
  await flushSaveSoft();
  const body: Record<string, unknown> = { revision: serverRevision.get(id) };
  if (fields.name !== undefined) body.name = fields.name;
  if (fields.visibility !== undefined) body.visibility = fields.visibility;
  const saved = await office.json<Asset>(`/assets/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  rememberRevision(saved.id, saved.revision);
  return saved;
}

export async function createProject(name: string) {
  await flushSaveSoft();
  const project = await office.json<Project>('/projects', { method: 'POST', body: JSON.stringify({ name }) });
  return selectProject(project.id);
}

export async function createAsset(projectId: string, kind: Kind, name: string, visibility: 'project' | 'user' = 'project', content?: unknown, sourceUrl?: string) {
  await flushSaveSoft();
  const body = { kind, name, visibility, ...(content === undefined ? {} : { content }), ...(sourceUrl ? { source_url: sourceUrl } : {}) };
  let asset = await office.json<Asset>(`/projects/${projectId}/assets`, {
    method: 'POST', body: JSON.stringify(body),
  });
  // Older running backends ignore initial research content; persist it before opening the editor.
  if (kind === 'research' && content !== undefined && JSON.stringify(asset.content) !== JSON.stringify(content)) {
    asset = await office.json<Asset>(`/assets/${asset.id}`, {
      method: 'PUT', body: JSON.stringify({ content, revision: asset.revision }),
    });
    const expected = content as { type?: unknown };
    if (!asset.content || asset.content.type !== expected.type) {
      throw new Error('研究图类型保存失败，请重启后台服务后重试。');
    }
  }
  return cacheAsset(asset);
}

export async function uploadPdf(
  projectId: string,
  file: File,
  onProgress?: (progress: PdfFileProgress) => void,
) {
  const name = file.name.replace(/\.pdf$/i, '') || '未命名 PDF';
  const asset = await createAsset(projectId, 'pdf', name);
  const uploaded = await office.upload<Asset>(asset.id, file, onProgress);
  return cacheAsset(uploaded);
}

export async function savePdfFile(id: string, file: File) {
  const uploaded = await office.upload<Asset>(id, file);
  rememberRevision(uploaded.id, uploaded.revision);
  window.dispatchEvent(new CustomEvent('office-saved'));
  return uploaded;
}

export async function login(email: string, password: string) {
  const data = await office.json<{ token: string; user: User }>('/auth/login', {
    method: 'POST', body: JSON.stringify({ email, password }),
  });
  office.setToken(data.token);
  return data.user;
}

export async function register(email: string, password: string) {
  const data = await office.json<{ token: string; user: User }>('/auth/register', {
    method: 'POST', body: JSON.stringify({ email, password }),
  });
  office.setToken(data.token);
  return data.user;
}

const BOUND_REMOTE_KEY = 'youju-bound-remote';

/** 远程账号登录后：按注册邮箱绑定本机 users，并把访客资产挂到该邮箱下。 */
export async function bindLocalAccount(opts?: { force?: boolean; password?: string }) {
  const remote = remoteToken();
  if (!remote) throw new Error('请先登录远程账号。');
  // 同一次会话里同一远程令牌已绑过且本机仍有 JWT，跳过，避免反复打 mcp /api/account/me
  if (!opts?.force && office.signedIn() && sessionStorage.getItem(BOUND_REMOTE_KEY) === remote) {
    return null;
  }
  // 远程刚校验通过的密码一并交给本机保存哈希，网络不可用时可离线进入。
  const data = await office.json<{ token: string; user: User; email?: string }>('/local/bind-account', {
    method: 'POST',
    ...(opts?.password ? { body: JSON.stringify({ password: opts.password }) } : {}),
  });
  office.setToken(data.token);
  sessionStorage.setItem(BOUND_REMOTE_KEY, remote);
  return data.user;
}

/** 远程改密成功后同步本机离线凭据。 */
export async function syncLocalPassword(password: string) {
  await office.json('/local/password', { method: 'PUT', body: JSON.stringify({ password }) });
}

export function logout() {
  clearTimeout(saveTimer);
  queued = null;
  saveFailed = false;
  conflictId = null;
  serverRevision.clear();
  assetCache.clear();
  filePasswords.clear();
  clearPdfFileCache();
  sessionStorage.removeItem(BOUND_REMOTE_KEY);
  setOfflineMode(false);
  void office.json('/auth/logout', { method: 'POST' }).catch(() => {});
  office.setToken(null);
}
