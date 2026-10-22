// Smoke-test a DEPLOYED showcase (a draft or the live site) the way Retell and a visitor use it. No dependencies: Node 22+ only.
//
//   ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the site> [--write] [--record <file.json>] [--after-idle <seconds>] [--header "Name: value"]...
//
// BEFORE anything else it asks /healthz and requires the application's own JSON answer. An address that answers with a login page or any other gate (a private preview, a password) is not the
// application: it stops with "BLOCKED" (exit 3) and checks nothing, because every "must be refused" check would otherwise pass for the wrong reason (the gate also says 401). `--header` (repeatable)
// sends an extra request header on every request, for whatever access credential the host supports (a cookie, a token header); its value is never printed.
//
// Without --write it only READS: health, authentication, all eleven tools in their read-only paths (a lookup, an availability check, alternatives, a client lookup, a last-visit lookup, the
// preferences read, and the read-back of a booking / cancellation / move / preference that is never confirmed), the open times, the staff calendar page, input validation. It writes nothing.
//
// With --write it also exercises every WRITE path with ONE clearly synthetic client ("Smoke Test <letters>", a 555-01xx number, both unique to this run): two clients racing for the same
// slot (exactly one may win: this is the hosted write-concurrency check), a repeated yes, a move, a preference saved / read / removed, a cancellation and its repeat. It leaves cancelled
// synthetic history behind (status "cancelled"; it cannot delete a record) and RECORDS every change it made: the record is printed and, with --record, written to a file so the test
// mutations can be accounted for. Every call id is unique to the run. --after-idle waits that many seconds (>= 330 is a real cold resume of a sleeping database) and then repeats a
// check, printing how long the first database request took.
//
// It prints no secret and no token, sends nothing to any customer and starts no call. Every request is an ordinary HTTPS request to the address you give it.
import { createHmac, randomBytes } from "node:crypto";
import { writeFile } from "node:fs/promises";

const args = process.argv.slice(2);
const base = (args.find((a, i) => !a.startsWith("--") && !["--record", "--after-idle", "--header"].includes(args[i - 1])) ?? "").replace(/\/+$/, "");
const flag = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
const write = args.includes("--write");
const extraHeaders = {};
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--header") {
    const text = args[i + 1] ?? "";
    const at = text.indexOf(":");
    if (at > 0) extraHeaders[text.slice(0, at).trim().toLowerCase()] = text.slice(at + 1).trim();
  }
}
const recordFile = flag("--record");
const idleSeconds = flag("--after-idle") === null ? 0 : Number(flag("--after-idle"));
const key = process.env.ARIA_RETELL_API_KEY;
const token = process.env.ARIA_RETELL_TOOL_TOKEN;
if (!/^(https:\/\/[^/]+|http:\/\/(127\.0\.0\.1|localhost)(:\d+)?)$/.test(base) || !key || !token || (idleSeconds && !(idleSeconds > 0))) { // https only, except a loopback address (to try the script against a local copy)
  console.error("usage: ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the site> [--write] [--record <file.json>] [--after-idle <seconds>]");
  process.exit(2);
}

const run = randomBytes(4).toString("hex"); // unique to this run
const letters = [...run].map((c) => "abcdefghijklmnop"[parseInt(c, 16)]).join(""); // a name without digits
const callId = (label) => `smoke-${run}-${label}`;
const mutations = [];
const note = (what, detail = {}) => mutations.push({ at: new Date().toISOString(), what, ...detail });
let failures = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -  " + detail : ""}`); if (!ok) failures += 1; };
const friendly = (r) => r.status === 200 && typeof r.body?.status === "string" && r.body.status !== "error" && typeof r.body?.speak === "string" && r.body.speak.length > 0; // answered, in words, not an error

const get = async (path, headers = {}) => { const started = Date.now(); const r = await fetch(base + path, { headers: { ...extraHeaders, ...headers } }); const text = await r.text(); let body = null; try { body = JSON.parse(text); } catch { /* html */ } return { status: r.status, text, body, ms: Date.now() - started, headers: r.headers }; };
async function tool(name, toolArgs, call, { sign = true, bearer = true } = {}) {
  const body = JSON.stringify({ name, call: { call_id: call }, args: toolArgs });
  const at = Date.now();
  const headers = { ...extraHeaders, "content-type": "application/json" };
  if (sign) headers["x-retell-signature"] = `v=${at},d=${createHmac("sha256", key).update(body).update(String(at)).digest("hex")}`;
  if (bearer) headers.authorization = `Bearer ${token}`;
  const started = Date.now();
  const r = await fetch(`${base}/tools/${name}`, { method: "POST", headers, body });
  const text = await r.text();
  let parsed = null; try { parsed = JSON.parse(text); } catch { /* not json */ }
  return { status: r.status, body: parsed, ms: Date.now() - started };
}
const dayOf = (offset) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(Date.now() + offset * 86_400_000);
const tomorrow = dayOf(1);
const SERVICE = "massage-swedish-massage-60";
const WORDS = "swedish massage";
const FAMILY = "Swedish Massage";
const TECH = "Sienna";

console.log(`run ${run}${write ? " (write paths ON)" : " (read-only)"} against ${base}`);

// ---------------------------------------------------------------- is this the application at all?
const probe = await get("/healthz").catch((error) => ({ status: 0, body: null, text: String(error.message ?? error).slice(0, 80), headers: new Headers() }));
if (probe.status !== 200 || probe.body?.status !== "ok") {
  const kind = /html/i.test(probe.headers.get("content-type") ?? "") || /^\s*</.test(probe.text ?? "") ? "a web page (a login or access gate?)" : probe.status === 0 ? `no answer (${probe.text})` : "something that is not the application's JSON";
  console.log(`BLOCKED  ${base}/healthz answered ${probe.status} with ${kind}, not the application's {"status":"ok"}.`);
  console.log("         Nothing was checked: behind a gate, \"must be refused\" checks would pass for the wrong reason. Pass --header for the access credential the host supports, or test an address without the gate.");
  process.exit(3);
}

// ---------------------------------------------------------------- health and authentication
const health = await get("/healthz");
check("healthz answers without the database", health.status === 200, `${health.ms} ms`);
const demo = await get("/demo/health");
check("demo health names the demo calendar and may be kept briefly (private)", demo.status === 200 && demo.body?.source === "demo_calendar" && /^private, max-age=\d+$/.test(demo.headers.get("cache-control") ?? ""), JSON.stringify(demo.body?.covers));
check("an unsigned tool call is refused (401)", (await tool("lookup_service", { query: "swedish massage" }, callId("auth"), { sign: false })).status === 401);
check("a signed call without the bearer token is refused (401)", (await tool("lookup_service", { query: "swedish massage" }, callId("auth"), { bearer: false })).status === 401);
const ready = await get("/readyz", { authorization: `Bearer ${token}` });
check("readiness (opens the database, needs the token)", ready.status === 200 && ready.body?.status === "ready", `${ready.ms} ms; ${JSON.stringify(ready.body?.counts ?? ready.body)}`);
check("readiness without the token is refused", (await get("/readyz")).status === 401);

// ---------------------------------------------------------------- the eleven tools, read-only paths
const lookup = await tool("lookup_service", { query: "sixty minute massage" }, callId("menu"));
check("1 lookup_service: a length plus a group asks which treatment", lookup.status === 200 && lookup.body?.status === "service_group", `${lookup.ms} ms`);
const variant = await tool("lookup_service", { query: "swedish massage" }, callId("menu"));
check("1 lookup_service: a named treatment offers its lengths", variant.status === 200 && variant.body?.status === "choose_variant");
const slot = await tool("check_slot", { service_id: SERVICE, date: tomorrow, time: "17:00", staff: TECH, service_words: WORDS }, callId("avail"));
check("2 check_slot: answers from the database", slot.status === 200 && ["available", "alternatives"].includes(slot.body?.status), `${slot.body?.status}, ${slot.ms} ms`);
const unknownStaff = await tool("check_slot", { service_id: "massage-deep-tissue-massage-60", date: tomorrow, time: "16:00", staff: "Tamsen", service_words: "deep tissue massage" }, callId("avail"));
check("2 check_slot: a name nobody has is a question, not an availability list", unknownStaff.body?.reason === "unknown_staff" && unknownStaff.body?.times_checked === false);
const alternatives = await tool("find_alternatives", { service_id: SERVICE, date: tomorrow, time: "12:00", staff: TECH, service_words: WORDS }, callId("avail"));
check("3 find_alternatives: answers with times or says there are none", friendly(alternatives), `${alternatives.body?.status}, ${alternatives.ms} ms`);
const nobody = { phone: "+1 212 555 0100", name: `Nobody ${letters}` };
check("4 find_appointments: a number with no account is told so, in words", friendly(await tool("find_appointments", nobody, callId("lookup"))));
check("5 last_completed_visit: the same", friendly(await tool("last_completed_visit", { ...nobody, service_category: "massage" }, callId("history"))));
check("6 get_preferences: the same", friendly(await tool("get_preferences", { ...nobody, service: FAMILY }, callId("prefs"))));
check("7 book_appointment: a read-back saves nothing and asks", (await tool("book_appointment", { service_id: SERVICE, date: tomorrow, time: "17:00", staff: TECH, client_name: "Read Only", phone: "+1 212 555 0100", service_words: WORDS }, callId("readback"))).body?.status !== "booked_verified");
check("8 cancel_appointment: a reference that is not there is told so, in words", friendly(await tool("cancel_appointment", { reference: "ZZZZZZ", ...nobody }, callId("readback"))));
check("9 reschedule_appointment: the same", friendly(await tool("reschedule_appointment", { reference: "ZZZZZZ", ...nobody, date: tomorrow, time: "15:00" }, callId("readback"))));
check("10 save_preference: no account, no preference saved, said in words", friendly(await tool("save_preference", { ...nobody, service: FAMILY, staff: TECH }, callId("readback"))));
check("11 remove_preference: the same", friendly(await tool("remove_preference", { ...nobody, service: FAMILY }, callId("readback"))));

// ---------------------------------------------------------------- the calendar and the website's reads
const times = await get(`/demo/open-times?service_id=${SERVICE}&date=${tomorrow}`);
check("open times for the website", times.status === 200 && ["open", "none"].includes(times.body?.status) && times.headers.get("cache-control") === "no-store", `${times.body?.status}, ${times.ms} ms`);
const page = await get(`/pro/en-us/9000001/calendar?date=${tomorrow}&view=day&staffers=working`);
check("the staff calendar page, framed only by this site", page.status === 200 && page.text.includes("demo-banner") && /frame-ancestors/.test(page.headers.get("content-security-policy") ?? ""), `${page.ms} ms`);
const changes = await get("/demo/changes");
check("the change stamp", changes.status === 200 && typeof changes.body?.stamp === "string", changes.body?.stamp);
check("a malformed open-times request is a 400, not a database wake-up", (await get("/demo/open-times?date=invalid")).status === 400 && (await get(`/demo/open-times?service_id=${SERVICE}&date=not-a-date`)).status === 400);
check("a date outside the demo window is said so (200, 'outside')", (await get(`/demo/open-times?service_id=${SERVICE}&date=2099-01-01`)).body?.status === "outside");

// ---------------------------------------------------------------- the write paths, with one synthetic client
if (write) {
  const phoneOf = (n) => `+1 212 555 01${String(n).padStart(2, "0")}`;
  const mine = { name: `Smoke Test ${letters}`, phone: phoneOf(parseInt(run.slice(0, 2), 16) % 100) };
  const rival = { name: `Smoke Rival ${letters}`, phone: phoneOf((parseInt(run.slice(2, 4), 16) % 99) + 1) };
  if (rival.phone === mine.phone) rival.phone = phoneOf((parseInt(run.slice(0, 2), 16) + 1) % 100);

  const freeTimes = [];
  for (const candidate of ["17:15", "14:00", "13:15", "15:30", "16:30", "18:30", "12:45", "10:30", "11:15", "09:45"]) { // genuinely free times tomorrow, found by asking
    const free = await tool("check_slot", { service_id: SERVICE, date: tomorrow, time: candidate, staff: TECH, service_words: WORDS }, callId("find-free"));
    if (free.body?.status === "available") freeTimes.push(candidate);
    if (freeTimes.length === 2) break;
  }
  check("write: two free times were found to test with", freeTimes.length === 2, freeTimes.join(", "));
  const [first, second] = freeTimes;
  const bookArgs = (who, time) => ({ service_id: SERVICE, date: tomorrow, time, staff: TECH, client_name: who.name, phone: who.phone, service_words: WORDS });

  // two clients, on two calls, both read back the same slot and then confirm at the same moment: exactly one may get it
  const [readMine, readRival] = await Promise.all([
    tool("book_appointment", bookArgs(mine, first), callId("mine")),
    tool("book_appointment", bookArgs(rival, first), callId("rival")),
  ]);
  check("write: both read-backs save nothing", readMine.body?.status === "confirmation_required" && readRival.body?.status === "confirmation_required", `${readMine.body?.status}, ${readRival.body?.status}`);
  const [raceMine, raceRival] = await Promise.all([
    tool("book_appointment", { ...bookArgs(mine, first), confirmed: true, confirmation_token: readMine.body?.confirmation_token }, callId("mine")),
    tool("book_appointment", { ...bookArgs(rival, first), confirmed: true, confirmation_token: readRival.body?.confirmation_token }, callId("rival")),
  ]);
  const winners = [raceMine, raceRival].filter((r) => r.body?.status === "booked_verified");
  check("write: two clients racing for one slot: exactly one booked, the other was refused in words", winners.length === 1 && [raceMine, raceRival].every((r) => r.status === 200 && (r.body?.speak ?? "").length > 0), `${raceMine.body?.status}, ${raceRival.body?.status}`);
  for (const [who, result] of [[mine, raceMine], [rival, raceRival]]) if (result.body?.status === "booked_verified") note("booked", { reference: result.body.appointment?.reference, client: who.name, time: first, day: tomorrow });
  // whoever won is the client for the rest of the run; the loser just books a different free time
  let owner = winners.length === 1 && raceMine.body?.status === "booked_verified" ? { who: mine, call: callId("mine"), booked: raceMine } : { who: rival, call: callId("rival"), booked: raceRival };
  if (winners.length !== 1) { console.log("(no single winner: the rest of the write checks are skipped)"); failures += 0; } else {
    const ref = owner.booked.body.appointment.reference;
    const repeat = await tool("book_appointment", { ...bookArgs(owner.who, first), confirmed: true, confirmation_token: (owner.who === mine ? readMine : readRival).body.confirmation_token }, owner.call);
    check("write: a repeated yes books nothing more", repeat.body?.status === "already_booked");
    const found = await tool("find_appointments", { phone: owner.who.phone, name: owner.who.name }, callId("lookup2"));
    check("4 find_appointments: the new client's booking is found", found.status === 200 && JSON.stringify(found.body).includes(ref), found.body?.status);
    check("5 last_completed_visit: a new client has none, said in words", friendly(await tool("last_completed_visit", { phone: owner.who.phone, name: owner.who.name, service_category: "massage" }, callId("history2"))));
    const shown = await get(`/pro/en-us/9000001/calendar?date=${tomorrow}`);
    check("write: the booking is on the staff calendar", shown.text.includes(`data-appointment-id="${ref}"`));

    // a move
    const moveArgs = { reference: ref, phone: owner.who.phone, name: owner.who.name, date: tomorrow, time: second, staff: TECH };
    const moveRead = await tool("reschedule_appointment", moveArgs, callId("move"));
    check("9 reschedule_appointment: the read-back saves nothing", moveRead.body?.status === "confirmation_required", moveRead.body?.status);
    const moved = await tool("reschedule_appointment", { ...moveArgs, confirmed: true, confirmation_token: moveRead.body?.confirmation_token }, callId("move"));
    check("9 reschedule_appointment: the yes moves it once", moved.body?.status === "rescheduled", moved.body?.status);
    if (moved.body?.status === "rescheduled") note("rescheduled", { reference: ref, to: second, day: tomorrow });

    // a preference saved, read, removed
    const prefArgs = { phone: owner.who.phone, name: owner.who.name, service: FAMILY, staff: TECH };
    const saveRead = await tool("save_preference", prefArgs, callId("prefs2"));
    check("10 save_preference: the read-back saves nothing", saveRead.body?.status === "confirmation_required", saveRead.body?.status);
    const saved = await tool("save_preference", { ...prefArgs, confirmed: true, confirmation_token: saveRead.body?.confirmation_token }, callId("prefs2"));
    check("10 save_preference: the yes saves it", saved.body?.status === "preference_saved", saved.body?.status);
    if (saved.body?.status === "preference_saved") note("preference saved", { client: owner.who.name, family: FAMILY, technician: TECH });
    const listed = await tool("get_preferences", { phone: owner.who.phone, name: owner.who.name, service: FAMILY }, callId("prefs2"));
    check("6 get_preferences: it is read back", listed.status === 200 && (listed.body?.speak ?? "").includes(TECH), listed.body?.status);
    const removeRead = await tool("remove_preference", { phone: owner.who.phone, name: owner.who.name, service: FAMILY }, callId("prefs3"));
    const removed = await tool("remove_preference", { phone: owner.who.phone, name: owner.who.name, service: FAMILY, confirmed: true, confirmation_token: removeRead.body?.confirmation_token }, callId("prefs3"));
    check("11 remove_preference: read back, then removed", removeRead.body?.status === "confirmation_required" && removed.body?.status === "preference_removed", `${removeRead.body?.status}, ${removed.body?.status}`);
    if (removed.body?.status === "preference_removed") note("preference removed", { client: owner.who.name, family: FAMILY });

    // a cancellation, and its repeat
    const cancelArgs = { reference: ref, phone: owner.who.phone, name: owner.who.name };
    const cancelRead = await tool("cancel_appointment", cancelArgs, callId("cancel"));
    const cancelled = await tool("cancel_appointment", { ...cancelArgs, confirmed: true, confirmation_token: cancelRead.body?.confirmation_token }, callId("cancel"));
    check("8 cancel_appointment: read back, then cancelled", cancelRead.body?.status === "confirmation_required" && cancelled.body?.status === "cancelled", `${cancelRead.body?.status}, ${cancelled.body?.status}`);
    if (cancelled.body?.status === "cancelled") note("cancelled", { reference: ref });
    const again = await tool("cancel_appointment", { ...cancelArgs, confirmed: true, confirmation_token: cancelRead.body?.confirmation_token }, callId("cancel"));
    check("8 cancel_appointment: a repeated yes cancels nothing more", ["already_cancelled", "cancelled"].includes(again.body?.status) && again.body?.status !== "error", again.body?.status);
    const gone = await get(`/pro/en-us/9000001/calendar?date=${tomorrow}`);
    check("write: the staff calendar no longer shows it as booked", !gone.text.includes(`data-appointment-id="${ref}"`) || /cancel/i.test(gone.text));
  }
}

// ---------------------------------------------------------------- after a long idle: the first database request
if (idleSeconds > 0) {
  console.log(`waiting ${idleSeconds} s for the database to go idle...`);
  await new Promise((resolve) => setTimeout(resolve, idleSeconds * 1000));
  const cold = await tool("check_slot", { service_id: SERVICE, date: tomorrow, time: "17:00", staff: TECH, service_words: WORDS }, callId("cold"));
  check("after the idle wait the first database request still answers", cold.status === 200 && ["available", "alternatives"].includes(cold.body?.status), `${cold.ms} ms (this is the cold-resume time)`);
}

const record = { run, base, wrote: write, mutations };
if (write) {
  console.log("\nTEST MUTATIONS MADE BY THIS RUN (all synthetic; cancelled history stays in the records):");
  for (const m of mutations) console.log("  " + JSON.stringify(m));
}
if (recordFile) await writeFile(recordFile, JSON.stringify(record, null, 2), "utf8");
console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
