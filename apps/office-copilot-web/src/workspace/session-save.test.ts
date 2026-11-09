import { afterEach, expect, it, vi } from 'vitest';

vi.mock('../office-client/client', () => ({
  office: { json: vi.fn(async (path: string, options: { body: string }) => {
    const body = JSON.parse(options.body);
    return { id: path.split('/').pop(), kind: 'sheet', revision: body.revision + 1, content: body.content };
  }) },
  clearPdfFileCache: vi.fn(),
}));
vi.mock('../remote/client', () => ({ remoteToken: vi.fn() }));

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('flushes each pending asset to its own endpoint during rapid switches', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { dispatchEvent: vi.fn() });
  vi.stubGlobal('CustomEvent', class { constructor(public type: string) {} });
  const { queueSave, flushSave, rememberRevision } = await import('./session');
  const { office } = await import('../office-client/client');
  rememberRevision('tencent', 5);
  rememberRevision('allocation', 8);
  queueSave('tencent', { id: 'tencent', sheets: { a: {} } });
  queueSave('allocation', { id: 'allocation', sheets: { b: {} } });
  await flushSave();
  const calls = vi.mocked(office.json).mock.calls;
  expect(calls.map(call => call[0])).toEqual(['/assets/tencent', '/assets/allocation']);
  for (const [path, options] of calls) {
    const body = JSON.parse(options!.body as string);
    expect(body.content.id).toBe(path.split('/').pop());
  }
});
