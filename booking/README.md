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
| Create an appointment (the Save click and what follows) | **Run live ONCE (2026-10-08): created exactly one fictional ARIA TEST appointment, Mon 12 Oct 2026 11:00 AM-1:30 PM.** After Save the new-client prompt appeared and NOT NOW was clicked. The run then stopped on a false alarm of the dialog detector (the calendar's always-present 'confirmed'/'unconfirmed' labels were mistaken for a dialog; fixed and tested) and left the window open, as designed. The appointment was confirmed by a person looking at the calendar. Only this one path has been run live |
| Read a saved appointment's internal note back for verification | **Run live (read-only `verify`): found the saved appointment by its reference, and the time, duration and service matched.** The ledger entry went from uncertain to verified |
| Reschedule, cancel, expired-sign-in recovery against the real site | **NOT implemented / NOT run** |
| Voice-agent (Retell) integration: tools, signed HTTP boundary, `serve`, test-only prompt (`voice_agent_draft/`) | implemented and independently reviewed **offline** (reviewed commit `f5f5312`). **NOT configured in Retell, NOT run live, no tunnel.** Permission to run a supervised connection test is not verification: see `voice_agent_draft/SUPERVISED_TEST_CHECKLIST.md` |

**No real appointment has ever been created by this prototype.** The tests prove the logic and the parser's
behaviour on captured pages; they do not prove that a live Save behaves as assumed.

## Issue #3: staff availability and latency (read-only voice availability)

Branch `feature/service-booking-workflow`. This is the **availability-only demo**: Aria answers salon service/product questions from
the website knowledge (no calendar call), and only on an appointment or availability intent does she read the Booksy calendar. It has
**no write path of any kind**: the booking function is not in the demo's Retell tool file and the server has no booking route, so
"book it" cannot create, change, hold or cancel anything. A reply never says "booked".

What works (offline, tested; **nothing here has run against a live multi-staff Booksy account**):

| question | how it is answered |
|---|---|
| "Is Amy available at 4?" | `check_slot` with the named technician; the name is matched against the live roster (any staff member, not a fixed list); an ambiguous first name asks which one |
| "Who is available at 4?" | `check_slot` with no name: the first eligible technician who is actually free, plus the others free at that time |
| "If Amy is busy, what is her next opening?" | the same call, same page read: her openings that day, nearest to the asked time |
| "If Amy is busy, who else is available?" | other eligible technicians at that time, then `find_alternatives` for a wider search |

Rules kept (fail closed): a staff column is attributed by its own stable staff id (`data-resource`), never by position; a roster that is
incomplete, a column outside the roster, a duplicate id, a header that names someone else, a cut-short capture, or a header with no
hours all make that person's availability **unknown**, and unknown is never offered as free. A single-staff identity check taken earlier
is never reused when the page now contradicts it. Service-to-technician eligibility comes from a human-reviewed mapping table
(`voice_agent_draft/mapping_table.example.json` shows the shape with synthetic ids); a service without a verified mapping is not offered.

Latency work (structure only): a requested slot is answered from **one** page read; same-day alternatives come from that same read;
a read may be reused for 30 s on the availability-only line and the reply says how old it is; the loading-overlay check polls every
0.25 s instead of 2 s; per-phase timing (lock wait, navigate, page ready, paint wait, capture, parse, search, cache hit) is written to the
server log as names and milliseconds only.

Bounded and fail-fast (availability-only line): the server is threaded, so a local `lookup_service` answer never waits behind a slow
calendar read; an overlapping calendar request gets a spoken "busy" after 2 s instead of queueing; the WHOLE request (lock wait + every day read) has a 16 s budget, below Retell's 20 s tool timeout: no further day read is started
once it is spent, and a search cut short says so ("I ran out of time before I could check all of those days") instead of claiming there
are no openings; one calendar read has a 14 s hard
deadline (the page itself gives up after 10 s) and on a miss the answer is "I can't confirm that right now", never a guess; while an
abandoned read still occupies the browser, new reads fail immediately. Retell's tool timeouts for the demo are 5 s (lookup) and 20 s
(check/find), above that worst case and far below 30-40 s. Each server log line also carries `received_ms`/`sent_ms` wall-clock marks so
a call can be lined up with Retell's own call log (user stops speaking -> tool call -> first audio); the server cannot see speech start
itself, so caller-perceived latency still has to be read from Retell's call record during the supervised run.

**Measured latency: none yet.** The reported 30-40 second pauses have not been re-measured, and the 2-3 second target is **unproven**.
The first honest number will come from ONE read-only run with the timing log on (cold and warm, an open slot and a taken slot).

Remaining limitations / blockers:
- Needs a reviewed live read-only capture before any claim of readiness: time-off / blocked-time card rendering (not yet seen; any
  unrecognised card makes that person's time off unknown) and whether the roster ever spans more than one view. The real staff filter
  holds the staff plus three non-staff controls ("Select All", "Only me", "Working Staff Members"); each is recognised only by its
  complete observed structure and anything else makes the roster incomplete (the reader then refuses).
- The real service-to-staff mapping table has not been written or reviewed; service variant (30 min / 1 h / 1 h 30) structure on the
  Services tab is unverified, so Aria asks which length rather than inferring it.
- Retell's signing of custom-function calls is assumed to follow its webhook scheme; if not, requests fail closed (401).
- Cancel and reschedule are not built. Live real-customer booking stays disabled.

Manual test steps for Shobana (no booking can occur on this line):
1. From `booking`, run the offline suite: `python -m pytest` (about 1000 tests, no browser, no network).
2. With the sign-in already done in the test browser, set `ARIA_MAPPING_TABLE` to the reviewed table (check it first with
   `python -m aria_booking mapping-check`), then start the server: `python -m aria_booking serve --confirm-business-id <id>`
   with NO `--approve-*` flags (it is availability-only unless all five booking approvals are given).
3. Ask: "Is <a technician> available Monday at 4 for <a service>?", then "who else is free then?", then "book it".
   Expect: a yes/no with the technician's name, other free technicians, and a refusal to book that tells the caller to contact the salon.
4. Read the server log line for each call: `total=... read=... cache_hit=...`. Send those numbers to Sol; they are the first real latency data.

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
3. ~~Implement creation~~ **done and run once live.** One caveat: the real booking run itself ended in 'needs review'
   because of the detector false alarm, so the end-to-end `book` path has not yet completed cleanly in one run; its
   pieces (save, prompt, verify) have each been seen live. Run `verify` for any slot recorded in the ledger. Do NOT
   re-run `book` for the same slot.
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
