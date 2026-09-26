import { office } from '../../office-client/client';
import type { SheetStructure } from './read-structure';

export type SheetCacheMeta = {
  ok: boolean;
  sheet_id: string;
  revision: number;
  rows: number;
  cols: number;
  has_profile: boolean;
  profile_stale: boolean;
};

const SELECTION_INTENT = /所选|选区|这些单元格|当前选中|高亮单元格|这块区域|写入|填写|填入|写到|写进|覆盖|修改单元格|替换单元格/;

export function wantsSheetSelection(intent: string) {
  return SELECTION_INTENT.test(intent || '');
}

export async function putSheetCache(assetId: string, structure: SheetStructure, revision: number) {
  return office.json<SheetCacheMeta>(`/assets/${assetId}/sheet-cache`, {
    method: 'PUT',
    body: JSON.stringify({ ...structure, revision }),
  });
}

export async function patchSheetCache(assetId: string, body: {
  sheet_id: string;
  revision: number;
  cells: { row: number; column: number; value: unknown }[];
  rows: number;
  cols: number;
  range?: string;
  merges?: string[];
}) {
  return office.json<SheetCacheMeta>(`/assets/${assetId}/sheet-cache`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export async function refreshSheetProfile(assetId: string, sheetId: string) {
  return office.json<SheetCacheMeta>(`/assets/${assetId}/sheet-cache/profile`, {
    method: 'POST',
    body: JSON.stringify({ sheet_id: sheetId }),
  });
}
