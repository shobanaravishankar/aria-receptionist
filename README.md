# Aria — Glamour Day Spa voice demo

A one-page demo site recreating the look of Glamour Day Spa (Warren, NJ), with two ways to talk to an
AI assistant:

- **Speak to an agent** — a real spoken conversation with **Aria**, an existing Retell voice agent, in the
  browser. **One click** starts the call (the browser's microphone prompt still applies).
- **Talk to the avatar** — an optional, separate LiveAvatar embed shown in its own panel.

It is a demonstration, not the official website. **The page itself performs no bookings, transfers or
payments.** The appointment-booking prototype in [`booking/`](booking/README.md) is a separate, isolated
tool that is not connected to this page.

## How the voice call works

`retell-voice.js` uses the Retell Web SDK (`retell-client-js-sdk@3.0.1`, loaded on demand from jsDelivr) with a
browser **public key** — no backend, no access token, no private key:

```js
const client = new RetellClient({ key: publicKey });
const call = client.createWebCall({ agent_id: agentId, hooks });   // from the user's click
await call.end();                                                  // End call
```

- The call is created **only on a click**, never on page load. All "Speak to an agent" buttons share one
  active call, so repeated clicks cannot start duplicates.
- The panel shows real states: *Connecting* → *Connected* (with *Aria is speaking* / *Listening*) → *Ended*.
  A blocked microphone, a web address that is not on the key's allowed domains, or a network problem
  produces a specific message and a **Try again** button; it never falls back to a false "ready".
- Aria greets from the connected agent; the page plays no canned audio.
- Starting a voice call closes the avatar panel, and the avatar will not open during a live call (both want
  the microphone).

## Configure

Two values from your Retell dashboard go in **`demo-config.js`** (git-ignored; copy
`demo-config.example.js` to start):

| value | where to find it |
|---|---|
| `agentId` | open **Aria** in Retell → copy the **Agent ID** (`agent_…`) |
| `publicKey` | **API Keys → Public Keys** → create or select a key → copy its value |

On that public key, add **allowed domains**: `localhost` for local testing, and the exact hostname of any
deployed copy. A **public** key is meant for the browser — never put a private API key in the frontend.
`agentVersion` is optional.

## Run locally

```bash
cd C:\Users\shoba\code\glamour-dayspa-demo
python serve.py
```

Open <http://localhost:5173> (use `localhost` so the hostname matches the allowed domain). `serve.py` sends
no-cache headers and answers on both `localhost` and `127.0.0.1`. Before the keys exist, the voice panel
shows a "not configured yet" state with the same instructions.

## The avatar option

`avatar.js` opens a LiveAvatar embed (`embed.liveavatar.com`, a third-party service) in its own panel. The
iframe is created only when the panel opens and is **removed** when it closes, so the session and microphone
actually end. It is independent of Aria and of Retell.

## Deploy

Any static HTTPS host works; the voice call needs HTTPS off `localhost`. Upload `index.html`, `styles.css`,
`retell-voice.js`, `avatar.js` and a `demo-config.js` containing your public key and agent id (a public key is
safe to ship in the page, but it is only protected by the allowed-domains list). Add the exact deployed
hostname to the public key's allowed domains, then repeat the acceptance test on the hosted URL.

## Acceptance test

1. Open the page (local or hosted).
2. Click **Speak to an agent** once; allow the microphone.
3. Hear Aria greet you; ask a service question, then a follow-up.
4. Click **End call**, then **Speak to an agent** again to confirm restart.

Report success only once a real conversation has been heard end to end. Audio cannot be verified by
automated tests.

## Files

| file | purpose |
|---|---|
| `index.html` | the page |
| `styles.css` | styling |
| `retell-voice.js` | one-click Aria call via the Retell Web SDK, with real call states |
| `avatar.js` | optional LiveAvatar panel (lazy-loaded iframe) |
| `demo-config.js` | **your** Retell values (git-ignored) |
| `demo-config.example.js` | template with instructions |
| `serve.py` | local no-cache static server (`localhost` and `127.0.0.1`) |
| `booking/` | separate Booksy booking prototype (Selenium); see its README |
