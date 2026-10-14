/* Menu tabs for the Serenity showcase page. Without JavaScript every category is shown (the page ships them all and the <noscript> rule reveals them). */
(function () {
  document.documentElement.classList.add("js");
  var bar = document.querySelector(".mobile-cta"), actions = document.querySelector(".hero-actions");
  if (bar && actions && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { bar.classList.toggle("show", !e.isIntersecting); });
    }, { threshold: 0 }).observe(actions);
  } else if (bar) {
    bar.classList.add("show");
  }
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  if (!tabs.length) return;

  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
    try { tab.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (_) {}
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { select(tab, false); });
    tab.addEventListener("keydown", function (e) {
      var next = null;
      if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
      else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
      else if (e.key === "Home") next = tabs[0];
      else if (e.key === "End") next = tabs[tabs.length - 1];
      if (next) { e.preventDefault(); select(next, true); }
    });
  });

  // a link such as #massage opens that category
  function fromHash() {
    var id = (location.hash || "").replace("#", "");
    var tab = tabs.filter(function (t) { return t.getAttribute("data-category") === id; })[0];
    if (tab) select(tab, false);
  }
  window.addEventListener("hashchange", fromHash);
  fromHash();
})();
