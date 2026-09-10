# Glamour Day Spa — voice assistant demo

A one-page site recreating the look of Glamour Day Spa (Warren, NJ) with a **Speak to an agent**
button that starts a real spoken conversation with **Aria**, an existing Retell voice agent, in the
browser. Built for demonstration only — it is not the official website and performs no bookings,
transfers, or payments.

## What you need to make voice work

Two values from your Retell dashboard, pasted into **`demo-config.js`**:

| value | where to find it |
|---|---|
| `agentId` | open **Aria** in Retell → copy her **Agent ID** (`agent_…`) |
| `publicKey` | **API Keys → Public Keys** → create/select a key → copy its value |

On that public key, add **allowed domains**: `localhost` for local testing, then the deployed
hostname once we publish. A **public** key is meant for the browser — never paste a private API key
here or anywhere in the frontend.

If the dashboard hands you a ready-made `<script …></script>` widget snippet, you can instead paste
the whole snippet into `demo-config.js` as `widgetEmbed: "..."` and it is used as-is.

## Run locally

```bash
cd C:\Users\shoba\code\glamour-dayspa-demo
python -m http.server 5173
```

Open <http://localhost:5173>. Click **Speak to an agent** → **Start voice call** → allow the
microphone. Before the keys are added the panel shows a "not configured yet" state with these same
instructions.

## Deploy

Any static HTTPS host works (the voice widget requires HTTPS off `localhost`). After deploying, add
the exact deployed hostname to the public key's allowed domains, then re-run the acceptance test on
the hosted URL.

## Acceptance test (hosted)

1. Open the hosted page.
2. Click the voice button; allow the microphone.
3. Ask a service question; hear Aria answer.
4. Ask a follow-up.
5. End the call, then start another.

Report success only once a real conversation has been heard end to end.

## Files

| file | purpose |
|---|---|
| `index.html` | the page |
| `styles.css` | styling |
| `retell-voice.js` | loads the Retell widget from your config and wires the buttons / call states |
| `demo-config.js` | **your** Retell values (git-ignored) |
| `demo-config.example.js` | template with instructions |
