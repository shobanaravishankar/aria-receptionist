# Aria booking prototype (Booksy Biz test account, Selenium)

An isolated prototype of the booking half of Aria, the salon receptionist. It is **not** imported by the
website and changes nothing about the voice/avatar demo. It targets **one dedicated Booksy Biz test
account** and nothing else.

## Status — read this first

| capability | status |
|---|---|
| Find slots that fit the service, buffers, working hours, time off, existing appointments, lead time | implemented, unit-tested against an in-memory calendar |
| Unknown availability is never treated as free | implemented, tested |
| Reject a booking that would run past closing | implemented, tested (rule only) |
| Duplicate / retry safety: ledger, read-back verification, reconcile after an uncertain save | implemented, tested against a fake calendar that simulates the failures |
| Safety guards (right business, test marker, future slot, run limit) | implemented, tested |
| Detect a concurrent booking after the fact | implemented, tested |
| Browser launch, wait for a **person** to sign in, verify the signed-in business | implemented, tested with a fake browser — **not yet run against Booksy** |
| Structural discovery of the calendar page (redacted) | implemented, tested — **not yet run against Booksy** |
| Read availability from the real calendar | **NOT implemented** (needs discovery first) |
| Create an appointment in the real calendar | **NOT implemented** (needs discovery first) |
| Reschedule, cancel, expired-sign-in recovery against the real site | **NOT implemented / NOT run** |

Nothing in this prototype has created, changed or read a real Booksy appointment. The deterministic
tests prove the **logic**; they do not prove that Booksy behaves as assumed.

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
python -m aria_booking config      # effective settings (business id masked)
python -m aria_booking login       # opens the test browser; a PERSON signs in; verifies the business
python -m aria_booking discover    # writes a REDACTED page-structure report for review
python -m aria_booking find        # free slots; unknown days are reported as unknown
python -m aria_booking book --start "2026-10-20 10:00" --confirm-business-id <id>
```

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

1. A person runs `login` and signs in (first live use; needs a chromedriver — see above).
2. `discover` produces a redacted structure report. The Selenium parsers and the appointment-creation steps
   are then written from that real structure, with sanitized fixtures for unit tests.
3. Opt-in live tests (`python -m pytest -m live`) are then extended and run against the test account.
4. Later scenarios — near-closing rejection on the live calendar, reschedule freeing the original slot,
   cancellation restoring availability, expired sign-in — are added and are **not** considered passed until
   actually run.

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
    cli.py             command line
  tests/               deterministic tests + opt-in live tests
```
