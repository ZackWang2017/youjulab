import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('../office-client/client', () => ({ office: { json: vi.fn() }, clearPdfFileCache: vi.fn() }));
vi.mock('../remote/client', () => ({ remoteToken: vi.fn() }));

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.stubGlobal('window', { dispatchEvent: vi.fn() });
  vi.stubGlobal('CustomEvent', class { constructor(public type: string, public init: unknown) {} });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

async function conflict() {
  const session = await import('./session');
  const { office } = await import('../office-client/client');
  vi.mocked(office.json).mockRejectedValueOnce(Object.assign(new Error('changed'), { status: 409 }));
  session.queueSave('original', { draft: 1 });
  await session.flushSave();
  expect(session.hasUnsavedWork()).toBe(true);
  return { session, json: vi.mocked(office.json) };
}

it('pauses autosave and overwrites only the freshly fetched revision', async () => {
  const { session, json } = await conflict();
  session.queueSave('original', { draft: 2 });
  await vi.advanceTimersByTimeAsync(1000);
  expect(json).toHaveBeenCalledTimes(1);
  json.mockResolvedValueOnce({ id: 'original', revision: 9, content: { remote: true } });
  json.mockResolvedValueOnce({ id: 'original', revision: 10, content: { draft: 2 } });
  await session.resolveSaveConflict('original', 'overwrite');
  expect(JSON.parse(json.mock.calls[2][1]!.body as string)).toEqual({ content: { draft: 2 }, revision: 9 });
  expect(session.hasUnsavedWork()).toBe(false);
});

it('discards without writing to the original', async () => {
  const { session, json } = await conflict();
  json.mockResolvedValueOnce({ id: 'original', revision: 9, content: { remote: true } });
  const asset = await session.resolveSaveConflict('original', 'discard');
  expect(asset.content).toEqual({ remote: true });
  expect(json).toHaveBeenCalledTimes(2);
  expect(session.hasUnsavedWork()).toBe(false);
});

it('copies the local draft and retains it if copying fails', async () => {
  const { session, json } = await conflict();
  json.mockResolvedValueOnce({ id: 'original', revision: 9 }).mockRejectedValueOnce(new Error('offline'));
  await expect(session.resolveSaveConflict('original', 'copy')).rejects.toThrow('offline');
  expect(session.hasUnsavedWork()).toBe(true);
  json.mockResolvedValueOnce({ id: 'original', revision: 9 }).mockResolvedValueOnce({ id: 'copy', revision: 1 });
  const asset = await session.resolveSaveConflict('original', 'copy');
  expect(asset.id).toBe('copy');
  expect(json.mock.calls.at(-1)![0]).toBe('/assets/original/copy');
  expect(JSON.parse(json.mock.calls.at(-1)![1]!.body as string)).toEqual({ content: { draft: 1 } });
});
