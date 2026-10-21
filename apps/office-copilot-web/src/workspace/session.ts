import { office } from '../office-client/client';
import type { Surface } from '../context/types';
import { clearLocalStore, deleteLocalAsset, getLocalJson, putLocalJson, type LocalJson } from '../local-store';

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
let queued: { id: string; content: unknown } | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> = Promise.resolve();
let saveFailed = false;
const localWrites = new Map<string, Promise<void>>();

function rememberLocal(
  id: string,
  content: unknown,
  revision: number,
  pending: boolean,
  extra?: Pick<LocalJson, 'kind' | 'name' | 'project_id'>,
) {
  const prev = localWrites.get(id) ?? Promise.resolve();
  const next = prev.then(async () => {
    const existing = await getLocalJson(id).catch(() => null);
    await putLocalJson({
      id,
      content,
      revision,
      baseRevision: revision,
      pending,
      kind: extra?.kind ?? existing?.kind,
      name: extra?.name ?? existing?.name,
      project_id: extra?.project_id ?? existing?.project_id ?? null,
    });
  }).catch(() => {});
  localWrites.set(id, next);
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
      serverRevision.set(saved.id, saved.revision);
      const newer = queued && queued.id === job.id ? queued : null;
      rememberLocal(saved.id, newer ? newer.content : job.content, saved.revision, Boolean(newer), {
        kind: saved.kind,
        name: saved.name,
        project_id: saved.project_id,
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
  rememberLocal(id, content, serverRevision.get(id) ?? 0, true);
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
    const listed = (await office.json<{ assets: Asset[] }>(`/projects/${projectId}/assets?kind=${kind}`)).assets[0];
    if (!listed) return null;
    return getAsset(listed.id);
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

function assetFromLocal(id: string, local: LocalJson): Asset {
  return {
    id,
    project_id: local.project_id ?? null,
    kind: (local.kind || 'document') as Asset['kind'],
    name: local.name || '未命名',
    revision: local.pending ? local.baseRevision : local.revision,
    updated_at: new Date(local.updatedAt).toISOString(),
    content: local.content,
  };
}

export async function getAsset(id: string) {
  let local: LocalJson | null = null;
  try { local = await getLocalJson(id); } catch { local = null; }
  let meta: Asset;
  try {
    meta = await office.json<Asset>(`/assets/${id}?meta=1`);
  } catch (e) {
    if (local) return assetFromLocal(id, local);
    throw e;
  }
  rememberRevision(meta.id, meta.revision);
  if (local && local.pending && local.baseRevision === meta.revision) {
    return { ...meta, content: local.content };
  }
  if (local && !local.pending && local.revision === meta.revision) {
    return { ...meta, content: local.content };
  }
  const asset = await office.json<Asset>(`/assets/${id}`);
  rememberRevision(asset.id, asset.revision);
  rememberLocal(asset.id, asset.content, asset.revision, false, {
    kind: asset.kind,
    name: asset.name,
    project_id: asset.project_id,
  });
  return asset;
}

export function forgetLocalAsset(id: string) {
  void deleteLocalAsset(id).catch(() => {});
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
  const [asset, user] = await Promise.all([
    getAsset(id),
    office.json<User>(`/assets/${id}/select${suffix}`, { method: 'POST' }),
  ]);
  return { asset, user };
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
  rememberRevision(asset.id, asset.revision);
  return asset;
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

export function logout() {
  clearTimeout(saveTimer);
  queued = null;
  saveFailed = false;
  serverRevision.clear();
  office.setToken(null);
  void clearLocalStore().catch(() => {});
}
