import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { informationApi, informationSkills } from './api';

vi.mock('../remote/client', () => ({ remoteToken: () => null }));

describe('local information API', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => key === 'office-token' ? 'local-user-token' : null,
      removeItem: vi.fn(),
    });
    vi.stubGlobal('window', { youju: { officeApiBase: 'http://127.0.0.1:8001' } });
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ groups: [], holdings: [], adapters: [], reviews: [] })));
  });

  afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

  it('routes every data operation through Office with the local bearer token', async () => {
    await informationApi.list('project/id');
    await informationApi.create({ project_id: 'project/id', name: 'News', kind: 'finance_api' });
    await informationApi.update(1, { name: 'Updated' });
    await informationApi.remove(1);
    await informationApi.items(1, false, { review: true });
    await informationApi.fetchToday(1);
    await informationApi.summary(1);
    await informationApi.reviews(1);
    await informationApi.review(2);
    await informationApi.clear('project-a');
    await informationApi.favorite(3, false);
    await informationApi.hide(3);
    await informationApi.adapters('finance rss');
    await informationApi.holdings();
    await informationApi.wereadStatus();
    await informationApi.wereadLogin();
    await informationApi.wereadLoginStatus('session/id');

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method || 'GET'])).toEqual([
      ['/groups?project_id=project%2Fid', 'GET'], ['/groups', 'POST'], ['/groups/1', 'PUT'], ['/groups/1', 'DELETE'],
      ['/groups/1/items?today=0&review=1', 'GET'], ['/groups/1/fetch', 'POST'],
      ['/groups/1/summary', 'GET'], ['/groups/1/reviews', 'GET'], ['/reviews/2', 'GET'],
      ['/clear?project_id=project-a', 'POST'], ['/items/3/favorite', 'POST'], ['/items/3', 'DELETE'],
      ['/adapters?kind=finance%20rss', 'GET'], ['/holdings', 'GET'],
      ['/weread/status', 'GET'], ['/weread/login', 'POST'], ['/weread/login/session%2Fid', 'GET'],
    ].map(([path, method]) => [`http://127.0.0.1:8001/api/information${path}`, method]));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ project_id: 'project/id' });
    for (const [, init] of fetchMock.mock.calls) {
      expect(init.headers.get('Authorization')).toBe('Bearer local-user-token');
      expect(init.headers.has('X-Remote-Token')).toBe(false);
      if (init.body) expect(init.headers.get('Content-Type')).toBe('application/json');
    }
  });

  it('keeps assistant streams on Office', async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"done"}\n\n'));
    const onEvent = vi.fn();
    await informationSkills.review({ group_id: 1 }, onEvent);
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8001/assistant/information/review');
    expect(onEvent).toHaveBeenCalledWith({ type: 'done' });
  });

  it('displays legacy favorites names as 资讯收藏栏', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ groups: [{ id: 1, kind: 'favorites', name: '收藏夹' }] })));
    expect(await informationApi.list('project')).toMatchObject([{ name: '资讯收藏栏' }]);
  });
});
