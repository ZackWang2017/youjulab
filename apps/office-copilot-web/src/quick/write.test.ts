import { describe, expect, it } from 'vitest';
import { appendNoteToDoc, applyTodoOp, applyTodoOpToCalendar, ConflictError, mergeAppendedTail, saveWithRetry, type QuickAsset } from './write';

describe('mergeAppendedTail', () => {
  const p = (text: string) => ({ type: 'paragraph', content: [{ type: 'text', text }] });
  it('appends quick notes added on the server after local edits', () => {
    const base = { type: 'doc', content: [p('A'), { type: 'paragraph' }] };
    const server = appendNoteToDoc(base, '快速', new Date(2026, 0, 1, 8, 0));
    const local = { type: 'doc', content: [p('A 改过'), p('B')] };
    const merged = mergeAppendedTail(base, local, server)!;
    expect(merged.content).toEqual([p('A 改过'), p('B'), ...server.content!.slice(1)]);
  });

  it('refuses when the server changed existing content', () => {
    expect(mergeAppendedTail({ type: 'doc', content: [p('A')] }, { type: 'doc', content: [p('L')] }, { type: 'doc', content: [p('X')] })).toBeNull();
  });
});

describe('quick note append', () => {
  it('appends a timestamp heading and paragraphs, dropping trailing empty paragraphs only', () => {
    const doc = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: '已有' }] }, { type: 'paragraph' }] };
    const next = appendNoteToDoc(doc, '第一行\n\n第三行\n', new Date(2026, 10, 11, 9, 5));
    expect(next.content?.[0]).toEqual(doc.content[0]);
    expect(next.content?.slice(1)).toEqual([
      { type: 'heading', attrs: { level: 3 }, content: [{ type: 'text', text: '2026-11-11 09:05' }] },
      { type: 'paragraph', content: [{ type: 'text', text: '第一行' }] },
      { type: 'paragraph' },
      { type: 'paragraph', content: [{ type: 'text', text: '第三行' }] },
    ]);
    expect(doc.content).toHaveLength(2);
  });

  it('starts a document when the notebook is empty or invalid', () => {
    expect(appendNoteToDoc(null, 'x', new Date(2026, 0, 2, 3, 4)).content).toHaveLength(2);
  });
});

describe('quick todo ops', () => {
  const items = [{ done: false, text: '买书' }, { done: true, text: '写周报' }];

  it('locates items by text so reordered lists stay correct', () => {
    const reordered = [items[1], items[0]];
    expect(applyTodoOp(reordered, { type: 'toggle', text: '买书', done: true })).toEqual([
      { done: true, text: '写周报' }, { done: true, text: '买书' },
    ]);
    expect(applyTodoOp(items, { type: 'delete', text: '写周报' })).toEqual([items[0]]);
    expect(applyTodoOp(items, { type: 'edit', text: '买书', next: '买两本书' })[0].text).toBe('买两本书');
    expect(applyTodoOp(items, { type: 'add', text: '  新待办 ' })[2]).toEqual({ done: false, text: '新待办' });
    expect(applyTodoOp(items, { type: 'delete', text: '不存在' })).toEqual(items);
  });

  it('changes only the todos field of the calendar', () => {
    const calendar = { format: 'invp-calendar-v1', weeks: { w: 1 }, todos: '- [ ] 买书' };
    const next = applyTodoOpToCalendar(calendar, { type: 'add', text: '开会' });
    expect(next).toEqual({ format: 'invp-calendar-v1', weeks: { w: 1 }, todos: '- [ ] 买书\n- [ ] 开会' });
  });

  it('archives, restores, and deletes only the requested archive scope', () => {
    const active = { done: false, text: 'same' };
    const archived = { done: true, text: 'same', archived: true };
    const both = [active, archived];
    expect(applyTodoOp(both, { type: 'archive', text: 'same', archived: true })).toEqual([
      { ...active, archived: true }, archived,
    ]);
    expect(applyTodoOp(both, { type: 'archive', text: 'same', archived: false })).toEqual([
      active, { done: true, text: 'same' },
    ]);
    expect(applyTodoOp(both, { type: 'delete', text: 'same', archived: true })).toEqual([active]);
    expect(applyTodoOp(both, { type: 'delete', text: 'same' })).toEqual([archived]);
    expect(applyTodoOp(both, { type: 'edit', text: 'same', next: 'new' })).toEqual([
      { done: false, text: 'new' }, archived,
    ]);
    expect(both).toEqual([active, archived]);
  });

  it('preserves archive metadata when adding or editing other items', () => {
    const raw = { todos: '- [ ] A <!-- youju-todo:archived -->', weeks: { w: 1 }, days: { d: 2 } };
    const result = applyTodoOpToCalendar(raw, { type: 'add', text: 'B' });
    expect(result.todos).toBe('- [ ] A <!-- youju-todo:archived -->\n- [ ] B');
    expect(result.weeks).toEqual(raw.weeks);
    expect(result.days).toEqual(raw.days);
  });
});

describe('saveWithRetry', () => {
  it('retries archive on the newest calendar without dropping other window changes', async () => {
    let loads = 0;
    const result = await saveWithRetry(
      async () => ++loads === 1
        ? { id: 'c', revision: 1, content: { todos: '- [ ] A' } }
        : { id: 'c', revision: 2, content: { todos: '- [ ] A\n- [x] B <!-- youju-todo:archived -->', days: { today: 'keep' } } },
      content => applyTodoOpToCalendar(content, { type: 'archive', text: 'A', archived: true }),
      async (asset, content) => {
        if (asset.revision === 1) throw new ConflictError();
        return { ...asset, content, revision: 3 };
      },
    );
    expect(result.content).toEqual({
      todos: '- [ ] A <!-- youju-todo:archived -->\n- [x] B <!-- youju-todo:archived -->', days: { today: 'keep' },
    });
  });

  it('reloads and reapplies the change after a revision conflict', async () => {
    const versions: QuickAsset[] = [
      { id: 'c', revision: 1, content: { todos: '- [ ] A' } },
      { id: 'c', revision: 2, content: { todos: '- [ ] A\n- [ ] B' } },
    ];
    let loads = 0;
    const saved: unknown[] = [];
    const result = await saveWithRetry(
      async () => versions[Math.min(loads++, 1)],
      content => applyTodoOpToCalendar(content, { type: 'add', text: 'C' }),
      async (asset, content) => {
        if (asset.revision === 1) throw new ConflictError();
        saved.push(content);
        return { ...asset, revision: asset.revision + 1, content };
      },
    );
    expect(loads).toBe(2);
    expect(result.revision).toBe(3);
    expect(saved).toEqual([{ todos: '- [ ] A\n- [ ] B\n- [ ] C' }]);
  });

  it('gives up after the attempt limit', async () => {
    await expect(saveWithRetry(
      async () => ({ id: 'n', revision: 1, content: null }),
      content => content,
      async () => { throw new ConflictError(); },
      2,
    )).rejects.toBeInstanceOf(ConflictError);
  });
});
