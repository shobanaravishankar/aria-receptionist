// Smoke-test a DEPLOYED showcase (a draft or the live site) the way Retell and a visitor use it. No dependencies: Node 22+ only.
//
//   ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the site> [--write]
//
// Without --write it only READS: health, a signed lookup, a signed availability check, the open times, the staff calendar page, the readiness check. With --write it also makes ONE clearly
// synthetic booking (name "Smoke Test", a 555-01xx number) and cancels it, to prove the whole write path on the real database. It prints no secret and no token. It sends nothing to any
// customer and starts no call. Every request is an ordinary HTTPS request to the address you give it.
import { createHmac } from "node:crypto";

const base = (process.argv[2] ?? "").replace(/\/+$/, "");
const write = process.argv.includes("--write");
const key = process.env.ARIA_RETELL_API_KEY;
const token = process.env.ARIA_RETELL_TOOL_TOKEN;
if (!/^(https:\/\/[^/]+|http:\/\/(127\.0\.0\.1|localhost)(:\d+)?)$/.test(base) || !key || !token) { // https only, except a loopback address (to try the script against a local copy)
  console.error("usage: ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the site> [--write]");
  process.exit(2);
}
let failures = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -  " + detail : ""}`); if (!ok) failures += 1; };
const get = async (path, headers = {}) => { const started = Date.now(); const r = await fetch(base + path, { headers }); const text = await r.text(); let body = null; try { body = JSON.parse(text); } catch {} return { status: r.status, text, body, ms: Date.now() - started, headers: r.headers }; };
async function tool(name, args, call, { sign = true, bearer = true } = {}) {
  const body = JSON.stringify({ name, call: { call_id: call }, args });
  const at = Date.now();
  const headers = { "content-type": "application/json" };
  if (sign) headers["x-retell-signature"] = `v=${at},d=${createHmac("sha256", key).update(body).update(String(at)).digest("hex")}`;
  if (bearer) headers.authorization = `Bearer ${token}`;
  const started = Date.now();
  const r = await fetch(`${base}/tools/${name}`, { method: "POST", headers, body });
  const text = await r.text();
  let parsed = null; try { parsed = JSON.parse(text); } catch {}
  return { status: r.status, body: parsed, ms: Date.now() - started };
}
const tomorrow = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(Date.now() + 86_400_000);

const health = await get("/healthz");
check("healthz answers without the database", health.status === 200, `${health.ms} ms`);
const demo = await get("/demo/health");
check("demo health names the demo calendar", demo.status === 200 && demo.body?.source === "demo_calendar", JSON.stringify(demo.body?.covers));
const unsigned = await tool("lookup_service", { query: "swedish massage" }, "smoke", { sign: false });
check("an unsigned tool call is refused (401)", unsigned.status === 401, `${unsigned.status}`);
const noBearer = await tool("lookup_service", { query: "swedish massage" }, "smoke", { bearer: false });
check("a signed call without the bearer token is refused (401)", noBearer.status === 401, `${noBearer.status}`);
const ready = await get("/readyz", { authorization: `Bearer ${token}` });
check("readiness (opens the database, needs the token)", ready.status === 200 && ready.body?.status === "ready", `${ready.ms} ms; ${JSON.stringify(ready.body?.counts ?? ready.body)}`);
const lookup = await tool("lookup_service", { query: "sixty minute massage" }, "smoke");
check("lookup: a length plus a group asks which treatment", lookup.status === 200 && lookup.body?.status === "service_group", `${lookup.ms} ms`);
const slot = await tool("check_slot", { service_id: "massage-swedish-massage-60", date: tomorrow, time: "17:00", staff: "Sienna", service_words: "swedish massage" }, "smoke");
check("check_slot answers from the database", slot.status === 200 && ["available", "alternatives"].includes(slot.body?.status), `${slot.body?.status}, ${slot.ms} ms`);
const unknownStaff = await tool("check_slot", { service_id: "massage-deep-tissue-massage-60", date: tomorrow, time: "16:00", staff: "Tamsen", service_words: "deep tissue massage" }, "smoke");
check("a name nobody has is a question, not an availability list", unknownStaff.body?.reason === "unknown_staff" && unknownStaff.body?.times_checked === false);
const times = await get(`/demo/open-times?service_id=massage-swedish-massage-60&date=${tomorrow}`);
check("open times for the website", times.status === 200 && ["open", "none"].includes(times.body?.status), `${times.body?.status}, ${times.ms} ms`);
const page = await get(`/pro/en-us/9000001/calendar?date=${tomorrow}&view=day&staffers=working`);
check("the staff calendar page", page.status === 200 && page.text.includes("demo-banner") && /frame-ancestors/.test(page.headers.get("content-security-policy") ?? ""), `${page.ms} ms`);
const changes = await get("/demo/changes");
check("the change stamp", changes.status === 200 && typeof changes.body?.stamp === "string", changes.body?.stamp);

if (write) {
  let time = null; // a time that is genuinely free tomorrow (an earlier booking may hold the usual one)
  for (const candidate of ["17:15", "14:00", "13:15", "15:30", "16:30", "18:30", "12:45", "10:30"]) {
    const free = await tool("check_slot", { service_id: "massage-swedish-massage-60", date: tomorrow, time: candidate, staff: "Sienna", service_words: "swedish massage" }, "smoke-write");
    if (free.body?.status === "available") { time = candidate; break; }
  }
  check("write: a free time was found to test with", time !== null);
  const args = { service_id: "massage-swedish-massage-60", date: tomorrow, time: time ?? "17:15", staff: "Sienna", client_name: "Smoke Test", phone: "+1 212 555 0199", service_words: "swedish massage" };
  const read = await tool("book_appointment", args, "smoke-write");
  check("write: the read-back saves nothing", read.body?.status === "confirmation_required" && Boolean(read.body?.confirmation_token));
  const done = await tool("book_appointment", { ...args, confirmed: true, confirmation_token: read.body?.confirmation_token }, "smoke-write");
  check("write: the yes books once", done.body?.status === "booked_verified" && done.body?.ok === true, `${done.ms} ms`);
  const repeat = await tool("book_appointment", { ...args, confirmed: true, confirmation_token: read.body?.confirmation_token }, "smoke-write");
  check("write: a repeated yes books nothing more", repeat.body?.status === "already_booked");
  const shown = await get(`/pro/en-us/9000001/calendar?date=${tomorrow}`);
  check("write: the booking is on the staff calendar", shown.text.includes(`data-appointment-id="${done.body?.appointment?.reference}"`));
  const cancelArgs = { reference: done.body?.appointment?.reference, phone: "+1 212 555 0199", name: "Smoke Test" };
  const cancelRead = await tool("cancel_appointment", cancelArgs, "smoke-write-2");
  const cancelled = await tool("cancel_appointment", { ...cancelArgs, confirmed: true, confirmation_token: cancelRead.body?.confirmation_token }, "smoke-write-2");
  check("write: the smoke booking is cancelled again", cancelled.body?.status === "cancelled");
}
console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
