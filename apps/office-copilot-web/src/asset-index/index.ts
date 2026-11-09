import './style.css';

export { configureAssetIndex, assetIndexExtensions, getAssetIndexDeps } from './bind';
export { attachIntentEditor, type IntentEditorHandle } from './intent';
export { AssetReference, createAssetIndexExtensions } from './tiptap';
export { parseAssetIndexQuery, fuzzyMatch } from './query';
export type { AssetIndexDeps, AssetRef, AssetKind, CatalogItem } from './types';
