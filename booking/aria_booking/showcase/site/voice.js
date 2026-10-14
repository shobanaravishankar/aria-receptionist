/* Speak to Aria: starts ONE voice call from a click, through the Retell Web SDK (v3) with a browser PUBLIC key. No backend, no access token, no secret.
 *
 *   new RetellClient({ key: publicKey }).createWebCall({ agent_id, hooks })
 *
 * The call is created from the visitor's click (never on page load), and the browser's own microphone prompt still applies. Real states only: no simulated
 * success and no canned greeting; Aria speaks first from the connected agent. The public settings come from demo-config.js (window.RETELL_DEMO).
 */

const SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm";
const cfg = (typeof window !== "undefined" && window.RETELL_DEMO) || {};

const panel = document.getElementById("voice-panel");
const statusText = panel.querySelector(".voice-status__text");
const statusRing = panel.querySelector(".voice-status__ring");
const endBtn = document.getElementById("voice-end");
const retryBtn = document.getElementById("voice-retry");
const closeBtn = panel.querySelector("[data-voice-close]");
const configBox = document.getElementById("voice-config");
const micNote = panel.querySelector(".voice-panel__mic");
const toastEl = document.getElementById("toast");

let sdkPromise = null;   // memoized dynamic import
let client = null;       // RetellClient (created once)
let call = null;         // active WebCallSession, or null
let starting = false;    // request in flight, not yet "live"
let everLive = false;    // did this call reach "live" before ending?
let failure = null;      // the specific message of a failed start or call: the "ended" event that follows an error must not replace it with the generic one
let lastFocus = null;

/* ---------------- helpers ---------------- */
function setStatus(state, message) {
  statusRing.dataset.state = state;
  statusText.textContent = message;
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
    return "Microphone blocked. Allow it for this site in your browser, then press “Speak to Aria” again.";
  if (/network|failed to fetch|load|timeout/i.test(raw))
    return "Network problem reaching Aria. Check the connection and try again. (" + raw + ")";
  return "Couldn’t start the call: " + raw;
}

/* ---------------- panel open / close ---------------- */
function openPanel() {
  if (panel.hidden) {
    lastFocus = document.activeElement;
    panel.hidden = false;
    document.body.style.overflow = "hidden";
  }
  if (!isConfigured()) {
    setStatus("error", "Voice isn’t configured yet.");
    configBox.open = true;
    endBtn.hidden = true;
    retryBtn.hidden = true;
    closeBtn.hidden = false;
  }
}

function closePanel() {
  panel.hidden = true;
  document.body.style.overflow = "";
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

/* ---------------- call lifecycle ---------------- */
async function startCall() {
  openPanel();
  if (!isConfigured()) return;
  if (call || starting) { toast("A call is already connecting."); return; }

  starting = true;
  failure = null;                  // a new attempt: forget the last failure
  document.documentElement.dataset.ariaCall = "1";
  endBtn.hidden = true;
  retryBtn.hidden = true;
  closeBtn.hidden = true;          // no accidental dismiss mid-connect
  setStatus("connecting", "Connecting to Aria…");
  micNote.textContent = micNote.dataset.idle || "";

  try {
    const mod = await loadSdk();
    const RetellClient = mod.RetellClient || (mod.default && mod.default.RetellClient);
    if (!RetellClient) throw new Error("Retell SDK failed to load");
    if (!client) client = new RetellClient({ key: cfg.publicKey });

    const options = {
      agent_id: cfg.agentId,
      hooks: {
        onStatus: onStatus,
        onEnd: onEnd,
        onError: onError,
        onAgentStartTalking: () => { if (call) setStatus("live", "Aria is speaking…"); },
        onAgentStopTalking: () => { if (call) setStatus("live", "Listening…"); },
      },
    };
    if (Number.isFinite(cfg.agentVersion)) options.agent_version = cfg.agentVersion;
    if (cfg.recaptchaToken) options.recaptchaToken = cfg.recaptchaToken;

    call = client.createWebCall(options);   // returns immediately; progress arrives through the hooks
  } catch (err) {
    fail(err);
  }
}

function onStatus(status) {
  if (!call) return;
  if (status === "connecting") {
    setStatus("connecting", "Connecting to Aria…");
  } else if (status === "live") {
    starting = false;
    everLive = true;
    setStatus("live", "Connected — say hello to Aria.");
    endBtn.hidden = false;
    retryBtn.hidden = true;
    closeBtn.hidden = false;
    micNote.textContent = micNote.dataset.live || "";
    try { call.startAudioPlayback && call.startAudioPlayback(); } catch (_) {}
    toast("You’re connected to Aria.");
  }
  // "ended" is handled by onEnd
}

function onEnd() {
  if (failure) {
    // An error already said why this call failed (and showed it). Retell also fires "ended" afterwards: keep the specific message.
    reset();
    setStatus("error", failure);
    retryBtn.textContent = "Try again";
    retryBtn.hidden = false;
    closeBtn.hidden = false;
    return;
  }
  const connected = everLive;
  reset();
  if (connected) {
    setStatus("ended", "Call ended. Press “Speak to Aria” to talk again.");
    retryBtn.textContent = "Talk to Aria again";
  } else {
    setStatus("error", "The call didn’t connect. This is usually the microphone being blocked, or this site’s address not being on the Retell public key’s allowed domains. Check both, then try again.");
    retryBtn.textContent = "Try again";
  }
  retryBtn.hidden = false;
  closeBtn.hidden = false;
}

function onError(err) {
  fail(err);
}

function fail(err) {
  const message = friendlyError(err);
  failure = message;
  reset();
  setStatus("error", message);
  retryBtn.hidden = false;
  retryBtn.textContent = "Try again";
  closeBtn.hidden = false;
}

function reset() {
  starting = false;
  everLive = false;
  call = null;
  endBtn.hidden = true;
  delete document.documentElement.dataset.ariaCall;
}

async function endCall() {
  if (!call) { onEnd(); return; }
  endBtn.disabled = true;
  setStatus("connecting", "Ending the call…");
  try { await call.end(); } catch (_) { /* onEnd/onError will still fire */ }
  endBtn.disabled = false;
  // Safety net if no event arrives:
  setTimeout(() => { if (call) onEnd(); }, 1500);
}

/* ---------------- wire up ---------------- */
document.querySelectorAll("[data-voice-start]").forEach((b) => b.addEventListener("click", startCall));

closeBtn.addEventListener("click", async () => {
  if (call || starting) { await endCall(); }
  closePanel();
});
document.querySelectorAll(".voice-panel__scrim").forEach((b) => b.addEventListener("click", async () => {
  if (call || starting) return;   // never dismiss the panel by accident mid-call
  closePanel();
}));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !panel.hidden && !call && !starting) closePanel();
});

endBtn.addEventListener("click", endCall);
retryBtn.addEventListener("click", startCall);

// A denied microphone can surface as an unhandled rejection; route it to the visible error path
window.addEventListener("unhandledrejection", (e) => {
  const r = e && e.reason;
  const text = ((r && r.name) || "") + " " + ((r && r.message) || String(r || ""));
  if ((call || starting) && /notallowed|permission|denied/i.test(text)) {
    e.preventDefault();
    fail(r);
    try { call && call.end(); } catch (_) {}
  }
});

window.addEventListener("beforeunload", () => { try { call && call.end(); } catch (_) {} });

if (!isConfigured()) {
  document.querySelectorAll("[data-voice-start]").forEach((b) => {
    b.title = "Voice is not configured yet: the page needs the public key and the agent id in demo-config.js";
  });
}
