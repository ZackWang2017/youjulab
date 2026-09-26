import { office } from '../office-client/client';
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
  project_id: string;
  kind: Kind;
  name: string;
  revision: number;
  updated_at: string;
  filename?: string | null;
  size?: number | null;
  content?: any;
  scope?: 'personal' | 'project' | null;
}
export interface Workspace {
  user: User;
  projects: Project[];
  project: Project;
  assets: Record<Kind, Asset[]>;
  selected: Record<Kind, Asset | null>;
}

const KINDS: Kind[] = ['sheet', 'document', 'pdf', 'mindmap', 'calendar'];
const serverRevision = new Map<string, number>();
let queued: { id: string; content: unknown } | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> = Promise.resolve();
let saveFailed = false;

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
      const saved = await office.json<Asset>(`/assets/${job.id}`, {
        method: 'PUT',
        body: JSON.stringify({ content: job.content, revision: serverRevision.get(job.id) ?? 0 }),
      });
      serverRevision.set(job.id, saved.revision);
      saveFailed = false;
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
    calendar: [] as Asset[],
  };
  await Promise.all(KINDS.map(async kind => {
    assets[kind] = (await office.json<{ assets: Asset[] }>(`/projects/${projectId}/assets?kind=${kind}`)).assets;
  }));
  return assets;
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
  };
}

export async function getAsset(id: string) {
  const asset = await office.json<Asset>(`/assets/${id}`);
  rememberRevision(asset.id, asset.revision);
  return asset;
}

async function flushSaveSoft() {
  await flushSave();
  if (saveFailed) {
    throw new Error('当前内容尚未保存到服务器，请先重试保存后再切换。');
  }
}

export async function openAsset(id: string) {
  await flushSaveSoft();
  const asset = await getAsset(id);
  const user = await office.json<User>(`/assets/${id}/select`, { method: 'POST' });
  return { asset, user };
}

export async function selectProject(id: string) {
  await flushSaveSoft();
  const user = await office.json<User>(`/projects/${id}/select`, { method: 'POST' });
  const { projects } = await office.json<{ projects: Project[] }>('/projects');
  const project = projects.find(item => item.id === id);
  if (!project) throw new Error('项目不存在。');
  const assets = await loadAssets(id);
  return {
    user, projects, project, assets,
    selected: selectedFrom(assets, user.last_assets),
  } satisfies Workspace;
}

export async function renameProject(id: string, name: string) {
  await flushSaveSoft();
  return office.json<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
}

export async function renameAsset(id: string, name: string) {
  await flushSaveSoft();
  const saved = await office.json<Asset>(`/assets/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ name, revision: serverRevision.get(id) }),
  });
  rememberRevision(saved.id, saved.revision);
  return saved;
}

export async function createProject(name: string) {
  await flushSaveSoft();
  const project = await office.json<Project>('/projects', { method: 'POST', body: JSON.stringify({ name }) });
  return selectProject(project.id);
}

export async function createAsset(
  projectId: string,
  kind: Kind,
  name: string,
  options?: { scope?: 'personal' | 'project' },
) {
  await flushSaveSoft();
  const body: { kind: Kind; name: string; scope?: 'personal' | 'project' } = { kind, name };
  if (kind === 'calendar') body.scope = options?.scope || 'personal';
  const asset = await office.json<Asset>(`/projects/${projectId}/assets`, {
    method: 'POST', body: JSON.stringify(body),
  });
  rememberRevision(asset.id, asset.revision);
  return asset;
}

export async function uploadPdf(projectId: string, file: File) {
  const name = file.name.replace(/\.pdf$/i, '') || '未命名 PDF';
  const asset = await createAsset(projectId, 'pdf', name);
  const uploaded = await office.upload(asset.id, file);
  rememberRevision(uploaded.id, uploaded.revision);
  return uploaded;
}

export async function savePdfFile(id: string, file: File) {
  const uploaded = await office.upload(id, file);
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
}
