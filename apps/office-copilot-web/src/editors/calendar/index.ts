import { Calendar } from '@fullcalendar/core';
import type { EventClickArg, EventInput } from '@fullcalendar/core';
import zhCnLocale from '@fullcalendar/core/locales/zh-cn';
import dayGridPlugin from '@fullcalendar/daygrid';
import interactionPlugin from '@fullcalendar/interaction';
import listPlugin from '@fullcalendar/list';
import './style.css';
import { download, type EditorAdapter, type Patch } from '../../context/types';
import { getDayBadge } from './day-meta';
import { bindRenderedPlanCopy, mountMarkdownEditor, renderMarkdown, type MdEditorHandle } from './markdown';
import {
  eventsForMonth,
  normalizeMonthlyEvents,
  openMonthlyEventManager,
  openMonthlyMonthOverview,
  REPEAT_LABEL,
  type MonthlyEvent,
} from './monthly-events';
import { openTodoList } from './todos';

/** 按日工作计划：key 为 YYYY-MM-DD，value 为 Markdown 正文。 */
export interface CalendarContent {
  format: 'invp-calendar-v1';
  timezone: string;
  days: Record<string, string>;
  monthlyEvents: MonthlyEvent[];
  /** Markdown 待办，每行 `- [ ]` 或 `- [x]`。 */
  todos: string;
}

type Selection = { date: string; text: string };

const FALLBACK: CalendarContent = {
  format: 'invp-calendar-v1',
  timezone: 'Asia/Shanghai',
  days: {},
  monthlyEvents: [],
  todos: '',
};

function dateKey(iso: string) {
  return String(iso || '').slice(0, 10);
}

function isDayMap(value: unknown): value is Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return Object.values(value).every(v => typeof v === 'string');
}

/** 兼容旧版 events[]，合并为按日 Markdown。 */
function migrateLegacyEvents(events: unknown[]): Record<string, string> {
  const days: Record<string, string> = {};
  for (const raw of events) {
    if (!raw || typeof raw !== 'object') continue;
    const item = raw as Record<string, unknown>;
    const date = dateKey(String(item.start || ''));
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    const title = String(item.title || '').trim();
    const memo = String(item.memo || '').trim();
    const chunk = memo || title;
    if (!chunk) continue;
    days[date] = days[date] ? `${days[date].trimEnd()}\n\n${chunk}` : chunk;
  }
  return days;
}

function normalizeContent(content: unknown): CalendarContent {
  if (!content || typeof content !== 'object' || Array.isArray(content)) {
    return { ...FALLBACK, days: {}, monthlyEvents: [], todos: '' };
  }
  const raw = content as Record<string, unknown>;
  const timezone = typeof raw.timezone === 'string' && raw.timezone ? raw.timezone : 'Asia/Shanghai';
  let days: Record<string, string> = {};
  if (isDayMap(raw.days)) {
    for (const [k, v] of Object.entries(raw.days)) {
      const key = dateKey(k);
      const text = String(v || '').trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(key) && text) days[key] = String(v);
    }
  } else if (Array.isArray(raw.events)) {
    days = migrateLegacyEvents(raw.events);
  }
  return {
    format: 'invp-calendar-v1',
    timezone,
    days,
    monthlyEvents: normalizeMonthlyEvents(raw.monthlyEvents),
    todos: typeof raw.todos === 'string' ? raw.todos : '',
  };
}

function formatDayLabel(date: string) {
  const [y, m, d] = date.split('-').map(Number);
  const week = '日一二三四五六'[new Date(y, m - 1, d).getDay()];
  return `${y}年${m}月${d}日 周${week}`;
}

function plainPreview(md: string, max = 80) {
  return md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[*_~>`#-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, max);
}

function openDayPlan(
  date: string,
  initial: string,
  onSave: (markdown: string) => void,
) {
  return new Promise<void>(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const dialog = document.createElement('div');
    dialog.className = 'calendar-day-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-labelledby', 'calendar-day-title');

    const badge = getDayBadge(date);
    const title = document.createElement('h2');
    title.id = 'calendar-day-title';
    title.className = 'calendar-day-title';
    const solar = document.createElement('span');
    solar.className = 'calendar-day-solar';
    solar.textContent = formatDayLabel(date);
    title.append(solar);
    const meta = document.createElement('span');
    meta.className = 'calendar-day-title-meta';
    if (badge.lunar) {
      const lunar = document.createElement('span');
      lunar.className = 'fc-day-lunar';
      lunar.textContent = badge.lunar;
      meta.append(lunar);
    }
    if (badge.festival) {
      const fest = document.createElement('span');
      fest.className = 'fc-day-fest';
      fest.textContent = badge.festival;
      meta.append(fest);
    }
    if (badge.rest || badge.work) {
      const mark = document.createElement('span');
      mark.className = `fc-day-badge ${badge.rest ? 'is-rest' : 'is-work'}`;
      mark.textContent = badge.rest ? '休' : '班';
      meta.append(mark);
    }
    if (meta.childNodes.length) title.append(meta);

    const host = document.createElement('div');
    host.className = 'md-editor-host';
    host.dataset.mdHost = '';
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `
      <button type="button" data-cancel>取消</button>
      <button type="button" class="primary" data-save>保存</button>`;
    dialog.append(title, host, actions);
    overlay.append(dialog);
    document.body.append(overlay);
    let mdEditor: MdEditorHandle | null = mountMarkdownEditor(host, initial, {
      placeholder: '写下今天的内容…',
      live: true,
    });
    requestAnimationFrame(() => {
      mdEditor?.focus();
    });

    const close = () => {
      mdEditor?.destroy();
      mdEditor = null;
      overlay.remove();
      resolve();
    };

    dialog.querySelector('[data-cancel]')!.addEventListener('click', close);
    dialog.querySelector('[data-save]')!.addEventListener('click', () => {
      const markdown = mdEditor?.getValue() || '';
      close();
      onSave(markdown);
    });
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  });
}

function buildDayContentEl(markdown: string): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'plan-md fc-day-plan';
  wrap.innerHTML = renderMarkdown(markdown);
  wrap.title = plainPreview(markdown, 200);
  return wrap;
}

export function mountCalendar(
  container: HTMLElement,
  changed: () => void,
  onDirty: (content: unknown) => void,
): EditorAdapter {
  let revision = 0;
  let assetId = '';
  let loading = false;
  let data: CalendarContent = { ...FALLBACK, days: {}, monthlyEvents: [], todos: '' };
  let selection: Selection | null = null;
  let calendar: Calendar | null = null;

  container.innerHTML = `<div class="calendar-stage"><div data-calendar-host></div></div>`;
  const host = container.querySelector<HTMLElement>('[data-calendar-host]')!;
  bindRenderedPlanCopy(host);

  const emitDirty = () => {
    if (loading || !assetId) return;
    revision += 1;
    onDirty(snapshotContent());
    changed();
  };

  const snapshotContent = (): CalendarContent => ({
    format: 'invp-calendar-v1',
    timezone: data.timezone || 'Asia/Shanghai',
    days: { ...data.days },
    monthlyEvents: data.monthlyEvents.map(e => ({ ...e })),
    todos: data.todos || '',
  });

  const viewYearMonth = () => {
    const d = calendar?.getDate() || new Date();
    return { year: d.getFullYear(), month: d.getMonth() + 1 };
  };

  const setDayContent = (date: string, markdown: string) => {
    const cleaned = markdown.replace(/^\uFEFF/, '');
    if (cleaned.trim()) data.days[date] = cleaned;
    else delete data.days[date];
    selection = { date, text: data.days[date] || '' };
    syncDays();
    emitDirty();
  };

  const setMonthlyEvents = (events: MonthlyEvent[]) => {
    data.monthlyEvents = events;
    refreshMonthStrip();
    emitDirty();
  };

  const setTodos = (markdown: string) => {
    const next = markdown.replace(/^\uFEFF/, '');
    if (next === data.todos) return;
    data.todos = next;
    emitDirty();
  };

  const openTodos = () => {
    void openTodoList(data.todos || '', setTodos);
  };

  const toFcInputs = (): EventInput[] => Object.entries(data.days)
    .filter(([, text]) => String(text || '').trim())
    .map(([date, text]) => ({
      id: date,
      title: plainPreview(text, 40) || '工作计划',
      start: date,
      allDay: true,
      editable: false,
      startEditable: false,
      durationEditable: false,
      extendedProps: { content: text },
    }));

  const syncDays = () => {
    if (!calendar) return;
    calendar.removeAllEvents();
    for (const ev of toFcInputs()) calendar.addEvent(ev);
  };

  const setSelectionDate = (date: string) => {
    selection = { date, text: data.days[date] || '' };
    host.querySelectorAll('.fc-day-selected').forEach(el => el.classList.remove('fc-day-selected'));
    host.querySelectorAll(`[data-date="${date}"]`).forEach(el => el.classList.add('fc-day-selected'));
    changed();
  };

  const openDay = (date: string) => {
    if (dayDialogOpen) return;
    dayDialogOpen = true;
    setSelectionDate(date);
    void openDayPlan(date, data.days[date] || '', markdown => {
      setDayContent(date, markdown);
    }).finally(() => {
      dayDialogOpen = false;
      changed();
    });
  };

  const openMonthOverview = () => {
    const anchor = viewYearMonth();
    void openMonthlyMonthOverview(data.monthlyEvents, anchor.year, anchor.month, {
      onChange: next => setMonthlyEvents(next),
    });
  };

  const openManage = () => {
    const anchor = viewYearMonth();
    void openMonthlyEventManager(data.monthlyEvents, anchor, next => {
      setMonthlyEvents(next);
    });
  };

  function ensureMonthStrip(): HTMLElement | null {
    const todayBtn = host.querySelector<HTMLElement>('.fc-today-button');
    if (!todayBtn?.parentElement) return null;
    let strip = host.querySelector<HTMLElement>('.cal-month-events-strip');
    if (!strip) {
      strip = document.createElement('div');
      strip.className = 'cal-month-events-strip';
      todayBtn.insertAdjacentElement('afterend', strip);
    }
    return strip;
  }

  function refreshMonthStrip() {
    const strip = ensureMonthStrip();
    if (!strip) return;
    const current = viewYearMonth();

    strip.replaceChildren();

    const scroller = document.createElement('div');
    scroller.className = 'cal-month-events-scroll';
    scroller.setAttribute('role', 'list');

    const visible = eventsForMonth(data.monthlyEvents, current.year, current.month);
    if (!visible.length) {
      const empty = document.createElement('div');
      empty.className = 'cal-month-events-empty';
      empty.textContent = '本月暂无事件';
      scroller.append(empty);
    } else {
      for (const ev of visible) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'cal-month-event-chip';
        btn.setAttribute('role', 'listitem');
        btn.title = `${ev.title}（${REPEAT_LABEL[ev.repeat]}）· 点击查看本月全部事件`;
        const label = document.createElement('span');
        label.className = 'cal-month-event-title';
        label.textContent = ev.title;
        const badge = document.createElement('span');
        badge.className = `cal-me-badge cal-me-badge-${ev.repeat}`;
        badge.textContent = REPEAT_LABEL[ev.repeat];
        btn.append(label, badge);
        btn.addEventListener('click', e => {
          e.preventDefault();
          e.stopPropagation();
          openMonthOverview();
        });
        scroller.append(btn);
      }
    }

    strip.append(scroller);
  }

  let lastDateClick = { date: '', at: 0 };
  let dayDialogOpen = false;

  const bindDayDblClick = () => {
    host.querySelectorAll<HTMLElement>('.fc-daygrid-day[data-date]').forEach(cell => {
      if (cell.dataset.dblBound) return;
      cell.dataset.dblBound = '1';
      cell.addEventListener('dblclick', e => {
        e.preventDefault();
        e.stopPropagation();
        const date = cell.dataset.date;
        if (!date) return;
        openDay(date);
      });
    });
  };

  calendar = new Calendar(host, {
    plugins: [dayGridPlugin, interactionPlugin, listPlugin],
    locale: zhCnLocale,
    initialView: 'dayGridMonth',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'todos,monthlyManage dayGridMonth,dayGridWeek,dayGridDay,listWeek',
    },
    customButtons: {
      todos: {
        text: '待办事项',
        hint: 'Markdown 待办，勾选完成',
        click: () => openTodos(),
      },
      monthlyManage: {
        text: '月度事件',
        hint: '按月添加、编辑、删除月度事件',
        click: () => openManage(),
      },
    },
    buttonText: {
      today: '今天',
      month: '月',
      week: '周',
      day: '日',
      list: '列表',
    },
    height: '100%',
    editable: false,
    selectable: false,
    dayMaxEvents: true,
    events: [],
    dayCellContent(arg) {
      if (String(arg.view.type || '').startsWith('list')) {
        return true;
      }
      const y = arg.date.getFullYear();
      const m = String(arg.date.getMonth() + 1).padStart(2, '0');
      const d = String(arg.date.getDate()).padStart(2, '0');
      const date = `${y}-${m}-${d}`;
      const badge = getDayBadge(date);

      const wrap = document.createElement('div');
      wrap.className = 'fc-day-head';

      const left = document.createElement('div');
      left.className = 'fc-day-left';

      if (badge.lunar) {
        const lunar = document.createElement('span');
        lunar.className = 'fc-day-lunar';
        lunar.textContent = badge.lunar;
        left.append(lunar);
      }
      if (badge.festival) {
        const fest = document.createElement('span');
        fest.className = 'fc-day-fest';
        fest.textContent = badge.festival;
        left.append(fest);
      }
      if (badge.rest || badge.work) {
        const mark = document.createElement('span');
        mark.className = `fc-day-badge ${badge.rest ? 'is-rest' : 'is-work'}`;
        mark.textContent = badge.rest ? '休' : '班';
        left.append(mark);
      }

      const num = document.createElement('span');
      num.className = 'fc-day-num';
      num.textContent = arg.dayNumberText.replace(/日$/, '');

      wrap.append(left, num);
      return { domNodes: [wrap] };
    },
    eventContent(arg) {
      const content = String(arg.event.extendedProps.content || arg.event.title || '');
      return { domNodes: [buildDayContentEl(content)] };
    },
    dateClick: info => {
      const date = info.dateStr.slice(0, 10);
      setSelectionDate(date);
      const now = Date.now();
      if (date === lastDateClick.date && now - lastDateClick.at < 450) {
        openDay(date);
        lastDateClick = { date: '', at: 0 };
        return;
      }
      lastDateClick = { date, at: now };
    },
    eventClick: (info: EventClickArg) => {
      const date = dateKey(info.event.id || info.event.startStr);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return;
      setSelectionDate(date);
      openDay(date);
    },
    datesSet: () => {
      bindDayDblClick();
      refreshMonthStrip();
    },
  });
  calendar.render();
  bindDayDblClick();
  refreshMonthStrip();

  function currentSelection() {
    if (!assetId || !selection) return null;
    return {
      document_id: assetId,
      surface: 'calendar' as const,
      revision,
      selection: {
        date: selection.date,
        text: selection.text,
      },
      surrounding: selection.text.trim()
        ? `日期：${selection.date}\n内容：\n${selection.text}`
        : `日期：${selection.date}\n（当天尚无内容）`,
    };
  }

  return {
    async load(asset) {
      loading = true;
      assetId = asset.id;
      revision = asset.revision;
      selection = null;
      data = normalizeContent(asset.content);
      syncDays();
      refreshMonthStrip();
      loading = false;
      const looksLegacy = Boolean(
        asset.content
        && typeof asset.content === 'object'
        && Array.isArray((asset.content as { events?: unknown }).events)
        && !isDayMap((asset.content as { days?: unknown }).days),
      );
      const missingDays = !(asset.content && typeof asset.content === 'object' && 'days' in (asset.content as object));
      const missingMonthly = !(asset.content && typeof asset.content === 'object' && 'monthlyEvents' in (asset.content as object));
      const missingTodos = !(asset.content && typeof asset.content === 'object' && 'todos' in (asset.content as object));
      if (looksLegacy || missingDays || missingMonthly || missingTodos) {
        emitDirty();
      } else {
        changed();
      }
    },
    context() {
      const snap = currentSelection();
      if (!snap) return null;
      if (!String(snap.selection.text || '').trim()) return null;
      return snap;
    },
    anchor() {
      return currentSelection();
    },
    apply(patch: Patch) {
      if (patch.revision !== revision) throw new Error('工作计划已更改，请重新生成建议。');
      const date = String(patch.selection?.date || '').slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('请先选中日期。');
      const replacement = String(patch.replacement ?? '');
      if (patch.task === 'generate') {
        const text = replacement.trim();
        if (!text) throw new Error('生成内容不能为空。');
        const prev = (data.days[date] || '').trimEnd();
        setDayContent(date, prev ? `${prev}\n\n${text}` : text);
        return;
      }
      setDayContent(date, replacement);
    },
    snapshot() {
      return snapshotContent();
    },
    undo() { /* 无内置撤销 */ },
    export() {
      download('calendar.invp-calendar-v1.json', JSON.stringify(snapshotContent(), null, 2));
    },
  };
}
