import { describe, expect, it } from 'vitest';
import type { EditorAdapter } from './types';
import { assertEditorIdentity, trackEditorIdentity } from './editor-identity';
import { assertSheetContentOwner, sheetBindings } from '../editors/sheet/asset-binding';

describe('asset ownership during navigation', () => {
  it('does not treat a new navigation selection as a loaded workbook', async () => {
    const editor = trackEditorIdentity({ load: async () => {} } as unknown as EditorAdapter);
    await editor.load({ id: 'tencent', name: 'data', revision: 0 });
    expect(() => assertEditorIdentity('allocation', editor)).toThrow();
    await editor.load({ id: 'allocation', name: 'plan', revision: 0 });
    expect(() => assertEditorIdentity('allocation', editor)).not.toThrow();
  });

  it('blocks save while loading or after failed load', async () => {
    let finish!: () => void;
    const editor = trackEditorIdentity({ load: () => new Promise<void>(resolve => { finish = resolve; }) } as unknown as EditorAdapter);
    const pending = editor.load({ id: 'a', name: 'a', revision: 0 });
    expect(() => assertEditorIdentity('a', editor)).toThrow();
    finish();
    await pending;
    expect(editor.loadedAssetId?.()).toBe('a');
    const failed = trackEditorIdentity({ load: async () => { throw new Error('failed'); } } as unknown as EditorAdapter);
    await expect(failed.load({ id: 'b', name: 'b', revision: 0 })).rejects.toThrow();
    expect(() => assertEditorIdentity('b', failed)).toThrow();
  });

  it('invalidates callbacks even when reopening the same workbook', () => {
    const bindings = sheetBindings();
    const first = bindings.begin('a');
    const next = bindings.begin('a');
    expect(bindings.current(first)).toBe(false);
    expect(bindings.current(next)).toBe(true);
    bindings.begin('b');
    expect(bindings.current(next)).toBe(false);
  });

  it('rejects foreign snapshots without rewriting their identity', () => {
    const content = { id: 'tencent', sheets: {} };
    expect(() => assertSheetContentOwner('allocation', content)).toThrow();
    expect(content.id).toBe('tencent');
    expect(() => assertSheetContentOwner('allocation', {})).not.toThrow();
  });
});
