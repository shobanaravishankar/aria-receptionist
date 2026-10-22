/* Time in the spa's zone, as exact instants. An instant is a number of milliseconds since the epoch; all arithmetic is on real elapsed time (never wall-clock), exactly like the Python
 * backend's `add_minutes`, and local fields (hour, minute, weekday, date) are read through Intl. No dependency. */

export const ZONE = "America/New_York";
export type Instant = number;
export const MINUTE = 60_000;
export const DAY_MS = 24 * 60 * MINUTE;

const parts = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short",
});
const WEEKDAYS: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

export interface LocalParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  weekday: number; // Monday = 0, as Python's date.weekday()
}

export function localParts(t: Instant): LocalParts {
  const out: Record<string, string> = {};
  for (const p of parts.formatToParts(new Date(t))) out[p.type] = p.value;
  return {
    year: Number(out.year), month: Number(out.month), day: Number(out.day), hour: Number(out.hour) % 24, minute: Number(out.minute), second: Number(out.second),
    weekday: WEEKDAYS[out.weekday],
  };
}

/** Real elapsed minutes after (or before, if negative) an instant: Python's `add_minutes`, not wall-clock arithmetic. */
export function addMinutes(t: Instant, minutes: number): Instant {
  return t + minutes * MINUTE;
}

/** The zone's offset from UTC at an instant, in minutes (negative west of Greenwich). */
export function offsetMinutes(t: Instant): number {
  const p = localParts(t);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUtc - Math.floor(t / 1000) * 1000) / MINUTE);
}

/** A wall-clock time in the zone as an instant, with Python's rule for an aware datetime built from wall-clock fields (fold = 0): a repeated hour (clocks going back) takes its FIRST
 * occurrence, and a skipped hour (clocks going forward) is read with the offset in force before the change. */
export function fromLocal(year: number, month: number, day: number, hour = 0, minute = 0, second = 0): Instant {
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  const offsets = new Set([offsetMinutes(guess - DAY_MS), offsetMinutes(guess + DAY_MS)]);
  const valid: Instant[] = [];
  for (const offset of offsets) {
    const candidate = guess - offset * MINUTE;
    const p = localParts(candidate);
    if (p.year === year && p.month === month && p.day === day && p.hour === hour && p.minute === minute && p.second === second) valid.push(candidate);
  }
  if (valid.length) return Math.min(...valid);
  return guess - offsetMinutes(guess - DAY_MS) * MINUTE;
}

/** The first instant of a calendar day in the zone, and the instant the next day begins. */
export function dayStart(date: DateParts): Instant {
  return fromLocal(date.year, date.month, date.day);
}
export function nextDay(date: DateParts): DateParts {
  const d = new Date(Date.UTC(date.year, date.month - 1, date.day) + DAY_MS);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
}
export function addDays(date: DateParts, days: number): DateParts {
  const d = new Date(Date.UTC(date.year, date.month - 1, date.day) + days * DAY_MS);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
}

export interface DateParts {
  year: number;
  month: number;
  day: number;
}

export function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}
export function isoDate(date: DateParts): string {
  return `${pad(date.year, 4)}-${pad(date.month)}-${pad(date.day)}`;
}
export function parseIsoDate(text: string): DateParts {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) throw new Error("not an ISO date");
  const date = { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
  const back = new Date(Date.UTC(date.year, date.month - 1, date.day));
  if (back.getUTCFullYear() !== date.year || back.getUTCMonth() + 1 !== date.month || back.getUTCDate() !== date.day) throw new Error("not a real date");
  return date;
}
/** The calendar date (in the zone) an instant falls on. */
export function dateOf(t: Instant): DateParts {
  const p = localParts(t);
  return { year: p.year, month: p.month, day: p.day };
}
/** Monday = 0, for a calendar date (as Python's date.weekday()). */
export function weekdayOf(date: DateParts): number {
  return (new Date(Date.UTC(date.year, date.month - 1, date.day)).getUTCDay() + 6) % 7;
}
export function compareDates(a: DateParts, b: DateParts): number {
  return Date.UTC(a.year, a.month - 1, a.day) - Date.UTC(b.year, b.month - 1, b.day);
}
export function daysBetween(a: DateParts, b: DateParts): number {
  return Math.round(compareDates(b, a) / DAY_MS);
}

/** "2026-10-22 16:00": the personal-data-free rendering the backend uses in messages (Python's strftime("%Y-%m-%d %H:%M")). */
export function fmt(t: Instant): string {
  const p = localParts(t);
  return `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`;
}
export function hhmm(t: Instant): string {
  const p = localParts(t);
  return `${pad(p.hour)}:${pad(p.minute)}`;
}
/** Minutes since local midnight, by the wall clock (hour * 60 + minute). */
export function minuteOfDay(t: Instant): number {
  const p = localParts(t);
  return p.hour * 60 + p.minute;
}
