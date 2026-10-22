import { createUniver, LocaleType } from '@univerjs/presets';
import { mergeLocales } from '@univerjs/core';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import ZhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
import { UniverSheetsThreadCommentPreset } from '@univerjs/preset-sheets-thread-comment';
import ThreadCommentZhCN from '@univerjs/preset-sheets-thread-comment/locales/zh-CN';
import { SetStyleCommand } from '@univerjs/sheets';
import { IMenuManagerService } from '@univerjs/ui';
import '@univerjs/preset-sheets-core/lib/index.css';
import '@univerjs/preset-sheets-thread-comment/lib/index.css';
import { download, type EditorAdapter } from '../../context/types';
import { openAiChartAssistant, type ChartResponse, type SavedSheetChart } from './ai-chart';
import { office } from '../../office-client/client';
import { coerceNumericString, inferOrKeepNumberFormat } from './numeric-format';
import { parseSheetStructure, serializeSheetStructure, STRUCTURE_JSON_MAX, type StructureCell } from './read-structure';
import { patchSheetCache, putSheetCache, refreshSheetProfile } from './sheet-cache';
import { computeSelectionStats, extractNumbers, formatStatsBrief } from './selection-stats';

const SHEET_LOCALES = mergeLocales(ZhCN, ThreadCommentZhCN, {
  ui: {
    ...ZhCN.ui,
    ribbon: {
      ...ZhCN.ui?.ribbon,
      ai: 'AI助手',
      aiDesc: 'AI 助手相关能力',
    },
  },
});

function isSnapshot(content: unknown): content is Record<string, unknown> {
  return Boolean(content && typeof content === 'object' && !Array.isArray(content)
    && ('sheets' in content || 'sheetOrder' in content));
}

const SHEET_COMMENT_MANAGEMENT_MENU_ID = 'invp.sheet.comment-management';
const TOGGLE_SHEET_COMMENT_PANEL_OPERATION_ID = 'sheet.operation.toggle-comment-panel';

function rememberedSheetId(snapshot: Record<string, unknown>): string {
  const custom = snapshot.custom;
  if (!custom || typeof custom !== 'object' || Array.isArray(custom)) return '';
  const id = (custom as { activeSheetId?: unknown }).activeSheetId;
  return typeof id === 'string' ? id : '';
}

/** Univer FRange 要求 endColumn < columnCount；历史数据可能 cellData/合并区超出声明尺寸。 */
function sheetDataExtent(sheet: Record<string, unknown>): { maxRow: number; maxCol: number } {
  let maxRow = -1;
  let maxCol = -1;
  const bump = (row: unknown, col: unknown) => {
    const r = Number(row);
    const c = Number(col);
    if (Number.isFinite(r)) maxRow = Math.max(maxRow, r);
    if (Number.isFinite(c)) maxCol = Math.max(maxCol, c);
  };
  const cellData = sheet.cellData;
  if (cellData && typeof cellData === 'object' && !Array.isArray(cellData)) {
    for (const [rowKey, row] of Object.entries(cellData as Record<string, unknown>)) {
      if (!row || typeof row !== 'object' || Array.isArray(row)) {
        bump(rowKey, undefined);
        continue;
      }
      for (const colKey of Object.keys(row as object)) bump(rowKey, colKey);
    }
  }
  const merges = sheet.mergeData;
  if (Array.isArray(merges)) {
    for (const merge of merges) {
      if (!merge || typeof merge !== 'object' || Array.isArray(merge)) continue;
      const m = merge as { endRow?: unknown; endColumn?: unknown };
      bump(m.endRow, m.endColumn);
    }
  }
  const rowData = sheet.rowData;
  if (rowData && typeof rowData === 'object' && !Array.isArray(rowData)) {
    for (const rowKey of Object.keys(rowData as object)) bump(rowKey, undefined);
  }
  const columnData = sheet.columnData;
  if (columnData && typeof columnData === 'object' && !Array.isArray(columnData)) {
    for (const colKey of Object.keys(columnData as object)) bump(undefined, colKey);
  }
  return { maxRow, maxCol };
}

/** 扩大 rowCount/columnCount 以覆盖 cellData，避免「Range is out of bounds」。 */
function ensureSnapshotDimensions(snapshot: Record<string, unknown>): {
  snapshot: Record<string, unknown>;
  expanded: boolean;
} {
  const sheets = snapshot.sheets;
  if (!sheets || typeof sheets !== 'object' || Array.isArray(sheets)) {
    return { snapshot, expanded: false };
  }
  let expanded = false;
  const nextSheets: Record<string, unknown> = {};
  for (const [id, raw] of Object.entries(sheets as Record<string, unknown>)) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      nextSheets[id] = raw;
      continue;
    }
    const sheet = { ...(raw as Record<string, unknown>) };
    const { maxRow, maxCol } = sheetDataExtent(sheet);
    const rowCount = Math.max(Number(sheet.rowCount) || 0, maxRow + 1);
    const columnCount = Math.max(Number(sheet.columnCount) || 0, maxCol + 1);
    if (rowCount !== sheet.rowCount || columnCount !== sheet.columnCount) {
      sheet.rowCount = rowCount;
      sheet.columnCount = columnCount;
      expanded = true;
    }
    nextSheets[id] = sheet;
  }
  if (!expanded) return { snapshot, expanded: false };
  return { snapshot: { ...snapshot, sheets: nextSheets }, expanded: true };
}

export function mountSheet(container: HTMLElement, changed: () => void, onDirty: (content: unknown) => void): EditorAdapter {
  let univer: ReturnType<typeof createUniver>['univer'] | undefined;
  let univerAPI: ReturnType<typeof createUniver>['univerAPI'] | undefined;
  let workbook: ReturnType<ReturnType<typeof createUniver>['univerAPI']['createWorkbook']> | undefined;
  let assetId = '';
  let revision = 0;
  let loading = false;
  let restoring = false;
  let lastSheetId = '';
  let lastStructureJson = '';
  let lastStructureSheetId = '';
  let lastReadPayloadJson = '';
  let syncedValues: StructureCell[][] = [];
  let syncedSheetId = '';
  let syncedRevision = -1;
  let profileStale = false;
  let cacheTimer: ReturnType<typeof setTimeout> | undefined;

  function clearStructureCache() {
    lastStructureJson = '';
    lastStructureSheetId = '';
    lastReadPayloadJson = '';
    syncedValues = [];
    syncedSheetId = '';
    syncedRevision = -1;
    profileStale = false;
    if (cacheTimer) clearTimeout(cacheTimer);
  }

  function currentStructure() {
    if (!workbook || !assetId) return null;
    const sheet = workbook.getActiveSheet();
    const structure = parseSheetStructure(sheet);
    const json = serializeSheetStructure(structure);
    const parsed = JSON.parse(json) as typeof structure;
    lastStructureJson = json;
    lastStructureSheetId = structure.sheet_id;
    return parsed;
  }

  function diffCells(next: StructureCell[][]) {
    const cells: { row: number; column: number; value: StructureCell }[] = [];
    const rows = Math.max(syncedValues.length, next.length);
    const cols = Math.max(syncedValues[0]?.length || 0, next[0]?.length || 0, 1);
    let changed = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < Math.max(syncedValues[r]?.length || 0, next[r]?.length || 0); c++) {
        const before = syncedValues[r]?.[c] ?? null;
        const after = next[r]?.[c] ?? null;
        if (before !== after) {
          changed += 1;
          cells.push({ row: r, column: c, value: after });
        }
      }
    }
    const ratio = changed / Math.max(rows * cols, 1);
    return { cells, ratio, rows: next.length, cols: next[0]?.length || 0 };
  }

  async function publishSheetCache(regenerateProfile: boolean) {
    if (!assetId || !workbook || loading) return;
    const structure = currentStructure();
    if (!structure?.sheet_id) return;
    const sameSheet = syncedSheetId === structure.sheet_id && syncedValues.length > 0 && !regenerateProfile;
    const diff = sameSheet ? diffCells(structure.values) : null;
    let meta;
    if (diff && diff.ratio <= 0.2 && diff.cells.length) {
      meta = await patchSheetCache(assetId, {
        sheet_id: structure.sheet_id,
        revision,
        cells: diff.cells,
        rows: structure.rows,
        cols: structure.cols,
        range: structure.range,
        merges: structure.merges,
      });
    } else if (diff && !diff.cells.length && !regenerateProfile) {
      syncedRevision = revision;
      return;
    } else {
      meta = await putSheetCache(assetId, structure, revision);
    }
    syncedValues = structure.values;
    syncedSheetId = structure.sheet_id;
    syncedRevision = revision;
    profileStale = Boolean(meta.profile_stale || !meta.has_profile);
    const incremental = Boolean(diff && diff.cells.length);
    if (regenerateProfile || (!meta.has_profile && !incremental)) {
      const refreshed = await refreshSheetProfile(assetId, structure.sheet_id);
      profileStale = Boolean(refreshed.profile_stale || !refreshed.has_profile);
    }
    changed();
  }

  function scheduleSheetCache() {
    if (loading || restoring || !assetId) return;
    if (cacheTimer) clearTimeout(cacheTimer);
    cacheTimer = setTimeout(() => {
      void publishSheetCache(false).catch(err => {
        window.dispatchEvent(new CustomEvent('office-error', {
          detail: err instanceof Error ? err.message : String(err),
        }));
      });
    }, 800);
  }

  function selectionHasContent(values: unknown[][]) {
    return values.some(row => row.some(v => v != null && String(v).trim() !== ''));
  }

  function tagSurrounding(raw: string, scope: 'selection' | 'sheet'): string {
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      return JSON.stringify({ ...parsed, scope });
    } catch {
      return JSON.stringify({ scope, data: raw });
    }
  }

  function cachedSurrounding(sheetId: string, scope: 'selection' | 'sheet'): string {
    if (lastReadPayloadJson && lastStructureSheetId === sheetId) {
      return tagSurrounding(lastReadPayloadJson, scope);
    }
    if (lastStructureJson && lastStructureSheetId === sheetId) {
      return tagSurrounding(lastStructureJson, scope);
    }
    return '';
  }

  function buildReadPayload(sheet: {
    getSelection(): { getActiveRange(): {
      getA1Notation(): string;
      getValues(): unknown[][];
    } | null | undefined } | null | undefined;
  } & Parameters<typeof parseSheetStructure>[0]) {
    const range = sheet.getSelection()?.getActiveRange();
    const address = range?.getA1Notation() || '';
    const selectionValues = range ? (range.getValues() as unknown[][]) : [];
    const stats = computeSelectionStats(extractNumbers(selectionValues));
    const structure = parseSheetStructure(sheet);
    const structureJson = serializeSheetStructure(structure);
    lastStructureJson = structureJson;
    lastStructureSheetId = structure.sheet_id;
    const payload = {
      selection: {
        address,
        rows: selectionValues.length,
        cols: selectionValues[0]?.length || 0,
        stats,
      },
      structure: JSON.parse(structureJson) as Record<string, unknown>,
      note: 'selection.address 为当前选区坐标；selection.stats 为选区数值特征；structure 为整表合并填充后的定位结构',
    };
    let json = JSON.stringify(payload);
    if (json.length > STRUCTURE_JSON_MAX) {
      const slim = {
        ...payload,
        structure: {
          ...payload.structure,
          values: [],
          truncated: true,
        },
      };
      json = JSON.stringify(slim);
    }
    lastReadPayloadJson = json;
    return { address, stats, payload, json };
  }

  function rememberActiveSheet(persist: boolean) {
    if (!workbook) return;
    const id = workbook.getActiveSheet()?.getSheetId();
    if (!id || id === lastSheetId) return;
    lastSheetId = id;
    const custom = { ...(workbook.getCustomMetadata() || {}), activeSheetId: id };
    workbook.setCustomMetadata(custom);
    if (persist && !loading && !restoring && assetId) {
      revision++;
      onDirty(workbook.save());
    }
  }

  function restoreActiveSheet() {
    if (!workbook || !lastSheetId) return;
    const current = workbook.getActiveSheet()?.getSheetId();
    if (current === lastSheetId) return;
    const sheet = workbook.getSheetBySheetId(lastSheetId);
    if (!sheet) {
      lastSheetId = current || '';
      return;
    }
    restoring = true;
    workbook.setActiveSheet(sheet);
    restoring = false;
  }

  const markReady = () => {
    if (!univerAPI) return;
    if (univerAPI.getCurrentLifecycleStage() >= univerAPI.Enum.LifecycleStages.Rendered) {
      restoreActiveSheet();
      loading = false;
      container.inert = false;
      container.dataset.ready = 'true';
      changed();
      if (assetId) {
        void publishSheetCache(false).catch(err => {
          window.dispatchEvent(new CustomEvent('office-error', {
            detail: err instanceof Error ? err.message : String(err),
          }));
        });
      }
    }
  };

  function openReadDataDialog() {
    if (!workbook) return;
    const sheet = workbook.getActiveSheet();
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'name-dialog';
    dialog.style.width = 'min(420px, 92vw)';
    const title = document.createElement('h2');
    title.textContent = '读数据';

    const field = document.createElement('label');
    field.className = 'field-label';
    field.style.display = 'grid';
    field.style.gap = '6px';
    field.style.margin = '0';
    const fieldName = document.createElement('span');
    fieldName.textContent = '选区';
    const fieldValue = document.createElement('input');
    fieldValue.type = 'text';
    fieldValue.readOnly = true;
    fieldValue.style.cssText = 'min-height:34px;border:1px solid #d8dde1;border-radius:5px;padding:6px 10px;font:inherit;background:#f6f8f9;color:#24282d';
    field.append(fieldName, fieldValue);

    const hint = document.createElement('p');
    hint.style.cssText = 'margin:8px 0 0;font-size:12px;line-height:1.5;color:#66707a';

    try {
      const { address, stats } = buildReadPayload(sheet);
      fieldValue.value = address || '（未选中单元格）';
      hint.textContent = address
        ? `已缓存整表结构供 AI 定位。${formatStatsBrief(stats)}`
        : '请先框选单元格，再点读数据。';
    } catch (err) {
      clearStructureCache();
      fieldValue.value = sheet.getSelection()?.getActiveRange()?.getA1Notation() || '（未选中单元格）';
      hint.textContent = err instanceof Error ? err.message : String(err);
    }

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = '<button type="button" class="primary" data-close>关闭</button>';
    dialog.append(title, field, hint, actions);
    overlay.append(dialog);
    document.body.append(overlay);
    const close = () => overlay.remove();
    actions.querySelector('[data-close]')!.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  }

  function ensureAiRibbonTab() {
    if (!univer) return;
    const menuManager = univer.__getInjector().get(IMenuManagerService);
    menuManager.appendRootMenu({
      ribbon: {
        'ribbon.ai': {
          order: 3.5,
          title: 'ui.ribbon.ai',
          'ribbon.ai.others': { order: 0 },
        },
      },
    });
  }

  function bindApi() {
    if (!univerAPI) return;
    ensureAiRibbonTab();
    univerAPI.createMenu({
      id: SHEET_COMMENT_MANAGEMENT_MENU_ID,
      title: '评论管理',
      tooltip: 'sheets-thread-comment-ui.menu.commentManagement',
      action: () => univerAPI?.executeCommand(TOGGLE_SHEET_COMMENT_PANEL_OPERATION_ID),
    }).appendTo('ribbon.data.others');
    univerAPI.addEvent(univerAPI.Event.LifeCycleChanged, markReady);
    univerAPI.addEvent(univerAPI.Event.SelectionChanged, changed);
    univerAPI.addEvent(univerAPI.Event.ActiveSheetChanged, () => {
      if (loading || restoring) return;
      document.querySelector<HTMLButtonElement>('.sheet-ai-chart-window [data-close]')?.click();
      clearStructureCache();
      rememberActiveSheet(true);
      changed();
      scheduleSheetCache();
    });
    univerAPI.createMenu({
      id: 'office.sheet.text-to-number',
      title: '文本转数值',
      tooltip: '将选区内「看起来像数字」的文本转为数值，并保留原数字格式',
      action: () => {
        if (!workbook) return;
        const sheet = workbook.getActiveSheet();
        const range = sheet.getSelection()?.getActiveRange();
        if (!range) return;
        const cells = range.getCellDatas();
        const formats = range.getNumberFormats?.() || [];
        const nextFormats: string[][] = [];
        let converted = 0;
        const next = cells.map((row, r) => {
          const formatRow = formats[r] || [];
          const outFormats: string[] = [];
          const outRow = (row || []).map((cell, c) => {
            const prevFmt = formatRow[c] || '';
            if (!cell || cell.f) {
              outFormats.push(prevFmt);
              return cell ?? null;
            }
            if (cell.t !== 1 && cell.t !== 4) {
              outFormats.push(prevFmt);
              return cell;
            }
            if (typeof cell.v !== 'string' && typeof cell.v !== 'number') {
              outFormats.push(prevFmt);
              return cell;
            }
            const raw = typeof cell.v === 'number' ? String(cell.v) : cell.v;
            const num = coerceNumericString(raw);
            if (num === null) {
              outFormats.push(prevFmt);
              return cell;
            }
            converted += 1;
            outFormats.push(inferOrKeepNumberFormat(raw, prevFmt));
            return { ...cell, v: num, t: 2 };
          });
          nextFormats.push(outFormats);
          return outRow;
        });
        if (!converted) return;
        range.setValues(next as Parameters<typeof range.setValues>[0]);
        if (typeof range.setNumberFormats === 'function') {
          range.setNumberFormats(nextFormats);
        }
        onDirty(workbook.save());
        changed();
      },
    }).appendTo('ribbon.data.others');

    univerAPI.createMenu({
      id: 'office.sheet.indent-text',
      title: '缩进文本',
      tooltip: '为当前选区增加一个汉字宽度的左侧缩进；再次点击取消',
      action: () => {
        if (!workbook || !univerAPI) return;
        const sheet = workbook.getActiveSheet();
        const range = sheet.getSelection()?.getActiveRange();
        if (!range) return;
        const padding = range.getCellStyleData()?.pd || {};
        const currentLeft = padding.l || 0;
        const nextLeft = currentLeft >= 12 ? 0 : 12;
        void univerAPI.executeCommand(SetStyleCommand.id, {
          unitId: workbook.getId(),
          subUnitId: sheet.getSheetId(),
          range: range.getRange(),
          style: { type: 'pd', value: { ...padding, l: nextLeft || undefined } },
        }).catch(error => {
          window.dispatchEvent(new CustomEvent('office-error', {
            detail: error instanceof Error ? `设置缩进失败：${error.message}` : '设置缩进失败。',
          }));
        });
      },
    }).appendTo('ribbon.data.others');

    // AI助手（一级，位于数据之后）→ 读数据
    univerAPI.createMenu({
      id: 'office.sheet.ai.read-data',
      title: '读数据',
      tooltip: '显示当前选区坐标，并缓存整表结构与选区统计供 AI',
      action: () => openReadDataDialog(),
    }).appendTo('ribbon.ai.others');

    univerAPI.createMenu({
      id: 'office.sheet.ai.generate-chart',
      title: '生成图表',
      tooltip: '复用协作助手读取的表结构，由模型选择 X/Y 轴和图表类型',
      action: () => {
        if (!workbook || !assetId) return;
        const activeSheet = () => workbook!.getActiveSheet();
        const chartSelection = () => {
          const range = activeSheet().getSelection()?.getActiveRange();
          if (!range) return null;
          const target = range.getRange();
          const rows = target.endRow - target.startRow + 1;
          const cols = target.endColumn - target.startColumn + 1;
          if (rows * cols > 2000) return null;
          return {
            sheet_id: activeSheet().getSheetId(),
            address: range.getA1Notation(),
            row: target.startRow,
            column: target.startColumn,
            rows,
            cols,
            values: range.getValues() as unknown[][],
          };
        };
        openAiChartAssistant({
          selectionAddress: () => chartSelection()?.address || '',
          getModelStatus: () => office.json('/assistant/status'),
          generate: async (description, onEvent) => {
            await publishSheetCache(false);
            const selection = chartSelection();
            let response: ChartResponse | undefined;
            let streamError = '';
            await office.stream(`/assets/${assetId}/assistant/chart/stream`, {
              sheet_id: activeSheet().getSheetId(),
              description,
              selection,
            }, event => {
              onEvent(event);
              if (event.type === 'done' && event.chart) response = event as unknown as ChartResponse;
              if (event.type === 'error') streamError = String(event.error || '图表生成失败。');
            });
            if (streamError) throw new Error(streamError);
            if (!response) throw new Error('模型没有返回图表配置。');
            return response;
          },
          list: async () => {
            const sheetId = encodeURIComponent(activeSheet().getSheetId());
            const response = await office.json<{ charts: SavedSheetChart[] }>(
              `/assets/${assetId}/sheet-charts?sheet_id=${sheetId}`,
            );
            return response.charts;
          },
          save: (name, description, chart) => office.json(`/assets/${assetId}/sheet-charts`, {
            method: 'POST',
            body: JSON.stringify({
              sheet_id: activeSheet().getSheetId(),
              name,
              description,
              chart,
            }),
          }),
          rename: (chartId, name) => office.json(`/assets/${assetId}/sheet-charts/${chartId}`, {
            method: 'PUT',
            body: JSON.stringify({ name }),
          }),
        });
      },
    }).appendTo('ribbon.ai.others');

    univerAPI.onCommandExecuted(command => {
      if (loading || restoring || command.type !== 2 || !assetId || !workbook) return;
      rememberActiveSheet(false);
      revision++;
      onDirty(workbook.save());
      changed();
      scheduleSheetCache();
    });
  }

  function boot(snapshot: Record<string, unknown>) {
    univer?.dispose();
    univer = undefined;
    univerAPI = undefined;
    workbook = undefined;
    clearStructureCache();
    container.replaceChildren();
    container.inert = true;
    container.dataset.ready = 'false';
    document.querySelectorAll<HTMLElement>('.sheet-ai-chart-window').forEach(el => {
      const close = el.querySelector<HTMLButtonElement>('[data-close]');
      if (close) close.click();
      else el.remove();
    });
    const created = createUniver({
      locale: LocaleType.ZH_CN, locales: { [LocaleType.ZH_CN]: SHEET_LOCALES },
      presets: [
        UniverSheetsCorePreset({
          container,
          // 隐藏内置「文本转数字」（工具栏 + 右键），改用自定义「文本转数值」
          menu: {
            'sheet.toolbar.text-to-number': { hidden: true },
            'sheet.contextMenu.text-to-number': { hidden: true },
            // Move the built-in comment panel entry from Insert to Data below.
            [TOGGLE_SHEET_COMMENT_PANEL_OPERATION_ID]: { hidden: true },
          },
          sheets: {
            disableForceStringAlert: true,
            disableForceStringMark: true,
          },
        }),
        UniverSheetsThreadCommentPreset(),
      ],
    });
    univer = created.univer;
    univerAPI = created.univerAPI;
    bindApi();
    workbook = univerAPI.createWorkbook(snapshot);
    markReady();
  }

  boot({ name: '未命名表格' });
  return {
    async load(asset) {
      // 同一表格已就绪：不 dispose/重建，避免切回工作区时白屏闪烁
      if (
        asset.id
        && asset.id === assetId
        && workbook
        && container.dataset.ready === 'true'
        && asset.revision === revision
      ) {
        window.dispatchEvent(new Event('resize'));
        return;
      }
      loading = true;
      clearStructureCache();
      assetId = asset.id;
      revision = asset.revision;
      let expanded = false;
      let snapshot: Record<string, unknown> = isSnapshot(asset.content)
        ? asset.content
        : { id: asset.id, name: asset.name || '未命名表格' };
      if (isSnapshot(asset.content)) {
        const normalized = ensureSnapshotDimensions(snapshot);
        snapshot = normalized.snapshot;
        expanded = normalized.expanded;
      }
      lastSheetId = isSnapshot(asset.content) ? rememberedSheetId(snapshot) : '';
      boot(snapshot);
      restoreActiveSheet();
      if (workbook && (!isSnapshot(asset.content) || expanded)) onDirty(workbook.save());
      markReady();
    },
    context() {
      if (container.inert || !assetId || !workbook) return null;
      const sheet = workbook.getActiveSheet();
      const range = sheet.getSelection()?.getActiveRange();
      if (!range) return null;
      const target = range.getRange();
      const rows = target.endRow - target.startRow + 1;
      const cols = target.endColumn - target.startColumn + 1;
      if (rows * cols > 2000) throw new Error('请将选区缩小到 2000 个单元格以内。');
      const cells = range.getCellDatas();
      const values = Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => {
        const cell = cells[r]?.[c];
        return cell?.f || cell?.v || (cell?.v === 0 ? 0 : cell?.v === false ? false : null);
      }));
      const sheetId = sheet.getSheetId();
      const surrounding = lastReadPayloadJson && lastStructureSheetId === sheetId
        ? lastReadPayloadJson
        : lastStructureJson && lastStructureSheetId === sheetId
          ? lastStructureJson
          : JSON.stringify(sheet.getRange(0, target.startColumn, 1, cols).getValues());
      return { document_id: assetId, surface: 'sheet' as const, revision,
        selection: { sheet_id: sheetId, row: target.startRow, column: target.startColumn,
          rows, cols, address: range.getA1Notation(), values },
        surrounding,
      };
    },
    anchor() {
      const ctx = this.context();
      if (!ctx) return null;
      const value = ctx.selection.values[0][0];
      return { ...ctx, selection: { ...ctx.selection, rows: 1, cols: 1, values: [[value]] } };
    },
    resolveContext() {
      if (container.inert || !assetId || !workbook) return null;
      const sheet = workbook.getActiveSheet();
      const sheetId = sheet.getSheetId();
      const range = sheet.getSelection()?.getActiveRange();
      if (range) {
        const target = range.getRange();
        const rows = target.endRow - target.startRow + 1;
        const cols = target.endColumn - target.startColumn + 1;
        if (rows * cols <= 2000) {
          const cells = range.getCellDatas();
          const values = Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => {
            const cell = cells[r]?.[c];
            return cell?.f || cell?.v || (cell?.v === 0 ? 0 : cell?.v === false ? false : null);
          }));
          if (selectionHasContent(values)) {
            const headerFallback = JSON.stringify(sheet.getRange(0, target.startColumn, 1, cols).getValues());
            const surrounding = cachedSurrounding(sheetId, 'selection')
              || tagSurrounding(headerFallback, 'selection');
            return {
              document_id: assetId,
              surface: 'sheet' as const,
              revision,
              selection: {
                sheet_id: sheetId,
                row: target.startRow,
                column: target.startColumn,
                rows,
                cols,
                address: range.getA1Notation(),
                values,
              },
              surrounding,
            };
          }
        }
      }
      const structure = parseSheetStructure(sheet);
      const structureJson = serializeSheetStructure(structure);
      lastStructureJson = structureJson;
      lastStructureSheetId = sheetId;
      return {
        document_id: assetId,
        surface: 'sheet' as const,
        revision,
        selection: {
          sheet_id: sheetId,
          row: 0,
          column: 0,
          rows: 1,
          cols: 1,
          address: '',
          values: [[null]],
        },
        surrounding: tagSurrounding(structureJson, 'sheet'),
      };
    },
    askContext(includeSelection: boolean) {
      if (container.inert || !assetId || !workbook) return null;
      const sheet = workbook.getActiveSheet();
      const sheetId = sheet.getSheetId();
      const empty = {
        document_id: assetId,
        surface: 'sheet' as const,
        revision,
        selection: {
          sheet_id: sheetId,
          row: 0,
          column: 0,
          rows: 1,
          cols: 1,
          address: '',
          values: [[null]] as unknown[][],
        },
        surrounding: '',
      };
      if (!includeSelection) return empty;
      const range = sheet.getSelection()?.getActiveRange();
      if (!range) return empty;
      const target = range.getRange();
      const rows = target.endRow - target.startRow + 1;
      const cols = target.endColumn - target.startColumn + 1;
      if (rows * cols > 2000) return empty;
      const cells = range.getCellDatas();
      const values = Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => {
        const cell = cells[r]?.[c];
        return cell?.f || cell?.v || (cell?.v === 0 ? 0 : cell?.v === false ? false : null);
      }));
      if (!selectionHasContent(values) && rows === 1 && cols === 1) return empty;
      return {
        ...empty,
        selection: {
          sheet_id: sheetId,
          row: target.startRow,
          column: target.startColumn,
          rows,
          cols,
          address: range.getA1Notation(),
          values,
        },
      };
    },
    syncSheetCache(opts) {
      return publishSheetCache(Boolean(opts?.regenerateProfile));
    },
    cacheStale() {
      return profileStale || syncedRevision !== revision;
    },
    apply(patch) {
      if (!workbook || !univerAPI) throw new Error('表格尚未打开。');
      if (patch.revision !== revision) throw new Error('表格已更改，请重新生成建议。');
      const s = patch.selection;
      const sheet = workbook.getSheetBySheetId(s.sheet_id);
      if (!sheet) throw new Error('工作表不存在。');
      const formatOps = Array.isArray(patch.format_ops) ? patch.format_ops : [];
      for (const op of formatOps) {
        const address = String(op?.address || '').trim();
        const pattern = String(op?.number_format || '').trim();
        if (!address || !pattern) throw new Error('数字格式修改无效。');
        const range = sheet.getRange(address);
        const box = range.getRange();
        const rows = box.endRow - box.startRow + 1;
        const cols = box.endColumn - box.startColumn + 1;
        if (rows < 1 || cols < 1 || rows * cols > 50_000) throw new Error('数字格式区域无效。');
        const matrix = Array.from({ length: rows }, () => Array.from({ length: cols }, () => pattern));
        if (typeof range.setNumberFormats !== 'function') {
          throw new Error('当前表格不支持设置数字格式。');
        }
        range.setNumberFormats(matrix);
      }
      if (patch.replacement != null) {
        const values = patch.replacement as (string | number | boolean | null)[][];
        if (!Array.isArray(values) || !values.length || !Array.isArray(values[0]) || !values[0].length) {
          throw new Error('修改矩阵无效。');
        }
        sheet.getRange(s.row, s.column, values.length, values[0].length).setValues(values.map(row => row.map(v => {
          if (typeof v === 'string' && v.startsWith('=')) return { f: v };
          if (typeof v === 'number') return { v, t: 2, f: null };
          if (typeof v === 'boolean') return { v, t: 3, f: null };
          if (typeof v === 'string') {
            const coerced = coerceNumericString(v);
            if (coerced !== null) return { v: coerced, t: 2, f: null };
          }
          return { v, f: null };
        })));
      } else if (!formatOps.length) {
        throw new Error('修改内容为空。');
      }
      onDirty(workbook.save());
      changed();
    },
    snapshot() { return workbook?.save(); },
    undo() { void univerAPI?.undo(); },
    export() {
      if (!workbook) return;
      download('workbook.univer.json', JSON.stringify(workbook.save(), null, 2));
    },
  };
}
