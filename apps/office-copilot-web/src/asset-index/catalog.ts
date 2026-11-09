import { office } from '../office-client/client';
import { ASSET_KIND_OPTIONS, type AssetCatalog, type AssetIndexAsset, type AssetIndexProject, type AssetKind, type CatalogItem } from './types';
import { filterCatalogItems, resolveKindKey } from './query';

const projectCache = { at: 0, items: [] as AssetIndexProject[] };
const assetCache = new Map<string, { at: number; items: AssetIndexAsset[] }>();
const CACHE_MS = 8_000;

export function createOfficeAssetCatalog(): AssetCatalog {
  return {
    async listProjects() {
      const now = Date.now();
      if (now - projectCache.at < CACHE_MS && projectCache.items.length) return projectCache.items;
      const { projects } = await office.json<{ projects: AssetIndexProject[] }>('/projects');
      projectCache.at = now;
      projectCache.items = projects || [];
      return projectCache.items;
    },
    async listAssets(projectId, kind) {
      const key = `${projectId}:${kind}`;
      const now = Date.now();
      const hit = assetCache.get(key);
      if (hit && now - hit.at < CACHE_MS) return hit.items;
      const { assets } = await office.json<{ assets: AssetIndexAsset[] }>(
        `/projects/${encodeURIComponent(projectId)}/assets?kind=${encodeURIComponent(kind)}`,
      );
      const items = assets || [];
      assetCache.set(key, { at: now, items });
      return items;
    },
  };
}

export function invalidateAssetCatalogCache() {
  projectCache.at = 0;
  projectCache.items = [];
  assetCache.clear();
}

export async function resolveProjectId(catalog: AssetCatalog, projectKey: string): Promise<string | null> {
  const key = projectKey.trim();
  if (!key) return null;
  const projects = await catalog.listProjects();
  const exact = projects.find(item => item.id === key || item.name === key);
  if (exact) return exact.id;
  const fuzzy = projects.find(item => item.name.toLowerCase().includes(key.toLowerCase()));
  return fuzzy?.id || null;
}

export async function loadCatalogItems(
  catalog: AssetCatalog,
  parsed: ReturnType<typeof import('./query').parseAssetIndexQuery>,
): Promise<CatalogItem[]> {
  if (!parsed.active) return [];
  if (parsed.stage === 'projects') {
    const projects = await catalog.listProjects();
    return filterCatalogItems(
      projects.map(item => ({
        id: item.id,
        label: item.name,
        pathSegment: item.name,
        projectId: item.id,
      })),
      parsed.projectQuery,
    );
  }
  if (parsed.stage === 'kinds') {
    return filterCatalogItems(
      ASSET_KIND_OPTIONS.map(item => ({
        id: item.id,
        label: item.label,
        kind: item.id,
        pathSegment: item.id,
      })),
      parsed.kindQuery,
    );
  }
  const projectId = await resolveProjectId(catalog, parsed.projectKey);
  const kind = resolveKindKey(parsed.kindKey);
  if (!projectId || !kind) return [];
  const assets = await catalog.listAssets(projectId, kind);
  return filterCatalogItems(
    assets.map(item => ({
      id: item.id,
      label: item.name,
      kind: kind as AssetKind,
      projectId,
      pathSegment: item.name,
      asset: {
        assetId: item.id,
        projectId,
        kind,
        label: item.name,
      },
    })),
    parsed.assetQuery,
  );
}
