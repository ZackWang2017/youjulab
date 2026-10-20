import { describe, expect, it } from 'vitest';
import { addSiblingTask, cascadeDelete, canMoveTask, deriveParentValues, moveTask, normalizePlan } from './model';

describe('project plan model', () => {
  it('normalizes invalid data and prevents parent cycles', () => {
    const plan = normalizePlan({ tasks: [
      { id: 'a', parentId: 'b', startDate: 'bad', endDate: '2026-02-01', progress: 160 },
      { id: 'b', parentId: 'a', startDate: '2026-02-03', endDate: '2026-02-01', progress: -4 },
      { id: 'c', parentId: null, startDate: '2026-02-03', endDate: '2026-02-04', progress: 160 },
    ] });
    expect(plan.tasks.map(task => task.id).sort()).toEqual(['a', 'b', 'c']);
    expect(plan.tasks.find(task => task.id === 'a')?.parentId).toBeNull();
    expect(plan.tasks.find(task => task.id === 'b')?.parentId).toBe('a');
    expect(plan.tasks.find(task => task.id === 'c')?.progress).toBe(100);
    expect(plan.tasks.find(task => task.id === 'b')?.progress).toBe(0);
    expect(plan.tasks.every(task => task.note === '')).toBe(true);
    const child = plan.tasks.find(task => task.id === 'b')!;
    expect(child.endDate).toBe(child.startDate);
  });

  it('derives parent dates and progress from direct children', () => {
    const result = deriveParentValues([
      { id: 'parent', name: '父', parentId: null, startDate: '2026-01-01', endDate: '2026-01-01', progress: 0, note: '', order: 0 },
      { id: 'a', name: 'A', parentId: 'parent', startDate: '2026-01-03', endDate: '2026-01-05', progress: 20, note: '准备材料', order: 0 },
      { id: 'b', name: 'B', parentId: 'parent', startDate: '2026-01-01', endDate: '2026-01-09', progress: 81, note: '', order: 1 },
    ]);
    expect(result.find(task => task.id === 'parent')).toMatchObject({ startDate: '2026-01-01', endDate: '2026-01-09', progress: 51 });
  });

  it('rejects cycle moves and cascades deletion', () => {
    const tasks = normalizePlan({ tasks: [
      { id: 'a', name: 'A', parentId: null }, { id: 'b', name: 'B', parentId: 'a' }, { id: 'c', name: 'C', parentId: 'b' },
    ] }).tasks;
    expect(canMoveTask(tasks, 'a', 'c')).toBe(false);
    expect(moveTask(tasks, 'a', 'c', 0)).toBeNull();
    expect(cascadeDelete(tasks, 'a').deleted).toHaveLength(3);
  });

  it('adds a sibling after the selected task', () => {
    const tasks = normalizePlan({ tasks: [
      { id: 'root', name: '根事项', parentId: null, startDate: '2026-02-01', endDate: '2026-02-03' },
      { id: 'a', name: 'A', parentId: 'root', startDate: '2026-02-01', endDate: '2026-02-02', order: 0 },
      { id: 'b', name: 'B', parentId: 'root', startDate: '2026-02-03', endDate: '2026-02-04', order: 1 },
    ] }).tasks;
    const result = addSiblingTask(tasks, 'a');
    expect(result?.task).toMatchObject({ parentId: 'root', startDate: '2026-02-01', endDate: '2026-02-02' });
    expect(result?.tasks.filter(task => task.parentId === 'root').map(task => task.id)).toEqual(['a', result?.task.id, 'b']);
  });

  it('orders sibling plans by their start date', () => {
    const tasks = normalizePlan({ tasks: [
      { id: 'late', name: '较晚', parentId: null, startDate: '2026-03-08', endDate: '2026-03-09', order: 0 },
      { id: 'early', name: '较早', parentId: null, startDate: '2026-03-01', endDate: '2026-03-02', order: 1 },
    ] }).tasks;
    expect(tasks.filter(task => task.parentId === null).sort((a, b) => a.order - b.order).map(task => task.id)).toEqual(['early', 'late']);
  });
});
