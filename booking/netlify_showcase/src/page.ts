/* The demo calendar PAGE: a Booksy-style day view drawn from the dataset. Port of aria_booking/demo/page.py; the stylesheet, the small script, the icons and the colours are DATA exported from
 * the Python module (oracle/export_data.py), so the page here is byte for byte the page the Python demo draws (a test compares them over many days and stored appointments).
 *
 * What is faithful, and what is not: geometry and structure follow a redacted capture of the real day view (56 px sidebar, 75 px toolbar, 250 px staff columns under 64 px headers, 80 px per
 * hour from 9:00 AM to 9:00 PM) and the same test ids the real reader consumes. Colours, fonts, icons and borders are neutral, clearly approximate choices: nothing claims a visual match to
 * any real product's styling. A persistent banner says this is a DEMO. The page has no sign-in form, no credentials, no customer contact details and no notes. */

import page from "./data/page.json" with { type: "json" };
import { GRID_END, GRID_START, NotCovered } from "./dataset.ts";
import type { DemoDataset } from "./dataset.ts";
import { shownHours } from "./dayplan.ts";
import type { DayPlan, StaffPlan, Visit } from "./dayplan.ts";
import { addDays, isoDate } from "./time.ts";
import type { DateParts } from "./time.ts";
import type { StoredAppointment } from "./appointments.ts";

export const DEMO_BUSINESS_ID = "9000001"; // synthetic: used only in the demo page's address
export const DEMO_LABEL = "Demo calendar - simulated availability";
export const CALENDAR_PATH = `/pro/en-us/${DEMO_BUSINESS_ID}/calendar`;
const PX_PER_HOUR = 80;
const COLUMN_WIDTH = 250;
const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ICONS = page.icons as Record<string, string>;
const COLORS = page.categoryColors as Record<string, [string, string]>;

const y = (minute: number) => Math.round(((minute - GRID_START) * PX_PER_HOUR) / 60);
/** Python's round(): halves go to the even neighbour (card heights are whole numbers here, but the rule is kept). */
function pyRound(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}
const yy = (minute: number) => pyRound(((minute - GRID_START) * PX_PER_HOUR) / 60);
export const hm = (minutes: number): string => {
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return `${hour % 12 || 12}:${String(minute).padStart(2, "0")} ${hour < 12 ? "AM" : "PM"}`;
};
const hourLabel = (hour: number) => `${hour % 12 || 12} ${hour < 12 ? "am" : "pm"}`;
const initials = (name: string) => name.split(/\s+/).filter(Boolean).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
const TONES = ["#8c6bb1", "#4f9d8f", "#c97b9b", "#5b88b8", "#b88a4a", "#7a8f5a"];
const tone = (name: string) => TONES[[...name].reduce((n, ch) => n + ch.codePointAt(0)!, 0) % 6];
const esc = (text: unknown) => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#x27;");

function filterPanel(dataset: DemoDataset, shownIds: Set<string>): string {
  const rows: Array<[string, string, string, boolean]> = [
    ["filtersValue-input", "filtersValue", "Only me", false],
    ["filtersValue-input", "filtersValue", "Working Staff Members", true],
    ["filtersValue_all-input", "filtersValue_all", "Select All", false],
    ...dataset.roster.map((s): [string, string, string, boolean] => [`filtersValue_${s.staffId}-input`, `filtersValue_${s.staffId}`, s.name, shownIds.has(s.staffId)]),
  ];
  const items = rows.map(([boxTestid, labelTestid, name, checked], index) => {
    const boxId = `ariaFilter${index}`;
    const avatar = index >= 3 ? `<span class="ariaMini" style="background:${tone(name)}">${esc(initials(name))}</span>` : "";
    return (
      `<li class="ariaFilterRow"><input type="checkbox" id="${boxId}" data-testid="${esc(boxTestid)}"${checked ? " checked" : ""}>${avatar}` +
      `<label for="${boxId}" data-testid="${esc(labelTestid)}" class="_resourceItem_xftsu_44 flex items-center">${esc(name)}</label></li>`
    );
  });
  return (
    '<div data-testid="resources-filter-by-staffers" class="ariaFilterBox"><div class="ariaFilterTitle">Staff</div><ul>' + items.join("") + "</ul></div>" +
    '<div class="sticky bottom-0 bg-primary ariaDrawerFoot"><button data-testid="calendar-status-filter-clear" class="ariaBtn" type="button">Clear</button>' +
    '<button data-testid="calendar-status-filter-submit" class="ariaBtn ariaBtnPrimary" type="button">Apply</button></div>'
  );
}

function card(plan: StaffPlan, visit: Visit, ordinal: number): string {
  const [border, tint] = COLORS[visit.category] ?? ["#6b7280", "#f3f4f6"];
  const top = yy(visit.start);
  const height = pyRound(((visit.end - visit.start) * PX_PER_HOUR) / 60) - 1;
  const size = height >= 99 ? "sizeL_mf7bh_241" : height >= 59 ? "sizeM_mf7bh_218" : "sizeS_mf7bh_195";
  const tid = `calendar-content-calendar-grid-calendar-card-${plan.staff.staffId}-${ordinal}`;
  return (
    `<div class="_cardWrap_mf7bh_10" data-testid="${tid}" data-appointment-id="${esc(visit.reference || "x")}" style="top:${top}px;height:${height}px">` +
    `<div class="_card_mf7bh_10 _padding-right-0_mf7bh_174 _${size}" style="--cat:${border};--tint:${tint}">` +
    `<div class="_cardBorder_mf7bh_65"></div>` +
    `<p class="_header_mf7bh_73 _text_mf7bh_57"><span class="hour_from">${hm(visit.start)}</span> - <span class="hour_till">${hm(visit.end)}</span></p>` +
    `<p class="_details_mf7bh_53"><span class="_text_mf7bh_57 ariaClient">${esc(visit.client)}</span>` +
    `<span class="_text_mf7bh_57 ariaService">${esc(visit.service)}</span></p></div></div>`
  );
}

function offBlock(plan: StaffPlan, start: number, end: number, label: string, ordinal: number): string {
  const tid = `calendar-content-calendar-grid-calendar-card-${plan.staff.staffId}-${ordinal}`;
  const top = yy(start);
  const height = pyRound(((end - start) * PX_PER_HOUR) / 60) - 1;
  return (
    `<div class="_cardWrap_mf7bh_10" data-testid="${tid}" style="top:${top}px;height:${height}px">` +
    `<div data-testid="non-working-event" class="_card_mf7bh_10 _off_mf7bh_14 _isNonworking_mf7bh_118" data-label="${esc(label)}"></div></div>`
  );
}

function column(plan: StaffPlan): string {
  const cards: string[] = [];
  let ordinal = 0;
  const window = plan.hours ? plan.hours : [GRID_START, GRID_END];
  // outside the working window the grid is shaded as non-working (the real calendar does the same); a person with no shown hours is shaded nowhere
  if (shownHours(plan) !== null) {
    if (window[0] > GRID_START) cards.push(offBlock(plan, GRID_START, window[0], "", ordinal++));
    if (window[1] < GRID_END) cards.push(offBlock(plan, window[1], GRID_END, "", ordinal++));
  }
  for (const b of plan.breaks) cards.push(offBlock(plan, b.start, b.end, b.label, ordinal++));
  for (const v of plan.visits) cards.push(card(plan, v, ordinal++));
  return `<div class="_calendarColumn_1pknx_16" data-resource="${esc(plan.staff.staffId)}">` + cards.join("") + "</div>";
}

function header(plan: StaffPlan): string {
  const shown = shownHours(plan);
  const hours = shown ? `<span class="_hours_10ie7_48">${hm(shown[0])}-${hm(shown[1])}</span>` : "";
  return (
    '<div data-testid="resource" class="_resource_na70m_7 _resource_10ie7_7 _resourceFixedSize_10ie7_31">' +
    `<span class="ariaAvatar" style="background:${tone(plan.staff.name)}">${esc(initials(plan.staff.name))}</span>` +
    '<div data-testid="resource-content" class="_resourceWrapper_10ie7_16 flex"><div class="flex-1">' +
    `<span class="_name_10ie7_43 w-full">${esc(plan.staff.name)}</span><br>${hours}</div></div></div>`
  );
}

const svg = (inner: string) => `<svg class="b-svg icon" viewBox="0 0 28 28" width="28" height="28" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
function sideMenu(): string {
  let items = (page.sideMenu as Array<[string, boolean]>).map(([tid, active]) => `<li><div data-testid="${tid}" class="iconWrapper${active ? " active" : ""}">${svg(ICONS[tid])}</div></li>`).join("");
  const lower = ["profile", "settings"].map((tid) => `<li><div data-testid="${tid}" class="iconWrapper">${svg(ICONS[tid])}</div></li>`).join("");
  items +=
    '<li class="ariaSep"><hr class="separator"></li>' + lower +
    `<li><div class="iconWrapper"><button type="button" aria-label="Help"><svg class="b-svg icon" viewBox="0 0 28 28" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS.help}</svg></button></div></li>`;
  return (
    '<aside data-testid="side-menu" class="bar"><div class="barContent"><div data-testid="side-menu-select-language" class="language"></div><hr class="separator">' +
    `<ul class="list">${items}</ul><div class="avatar"><div data-testid="side-menu-avatar" class="avatarBox"><div class="avatarInitials">D</div></div></div></div></aside>`
  );
}

/** The full HTML document for one day. A date outside the dataset's coverage shows a clear 'no demo data' state and NO columns. `rows` are the stored appointments of that day. */
export function renderPage(dataset: DemoDataset, day: DateParts, rows: Iterable<StoredAppointment> = [], basePath = ""): string {
  let plan: DayPlan | null;
  try {
    plan = dataset.dayPlan(day, rows);
  } catch (error) {
    if (!(error instanceof NotCovered)) throw error;
    plan = null;
  }
  const plans = plan ? plan.plans : [];
  const shownIds = new Set(plans.map((p) => p.staff.staffId));
  const columns = plans.map(column).join("");
  const headers = plans.map(header).join("");
  const width = Math.max(plans.length, 1) * COLUMN_WIDTH;
  const layout = plans.map(() => '<div class="_calendarColumn_1pknx_16"></div>').join("");
  const labels: string[] = [];
  for (let h = Math.floor(GRID_START / 60) + 1; h < Math.floor(GRID_END / 60); h++) labels.push(`<span class="_hourText_ultg9_16" style="top:${y(h * 60) - 9}px">${hourLabel(h)}</span>`);
  const prevDay = isoDate(addDays(day, -1));
  const nextDay = isoDate(addDays(day, 1));
  const base = `${basePath}/pro/en-us/${DEMO_BUSINESS_ID}/calendar`;
  const notice = plan ? "" : `<div class="ariaNoData" data-testid="demo-no-data">The demo calendar has no data for ${esc(isoDate(day))}. It covers ${esc(isoDate(dataset.firstDay))} to ${esc(isoDate(dataset.lastDay))}.</div>`;
  const weekday = WEEKDAY_SHORT[(new Date(Date.UTC(day.year, day.month - 1, day.day)).getUTCDay() + 6) % 7];
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo calendar - simulated availability</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>${page.css}</style></head>
<body data-source="demo_calendar" data-demo-date="${esc(isoDate(day))}">
<div id="demo-banner" data-testid="demo-banner"><strong>${esc(DEMO_LABEL)}</strong><span>All appointments, clients and availability on this page are invented for a demo. This is not Booksy and not the salon's real schedule.</span></div>
<div class="w-full h-full" id="root"><main data-testid="main" class="flex flex-col h-full"><div data-testid="calendar-view" class="flex w-full h-full">
${sideMenu()}
<div data-testid="content" class="content"><div class="calendarView">
<header class="bg-primary header"><div class="headerRow">
<div class="headerCol items-center"><button data-testid="calendar-filters-toggle" class="mr-6" type="button" id="ariaToggle" aria-label="Show staff filters"><svg viewBox="0 0 32 32" width="32" height="32"><path d="M8 11h16M8 16h16M8 21h16" stroke="currentColor" stroke-width="2" fill="none"/></svg></button>
<div data-testid="calendar-grid-tabs" class="relative ariaTabs"><div class="cursor-pointer"><div data-testid="select-dropdown-calendar-date-picker"><input class="b-form-control" value="Day" readonly aria-label="View"></div></div></div>
<button data-testid="today-button" class="_button_11aed_7 ariaBtn" type="button" data-href="${base}?date=today&view=day&staffers=working"><div class="_caption_11aed_19">Today</div></button></div>
<div data-testid="calendar-date-switcher" class="dateSelect"><button data-testid="date-switcher-previous" class="_button_11aed_7 ariaNav" type="button" data-href="${base}?date=${prevDay}&view=day&staffers=working" aria-label="Previous day">&#8249;</button>
<div data-testid="date-switcher-label" class="_name_1tz3u_33 border"><div class="_date_1tz3u_7 flex-1"><div class="_month_1tz3u_20">${esc(`${weekday}, ${day.day} ${MONTH_SHORT[day.month - 1]}`)}</div><div class="_hours_1tz3u_55">10:00 AM - 8:00 PM</div></div></div>
<button data-testid="date-switcher-next" class="_button_11aed_7 ariaNav" type="button" data-href="${base}?date=${nextDay}&view=day&staffers=working" aria-label="Next day">&#8250;</button></div>
<div class="headerCol headerColSecondary items-center"><button data-testid="settings-icon" class="ariaIcon" type="button" aria-label="Settings"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></svg></button><button data-testid="referral-icon" class="ariaIcon ml-6" type="button" aria-label="Referral"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="9" width="16" height="11" rx="1"/><path d="M3 6h18v3H3zM12 6v14"/></svg></button><button data-testid="notifications-icon" class="ariaIcon" type="button" aria-label="Notifications"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15zM10 21h4"/></svg></button><div class="_searchInput_1nsw7_81 ariaSearch"><input class="b-form-control" placeholder="Search" aria-label="Search" disabled></div></div>
</div></header>
<div class="ariaBody"><div class="ariaDrawer" id="ariaDrawer">${filterPanel(dataset, shownIds)}</div>
<div data-testid="calendar-day-view" class="bg-primary _calendar_1pknx_7 _calendarIsDay_1pknx_16">${notice}
<div class="ariaCorner"></div><div class="ariaHeadStrip" id="ariaHeadStrip"><div class="ariaHeadRow" style="width:${width}px">${headers}</div></div>
<div class="ariaAxis"><div class="ariaAxisInner" id="ariaAxis">${labels.join("")}</div></div>
<div data-testid="calendar-grid-day" class="_calendarContent_1pknx_70 _calendarContentScroll_1pknx_76 scrollable-xy" id="ariaGrid">
<div class="_calendarWrapper_1pknx_36" style="width:${width}px;height:${Math.floor(((GRID_END - GRID_START) * PX_PER_HOUR) / 60)}px">
<div class="_calendarGrid_1pknx_83 _calendarGrid--overlay_1pknx_93" style="width:${width}px">${columns}</div>
<div class="_calendarGrid_1pknx_83 _calendarGrid--layout_1pknx_105" style="width:${width}px">${layout}</div></div></div></div></div></div></div></main></div>
<script>${page.js}</script></body></html>`;
}
