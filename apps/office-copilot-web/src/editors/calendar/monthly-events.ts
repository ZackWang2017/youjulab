import { mountMarkdownEditor, renderMarkdown, type MdEditorHandle } from './markdown';

export type MonthlyRepeat = 'once' | 'monthly' | 'yearly';

export interface MonthlyEvent {
  id: string;
  title: string;
  content: string;
  repeat: MonthlyRepeat;
  /** 1–12 */
  month: number;
  /** once 必填；yearly 可选起始年；monthly 忽略 */
  year?: number;
}

export const REPEAT_LABEL: Record<MonthlyRepeat, string> = {
  once: '一次性',
  monthly: '每月重复',
  yearly: '每年重复',
};

function uid() {
  return `me_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function monthKey(year: number, month: number) {
  return `${year}-${String(month).padStart(2, '0')}`;
}

export function parseMonthKey(key: string): { year: number; month: number } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  if (month < 1 || month > 12) return null;
  return { year, month };
}

export function formatMonthLabel(year: number, month: number) {
  return `${year}年${month}月`;
}

function clampMonth(n: number) {
  if (!Number.isFinite(n)) return 1;
  return Math.min(12, Math.max(1, Math.round(n)));
}

export function normalizeMonthlyEvents(raw: unknown): MonthlyEvent[] {
  if (!Array.isArray(raw)) return [];
  const out: MonthlyEvent[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const title = String(row.title || '').trim();
    if (!title) continue;
    const repeat = row.repeat === 'monthly' || row.repeat === 'yearly' || row.repeat === 'once'
      ? row.repeat
      : 'once';
    const month = clampMonth(Number(row.month) || 1);
    const yearRaw = Number(row.year);
    const year = Number.isFinite(yearRaw) && yearRaw >= 1970 && yearRaw <= 9999
      ? Math.round(yearRaw)
      : undefined;
    if (repeat === 'once' && year == null) continue;
    out.push({
      id: String(row.id || uid()),
      title,
      content: String(row.content || ''),
      repeat,
      month,
      ...(year != null ? { year } : {}),
    });
  }
  return out;
}

/** 某年月应展示的事件（含重复规则）。 */
export function eventsForMonth(events: MonthlyEvent[], year: number, month: number): MonthlyEvent[] {
  return events.filter(ev => {
    if (ev.repeat === 'monthly') return true;
    if (ev.repeat === 'yearly') return ev.month === month;
    return ev.year === year && ev.month === month;
  }).sort((a, b) => a.title.localeCompare(b.title, 'zh'));
}

/** 某年应展示的事件：一次性仅该年；每月/每年重复均计入。 */
export function eventsForYear(events: MonthlyEvent[], year: number): MonthlyEvent[] {
  return events.filter(ev => {
    if (ev.repeat === 'monthly' || ev.repeat === 'yearly') return true;
    return ev.year === year;
  }).sort((a, b) => {
    if (a.month !== b.month) return a.month - b.month;
    return a.title.localeCompare(b.title, 'zh');
  });
}

export function monthOptionsAround(centerYear: number, centerMonth: number, span = 18): { key: string; label: string }[] {
  const items: { key: string; label: string }[] = [];
  let y = centerYear;
  let m = centerMonth - span;
  while (m < 1) { m += 12; y -= 1; }
  for (let i = 0; i < span * 2 + 1; i += 1) {
    items.push({ key: monthKey(y, m), label: formatMonthLabel(y, m) });
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return items;
}

function fieldRow(label: string, control: HTMLElement) {
  const wrap = document.createElement('label');
  wrap.className = 'cal-me-field';
  const text = document.createElement('span');
  text.className = 'cal-me-field-label';
  text.textContent = label;
  wrap.append(text, control);
  return wrap;
}

export function openMonthlyEventEditor(
  initial: Partial<MonthlyEvent> | null,
  defaults: { year: number; month: number },
  onSave: (event: MonthlyEvent) => void,
): Promise<void> {
  return new Promise(resolve => {
    const isNew = !initial?.id;
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog';
    dialog.setAttribute('role', 'dialog');

    const titleEl = document.createElement('h2');
    titleEl.textContent = isNew ? '添加月度事件' : '编辑月度事件';

    const titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.maxLength = 120;
    titleInput.placeholder = '事件标题';
    titleInput.value = initial?.title || '';
    titleInput.required = true;

    const repeatSelect = document.createElement('select');
    (['once', 'monthly', 'yearly'] as MonthlyRepeat[]).forEach(key => {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = REPEAT_LABEL[key];
      repeatSelect.append(opt);
    });
    repeatSelect.value = initial?.repeat || 'once';

    const yearInput = document.createElement('input');
    yearInput.type = 'number';
    yearInput.min = '1970';
    yearInput.max = '9999';
    yearInput.value = String(initial?.year ?? defaults.year);

    const monthSelect = document.createElement('select');
    for (let m = 1; m <= 12; m += 1) {
      const opt = document.createElement('option');
      opt.value = String(m);
      opt.textContent = `${m}月`;
      monthSelect.append(opt);
    }
    monthSelect.value = String(initial?.month ?? defaults.month);

    const yearMonthRow = document.createElement('div');
    yearMonthRow.className = 'cal-me-year-month';
    yearMonthRow.append(
      fieldRow('年份', yearInput),
      fieldRow('月份', monthSelect),
    );

    const syncVisibility = () => {
      const repeat = repeatSelect.value as MonthlyRepeat;
      const hideYear = repeat === 'monthly';
      const hideMonth = repeat === 'monthly';
      yearInput.disabled = hideYear;
      yearInput.parentElement!.hidden = hideYear;
      monthSelect.disabled = hideMonth;
      monthSelect.parentElement!.hidden = hideMonth;
      yearMonthRow.hidden = hideYear && hideMonth;
    };
    repeatSelect.addEventListener('change', syncVisibility);
    syncVisibility();

    const host = document.createElement('div');
    host.className = 'md-editor-host';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `
      <button type="button" data-cancel>取消</button>
      <button type="button" class="primary" data-save>保存</button>`;

    dialog.append(
      titleEl,
      fieldRow('标题', titleInput),
      fieldRow('类型', repeatSelect),
      yearMonthRow,
      host,
      actions,
    );
    overlay.append(dialog);
    document.body.append(overlay);

    let mdEditor: MdEditorHandle | null = mountMarkdownEditor(host, initial?.content || '', {
      placeholder: '事件详情（Markdown）…',
    });
    requestAnimationFrame(() => titleInput.focus());

    const close = () => {
      mdEditor?.destroy();
      mdEditor = null;
      overlay.remove();
      resolve();
    };

    actions.querySelector('[data-cancel]')!.addEventListener('click', close);
    actions.querySelector('[data-save]')!.addEventListener('click', () => {
      const title = titleInput.value.trim();
      if (!title) {
        titleInput.focus();
        return;
      }
      const repeat = repeatSelect.value as MonthlyRepeat;
      const month = clampMonth(Number(monthSelect.value));
      const year = Math.round(Number(yearInput.value));
      if (repeat === 'once' && (!Number.isFinite(year) || year < 1970)) {
        yearInput.focus();
        return;
      }
      const saved: MonthlyEvent = {
        id: initial?.id || uid(),
        title,
        content: mdEditor?.getValue() || '',
        repeat,
        month: repeat === 'monthly' ? 1 : month,
        ...(repeat === 'once'
          ? { year }
          : repeat === 'yearly' && Number.isFinite(year)
            ? { year }
            : {}),
      };
      close();
      onSave(saved);
    });
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  });
}

export function openMonthlyEventDetail(
  event: MonthlyEvent,
  handlers: {
    onEdit: () => void;
    onDelete: () => void;
  },
): Promise<void> {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog calendar-me-detail';
    dialog.setAttribute('role', 'dialog');

    const head = document.createElement('div');
    head.className = 'cal-me-detail-head';
    const title = document.createElement('h2');
    title.textContent = event.title;
    const badge = document.createElement('span');
    badge.className = `cal-me-badge cal-me-badge-${event.repeat}`;
    badge.textContent = REPEAT_LABEL[event.repeat];
    const meta = document.createElement('p');
    meta.className = 'cal-me-detail-meta';
    if (event.repeat === 'once' && event.year != null) {
      meta.textContent = formatMonthLabel(event.year, event.month);
    } else if (event.repeat === 'yearly') {
      meta.textContent = `每年 ${event.month} 月`;
    } else {
      meta.textContent = '每月';
    }
    head.append(title, badge, meta);

    const body = document.createElement('div');
    body.className = 'plan-md cal-me-detail-body';
    const md = (event.content || '').trim();
    body.innerHTML = md ? renderMarkdown(md) : '<p class="plan-md-empty">暂无详情</p>';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `
      <button type="button" data-delete>删除</button>
      <button type="button" data-edit>编辑</button>
      <button type="button" class="primary" data-close>关闭</button>`;

    dialog.append(head, body, actions);
    overlay.append(dialog);
    document.body.append(overlay);

    const close = () => {
      overlay.remove();
      resolve();
    };
    actions.querySelector('[data-close]')!.addEventListener('click', close);
    actions.querySelector('[data-edit]')!.addEventListener('click', () => {
      close();
      handlers.onEdit();
    });
    actions.querySelector('[data-delete]')!.addEventListener('click', () => {
      if (!confirm(`删除月度事件「${event.title}」？`)) return;
      handlers.onDelete();
      close();
    });
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  });
}

/** 首页点击：展示某月全部事件列表。 */
export function openMonthlyMonthOverview(
  allEvents: MonthlyEvent[],
  year: number,
  month: number,
  handlers: {
    onChange: (next: MonthlyEvent[]) => void;
  },
): Promise<void> {
  return new Promise(resolve => {
    let draft = allEvents.map(e => ({ ...e }));
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog calendar-me-month-overview';
    dialog.setAttribute('role', 'dialog');

    const title = document.createElement('h2');
    title.textContent = `${formatMonthLabel(year, month)}事件`;

    const list = document.createElement('div');
    list.className = 'cal-me-manager-list';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `<button type="button" class="primary" data-close>关闭</button>`;

    dialog.append(title, list, actions);
    overlay.append(dialog);
    document.body.append(overlay);

    const persist = () => handlers.onChange(draft.map(e => ({ ...e })));

    const renderList = () => {
      list.replaceChildren();
      const visible = eventsForMonth(draft, year, month);
      if (!visible.length) {
        const empty = document.createElement('p');
        empty.className = 'cal-me-empty';
        empty.textContent = '本月暂无事件';
        list.append(empty);
        return;
      }
      for (const ev of visible) {
        const row = document.createElement('div');
        row.className = 'cal-me-manager-row cal-me-overview-row';
        row.tabIndex = 0;
        row.setAttribute('role', 'button');

        const main = document.createElement('div');
        main.className = 'cal-me-manager-main';
        const name = document.createElement('strong');
        name.textContent = ev.title;
        const badge = document.createElement('span');
        badge.className = `cal-me-badge cal-me-badge-${ev.repeat}`;
        badge.textContent = REPEAT_LABEL[ev.repeat];
        const meta = document.createElement('span');
        meta.className = 'cal-me-manager-meta';
        if (ev.repeat === 'once' && ev.year != null) meta.textContent = formatMonthLabel(ev.year, ev.month);
        else if (ev.repeat === 'yearly') meta.textContent = `每年${ev.month}月`;
        else meta.textContent = '每月';
        const preview = document.createElement('div');
        preview.className = 'cal-me-overview-preview plan-md';
        const md = (ev.content || '').trim();
        preview.innerHTML = md
          ? renderMarkdown(md)
          : '<p class="plan-md-empty">暂无详情</p>';
        main.append(name, badge, meta, preview);

        const ops = document.createElement('div');
        ops.className = 'cal-me-manager-ops';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = '编辑';
        const del = document.createElement('button');
        del.type = 'button';
        del.textContent = '删除';
        ops.append(edit, del);
        row.append(main, ops);
        list.append(row);

        const openOne = () => {
          void openMonthlyEventDetail(ev, {
            onEdit: () => {
              void openMonthlyEventEditor(ev, { year, month }, next => {
                draft = draft.map(item => (item.id === next.id ? next : item));
                persist();
                renderList();
              });
            },
            onDelete: () => {
              draft = draft.filter(item => item.id !== ev.id);
              persist();
              renderList();
            },
          }).then(() => renderList());
        };

        row.addEventListener('click', e => {
          if ((e.target as HTMLElement).closest('button')) return;
          openOne();
        });
        row.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openOne();
          }
        });
        edit.addEventListener('click', e => {
          e.stopPropagation();
          void openMonthlyEventEditor(ev, { year, month }, next => {
            draft = draft.map(item => (item.id === next.id ? next : item));
            persist();
            renderList();
          });
        });
        del.addEventListener('click', e => {
          e.stopPropagation();
          if (!confirm(`删除「${ev.title}」？`)) return;
          draft = draft.filter(item => item.id !== ev.id);
          persist();
          renderList();
        });
      }
    };

    actions.querySelector('[data-close]')!.addEventListener('click', () => {
      overlay.remove();
      resolve();
    });
    overlay.addEventListener('click', e => {
      if (e.target === overlay) {
        overlay.remove();
        resolve();
      }
    });
    renderList();
  });
}

/** 展示某年全部事件清单（含每月/每年重复）。 */
export function openMonthlyYearOverview(
  allEvents: MonthlyEvent[],
  year: number,
  handlers: {
    onChange: (next: MonthlyEvent[]) => void;
  },
): Promise<void> {
  return new Promise(resolve => {
    let draft = allEvents.map(e => ({ ...e }));
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog calendar-me-year-overview';
    dialog.setAttribute('role', 'dialog');

    const title = document.createElement('h2');
    title.textContent = `${year}年全部事件`;

    const list = document.createElement('div');
    list.className = 'cal-me-manager-list';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `<button type="button" class="primary" data-close>关闭</button>`;

    dialog.append(title, list, actions);
    overlay.append(dialog);
    document.body.append(overlay);

    const persist = () => handlers.onChange(draft.map(e => ({ ...e })));

    const renderList = () => {
      list.replaceChildren();
      const visible = eventsForYear(draft, year);
      if (!visible.length) {
        const empty = document.createElement('p');
        empty.className = 'cal-me-empty';
        empty.textContent = `${year}年暂无事件`;
        list.append(empty);
        return;
      }
      for (const ev of visible) {
        const row = document.createElement('div');
        row.className = 'cal-me-manager-row cal-me-overview-row';
        row.tabIndex = 0;
        row.setAttribute('role', 'button');

        const main = document.createElement('div');
        main.className = 'cal-me-manager-main';
        const name = document.createElement('strong');
        name.textContent = ev.title;
        const badge = document.createElement('span');
        badge.className = `cal-me-badge cal-me-badge-${ev.repeat}`;
        badge.textContent = REPEAT_LABEL[ev.repeat];
        const meta = document.createElement('span');
        meta.className = 'cal-me-manager-meta';
        if (ev.repeat === 'once' && ev.year != null) meta.textContent = formatMonthLabel(ev.year, ev.month);
        else if (ev.repeat === 'yearly') meta.textContent = `每年${ev.month}月`;
        else meta.textContent = '每月';
        const preview = document.createElement('div');
        preview.className = 'cal-me-overview-preview plan-md';
        const md = (ev.content || '').trim();
        preview.innerHTML = md
          ? renderMarkdown(md)
          : '<p class="plan-md-empty">暂无详情</p>';
        main.append(name, badge, meta, preview);

        const ops = document.createElement('div');
        ops.className = 'cal-me-manager-ops';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = '编辑';
        const del = document.createElement('button');
        del.type = 'button';
        del.textContent = '删除';
        ops.append(edit, del);
        row.append(main, ops);
        list.append(row);

        const anchor = { year, month: ev.month };
        const openOne = () => {
          void openMonthlyEventDetail(ev, {
            onEdit: () => {
              void openMonthlyEventEditor(ev, anchor, next => {
                draft = draft.map(item => (item.id === next.id ? next : item));
                persist();
                renderList();
              });
            },
            onDelete: () => {
              draft = draft.filter(item => item.id !== ev.id);
              persist();
              renderList();
            },
          }).then(() => renderList());
        };

        row.addEventListener('click', e => {
          if ((e.target as HTMLElement).closest('button')) return;
          openOne();
        });
        row.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openOne();
          }
        });
        edit.addEventListener('click', e => {
          e.stopPropagation();
          void openMonthlyEventEditor(ev, anchor, next => {
            draft = draft.map(item => (item.id === next.id ? next : item));
            persist();
            renderList();
          });
        });
        del.addEventListener('click', e => {
          e.stopPropagation();
          if (!confirm(`删除「${ev.title}」？`)) return;
          draft = draft.filter(item => item.id !== ev.id);
          persist();
          renderList();
        });
      }
    };

    actions.querySelector('[data-close]')!.addEventListener('click', () => {
      overlay.remove();
      resolve();
    });
    overlay.addEventListener('click', e => {
      if (e.target === overlay) {
        overlay.remove();
        resolve();
      }
    });
    renderList();
  });
}

export function openMonthlyEventManager(
  events: MonthlyEvent[],
  defaults: { year: number; month: number },
  onChange: (next: MonthlyEvent[]) => void,
): Promise<void> {
  return new Promise(resolve => {
    let draft = events.map(e => ({ ...e }));
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-me-dialog calendar-me-manager';
    dialog.setAttribute('role', 'dialog');

    const title = document.createElement('h2');
    title.textContent = '月度事件';

    const toolbar = document.createElement('div');
    toolbar.className = 'cal-me-manager-toolbar';
    const toolbarLeft = document.createElement('div');
    toolbarLeft.className = 'cal-me-manager-toolbar-left';
    const filter = document.createElement('select');
    filter.className = 'cal-me-month-select';
    const allOpt = document.createElement('option');
    allOpt.value = '*';
    allOpt.textContent = '全部事件';
    filter.append(allOpt);
    for (const opt of monthOptionsAround(defaults.year, defaults.month, 24)) {
      const el = document.createElement('option');
      el.value = opt.key;
      el.textContent = opt.label;
      filter.append(el);
    }
    filter.value = monthKey(defaults.year, defaults.month);

    const viewYearBtn = document.createElement('button');
    viewYearBtn.type = 'button';
    viewYearBtn.className = 'cal-me-view-year';
    viewYearBtn.textContent = '查看全部事件';
    viewYearBtn.title = '查看当前年份的全部事件清单';
    toolbarLeft.append(filter, viewYearBtn);

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'primary';
    addBtn.textContent = '添加';
    toolbar.append(toolbarLeft, addBtn);

    const list = document.createElement('div');
    list.className = 'cal-me-manager-list';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `<button type="button" class="primary" data-close>完成</button>`;

    dialog.append(title, toolbar, list, actions);
    overlay.append(dialog);
    document.body.append(overlay);

    const persist = () => {
      onChange(draft.map(e => ({ ...e })));
    };

    const renderList = () => {
      list.replaceChildren();
      const key = filter.value;
      const parsed = key === '*' ? null : parseMonthKey(key);
      const visible = parsed
        ? eventsForMonth(draft, parsed.year, parsed.month)
        : [...draft].sort((a, b) => a.title.localeCompare(b.title, 'zh'));
      if (!visible.length) {
        const empty = document.createElement('p');
        empty.className = 'cal-me-empty';
        empty.textContent = '暂无月度事件';
        list.append(empty);
        return;
      }
      for (const ev of visible) {
        const row = document.createElement('div');
        row.className = 'cal-me-manager-row';
        const main = document.createElement('div');
        main.className = 'cal-me-manager-main';
        const name = document.createElement('strong');
        name.textContent = ev.title;
        const badge = document.createElement('span');
        badge.className = `cal-me-badge cal-me-badge-${ev.repeat}`;
        badge.textContent = REPEAT_LABEL[ev.repeat];
        const meta = document.createElement('span');
        meta.className = 'cal-me-manager-meta';
        if (ev.repeat === 'once' && ev.year != null) meta.textContent = formatMonthLabel(ev.year, ev.month);
        else if (ev.repeat === 'yearly') meta.textContent = `每年${ev.month}月`;
        else meta.textContent = '每月';
        main.append(name, badge, meta);

        const ops = document.createElement('div');
        ops.className = 'cal-me-manager-ops';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = '编辑';
        const del = document.createElement('button');
        del.type = 'button';
        del.textContent = '删除';
        ops.append(edit, del);
        row.append(main, ops);
        list.append(row);

        edit.addEventListener('click', () => {
          const anchor = parsed || { year: defaults.year, month: defaults.month };
          void openMonthlyEventEditor(ev, anchor, next => {
            draft = draft.map(item => (item.id === next.id ? next : item));
            persist();
            renderList();
          });
        });
        del.addEventListener('click', () => {
          if (!confirm(`删除「${ev.title}」？`)) return;
          draft = draft.filter(item => item.id !== ev.id);
          persist();
          renderList();
        });
      }
    };

    filter.addEventListener('change', renderList);
    viewYearBtn.addEventListener('click', () => {
      const parsed = parseMonthKey(filter.value);
      const year = parsed?.year ?? defaults.year;
      void openMonthlyYearOverview(draft, year, {
        onChange: next => {
          draft = next.map(e => ({ ...e }));
          persist();
          renderList();
        },
      }).then(() => renderList());
    });
    addBtn.addEventListener('click', () => {
      const parsed = parseMonthKey(filter.value) || { year: defaults.year, month: defaults.month };
      void openMonthlyEventEditor(null, parsed, next => {
        draft = [...draft, next];
        persist();
        renderList();
      });
    });
    actions.querySelector('[data-close]')!.addEventListener('click', () => {
      overlay.remove();
      resolve();
    });
    overlay.addEventListener('click', e => {
      if (e.target === overlay) {
        overlay.remove();
        resolve();
      }
    });
    renderList();
  });
}
