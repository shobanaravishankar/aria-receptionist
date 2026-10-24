/* The Glamour Day Spa information assistant page: typed chat (through the server relay) and voice (the Retell browser SDK with the PUBLIC key) in ONE conversation area.
 *
 *  - Nothing starts by itself: no microphone, no call and no chat session until the visitor presses Send or Talk to Aria.
 *  - Everything shown is put on the page as text (textContent), never as HTML. Only the salon's own web address and phone number become links.
 *  - One request or call at a time: a repeated click, a repeated Enter or a late answer from an older attempt cannot start a second one or change the screen.
 *  - Nothing is stored in the browser. Typed chat and a voice call are separate conversations with the same assistant and the same information; the page says so.
 *  - Every failure is said in plain words with the salon's phone number; no technical text reaches the visitor. */

const cfg = window.GLAMOUR || {};
const PHONE = cfg.salonPhone || "(908) 668-8828";
const el = (id) => document.getElementById(id);
const logEl = el("log"), statusEl = el("status"), formEl = el("form"), inputEl = el("text"), sendBtn = el("send"), talkBtn = el("talk"), hintEl = el("voice-hint");
const SITE_URL = /https:\/\/www\.warrenglamourdayspanj\.com\/[A-Za-z0-9\-_/]*/g;
const FALLBACK = `I can't answer that right now. Please call the salon at ${PHONE} or use the Book Online page on the website.`;

/* ---------------------------------------------------------------- the conversation area */
function linkify(parent, text) {
  const pattern = new RegExp(`${SITE_URL.source}|${PHONE.replace(/[()]/g, "\\$&")}`, "g");
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const a = document.createElement("a");
    a.textContent = m[0];
    if (m[0] === PHONE) a.href = `tel:+1${PHONE.replace(/\D/g, "")}`;
    else { a.href = m[0]; a.rel = "noopener"; }
    parent.append(a);
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}
function addMessage(who, text, opts = {}) {
  const p = document.createElement("p");
  p.className = `msg msg--${who}${opts.error ? " msg--error" : ""}`;
  if (opts.tag) {
    const tag = document.createElement("span");
    tag.className = "msg__tag";
    tag.textContent = opts.tag;
    p.append(tag);
  }
  linkify(p, text);
  logEl.append(p);
  logEl.scrollTop = logEl.scrollHeight;
  return p;
}
function setStatus(state, text) {
  statusEl.dataset.state = state;
  statusEl.textContent = text;
}

/* ---------------------------------------------------------------- typed chat */
const chat = { token: null, busy: false };
async function api(path, body) {
  const response = await fetch(`${cfg.chatApi || "api/chat"}/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), credentials: "omit", cache: "no-store" });
  let data = null;
  try { data = await response.json(); } catch (_) { /* not JSON */ }
  return { status: response.status, data };
}
const refusal = (r) => Object.assign(new Error("chat"), { userMessage: (r && r.data && typeof r.data.message === "string" && r.data.message) || FALLBACK, code: r && r.data && r.data.error });

async function startChat() {
  const r = await api("start", {});
  if (r.status !== 201 || !r.data || typeof r.data.token !== "string") throw refusal(r);
  chat.token = r.data.token;
}
async function ask(text) {
  if (chat.busy) return;   // one question at a time: a second Enter or click while one is on its way does nothing
  chat.busy = true;
  setBusy(true);
  addMessage("you", text);
  const typing = addMessage("aria", "...", { tag: "Aria is typing" });
  try {
    if (!chat.token) await startChat();
    let r = await api("message", { token: chat.token, text });
    if (r.status === 401) {   // the chat expired: begin a new one and say so
      chat.token = null;
      await startChat();
      addMessage("note", "That chat had expired, so I started a new one.");
      r = await api("message", { token: chat.token, text });
    }
    if (r.status !== 200 || !r.data || typeof r.data.reply !== "string" || !r.data.reply) throw refusal(r);
    chat.token = r.data.token;
    typing.remove();
    addMessage("aria", r.data.reply);
  } catch (error) {
    typing.remove();
    if (error && error.code === "chat_full") chat.token = null;   // the next question opens a fresh chat
    addMessage("aria", (error && error.userMessage) || FALLBACK, { error: true });
    inputEl.value = text;   // nothing the visitor typed is lost
  } finally {
    chat.busy = false;
    setBusy(false);
    inputEl.focus({ preventScroll: true });
  }
}
function setBusy(busy) {
  sendBtn.disabled = busy;
  formEl.setAttribute("aria-busy", busy ? "true" : "false");
}
formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text || chat.busy) return;
  inputEl.value = "";
  ask(text);
});

/* ---------------------------------------------------------------- voice */
const voice = { state: "idle", client: null, call: null, run: null, nodes: [] };   // idle | loading | connecting | live | ended | error
function setVoice(state) {
  voice.state = state;
  const active = state === "loading" || state === "connecting" || state === "live";
  talkBtn.setAttribute("aria-pressed", active ? "true" : "false");
  talkBtn.textContent = state === "live" ? "End call" : active ? "Cancel" : "Talk to Aria";
  talkBtn.disabled = false;
  hintEl.hidden = active;
  document.documentElement.dataset.voice = state;
}
const classify = (err) => (err && /permission|denied|notallowed|microphone|media|device/i.test(`${err.name || ""} ${err.message || ""}`) ? "microphone" : "other");

async function startVoice() {
  if (voice.state === "loading" || voice.state === "connecting" || voice.state === "live") return;   // never a second call
  if (!cfg.retellPublicKey || !cfg.voiceAgentId) {
    addMessage("aria", `Voice isn't available right now. You can type your question here, or call the salon at ${PHONE}.`, { error: true });
    setStatus("error", "Voice is not available.");
    return;
  }
  const mine = { over: false };
  voice.run = mine;
  voice.nodes = [];
  setVoice("loading");
  setStatus("connecting", "Connecting to Aria...");
  try {
    const mod = await import(cfg.sdkUrl);
    if (mine.over) return;   // cancelled while the SDK loaded
    const RetellClient = mod.RetellClient || (mod.default && mod.default.RetellClient);
    if (!RetellClient) throw new Error("sdk");
    if (!voice.client) voice.client = new RetellClient({ key: cfg.retellPublicKey });
    const own = (fn) => (...args) => { if (voice.run === mine && !mine.over) fn(...args); };   // a late event from an older call cannot touch this one
    setVoice("connecting");
    const options = {
      agent_id: cfg.voiceAgentId,
      transcript: true,
      hooks: {
        onStatus: own((status) => {
          if (status === "live") {
            setVoice("live");
            setStatus("live", "Listening...");
            addMessage("note", "Voice call started. Your typed chat and this call are separate conversations; both answer from the same salon information.");
            try { voice.call && voice.call.startAudioPlayback && voice.call.startAudioPlayback(); } catch (_) { /* optional */ }
          }
        }),
        onAgentStartTalking: own(() => setStatus("live", "Aria is speaking...")),
        onAgentStopTalking: own(() => setStatus("live", "Listening...")),
        onTranscript: own((t) => showTranscript(t)),
        onEnd: own(() => finishVoice(mine, "Call ended.")),
        onError: own((err) => failVoice(mine, err)),
      },
    };
    if (Number.isFinite(cfg.voiceAgentVersion)) options.agent_version = cfg.voiceAgentVersion;
    voice.call = voice.client.createWebCall(options);
  } catch (err) {
    if (!mine.over) failVoice(mine, err);
  }
}
function endVoice() {
  const mine = voice.run;
  if (!mine) return;
  const call = voice.call;
  try { call && call.end && call.end(); } catch (_) { /* already over */ }
  finishVoice(mine, "Call ended.");
}
function finishVoice(mine, text) {
  if (mine.over) return;
  mine.over = true;
  voice.run = null;
  voice.call = null;
  setVoice("ended");
  setStatus("idle", text);
}
function failVoice(mine, err) {
  if (mine.over) return;
  const kind = classify(err);
  mine.over = true;
  voice.run = null;
  try { voice.call && voice.call.end && voice.call.end(); } catch (_) { /* nothing to end */ }
  voice.call = null;
  setVoice("error");
  setStatus("error", kind === "microphone" ? "I couldn't use your microphone." : "I couldn't connect the call.");
  addMessage("aria", kind === "microphone"
    ? "I couldn't use your microphone. Please allow microphone access in your browser, or type your question here, or call the salon at " + PHONE + "."
    : "I couldn't connect the call. Please try again, type your question here, or call the salon at " + PHONE + ".", { error: true });
}
/* The transcript of the call, in the same conversation area. The shape is [{ role, content }, ...] (the whole call so far) or a single such object; each line has its own place, so an updated
 * transcript replaces earlier lines instead of repeating them. */
function showTranscript(t) {
  const lines = Array.isArray(t) ? t : t && typeof t === "object" ? [t] : [];
  lines.forEach((line, i) => {
    if (!line || typeof line.content !== "string" || !line.content.trim()) return;
    const who = line.role === "user" ? "you" : "aria";
    let node = voice.nodes[i];
    if (!node) {
      node = addMessage(who, "", { tag: "By voice" });
      voice.nodes[i] = node;
    }
    node.replaceChildren();
    const tag = document.createElement("span");
    tag.className = "msg__tag";
    tag.textContent = "By voice";
    node.append(tag);
    linkify(node, line.content.trim());
  });
  logEl.scrollTop = logEl.scrollHeight;
}
talkBtn.addEventListener("click", () => {
  if (voice.state === "idle" || voice.state === "ended" || voice.state === "error") startVoice();
  else endVoice();   // "Cancel" while connecting, "End call" while live
});
window.addEventListener("pagehide", () => { if (voice.run) endVoice(); });

/* ---------------------------------------------------------------- start: a greeting that costs nothing (no request, no microphone) */
addMessage("aria", cfg.greeting || "Hi, I'm Aria, the virtual assistant for Glamour Day Spa. Ask me about our services, hours or location.");
setVoice("idle");
setStatus("idle", "");
window.__glamourReady = true;
