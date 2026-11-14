import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mountInformation } from './index';
import { informationApi } from './api';

vi.mock('lucide', () => ({ createIcons: vi.fn(), icons: {} }));
vi.mock('../ui/thinking-status', () => ({ mountThinkingStatus: vi.fn() }));
vi.mock('./dialogs', () => ({}));
vi.mock('./api', () => ({ informationApi: { items: vi.fn() }, informationSkills: {} }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function mounted() {
  const shell = { innerHTML: '', querySelector: () => null, querySelectorAll: () => [] };
  const section = { innerHTML: '', querySelector: () => shell, replaceChildren: vi.fn() };
  const onError = vi.fn();
  const controller = mountInformation(section as unknown as HTMLElement, onError);
  return { shell, section, onError, controller };
}

describe('information lifecycle', () => {
  beforeEach(() => vi.clearAllMocks());

  it('discards an older group response after selection changes', async () => {
    const old = deferred<any>();
    vi.mocked(informationApi.items).mockReturnValueOnce(old.promise).mockResolvedValueOnce({
      group: { id: 2, name: 'Current', kind: 'finance_api' } as any, items: [],
    });
    const { controller, shell } = mounted();
    const pending = controller.show(1, 'today');
    await controller.show(2, 'today');
    old.resolve({ group: { id: 1, name: 'Previous' }, items: [] });
    await pending;
    expect(shell.innerHTML).toContain('Current');
    expect(shell.innerHTML).not.toContain('Previous');
    expect(shell.innerHTML).toContain('role="tablist" aria-label="资讯功能"');
    expect(shell.innerHTML).toContain('data-info-view="today" aria-selected="true"');
    expect(shell.innerHTML).toContain('data-info-view="review" aria-selected="false"');
  });

  it('routes toolbar mode selection through the owner controller', async () => {
    const callbacks: Array<() => void> = [];
    const button = { dataset: { infoView: 'review' }, addEventListener: (_: string, fn: () => void) => callbacks.push(fn) };
    const shell = { innerHTML: '', querySelector: () => null, querySelectorAll: (selector: string) => selector === '[data-info-view]' ? [button] : [] };
    const section = { innerHTML: '', querySelector: () => shell };
    const onView = vi.fn();
    vi.mocked(informationApi.items).mockResolvedValue({ group: { id: 1, name: '资讯收藏栏', kind: 'favorites' } as any, items: [] });
    const controller = mountInformation(section as unknown as HTMLElement, vi.fn(), { onView });
    await controller.show(1, 'today');
    callbacks.at(-1)!();
    expect(onView).toHaveBeenCalledWith('review');
    expect(shell.innerHTML).toContain('资讯收藏栏');
  });

  it('invalidates pending work when destroyed for an account switch', async () => {
    const old = deferred<any>();
    vi.mocked(informationApi.items).mockReturnValueOnce(old.promise);
    const { controller, shell, section, onError } = mounted();
    const pending = controller.show(1, 'today');
    controller.destroy();
    const html = shell.innerHTML;
    old.resolve({ group: { id: 1, name: 'Previous account' }, items: [] });
    await pending;
    await controller.refresh();
    expect(section.replaceChildren).toHaveBeenCalledOnce();
    expect(shell.innerHTML).toBe(html);
    expect(informationApi.items).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });
});
