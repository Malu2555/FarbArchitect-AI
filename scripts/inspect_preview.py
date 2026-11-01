"""Debug helper — inspect the fallback preview PNG for label rendering.

Usage::

    python scripts/inspect_preview.py [seed]
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from agents.spatial_renderer import build_fallback_result  # noqa: E402  # pyright: ignore[reportMissingImports]
from core.synthetic_gen import generate_scene  # noqa: E402  # pyright: ignore[reportMissingImports]

TOKEN_LABEL_COLORS = {
    "teal": (153, 246, 228),
    "warn": (254, 215, 170),
    "danger": (254, 202, 202),
}


def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42

    scene = generate_scene(seed)
    result = build_fallback_result(scene.equipment)
    spec = result.spatial_spec

    raw = base64.b64decode(result.preview_png_data_url.split(",", 1)[1])
    print(f"seed={seed}  footprints={len(spec.footprints)}  png={len(raw)} bytes")

    # Rebuild the raster so we can count label pixels per token.
    from core.png import rasterize_cleanroom  # pyright: ignore[reportMissingImports]

    boxes = [
        {
            "origin": [b.origin[0], b.origin[1]],
            "size": [b.size[0], b.size[1]],
            "label": b.label,
            "color_token": b.color_token,
        }
        for b in spec.footprints
    ]
    image = rasterize_cleanroom(boxes)

    total_label_px = 0
    for token, colour in TOKEN_LABEL_COLORS.items():
        mask = np.all(image == colour, axis=-1)
        count = int(mask.sum())
        total_label_px += count
        if count:
            ys, xs = np.nonzero(mask)
            print(
                f"  {token:6s} label pixels={count:5d} "
                f"bbox=({xs.min()},{ys.min()})-({xs.max()},{ys.max()})"
            )

    print(f"  total label pixels = {total_label_px}")

    print("  footprints (cell coords -> pixel coords) and labels:")
    for box in spec.footprints:
        x, y = box.origin
        w, h = box.size
        print(
            f"    {box.label:<22s} origin=({x:3d},{y:3d}) size=({w:2d},{h:2d}) "
            f"px=({x * 5},{y * 5})-({(x + w) * 5},{(y + h) * 5})"
        )

    out = Path(__file__).resolve().parents[1] / "preview_sample.png"
    out.write_bytes(raw)
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
