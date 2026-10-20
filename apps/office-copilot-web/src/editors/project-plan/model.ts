export const PROJECT_PLAN_FORMAT = 'invp-project-plan-v1' as const;

export type ProjectPlanTask = {
  id: string;
  name: string;
  parentId: string | null;
  startDate: string;
  endDate: string;
  progress: number;
  note: string;
  order: number;
};

export type ProjectPlanContent = {
  format: typeof PROJECT_PLAN_FORMAT;
  tasks: ProjectPlanTask[];
};

type RawTask = Partial<ProjectPlanTask> & Record<string, unknown>;

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function dateText(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

export function todayRange() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { startDate: dateText(start), endDate: dateText(end) };
}

export function parseDate(value: unknown): string | null {
  if (typeof value !== 'string' || !DATE.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(year, month - 1, day);
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day ? value : null;
}

export function addDays(value: string, days: number) {
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + days);
  return dateText(date);
}

function newId(index: number, used: Set<string>) {
  let id = `task-${index + 1}`;
  let suffix = 1;
  while (used.has(id)) id = `task-${index + 1}-${suffix++}`;
  return id;
}

function directChildren(tasks: ProjectPlanTask[], parentId: string) {
  return tasks.filter(task => task.parentId === parentId);
}

function isCyclicParent(task: ProjectPlanTask, byId: Map<string, ProjectPlanTask>) {
  const seen = new Set<string>([task.id]);
  let parentId = task.parentId;
  while (parentId) {
    if (seen.has(parentId)) return true;
    seen.add(parentId);
    parentId = byId.get(parentId)?.parentId || null;
  }
  return false;
}

export function deriveParentValues(tasks: ProjectPlanTask[]) {
  const next = tasks.map(task => ({ ...task }));
  const byId = new Map(next.map(task => [task.id, task]));
  const depth = (task: ProjectPlanTask, seen = new Set<string>()): number => {
    if (!task.parentId || seen.has(task.id)) return 0;
    const parent = byId.get(task.parentId);
    return parent ? depth(parent, new Set(seen).add(task.id)) + 1 : 0;
  };
  [...next].sort((a, b) => depth(b) - depth(a)).forEach(parent => {
    const children = directChildren(next, parent.id);
    if (!children.length) return;
    parent.startDate = children.reduce((earliest, child) => child.startDate < earliest ? child.startDate : earliest, children[0].startDate);
    parent.endDate = children.reduce((latest, child) => child.endDate > latest ? child.endDate : latest, children[0].endDate);
    parent.progress = Math.round(children.reduce((total, child) => total + child.progress, 0) / children.length);
  });
  return next;
}

export function normalizePlan(value: unknown): ProjectPlanContent {
  const rawTasks = value && typeof value === 'object' && Array.isArray((value as { tasks?: unknown }).tasks)
    ? (value as { tasks: unknown[] }).tasks : [];
  const fallback = todayRange();
  const used = new Set<string>();
  const tasks = rawTasks.map((raw, index) => {
    const source = raw && typeof raw === 'object' ? raw as RawTask : {};
    const requested = typeof source.id === 'string' ? source.id.trim() : '';
    const id = requested && !used.has(requested) ? requested : newId(index, used);
    used.add(id);
    const startDate = parseDate(source.startDate) || fallback.startDate;
    const candidateEnd = parseDate(source.endDate) || fallback.endDate;
    return {
      id,
      name: typeof source.name === 'string' && source.name.trim() ? source.name.trim().slice(0, 160) : `事项 ${index + 1}`,
      parentId: typeof source.parentId === 'string' && source.parentId.trim() ? source.parentId.trim() : null,
      startDate,
      endDate: candidateEnd < startDate ? startDate : candidateEnd,
      progress: Math.round(Math.min(100, Math.max(0, Number(source.progress) || 0))),
      note: typeof source.note === 'string' ? source.note.slice(0, 4000) : '',
      order: Number.isFinite(Number(source.order)) ? Math.max(0, Math.trunc(Number(source.order))) : index,
    };
  });
  const byId = new Map(tasks.map(task => [task.id, task]));
  tasks.forEach(task => {
    if (!task.parentId || !byId.has(task.parentId) || task.parentId === task.id) task.parentId = null;
  });
  tasks.forEach(task => { if (isCyclicParent(task, byId)) task.parentId = null; });
  const derivedTasks = deriveParentValues(tasks);
  const position = new Map(derivedTasks.map((task, index) => [task.id, index]));
  derivedTasks.sort((a, b) => (a.parentId || '').localeCompare(b.parentId || '')
    || a.startDate.localeCompare(b.startDate)
    || a.endDate.localeCompare(b.endDate)
    || a.order - b.order
    || position.get(a.id)! - position.get(b.id)!);
  const siblingOrder = new Map<string, number>();
  derivedTasks.forEach(task => {
    const key = task.parentId || '__root__';
    task.order = siblingOrder.get(key) || 0;
    siblingOrder.set(key, task.order + 1);
  });
  return { format: PROJECT_PLAN_FORMAT, tasks: deriveParentValues(derivedTasks) };
}

export function addTask(tasks: ProjectPlanTask[], parentId: string | null) {
  const parent = parentId ? tasks.find(task => task.id === parentId) : undefined;
  const range = parent ? { startDate: parent.startDate, endDate: parent.endDate } : todayRange();
  const used = new Set(tasks.map(task => task.id));
  return {
    id: newId(tasks.length, used), name: '新事项', parentId: parent?.id || null,
    ...range, progress: 0, note: '',
    order: tasks.filter(task => task.parentId === (parent?.id || null)).length,
  } satisfies ProjectPlanTask;
}

export function addSiblingTask(tasks: ProjectPlanTask[], taskId: string) {
  const current = tasks.find(task => task.id === taskId);
  if (!current) return null;
  const siblings = tasks
    .filter(task => task.parentId === current.parentId)
    .sort((a, b) => a.order - b.order);
  const index = siblings.findIndex(task => task.id === current.id);
  const used = new Set(tasks.map(task => task.id));
  const task: ProjectPlanTask = {
    id: newId(tasks.length, used),
    name: '新事项',
    parentId: current.parentId,
    startDate: current.startDate,
    endDate: current.endDate,
    progress: 0,
    note: '',
    order: index + 1,
  };
  siblings.splice(index + 1, 0, task);
  const orders = new Map(siblings.map((item, order) => [item.id, order]));
  const next = tasks.map(item => item.parentId === current.parentId
    ? { ...item, order: orders.get(item.id)! }
    : { ...item });
  return { task, tasks: normalizePlan({ tasks: [...next, task] }).tasks };
}

export function canMoveTask(tasks: ProjectPlanTask[], taskId: string, parentId: string | null) {
  if (!parentId) return true;
  if (taskId === parentId || !tasks.some(task => task.id === parentId)) return false;
  let cursor = tasks.find(task => task.id === parentId);
  const seen = new Set<string>();
  while (cursor?.parentId && !seen.has(cursor.id)) {
    if (cursor.parentId === taskId) return false;
    seen.add(cursor.id);
    cursor = tasks.find(task => task.id === cursor!.parentId);
  }
  return true;
}

export function moveTask(tasks: ProjectPlanTask[], taskId: string, parentId: string | null, order: number) {
  if (!canMoveTask(tasks, taskId, parentId)) return null;
  const next = tasks.map(task => ({ ...task }));
  const moving = next.find(task => task.id === taskId);
  if (!moving) return null;
  moving.parentId = parentId;
  const siblings = next.filter(task => task.parentId === parentId && task.id !== taskId).sort((a, b) => a.order - b.order);
  siblings.splice(Math.max(0, Math.min(order, siblings.length)), 0, moving);
  siblings.forEach((task, index) => { task.order = index; });
  return deriveParentValues(next);
}

export function cascadeDelete(tasks: ProjectPlanTask[], taskId: string) {
  const deleted = new Set<string>([taskId]);
  let changed = true;
  while (changed) {
    changed = false;
    tasks.forEach(task => {
      if (task.parentId && deleted.has(task.parentId) && !deleted.has(task.id)) {
        deleted.add(task.id);
        changed = true;
      }
    });
  }
  return { deleted: [...deleted], tasks: deriveParentValues(tasks.filter(task => !deleted.has(task.id))) };
}
