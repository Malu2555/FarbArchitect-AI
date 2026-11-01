"""Dependency-free 5x7 bitmap font.

Why hand-rolled?
----------------
``core.png`` must stay within the frozen dependency list (no Pillow, no
matplotlib). A 5x7 bitmap font is a few hundred bytes of data and enough to
label machine footprints on the preview image.

Glyphs are written as string art (``#`` = pixel, ``.`` = empty) split by ``|``
for the seven scanlines — deliberately human-readable so the font can be
audited by eye rather than decoded from bitmasks.

Rendering is done straight into a NumPy RGB array with integer scaling, so
output stays crisp (no anti-aliasing artifacts at small sizes).
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "GLYPH_H",
    "GLYPH_GAP",
    "GLYPH_W",
    "SUPPORTED_CHARS",
    "draw_text",
    "measure_text",
]

GLYPH_W = 5
GLYPH_H = 7
GLYPH_GAP = 1  # blank column between glyphs, in glyph units

#: Each entry is 7 rows of 5 columns, rows separated by '|'.
_GLYPHS: dict[str, str] = {
    " ": ".....|.....|.....|.....|.....|.....|.....",
    "A": ".###.|#...#|#...#|#####|#...#|#...#|#...#",
    "B": "####.|#...#|#...#|####.|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.",
    "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|####.|#....|#....|#####",
    "F": "#####|#....|#....|####.|#....|#....|#....",
    "G": ".###.|#...#|#....|#.###|#...#|#...#|.###.",
    "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#",
    "I": "#####|..#..|..#..|..#..|..#..|..#..|#####",
    "J": "....#|....#|....#|....#|#...#|#...#|.###.",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#",
    "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#...#|#...#|#...#|#...#",
    "N": "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
    "P": "####.|#...#|#...#|####.|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#",
    "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.",
    "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "V": "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#...#|#.#.#|##.##|#...#",
    "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..",
    "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.",
    "1": "..#..|.##..|..#..|..#..|..#..|..#..|#####",
    "2": ".###.|#...#|....#|...#.|..#..|.#...|#####",
    "3": "#####|...#.|..#..|...#.|....#|#...#|.###.",
    "4": "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.",
    "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.",
    "7": "#####|....#|...#.|..#..|.#...|.#...|.#...",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.",
    "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "-": ".....|.....|.....|#####|.....|.....|.....",
    ".": ".....|.....|.....|.....|.....|.##..|.##..",
    "_": ".....|.....|.....|.....|.....|.....|#####",
    "/": "....#|....#|...#.|..#..|.#...|#....|#....",
    "(": "...#.|..#..|.#...|.#...|.#...|..#..|...#.",
    ")": ".#...|..#..|...#.|...#.|...#.|..#..|.#...",
    ":": ".....|.##..|.##..|.....|.##..|.##..|.....",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....",
    ",": ".....|.....|.....|.....|.##..|.##..|..#..",
}

SUPPORTED_CHARS = frozenset(_GLYPHS)

#: Fallback glyph used for any unsupported character.
_BLANK = _GLYPHS[" "]


def _glyph_rows(char: str) -> list[str]:
    return _GLYPHS.get(char.upper(), _BLANK).split("|")


def measure_text(text: str, scale: int = 2) -> int:
    """Width in pixels that ``text`` occupies at the given integer scale."""
    if not text:
        return 0
    glyph_advance = (GLYPH_W + GLYPH_GAP) * scale
    return len(text) * glyph_advance - GLYPH_GAP * scale


def draw_text(
    image: np.ndarray,
    text: str,
    x: int,
    y: int,
    *,
    color: tuple[int, int, int],
    scale: int = 2,
    max_width: int | None = None,
) -> int:
    """Blit ``text`` into ``image`` (H, W, 3 uint8) at top-left ``(x, y)``.

    ``max_width`` truncates the string at a character boundary so labels never
    bleed outside a footprint. Returns the drawn pixel width (0 when nothing
    fits). Out-of-bounds pixels are silently clipped.
    """
    if not text or scale < 1:
        return 0

    rendered = str(text).upper()
    if max_width is not None:
        if max_width <= 0:
            return 0
        while rendered and measure_text(rendered, scale) > max_width:
            rendered = rendered[:-1]
        if not rendered:
            return 0

    height, width = image.shape[0], image.shape[1]
    pen_x = 0
    glyph_advance = (GLYPH_W + GLYPH_GAP) * scale

    for char in rendered:
        base_x = x + pen_x
        for row_index, row in enumerate(_glyph_rows(char)):
            base_y = y + row_index * scale
            if base_y >= height:
                continue
            for col_index, on in enumerate(row):
                if on != "#":
                    continue
                pixel_x = base_x + col_index * scale
                if pixel_x >= width:
                    continue
                if pixel_x < 0 or base_y < 0:
                    continue
                x_end = min(width, pixel_x + scale)
                y_end = min(height, base_y + scale)
                image[base_y:y_end, pixel_x:x_end] = color
        pen_x += glyph_advance

    return min(measure_text(rendered, scale), max_width or 10**9)
