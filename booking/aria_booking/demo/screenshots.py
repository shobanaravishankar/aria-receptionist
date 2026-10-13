"""Take screenshots of the demo calendar page in a (visible) browser window, from the loopback demo server. Nothing leaves this machine."""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from ..driver import DriverError, DriverUnavailable
from .dataset import DemoDataset
from .server import calendar_url, make_site

DEFAULT_VIEWPORTS = ((1600, 817), (1280, 720))  # 817 = the reference 789 + the demo banner's 28 px


def capture(
    dataset: DemoDataset, out_dir: Path, *, day: Optional[date] = None, viewports: Sequence[tuple[int, int]] = DEFAULT_VIEWPORTS,
    channel: Optional[str] = None, scroll_afternoon: bool = True,
) -> list[Path]:
    """PNG files for each viewport (and one scrolled to the afternoon). ``day`` defaults to the first scripted day."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DriverUnavailable("Playwright is not installed; see booking/README.md") from exc
    day = day or dataset.scripted_days[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    site = make_site(dataset, 0)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    port = site.server_address[1]
    written: list[Path] = []
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=False, **({"channel": channel} if channel else {}))
            except PlaywrightError as exc:
                raise DriverUnavailable(
                    "no browser could be started: run `python -m playwright install chromium` or set ARIA_BROWSER_CHANNEL=chrome"
                ) from exc
            try:
                for width, height in viewports:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.goto(calendar_url(port, day.isoformat()), wait_until="load")
                    page.wait_for_selector('[data-testid="calendar-grid-day"]')
                    path = out_dir / f"demo-day-{width}x{height}.png"
                    page.screenshot(path=str(path))
                    written.append(path)
                    if scroll_afternoon:
                        page.evaluate("document.getElementById('ariaGrid').scrollTop = 400")
                        page.wait_for_timeout(100)
                        path = out_dir / f"demo-day-afternoon-{width}x{height}.png"
                        page.screenshot(path=str(path))
                        written.append(path)
                    page.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise DriverError(f"the browser stopped ({type(exc).__name__})") from exc
    finally:
        site.shutdown()
        site.server_close()
    return written
