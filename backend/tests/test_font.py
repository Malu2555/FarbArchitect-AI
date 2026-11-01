"""Tests for the dependency-free 5x7 bitmap font and label rendering."""

from __future__ import annotations

import numpy as np

from core.font5x7 import (
    GLYPH_GAP,
    GLYPH_H,
    GLYPH_W,
    draw_text,
    measure_text,
)
from core.png import rasterize_cleanroom


def _blank(w: int = 60, h: int = 20) -> np.ndarray:
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = (0, 0, 0)
    return image


def test_measure_text_matches_glyph_advance() -> None:
    text = "ABCDE"
    expected = len(text) * (GLYPH_W + GLYPH_GAP) - GLYPH_GAP
    assert measure_text(text, scale=1) == expected
    assert measure_text(text, scale=3) == expected * 3


def test_measure_text_empty_is_zero() -> None:
    assert measure_text("", scale=2) == 0


def test_draw_text_blits_pixels() -> None:
    image = _blank()
    drawn = draw_text(image, "A", 2, 2, color=(255, 255, 255), scale=1)

    assert drawn > 0
    assert (image == 255).any(), "no glyph pixels were written"


def test_draw_text_uppercases_and_supports_lowercase_input() -> None:
    lower = _blank()
    upper = _blank()
    draw_text(lower, "etch", 0, 0, color=(255, 255, 255), scale=1)
    draw_text(upper, "ETCH", 0, 0, color=(255, 255, 255), scale=1)

    assert np.array_equal(lower, upper)


def test_draw_text_truncates_to_max_width() -> None:
    image = _blank(w=200, h=30)
    drawn = draw_text(
        image, "LITHO SCANNER", 0, 0, color=(255, 255, 255), scale=2, max_width=40
    )

    assert 0 < drawn <= 40
    # Nothing may be written past the requested width.
    assert not (image[:, 41:] == 255).any()


def test_draw_text_clips_out_of_bounds_without_raising() -> None:
    image = _blank(w=20, h=10)
    # Partially off-canvas in both axes: must clip, not raise.
    draw_text(image, "ABC", 15, 8, color=(255, 255, 255), scale=1)


def test_draw_text_ignores_unsupported_characters() -> None:
    image = _blank(w=80, h=20)
    # Unsupported glyphs map to blanks; drawing must not raise.
    draw_text(image, "Ω≈ç", 0, 0, color=(255, 255, 255), scale=1)
    assert not (image == 255).any()


def test_rasterize_draws_labels_inside_footprints() -> None:
    footprints = [
        {
            "origin": [10, 10],
            "size": [20, 10],  # 100 x 50 px — plenty of room for a label
            "label": "LITHO SCANNER",
            "color_token": "teal",
        }
    ]

    image = rasterize_cleanroom(footprints)

    # The label text colour is (153, 246, 228) for the teal token.
    label_pixels = np.all(image == (153, 246, 228), axis=-1)
    assert label_pixels.any(), "no label pixels rendered"

    # Every label pixel must sit inside the footprint box (origin 10,10 -> 50,50).
    ys, xs = np.nonzero(label_pixels)
    assert ys.min() >= 50, f"label above footprint top (min y={ys.min()})"
    assert ys.max() <= 99, f"label below footprint bottom (max y={ys.max()})"
    assert xs.min() >= 50, f"label left of footprint (min x={xs.min()})"
    assert xs.max() <= 149, f"label past footprint right (max x={xs.max()})"


def test_rasterize_respects_color_token() -> None:
    box = {
        "origin": [10, 10],
        "size": [10, 10],
        "label": "",
        "color_token": "danger",
    }
    image = rasterize_cleanroom([box])

    # Danger edge colour is (239, 68, 68).
    assert np.all(image == (239, 68, 68), axis=-1).any(), "danger edge not drawn"


def test_rasterize_without_label_leaves_interior_clean() -> None:
    box = {"origin": [10, 10], "size": [10, 10], "label": ""}
    image = rasterize_cleanroom([box])

    # The default teal label colour must not appear when there is no label.
    assert not np.all(image == (153, 246, 228), axis=-1).any()


def test_rasterize_annotates_tiny_footprints_beside_the_box() -> None:
    footprints = [
        {"origin": [5, 5], "size": [3, 3], "label": "METROLOGY", "color_token": "teal"}
    ]
    image = rasterize_cleanroom(footprints)

    label_pixels = np.all(image == (153, 246, 228), axis=-1)
    assert label_pixels.any(), "no label pixels for a tiny footprint"

    ys, xs = np.nonzero(label_pixels)
    # 3x3 cells at (5,5) -> box spans x 25..39. Annotation goes to the right.
    assert xs.min() >= 40, f"label not placed beside the box (min x={xs.min()})"


def test_rasterize_full_label_inside_when_it_fits() -> None:
    # 40 x 10 cells = 200 x 50 px: wide enough for "LITHO SCANNER" at scale 2.
    footprints = [
        {
            "origin": [10, 10],
            "size": [40, 10],
            "label": "LITHO SCANNER",
            "color_token": "teal",
        }
    ]
    image = rasterize_cleanroom(footprints)

    label_pixels = np.all(image == (153, 246, 228), axis=-1)
    assert label_pixels.any()

    ys, xs = np.nonzero(label_pixels)
    assert xs.max() < 250, "scale-2 label should fit inside a 200 px box"


def test_font_height_constant() -> None:
    assert GLYPH_H == 7
    assert GLYPH_W == 5
