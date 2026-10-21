"""A web-sized copy of an avatar image: square, transparent, WebP. Done in a local browser (no extra library, no network), so a 1.6 MB cut-out becomes a small file.

The image never leaves this machine: it is read from disk, drawn on a canvas in a local browser page, and the result is written to disk. Transparency is kept (alpha is
checked by the tests). The browser is only used for this resize, never started by the website or the call.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

MAX_SOURCE_BYTES = 20_000_000
TYPES = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

_DRAW = """async ({dataUrl, size, quality}) => {
  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  const scale = Math.min(size / image.naturalWidth, size / image.naturalHeight);
  const w = Math.round(image.naturalWidth * scale), h = Math.round(image.naturalHeight * scale);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(image, Math.round((size - w) / 2), size - h, w, h);   // fitted inside the square, resting on its bottom edge
  return canvas.toDataURL('image/webp', quality).split(',')[1];
}"""


def optimize_avatar(source, dest, *, size: int = 512, quality: float = 0.92, channel: Optional[str] = None) -> Path:
    """Write ``dest`` (a .webp) from ``source`` and return it. Raises ValueError for an unusable file and RuntimeError when no browser is available."""
    src, out = Path(source), Path(dest)
    mime = TYPES.get(src.suffix.lower())
    if mime is None:
        raise ValueError("the avatar source must be a .png, .webp or .jpg file")
    if not src.is_file() or src.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("the avatar source was not found or is too large")
    if out.suffix.lower() != ".webp":
        raise ValueError("the web copy must be named .webp (it keeps transparency and is small)")
    if not 64 <= size <= 2048:
        raise ValueError("the size must be between 64 and 2048 pixels")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("resizing needs Playwright (pip install -r requirements.txt)") from exc
    data_url = f"data:{mime};base64," + base64.b64encode(src.read_bytes()).decode("ascii")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**({"channel": channel} if channel else {}), headless=True)
            try:
                page = browser.new_page()
                encoded = page.evaluate(_DRAW, {"dataUrl": data_url, "size": size, "quality": quality})
            finally:
                browser.close()
    except Exception as exc:  # a missing browser, or an image the browser cannot decode: say so without echoing the image
        raise RuntimeError(f"the avatar could not be resized ({type(exc).__name__}); check that a browser is installed and the file is a real image") from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(encoded))
    return out
