# Aria booking prototype (Booksy Biz test account, Selenium)

An isolated prototype of the booking half of Aria, the salon receptionist. It is **not** imported by the
website and changes nothing about the voice/avatar demo. It targets **one dedicated Booksy Biz test
account** and nothing else.

## Status — read this first

| capability | status |
|---|---|
| Find slots that fit the service, buffers, working hours, time off, existing appointments, lead time | implemented, unit-tested against an in-memory calendar |
| Unknown availability is never treated as free | implemented, tested |
| Reject a booking that would run past closing | implemented, tested (rule only; also exercised on the real captured pages) |
| Duplicate / retry safety: ledger, read-back verification, reconcile after an uncertain save | implemented, tested against a fake calendar that simulates the failures |
| Safety guards (right business, test marker, future slot, run limit) | implemented, tested |
| Detect a concurrent booking after the fact | implemented, tested |
| Browser launch, wait for a **person** to sign in, verify the signed-in business | **run live** (a person signed in using an ordinary Chrome window; the automated browser reused the session) |
| Structural discovery, including approval-gated click-through steps | **run live**, read-only; nothing was ever saved |
| Dress rehearsal of the New Appointment form (everything except Save) | **run live** for one slot; every value read back matched; the draft was discarded; the page ended identical to how it began |
| **Calendar reader** (parse a day: working hours, appointments, time off) | implemented; tested on **real captured pages** and 19 deliberate corruptions; **run live, read-only**: 7 consecutive days parsed as predicted (the busy day showed no slots, the six empty days showed starts 10:00-16:30), no day came back unknown |
| Create an appointment (the Save click and what follows) | **Written and tested offline only; NEVER RUN against the real site.** The form up to Save is proven live (rehearsal). The prompt after Save and the saved state have never been seen, so the code handles only NOT NOW and stops, leaving the window open, on anything else |
| Read a saved appointment's internal note back for verification | **Written and tested offline only; NEVER RUN live** (the note is under the details view's Notes & Info tab; the live selectors for the details view's note are a best guess until one real booking has been read back) |
| Reschedule, cancel, expired-sign-in recovery against the real site | **NOT implemented / NOT run** |

**No real appointment has ever been created by this prototype.** The tests prove the logic and the parser's
behaviour on captured pages; they do not prove that a live Save behaves as assumed.

### The calendar reader's rules (`calendar_parser.py`)

It is a pure function over the page's structure, so it is tested offline against sanitized real captures
(`tests/fixtures`, see its README). It refuses rather than guesses:

- the page must be the day requested (the label has a weekday and date but no year, so the weekday is checked too);
- the page must be quiet: no loading overlay, form, drawer or dialog;
- every card must be understood. An unrecognised card makes **time off unknown**; an appointment whose times
  cannot be read, or whose text disagrees with its position on the hour axis, makes **appointments unknown**;
- working hours = the visible grid minus non-working blocks, limited to the day's displayed hours;
- **staff identity is not shown on the page.** The reader therefore first opens the account's Staff page (a read-only
  view) and accepts a day only if that list contains **exactly one** staff member whose first name is the configured one
  *and* the calendar has exactly one column. Two staff, none, or another name: it refuses. The result is remembered for
  the session. (Live, the Staff page listed exactly one member.)

## What the rules guarantee — and what they cannot

- **Unknown is not free.** If working hours, time off or existing appointments cannot be read, the day is
  reported as unknown and no slot is offered.
- **No overlaps.** A slot must sit fully inside a known working interval (buffers included), clear of time
  off and of every appointment that blocks time. Touching is allowed. A booking that would end after
  closing is declined.
- **No duplicates.** Intent is written to a local ledger *before* saving. After a save, the appointment is
  read back by a unique reference in its internal note and compared (start, duration, service, staff).
  If the save outcome is uncertain, the next attempt checks the calendar first; it only creates again when
  the calendar is readable and proves the appointment is absent (after a settle delay). Otherwise it stops
  and says so.
- **Not atomic.** A browser cannot lock the calendar. A colleague could add an appointment between our final
  re-check and the Save click. We narrow that window and *detect* it afterwards
  (`booked_conflict_detected`), but we do not claim to prevent it and we never delete anything automatically.
- Booksy's own staff UI may allow overlaps; this tool declines them regardless.

## Safety rules (enforced in code before any booking)

- Signed-in business must equal the configured test business (`ARIA_BOOKSY_BUSINESS_ID`). The id is never
  committed; this repository is public.
- Staff, service and duration must match the configured test values (defaults: staff *Shobs*, service
  *Aria Salon*, 150 minutes).
- The slot must be at least `ARIA_MIN_LEAD_MINUTES` (default 60) in the future.
- The internal note must carry the **ARIA TEST** marker ("fictional appointment, no real customer or
  payment, safe to cancel after testing") plus a reference. No client contact details are ever entered.
- At most one booking per run (`ARIA_MAX_BOOKINGS_PER_RUN`).
- `book` is refused unless `ARIA_LIVE_BOOKSY=1` **and** `--confirm-business-id` equals the configured id —
  checked before any browser is created.
- Out of scope and never done: customer messages, payments, subscription/visibility changes, any other
  business (including any real salon account), deployment.

## Setup

```bash
cd C:\Users\shoba\code\glamour-dayspa-demo
python -m venv .venv
.venv\Scripts\python -m pip install -r booking\requirements.txt
```

## Run the tests (no browser, no network, no Booksy)

```bash
cd booking
..\.venv\Scripts\python -m pytest
```

The live tests in `tests/live/` are excluded by default and skip cleanly without configuration.

## Configuration (environment variables; none are secrets)

| variable | meaning |
|---|---|
| `ARIA_BOOKSY_BUSINESS_ID` | numeric id of the **test** business (required for any live command) |
| `ARIA_LIVE_BOOKSY` | must be `1` to allow `book` and live tests |
| `ARIA_CHROMEDRIVER_PATH` | path to an existing chromedriver |
| `ARIA_ALLOW_DRIVER_DOWNLOAD` | `1` lets Selenium download a matching driver (a file download; off by default) |
| `ARIA_STAFF_NAME`, `ARIA_SERVICE_NAME`, `ARIA_SERVICE_MINUTES` | test staff/service (defaults above) |
| `ARIA_BUFFER_BEFORE_MINUTES`, `ARIA_BUFFER_AFTER_MINUTES` | padding around the service (default 0) |
| `ARIA_GRID_MINUTES`, `ARIA_MIN_LEAD_MINUTES`, `ARIA_MAX_BOOKINGS_PER_RUN`, `ARIA_SETTLE_SECONDS` | tuning |
| `ARIA_TIMEZONE` | calendar timezone (default `America/New_York`) |
| `ARIA_LOCAL_DIR` | where the browser profile, ledger and evidence live (default `booking/.local`) |

## Commands

Run these from the `booking` directory with the project's virtual environment (`..\.venv\Scripts\python`).

```bash
python -m aria_booking config            # effective settings (business id masked)
python -m aria_booking login             # opens the test browser; a PERSON signs in; verifies the business
python -m aria_booking discover          # writes a REDACTED page-structure report for review
python -m aria_booking discover-steps --allow <steps>
                                         # click-through discovery; every step type must be named (tour, appointment,
                                         # notes-tab, future-date, new-form, form-explore); nothing is typed or saved
python -m aria_booking rehearse --start "2026-10-12 11:00" --confirm-business-id <id> \
        --approve-note-typing --approve-draft-discard --approve-tour-popups
                                         # fills the New Appointment form completely, NEVER saves, discards the draft
python -m aria_booking find              # free slots; unknown days are reported as unknown
python -m aria_booking book --start "2026-10-20 10:00" --confirm-business-id <id>         --approve-save --approve-note-typing --approve-tour-popups --approve-not-now --approve-note-readback
                                         # creates ONE real fictional (ARIA TEST) appointment. Needs ARIA_LIVE_BOOKSY=1,
                                         # all five approvals, and a person watching. Not yet run live.
```

`discover-steps`, `rehearse` and `book` are refused unless every required approval is given explicitly, before any
browser exists. Every click goes through a guard that refuses anything that looks like an action (save, confirm,
delete, cancel appointment, checkout, pay, send, submit, discard, create, book, yes); the rehearsal's single
Discard click is a narrow, separately approved exception for an unsaved draft.

## Sign-in, credentials and evidence

- A **person** signs in to Booksy Biz in the dedicated browser window. This tool never types or stores a
  password, and nobody should paste one into chat or a file.
- The browser keeps its own session in `booking/.local/chrome-profile`. Everything under `booking/.local/`
  (profile, ledger, evidence) is **git-ignored** and must never be committed or shared.
- `discover` redacts text character-by-character before writing (times, small numbers and known UI words are
  kept; names, phone numbers and emails are masked), records attribute *names* only, and masks the business
  id in URLs. Redaction is best-effort; review a report before sharing it.
- A browser driver download is a file download from the internet. It happens only if you set
  `ARIA_ALLOW_DRIVER_DOWNLOAD=1` or provide a driver path.

## What happens next

1. ~~Run the reader live and read-only~~ **done**: it parsed seven live days as predicted.
2. ~~Verify the staff identity properly~~ **done**: the reader now checks the Staff page itself.
3. ~~Implement creation~~ **written and tested offline**. Run it once for a named, explicitly approved slot with a person
   watching; it stops at anything unexpected and verifies by reopening the card and reading the note back. Until that run,
   treat the post-Save handling and the note read-back as unproven.
4. Later scenarios — near-closing rejection, reschedule, cancellation, expired sign-in — are **not** considered passed
   until actually run.

## Layout

```
booking/
  aria_booking/
    models.py          data types, DST-safe time arithmetic
    config.py          environment configuration (no secrets)
    availability.py    pure slot-finding and validation rules
    safety.py          pre-booking guards and the ARIA TEST note
    ledger.py          local intent/outcome ledger with an exclusive lock
    booking_service.py check -> recheck -> record -> save -> read back -> reconcile
    driver.py          the browser interface and its error types
    selenium_driver.py Selenium adapter (scaffold; see Status)
    discover.py        redacted structural discovery
    discover_interactive.py  approval-gated click-through discovery
    form_rehearsal.py  fill-everything-except-Save rehearsal
    calendar_parser.py pure parser: page structure -> availability
    staff_census.py    confirms the account has exactly one staff member, the configured one
    timeparse.py       time/date text parsing
    cli.py             command line
  tests/               deterministic tests, sanitized real-page fixtures, opt-in live tests
```
