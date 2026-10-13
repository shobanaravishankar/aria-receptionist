"""The demo calendar PAGE: a Booksy-style day view drawn from the dataset.

What is faithful, and what is NOT. GEOMETRY and STRUCTURE follow a redacted capture of the real day view at a 1600 x 789 viewport: a 56 px icon
sidebar, a 75 px toolbar (filter toggle, date picker, Today, previous / date label / next, settings, referral, bell, search), the staff-filter
drawer, 250 px staff columns under 64 px staff headers, a 74 px time axis, 80 px per hour from 9:00 AM to 9:00 PM, 3 px card inset, card height
(minutes x 4/3 - 1), shaded non-working blocks, and the same test ids / class names the real reader consumes, so the REAL capture script and
parser read this page exactly as they read the real one (a test proves it equals the dataset).
COLORS, FONTS, ICONS and BORDERS are NOT known: the capture holds none of them and no reference screenshot has been supplied. They are neutral,
clearly approximate choices. Nothing here claims a visual match to Booksy's styling.

A persistent banner says this is a DEMO. The page has no sign-in form, no credentials, no customer contact details and no notes.
"""

from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Optional

from . import DEMO_BUSINESS_ID, DEMO_LABEL
from .dataset import GRID_END, GRID_START, DayPlan, DemoDataset, NotCovered, StaffPlan, hm

PX_PER_HOUR = 80
COLUMN_WIDTH = 250
CATEGORY_COLORS = {  # approximate and arbitrary: borders / tints per service category
    "massage": ("#7a5bbd", "#f1ecfa"), "facial": ("#2c9a8b", "#e6f5f2"), "lash": ("#d0507f", "#fbe8f0"), "waxing": ("#d98a1f", "#fcf0de"),
    "head-spa": ("#3a7fc1", "#e7f0fa"), "body-scrub": ("#8a6d3b", "#f4eee2"),
}
ICONS = {
    "calendar-main": '<rect x="5" y="7" width="18" height="16" rx="2"/><path d="M5 12h18M10 5v4M18 5v4"/>',
    "sales": '<path d="M5 8h3l2 10h10l2-7H9"/><circle cx="12" cy="22" r="1"/><circle cx="19" cy="22" r="1"/>',
    "customers-main": '<circle cx="11" cy="11" r="3.5"/><path d="M4 22c0-4 3-6 7-6s7 2 7 6"/><path d="M19 9a3 3 0 0 1 0 6M22 22c0-3-1.5-4.5-3-5"/>',
    "inventory": '<path d="M5 10l9-5 9 5v10l-9 5-9-5z"/><path d="M5 10l9 5 9-5M14 15v10"/>',
    "promotion": '<path d="M6 16v-4l12-5v14L6 16z"/><path d="M9 16l1 6h3l-1-5"/>',
    "gifts-and-bundle-services": '<rect x="5" y="11" width="18" height="12" rx="1"/><path d="M4 8h20v3H4zM14 8v15M14 8c-4 0-6-4-3-4s3 4 3 4zm0 0c4 0 6-4 3-4s-3 4-3 4z"/>',
    "stats-and-reports": '<path d="M6 22V12M12 22V6M18 22v-8M4 22h20"/>',
    "staff": '<circle cx="14" cy="10" r="4"/><path d="M6 23c0-5 3.5-7.5 8-7.5s8 2.5 8 7.5"/>',
    "side-menu-settings.subscription": '<rect x="4" y="8" width="20" height="13" rx="2"/><path d="M4 12h20M8 17h5"/>',
    "booksy-wallet": '<rect x="4" y="8" width="20" height="14" rx="2"/><path d="M4 12h20"/><circle cx="19" cy="17" r="1.5"/>',
    "profile": '<path d="M5 12l9-7 9 7v11H5z"/><path d="M11 23v-7h6v7"/>',
    "settings": '<circle cx="14" cy="14" r="4"/><path d="M14 4v3M14 21v3M4 14h3M21 14h3M7 7l2 2M19 19l2 2M21 7l-2 2M9 19l-2 2"/>',
    "help": '<circle cx="14" cy="14" r="9"/><path d="M11 11a3 3 0 1 1 4 2.8c-.8.4-1 1-1 2M14 20h.01"/>',
}
SIDE_MENU = (
    ("calendar-main", True), ("sales", False), ("customers-main", False), ("inventory", False), ("promotion", False),
    ("gifts-and-bundle-services", False), ("stats-and-reports", False), ("staff", False), ("side-menu-settings.subscription", False),
    ("booksy-wallet", False),
)


def _y(minute: int) -> int:
    return round((minute - GRID_START) * PX_PER_HOUR / 60)


def _clock12(minute: int) -> str:
    return hm(minute)


def _hour_label(hour: int) -> str:
    return f"{(hour % 12) or 12} {'am' if hour < 12 else 'pm'}"


def _initials(name: str) -> str:
    return "".join(part[0] for part in name.split())[:2].upper()


def _tone(name: str) -> str:
    return ("#8c6bb1", "#4f9d8f", "#c97b9b", "#5b88b8", "#b88a4a", "#7a8f5a")[sum(map(ord, name)) % 6]


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _filter_panel(dataset: DemoDataset, shown_ids: set[str]) -> str:
    rows = [
        ('filtersValue-input', 'filtersValue', "Only me", False),
        ('filtersValue-input', 'filtersValue', "Working Staff Members", True),
        ('filtersValue_all-input', 'filtersValue_all', "Select All", False),
    ]
    rows += [(f"filtersValue_{s.staff_id}-input", f"filtersValue_{s.staff_id}", s.name, s.staff_id in shown_ids) for s in dataset.roster]
    items = []
    for index, (box_testid, label_testid, name, checked) in enumerate(rows):
        box_id = f"ariaFilter{index}"
        staff = index >= 3
        avatar = f'<span class="ariaMini" style="background:{_tone(name)}">{_esc(_initials(name))}</span>' if staff else ""
        items.append(
            f'<li class="ariaFilterRow"><input type="checkbox" id="{box_id}" data-testid="{_esc(box_testid)}"{" checked" if checked else ""}>{avatar}'
            f'<label for="{box_id}" data-testid="{_esc(label_testid)}" class="_resourceItem_xftsu_44 flex items-center">{_esc(name)}</label></li>'
        )
    return (
        '<div data-testid="resources-filter-by-staffers" class="ariaFilterBox"><div class="ariaFilterTitle">Staff</div><ul>' + "".join(items) + "</ul></div>"
        '<div class="sticky bottom-0 bg-primary ariaDrawerFoot"><button data-testid="calendar-status-filter-clear" class="ariaBtn" type="button">Clear</button>'
        '<button data-testid="calendar-status-filter-submit" class="ariaBtn ariaBtnPrimary" type="button">Apply</button></div>'
    )


def _card(plan: StaffPlan, visit, ordinal: int) -> str:
    border, tint = CATEGORY_COLORS.get(visit.category, ("#6b7280", "#f3f4f6"))
    top, height = _y(visit.start), round((visit.end - visit.start) * PX_PER_HOUR / 60) - 1
    size = "sizeL_mf7bh_241" if height >= 99 else "sizeM_mf7bh_218" if height >= 59 else "sizeS_mf7bh_195"
    tid = f"calendar-content-calendar-grid-calendar-card-{plan.staff.staff_id}-{ordinal}"
    return (
        f'<div class="_cardWrap_mf7bh_10" data-testid="{tid}" data-appointment-id="x" style="top:{top}px;height:{height}px">'
        f'<div class="_card_mf7bh_10 _padding-right-0_mf7bh_174 _{size}" style="--cat:{border};--tint:{tint}">'
        f'<div class="_cardBorder_mf7bh_65"></div>'
        f'<p class="_header_mf7bh_73 _text_mf7bh_57"><span class="hour_from">{_clock12(visit.start)}</span> - <span class="hour_till">{_clock12(visit.end)}</span></p>'
        f'<p class="_details_mf7bh_53"><span class="_text_mf7bh_57 ariaClient">{_esc(visit.client)}</span>'
        f'<span class="_text_mf7bh_57 ariaService">{_esc(visit.service)}</span></p></div></div>'
    )


def _off_block(plan: StaffPlan, start: int, end: int, label: str, ordinal: int) -> str:
    tid = f"calendar-content-calendar-grid-calendar-card-{plan.staff.staff_id}-{ordinal}"
    top, height = _y(start), round((end - start) * PX_PER_HOUR / 60) - 1
    return (
        f'<div class="_cardWrap_mf7bh_10" data-testid="{tid}" style="top:{top}px;height:{height}px">'
        f'<div data-testid="non-working-event" class="_card_mf7bh_10 _off_mf7bh_14 _isNonworking_mf7bh_118" data-label="{_esc(label)}"></div></div>'
    )


def _column(plan: StaffPlan) -> str:
    cards, ordinal = [], 0
    window = plan.hours if plan.hours else (GRID_START, GRID_END)
    # outside the working window the grid is shaded as non-working (the real calendar does the same); a person with no shown hours is shaded nowhere
    if plan.shown_hours is not None:
        if window[0] > GRID_START:
            cards.append(_off_block(plan, GRID_START, window[0], "", ordinal)); ordinal += 1
        if window[1] < GRID_END:
            cards.append(_off_block(plan, window[1], GRID_END, "", ordinal)); ordinal += 1
    for b in plan.breaks:
        cards.append(_off_block(plan, b.start, b.end, b.label, ordinal)); ordinal += 1
    for v in plan.visits:
        cards.append(_card(plan, v, ordinal)); ordinal += 1
    return f'<div class="_calendarColumn_1pknx_16" data-resource="{_esc(plan.staff.staff_id)}">' + "".join(cards) + "</div>"


def _header(plan: StaffPlan) -> str:
    hours = f'<span class="_hours_10ie7_48">{_clock12(plan.hours[0])}-{_clock12(plan.hours[1])}</span>' if plan.shown_hours else ""
    return (
        '<div data-testid="resource" class="_resource_na70m_7 _resource_10ie7_7 _resourceFixedSize_10ie7_31">'
        f'<span class="ariaAvatar" style="background:{_tone(plan.staff.name)}">{_esc(_initials(plan.staff.name))}</span>'
        '<div data-testid="resource-content" class="_resourceWrapper_10ie7_16 flex"><div class="flex-1">'
        f'<span class="_name_10ie7_43 w-full">{_esc(plan.staff.name)}</span><br>{hours}</div></div></div>'
    )


def _side_menu() -> str:
    items = "".join(
        f'<li><div data-testid="{tid}" class="iconWrapper{" active" if active else ""}"><svg class="b-svg icon" viewBox="0 0 28 28" width="28" height="28" aria-hidden="true" '
        f'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[tid]}</svg></div></li>'
        for tid, active in SIDE_MENU
    )
    lower = "".join(
        f'<li><div data-testid="{tid}" class="iconWrapper"><svg class="b-svg icon" viewBox="0 0 28 28" width="28" height="28" aria-hidden="true" '
        f'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[tid]}</svg></div></li>'
        for tid in ("profile", "settings")
    )
    items += (
        '<li class="ariaSep"><hr class="separator"></li>' + lower
        + f'<li><div class="iconWrapper"><button type="button" aria-label="Help"><svg class="b-svg icon" viewBox="0 0 28 28" width="28" height="28" fill="none" '
        f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS["help"]}</svg></button></div></li>'
    )
    return (
        '<aside data-testid="side-menu" class="bar"><div class="barContent"><div data-testid="side-menu-select-language" class="language"></div><hr class="separator">'
        f'<ul class="list">{items}</ul><div class="avatar"><div data-testid="side-menu-avatar" class="avatarBox"><div class="avatarInitials">D</div></div></div></div></aside>'
    )


def render_page(dataset: DemoDataset, day: date, *, base_path: str = "") -> str:
    """The full HTML document for one day. A date outside the dataset's coverage shows a clear 'no demo data' state and NO columns."""
    try:
        plan: Optional[DayPlan] = dataset.day_plan(day)
    except NotCovered:
        plan = None
    plans = plan.plans if plan else ()
    shown_ids = {p.staff.staff_id for p in plans}
    columns = "".join(_column(p) for p in plans)
    headers = "".join(_header(p) for p in plans)
    width = max(len(plans), 1) * COLUMN_WIDTH
    layout = "".join('<div class="_calendarColumn_1pknx_16"></div>' for _ in plans)
    labels = "".join(
        f'<span class="_hourText_ultg9_16" style="top:{_y(h * 60) - 9}px">{_hour_label(h)}</span>' for h in range(GRID_START // 60 + 1, GRID_END // 60)
    )
    prev_day, next_day = (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()
    base = f"{base_path}/pro/en-us/{DEMO_BUSINESS_ID}/calendar"
    notice = "" if plan else (
        f'<div class="ariaNoData" data-testid="demo-no-data">The demo calendar has no data for {_esc(day.isoformat())}. '
        f'It covers {_esc(dataset.first_day.isoformat())} to {_esc(dataset.last_day.isoformat())}.</div>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo calendar - simulated availability</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head>
<body data-source="demo_calendar" data-demo-date="{_esc(day.isoformat())}">
<div id="demo-banner" data-testid="demo-banner"><strong>{_esc(DEMO_LABEL)}</strong><span>All appointments, clients and availability on this page are invented for a demo. This is not Booksy and not the salon's real schedule.</span></div>
<div class="w-full h-full" id="root"><main data-testid="main" class="flex flex-col h-full"><div data-testid="calendar-view" class="flex w-full h-full">
{_side_menu()}
<div data-testid="content" class="content"><div class="calendarView">
<header class="bg-primary header"><div class="headerRow">
<div class="headerCol items-center"><button data-testid="calendar-filters-toggle" class="mr-6" type="button" id="ariaToggle" aria-label="Show staff filters"><svg viewBox="0 0 32 32" width="32" height="32"><path d="M8 11h16M8 16h16M8 21h16" stroke="currentColor" stroke-width="2" fill="none"/></svg></button>
<div data-testid="calendar-grid-tabs" class="relative ariaTabs"><div class="cursor-pointer"><div data-testid="select-dropdown-calendar-date-picker"><input class="b-form-control" value="Day" readonly aria-label="View"></div></div></div>
<button data-testid="today-button" class="_button_11aed_7 ariaBtn" type="button" data-href="{base}?date=today&view=day&staffers=working"><div class="_caption_11aed_19">Today</div></button></div>
<div data-testid="calendar-date-switcher" class="dateSelect"><button data-testid="date-switcher-previous" class="_button_11aed_7 ariaNav" type="button" data-href="{base}?date={prev_day}&view=day&staffers=working" aria-label="Previous day">&#8249;</button>
<div data-testid="date-switcher-label" class="_name_1tz3u_33 border"><div class="_date_1tz3u_7 flex-1"><div class="_month_1tz3u_20">{_esc(f"{day.strftime('%a')}, {day.day} {day.strftime('%b')}")}</div><div class="_hours_1tz3u_55">10:00 AM - 8:00 PM</div></div></div>
<button data-testid="date-switcher-next" class="_button_11aed_7 ariaNav" type="button" data-href="{base}?date={next_day}&view=day&staffers=working" aria-label="Next day">&#8250;</button></div>
<div class="headerCol headerColSecondary items-center"><button data-testid="settings-icon" class="ariaIcon" type="button" aria-label="Settings"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></svg></button><button data-testid="referral-icon" class="ariaIcon ml-6" type="button" aria-label="Referral"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="9" width="16" height="11" rx="1"/><path d="M3 6h18v3H3zM12 6v14"/></svg></button><button data-testid="notifications-icon" class="ariaIcon" type="button" aria-label="Notifications"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15zM10 21h4"/></svg></button><div class="_searchInput_1nsw7_81 ariaSearch"><input class="b-form-control" placeholder="Search" aria-label="Search" disabled></div></div>
</div></header>
<div class="ariaBody"><div class="ariaDrawer" id="ariaDrawer">{_filter_panel(dataset, shown_ids)}</div>
<div data-testid="calendar-day-view" class="bg-primary _calendar_1pknx_7 _calendarIsDay_1pknx_16">{notice}
<div class="ariaCorner"></div><div class="ariaHeadStrip" id="ariaHeadStrip"><div class="ariaHeadRow" style="width:{width}px">{headers}</div></div>
<div class="ariaAxis"><div class="ariaAxisInner" id="ariaAxis">{labels}</div></div>
<div data-testid="calendar-grid-day" class="_calendarContent_1pknx_70 _calendarContentScroll_1pknx_76 scrollable-xy" id="ariaGrid">
<div class="_calendarWrapper_1pknx_36" style="width:{width}px;height:{(GRID_END - GRID_START) * PX_PER_HOUR // 60}px">
<div class="_calendarGrid_1pknx_83 _calendarGrid--overlay_1pknx_93" style="width:{width}px">{columns}</div>
<div class="_calendarGrid_1pknx_83 _calendarGrid--layout_1pknx_105" style="width:{width}px">{layout}</div></div></div></div></div></div></div></main></div>
<script>{JS}</script></body></html>"""


CSS = """
:root{--line:#e4e7ec;--ink:#1f2937;--muted:#6b7280;--accent:#6d4aa8;--side:#1d2230}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{display:flex;flex-direction:column;font:13px/1.3 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:#fff;overflow:hidden}
#demo-banner{flex:0 0 28px;height:28px;display:flex;align-items:center;gap:14px;padding:0 16px;background:#fde68a;color:#78350f;border-bottom:1px solid #f59e0b;font-size:12px;white-space:nowrap;overflow:hidden}
#demo-banner strong{font-size:13px;letter-spacing:.2px}
#root{flex:1 1 auto;min-height:0}.w-full{width:100%}.h-full{height:100%}.flex{display:flex}.flex-col{flex-direction:column}.flex-1{flex:1}.items-center{align-items:center}.relative{position:relative}
main{height:100%}[data-testid=calendar-view]{height:100%}
.bar{flex:0 0 56px;width:56px;height:100%;background:var(--side);color:#cfd5e3}.barContent{height:100%;position:relative}
.language{position:absolute;left:16px;top:16px;width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,#6b7280,#9ca3af)}
.separator{position:absolute;left:14px;top:56px;width:28px;margin:0;border:0;border-top:1px solid #3a4157}
.list{list-style:none;margin:0;padding:0;position:absolute;top:65px;bottom:0;left:0;width:56px;overflow-y:auto;scrollbar-width:none}.list::-webkit-scrollbar{display:none}.list li{height:52px;width:50px;padding-top:8px}
.iconWrapper{width:50px;height:36px;display:flex;align-items:center;justify-content:center;color:#aab2c5}.iconWrapper.active{color:#fff;background:#2c3550;border-left:3px solid #a78bfa}
.barContent>.avatar{position:absolute;left:10px;bottom:16px;width:36px;height:36px}.avatarBox{width:36px;height:36px}.avatarInitials{width:36px;height:36px;border-radius:50%;background:#6d4aa8;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600}
.content{flex:1 1 auto;min-width:0;height:100%}.calendarView{height:100%;display:flex;flex-direction:column}
.header{flex:0 0 75px;height:75px;background:#fff;border-bottom:1px solid var(--line);padding:12px 16px;position:relative}
.headerRow{position:relative;height:50px;display:flex;align-items:center;justify-content:space-between}
.headerCol{display:flex;align-items:center}.headerColSecondary{gap:24px}.mr-6{margin-right:24px}.ml-6{margin-left:0}
#ariaToggle{width:32px;height:32px;border:0;background:none;color:var(--ink);padding:0;cursor:pointer}
.ariaTabs{width:180px;height:56px;display:flex;align-items:center;align-self:flex-start;margin-top:-6px}.ariaTabs input{width:176px;height:40px;border:1px solid var(--line);border-radius:6px;padding:0 12px;font:inherit}
.ariaBtn{height:48px;min-width:101px;margin-left:16px;border:1px solid var(--line);background:#fff;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}
.ariaBtnPrimary{background:var(--accent);color:#fff;border-color:var(--accent)}
.dateSelect{position:absolute;left:50%;top:1px;width:274px;margin-left:-137px;display:flex;gap:4px;align-items:center}.ariaNav:last-child{margin-left:1px}
.ariaNav{width:48px;height:48px;border:1px solid var(--line);background:#fff;border-radius:8px;font-size:22px;cursor:pointer;margin:0}
[data-testid=date-switcher-label]{width:169px;height:46px;border:1px solid var(--line);border-radius:8px;display:flex;align-items:center;padding:0 12px}
._date_1tz3u_7{padding-left:12px}._month_1tz3u_20{font-weight:600;font-size:14px;line-height:22px;width:104px}._hours_1tz3u_55{font-size:12px;color:var(--muted);line-height:18px;width:104px;white-space:nowrap}
.ariaIcon.ml-6{width:40px;height:40px}.ariaIcon{width:32px;height:32px;border:1px solid var(--line);border-radius:50%;background:#fff;color:#4b5563;display:inline-flex;align-items:center;justify-content:center;padding:0}.ariaSearch{margin-left:-8px}.ariaSearch input{width:140px;height:48px;border:1px solid var(--line);border-radius:8px;padding:0 12px;font:inherit}
.ariaBody{flex:1 1 auto;min-height:0;display:flex}
.ariaDrawer{flex:0 0 217px;width:217px;border-right:1px solid var(--line);padding:12px 12px 0;position:relative;overflow:hidden;background:#fff}
.ariaDrawer.closed{display:none}.ariaFilterTitle{font-weight:600;margin:0 0 8px}.ariaFilterBox ul{list-style:none;margin:0;padding:0}
.list li.ariaSep{height:17px;padding:0}.iconWrapper button{background:none;border:0;color:inherit;padding:0}.ariaFilterRow{display:flex;align-items:center;gap:8px;height:30px}.ariaFilterRow label{display:flex;align-items:center;cursor:pointer}.ariaMini{width:16px;height:16px;border-radius:50%;color:#fff;font-size:8px;display:inline-flex;align-items:center;justify-content:center}
.ariaDrawerFoot{position:absolute;left:12px;right:18px;bottom:0;display:flex;flex-direction:column;gap:8px;padding:8px 0 7px;background:#fff}.ariaDrawerFoot .ariaBtn{margin:0;width:100%;height:34px;min-width:0}
[data-testid=calendar-day-view]{flex:1 1 auto;min-width:0;position:relative;display:grid;grid-template-columns:74px 1fr;grid-template-rows:64px 1fr;background:#fff}
.ariaCorner{border-bottom:1px solid var(--line)}.ariaHeadStrip{overflow:hidden;border-bottom:1px solid var(--line)}.ariaHeadRow{display:flex;height:64px}
._resource_10ie7_7{flex:0 0 250px;width:250px;height:64px;display:flex;align-items:center;gap:0;padding-left:10px;box-shadow:inset 1px 0 0 var(--line)}
.ariaAvatar{flex:0 0 40px;width:40px;height:40px;border-radius:50%;color:#fff;font-weight:600;display:flex;align-items:center;justify-content:center;margin-right:0}
._resourceWrapper_10ie7_16{margin-left:0;padding-left:0;width:170px;height:40px;align-items:center;margin-left:0}._resourceWrapper_10ie7_16 .flex-1{padding-left:12px}
._name_10ie7_43{font-weight:600;font-size:14px;line-height:20px}._hours_10ie7_48{font-size:12px;color:var(--muted);line-height:18px}
.ariaAxis{overflow:hidden;position:relative;border-right:1px solid var(--line)}.ariaAxisInner{position:relative;width:74px;height:960px}
._hourText_ultg9_16{position:absolute;left:0;width:74px;height:18px;line-height:18px;text-align:right;padding-right:10px;color:var(--muted);font-size:12px}
._calendarContent_1pknx_70{position:relative;overflow:auto;min-width:0;min-height:0}
._calendarWrapper_1pknx_36{position:relative}._calendarGrid_1pknx_83{position:absolute;left:0;top:0;height:960px;display:flex}
._calendarGrid--layout_1pknx_105{pointer-events:none}._calendarGrid--layout_1pknx_105 ._calendarColumn_1pknx_16{background-image:repeating-linear-gradient(to bottom,transparent 0,transparent 79px,var(--line) 79px,var(--line) 80px);border-left:1px solid var(--line)}
._calendarColumn_1pknx_16{position:relative;flex:0 0 250px;width:250px;height:960px}
._calendarGrid--overlay_1pknx_93{z-index:2}._calendarGrid--overlay_1pknx_93 ._calendarColumn_1pknx_16{background:none}
._cardWrap_mf7bh_10{position:absolute;left:3px;width:244px;margin:0}
._card_mf7bh_10{position:relative;height:100%;width:100%;overflow:hidden;border-radius:6px;background:var(--tint,#f3f4f6);border:1px solid color-mix(in srgb,var(--cat,#6b7280) 25%,#fff);padding:3px 8px 0 8px}
._cardBorder_mf7bh_65{position:absolute;left:0;top:1px;bottom:1px;width:3px;background:var(--cat,#6b7280);border-radius:2px}
._header_mf7bh_73{margin:0;font-weight:600;font-size:12px;line-height:18px;white-space:nowrap}._details_mf7bh_53{margin:0;font-size:12px;line-height:16px}
._details_mf7bh_53 ._text_mf7bh_57{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.ariaService{color:var(--muted)}
._sizeS_mf7bh_195 ._details_mf7bh_53,._sizeM_mf7bh_218 .ariaService{display:none}._sizeM_mf7bh_218 ._details_mf7bh_53{height:16px}
._sizeS_mf7bh_195{padding-top:0}._sizeS_mf7bh_195 ._header_mf7bh_73{line-height:inherit}
._off_mf7bh_14{background:repeating-linear-gradient(135deg,#f3f4f6 0,#f3f4f6 6px,#eceef2 6px,#eceef2 12px);border:0;border-radius:0;padding:0}
._off_mf7bh_14::after{content:attr(data-label);position:absolute;left:8px;top:4px;color:#8b92a1;font-size:11px}
.ariaNoData{position:absolute;inset:64px 0 0 74px;z-index:5;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.92);color:var(--muted);font-size:15px;text-align:center;padding:24px}
"""

JS = """
(function(){
  var grid=document.getElementById('ariaGrid'),head=document.getElementById('ariaHeadStrip'),axis=document.getElementById('ariaAxis');
  if(grid){grid.addEventListener('scroll',function(){head.scrollLeft=grid.scrollLeft;axis.style.transform='translateY('+(-grid.scrollTop)+'px)';});}
  var toggle=document.getElementById('ariaToggle'),drawer=document.getElementById('ariaDrawer');
  if(toggle&&drawer){toggle.addEventListener('click',function(){drawer.classList.toggle('closed');});}
  document.querySelectorAll('[data-href]').forEach(function(b){b.addEventListener('click',function(){location.href=b.getAttribute('data-href');});});
})();
"""
