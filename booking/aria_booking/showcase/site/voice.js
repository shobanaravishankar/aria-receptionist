/* Aria, the voice assistant: ONE voice call from a click, through the Retell Web SDK (v3) with a browser PUBLIC key. No backend, no access token, no secret.
 *
 *   new RetellClient({ key: publicKey }).createWebCall({ agent_id, hooks })
 *
 * The call is created from the visitor's click (never on page load), and the browser's own microphone prompt still applies. Real states only: no simulated
 * success, no canned greeting and no pretend speech animation; Aria speaks first from the connected agent, and the avatar's "speaking" glow follows the SDK's own
 * agent_start_talking / agent_stop_talking events. The public settings come from demo-config.js (window.RETELL_DEMO).
 *
 * The widget is a small floating avatar with two separate icon controls (Call, Hang up) and a hide control. Hiding never ends a call: while a call is on and the
 * avatar is put away, a call bar says so and can bring it back or end the call. The page is never navigated, so the call survives browsing.
 */

const SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm";
const cfg = (typeof window !== "undefined" && window.RETELL_DEMO) || {};

const widget = document.getElementById("aria");
const statusBox = document.getElementById("voice-status");
const statusText = statusBox.querySelector(".aria__text");
const callBtn = document.getElementById("aria-call");
const hangBtn = document.getElementById("aria-hangup");
const hideBtn = document.getElementById("aria-hide");
const toastEl = document.getElementById("toast");
const bar = document.getElementById("call-bar");
const barText = document.getElementById("call-bar-text");
const barShow = document.getElementById("call-show");
const barEnd = document.getElementById("call-end");

let sdkPromise = null;   // memoized dynamic import
let client = null;       // RetellClient (created once)
let call = null;         // active WebCallSession, or null
let starting = false;    // request in flight, not yet "live"
let run = null;          // the call ATTEMPT in progress, {over, ending}: every SDK event, every pending hang-up and every timer belongs to ONE attempt, and is ignored once that attempt is over or a newer one has begun
let endTimer = null;     // the safety net after a hang-up; it belongs to the attempt that set it
let everLive = false;    // did this call reach "live" before ending?
let failure = null;      // the specific message of a failed start or call: the "ended" event that follows an error must not replace it with the generic one
let clearTimer = null;

/* ---------------- helpers ---------------- */
function inCall() {
  return Boolean(call || starting);
}

/* The two controls are separate: Call while there is no call, Hang up while there is one (or one is being set up). */
function syncControls() {
  const active = inCall();
  const hadFocus = document.activeElement === callBtn || document.activeElement === hangBtn;
  callBtn.hidden = active;
  hangBtn.hidden = !active;
  hangBtn.disabled = Boolean(run && run.ending);   // only while THIS attempt is hanging up: the next call's control is never held by the last call's teardown
  callBtn.setAttribute("aria-label", widget.dataset.state === "error" ? "Try calling Aria again" : "Call Aria");
  if (hadFocus && !widget.hidden) (active ? hangBtn : callBtn).focus({ preventScroll: true });
  if (bar) bar.hidden = !(active && widget.hidden);
}

function setStatus(state, message) {
  clearTimeout(clearTimer);
  widget.dataset.state = state;
  statusText.textContent = message;
  statusBox.hidden = !message;
  if (barText) barText.textContent = "Aria · " + message;
  if (state !== "live") delete widget.dataset.speaking;
  syncControls();
}

/* "Call ended." is shown for a moment and then the widget returns to rest. Errors stay until the next try. */
function settleLater() {
  clearTimeout(clearTimer);
  clearTimer = setTimeout(() => { if (!inCall() && widget.dataset.state === "ended") setStatus("idle", ""); }, 8000);
}

function setSpeaking(on) {
  if (!call) return;
  if (on) { widget.dataset.speaking = "true"; setStatus("live", "Aria is speaking…"); }
  else { delete widget.dataset.speaking; setStatus("live", "Listening…"); }
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.hidden = true; }, 3600);
}

function isConfigured() {
  return Boolean(cfg.publicKey && cfg.agentId);
}

function loadSdk() {
  if (!sdkPromise) sdkPromise = import(SDK_URL);
  return sdkPromise;
}

function friendlyError(err) {
  const raw = (err && (err.message || String(err))) || "Unknown error";
  // Domain and key problems first: "origin not allowed (403)" contains "not allowed" and must not be reported as a blocked microphone.
  if (/origin|domain|forbidden|unauthori[sz]ed|\b40[13]\b/i.test(raw))
    return "This web address isn’t on the Retell key’s allowed domains yet — add it in Retell → API Keys → Public Keys, then try again. (" + raw + ")";
  if (/denied|permission|notallowed|not allowed/i.test(raw))
    return "Microphone blocked. Allow it for this site in your browser, then press Call again.";
  if (/network|failed to fetch|load|timeout/i.test(raw))
    return "Network problem reaching Aria. Check the connection and try again. (" + raw + ")";
  return "Couldn’t start the call: " + raw;
}

/* ---------------- show / hide ---------------- */
function showWidget() {
  widget.hidden = false;
  syncControls();
}

function hideWidget() {
  const focusWasHere = widget.contains(document.activeElement);
  widget.hidden = true;
  syncControls();
  if (focusWasHere) {
    const next = !bar.hidden ? barShow : document.querySelector("[data-voice-start]:not(#aria-call)");
    if (next) { try { next.focus({ preventScroll: true }); } catch (_) {} }
  }
}

/* ---------------- call lifecycle ---------------- */
async function startCall() {
  const wasHidden = widget.hidden;
  showWidget();
  if (!isConfigured()) {
    setStatus("error", "Voice isn’t set up on this page yet.");
    return;
  }
  if (inCall()) {   // a live call just comes back to the front; one that is still connecting says so. Never a second call.
    if (!everLive) toast("A call is already connecting.");
    if (wasHidden) (hangBtn.hidden ? callBtn : hangBtn).focus({ preventScroll: true });
    return;
  }

  const mine = { over: false, ending: false };
  run = mine;
  clearTimeout(endTimer);
  starting = true;
  failure = null;                  // a new attempt: forget the last failure
  document.documentElement.dataset.ariaCall = "1";
  setStatus("connecting", "Connecting to Aria…");

  try {
    const mod = await loadSdk();
    if (run !== mine || mine.over) return;   // hung up while the SDK loaded (and perhaps called again since): only the newest attempt may create a call
    const RetellClient = mod.RetellClient || (mod.default && mod.default.RetellClient);
    if (!RetellClient) throw new Error("Retell SDK failed to load");
    if (!client) client = new RetellClient({ key: cfg.publicKey });

    const own = (fn) => (...args) => { if (run === mine && !mine.over) fn(...args); };   // a late or repeated event from an older call cannot touch this one
    const options = {
      agent_id: cfg.agentId,
      hooks: {
        onStatus: own(onStatus),
        onEnd: own(onEnd),
        onError: own(onError),
        onAgentStartTalking: own(() => setSpeaking(true)),
        onAgentStopTalking: own(() => setSpeaking(false)),
      },
    };
    if (Number.isFinite(cfg.agentVersion)) options.agent_version = cfg.agentVersion;
    if (cfg.recaptchaToken) options.recaptchaToken = cfg.recaptchaToken;

    call = client.createWebCall(options);   // returns immediately; progress arrives through the hooks
    syncControls();
  } catch (err) {
    if (run === mine && !mine.over) fail(err);
  }
}

function onStatus(status) {
  if (!call) return;
  if (status === "connecting") {
    setStatus("connecting", "Connecting to Aria…");
  } else if (status === "live") {
    starting = false;
    everLive = true;
    setStatus("live", "Listening…");
    try { call.startAudioPlayback && call.startAudioPlayback(); } catch (_) {}
    toast("You’re connected to Aria.");
  }
  // "ended" is handled by onEnd
}

function onEnd() {
  if (widget.hidden) showWidget();   // the call ended while the avatar was put away: show how it ended
  if (failure) {
    // An error already said why this call failed (and showed it). Retell also fires "ended" afterwards: keep the specific message.
    reset();
    setStatus("error", failure);
    return;
  }
  const connected = everLive;
  reset();
  if (connected) {
    setStatus("ended", "Call ended.");
    settleLater();
  } else {
    setStatus("error", "The call didn’t connect. This is usually the microphone being blocked, or this site’s address not being on the Retell public key’s allowed domains. Check both, then try again.");
  }
}

function onError(err) {
  fail(err);
}

function fail(err) {
  if (widget.hidden) showWidget();
  const message = friendlyError(err);
  failure = message;
  reset();
  setStatus("error", message);
}

function reset() {
  if (run) run.over = true;   // whatever this attempt says from now on (a repeated "ended", a late timer) is ignored
  run = null;
  clearTimeout(endTimer);
  starting = false;
  everLive = false;
  call = null;
  delete document.documentElement.dataset.ariaCall;
  delete widget.dataset.speaking;
  syncControls();
}

async function endCall() {
  if (!call) {
    if (starting) {   // still loading the SDK: nothing exists to end; that attempt is over, so nothing will be created for that click
      if (run) run.over = true;
      run = null;
      starting = false;
      delete document.documentElement.dataset.ariaCall;
      setStatus("idle", "");
      return;
    }
    onEnd();
    return;
  }
  const mine = run;
  const ending = call;
  if (!mine) { onEnd(); return; }
  if (mine.ending) return;   // already hanging up: a second click on either Hang up control does nothing more
  mine.ending = true;
  setStatus("connecting", "Ending the call…");
  try { await ending.end(); } catch (_) { /* onEnd/onError will still fire */ }
  // This continuation may resume long after the call it belongs to has finished and another has begun: from here it may touch shared state only while it is still current.
  if (run !== mine || mine.over) return;
  clearTimeout(endTimer);
  // Safety net if no event arrives: only for THIS call, and only if it is still the current one when the time is up
  endTimer = setTimeout(() => { if (run === mine && !mine.over && call) onEnd(); }, 1500);
}

/* ---------------- wire up ---------------- */
document.querySelectorAll("[data-voice-start]").forEach((b) => b.addEventListener("click", startCall));
hangBtn.addEventListener("click", endCall);
hideBtn.addEventListener("click", hideWidget);   // hiding never ends a call
if (barShow) barShow.addEventListener("click", () => { showWidget(); (hangBtn.hidden ? callBtn : hangBtn).focus({ preventScroll: true }); });
if (barEnd) barEnd.addEventListener("click", () => { endCall(); });

// A denied microphone can surface as an unhandled rejection; route it to the visible error path
window.addEventListener("unhandledrejection", (e) => {
  const r = e && e.reason;
  const text = ((r && r.name) || "") + " " + ((r && r.message) || String(r || ""));
  if (inCall() && /notallowed|permission|denied/i.test(text)) {
    e.preventDefault();
    const active = call;   // fail() lets go of the call: take hold of it first so it can be ended
    fail(r);
    try { active && active.end(); } catch (_) {}
  }
});

window.addEventListener("beforeunload", () => { try { call && call.end(); } catch (_) {} });

syncControls();
if (!isConfigured()) {
  document.querySelectorAll("[data-voice-start]").forEach((b) => {
    b.title = "Voice is not set up on this page yet: it needs the public key and the agent id in demo-config.js";
  });
}
