import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { notebookPositionPatch, restoreNotebookPosition } from './notebook-position';

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 16));
  vi.stubGlobal('cancelAnimationFrame', (id: ReturnType<typeof setTimeout>) => clearTimeout(id));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function host(height = 2000, viewport = 400) {
  let top = 0;
  return {
    scrollHeight: height,
    clientHeight: viewport,
    get scrollTop() { return top; },
    set scrollTop(value: number) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  };
}

describe('PDF asset notebook reading position', () => {
  it('does not overwrite saved position with the zero from a hidden notebook', () => {
    const saved = { notebookScrollTop: 730 };
    expect({ ...saved, ...notebookPositionPatch(host(0, 0), false) }).toEqual(saved);
    expect(notebookPositionPatch(host(0, 0), true)).toEqual({});
  });

  it('records intentional scrolling back to the top', () => {
    expect(notebookPositionPatch(host(), true)).toEqual({ notebookScrollTop: 0 });
  });

  it('waits for editor height and corrects a late Tiptap scroll reset', () => {
    const el = host(0, 0);
    const done = vi.fn();
    restoreNotebookPosition(el, 730, () => true, done);
    vi.advanceTimersByTime(48);
    el.scrollHeight = 2000;
    el.clientHeight = 400;
    vi.advanceTimersByTime(16);
    expect(el.scrollTop).toBe(730);
    el.scrollTop = 0;
    vi.advanceTimersByTime(80);
    expect(el.scrollTop).toBe(730);
    expect(done).toHaveBeenCalledOnce();
  });

  it('does not apply an old asset restoration to the next PDF', () => {
    const el = host();
    let id = 'a';
    restoreNotebookPosition(el, 730, () => id === 'a', vi.fn());
    id = 'b';
    restoreNotebookPosition(el, 120, () => id === 'b', vi.fn());
    vi.runAllTimers();
    expect(el.scrollTop).toBe(120);
  });

  it('clamps a position when the note was shortened', () => {
    const el = host(600, 400);
    const done = vi.fn();
    restoreNotebookPosition(el, 900, () => true, done);
    vi.runAllTimers();
    expect(el.scrollTop).toBe(200);
    expect(done).toHaveBeenCalledOnce();
  });

  it('lets user input cancel automatic restoration', () => {
    const el = host();
    const cancel = restoreNotebookPosition(el, 900, () => true, vi.fn());
    cancel();
    el.scrollTop = 50;
    vi.runAllTimers();
    expect(el.scrollTop).toBe(50);
  });
});
