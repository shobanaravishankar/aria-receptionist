/* The Serenity showcase's two calendar views, inside the same page.
 *
 * "Calendar" lists the open times for a chosen treatment, length, day and (optionally) technician, from the SAME prototype calendar the voice assistant reads.
 * "Staff calendar" is the detailed, fictional staff schedule, shown in a frame.
 *
 * They are views of THIS page (a hash route, no navigation away, no new tab), so an active call with Aria, which lives in voice.js on this page, is never
 * unloaded or disturbed by opening, using or leaving them. Browser Back and the visible "Back to Serenity Spa" control both return home.
 * Nothing here holds a credential: the browser only reads synthetic open times from the configured calendar address.
 */
(function () {
  "use strict";
  var home = document.getElementById("home");
  var views = { "calendar": document.getElementById("view-calendar"), "staff-calendar": document.getElementById("view-staff") };
  if (!home || !views["calendar"] || !views["staff-calendar"]) return;

  var titles = { "calendar": "Open times", "staff-calendar": "Staff calendar" };
  var homeTitle = document.title;
  var data = window.SERENITY_BOOKABLE || { groups: [] };
  var cameFromHome = false;      // true when a view was opened by an in-page link, so Back/Home can use history instead of adding entries
  var current = null;
  var ready = false;

  function currentRoute() {
    var id = "";
    try { id = decodeURIComponent((location.hash || "").replace(/^#/, "")); } catch (_) { id = ""; }
    return views[id] ? id : null;
  }

  function route(event) {
    var name = currentRoute();
    if (name && !current) cameFromHome = !!event;  // opened by an in-page navigation (or Back/Forward), not by loading the page on the view
    Object.keys(views).forEach(function (key) { views[key].hidden = key !== name; });
    home.hidden = !!name;
    document.body.classList.toggle("in-view", !!name);
    if (name) {
      document.title = titles[name] + " · " + homeTitle;
      if (name === "calendar") openCalendar(); else openStaff();
      window.scrollTo(0, 0);
      var heading = views[name].querySelector("h1");
      if (heading) { heading.setAttribute("tabindex", "-1"); try { heading.focus({ preventScroll: true }); } catch (_) {} }
    } else {
      document.title = homeTitle;
      var id = (location.hash || "").replace(/^#/, "");
      var target = id && document.getElementById(id);
      if (target && !target.hidden) { try { target.scrollIntoView(); } catch (_) {} }
      else if (current) window.scrollTo(0, 0);
    }
    current = name;
  }

  function goHome(event) {
    if (event) event.preventDefault();
    if (cameFromHome && window.history.length > 1) { window.history.back(); return; }
    try { window.history.replaceState(null, "", location.pathname + location.search + "#top"); } catch (_) { location.hash = "#top"; }
    route();
  }

  // Moving from one view to the other REPLACES the history entry rather than adding one, so there is only ever one view entry above the home page:
  // "Back to ..." and the browser's Back button both land on the home page, never on the view you just left.
  document.addEventListener("click", function (event) {
    var link = event.target && event.target.closest ? event.target.closest("a[href^='#']") : null;
    var id = link ? (link.getAttribute("href") || "").replace(/^#/, "") : "";
    if (!current || !views[id] || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (id === current) return;
    try { window.history.replaceState(null, "", location.pathname + location.search + "#" + id); } catch (_) { location.hash = "#" + id; return; }
    route();
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-home]"), function (el) { el.addEventListener("click", goHome); });
  window.addEventListener("hashchange", route);
  window.addEventListener("popstate", route);

  // ---------------------------------------------------------------- staff calendar (a frame, loaded the first time it is opened and kept)
  var frame = document.getElementById("staff-frame");
  function openStaff() {
    if (frame && !frame.getAttribute("src") && frame.getAttribute("data-src")) frame.setAttribute("src", frame.getAttribute("data-src"));
  }

  // ---------------------------------------------------------------- customer calendar: open times
  var form = document.getElementById("open-times-form");
  var fam = document.getElementById("ot-family"), len = document.getElementById("ot-length"), staff = document.getElementById("ot-staff"), day = document.getElementById("ot-date");
  var out = document.getElementById("ot-results");
  var families = [];

  function option(value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; return o; }

  function fillForm() {
    if (ready || !form || !fam) return;
    ready = true;
    data.groups.forEach(function (group) {
      var g = document.createElement("optgroup");
      g.label = group.title;
      group.families.forEach(function (family) { families.push(family); g.appendChild(option(String(families.length - 1), family.name)); });
      fam.appendChild(g);
    });
    fam.addEventListener("change", onFamily);
    len.addEventListener("change", onLength);
    onFamily();
    form.addEventListener("submit", function (e) { e.preventDefault(); search(); });
    syncDates();
  }

  function onFamily() {
    var family = families[parseInt(fam.value, 10)];
    len.innerHTML = "";
    if (!family) return;
    family.variants.forEach(function (v, i) { len.appendChild(option(String(i), v.label)); });
    onLength();
  }

  function onLength() {
    var family = families[parseInt(fam.value, 10)];
    var variant = family && family.variants[parseInt(len.value, 10) || 0];
    staff.innerHTML = "";
    staff.appendChild(option("", "Any available technician"));
    if (variant) variant.technicians.forEach(function (name) { staff.appendChild(option(name, name)); });
  }

  function syncDates() {
    fetch(data.calendarOrigin + "/demo/health", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (h) {
      if (!h || !h.today) return;
      day.min = h.today;
      if (h.covers && h.covers[1]) day.max = h.covers[1];
      if (!day.value || day.value < h.today) day.value = h.today;
    }).catch(function () { if (!day.value) day.value = new Date().toISOString().slice(0, 10); });
  }

  function say(html, state) { out.innerHTML = html; out.setAttribute("data-state", state || ""); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function search() {
    var family = families[parseInt(fam.value, 10)];
    var variant = family && family.variants[parseInt(len.value, 10) || 0];
    if (!variant || !day.value) { say("<p>Choose a treatment, a length and a day.</p>", "needs"); return; }
    var url = data.calendarOrigin + "/demo/open-times?service_id=" + encodeURIComponent(variant.service_id) + "&date=" + encodeURIComponent(day.value) +
      (staff.value ? "&staff=" + encodeURIComponent(staff.value) : "");
    say("<p>Looking at the calendar…</p>", "loading");
    fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); }).then(render).catch(function () {
      say("<p>The calendar couldn't be reached just now, so nothing can be shown. Please try again in a moment.</p>", "error");
    });
  }

  function render(r) {
    if (r.status === "open") {
      var items = r.times.map(function (t) {
        return '<li class="slot"><span class="slot-time">' + esc(t.label) + '</span><span class="slot-tech">with ' + esc(t.technicians.join(", ")) + "</span></li>";
      }).join("");
      say('<h2 class="ot-day">Open on ' + esc(r.day) + "</h2><p class=\"ot-sub\">" + esc(r.service) + " · start times for the full length</p>" +
        '<ul class="slot-list">' + items + "</ul>" + (r.message ? '<p class="ot-note">' + esc(r.message) + "</p>" : ""), "open");
    } else if (r.status === "none") {
      say('<h2 class="ot-day">' + esc(r.day || "") + "</h2><p>" + esc(r.message) + " Try another day or technician.</p>", "none");
    } else if (r.status === "unknown") {
      say('<h2 class="ot-day">' + esc(r.day || "") + "</h2><p>" + esc(r.message) + "</p>", "unknown");
    } else {
      say("<p>" + esc(r.message || "Those times can't be shown.") + "</p>", r.status || "error");
    }
  }

  function openCalendar() { fillForm(); }

  route();
})();
