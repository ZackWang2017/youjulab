export type Surface = 'sheet' | 'document' | 'pdf' | 'mindmap' | 'research' | 'diagram' | 'project_plan' | 'calendar';
export interface Context {
  document_id: string;
  surface: Surface;
  revision: number;
  selection: Record<string, any>;
  surrounding: string;
}
export interface Patch extends Omit<Context, 'surrounding'> {
  patch_id: string;
  replacement: string | (string | number | boolean | null)[][] | null;
  format_ops?: Array<{
    address: string;
    number_format: string;
    row?: number;
    column?: number;
    rows?: number;
    cols?: number;
  }>;
  mindmap_ops?: Array<{
    op: 'add' | 'update' | 'delete' | 'move';
    id?: string;
    parent_id?: string;
    topic?: string;
    index?: number;
  }>;
  task?: 'explain' | 'generate';
}
export interface EditorAdapter {
  context(): Context | null;
  /** 协作助手专用：有选区用选区，无选区用整表 surrounding。 */
  resolveContext?(): Context | null;
  /** 表格助手提问：附带当前选区，供写入补丁对齐行列。 */
  askContext?(includeSelection: boolean): Context | null;
  syncSheetCache?(opts?: { regenerateProfile?: boolean }): Promise<void>;
  cacheStale?(): boolean;
  anchor(): Context | null;
  apply(patch: Patch): void;
  undo(): void;
  export(): void;
  load(asset: {
    id: string;
    name: string;
    revision: number;
    content?: unknown;
    filename?: string | null;
    size?: number | null;
  }): Promise<void> | void;
  snapshot?(): unknown;
  persist?: () => Promise<void>;
}
export function download(name: string, value: string) {
  const url = URL.createObjectURL(new Blob([value], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function saved(key: string) {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; }
}
export function persist(key: string, data: unknown) {
  try { localStorage.setItem(key, JSON.stringify(data)); } catch {
    window.dispatchEvent(new CustomEvent('office-error', { detail: '本地存储已满，请导出备份。' }));
  }
}
