"""Readiness inside the server (``GET /readyz``) and the checks a person can run against a deployed backend from anywhere (``demo-public-check``).

Every check that fails carries ``fix``: one plain sentence saying what to change. Nothing here prints, returns or logs a secret, a phone number or a client's name: counts, dates,
origins and yes/no answers only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..demo import DEMO_BUSINESS_ID, SPA_TIMEZONE


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True  # an optional check that fails is reported but does not make the backend not ready


def readiness(config, lifecycle, *, site_dir=None, maintenance: Optional[dict] = None, now: Optional[datetime] = None) -> list[Check]:
    """The server's own view of itself. ``now`` is for tests."""
    zone = ZoneInfo(SPA_TIMEZONE)
    spa_today = (now or datetime.now(zone)).astimezone(zone).date()
    checks: list[Check] = []
    try:
        counts = lifecycle.store.counts()
        checks.append(Check("store_readable", True, f"{sum(counts['appointments'].values())} appointments, {counts['clients']} clients"))
    except Exception:
        checks.append(Check("store_readable", False, "the appointment file could not be read",
                            "Check ARIA_STORE_PATH points at the file on the persistent volume, and that the volume is attached (a restart on a host without a volume starts from an empty file)."))
    try:
        writable = lifecycle.store.can_write()
    except Exception:
        writable = False
    checks.append(Check("store_writable", writable, "bookings can be saved" if writable else "the appointment file cannot be written",
                        "" if writable else "Make the volume writable by the server's user, and make sure the disk is not full; no booking can be saved until then."))
    checks.append(Check("spa_date", lifecycle.dataset.today == spa_today, f"the calendar's date is {lifecycle.dataset.today.isoformat()} (spa time)",
                        "" if lifecycle.dataset.today == spa_today else "The calendar's date is behind the real date in the spa's time zone: restart the server once and report it."))
    ran = (maintenance or {}).get("date")
    checks.append(Check("daily_upkeep", ran == spa_today.isoformat(), f"last upkeep for {ran or 'no day yet'}",
                        "" if ran == spa_today.isoformat() else "The daily upkeep (past fictional visits settled, upcoming ones topped up) has not run today: restart the server once.", required=False))
    checks.append(Check("tool_secrets", bool(config.api_key) and (bool(config.tool_token) or config.dev), "the Retell key and the tool token are set" if config.tool_token else "the Retell key is set (no tool token: development only)",
                        "" if config.api_key and (config.tool_token or config.dev) else "Set ARIA_RETELL_API_KEY and ARIA_RETELL_TOOL_TOKEN as host secrets."))
    checks.append(Check("origins", True, f"this backend is {config.public_origin}; websites allowed: " + (", ".join(config.site_origins) or "only this backend")))
    voice = config.voice_settings is not None
    checks.append(Check("voice_settings", voice, "the page has the public key and the agent id" if voice else "the page will say voice is not set up",
                        "" if voice else "Set ARIA_RETELL_PUBLIC_KEY and ARIA_RETELL_AGENT_ID so the website this backend serves can start a call.", required=False))
    if config.serve_site:
        present = bool(site_dir and (site_dir / "index.html").is_file())
        checks.append(Check("site_files", present, "the website is built and served from here" if present else "the website files are missing",
                            "" if present else "Restart the server: it builds the website at start."))
    return checks


def as_json(checks: list[Check], *, spa_date: str, version: str = "") -> dict:
    ok = all(c.ok for c in checks if c.required)
    return {"ok": ok, "spa_date": spa_date, "version": version,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, **({"fix": c.fix} if c.fix and not c.ok else {}), **({"required": False} if not c.required else {})} for c in checks]}


# ---------------------------------------------------------------- from outside


Fetch = Callable[[str, str, dict, Optional[bytes]], tuple[int, dict, bytes]]


def _fetch(method: str, url: str, headers: dict, body: Optional[bytes]) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), response.read(200_000)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(200_000)


def check_public(base_url: str, site_origin: str = "", *, fetch: Fetch = _fetch) -> list[Check]:
    """Ask a deployed backend the questions that matter, from outside, the way the website and Retell will. Read-only: it signs nothing and sends no tool call that could change anything."""
    base = base_url.rstrip("/")
    out: list[Check] = []

    def ask(method: str, path: str, headers: Optional[dict] = None, body: Optional[bytes] = None):
        try:
            return fetch(method, base + path, headers or {}, body)
        except Exception as exc:
            return 0, {}, f"{type(exc).__name__}".encode()

    def header(headers: dict, name: str) -> str:
        return next((v for k, v in headers.items() if k.lower() == name.lower()), "")

    status, _h, _b = ask("GET", "/healthz")
    out.append(Check("reachable", status == 200, f"/healthz answered {status or 'nothing'}",
                     "" if status == 200 else "The address does not answer. Check the host shows the app as running, the address is exactly the one the host gave, and that the app listens on the host's PORT."))
    if status != 200:
        return out
    status, _h, body = ask("GET", "/readyz")
    try:
        ready = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        ready = {}
    out.append(Check("ready", status == 200 and bool(ready.get("ok")), "the server says it is ready" if status == 200 else f"/readyz answered {status}",
                     "" if status == 200 else "; ".join(c.get("fix", "") for c in ready.get("checks", []) if not c.get("ok") and c.get("fix")) or "The server is up but not ready: see /readyz."))
    status, headers, body = ask("GET", "/demo/health", {"Origin": site_origin} if site_origin else {})
    out.append(Check("calendar_health", status == 200, f"/demo/health answered {status}", "" if status == 200 else "The calendar part of the server is not answering."))
    if site_origin:
        allowed = header(headers, "Access-Control-Allow-Origin")
        out.append(Check("website_may_read", allowed == site_origin, f"the website {site_origin} " + ("may read the open times" if allowed == site_origin else "is not allowed to read the open times"),
                         "" if allowed == site_origin else f"Add {site_origin} to ARIA_SITE_ORIGINS on the backend (exactly as the address bar shows it, https, no trailing slash) and restart."))
    status, headers, body = ask("GET", "/demo/open-times?service_id=massage-swedish-massage-60&date=" + (json.loads(body.decode("utf-8")).get("today", "") if status == 200 else ""),
                                {"Origin": site_origin} if site_origin else {})
    try:
        answer = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        answer = {}
    out.append(Check("open_times", status == 200 and answer.get("status") in ("open", "none", "unknown", "outside"), f"/demo/open-times answered {status}",
                     "" if status == 200 else "The open-times answer is failing: see the server's log."))
    status, headers, _b = ask("GET", f"/pro/en-us/{DEMO_BUSINESS_ID}/calendar?date=today&view=day&staffers=working")
    policy = header(headers, "Content-Security-Policy")
    frame_ok = status == 200 and (not site_origin or site_origin in policy)
    out.append(Check("calendar_page_frameable", frame_ok, f"the staff calendar page answered {status}" + (f" and may be shown inside {site_origin}" if frame_ok and site_origin else ""),
                     "" if frame_ok else (f"Add {site_origin} to ARIA_SITE_ORIGINS so the staff calendar may be shown inside the website." if status == 200 else "The staff calendar page is not answering.")))
    status, _h, _b = ask("POST", "/tools/lookup_service", {"Content-Type": "application/json"}, b"{}")
    out.append(Check("tools_protected", status == 401, f"an unsigned tool call is refused with {status}" if status == 401 else f"an unsigned tool call answered {status}",
                     "" if status == 401 else ("The tool route is missing: Retell's tool address must be https://<this backend>/tools/<name>." if status in (404, 0) else "An unsigned tool call was not refused: set ARIA_RETELL_API_KEY on the backend.")))
    status, _h, _b = ask("GET", "/tools/lookup_service")
    out.append(Check("tools_read_only_surface", status in (404, 405), f"a GET to a tool address answered {status}", "" if status in (404, 405) else "Tool addresses must accept signed POST requests only."))
    return out
