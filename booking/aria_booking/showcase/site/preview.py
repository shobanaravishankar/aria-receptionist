"""Desktop and phone previews of the Serenity page, taken in a real browser against a loopback server. The browser can reach ONLY this machine: any request to anything
else is aborted and counted, and the capture fails if there was one. The voice SDK is a local stub, so no call and no network are involved."""

from __future__ import annotations

import functools
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import build_site

SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = "export class RetellClient { constructor(o) {} createWebCall(o) { window.__hooks = o.hooks; return { end: async () => {}, startAudioPlayback() {} }; } }"
VIEWPORTS = (("desktop", {"width": 1440, "height": 900}, 1, False), ("phone", {"width": 390, "height": 844}, 2, True))


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def capture(out_dir, *, channel: Optional[str] = None, site_dir=None) -> list[Path]:
    """Write previews into ``out_dir``: the top of the page, the whole page, the facial menu and the voice panel, for desktop and for a phone. Returns the files."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("the previews need Playwright (pip install -r requirements.txt)") from exc
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    holder = None
    if site_dir is None:
        holder = tempfile.TemporaryDirectory()
        site_dir = Path(holder.name)
        build_site(site_dir, settings={"publicKey": "public_key_preview", "agentId": "agent_preview", "agentVersion": 0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(site_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
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
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if holder is not None:
            holder.cleanup()
    if blocked:
        raise RuntimeError("the page tried to reach beyond this machine: " + ", ".join(sorted(set(blocked))))
    return written
