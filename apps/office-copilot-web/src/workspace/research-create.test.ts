import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('../office-client/client', () => ({ office: { json: vi.fn() }, clearPdfFileCache: vi.fn() }));
import { office } from '../office-client/client';
import { cacheAsset, createAsset, openAsset } from './session';

describe('research asset creation with an older running backend', () => {
  beforeEach(() => vi.mocked(office.json).mockReset());
  const content = { format: 'invp-research-diagram-v1', type: 'financial_accounts', accounts: [], rules: [], stock: { name: '腾讯控股', ticker_code: '00700.HK' }, standard: 'HKFRS' };
  it('saves the chosen diagram before returning a legacy default asset', async () => {
    vi.mocked(office.json)
      .mockResolvedValueOnce({ id: 'research-legacy', revision: 0, content: { type: 'timeline' } })
      .mockResolvedValueOnce({ id: 'research-legacy', revision: 1, content });
    const asset = await createAsset('project', 'research', '账户图', 'project', content);
    expect(asset.content.type).toBe('financial_accounts');
    expect(office.json).toHaveBeenLastCalledWith('/assets/research-legacy', { method: 'PUT', body: JSON.stringify({ content, revision: 0 }) });
  });
  it('avoids an extra save when initial content is already persisted', async () => {
    vi.mocked(office.json).mockResolvedValueOnce({ id: 'research-new', revision: 0, content });
    await createAsset('project', 'research', '账户图', 'project', content);
    expect(office.json).toHaveBeenCalledTimes(1);
  });
  it('does not silently open the wrong canvas if the fallback also fails', async () => {
    vi.mocked(office.json).mockResolvedValue({ id: 'research-bad', revision: 0, content: { type: 'timeline' } });
    await expect(createAsset('project', 'research', '账户图', 'project', content)).rejects.toThrow('研究图类型保存失败');
  });
  it('reloads repaired research content instead of returning the cached timeline', async () => {
    cacheAsset({ id: 'repaired', kind: 'research', name: '账户图', revision: 2, project_id: 'project', updated_at: '', content: { format: 'invp-research-diagram-v1', type: 'timeline' } });
    vi.mocked(office.json).mockImplementation(async path => path === '/assets/repaired'
      ? { id: 'repaired', kind: 'research', revision: 3, content }
      : {});
    const opened = await openAsset('repaired', false);
    expect(opened.asset.content.type).toBe('financial_accounts');
    expect(office.json).toHaveBeenCalledWith('/assets/repaired', {});
  });
});
