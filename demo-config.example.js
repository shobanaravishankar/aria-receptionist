/* Copy this file to  demo-config.js  and fill in the two values.
 * demo-config.js is git-ignored so your account values are not committed.
 *
 * Where to get these (Retell dashboard):
 *   agentId   - open Aria, copy her Agent ID (starts with "agent_")
 *   publicKey - API Keys  ->  Public Keys  ->  create/pick a key, copy its value
 *               (a PUBLIC key is meant for the browser. Never put a private API key here.)
 *   On that public key, add the allowed domains:  localhost  for local testing, and the
 *   exact hostname of any deployed copy.
 */
window.RETELL_DEMO = {
  publicKey: "",       // e.g. "public_key_xxxxxxxxxxxxxxxx"
  agentId: "",         // e.g. "agent_xxxxxxxxxxxxxxxxxxxxxxxx"
  agentVersion: null   // optional: a number, or leave null for the latest
};
