import { Solar, HolidayUtil } from 'lunar-javascript';

export type DayBadge = {
  lunar: string;
  /** 法定节假日休息 */
  rest: boolean;
  /** 调休上班 */
  work: boolean;
  /** 节日短名（可选展示） */
  festival?: string;
};

const cache = new Map<string, DayBadge>();

function parseYmd(date: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

/** 农历文案：初一显示「九月初一」，其余只显示「初十」等。 */
function lunarText(year: number, month: number, day: number): string {
  const lunar = Solar.fromYmd(year, month, day).getLunar();
  const dayCn = lunar.getDayInChinese();
  if (lunar.getDay() === 1) return `${lunar.getMonthInChinese()}月${dayCn}`;
  return dayCn;
}

/**
 * 查询某日农历与法定休/班。
 *
 * 数据来源：`lunar-javascript` 的 HolidayUtil（内置国务院办公厅公布的放假调休安排）。
 * 当前库版本法定数据约到 2026 年国庆结束；其后月份若无国务院安排则不会出现「休/班」。
 * HolidayUtil：isWork()=true 为调休上班；false 为放假。
 */
export function getDayBadge(date: string): DayBadge {
  const hit = cache.get(date);
  if (hit) return hit;

  const ymd = parseYmd(date);
  if (!ymd) {
    const empty = { lunar: '', rest: false, work: false };
    cache.set(date, empty);
    return empty;
  }
  const [y, m, d] = ymd;
  let lunar = '';
  try {
    lunar = lunarText(y, m, d);
  } catch {
    lunar = '';
  }

  let rest = false;
  let work = false;
  let festival: string | undefined;
  try {
    const holiday = HolidayUtil.getHoliday(y, m, d);
    if (holiday) {
      festival = String(holiday.getName() || '') || undefined;
      if (holiday.isWork()) work = true;
      else rest = true;
    }
  } catch {
    /* 无数据年份忽略 */
  }

  const badge = { lunar, rest, work, festival };
  cache.set(date, badge);
  return badge;
}
