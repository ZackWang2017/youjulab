import { loadCatalogItems } from './catalog';
import { parseAssetIndexQuery } from './query';
import type { AssetCatalog, CatalogItem, ParsedAssetQuery } from './types';

export type AssetIndexSelection =
  | { type: 'advance'; pathWithoutHash: string }
  | { type: 'insert'; item: CatalogItem };

/** 根据当前 `#` 后查询串拉列表；供非 TipTap 宿主或单测使用。 */
export async function queryAssetIndex(catalog: AssetCatalog, queryAfterHash: string): Promise<{
  parsed: ParsedAssetQuery;
  items: CatalogItem[];
}> {
  const parsed = parseAssetIndexQuery(queryAfterHash);
  if (!parsed.active) return { parsed, items: [] };
  const items = await loadCatalogItems(catalog, parsed);
  return { parsed, items };
}

export function selectionFromItem(parsed: ParsedAssetQuery, item: CatalogItem): AssetIndexSelection {
  if (item.asset) return { type: 'insert', item };
  if (parsed.stage === 'projects') {
    return { type: 'advance', pathWithoutHash: `/${item.pathSegment}/` };
  }
  return { type: 'advance', pathWithoutHash: `/${parsed.projectKey}/${item.pathSegment}/` };
}
