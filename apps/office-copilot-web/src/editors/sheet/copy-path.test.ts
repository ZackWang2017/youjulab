import { afterEach, describe, expect, it, vi } from 'vitest';
import { copySheetPath, sheetAssetPath } from './copy-path';

afterEach(() => vi.unstubAllGlobals());

describe('sheet path copying', () => {
  it('includes project, asset and worksheet without changing the titles', () => {
    expect(sheetAssetPath('腾讯控股（00700.hk）-数据分析', '游戏业务-数据'))
      .toBe('腾讯控股（00700.hk）-数据分析-游戏业务-数据');
    expect(() => sheetAssetPath(undefined, '旧工作表')).toThrow();
  });

  it('writes the complete path to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    await copySheetPath('项目-表格-工作表');
    expect(writeText).toHaveBeenCalledWith('项目-表格-工作表');
  });

  it('falls back on denied clipboard access and removes the temporary input', async () => {
    const input = { value: '', style: { cssText: '' }, setAttribute: vi.fn(), select: vi.fn(), remove: vi.fn() };
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    vi.stubGlobal('document', { createElement: () => input, body: { append: vi.fn() }, execCommand: vi.fn(() => true) });
    await copySheetPath('项目-表格-工作表');
    expect(input.value).toBe('项目-表格-工作表');
    expect(input.remove).toHaveBeenCalledOnce();
  });

  it('reports failed copying instead of a success notification', async () => {
    const remove = vi.fn();
    vi.stubGlobal('navigator', {});
    vi.stubGlobal('document', {
      createElement: () => ({ style: {}, setAttribute() {}, select() {}, remove }),
      body: { append() {} }, execCommand: () => false,
    });
    await expect(copySheetPath('项目-表格-工作表')).rejects.toThrow('剪贴板权限');
    expect(remove).toHaveBeenCalledOnce();
  });
});
