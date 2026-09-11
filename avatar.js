/* "Talk to the avatar" — a separate, optional demo option (LiveAvatar).
 * Independent of the Aria/Retell voice call above. Lazy-loaded: the iframe
 * is only created when the panel opens, and removed when it closes, so it
 * never holds the microphone in the background.
 */
const AVATAR_EMBED_URL = "https://embed.liveavatar.com/v1/930cca35-8259-4e1c-be20-5053c88f568e?orientation=horizontal";

const panel = document.getElementById("avatar-panel");
const embedHost = document.getElementById("avatar-embed");
let lastFocus = null;

function ariaCallActive() {
  return document.documentElement.dataset.ariaCall === "1";
}

function openAvatar() {
  if (ariaCallActive()) {
    window.dispatchEvent(new CustomEvent("demo-toast", { detail: "End the Aria voice call first, then open the avatar." }));
    return;
  }
  lastFocus = document.activeElement;
  panel.hidden = false;
  document.body.style.overflow = "hidden";

  if (!embedHost.querySelector("iframe")) {
    embedHost.innerHTML = "";
    const iframe = document.createElement("iframe");
    iframe.src = AVATAR_EMBED_URL;
    iframe.title = "LiveAvatar Embed";
    iframe.allow = "microphone; autoplay";
    embedHost.appendChild(iframe);
  }
}

function closeAvatar() {
  panel.hidden = true;
  document.body.style.overflow = "";
  // remove the iframe so its session/mic actually end, not just hide visually
  embedHost.innerHTML = '<p class="avatar-embed__placeholder">Loading the avatar&hellip;</p>';
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

document.querySelectorAll("[data-avatar-start]").forEach((b) => b.addEventListener("click", openAvatar));
document.querySelectorAll("[data-avatar-close]").forEach((b) => b.addEventListener("click", closeAvatar));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !panel.hidden) closeAvatar();
});

// let retell-voice.js close this panel if the user switches to the voice call
window.__closeAvatarPanel = closeAvatar;

// simple shared toast (reuses the #toast element from index.html)
window.addEventListener("demo-toast", (e) => {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = e.detail;
  el.hidden = false;
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => { el.hidden = true; }, 3600);
});
