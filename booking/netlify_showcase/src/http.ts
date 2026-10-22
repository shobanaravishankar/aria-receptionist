/* What the public backend answers over HTTP, as a plain function of the request: the eleven Retell custom functions (authenticated and validated before anything runs or any database is
 * opened), the demo calendar's read-only answers (open times, "has anything changed?", health) and the staff calendar page. Port of aria_booking/voice/retell_http.py (the signature and the
 * routing) and aria_booking/demo/server.py (CalendarSite). One source: the Netlify Function is a thin adapter around this.
 *
 * Retell's scheme (resolved against its official SDK when the Python endpoint was written): header `X-Retell-Signature` = `v=<unix ms>,d=<64 lowercase hex>`; the digest is HMAC-SHA256 with
 * the API key over the RAW body with the timestamp string appended directly; compared in constant time; the timestamp must be within 5 minutes of now. There is no plain-body fallback: a
 * request in any other form is rejected. Domain outcomes (refusals, unknowns) are 200 with a `status`; only authentication and format problems are 4xx. */

import { createHmac, timingSafeEqual } from "node:crypto";

import { PREFERENCE_ROUTES } from "./prefTools.ts";
import { CALENDAR_PATH, DEMO_LABEL, renderPage } from "./page.ts";
import { openTimes } from "./openTimes.ts";
import { dayBounds } from "./dayplan.ts";
import { casefold, CONFIRMED } from "./appointments.ts";
import { COVERAGE_AFTER_DAYS, COVERAGE_BEFORE_DAYS } from "./dataset.ts";
import type { DemoDataset } from "./dataset.ts";
import { isEligible } from "./bookable.ts";
import { addDays, dateOf, isoDate, parseIsoDate } from "./time.ts";
import type { DateParts, Instant } from "./time.ts";
import type { World } from "./world.ts";

export const MAX_BODY_BYTES = 256 * 1024; // Retell includes the transcript so far in `call`; a long call must not be refused for size
export const TOLERANCE_MS = 5 * 60 * 1000;
export const OPEN_TIMES_PATH = "/demo/open-times";
export const CHANGES_PATH = "/demo/changes";
const MAX_PARAM = 80;
const SIGNATURE_RE = /^v=(\d+),d=([0-9a-f]{64})$/;

export interface HttpRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Record<string, string>; // lower-case names
  body: Uint8Array;
}
export interface HttpResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}
export interface AppOptions {
  /** Opens the world (database, store, tools). Called only after a request has been authenticated or needs data; never for health, never for a request that is malformed or answerable without records. */
  world: () => Promise<World>;
  /** The demo's fixed schedule and menu. It needs no database: it lets a request be validated, and answered where no records matter, before one is opened. */
  dataset: () => DemoDataset;
  apiKey: string | null;
  bearer: string | null;
  now: () => Instant;
  /** Websites other than this one that may read the calendar and show its staff page in a frame (the same site needs nothing). */
  siteOrigins?: string[];
  log?: (message: string) => void;
}

const json = (status: number, data: unknown, extra: Record<string, string> = {}): HttpResponse => ({
  status, headers: { "content-type": "application/json", "cache-control": "no-store", "x-demo-source": "demo_calendar", ...extra }, body: JSON.stringify(data),
});
const text = (status: number, body: string, extra: Record<string, string> = {}): HttpResponse => ({ status, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store", ...extra }, body });
const failure = (status: number, error: string): HttpResponse => json(status, { status: "error", ok: false, speak: "", error });
/* Caching: what comes out of the database (open times, the change stamp, the calendar page) is never cached, because a stale answer would show a booked time as open and none of it may be kept
 * for someone else. What does NOT depend on any record (health, an unknown treatment, a date outside the demo window) may be kept briefly by the browser, so a reload costs nothing.
 * `private`: kept by the visitor's own browser only, never by a shared cache (the answer varies with the Origin header). */
const BRIEFLY = (seconds: number): Record<string, string> => ({ "cache-control": `private, max-age=${seconds}` });

/** True only for a signature in the official form that matches the raw body and is fresh. Never throws. */
export function verifySignature(rawBody: Uint8Array, header: string | null | undefined, apiKey: string | null, nowMs: number): boolean {
  if (!apiKey || !header) return false;
  const match = SIGNATURE_RE.exec(header);
  if (match === null) return false;
  const [, timestamp, digest] = match;
  if (Math.abs(nowMs - Number(timestamp)) > TOLERANCE_MS) return false;
  const expected = createHmac("sha256", apiKey).update(rawBody).update(timestamp, "ascii").digest("hex");
  return timingSafeEqual(Buffer.from(expected, "ascii"), Buffer.from(digest, "ascii"));
}
function sameSecret(supplied: string, wanted: string): boolean {
  const a = Buffer.from(supplied, "utf8");
  const b = Buffer.from(wanted, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function handle(req: HttpRequest, app: AppOptions): Promise<HttpResponse> {
  const method = req.method.toUpperCase();
  const path = req.path;
  const origin = req.headers.origin;
  const cors = (): Record<string, string> => (origin && (app.siteOrigins ?? []).includes(origin) ? { "access-control-allow-origin": origin, vary: "Origin" } : { vary: "Origin" });

  // ---- the voice tools
  if (path.startsWith("/tools/")) {
    const route = PREFERENCE_ROUTES[path];
    if (method !== "POST") return failure(405, "method_not_allowed");
    if (route === undefined) return failure(404, "not_found");
    if (req.body.length > MAX_BODY_BYTES) return failure(413, "too_large");
    if (!app.apiKey) return failure(503, "not_configured");
    if (!verifySignature(req.body, req.headers["x-retell-signature"], app.apiKey, app.now())) {
      app.log?.(`rejected ${route}: bad or missing signature`);
      return failure(401, "unauthorized");
    }
    if (app.bearer !== null && !sameSecret(req.headers.authorization ?? "", `Bearer ${app.bearer}`)) {
      app.log?.(`rejected ${route}: bad or missing bearer token`);
      return failure(401, "unauthorized");
    }
    let body: any;
    try {
      body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(req.body));
    } catch {
      return failure(400, "bad_json");
    }
    if (typeof body !== "object" || body === null || Array.isArray(body)) return failure(400, "bad_shape");
    const { name, call } = body;
    const args = "args" in body ? body.args : {};
    if (name !== null && name !== undefined && name !== route) return failure(400, "name_mismatch");
    const callId = typeof call === "object" && call !== null ? call.call_id : null;
    if (typeof callId !== "string" || !callId.trim() || typeof args !== "object" || args === null || Array.isArray(args)) return failure(400, "bad_shape");
    try {
      const world = await app.world();
      const result = await world.call(route, callId, args);
      app.log?.(`${route}: ${result.status}`);
      return json(200, result);
    } catch (error) {
      app.log?.(`${route} failed: ${(error as Error).constructor.name}`); // never leak internals to the caller; the type name is enough for the operator log
      return json(200, { status: "error", ok: false, speak: "I'm not able to check that right now, so I can't confirm anything. Please try again shortly or contact the salon directly." });
    }
  }

  // ---- the calendar's read-only answers
  const known = [OPEN_TIMES_PATH, CHANGES_PATH, "/demo/health", CALENDAR_PATH];
  if (method === "OPTIONS") {
    if (!known.includes(path)) return failure(404, "not_found");
    const allowed = Boolean(origin && (app.siteOrigins ?? []).includes(origin));
    return { status: 204, headers: { ...cors(), ...(allowed ? { "access-control-allow-methods": "GET, OPTIONS", "access-control-allow-headers": "Content-Type", "access-control-max-age": "600" } : {}) }, body: "" };
  }
  if (path === "/healthz") return method === "GET" || method === "HEAD" ? json(200, { status: "ok" }) : failure(405, "method_not_allowed");
  if (path === "/readyz") { // the operator's check: it opens the database, so it needs the same bearer token as the tools (a public address must not be able to wake the database)
    if (method !== "GET") return failure(405, "method_not_allowed");
    if (app.bearer === null || !sameSecret(req.headers.authorization ?? "", `Bearer ${app.bearer}`)) return failure(401, "unauthorized");
    try {
      const world = await app.world();
      return json(200, { status: "ready", stamp: await world.store.changeStamp(), counts: await world.store.counts(), today: isoDate(dateOf(app.now())) });
    } catch (error) {
      return json(503, { status: "not_ready", error: (error as Error).constructor.name });
    }
  }
  if (!known.includes(path)) return failure(404, "not_found");
  if (method !== "GET" && method !== "HEAD") return text(405, "read-only");
  const today = dateOf(app.now());
  if (path === "/demo/health") { // no database: coverage is a fixed window around today
    return json(200, { source: "demo_calendar", label: DEMO_LABEL, today: isoDate(today), covers: [isoDate(addDays(today, -COVERAGE_BEFORE_DAYS)), isoDate(addDays(today, COVERAGE_AFTER_DAYS))] }, { ...cors(), ...BRIEFLY(60) });
  }
  // Everything below is validated BEFORE a database is opened: a malformed or unanswerable request must never wake it.
  let openTimesAsk: { serviceId: string; staff: string | null; day: DateParts } | null = null;
  let pageDay: DateParts | null = null;
  if (path === OPEN_TIMES_PATH) {
    const serviceId = req.query.get("service_id") ?? "";
    const staff = req.query.get("staff") || null;
    if (!serviceId || serviceId.length > MAX_PARAM || (staff !== null && staff.length > MAX_PARAM)) return json(400, { status: "error", message: "service_id is required" }, cors());
    let day: DateParts;
    try {
      day = parseIsoDate(req.query.get("date") ?? "");
    } catch {
      return json(400, { status: "error", message: "date must be YYYY-MM-DD" }, cors());
    }
    const dataset = app.dataset();
    const service = dataset.registry.get(serviceId);
    const whoDoes = service === null ? [] : dataset.roster.filter((s) => isEligible(service, s.name, s.staffId)).map((s) => casefold(s.name));
    if (service === null || !service.verified || !dataset.covers(day) || (staff !== null && !whoDoes.includes(casefold(staff.trim())))) { // none of these depends on a record
      return json(200, openTimes(dataset, serviceId, day, { now: app.now(), staff }), { ...cors(), ...BRIEFLY(300) });
    }
    openTimesAsk = { serviceId, staff, day };
  } else if (path === CALENDAR_PATH) {
    const wanted = req.query.get("date") ?? "today";
    try {
      pageDay = wanted === "today" ? today : parseIsoDate(wanted);
    } catch {
      return text(400, "the date must be YYYY-MM-DD or 'today'");
    }
    if (!app.dataset().covers(pageDay)) { // the page says plainly that there is no demo data for that date; no record is needed for that
      return { status: 200, headers: { "content-type": "text/html; charset=utf-8", ...BRIEFLY(300), "x-demo-source": "demo_calendar", "content-security-policy": "frame-ancestors 'self' " + (app.siteOrigins ?? []).join(" ") }, body: method === "HEAD" ? "" : renderPage(app.dataset(), pageDay, []) };
    }
  }
  try {
    const world = await app.world();
    if (path === CHANGES_PATH) return json(200, { stamp: `${isoDate(today)}|${await world.store.changeStamp()}`, today: isoDate(today) }, cors());
    if (openTimesAsk !== null) {
      const [low, high] = dayBounds(openTimesAsk.day);
      const rows = await world.store.between(low, high, { statuses: [CONFIRMED] });
      return json(200, openTimes(world.dataset, openTimesAsk.serviceId, openTimesAsk.day, { now: app.now(), rows, staff: openTimesAsk.staff }), cors());
    }
    // the staff calendar page
    const day = pageDay!;
    const [low, high] = dayBounds(day);
    const rows = await world.store.between(low, high, { statuses: [CONFIRMED] });
    // only this site (and any listed) may show this page inside a frame
    const framing = { "content-security-policy": "frame-ancestors 'self' " + (app.siteOrigins ?? []).join(" ") };
    return { status: 200, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-demo-source": "demo_calendar", ...framing }, body: method === "HEAD" ? "" : renderPage(world.dataset, day, rows) };
  } catch (error) {
    app.log?.(`${path} failed: ${(error as Error).constructor.name}`);
    return json(503, { status: "error", message: "The calendar can't be read right now." }, cors());
  }
}
