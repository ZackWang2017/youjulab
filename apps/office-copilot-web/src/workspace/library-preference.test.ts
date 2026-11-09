import { afterEach, describe, expect, it, vi } from 'vitest';
import { libraryExpanded, saveLibraryExpanded } from './library-preference';

afterEach(() => vi.unstubAllGlobals());

describe('library group preferences', () => {
  it('defaults to expanded and stores the selected state', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });
    expect(libraryExpanded('default')).toBe(true);
    saveLibraryExpanded('default', false);
    expect(libraryExpanded('default')).toBe(false);
    expect(values.get('youjulab:library-expanded:default')).toBe('false');
    saveLibraryExpanded('default', true);
    expect(libraryExpanded('default')).toBe(true);
  });

  it('restores persisted preferences independently for each user', () => {
    vi.stubGlobal('localStorage', { getItem: (key: string) => key.endsWith(':alice') ? 'false' : 'true' });
    expect(libraryExpanded('alice')).toBe(false);
    expect(libraryExpanded('bob')).toBe(true);
  });

  it('still toggles when browser storage is unavailable', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('blocked'); },
    });
    expect(libraryExpanded('restricted')).toBe(true);
    saveLibraryExpanded('restricted', false);
    expect(libraryExpanded('restricted')).toBe(false);
  });
});
