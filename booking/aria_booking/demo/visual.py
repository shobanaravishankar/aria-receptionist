"""Screenshot comparison for the demo calendar, with the standard library only (no image package).

Use: render the demo page at the SAME viewport as a user-supplied reference screenshot of the real day view, then compare the regions whose content
does not depend on data: the icon sidebar, the toolbar and the hour axis. The staff columns hold different (invented) data by design, so they are
not compared pixel for pixel. The demo's top banner is not part of the real page, so it is cropped before comparing.

Nothing here claims a match: it measures the difference and reports it. With no reference image there is nothing to compare, and the report says so.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

BANNER_PX = 28
# regions of the real day view at 1600 x 789 that do not depend on the day's data: (name, x, y, width, height)
CHROME_REGIONS = (
    ("sidebar", 0, 0, 56, 789),
    ("toolbar", 56, 0, 1544, 75),
    ("hour_axis", 273, 139, 74, 650),
)


class Image:
    def __init__(self, width: int, height: int, pixels: bytearray):
        self.width, self.height, self.pixels = width, height, pixels  # RGB, row-major, 3 bytes per pixel

    def crop(self, x: int, y: int, w: int, h: int) -> "Image":
        x2, y2 = min(x + w, self.width), min(y + h, self.height)
        rows = bytearray()
        for row in range(y, y2):
            start = (row * self.width + x) * 3
            rows += self.pixels[start:start + (x2 - x) * 3]
        return Image(x2 - x, y2 - y, rows)

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.pixels[i], self.pixels[i + 1], self.pixels[i + 2]


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def read_png(path: "str | Path") -> Image:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, width, height, depth, kind, idat = 8, 0, 0, 0, 0, bytearray()
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, kind, _comp, _filter, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or kind not in (2, 6) or interlace:
                raise ValueError("only 8-bit RGB / RGBA, non-interlaced PNGs are supported")
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    channels = 3 if kind == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray()
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        method = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if method == 1:
                line[i] = (line[i] + a) & 255
            elif method == 2:
                line[i] = (line[i] + b) & 255
            elif method == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif method == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
        previous = line
        if channels == 4:
            for i in range(0, stride, 4):
                out += line[i:i + 3]
        else:
            out += line
    return Image(width, height, out)


def write_png(path: "str | Path", image: Image) -> None:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    stride = image.width * 3
    raw = b"".join(b"\x00" + bytes(image.pixels[row * stride:(row + 1) * stride]) for row in range(image.height))
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    )


@dataclass(frozen=True)
class RegionDiff:
    name: str
    mean_abs_diff: float  # 0 (identical) .. 255
    differing_ratio: float  # share of pixels whose largest channel difference exceeds the threshold


def diff_region(a: Image, b: Image, name: str, threshold: int = 24) -> RegionDiff:
    if (a.width, a.height) != (b.width, b.height):
        raise ValueError(f"{name}: the regions differ in size ({a.width}x{a.height} vs {b.width}x{b.height})")
    total = bad = 0
    count = a.width * a.height
    for i in range(0, count * 3, 3):
        d = max(abs(a.pixels[i] - b.pixels[i]), abs(a.pixels[i + 1] - b.pixels[i + 1]), abs(a.pixels[i + 2] - b.pixels[i + 2]))
        total += abs(a.pixels[i] - b.pixels[i]) + abs(a.pixels[i + 1] - b.pixels[i + 1]) + abs(a.pixels[i + 2] - b.pixels[i + 2])
        bad += d > threshold
    return RegionDiff(name, total / (count * 3) if count else 0.0, bad / count if count else 0.0)


def compare_to_reference(
    reference: Image, rendered: Image, *, banner_px: int = BANNER_PX, regions: Sequence[tuple] = CHROME_REGIONS, threshold: int = 24,
) -> list[RegionDiff]:
    """Diff the data-independent regions of ``rendered`` (taller than the reference by the demo banner) against the reference screenshot."""
    below = rendered.crop(0, banner_px, rendered.width, rendered.height - banner_px)
    if (below.width, below.height) != (reference.width, reference.height):
        raise ValueError(
            f"render the demo at {reference.width} x {reference.height + banner_px} so that, without its {banner_px}px banner, it is "
            f"{reference.width} x {reference.height} like the reference (got {rendered.width} x {rendered.height})"
        )
    return [diff_region(reference.crop(x, y, w, h), below.crop(x, y, w, h), name, threshold) for name, x, y, w, h in regions]
