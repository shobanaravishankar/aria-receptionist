"""The voice tools for a returning client's PREFERRED TECHNICIAN per service family (demo only; an opt-in addition to the simulated lifecycle).

``PreferenceTools`` is the lifecycle tool set plus three routes: ``get_preferences``, ``save_preference`` and ``remove_preference``. Nothing else changes: booking, cancelling and moving
do not read or write a preference, so a one-off choice of technician, a past visit or an ordinary booking can never create or change one.

Rules enforced here, not left to the prompt:
  * Who is asking: the same demo match as the other tools (the complete phone number, and the full name when the number is shared). Two clients who share a household number never share a
    preference. This is a demo record match, not production authentication.
  * Nothing is saved or removed without a read-back the caller confirms; the read-back issues a token bound to this call and to exactly what was read; a confirmed request whose details
    differ (the preference changed meanwhile) gets a fresh read-back. A repeated confirmed request is answered from the first result and changes nothing.
  * The service must be ONE service family the calendar can check (a whole group such as "massage", an ambiguous or unknown service is asked about, never guessed) and the technician must be
    someone who does it (an unknown or ineligible name is asked about, with the real list of who does it).
  * ``ok`` is true only for a result that was re-read from the store after the write. A store that fails is reported as such and never as saved.
  * A preference is only WHO. It never guarantees a time: the tools say so, and booking still checks eligibility, hours, conflicts, the full duration and cleanup through ``check_slot``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..catalog.bookable import staff_key
from ..voice.speech import join_names
from .lifecycle import Lifecycle
from .preferences import NOTHING, REMOVED, REPLAYED, SAVED, UNCHANGED, PreferenceStore
from .store import StoreError
from .tool_defs import PREFERENCE_TOOL_NAMES  # noqa: F401  (the names the routes answer; re-exported for callers and tests)
from .tools import LIFECYCLE_OK, LIFECYCLE_ROUTES, MAX_CHANGES_PER_CALL, LifecycleTools

PREFERENCE_ROUTES = {
    **LIFECYCLE_ROUTES,
    "/tools/get_preferences": "get_preferences",
    "/tools/save_preference": "save_preference",
    "/tools/remove_preference": "remove_preference",
}
PREFERENCE_OK = LIFECYCLE_OK | frozenset({"preferences_listed", "no_preferences", "preference_for_service", "preference_saved", "preference_unchanged", "preference_removed",
                                          "nothing_to_remove", "preference_current"})
GUARANTEE = "It is only who you would like; it never guarantees a time, so I still check that person's hours and appointments for the full length."
SPOKEN_TEXT_MAX = 40


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def _reply(status: str, speak: str, **data: Any) -> dict:
    return {"status": status, "ok": status in PREFERENCE_OK, "speak": speak, **data}


def _echo(text: Any) -> str:
    """A caller's own words, safe to say back: one short line."""
    return " ".join(str(text).split())[:SPOKEN_TEXT_MAX]


class PreferenceTools(LifecycleTools):
    def __init__(self, lifecycle: Lifecycle, cfg, driver, clock, *, prefs: Optional[PreferenceStore] = None, **kwargs: Any):
        super().__init__(lifecycle, cfg, driver, clock, **kwargs)
        self._prefs = prefs or PreferenceStore(lifecycle.store)
        self._families: dict[str, tuple[str, Any]] = {}
        for item in self.catalog.items:
            self._families.setdefault(f"{item.category}-{slug(item.family)}", (item.family, item))

    # ---- which service, which technician -------------------------------------------------------------------------
    def _family(self, query: Any):
        """(family dict, None) for exactly one service family the calendar can check, else (None, a reply that asks for the one thing that would settle it)."""
        if not isinstance(query, str) or not query.strip():
            return None, _reply("needs_clarification", "Which service is that for?", reason="service_required")
        group = self._group_answer(query) if self._group_answer else None
        if group is not None:
            return None, _reply("needs_clarification", f"Which one is that for? For example {join_names(group['featured'])}.", reason="service_not_specific", examples=group["featured"])
        resolution = self.catalog.resolve(query)
        if resolution.kind == "unknown":
            return None, _reply("needs_clarification", "I don't have that service on the menu. Which service did you mean?", reason="unknown_service")
        if resolution.kind == "ambiguous":
            names = list(dict.fromkeys(i.family for i in resolution.items))[:8]
            return None, _reply("needs_clarification", f"Several services fit that: {join_names(names)}. Which one did you mean?", reason="service_ambiguous", families=names)
        item = resolution.items[0]
        service = self.registry.for_catalog_item(item.item_id)
        if service is None or not (service.verified or not self._require_verified):
            return None, _reply("needs_clarification", f"A preferred technician is only kept for services that are booked with a technician, and {item.family} isn't one I can book.", reason="not_bookable")
        return {"id": f"{item.category}-{slug(item.family)}", "name": item.family, "service": service}, None

    def _eligible(self, service) -> list:
        return [s for s in self._lc.dataset.roster if service.eligible(s.name, s.staff_id)]

    def _technician(self, family: dict, text: Any):
        eligible = self._eligible(family["service"])
        names = join_names([s.name for s in eligible])
        if not isinstance(text, str) or not text.strip():
            return None, _reply("needs_clarification", f"Which technician would you like saved? For {family['name']} it's {names}.", reason="staff_required", technicians=[s.name for s in eligible])
        match = next((s for s in self._lc.dataset.roster if staff_key(s.name) == staff_key(text)), None)
        if match is None:
            return None, _reply("needs_clarification", f"I don't know a technician called {_echo(text)}. For {family['name']} it's {names}. Which one would you like?", reason="unknown_staff",
                                technicians=[s.name for s in eligible])
        if not family["service"].eligible(match.name, match.staff_id):
            return None, _reply("needs_clarification", f"{match.name} doesn't do {family['name']}. For {family['name']} it's {names}. Which one would you like?", reason="staff_not_eligible",
                                technicians=[s.name for s in eligible])
        return match, None

    def _record(self, call_id: str, args: dict):
        client, _phone, problem = self._client(call_id, args)
        if problem is not None:
            if problem.get("reason") == "no_match":
                return None, _reply("not_found", self._contacting("I can't find an account for that number, so I can't keep a preferred technician for it."), reason="no_match")
            return None, problem
        return client, None

    def _show(self, staff_id: str) -> Optional[str]:
        person = next((s for s in self._lc.dataset.roster if s.staff_id == staff_id), None)
        return person.name if person else None

    # ---- READ ------------------------------------------------------------------------------------------------------------
    def get_preferences(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._get(call_id, args or {})))

    def _get(self, call_id: str, args: dict) -> dict:
        client, problem = self._record(call_id, args)
        if problem:
            return problem
        service_text = args.get("service")
        family = None
        if isinstance(service_text, str) and service_text.strip():
            family, problem = self._family(service_text)
            if problem:
                return problem
        try:
            held = self._prefs.for_client(client.client_id)
        except StoreError:
            return self._unavailable()
        if family is not None:
            mine = next((p for p in held if p.family_id == family["id"]), None)
            if mine is None:
                return _reply("no_preferences", f"I don't have a preferred technician saved for {family['name']}.", service=family["name"], preferred=None,
                              instruction=f"Nothing is saved for {family['name']}. Offer to check availability as usual; save one only if the caller asks. Never save one on your own.")
            person = self._show(mine.staff_id)
            still = bool(person and family["service"].eligible(person, mine.staff_id))
            if not person or not still:
                return _reply("preference_for_service", f"Your saved choice for {family['name']} is a technician who isn't available to book for it any more, so I'd suggest someone else for now.",
                              service=family["name"], preferred=person, eligible=False,
                              instruction="Do NOT change or remove the saved preference. Offer alternatives from check_slot or find_alternatives; the caller may ask to change it.")
            return _reply("preference_for_service", f"Your preferred technician for {family['name']} is {person}.", service=family["name"], preferred=person, eligible=True,
                          instruction=(f"If they are booking {family['name']}, offer {person} first ('{person}, your usual?') and call check_slot with staff={person}. {GUARANTEE} If {person} isn't free or doesn't "
                                       "fit, say why from the tool and offer alternatives; do NOT change or remove the saved preference. If they ask for someone else for this appointment, book that person "
                                       "and leave the saved preference alone."))
        if not held:
            return _reply("no_preferences", "I don't have any preferred technicians saved for you.", preferences=[],
                          instruction="Nothing is saved. Save one only if the caller asks, in their own words. Never save one on your own.")
        listed = []
        for p in held:
            name, _item = self._families.get(p.family_id, (p.family_id, None))
            listed.append({"service": name, "technician": self._show(p.staff_id) or "a technician who is no longer on the team"})
        spoken = "; ".join(f"{entry['technician']} for {entry['service']}" for entry in listed)
        return _reply("preferences_listed", f"Your preferred technicians: {spoken}.", preferences=listed,
                      instruction=f"Read these as they are. {GUARANTEE} Do not add or change any unless the caller asks.")

    # ---- SAVE or CHANGE ----------------------------------------------------------------------------------------------------
    def save_preference(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._save(call_id, args or {})))

    def _save(self, call_id: str, args: dict) -> dict:
        client, problem = self._record(call_id, args)
        if problem:
            return problem
        family, problem = self._family(args.get("service"))
        if problem:
            return problem
        person, problem = self._technician(family, args.get("staff"))
        if problem:
            return problem
        try:
            current = self._prefs.get(client.client_id, family["id"])
        except StoreError:
            return self._unavailable()
        if current is not None and current.staff_id == person.staff_id:
            return _reply("preference_unchanged", f"{person.name} is already saved as your preferred technician for {family['name']}. Nothing changed.", service=family["name"], preferred=person.name)
        if self._changed_count(call_id) >= MAX_CHANGES_PER_CALL:
            return _reply("refused", self._contacting("I can't change any more on this call."), reason="too_many_changes")
        wanted = {"client": client.client_id, "family": family["id"], "staff": person.staff_id, "current": current.staff_id if current else None}
        pending = self._take_pending("pref_set", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if pending is not None and {k: pending.payload[k] for k in ("client", "family", "staff")} == {k: wanted[k] for k in ("client", "family", "staff")}:
            if pending.payload["current"] != wanted["current"]:  # it changed after the read-back: read it back again
                return self._set_readback(call_id, wanted, family, person, current, stale=True)
            return self._commit_set(call_id, args["confirmation_token"], client, family, person)
        return self._set_readback(call_id, wanted, family, person, current)

    def _set_readback(self, call_id: str, wanted: dict, family: dict, person, current, *, stale: bool = False) -> dict:
        token = self._new_pending("pref_set", call_id, wanted)
        lead = "That has changed since I read it out. " if stale else ""
        was = self._show(current.staff_id) if current else None
        what = (f"change your preferred technician for {family['name']} from {was} to {person.name}" if current else f"save {person.name} as your preferred technician for {family['name']}")
        return _reply("confirmation_required", f"{lead}To confirm: {what}. {GUARANTEE} Shall I go ahead?", confirmation_token=token, service=family["name"], preferred=person.name, previous=was,
                      instruction=("Read this back and wait for a clear yes. If they say no, say that nothing was saved and stop. Then call save_preference again with the same number, service and "
                                   "technician, the same confirmation_token and confirmed=true. Only say it is saved after the tool says so."))

    def _commit_set(self, call_id: str, token: str, client, family: dict, person) -> dict:
        try:
            outcome = self._prefs.set(client.client_id, family["id"], person.staff_id, request_key=self._token_key("pref_set", call_id, token))
            now = self._prefs.get(client.client_id, family["id"])
        except StoreError:
            return self._unavailable()
        except Exception:
            return _reply("needs_review", self._contacting("Something went wrong and I can't confirm whether that was saved. Please don't rely on it."), reason="exception")
        if outcome.status == REPLAYED and (now is None or now.staff_id != person.staff_id):  # an earlier confirmation of this very request, since overtaken by a newer one
            kept = self._show(now.staff_id) if now else None
            return _reply("preference_current", (f"Your preferred technician for {family['name']} is now {kept}." if kept else f"You have no preferred technician saved for {family['name']} now."),
                          service=family["name"], preferred=kept)
        if outcome.status not in (SAVED, UNCHANGED, REPLAYED) or now is None or now.staff_id != person.staff_id:
            return _reply("needs_review", self._contacting("I can't confirm that was saved. Please don't rely on it."), reason="not_verified")
        if outcome.status == SAVED:
            self._count_change(call_id)
        return _reply("preference_saved" if outcome.status != UNCHANGED else "preference_unchanged",
                      f"Done: {person.name} is saved as your preferred technician for {family['name']}. Your appointments haven't changed." if outcome.status != UNCHANGED
                      else f"{person.name} was already saved as your preferred technician for {family['name']}. Nothing changed.",
                      service=family["name"], preferred=person.name)

    # ---- REMOVE ------------------------------------------------------------------------------------------------------------
    def remove_preference(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._remove(call_id, args or {})))

    def _remove(self, call_id: str, args: dict) -> dict:
        client, problem = self._record(call_id, args)
        if problem:
            return problem
        family, problem = self._family(args.get("service"))
        if problem:
            return problem
        try:
            current = self._prefs.get(client.client_id, family["id"])
        except StoreError:
            return self._unavailable()
        if current is None:
            return _reply("nothing_to_remove", f"You don't have a preferred technician saved for {family['name']}, so there is nothing to remove.", service=family["name"])
        if self._changed_count(call_id) >= MAX_CHANGES_PER_CALL:
            return _reply("refused", self._contacting("I can't change any more on this call."), reason="too_many_changes")
        wanted = {"client": client.client_id, "family": family["id"], "current": current.staff_id}
        pending = self._take_pending("pref_remove", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if pending is not None and pending.payload == wanted:
            return self._commit_remove(call_id, args["confirmation_token"], client, family)
        if pending is not None:  # confirmed, but what is saved is not what was read out
            return self._remove_readback(call_id, wanted, family, current, stale=True)
        return self._remove_readback(call_id, wanted, family, current)

    def _remove_readback(self, call_id: str, wanted: dict, family: dict, current, *, stale: bool = False) -> dict:
        token = self._new_pending("pref_remove", call_id, wanted)
        lead = "That has changed since I read it out. " if stale else ""
        return _reply("confirmation_required", f"{lead}To confirm: remove {self._show(current.staff_id) or 'your saved technician'} as your preferred technician for {family['name']}. "
                                               "Your appointments won't change. Shall I go ahead?",
                      confirmation_token=token, service=family["name"],
                      instruction=("Read this back and wait for a clear yes. If they say no, say that nothing was removed and stop. Then call remove_preference again with the same number and service, "
                                   "the same confirmation_token and confirmed=true. Only say it is removed after the tool says so."))

    def _commit_remove(self, call_id: str, token: str, client, family: dict) -> dict:
        try:
            outcome = self._prefs.remove(client.client_id, family["id"], request_key=self._token_key("pref_remove", call_id, token))
            gone = self._prefs.get(client.client_id, family["id"]) is None
        except StoreError:
            return self._unavailable()
        except Exception:
            return _reply("needs_review", self._contacting("Something went wrong and I can't confirm whether that was removed. Please don't rely on it."), reason="exception")
        if outcome.status not in (REMOVED, NOTHING, REPLAYED):
            return _reply("needs_review", self._contacting("I can't confirm that was removed. Please don't rely on it."), reason="not_verified")
        if outcome.status == REPLAYED and not gone:  # an earlier removal, since overtaken by a newer save
            return _reply("preference_current", f"You have a preferred technician saved for {family['name']} now, so I haven't removed it.", service=family["name"])
        if not gone:
            return _reply("needs_review", self._contacting("I can't confirm that was removed. Please don't rely on it."), reason="not_verified")
        if outcome.status == REMOVED:
            self._count_change(call_id)
        return _reply("preference_removed", f"Done: your preferred technician for {family['name']} is removed. Your appointments haven't changed.", service=family["name"])
