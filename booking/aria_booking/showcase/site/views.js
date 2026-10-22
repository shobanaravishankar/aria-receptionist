/* The Serenity showcase's two calendar views, inside the same page.
 *
 * "Calendar" lists the open times for a chosen treatment, length, day and (optionally) technician, from the SAME prototype calendar the voice assistant reads.
 * "Staff calendar" is the detailed, fictional staff schedule, shown in a frame.
 *
 * They are views of THIS page (a hash route, no navigation away, no new tab), so an active call with Aria, which lives in voice.js on this page, is never
 * unloaded or disturbed by opening, using or leaving them. "Back to ..." always lands on the home page and never depends on the frame's own history.
 *
 * Fresh, not stale: opening a view shows what the calendar holds NOW (open times are asked again, the schedule frame is reloaded on the day it was showing), and while
 * a view is open the page asks the calendar server a tiny "has anything changed?" question every few seconds and refreshes only when it has (a booking, move or
 * cancellation made by a call, say). Each view also has a Refresh button. A late answer to an older search never replaces a newer one.
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
  var watchMs = Math.max(1, Number(data.refreshSeconds) || 5) * 1000;
  // pollMode "call" (a free static host): the page asks "has anything changed?" ONLY while a call with Aria is live (voice.js marks it on the page), and looks afresh when a view opens and
  // once more when the call ends; otherwise the Refresh buttons do it. Left unset the page watches whenever a view is open, as before.
  var callOnly = data.pollMode === "call";
  function inCall() { return Boolean(document.documentElement.dataset.ariaCall); }
  var current = null;
  var ready = false;

  function currentRoute() {
    var id = "";
    try { id = decodeURIComponent((location.hash || "").replace(/^#/, "")); } catch (_) { id = ""; }
    return views[id] ? id : null;
  }

  function route() {
    var name = currentRoute();
    var previous = current;
    Object.keys(views).forEach(function (key) { views[key].hidden = key !== name; });
    home.hidden = !!name;
    document.body.classList.toggle("in-view", !!name);
    current = name;   // set before anything below starts watching: the watcher only runs while a view is current
    if (name) {
      document.title = titles[name] + " · " + homeTitle;
      if (name !== previous) {   // hashchange and popstate both arrive for one navigation: act once
        if (name === "calendar") openCalendar(); else openStaff();
        startWatching();
      }
      window.scrollTo(0, 0);
      var heading = views[name].querySelector("h1");
      if (heading) { heading.setAttribute("tabindex", "-1"); try { heading.focus({ preventScroll: true }); } catch (_) {} }
    } else {
      document.title = homeTitle;
      var id = (location.hash || "").replace(/^#/, "");
      var target = id && document.getElementById(id);
      if (target && !target.hidden) { try { target.scrollIntoView(); } catch (_) {} }
      else if (previous) window.scrollTo(0, 0);
      stopWatching();
    }
  }

  // "Back to ..." is explicit: it makes the home page the current entry itself. It never goes "back" in history, because the staff calendar frame's own day
  // navigation lives in the same session history and would be what "back" undid.
  function goHome(event) {
    if (event) event.preventDefault();
    try { window.history.replaceState(null, "", location.pathname + location.search + "#top"); } catch (_) { location.hash = "#top"; }
    route();
  }

  // Moving from one view to the other REPLACES the history entry rather than adding one, so there is only ever one view entry above the home page.
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
  Array.prototype.forEach.call(document.querySelectorAll("[data-refresh]"), function (el) {
    el.addEventListener("click", function () { if (el.getAttribute("data-refresh") === "staff") refreshStaff(); else refreshTimes(true); });
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("popstate", route);

  // ---------------------------------------------------------------- staff calendar (a frame, loaded the first time it is opened and kept)
  var frame = document.getElementById("staff-frame");
  var staffUrl = "";   // the day the frame is showing now, as the frame last told us (it cannot be read across origins)

  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!frame || event.source !== frame.contentWindow || event.origin !== data.calendarOrigin) return;
    if (!message || message.type !== "serenity-calendar" || typeof message.url !== "string" || message.url.indexOf(data.calendarOrigin + "/") !== 0) return;
    staffUrl = message.url;
  });

  function openStaff() {
    if (!frame) return;
    if (!frame.getAttribute("src")) {
      if (frame.getAttribute("data-src")) frame.setAttribute("src", frame.getAttribute("data-src"));
      return;
    }
    refreshStaff();   // it was loaded before and may be stale: show the calendar as it is now, on the day it was showing
  }

  function refreshStaff() {
    if (!frame || !frame.getAttribute("src")) { openStaff(); return; }
    var target = staffUrl || frame.getAttribute("data-src");
    try { frame.contentWindow.location.replace(target); } catch (_) { frame.setAttribute("src", target); }   // replace: refreshing never adds a history entry
  }

  // ---------------------------------------------------------------- "has anything changed?" (only while a view is open and the page is visible)
  var stamp = null, timer = null, failures = 0;

  function schedule(ms) {
    clearTimeout(timer);
    timer = current && document.visibilityState !== "hidden" && (!callOnly || inCall()) ? setTimeout(poll, ms) : null;
  }

  function poll() {
    timer = null;
    if (!current) return;
    fetch(data.calendarOrigin + "/demo/changes", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (c) {
      failures = 0;
      if (c && typeof c.stamp === "string") {
        if (stamp !== null && c.stamp !== stamp) refreshActive();
        stamp = c.stamp;
      }
    }).catch(function () { failures += 1; }).then(function () { schedule(Math.min(watchMs * Math.pow(2, failures), 60000)); });
  }

  function startWatching() { stamp = null; failures = 0; schedule(0); }
  function stopWatching() { clearTimeout(timer); timer = null; }
  function refreshActive() { if (current === "calendar") refreshTimes(true); else if (current === "staff-calendar") refreshStaff(); }
  // Hidden: stop asking, but KEEP the last stamp seen. When the tab is shown again the first answer is compared with it, so a booking made meanwhile is noticed and the open view refreshed.
  document.addEventListener("visibilitychange", function () { if (document.visibilityState !== "hidden" && current) schedule(0); else stopWatching(); });
  if (callOnly && window.MutationObserver) {
    new MutationObserver(function () {
      if (!current) return;
      if (inCall()) startWatching();             // a call began while a view is open: now it is worth asking
      else { stopWatching(); refreshActive(); }   // the call ended: stop asking, and show the calendar as it is now (the selection and the view stay as they were)
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-aria-call"] });
  }

  // ---------------------------------------------------------------- customer calendar: open times
  var form = document.getElementById("open-times-form");
  var fam = document.getElementById("ot-family"), len = document.getElementById("ot-length"), staff = document.getElementById("ot-staff"), day = document.getElementById("ot-date");
  var out = document.getElementById("ot-results"), note = document.getElementById("ot-refresh-note");
  var families = [];
  var latest = 0;        // the number of the newest request: an answer for any other request is dropped, success or failure
  var lastQuery = null;  // what the visitor last asked for, so a refresh asks the same thing

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

  function say(html, state) { out.innerHTML = html; out.setAttribute("data-state", state || ""); if (note) note.textContent = ""; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function search() {
    var family = families[parseInt(fam.value, 10)];
    var variant = family && family.variants[parseInt(len.value, 10) || 0];
    if (!variant || !day.value) { latest += 1; lastQuery = null; say("<p>Choose a treatment, a length and a day.</p>", "needs"); return; }
    lastQuery = { service_id: variant.service_id, date: day.value, staff: staff.value || "" };
    ask(lastQuery, false);
  }

  // Ask the calendar. ``quiet`` is a refresh of what is already shown: no "Looking..." placeholder, and a failure keeps the times already on screen.
  function ask(query, quiet) {
    var mine = ++latest;
    var url = data.calendarOrigin + "/demo/open-times?service_id=" + encodeURIComponent(query.service_id) + "&date=" + encodeURIComponent(query.date) +
      (query.staff ? "&staff=" + encodeURIComponent(query.staff) : "");
    if (!quiet) say("<p>Looking at the calendar…</p>", "loading");
    fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (r) {
      if (mine === latest) render(r);
    }).catch(function () {
      if (mine !== latest) return;
      if (quiet && out.getAttribute("data-state")) { if (note) note.textContent = "The calendar couldn't be reached just now, so these times may be out of date."; return; }
      say("<p>The calendar couldn't be reached just now, so nothing can be shown. Please try again in a moment.</p>", "error");
    });
  }

  function refreshTimes(quiet) { if (lastQuery) ask(lastQuery, quiet); }

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

  function openCalendar() { fillForm(); refreshTimes(true); }   // opening it again shows the times as they are now

  route();
})();
