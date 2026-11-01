"""Debug helper — render sample text as ASCII art to eyeball the 5x7 font.

Usage::

    python scripts/preview_font.py "LITHO SCANNER"
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from core.font5x7 import GLYPH_H, draw_text, measure_text  # noqa: E402  # type: ignore[reportMissingImports]

WHITE = (255, 255, 255)


def render(text: str, scale: int = 1) -> str:
    width = measure_text(text, scale) + 2
    image = np.zeros((GLYPH_H * scale + 2, max(width, 1), 3), dtype=np.uint8)
    draw_text(image, text, 1, 1, color=WHITE, scale=scale)

    lines = []
    for row in image:
        lines.append("".join("#" if px[0] == 255 else "." for px in row))
    return "\n".join(lines)


def main() -> int:
    samples = sys.argv[1:] or ["LITHO SCANNER", "ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ0123", "456789-._/"]
    for sample in samples:
        print(f"--- {sample!r} (scale 1) ---")
        print(render(sample, scale=1))
        print()
    print("--- 'ETCH' at scale 2 ---")
    print(render("ETCH", scale=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
