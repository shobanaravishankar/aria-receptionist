"""The screenshot-comparison harness (standard library only). Synthetic images; no browser."""

from __future__ import annotations

import struct
import zlib

import pytest

from aria_booking.demo.visual import BANNER_PX, Image, compare_to_reference, diff_region, read_png, write_png


def solid(width, height, rgb):
    return Image(width, height, bytearray(bytes(rgb) * (width * height)))


def gradient(width, height):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes(((x * 7) % 256, (y * 11) % 256, ((x + y) * 5) % 256))
    return Image(width, height, pixels)


def encode_with_filters(image: Image, kind: int, rgba: bool) -> bytes:
    """A PNG written with ONE filter type on every row, as browsers do, so the reader's filter handling is exercised."""
    channels = 4 if rgba else 3
    stride = image.width * channels
    rows = []
    for y in range(image.height):
        row = bytearray()
        for x in range(image.width):
            row += bytes(image.pixel(x, y)) + (b"\xff" if rgba else b"")
        rows.append(row)
    out = bytearray()
    prev = bytearray(stride)
    for row in rows:
        out.append(kind)
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = a
            elif kind == 2:
                predictor = b
            elif kind == 3:
                predictor = (a + b) // 2
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((row[i] - predictor) & 255)
        prev = row

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 6 if rgba else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(out))) + chunk(b"IEND", b""))


def test_write_then_read_gives_the_same_pixels(tmp_path):
    image = gradient(23, 17)
    write_png(tmp_path / "a.png", image)
    again = read_png(tmp_path / "a.png")
    assert (again.width, again.height) == (23, 17) and bytes(again.pixels) == bytes(image.pixels)


@pytest.mark.parametrize("kind", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("rgba", [False, True])
def test_every_png_filter_type_is_decoded(tmp_path, kind, rgba):
    image = gradient(19, 13)
    (tmp_path / "f.png").write_bytes(encode_with_filters(image, kind, rgba))
    assert bytes(read_png(tmp_path / "f.png").pixels) == bytes(image.pixels)


def test_unsupported_files_are_refused(tmp_path):
    (tmp_path / "x.png").write_bytes(b"not a png")
    with pytest.raises(ValueError, match="not a PNG"):
        read_png(tmp_path / "x.png")


def test_identical_regions_have_no_difference_and_a_changed_one_is_measured():
    a, b = solid(10, 10, (200, 200, 200)), solid(10, 10, (200, 200, 200))
    assert diff_region(a, b, "same").mean_abs_diff == 0 and diff_region(a, b, "same").differing_ratio == 0
    for x in range(5):
        for y in range(10):
            i = (y * 10 + x) * 3
            b.pixels[i:i + 3] = bytes((0, 0, 0))
    diff = diff_region(a, b, "half")
    assert diff.differing_ratio == 0.5 and diff.mean_abs_diff == 100.0


def test_small_differences_below_the_threshold_do_not_count_as_different():
    a, b = solid(4, 4, (100, 100, 100)), solid(4, 4, (110, 100, 100))
    assert diff_region(a, b, "near", threshold=24).differing_ratio == 0 and diff_region(a, b, "near", threshold=5).differing_ratio == 1


def test_regions_of_different_size_are_refused():
    with pytest.raises(ValueError, match="differ in size"):
        diff_region(solid(2, 2, (0, 0, 0)), solid(3, 2, (0, 0, 0)), "x")


def test_the_demo_banner_is_cropped_before_comparing():
    reference = gradient(60, 40)
    rendered = solid(60, 40 + BANNER_PX, (253, 230, 138))
    rendered.pixels[(BANNER_PX * 60) * 3:] = reference.pixels  # below the banner the page equals the reference
    diffs = compare_to_reference(reference, rendered, regions=(("all", 0, 0, 60, 40),))
    assert diffs[0].mean_abs_diff == 0 and diffs[0].differing_ratio == 0


def test_a_render_of_the_wrong_size_says_what_size_to_render():
    with pytest.raises(ValueError, match=f"render the demo at 60 x {40 + BANNER_PX}"):
        compare_to_reference(gradient(60, 40), solid(60, 40, (0, 0, 0)), regions=(("all", 0, 0, 60, 40),))


def test_cropping_clips_to_the_image():
    image = gradient(10, 10)
    cut = image.crop(8, 8, 10, 10)
    assert (cut.width, cut.height) == (2, 2)
