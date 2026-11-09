import { describe, expect, it } from 'vitest';
import { selectionFromItem } from './controller';
import { filterCatalogItems, fuzzyMatch, parseAssetIndexQuery, pathAfterSelectingKind, pathAfterSelectingProject, resolveKindKey } from './query';

describe('parseAssetIndexQuery', () => {
  it('ignores bare hash without slash', () => {
    expect(parseAssetIndexQuery('').active).toBe(false);
    expect(parseAssetIndexQuery('proj').active).toBe(false);
  });

  it('treats / as project stage', () => {
    const parsed = parseAssetIndexQuery('/');
    expect(parsed.active).toBe(true);
    expect(parsed.stage).toBe('projects');
    expect(parsed.projectQuery).toBe('');
  });

  it('filters projects while typing after /', () => {
    const parsed = parseAssetIndexQuery('/腾');
    expect(parsed.stage).toBe('projects');
    expect(parsed.projectQuery).toBe('腾');
  });

  it('enters kinds after project segment and slash', () => {
    const parsed = parseAssetIndexQuery('/腾讯/');
    expect(parsed.stage).toBe('kinds');
    expect(parsed.projectKey).toBe('腾讯');
    expect(parsed.kindQuery).toBe('');
  });

  it('filters kinds and then assets', () => {
    expect(parseAssetIndexQuery('/腾讯/pd').stage).toBe('kinds');
    expect(parseAssetIndexQuery('/腾讯/pd').kindQuery).toBe('pd');
    const assets = parseAssetIndexQuery('/腾讯/pdf/年报');
    expect(assets.stage).toBe('assets');
    expect(assets.projectKey).toBe('腾讯');
    expect(assets.kindKey).toBe('pdf');
    expect(assets.assetQuery).toBe('年报');
  });

  it('keeps extra slashes inside asset query', () => {
    expect(parseAssetIndexQuery('/a/pdf/foo/bar').assetQuery).toBe('foo/bar');
  });
});

describe('fuzzy and kinds', () => {
  it('matches substring and subsequence', () => {
    expect(fuzzyMatch('腾讯控股', '腾讯')).toBe(true);
    expect(fuzzyMatch('腾讯控股', '腾控')).toBe(true);
    expect(fuzzyMatch('腾讯控股', '阿里')).toBe(false);
  });

  it('resolves kind ids and labels', () => {
    expect(resolveKindKey('pdf')).toBe('pdf');
    expect(resolveKindKey('PDF')).toBe('pdf');
    expect(resolveKindKey('文档')).toBe('document');
    expect(resolveKindKey('nope')).toBeNull();
  });

  it('filters catalog items', () => {
    const items = filterCatalogItems([
      { id: '1', label: '年报', pathSegment: '年报' },
      { id: '2', label: '季报', pathSegment: '季报' },
    ], '年');
    expect(items.map(item => item.id)).toEqual(['1']);
  });

  it('builds advance paths', () => {
    expect(pathAfterSelectingProject('腾讯')).toBe('/腾讯/');
    expect(pathAfterSelectingKind('腾讯', 'pdf')).toBe('/腾讯/pdf/');
  });

  it('builds selection actions', () => {
    const projects = parseAssetIndexQuery('/');
    expect(selectionFromItem(projects, { id: 'p1', label: '腾讯', pathSegment: '腾讯' })).toEqual({
      type: 'advance',
      pathWithoutHash: '/腾讯/',
    });
    const kinds = parseAssetIndexQuery('/腾讯/');
    expect(selectionFromItem(kinds, { id: 'pdf', label: 'PDF', pathSegment: 'pdf' })).toEqual({
      type: 'advance',
      pathWithoutHash: '/腾讯/pdf/',
    });
    const assets = parseAssetIndexQuery('/腾讯/pdf/');
    expect(selectionFromItem(assets, {
      id: 'a1',
      label: '年报',
      pathSegment: '年报',
      asset: { assetId: 'a1', projectId: 'p1', kind: 'pdf', label: '年报' },
    }).type).toBe('insert');
  });
});
