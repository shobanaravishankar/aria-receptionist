/* The typed-chat backend for the Glamour Day Spa information assistant: a thin, defensive relay between the website's chat box and Retell's chat API. No dependencies; Node 22+.
 *
 * Why a relay at all: Retell's documented chat API needs the account's SECRET key, which must never reach a browser. The relay holds it (a server-side environment variable), starts a chat on the
 * customer's chat agent, passes each typed message on, and returns only the agent's words. It keeps no database and no log of message content.
 *
 * Session = a signed token (HMAC-SHA-256 under a key derived from the Retell key; nothing else to configure) holding the Retell chat id, an expiry and the number of messages so far. Each reply
 * carries the next token. A token cannot be forged or edited, and it EXPIRES: that expiry, the length limit on each message and the proper-origin check are enforced by the server.
 * The "number of messages so far" is NOT enforceable: the relay keeps no state, so the same token can be replayed within its lifetime (an old n=0 token is accepted again and again), and two
 * requests carrying the same n can both reach the provider. MAX_MESSAGES therefore only ends the conversation of a visitor who follows the tokens in order; it is a cooperative limit on one chat's
 * flow, never a cap on messages or on spend. What actually bounds use: the token lifetime, the per-visitor rate limits below (per warm instance, best effort) and Retell's own limits. See COSTS.md.
 *
 * Every provider failure becomes a short, truthful, non-technical answer that points to the phone number; no provider text, key, id or stack ever reaches the visitor. */

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const MAX_MESSAGES = 20; // a COOPERATIVE limit on one chat's flow (see the note below): not a cap on messages or on spend
export const MAX_TEXT = 500; // characters per typed message
export const MAX_BODY = 4096; // bytes of a request body
export const TOKEN_TTL_MS = 30 * 60_000; // a chat lasts at most half an hour
export const PROVIDER_TIMEOUT_MS = 20_000;
export const WINDOW_MS = 10 * 60_000;
export const MAX_STARTS_PER_WINDOW = 6; // new chats per visitor per window
export const MAX_MESSAGES_PER_WINDOW = 40; // typed messages per visitor per window

export interface Env {
  RETELL_API_KEY?: string;
  GLAMOUR_CHAT_AGENT_ID?: string;
  RETELL_API_BASE?: string; // default https://api.retellai.com (tests point it at a local fake)
  ALLOWED_ORIGINS?: string; // space separated extra origins (the hosted page's own origin is always allowed)
  SALON_PHONE?: string; // spoken in failure answers; default below
}
export interface ChatRequest {
  method: string;
  path: string;
  headers: Record<string, string>; // lower-case
  body: string;
  ip: string;
  url: string; // the full request URL (for the page's own origin)
}
export interface ChatResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}
export interface Deps {
  fetch: typeof fetch;
  now: () => number;
  log?: (message: string) => void;
  timeoutMs?: number; // the provider's time limit (tests shorten it)
}

const DEFAULT_PHONE = "(908) 668-8828";
const HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-content-type-options": "nosniff" };
const reply = (status: number, data: unknown, extra: Record<string, string> = {}): ChatResponse => ({ status, headers: { ...HEADERS, ...extra }, body: JSON.stringify(data) });
const sorry = (env: Env, status: number, why: string, extra: Record<string, string> = {}): ChatResponse =>
  reply(status, { ok: false, error: why, message: `I can't answer that right now. Please call the salon at ${env.SALON_PHONE ?? DEFAULT_PHONE} or use the Book Online page on the website.` }, extra);

// ---- the per-visitor rate limit: in memory, per warm instance (best effort, not shared between instances)
const seen = new Map<string, { starts: number[]; messages: number[] }>();
function allow(ip: string, kind: "starts" | "messages", now: number): boolean {
  const entry = seen.get(ip) ?? { starts: [], messages: [] };
  entry[kind] = entry[kind].filter((t) => now - t < WINDOW_MS);
  const ok = entry[kind].length < (kind === "starts" ? MAX_STARTS_PER_WINDOW : MAX_MESSAGES_PER_WINDOW);
  if (ok) entry[kind].push(now);
  seen.set(ip, entry);
  if (seen.size > 5000) for (const [key, value] of seen) if (!value.starts.length && !value.messages.length) seen.delete(key);
  return ok;
}
export const resetLimits = () => seen.clear();

// ---- the signed session token
const b64 = (data: Buffer | string) => Buffer.from(data).toString("base64url");
const key = (env: Env) => createHmac("sha256", env.RETELL_API_KEY ?? "").update("glamour-chat-session-v1").digest();
export function sign(env: Env, claims: { c: string; e: number; n: number }): string {
  const payload = b64(JSON.stringify({ ...claims, i: randomBytes(6).toString("hex") }));
  return `${payload}.${b64(createHmac("sha256", key(env)).update(payload).digest())}`;
}
export function verify(env: Env, token: unknown, now: number): { c: string; e: number; n: number } | null {
  if (typeof token !== "string" || token.length > 600) return null;
  const [payload, mac, extra] = token.split(".");
  if (!payload || !mac || extra !== undefined) return null;
  const want = createHmac("sha256", key(env)).update(payload).digest();
  let got: Buffer;
  try {
    got = Buffer.from(mac, "base64url");
  } catch {
    return null;
  }
  if (got.length !== want.length || !timingSafeEqual(got, want)) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (typeof claims.c !== "string" || !/^[\w-]{1,80}$/.test(claims.c) || !Number.isFinite(claims.e) || !Number.isInteger(claims.n) || claims.n < 0 || claims.e < now) return null;
    return { c: claims.c, e: claims.e, n: claims.n };
  } catch {
    return null;
  }
}

// ---- the provider
async function provider(env: Env, deps: Deps, path: string, body: unknown): Promise<{ ok: true; data: any } | { ok: false; status: number }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), deps.timeoutMs ?? PROVIDER_TIMEOUT_MS);
  try {
    const response = await deps.fetch(`${env.RETELL_API_BASE ?? "https://api.retellai.com"}${path}`, {
      method: "POST", headers: { authorization: `Bearer ${env.RETELL_API_KEY}`, "content-type": "application/json" }, body: JSON.stringify(body), signal: controller.signal,
    });
    if (!response.ok) return { ok: false, status: response.status };
    return { ok: true, data: await response.json() };
  } catch (error) {
    return { ok: false, status: (error as Error).name === "AbortError" ? 504 : 502 };
  } finally {
    clearTimeout(timer);
  }
}

/** The words of the agent's reply: only messages from the agent, as plain text. Tool activity, system notes and anything else are dropped. */
export function agentWords(data: any): string {
  const messages = Array.isArray(data?.messages) ? data.messages : [];
  return messages
    .filter((m: any) => m && m.role === "agent" && typeof m.content === "string")
    .map((m: any) => m.content.trim())
    .filter(Boolean)
    .join("\n")
    .slice(0, 2000);
}
const clean = (text: string) => text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g, "").trim();

export async function handle(req: ChatRequest, env: Env, deps: Deps): Promise<ChatResponse> {
  const now = deps.now();
  const route = req.path.replace(/\/+$/, "");
  if (route !== "/api/chat/start" && route !== "/api/chat/message") return reply(404, { ok: false, error: "not_found" });
  if (req.method !== "POST") return reply(405, { ok: false, error: "method_not_allowed" }, { allow: "POST" });
  if (!env.RETELL_API_KEY || !env.GLAMOUR_CHAT_AGENT_ID) return sorry(env, 503, "not_configured");

  // only this page (or an origin the operator listed) may use it from a browser
  const origin = req.headers.origin;
  if (origin) {
    const own = new URL(req.url).origin;
    if (origin !== own && !(env.ALLOWED_ORIGINS ?? "").split(/\s+/).filter(Boolean).includes(origin)) return reply(403, { ok: false, error: "origin_not_allowed" });
  }
  if (Buffer.byteLength(req.body, "utf8") > MAX_BODY) return reply(413, { ok: false, error: "too_large" });
  if (!/^application\/json\b/i.test(req.headers["content-type"] ?? "")) return reply(415, { ok: false, error: "json_required" });
  let body: any;
  try {
    body = JSON.parse(req.body || "{}");
  } catch {
    return reply(400, { ok: false, error: "bad_json" });
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return reply(400, { ok: false, error: "bad_shape" });

  if (route === "/api/chat/start") {
    if (!allow(req.ip, "starts", now)) return sorry(env, 429, "slow_down", { "retry-after": "60" });
    const made = await provider(env, deps, "/create-chat", { agent_id: env.GLAMOUR_CHAT_AGENT_ID, metadata: { source: "website" } });
    if (!made.ok || typeof made.data?.chat_id !== "string") return failure(env, deps, made.ok ? 502 : made.status);
    return reply(201, { ok: true, token: sign(env, { c: made.data.chat_id, e: now + TOKEN_TTL_MS, n: 0 }), max_messages: MAX_MESSAGES });
  }

  const claims = verify(env, body.token, now);
  if (!claims) return reply(401, { ok: false, error: "session_invalid", message: "This chat has ended or expired. Please start a new one." });
  if (typeof body.text !== "string") return reply(400, { ok: false, error: "text_required" });
  const text = clean(body.text);
  if (!text) return reply(400, { ok: false, error: "text_required" });
  if (Array.from(text).length > MAX_TEXT) return reply(413, { ok: false, error: "message_too_long", message: `Please keep each message under ${MAX_TEXT} characters.` });
  if (claims.n >= MAX_MESSAGES) return reply(429, { ok: false, error: "chat_full", message: `This chat has gone on long enough. Please start a new chat, or call the salon at ${env.SALON_PHONE ?? DEFAULT_PHONE}.` });
  if (!allow(req.ip, "messages", now)) return sorry(env, 429, "slow_down", { "retry-after": "60" });

  const done = await provider(env, deps, "/create-chat-completion", { chat_id: claims.c, content: text });
  if (!done.ok) return failure(env, deps, done.status);
  const words = agentWords(done.data);
  if (!words) return failure(env, deps, 502);
  return reply(200, { ok: true, reply: words, token: sign(env, { c: claims.c, e: claims.e, n: claims.n + 1 }), remaining: Math.max(0, MAX_MESSAGES - claims.n - 1) });
}

function failure(env: Env, deps: Deps, status: number): ChatResponse {
  deps.log?.(`chat provider failure: ${status}`); // the status only: never a message, a key or an id
  if (status === 429) return sorry(env, 503, "busy", { "retry-after": "30" });
  return sorry(env, status === 504 ? 504 : 502, "unavailable");
}
