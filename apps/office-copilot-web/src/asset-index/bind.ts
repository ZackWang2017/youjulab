import type { AnyExtension } from '@tiptap/core';
import { createOfficeAssetCatalog } from './catalog';
import { createAssetIndexExtensions } from './tiptap';
import type { AssetIndexDeps } from './types';

let configured: AssetIndexDeps | null = null;

/** 在应用启动时注入 openAsset；各编辑器通过 assetIndexExtensions() 取扩展。 */
export function configureAssetIndex(deps: AssetIndexDeps) {
  configured = {
    openAsset: deps.openAsset,
    catalog: deps.catalog || createOfficeAssetCatalog(),
  };
}

export function assetIndexExtensions(): AnyExtension[] {
  if (!configured) return [];
  return createAssetIndexExtensions(configured) as AnyExtension[];
}

export function getAssetIndexDeps(): AssetIndexDeps | null {
  return configured;
}
