/* Build-time settings. The Retell PUBLIC key and the VOICE agent id are public by design (they are in the page's source for anyone to read); the chat relay holds the secret key on the server.
 * `scripts/build_site.py` writes this file from its arguments; this committed copy is a harmless placeholder (nothing connects with it). */
window.GLAMOUR = {
  retellPublicKey: "",
  voiceAgentId: "",
  voiceAgentVersion: null,
  chatApi: "api/chat",
  salonPhone: "(908) 668-8828",
  bookOnline: "https://www.warrenglamourdayspanj.com/book-online",
  sdkUrl: "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm",
  greeting: "Hi, I'm Aria, the virtual assistant for Glamour Day Spa in Warren, NJ. Ask me about our services, hours or location, by typing here or by pressing Talk to Aria.",
};
