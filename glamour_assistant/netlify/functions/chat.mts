/* The one Netlify Function of the Glamour Day Spa information assistant: typed chat at /api/chat/start and /api/chat/message. A thin adapter around src/chat.ts (tested without Netlify).
 *
 * Environment variables (set on the site by the operator; never in Git or a page):
 *   RETELL_API_KEY           the Retell secret API key                                  [secret]
 *   GLAMOUR_CHAT_AGENT_ID    the id of the customer's CHAT agent (not the demo's)
 *   SALON_PHONE              optional: the number spoken in failure answers
 *   ALLOWED_ORIGINS          optional: other origins that may call the relay (space separated)
 * Nothing here polls, keeps anything warm, stores a message or sends a message to anyone. */

import { handle } from "../../src/chat.ts";

export default async (request: Request): Promise<Response> => {
  const url = new URL(request.url);
  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => { headers[key.toLowerCase()] = value; });
  const body = request.method === "POST" ? await request.text() : "";
  const ip = headers["x-nf-client-connection-ip"] ?? (headers["x-forwarded-for"] ?? "").split(",")[0].trim() ?? "unknown";
  const env = process.env as Record<string, string | undefined>;
  const result = await handle(
    { method: request.method, path: url.pathname, headers, body, ip: ip || "unknown", url: request.url },
    { RETELL_API_KEY: env.RETELL_API_KEY, GLAMOUR_CHAT_AGENT_ID: env.GLAMOUR_CHAT_AGENT_ID, SALON_PHONE: env.SALON_PHONE, ALLOWED_ORIGINS: env.ALLOWED_ORIGINS },
    { fetch, now: () => Date.now(), log: (message) => console.log(message) },
  );
  return new Response(result.body, { status: result.status, headers: result.headers });
};

export const config = { path: ["/api/chat/start", "/api/chat/message"] };
