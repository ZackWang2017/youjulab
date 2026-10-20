import { Calendar } from '@fullcalendar/core';
import type { EventClickArg, EventInput } from '@fullcalendar/core';
import { createIcons, icons } from 'lucide';
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
import { closeTodoList, openTodoList } from './todos';

/** 按日工作计划：key 为 YYYY-MM-DD，value 为 Markdown 正文。 */
export interface CalendarContent {
  format: 'invp-calendar-v1';
  timezone: string;
  /** 按周一归档的本周事项，key 为 YYYY-MM-DD。 */
  weeks: Record<string, string>;
  days: Record<string, string>;
  monthlyEvents: MonthlyEvent[];
  /** Markdown 待办，每行 `- [ ]` 或 `- [x]`。 */
  todos: string;
}

type Selection = { date: string; text: string; weekStart: string; weekText: string };

const FALLBACK: CalendarContent = {
  format: 'invp-calendar-v1',
  timezone: 'Asia/Shanghai',
  weeks: {},
  days: {},
  monthlyEvents: [],
  todos: '',
};

function dateKey(iso: string) {
  return String(iso || '').slice(0, 10);
}

function dateFromKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.getFullYear() === Number(match[1]) && date.getMonth() === Number(match[2]) - 1 && date.getDate() === Number(match[3])
    ? date
    : null;
}

function formatDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function weekStartFor(date: string) {
  const value = dateFromKey(date);
  if (!value) return date;
  value.setDate(value.getDate() - ((value.getDay() + 6) % 7));
  return formatDate(value);
}

function addDateDays(date: string, days: number) {
  const value = dateFromKey(date);
  if (!value) return date;
  value.setDate(value.getDate() + days);
  return formatDate(value);
}

function weekTone(weekStart: string) {
  let hash = 0;
  for (const char of weekStart) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return ['teal', 'blue', 'gold', 'plum'][hash % 4];
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
    return { ...FALLBACK, weeks: {}, days: {}, monthlyEvents: [], todos: '' };
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
  const weeks: Record<string, string> = {};
  if (isDayMap(raw.weeks)) {
    for (const [key, value] of Object.entries(raw.weeks)) {
      const date = dateKey(key);
      const weekStart = weekStartFor(date);
      const text = String(value || '').trim();
      if (!dateFromKey(date) || !text) continue;
      weeks[weekStart] = weeks[weekStart] ? `${weeks[weekStart].trimEnd()}\n\n${value}` : String(value);
    }
  }
  return {
    format: 'invp-calendar-v1',
    timezone,
    weeks,
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
  weekInitial: string,
  dayInitial: string,
  onSave: (weekMarkdown: string, dayMarkdown: string) => void,
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
    const titlebar = document.createElement('header');
    titlebar.className = 'calendar-day-dialog-head';
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'calendar-day-dialog-close';
    closeButton.setAttribute('title', '关闭');
    closeButton.setAttribute('aria-label', '关闭');
    closeButton.innerHTML = '<i data-lucide="x"></i>';
    titlebar.append(title, closeButton);

    const sections = document.createElement('div');
    sections.className = 'calendar-plan-sections';
    const weekSection = document.createElement('section');
    weekSection.className = 'calendar-plan-section calendar-week-section';
    const weekHeading = document.createElement('h3');
    weekHeading.textContent = `本周事项（${weekStartFor(date)} 起）`;
    const weekHost = document.createElement('div');
    weekHost.className = 'md-editor-host';
    weekHost.dataset.mdHost = '';
    weekSection.append(weekHeading, weekHost);

    const daySection = document.createElement('section');
    daySection.className = 'calendar-plan-section';
    const dayHeading = document.createElement('h3');
    dayHeading.textContent = '今日事项';
    const dayHost = document.createElement('div');
    dayHost.className = 'md-editor-host';
    dayHost.dataset.mdHost = '';
    daySection.append(dayHeading, dayHost);
    sections.append(weekSection, daySection);
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    actions.innerHTML = `
      <button type="button" data-cancel>取消</button>
      <button type="button" class="primary" data-save>保存</button>`;
    dialog.append(titlebar, sections, actions);
    overlay.append(dialog);
    document.body.append(overlay);
    createIcons({ icons });
    let weekEditor: MdEditorHandle | null = mountMarkdownEditor(weekHost, weekInitial, {
      placeholder: '写下本周重点、推进事项或风险…',
      live: true,
    });
    let dayEditor: MdEditorHandle | null = mountMarkdownEditor(dayHost, dayInitial, {
      placeholder: '写下今天的事项…',
      live: true,
    });
    requestAnimationFrame(() => {
      weekEditor?.focus();
    });

    const close = () => {
      weekEditor?.destroy();
      dayEditor?.destroy();
      weekEditor = null;
      dayEditor = null;
      overlay.remove();
      resolve();
    };

    dialog.querySelector('[data-cancel]')!.addEventListener('click', close);
    closeButton.addEventListener('click', close);
    dialog.querySelector('[data-save]')!.addEventListener('click', () => {
      const weekMarkdown = weekEditor?.getValue() || '';
      const dayMarkdown = dayEditor?.getValue() || '';
      close();
      onSave(weekMarkdown, dayMarkdown);
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
  let data: CalendarContent = { ...FALLBACK, weeks: {}, days: {}, monthlyEvents: [], todos: '' };
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
    weeks: { ...data.weeks },
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
    const weekStart = weekStartFor(date);
    selection = { date, text: data.days[date] || '', weekStart, weekText: data.weeks[weekStart] || '' };
    syncDays();
    emitDirty();
  };

  const setDayAndWeekContent = (date: string, weekMarkdown: string, dayMarkdown: string) => {
    const weekStart = weekStartFor(date);
    const cleanedWeek = weekMarkdown.replace(/^\uFEFF/, '');
    const cleanedDay = dayMarkdown.replace(/^\uFEFF/, '');
    if (cleanedWeek.trim()) data.weeks[weekStart] = cleanedWeek;
    else delete data.weeks[weekStart];
    if (cleanedDay.trim()) data.days[date] = cleanedDay;
    else delete data.days[date];
    selection = { date, text: data.days[date] || '', weekStart, weekText: data.weeks[weekStart] || '' };
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

  const toFcInputs = (): EventInput[] => [
    ...Object.entries(data.weeks)
      .filter(([, text]) => String(text || '').trim())
      .map(([weekStart, text]) => ({
        id: `week:${weekStart}`,
        // 周横幅按实际可用宽度裁切；这里必须保留完整摘要，不能预先按字符数截断。
        title: plainPreview(text, Number.MAX_SAFE_INTEGER) || '周事项',
        start: weekStart,
        end: addDateDays(weekStart, 7),
        allDay: true,
        editable: false,
        startEditable: false,
        durationEditable: false,
        classNames: ['calendar-week-event', `calendar-week-tone-${weekTone(weekStart)}`],
        extendedProps: { content: text, kind: 'week', weekStart },
      })),
    ...Object.entries(data.days)
      .filter(([, text]) => String(text || '').trim())
      .map(([date, text]) => ({
        id: `day:${date}`,
        title: plainPreview(text, 40) || '今日事项',
        start: date,
        allDay: true,
        editable: false,
        startEditable: false,
        durationEditable: false,
        classNames: ['calendar-day-event'],
        extendedProps: { content: text, kind: 'day', date },
      })),
  ];

  const syncDays = () => {
    if (!calendar) return;
    calendar.removeAllEvents();
    for (const ev of toFcInputs()) calendar.addEvent(ev);
  };

  const setSelectionDate = (date: string) => {
    const weekStart = weekStartFor(date);
    selection = { date, text: data.days[date] || '', weekStart, weekText: data.weeks[weekStart] || '' };
    host.querySelectorAll('.fc-day-selected').forEach(el => el.classList.remove('fc-day-selected'));
    host.querySelectorAll(`[data-date="${date}"]`).forEach(el => el.classList.add('fc-day-selected'));
    changed();
  };

  const openDay = (date: string) => {
    if (dayDialogOpen) return;
    dayDialogOpen = true;
    setSelectionDate(date);
    const weekStart = weekStartFor(date);
    void openDayPlan(date, data.weeks[weekStart] || '', data.days[date] || '', (weekMarkdown, dayMarkdown) => {
      setDayAndWeekContent(date, weekMarkdown, dayMarkdown);
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

  let dayDialogOpen = false;

  calendar = new Calendar(host, {
    plugins: [dayGridPlugin, interactionPlugin, listPlugin],
    locale: zhCnLocale,
    firstDay: 1,
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
      if (arg.event.extendedProps.kind === 'week') {
        const week = document.createElement('div');
        week.className = 'fc-week-plan';
        week.textContent = arg.event.title;
        return { domNodes: [week] };
      }
      const content = String(arg.event.extendedProps.content || arg.event.title || '');
      return { domNodes: [buildDayContentEl(content)] };
    },
    dateClick: info => {
      const date = info.dateStr.slice(0, 10);
      setSelectionDate(date);
      openDay(date);
    },
    eventClick: (info: EventClickArg) => {
      const date = info.event.extendedProps.kind === 'week'
        ? String(info.event.extendedProps.weekStart || info.event.startStr).slice(0, 10)
        : String(info.event.extendedProps.date || info.event.startStr).slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return;
      setSelectionDate(date);
      openDay(date);
    },
    datesSet: () => {
      refreshMonthStrip();
    },
  });
  calendar.render();
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
        week_start: selection.weekStart,
        week_text: selection.weekText,
      },
      surrounding: `本周起始：${selection.weekStart}\n本周事项：\n${selection.weekText || '（本周尚无事项）'}\n\n日期：${selection.date}\n今日事项：\n${selection.text || '（当天尚无事项）'}`,
    };
  }

  return {
    async load(asset) {
      // 待办窗口独立挂在 body；先持久化并关闭旧窗口，避免其状态串到新日历资产。
      closeTodoList();
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
      const missingWeeks = !(asset.content && typeof asset.content === 'object' && 'weeks' in (asset.content as object));
      const missingMonthly = !(asset.content && typeof asset.content === 'object' && 'monthlyEvents' in (asset.content as object));
      const missingTodos = !(asset.content && typeof asset.content === 'object' && 'todos' in (asset.content as object));
      if (looksLegacy || missingDays || missingWeeks || missingMonthly || missingTodos) {
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
