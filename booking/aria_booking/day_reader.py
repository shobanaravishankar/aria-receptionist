"""The ONE day-reading and staff-identity logic, shared by every browser adapter (Selenium, Playwright).

Moved out of the Selenium driver unchanged so both adapters apply exactly the same fail-closed rules: a page whose staff filter lists a
COMPLETE roster of two or more people is read by staff id; anything else takes the single-staff path, which needs a Staff-page census and
re-checks, on every read, that the page does not contradict it. An adapter supplies only the browser work:

    _load_day(day)          -> (raw page nodes, roster or None)     raises SignInRequired / CalendarParseError
    verify_single_staff()   -> bool                                  the once-per-session Staff-page census
    _attach_notes(snapshot) -> snapshot                              optional; the default refuses

and these attributes: cfg, approvals, _staff_confirmed, _single_staff_id, _appointment_counts, last_roster, last_read_ms, _read_timer,
_timing_clock.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .calendar_parser import CalendarParseError, normalize_nodes, parse_day, parse_day_multi, single_column_identity
from .driver import DriverError
from .models import DaySnapshot
from .timing import PhaseTimer


class DayReader:
    def _require_not_frozen(self) -> None:  # an adapter that can leave a window open for a person overrides this
        return None

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        """Load that day's calendar and parse it. Anything not understood raises (never 'free').

        Two paths. A page whose staff filter lists a COMPLETE roster of two or more people is read by staff id (parse_day_multi): each
        column is bound to its roster entry by its own data-resource id. Anything else takes the single-staff path proven live: it
        needs the Staff-page census to confirm exactly one staff member, and refuses otherwise.
        include_notes opens each appointment's details (read-only) to fill in its internal note (single-staff path only)."""
        if include_notes and "note-readback" not in self.approvals:
            raise DriverError("opening appointment details to read notes needs the note-readback approval")
        self._require_not_frozen()
        tz = ZoneInfo(self.cfg.timezone)
        self._read_timer = PhaseTimer(self._timing_clock)
        self.last_read_ms = {}
        try:
            return self._read_day(day, include_notes, tz)
        finally:
            self.last_read_ms = {name: ms for name, ms in self._read_timer.as_dict().items() if name != "total"}

    def _forget_single_staff(self) -> None:
        """Contradictory or missing identity evidence: the earlier Staff-page census no longer vouches for what the page shows now."""
        self._staff_confirmed = None
        self._single_staff_id = None
        self._appointment_counts.clear()  # a count taken under the old identity must never let a save proceed

    def _check_single_staff_identity(self, nodes, roster) -> str:
        """The single-staff path trusts a census taken ONCE. Before reusing it, make sure the page in front of us does not contradict it.

        Refuses (and forgets the census, so the next read re-checks the Staff page) when: the staff filter exists but was not read as exactly
        one complete entry; that entry is not the configured staff member or not the id seen before; the single column carries a different
        id than the filter or than before; or a header names someone else. A page with no filter and no ids at all is the legacy case the
        census was proven on, and keeps working.

        Returns the staff id VERIFIED for the snapshot: the id of a complete one-member filter whose name is the configured staff member
        (and which any column id on the page agrees with). Without that it returns "", i.e. unknown; a column id seen only on the page is
        remembered to detect later changes but is not promoted to a verified id."""
        configured = " ".join(self.cfg.staff_name.casefold().split())
        roster_id: Optional[str] = None
        if roster is not None:
            if not (roster.complete and len(roster.members) == 1):
                self._forget_single_staff()
                why = "; ".join(roster.problems) or "it does not list exactly one staff member"
                raise CalendarParseError(f"the staff filter was not read as one complete entry ({why}); refusing to reuse an earlier single-staff check")
            member = roster.members[0]
            roster_id = member.staff_id
            if " ".join(member.name.casefold().split()) != configured:
                self._forget_single_staff()
                raise CalendarParseError("the staff filter lists someone other than the configured staff member; refusing to reuse an earlier single-staff check")
        ids, header_names = single_column_identity(nodes)
        for name in header_names:
            if " ".join(name.casefold().split()) != configured:
                self._forget_single_staff()
                raise CalendarParseError("a column header names someone other than the configured staff member; refusing to reuse an earlier single-staff check")
        seen = set(ids)
        if len(seen) > 1:
            return ""  # several columns: parse_day refuses with its own message
        known = {value for value in (roster_id, self._single_staff_id) if value}
        if (seen and known and seen != known) or (roster_id and self._single_staff_id and roster_id != self._single_staff_id):
            self._forget_single_staff()
            raise CalendarParseError("the staff id on this page differs from the one seen before; refusing to attribute it to the configured staff member")
        current = roster_id or next(iter(seen), None)
        if current:
            self._single_staff_id = current
        return roster_id or ""

    def _read_day(self, day: date, include_notes: bool, tz) -> DaySnapshot:
        raw, roster = self._load_day(day)
        self.last_roster = roster
        if roster is not None and roster.complete and len(roster.members) >= 2:
            self._forget_single_staff()  # an account that shows several people no longer has a single-staff identity to reuse
            if include_notes:
                raise DriverError("reading appointment notes is not supported on a multi-staff calendar; refusing to guess whose note is whose")
            self._appointment_counts.pop(day, None)  # creation is single-staff only: no count, so create_appointment refuses
            with self._read_timer.phase("parse"):
                return parse_day_multi(normalize_nodes(raw), day=day, tz=tz, roster=roster, captured_at=datetime.now(tz))

        verified_id = self._check_single_staff_identity(normalize_nodes(raw), roster)
        if self._staff_confirmed is None:
            self.verify_single_staff()  # may raise; once per session. It leaves the day page, so load the day again.
            raw, roster = self._load_day(day)
            verified_id = self._check_single_staff_identity(normalize_nodes(raw), roster)
        with self._read_timer.phase("parse"):
            snapshot = parse_day(
                normalize_nodes(raw),
                day=day,
                tz=tz,
                staff=self.cfg.staff_name,
                staff_confirmed=self._staff_confirmed,
                captured_at=datetime.now(tz),
            )
        if verified_id:  # the single-staff parser yields exactly one staff day
            snapshot = replace(snapshot, staff_days=tuple(replace(day_, staff_id=verified_id) for day_ in snapshot.staff_days))
        staff_day = snapshot.for_staff(self.cfg.staff_name)
        if staff_day is not None and staff_day.appointments is not None:
            self._appointment_counts[day] = len(staff_day.appointments)
        else:
            self._appointment_counts.pop(day, None)
        if include_notes and staff_day is not None and staff_day.appointments:
            snapshot = self._attach_notes(snapshot)
        return snapshot

    def _attach_notes(self, snapshot: DaySnapshot) -> DaySnapshot:
        raise DriverError("this adapter cannot read appointment notes")
