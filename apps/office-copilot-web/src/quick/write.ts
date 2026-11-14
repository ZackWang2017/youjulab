/** 托盘快速记录的写入规则：笔记只追加、待办只改 todos 字段，版本冲突时重读重做。 */
import { parseTodoMarkdown, serializeTodoMarkdown, type TodoItem } from '../editors/calendar/todos';

export type QuickAsset = { id: string; kind?: string; revision: number; content?: unknown };

export class ConflictError extends Error {
  constructor(message = '内容已在其它窗口更新。') { super(message); this.name = 'ConflictError'; }
}

type DocNode = { type: string; attrs?: Record<string, unknown>; content?: DocNode[]; text?: string };

const EMPTY_DOC: DocNode = { type: 'doc', content: [] };

function pad(n: number) {
  return String(n).padStart(2, '0');
}

export function noteStamp(at: Date) {
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

function isEmptyParagraph(node: DocNode) {
  return node.type === 'paragraph' && !(node.content || []).some(child => (child.text || '').trim() || child.type !== 'text');
}

function paragraph(text: string): DocNode {
  return text ? { type: 'paragraph', content: [{ type: 'text', text }] } : { type: 'paragraph' };
}

/** 在笔记本文档末尾追加「时间小标题 + 正文段落」；去掉末尾空段落，不改动已有内容。 */
export function appendNoteToDoc(raw: unknown, text: string, at = new Date()): DocNode {
  const doc: DocNode = raw && typeof raw === 'object' && (raw as DocNode).type === 'doc'
    ? JSON.parse(JSON.stringify(raw))
    : JSON.parse(JSON.stringify(EMPTY_DOC));
  const content = [...(doc.content || [])];
  while (content.length && isEmptyParagraph(content[content.length - 1])) content.pop();
  const lines = text.replace(/\r\n/g, '\n').replace(/\s+$/, '').split('\n');
  content.push(
    { type: 'heading', attrs: { level: 3 }, content: [{ type: 'text', text: noteStamp(at) }] },
    ...lines.map(line => paragraph(line)),
  );
  return { ...doc, content };
}

function trimTrailingEmpty(nodes: DocNode[]) {
  const out = [...nodes];
  while (out.length && isEmptyParagraph(out[out.length - 1])) out.pop();
  return out;
}

/**
 * 主窗口笔记本保存冲突时合并：快速记录只在末尾追加，所以服务端内容 = 上次基线 + 追加尾部。
 * 把这段尾部接到本地内容后面；服务端不是「基线 + 追加」（被其它方式改过）时返回 null。
 */
export function mergeAppendedTail(base: unknown, local: unknown, server: unknown): DocNode | null {
  const nodesOf = (doc: unknown) => (doc && typeof doc === 'object' && Array.isArray((doc as DocNode).content) ? (doc as DocNode).content! : []);
  const baseNodes = trimTrailingEmpty(nodesOf(base));
  const serverNodes = nodesOf(server);
  if (serverNodes.length < baseNodes.length) return null;
  for (let i = 0; i < baseNodes.length; i++) {
    if (JSON.stringify(baseNodes[i]) !== JSON.stringify(serverNodes[i])) return null;
  }
  const tail = trimTrailingEmpty(serverNodes.slice(baseNodes.length));
  const localDoc: DocNode = local && typeof local === 'object' && (local as DocNode).type === 'doc'
    ? (local as DocNode) : { type: 'doc', content: [] };
  return { ...localDoc, content: [...trimTrailingEmpty(nodesOf(localDoc)), ...tail] };
}

export type TodoOp =
  | { type: 'add'; text: string }
  | { type: 'toggle'; text: string; done: boolean }
  | { type: 'edit'; text: string; next: string }
  | { type: 'archive'; text: string; archived: boolean }
  | { type: 'delete'; text: string; archived?: boolean };

/**
 * 按文本定位待办再修改：冲突重读后列表顺序可能变了，按下标会改错行。
 * 找不到目标（已被其它窗口删除或改名）时保持不变。
 */
export function applyTodoOp(items: TodoItem[], op: TodoOp): TodoItem[] {
  const next = items.map(item => ({ ...item }));
  if (op.type === 'add') {
    const text = op.text.trim();
    return text ? [...next, { done: false, text }] : next;
  }
  if (op.type === 'toggle') {
    // 同名待办优先改状态相反的那条，避免重复点击时改到另一条
    let index = next.findIndex(item => !item.archived && item.text === op.text && item.done !== op.done);
    if (index < 0) index = next.findIndex(item => !item.archived && item.text === op.text);
    if (index >= 0) next[index].done = op.done;
    return next;
  }
  const archived = op.type === 'archive' ? !op.archived : op.type === 'delete' ? Boolean(op.archived) : false;
  const index = next.findIndex(item => item.text === op.text && Boolean(item.archived) === archived);
  if (index < 0) return next;
  if (op.type === 'delete') return next.filter((_, i) => i !== index);
  if (op.type === 'archive') {
    if (op.archived) next[index].archived = true; else delete next[index].archived;
    return next;
  }
  const text = op.next.replace(/\r?\n/g, ' ').trim();
  if (!text) return next.filter((_, i) => i !== index);
  next[index].text = text;
  return next;
}

/** 只替换日历 content.todos，周计划、日计划等其它字段原样保留。 */
export function applyTodoOpToCalendar(raw: unknown, op: TodoOp): Record<string, unknown> {
  const content = raw && typeof raw === 'object' ? { ...(raw as Record<string, unknown>) } : {};
  const items = parseTodoMarkdown(typeof content.todos === 'string' ? content.todos : '');
  content.todos = serializeTodoMarkdown(applyTodoOp(items, op));
  return content;
}

/** 读取最新版本、计算新内容、带 revision 保存；409 时重新读取再算，最多 attempts 次。 */
export async function saveWithRetry(
  load: () => Promise<QuickAsset>,
  mutate: (content: unknown) => unknown,
  save: (asset: QuickAsset, content: unknown) => Promise<QuickAsset>,
  attempts = 3,
): Promise<QuickAsset> {
  let asset = await load();
  for (let attempt = 1; ; attempt++) {
    try {
      return await save(asset, mutate(asset.content));
    } catch (error) {
      if (!(error instanceof ConflictError) || attempt >= attempts) throw error;
      asset = await load();
    }
  }
}
