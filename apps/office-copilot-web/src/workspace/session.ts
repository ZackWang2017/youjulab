import { clearPdfFileCache, office } from '../office-client/client';
import { remoteToken } from '../remote/client';
import type { Surface } from '../context/types';

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

const KINDS: Kind[] = ['sheet', 'document', 'pdf', 'mindmap', 'research', 'diagram', 'project_plan', 'calendar'];
const serverRevision = new Map<string, number>();
/** 已拉取过正文的资产，切换文件时优先直接打开，避免每次重新请求。 */
const assetCache = new Map<string, Asset>();
let queued: { id: string; content: unknown } | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> = Promise.resolve();
let saveFailed = false;

function cacheAsset(asset: Asset) {
  assetCache.set(asset.id, asset);
  rememberRevision(asset.id, asset.revision);
  return asset;
}

function assetHasBody(asset: Asset | null | undefined) {
  if (!asset) return false;
  // PDF 正文在文件流里；缓存命中需至少带过一次详情（含笔记 content 字段）。
  if (asset.kind === 'pdf') return 'content' in asset;
  const content = asset.content as Record<string, unknown> | undefined;
  if (!content || typeof content !== 'object') return false;
  return Boolean(
    content.sheets || content.sheetOrder || content.nodeData || content.type === 'doc'
    || content.format === 'drawio-v1' || content.format === 'mermaid-v1'
    || content.format === 'invp-research-diagram-v1' || content.format === 'invp-project-plan-v1'
    || content.format === 'invp-calendar-v1' || content.format === 'mind-elixir-v1',
  );
}

export function rememberRevision(id: string, revision: number) {
  serverRevision.set(id, revision);
}

export function hasUnsavedWork() {
  return Boolean(queued) || saveFailed;
}

export async function flushSave() {
  clearTimeout(saveTimer);
  const job = queued;
  queued = null;
  if (!job) return saving;
  saving = saving.then(async () => {
    try {
      const saved = await office.json<Asset & { archived_name?: string }>(`/assets/${job.id}`, {
        method: 'PUT',
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
    } catch (e) {
      // 没有更新的排队内容时，把失败任务放回，便于重试、避免丢稿
      if (!queued) queued = job;
      saveFailed = true;
      const detail = e instanceof Error ? e.message : String(e);
      window.dispatchEvent(new CustomEvent('office-save-error', { detail }));
      window.dispatchEvent(new CustomEvent('office-error', { detail: `保存失败：${detail}` }));
    }
  });
  return saving;
}

export function queueSave(id: string, content: unknown) {
  queued = { id, content };
  saveFailed = false;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { void flushSave(); }, 400);
}

export function retrySave() {
  if (!queued) return Promise.resolve();
  return flushSave();
}

async function loadAssets(projectId: string) {
  const assets = {
    sheet: [] as Asset[],
    document: [] as Asset[],
    pdf: [] as Asset[],
    mindmap: [] as Asset[],
    research: [] as Asset[],
    diagram: [] as Asset[],
    project_plan: [] as Asset[],
    calendar: [] as Asset[],
  };
  await Promise.all(KINDS.map(async kind => {
    assets[kind] = (await office.json<{ assets: Asset[] }>(`/projects/${projectId}/assets?kind=${kind}`)).assets;
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

function pick(list: Asset[], last: string | null) {
  return list.find(item => item.id === last) || list[0] || null;
}

function selectedFrom(assets: Record<Kind, Asset[]>, last: Record<Kind, string | null>) {
  return {
    sheet: pick(assets.sheet, last.sheet),
    document: pick(assets.document, last.document),
    pdf: pick(assets.pdf, last.pdf),
    mindmap: pick(assets.mindmap, last.mindmap),
    research: pick(assets.research, last.research),
    diagram: pick(assets.diagram, last.diagram),
    project_plan: pick(assets.project_plan, last.project_plan),
    calendar: pick(assets.calendar, last.calendar),
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

export async function getAsset(id: string, opts?: { force?: boolean }) {
  if (!opts?.force) {
    const cached = assetCache.get(id);
    if (cached && assetHasBody(cached)) return cached;
  }
  const asset = await office.json<Asset>(`/assets/${id}`);
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
    cached && assetHasBody(cached) ? Promise.resolve(cached) : getAsset(id, { force: true }),
    office.json<User>(`/assets/${id}/select${suffix}`, { method: 'POST' }),
  ]);
  return { asset: cacheAsset(asset), user };
}

export async function selectProject(id: string) {
  await flushSaveSoft();
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

export async function createAsset(projectId: string, kind: Kind, name: string, visibility: 'project' | 'user' = 'project') {
  await flushSaveSoft();
  const body: { kind: Kind; name: string; visibility: 'project' | 'user' } = { kind, name, visibility };
  const asset = await office.json<Asset>(`/projects/${projectId}/assets`, {
    method: 'POST', body: JSON.stringify(body),
  });
  return cacheAsset(asset);
}

export async function uploadPdf(projectId: string, file: File) {
  const name = file.name.replace(/\.pdf$/i, '') || '未命名 PDF';
  const asset = await createAsset(projectId, 'pdf', name);
  const uploaded = await office.upload<Asset>(asset.id, file);
  rememberRevision(uploaded.id, uploaded.revision);
  return uploaded;
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
export async function bindLocalAccount(opts?: { force?: boolean }) {
  const remote = remoteToken();
  if (!remote) throw new Error('请先登录远程账号。');
  // 同一次会话里同一远程令牌已绑过且本机仍有 JWT，跳过，避免反复打 mcp /api/account/me
  if (!opts?.force && office.signedIn() && sessionStorage.getItem(BOUND_REMOTE_KEY) === remote) {
    return null;
  }
  const data = await office.json<{ token: string; user: User; email?: string }>('/local/bind-account', {
    method: 'POST',
  });
  office.setToken(data.token);
  sessionStorage.setItem(BOUND_REMOTE_KEY, remote);
  return data.user;
}

export function logout() {
  clearTimeout(saveTimer);
  queued = null;
  saveFailed = false;
  serverRevision.clear();
  assetCache.clear();
  clearPdfFileCache();
  sessionStorage.removeItem(BOUND_REMOTE_KEY);
  void office.json('/auth/logout', { method: 'POST' }).catch(() => {});
  office.setToken(null);
}
