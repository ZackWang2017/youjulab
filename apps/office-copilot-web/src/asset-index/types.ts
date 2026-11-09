/** 通用资产索引：分层 `#/` 引用的共享类型。 */

export type AssetKind =
  | 'sheet'
  | 'document'
  | 'pdf'
  | 'strategy'
  | 'mindmap'
  | 'research'
  | 'diagram'
  | 'project_plan'
  | 'calendar';

export type AssetIndexStage = 'projects' | 'kinds' | 'assets';

export type AssetRef = {
  assetId: string;
  projectId: string;
  kind: AssetKind;
  label: string;
};

export type CatalogItem = {
  id: string;
  label: string;
  kind?: AssetKind;
  projectId?: string;
  /** 选项目/类别时写入路径；选资产时带完整引用 */
  pathSegment: string;
  asset?: AssetRef;
};

export type ParsedAssetQuery = {
  /** 是否匹配到 `#/` 触发（含后续路径） */
  active: boolean;
  stage: AssetIndexStage;
  /** `#/` 之后的完整查询串，含前导 `/` 时去掉首个 `/` 再分段 */
  raw: string;
  projectQuery: string;
  kindQuery: string;
  assetQuery: string;
  /** 已选定的路径段（不含正在输入的过滤词） */
  projectKey: string;
  kindKey: string;
};

export type AssetIndexProject = { id: string; name: string };
export type AssetIndexAsset = { id: string; name: string; project_id: string | null; kind: string };

export type AssetCatalog = {
  listProjects: () => Promise<AssetIndexProject[]>;
  listAssets: (projectId: string, kind: AssetKind) => Promise<AssetIndexAsset[]>;
};

export type AssetIndexDeps = {
  openAsset: (assetId: string) => void | Promise<void>;
  catalog?: AssetCatalog;
};

export const ASSET_KIND_OPTIONS: Array<{ id: AssetKind; label: string }> = [
  { id: 'sheet', label: '表格' },
  { id: 'document', label: '文档' },
  { id: 'pdf', label: 'PDF' },
  { id: 'strategy', label: '策略' },
  { id: 'mindmap', label: '导图' },
  { id: 'research', label: '研究图' },
  { id: 'diagram', label: '绘图' },
  { id: 'project_plan', label: '项目计划' },
  { id: 'calendar', label: '计划' },
];

export const ASSET_REF_TOKEN = /«asset:([^|«»]+)\|([^«»]*)»/g;
