"""Desktop and phone previews of the Serenity page, taken in a real browser against a loopback server. The browser can reach ONLY this machine: any request to anything
else is aborted and counted, and the capture fails if there was one. The voice SDK is a local stub, so no call and no network are involved.

The page's own 'localhost' calendar address is answered from the loopback calendar server started here (and no other 'localhost' address is), so the Calendar and Staff
calendar views are photographed with real, synthetic open times."""

from __future__ import annotations

import functools
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from ...demo import SPA_TIMEZONE
from ...demo.dataset import DemoDataset
from ...demo.server import make_site, page_origins
from . import build_site

SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = "export class RetellClient { constructor(o) {} createWebCall(o) { window.__hooks = o.hooks; return { end: async () => {}, startAudioPlayback() {} }; } }"
VIEWPORTS = (("desktop", {"width": 1440, "height": 900}, 1, False), ("phone", {"width": 390, "height": 844}, 2, True))


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def capture(out_dir, *, channel: Optional[str] = None, site_dir=None) -> list[Path]:
    """Write previews into ``out_dir``: the top of the page, the whole page, the facial menu, the voice panel and (when the page is built here) the Calendar and Staff
    calendar views, for desktop and for a phone. Returns the files."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("the previews need Playwright (pip install -r requirements.txt)") from exc
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    holder = None
    built_here = site_dir is None
    if built_here:
        holder = tempfile.TemporaryDirectory()
        site_dir = Path(holder.name)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(site_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_port = server.server_address[1]
    base = f"http://127.0.0.1:{page_port}/"
    calendar = calendar_port = None
    tomorrow = ""
    if built_here:  # the calendar the page reads, on its own loopback port, for the two views
        zone = ZoneInfo(SPA_TIMEZONE)
        today = datetime.now(zone).date()
        tomorrow = (today + timedelta(days=1)).isoformat()
        calendar = make_site(DemoDataset(today), 0, allowed_origins=page_origins(page_port))
        threading.Thread(target=calendar.serve_forever, daemon=True).start()
        calendar_port = calendar.server_address[1]
        build_site(site_dir, settings={"publicKey": "public_key_preview", "agentId": "agent_preview", "agentVersion": 0}, calendar_port=calendar_port)
    written: list[Path] = []
    blocked: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**({"channel": channel} if channel else {}), headless=True)
            for name, viewport, scale, mobile in VIEWPORTS:
                context = browser.new_context(viewport=viewport, device_scale_factor=scale, is_mobile=mobile, has_touch=mobile)

                def guard(route):
                    url = route.request.url
                    if url.startswith(("http://127.0.0.1:", "data:", "blob:", "about:")):
                        route.continue_()
                    elif calendar_port is not None and url.startswith(f"http://localhost:{calendar_port}/"):  # the page's name for the calendar it was built with: this machine
                        route.fulfill(response=route.fetch(url=url.replace("//localhost:", "//127.0.0.1:", 1)))
                    elif url == SDK_URL:
                        route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB)
                    else:
                        blocked.append(url.split("?")[0])
                        route.abort()

                context.route("**/*", guard)
                page = context.new_page()
                page.goto(base, wait_until="networkidle")
                page.screenshot(path=str(out / f"{name}-top.jpg"), type="jpeg", quality=82)
                written.append(out / f"{name}-top.jpg")
                height = page.evaluate("document.documentElement.scrollHeight")
                for y in range(0, height, 600):  # scroll through so every lazy image is loaded before the whole-page picture
                    page.evaluate(f"window.scrollTo(0, {y})")
                    page.wait_for_timeout(60)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(250)
                page.screenshot(path=str(out / f"{name}-full.jpg"), type="jpeg", quality=78, full_page=True)
                written.append(out / f"{name}-full.jpg")
                page.click('[role="tab"][data-category="facial"]')
                page.locator("#menu").scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.screenshot(path=str(out / f"{name}-facials.jpg"), type="jpeg", quality=80)
                written.append(out / f"{name}-facials.jpg")
                page.evaluate("window.scrollTo(0, 0)")
                page.click("[data-voice-start] >> nth=0")
                page.wait_for_timeout(400)
                page.screenshot(path=str(out / f"{name}-voice-panel.jpg"), type="jpeg", quality=82)
                written.append(out / f"{name}-voice-panel.jpg")
                if built_here:
                    page.goto(base, wait_until="networkidle")
                    page.evaluate("location.hash = '#calendar'")
                    page.wait_for_function("() => !document.getElementById('view-calendar').hidden && document.getElementById('ot-date').min !== ''")
                    page.select_option("#ot-family", label="Swedish Massage")
                    page.select_option("#ot-length", index=1)
                    page.fill("#ot-date", tomorrow)
                    page.click('[data-testid="show-open-times"]')
                    page.wait_for_function("() => ['open', 'none', 'unknown', 'error'].includes(document.getElementById('ot-results').dataset.state)")
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(out / f"{name}-calendar.jpg"), type="jpeg", quality=82, full_page=True)
                    written.append(out / f"{name}-calendar.jpg")
                    page.evaluate("location.hash = '#staff-calendar'")
                    page.wait_for_function("() => !document.getElementById('view-staff').hidden")
                    for _ in range(75):  # the frame is another origin (the calendar's own port): find it through the browser, wait for the schedule to be drawn
                        shown = [f for f in page.frames if "/calendar" in f.url]
                        if shown and shown[0].locator('[data-testid="demo-banner"]').count():
                            break
                        page.wait_for_timeout(200)
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(out / f"{name}-staff-calendar.jpg"), type="jpeg", quality=82)
                    written.append(out / f"{name}-staff-calendar.jpg")
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if calendar is not None:
            calendar.shutdown()
            calendar.server_close()
        if holder is not None:
            holder.cleanup()
    if blocked:
        raise RuntimeError("the page tried to reach beyond this machine: " + ", ".join(sorted(set(blocked))))
    return written
