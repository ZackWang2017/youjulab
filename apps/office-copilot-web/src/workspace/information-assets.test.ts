import { beforeEach, describe, expect, it, vi } from 'vitest';
import { office } from '../office-client/client';
import { bootstrap, openAsset, selectProject } from './session';

vi.mock('../office-client/client', () => ({ office: { json: vi.fn() }, clearPdfFileCache: vi.fn() }));
vi.mock('../remote/client', () => ({ remoteToken: vi.fn() }));

describe('information workspace assets', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(office.json).mockImplementation(async (path: string) => {
      if (path === '/auth/me' || /\/projects\/.*\/select/.test(path) || /\/assets\/.*\/select/.test(path)) {
        return { id: 'user', last_project_id: 'alpha', last_assets: {} };
      }
      if (path === '/projects') return { projects: [{ id: 'alpha' }, { id: 'beta' }] };
      if (path.startsWith('/api/information/groups')) {
        const project = new URLSearchParams(path.split('?')[1]).get('project_id');
        return { groups: [{ id: project === 'alpha' ? 1 : 2, asset_id: `${project}-news`, project_id: project, kind: 'favorites' }] };
      }
      if (path.startsWith('/assets/recent')) {
        const project = new URLSearchParams(path.split('?')[1]).get('project_id');
        return { assets: [{ id: `${project}-news`, kind: 'information', project_id: project }] };
      }
      if (path.includes('/assets?kind=information')) {
        const project = path.split('/')[2];
        return { assets: [{ id: `${project}-news`, kind: 'information', project_id: project }] };
      }
      if (path.includes('/assets?kind=')) return { assets: [] };
      if (path === '/assets/alpha-news') return { id: 'alpha-news', kind: 'information', project_id: 'alpha', revision: 0, content: { format: 'invp-information-group-v1', group_id: 1 } };
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  it('loads groups and recent assets within the selected project', async () => {
    const alpha = await bootstrap();
    const beta = await selectProject('beta');
    expect(alpha.assets.information).toMatchObject([{ id: 'alpha-news', information_kind: 'favorites' }]);
    expect(beta.assets.information).toMatchObject([{ id: 'beta-news', information_kind: 'favorites' }]);
    expect(beta.recentAssets.map(asset => asset.id)).toEqual(['beta-news']);
    expect(office.json).toHaveBeenCalledWith('/api/information/groups?project_id=alpha');
    expect(office.json).toHaveBeenCalledWith('/api/information/groups?project_id=beta');
  });

  it('opens an information UUID without reordering recents, and tracks library opens', async () => {
    const recent = await openAsset('alpha-news', false);
    expect(recent.asset.content.group_id).toBe(1);
    expect(office.json).toHaveBeenCalledWith('/assets/alpha-news/select?track_recent=false', { method: 'POST' });
    await openAsset('alpha-news');
    expect(office.json).toHaveBeenCalledWith('/assets/alpha-news/select', { method: 'POST' });
    expect(vi.mocked(office.json).mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
  });
});
