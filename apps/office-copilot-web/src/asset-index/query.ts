import { ASSET_KIND_OPTIONS, type AssetIndexStage, type AssetKind, type CatalogItem, type ParsedAssetQuery } from './types';

/** Suggestion 的 char 是 `#`，query 为 `#` 之后的内容；有效触发须以 `/` 开头。 */
export function parseAssetIndexQuery(queryAfterHash: string): ParsedAssetQuery {
  const raw = String(queryAfterHash || '');
  if (!raw.startsWith('/')) {
    return {
      active: false,
      stage: 'projects',
      raw,
      projectQuery: '',
      kindQuery: '',
      assetQuery: '',
      projectKey: '',
      kindKey: '',
    };
  }
  const body = raw.slice(1);
  const parts = body.split('/');
  const projectKey = parts.length >= 2 ? parts[0] : '';
  const kindKey = parts.length >= 3 ? parts[1] : '';
  let stage: AssetIndexStage = 'projects';
  if (parts.length === 1) stage = 'projects';
  else if (parts.length === 2) stage = 'kinds';
  else stage = 'assets';

  return {
    active: true,
    stage,
    raw,
    projectQuery: parts.length === 1 ? parts[0] : '',
    kindQuery: parts.length === 2 ? parts[1] : '',
    assetQuery: parts.length >= 3 ? parts.slice(2).join('/') : '',
    projectKey,
    kindKey,
  };
}

export function fuzzyMatch(haystack: string, needle: string): boolean {
  const text = haystack.trim().toLowerCase();
  const query = needle.trim().toLowerCase();
  if (!query) return true;
  if (text.includes(query)) return true;
  let i = 0;
  for (const ch of text) {
    if (ch === query[i]) i += 1;
    if (i >= query.length) return true;
  }
  return false;
}

export function filterCatalogItems(items: CatalogItem[], needle: string): CatalogItem[] {
  return items.filter(item => fuzzyMatch(item.label, needle) || fuzzyMatch(item.pathSegment, needle) || fuzzyMatch(item.id, needle));
}

export function resolveKindKey(key: string): AssetKind | null {
  const raw = key.trim().toLowerCase();
  if (!raw) return null;
  const byId = ASSET_KIND_OPTIONS.find(item => item.id === raw || item.id.replace('_', '') === raw.replace(/[_-]/g, ''));
  if (byId) return byId.id;
  const byLabel = ASSET_KIND_OPTIONS.find(item => item.label.toLowerCase() === raw || item.label === key.trim());
  return byLabel?.id || null;
}

/** 选中项目/类别后写回编辑器的触发串（不含开头的 `#`，由 suggestion range 决定）。 */
export function pathAfterSelectingProject(projectSegment: string): string {
  return `/${projectSegment}/`;
}

export function pathAfterSelectingKind(projectSegment: string, kindSegment: string): string {
  return `/${projectSegment}/${kindSegment}/`;
}
