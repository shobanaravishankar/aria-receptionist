/* Aria voice assistant wiring for the Glamour Day Spa demo.
 *
 * Route: Retell's official public-key WEBSITE WIDGET (no backend, no private key).
 * Docs: https://docs.retellai.com/deploy/chat-widget
 *       https://docs.retellai.com/accounts/public-keys
 *
 * This script:
 *   - opens a small panel from any "Speak to an agent" button
 *   - loads the Retell widget on first use, from demo-config.js values
 *     (or from a full embed snippet you pasted into demo-config.js / index.html)
 *   - reports real states; it never fakes a successful call or a canned answer
 */

const WIDGET_SRC = "https://dashboard.retellai.com/retell-widget-v2.js";
const cfg = (typeof window !== "undefined" && window.RETELL_DEMO) || {};

const panel = document.getElementById("voice-panel");
const statusText = panel.querySelector(".voice-status__text");
const statusRing = panel.querySelector(".voice-status__ring");
const primaryBtn = document.getElementById("voice-primary");
const endBtn = document.getElementById("voice-end");
const configBox = document.getElementById("voice-config");
const toastEl = document.getElementById("toast");

let widgetRequested = false;
let widgetReady = false;
let lastFocus = null;

function setStatus(state, message) {
  statusRing.dataset.state = state;
  statusText.textContent = message;
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.hidden = true; }, 3200);
}

function isConfigured() {
  return Boolean((cfg.publicKey && cfg.agentId) || cfg.widgetEmbed || document.getElementById("retell-widget"));
}

/* ---------- panel open / close ---------- */
function openPanel() {
  lastFocus = document.activeElement;
  panel.hidden = false;
  document.body.style.overflow = "hidden";

  if (!isConfigured()) {
    setStatus("error", "Voice isn’t configured yet.");
    primaryBtn.disabled = true;
    configBox.open = true;
    primaryBtn.focus();
    return;
  }

  primaryBtn.disabled = false;
  if (widgetReady) {
    setStatus("idle", "Aria is ready. Press start to talk.");
  } else {
    setStatus("connecting", "Connecting you to Aria…");
    injectWidget(); // start loading right away so it's one click, not two
  }
  primaryBtn.focus();
}

function closePanel() {
  panel.hidden = true;
  document.body.style.overflow = "";
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

/* ---------- widget loading ---------- */
function injectWidget() {
  if (widgetRequested) return;
  widgetRequested = true;
  setStatus("connecting", "Loading Aria…");

  // 1) Full embed snippet pasted into config: use it verbatim.
  if (cfg.widgetEmbed && cfg.widgetEmbed.trim()) {
    const holder = document.createElement("div");
    holder.innerHTML = cfg.widgetEmbed.trim();
    const node = holder.querySelector("script") || holder.firstElementChild;
    if (node) {
      const s = document.createElement("script");
      for (const a of node.attributes) s.setAttribute(a.name, a.value);
      s.textContent = node.textContent || "";
      attachLoadHandlers(s);
      document.body.appendChild(s);
      return;
    }
  }

  // 2) An embed already present in index.html.
  const existing = document.getElementById("retell-widget");
  if (existing) { onWidgetReady(); return; }

  // 3) Build the widget tag from config values.
  const s = document.createElement("script");
  s.id = "retell-widget";
  s.src = WIDGET_SRC;
  s.type = "module";
  s.setAttribute("data-voice-public-key", cfg.publicKey);
  s.setAttribute("data-voice-agent-id", cfg.agentId);
  if (cfg.agentVersion != null) s.setAttribute("data-agent-version", String(cfg.agentVersion));
  if (cfg.recaptchaSiteKey) s.setAttribute("data-recaptcha-key", cfg.recaptchaSiteKey);
  s.setAttribute("data-fab-text", "Speak to an agent");
  s.setAttribute("data-color", "#0e3b35");
  s.setAttribute("data-theme-color", "#c0863c");
  attachLoadHandlers(s);
  document.body.appendChild(s);
}

function attachLoadHandlers(scriptEl) {
  scriptEl.addEventListener("load", () => onWidgetReady());
  scriptEl.addEventListener("error", () => {
    setStatus("error", "Could not load the voice assistant. Check your connection and try again.");
    primaryBtn.disabled = false;
    primaryBtn.textContent = "Try again";
  });
  // Fallback: the widget script may be a module that resolves without a load event we catch.
  setTimeout(() => { if (!widgetReady) onWidgetReady(true); }, 4000);
}

function onWidgetReady(soft) {
  if (widgetReady) return;
  widgetReady = true;
  setStatus("idle", "Aria is ready. Press start to talk.");
  primaryBtn.disabled = false;
  primaryBtn.innerHTML = '<span class="btn-voice__dot" aria-hidden="true"></span> Start voice call';
  if (!soft) tryOpenWidget();
}

/* ---------- open the widget's own call UI ---------- */
const FAB_SELECTORS = [
  'button[aria-label="Open Assistant"]',
  'button[aria-label*="assistant" i]',
  'button[aria-label*="agent" i]',
  'button[class*="_fabBase" i]',
  'button[class*="fab" i]',
  "#retell-widget-fab", ".retell-widget__fab", ".retell-widget-launcher",
  '[class*="retell"][class*="fab"]',
];
const END_SELECTORS = [
  'button[aria-label*="end" i]', 'button[aria-label*="hang" i]',
  'button[aria-label*="stop" i]', 'button[aria-label*="close" i]',
  '[class*="end" i] button', 'button[class*="end" i]', 'button[class*="hangup" i]',
];

function deepQuery(selectors, root = document, depth = 0) {
  for (const sel of selectors) {
    try {
      const hit = root.querySelector && root.querySelector(sel);
      if (hit) return hit;
    } catch (_) { /* invalid selector in this root */ }
  }
  if (depth > 8) return null;
  const els = root.querySelectorAll ? root.querySelectorAll("*") : [];
  for (const el of els) {
    if (el.shadowRoot) {
      const hit = deepQuery(selectors, el.shadowRoot, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

function deepFindByText(re, root = document, depth = 0) {
  const nodes = root.querySelectorAll ? root.querySelectorAll("button,[role=button],a") : [];
  for (const n of nodes) {
    if (re.test((n.getAttribute("aria-label") || "") + " " + (n.textContent || ""))) return n;
  }
  if (depth > 8) return null;
  for (const el of (root.querySelectorAll ? root.querySelectorAll("*") : [])) {
    if (el.shadowRoot) {
      const hit = deepFindByText(re, el.shadowRoot, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

function tryOpenWidget() {
  setStatus("connecting", "Opening Aria…");

  const api = window.RetellWidget || window.retellWidget || window.RetellAIWidget;
  if (api && typeof api.open === "function") {
    try { api.open(); startedHint(); return; } catch (_) { /* fall through */ }
  }

  const launcher =
    deepQuery(FAB_SELECTORS) ||
    deepFindByText(/open assistant|speak to an agent|talk to|start (voice )?call/i);

  if (launcher) {
    try {
      launcher.click();
      startedHint();
      return;
    } catch (_) { /* fall through */ }
  }

  // Could not drive it programmatically — point the user at the widget's own button.
  setStatus("live", "Aria is loaded. Tap the round “Speak to an agent” button at the bottom-right to start, then allow the microphone.");
  spotlightCorner();
}

function startedHint() {
  setStatus("live", "Connecting you to Aria — allow microphone access when asked.");
  endBtn.hidden = false;
  toast("Aria is starting. Allow the microphone to talk.");
  // hand over to the widget's own call UI
  setTimeout(() => { if (!panel.hidden) closePanel(); }, 900);
}

function spotlightCorner() {
  const dot = document.createElement("div");
  dot.setAttribute("aria-hidden", "true");
  Object.assign(dot.style, {
    position: "fixed", right: "18px", bottom: "18px", width: "72px", height: "72px",
    borderRadius: "50%", border: "3px solid #c0863c", pointerEvents: "none", zIndex: 199,
    boxShadow: "0 0 0 0 rgba(192,134,60,.6)", animation: "pulse 1.6s 4",
  });
  document.body.appendChild(dot);
  setTimeout(() => dot.remove(), 7000);
}

/* ---------- events ---------- */
document.querySelectorAll("[data-voice-start]").forEach((b) =>
  b.addEventListener("click", openPanel)
);
document.querySelectorAll("[data-voice-close]").forEach((b) =>
  b.addEventListener("click", closePanel)
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !panel.hidden) closePanel();
});

primaryBtn.addEventListener("click", () => {
  if (!isConfigured()) { configBox.open = true; return; }
  if (!widgetRequested) injectWidget();
  else if (widgetReady) tryOpenWidget();
});

endBtn.addEventListener("click", () => {
  const api = window.RetellWidget || window.retellWidget;
  if (api && typeof api.close === "function") { try { api.close(); } catch (_) {} }
  const closeBtn = deepQuery(END_SELECTORS) || deepFindByText(/end call|hang up|stop/i);
  if (closeBtn) { try { closeBtn.click(); } catch (_) {} }
  endBtn.hidden = true;
  setStatus("ended", "Call ended. Press start to talk again.");
});

document.querySelectorAll("[data-demo-booking]").forEach((b) =>
  b.addEventListener("click", () => toast("Online booking isn’t part of this demo — ask Aria about services instead."))
);

/* initial state */
if (!isConfigured()) {
  document.querySelectorAll("[data-voice-start]").forEach((b) => {
    b.title = "Add your Retell keys in demo-config.js to enable voice";
  });
}
