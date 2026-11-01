"""Minimal, dependency-free PNG encoder + a cleanroom rasteriser.

Why not Pillow?
---------------
The project's dependency list is intentionally frozen
(``django, django-ninja, numpy, scipy, httpx, pydantic``). A PNG encoder is a
small, well-specified format (signature + IHDR + IDAT + IEND chunks over zlib
DEFLATE), so we can emit valid files with only ``struct`` and ``zlib`` from the
standard library.

The rasteriser draws a *fallback* preview when the Muse Image renderer is not
available. It mirrors the pixel geometry used by the frontend canvas so both
views agree exactly.
"""

from __future__ import annotations

import base64
import struct
import zlib

import numpy as np

from core.font5x7 import GLYPH_H, GLYPH_W, draw_text, measure_text

__all__ = [
    "PNG_SIGNATURE",
    "png_data_url",
    "png_encode_rgb",
    "rasterize_cleanroom",
]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

GRID_SIZE = 100
CELL_SIZE_PX = 5
CANVAS_PX = GRID_SIZE * CELL_SIZE_PX  # 500

# Palette (RGB) shared with the frontend canvas.
_BG = (4, 7, 15)
_GRID = (30, 41, 59)

#: Per-`color_token` styling: (fill, edge, label text).
_TOKENS: dict[str, tuple[tuple[int, int, int], ...]] = {
    "teal": ((13, 60, 60), (20, 184, 166), (153, 246, 228)),
    "warn": ((60, 35, 13), (249, 115, 22), (254, 215, 170)),
    "danger": ((60, 13, 13), (239, 68, 68), (254, 202, 202)),
}

LABEL_SCALE = 2  # 10x14 px per glyph on a 500x500 canvas
LABEL_PAD = 3  # px inset from the footprint's top-left corner
OUTSIDE_PAD = 4  # px gap when a label is annotated beside its footprint



def _chunk(tag: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk: length | type | payload | CRC32(type+payload)."""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def png_encode_rgb(image: np.ndarray) -> bytes:
    """Encode an ``(H, W, 3)`` uint8 RGB array as a PNG byte stream."""
    if image.dtype != np.uint8:
        raise ValueError(f"png_encode_rgb expects uint8, got {image.dtype}")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"png_encode_rgb expects (H, W, 3), got {image.shape}")

    height, width, _ = image.shape
    # Bit depth 8, colour type 2 (truecolour RGB), default compression/filter/interlace.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    # PNG scanlines are prefixed with a filter-type byte; 0 = None.
    filtered = np.empty((height, width * 3 + 1), dtype=np.uint8)
    filtered[:, 0] = 0
    filtered[:, 1:] = image.reshape(height, width * 3)

    idat = zlib.compress(filtered.tobytes(), level=9)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def png_data_url(image: np.ndarray) -> str:
    """Return a ``data:image/png;base64,...`` URL for the given RGB array."""
    encoded = base64.b64encode(png_encode_rgb(image)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def rasterize_cleanroom(
    footprints: list[dict],
    *,
    cell_px: int = CELL_SIZE_PX,
    grid_size: int = GRID_SIZE,
) -> np.ndarray:
    """Rasterise footprint boxes into an RGB canvas array.

    ``footprints`` accepts the ``SpatialSpec`` shape::

        {"origin": [x, y], "size": [w, h], "label": str, "color_token": str}

    Labels are drawn with the bundled 5x7 bitmap font and truncated to the
    footprint width so text can never bleed outside its box.
    """
    size = grid_size * cell_px
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = _BG

    # Faint 10-cell grid, matching the frontend.
    for i in range(0, grid_size + 1, 10):
        at = min(size - 1, i * cell_px)
        img[:, at] = _GRID
        img[at, :] = _GRID

    for box in footprints:
        origin = box.get("origin") or (0, 0)
        dims = box.get("size") or (1, 1)
        x0 = int(max(0, min(int(origin[0]), grid_size - 1)))
        y0 = int(max(0, min(int(origin[1]), grid_size - 1)))
        w = int(max(1, min(int(dims[0]), grid_size - x0)))
        h = int(max(1, min(int(dims[1]), grid_size - y0)))

        token = str(box.get("color_token") or "teal")
        fill, edge, label_color = _TOKENS.get(token, _TOKENS["teal"])

        # Clip to the canvas so out-of-range cells can never wrap.
        X0, Y0 = x0 * cell_px, y0 * cell_px
        X1 = min(size - 1, X0 + w * cell_px - 1)
        Y1 = min(size - 1, Y0 + h * cell_px - 1)

        img[Y0:Y1, X0:X1] = fill
        img[Y0, X0 : X1 + 1] = edge
        img[Y1, X0 : X1 + 1] = edge
        img[Y0 : Y1 + 1, X0] = edge
        img[Y0 : Y1 + 1, X1] = edge

        label = str(box.get("label") or "").strip()
        if label:
            _draw_label(
                img,
                label,
                box_w=X1 - X0,
                box_h=Y1 - Y0,
                X0=X0,
                Y0=Y0,
                X1=X1,
                Y1=Y1,
                canvas=size,
                color=label_color,
            )

    return img


def _draw_label(
    image: np.ndarray,
    label: str,
    *,
    box_w: int,
    box_h: int,
    X0: int,
    Y0: int,
    X1: int,
    Y1: int,
    canvas: int,
    color: tuple[int, int, int],
) -> None:
    """Draw ``label`` inside its footprint when it fits, else beside the box.

    Placement ladder:
        1. Full label inside at scale 2 (most legible).
        2. Full label inside at scale 1.
        3. Full label annotated to the right/left of the box at scale 1.
        4. Truncated annotation in whichever side has more room.
    """
    # 1 & 2 — full label inside the footprint.
    for scale in (LABEL_SCALE, 1):
        needed_w = measure_text(label, scale) + LABEL_PAD * 2
        needed_h = GLYPH_H * scale + LABEL_PAD * 2
        if box_w >= needed_w and box_h >= needed_h:
            draw_text(
                image,
                label,
                X0 + LABEL_PAD,
                Y0 + LABEL_PAD,
                color=color,
                scale=scale,
                max_width=box_w - LABEL_PAD * 2,
            )
            return

    # 3 & 4 — annotate beside the footprint, vertically centred.
    scale = 1
    text_w = measure_text(label, scale)
    text_h = GLYPH_H * scale
    y = max(0, min(canvas - text_h, ((Y0 + Y1) // 2) - text_h // 2))

    right_start = X1 + OUTSIDE_PAD
    right_room = canvas - right_start - 2
    left_room = X0 - OUTSIDE_PAD - 2

    if right_room >= text_w:
        draw_text(image, label, right_start, y, color=color, scale=scale)
        return
    if left_room >= text_w:
        draw_text(image, label, X0 - OUTSIDE_PAD - text_w, y, color=color, scale=scale)
        return

    # Neither side fits the full label: use the wider side and truncate.
    if right_room >= left_room:
        min_chars = (GLYPH_W + 1) * scale
        if right_room >= min_chars:
            draw_text(
                image,
                label,
                right_start,
                y,
                color=color,
                scale=scale,
                max_width=right_room,
            )
        return

    min_chars = (GLYPH_W + 1) * scale
    if left_room < min_chars:
        return
    truncated = label
    while truncated and measure_text(truncated, scale) > left_room:
        truncated = truncated[:-1]
    if truncated:
        draw_text(
            image,
            truncated,
            X0 - OUTSIDE_PAD - measure_text(truncated, scale),
            y,
            color=color,
            scale=scale,
        )
