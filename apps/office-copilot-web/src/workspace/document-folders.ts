import { saved } from '../context/types';
import { office } from '../office-client/client';

/** 只用于文档菜单分类，不写入资产。 */
export interface DocumentFolder {
  id: string;
  name: string;
  children: DocumentFolder[];
}

export interface DocumentFolderStore {
  folders: DocumentFolder[];
  /** assetId -> folderId。不在表里的文档留在根目录。 */
  placements: Record<string, string>;
  collapsed: Record<string, boolean>;
}

const empty = (): DocumentFolderStore => ({ folders: [], placements: {}, collapsed: {} });
const cache = new Map<string, DocumentFolderStore>();
const hydrations = new Map<string, Promise<boolean>>();

function key(projectId: string) {
  return `office-document-folders:${projectId}`;
}

function isFolder(value: unknown): value is DocumentFolder {
  if (!value || typeof value !== 'object') return false;
  const row = value as DocumentFolder;
  return typeof row.id === 'string' && typeof row.name === 'string' && Array.isArray(row.children);
}

function normalizeFolders(value: unknown): DocumentFolder[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isFolder).map(folder => ({
    id: folder.id,
    name: folder.name,
    children: normalizeFolders(folder.children),
  }));
}

function legacyDocumentFolders(projectId: string): DocumentFolderStore {
  const raw = saved(key(projectId));
  if (!raw || typeof raw !== 'object') return empty();
  const row = raw as Partial<DocumentFolderStore>;
  const placements: Record<string, string> = {};
  if (row.placements && typeof row.placements === 'object') {
    for (const [assetId, folderId] of Object.entries(row.placements)) {
      if (typeof folderId === 'string') placements[assetId] = folderId;
    }
  }
  const collapsed: Record<string, boolean> = {};
  if (row.collapsed && typeof row.collapsed === 'object') {
    for (const [id, value] of Object.entries(row.collapsed)) collapsed[id] = Boolean(value);
  }
  return { folders: normalizeFolders(row.folders), placements, collapsed };
}

export function loadDocumentFolders(projectId: string): DocumentFolderStore {
  const stored = cache.get(projectId);
  if (stored) return stored;
  const legacy = legacyDocumentFolders(projectId);
  cache.set(projectId, legacy);
  return legacy;
}

function normalizeStore(value: Partial<DocumentFolderStore>): DocumentFolderStore {
  const placements: Record<string, string> = {};
  if (value.placements && typeof value.placements === 'object') {
    for (const [assetId, folderId] of Object.entries(value.placements)) {
      if (typeof folderId === 'string') placements[assetId] = folderId;
    }
  }
  const collapsed: Record<string, boolean> = {};
  if (value.collapsed && typeof value.collapsed === 'object') {
    for (const [id, isCollapsed] of Object.entries(value.collapsed)) collapsed[id] = Boolean(isCollapsed);
  }
  return { folders: normalizeFolders(value.folders), placements, collapsed };
}

/** 首次进入项目时把旧 UI 偏好迁移到独立的项目数据库记录。 */
export function hydrateDocumentFolders(projectId: string): Promise<boolean> {
  const pending = hydrations.get(projectId);
  if (pending) return pending;
  const before = loadDocumentFolders(projectId);
  const task = office.json<{ configured: boolean } & DocumentFolderStore>(`/projects/${projectId}/document-folders`)
    .then(async remote => {
      if (remote.configured) {
        const next = normalizeStore(remote);
        const changed = JSON.stringify(next) !== JSON.stringify(before);
        cache.set(projectId, next);
        return changed;
      }
      await office.json(`/projects/${projectId}/document-folders`, {
        method: 'PUT', body: JSON.stringify(before),
      });
      localStorage.removeItem(key(projectId));
      return false;
    })
    .catch(error => {
      window.dispatchEvent(new CustomEvent('office-error', {
        detail: `文档文件夹读取失败：${error instanceof Error ? error.message : String(error)}`,
      }));
      return false;
    })
    .finally(() => hydrations.delete(projectId));
  hydrations.set(projectId, task);
  return task;
}

export function saveDocumentFolders(projectId: string, store: DocumentFolderStore) {
  const next = normalizeStore(store);
  cache.set(projectId, next);
  void office.json(`/projects/${projectId}/document-folders`, {
    method: 'PUT', body: JSON.stringify(next),
  }).catch(error => {
    window.dispatchEvent(new CustomEvent('office-error', {
      detail: `文档文件夹保存失败：${error instanceof Error ? error.message : String(error)}`,
    }));
  });
}

export function findDocumentFolder(folders: DocumentFolder[], id: string): DocumentFolder | null {
  for (const folder of folders) {
    if (folder.id === id) return folder;
    const hit = findDocumentFolder(folder.children, id);
    if (hit) return hit;
  }
  return null;
}

export function documentFolderPaths(folders: DocumentFolder[], prefix: string[] = []): { id: string; path: string }[] {
  const rows: { id: string; path: string }[] = [];
  for (const folder of folders) {
    const path = [...prefix, folder.name].join(' / ');
    rows.push({ id: folder.id, path });
    rows.push(...documentFolderPaths(folder.children, [...prefix, folder.name]));
  }
  return rows;
}

function stripFolder(folders: DocumentFolder[], id: string): DocumentFolder | null {
  const index = folders.findIndex(folder => folder.id === id);
  if (index >= 0) return folders.splice(index, 1)[0];
  for (const folder of folders) {
    const hit = stripFolder(folder.children, id);
    if (hit) return hit;
  }
  return null;
}

export function addDocumentFolder(store: DocumentFolderStore, parentId: string | null, name: string) {
  const folder: DocumentFolder = { id: crypto.randomUUID(), name, children: [] };
  if (!parentId) {
    store.folders.push(folder);
    return folder;
  }
  const parent = findDocumentFolder(store.folders, parentId);
  if (!parent) return null;
  parent.children.push(folder);
  store.collapsed[parentId] = false;
  return folder;
}

export function renameDocumentFolder(store: DocumentFolderStore, id: string, name: string) {
  const folder = findDocumentFolder(store.folders, id);
  if (folder) folder.name = name;
}

export function deleteDocumentFolder(store: DocumentFolderStore, id: string) {
  const parent = parentOf(store.folders, id);
  const removed = stripFolder(store.folders, id);
  if (!removed) return;
  const lifted = removed.children;
  if (parent) parent.children.push(...lifted);
  else store.folders.push(...lifted);
  for (const [assetId, folderId] of Object.entries(store.placements)) {
    if (folderId !== id) continue;
    if (parent) store.placements[assetId] = parent.id;
    else delete store.placements[assetId];
  }
  delete store.collapsed[id];
}

function parentOf(folders: DocumentFolder[], id: string, parent: DocumentFolder | null = null): DocumentFolder | null {
  for (const folder of folders) {
    if (folder.id === id) return parent;
    const hit = parentOf(folder.children, id, folder);
    if (hit) return hit;
  }
  return null;
}

export function placeDocument(store: DocumentFolderStore, assetId: string, folderId: string | null) {
  if (!folderId) delete store.placements[assetId];
  else if (findDocumentFolder(store.folders, folderId)) store.placements[assetId] = folderId;
}

export function documentsInFolder(store: DocumentFolderStore, folderId: string | null, assetIds: string[]) {
  const known = new Set(assetIds);
  return assetIds.filter(id => {
    const placed = store.placements[id];
    if (!known.has(id)) return false;
    if (!folderId) return !placed || !findDocumentFolder(store.folders, placed);
    return placed === folderId;
  });
}

export function documentFolderMatches(
  folder: DocumentFolder,
  query: string,
  assetName: (id: string) => string,
  store: DocumentFolderStore,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (folder.name.toLowerCase().includes(q)) return true;
  const ids = Object.entries(store.placements).filter(([, folderId]) => folderId === folder.id).map(([id]) => id);
  if (ids.some(id => assetName(id).toLowerCase().includes(q))) return true;
  return folder.children.some(child => documentFolderMatches(child, query, assetName, store));
}
