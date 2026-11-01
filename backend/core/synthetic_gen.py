"""Deterministic synthetic cleanroom scene generator.

Uses NumPy's modern ``default_rng`` for reproducibility across Python versions.
For a given seed, the returned `Scene` is byte-identical, so a called-out seed
can be replayed in the UI without any persistence layer.
"""

from __future__ import annotations

import numpy as np

from agents.schemas import EquipmentItem, Scene

__all__ = ["CATALOG", "GRID_SIZE", "catalog_payload", "generate_scene"]

GRID_SIZE = 100
WALL_BUFFER = 3
PAIR_BUFFER = 2
MAX_ATTEMPTS = 2000

#: Master catalog. Footprints in grid cells; heat_watts and vibration_db chosen
#: to produce realistic conflicts (thermal + vibration) without exploding.
CATALOG: tuple[dict[str, float | int | str], ...] = (
    {"name": "LITHO_SCANNER", "w": 8, "h": 6, "heat_watts": 380.0, "vibration_db": 92.0},
    {"name": "ETCH_CHAMBER", "w": 5, "h": 5, "heat_watts": 220.0, "vibration_db": 78.0},
    {"name": "CMP_POLISHER", "w": 6, "h": 4, "heat_watts": 180.0, "vibration_db": 96.0},
    {"name": "ION_IMPLANTER", "w": 7, "h": 7, "heat_watts": 340.0, "vibration_db": 88.0},
    {"name": "THERMAL_CHAMBER", "w": 4, "h": 6, "heat_watts": 400.0, "vibration_db": 52.0},
    {"name": "METROLOGY", "w": 3, "h": 3, "heat_watts": 80.0, "vibration_db": 40.0},
    {"name": "WAFER_CLEAN", "w": 5, "h": 3, "heat_watts": 90.0, "vibration_db": 46.0},
    {"name": "DIFFUSION_FURNACE", "w": 4, "h": 8, "heat_watts": 300.0, "vibration_db": 58.0},
)

#: Short glyphs used by the drag-and-drop catalog cards in the SPA.
_GLYPHS: dict[str, str] = {
    "LITHO_SCANNER": "LS",
    "ETCH_CHAMBER": "EC",
    "CMP_POLISHER": "CP",
    "ION_IMPLANTER": "II",
    "THERMAL_CHAMBER": "TC",
    "METROLOGY": "MT",
    "WAFER_CLEAN": "WC",
    "DIFFUSION_FURNACE": "DF",
}


def catalog_payload() -> list[dict[str, float | int | str]]:
    """Return the equipment catalog as JSON-ready rows for the SPA.

    Single source of truth: the same ``CATALOG`` tuple that seeds the synthetic
    generator, so a machine the user drops behaves exactly like one the
    generator placed. ``id`` doubles as the catalog key and the id prefix.
    """
    rows: list[dict[str, float | int | str]] = []
    for template in CATALOG:
        name = str(template["name"])
        rows.append(
            {
                "id": name,
                "name": name.replace("_", " ").title(),
                "glyph": _GLYPHS.get(name, name[:2]),
                "w": int(template["w"]),
                "h": int(template["h"]),
                "heat_watts": float(template["heat_watts"]),
                "vibration_db": float(template["vibration_db"]),
            }
        )
    return rows


def _overlaps(a: dict, b: dict, buffer: int = PAIR_BUFFER) -> bool:
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    return not (
        ax2 + buffer <= bx1
        or bx2 + buffer <= ax1
        or ay2 + buffer <= by1
        or by2 + buffer <= ay1
    )


def generate_scene(seed: int, *, n_items: int = 8) -> Scene:
    """Generate a deterministic 100x100 cleanroom scene for ``seed``."""
    rng = np.random.default_rng(int(seed))
    placed: list[EquipmentItem] = []
    attempts = 0
    n_items = max(1, min(int(n_items), len(CATALOG)))

    while len(placed) < n_items and attempts < MAX_ATTEMPTS:
        attempts += 1
        template = CATALOG[len(placed) % len(CATALOG)]
        w, h = int(template["w"]), int(template["h"])

        x_high = GRID_SIZE - WALL_BUFFER - w
        y_high = GRID_SIZE - WALL_BUFFER - h
        if x_high <= WALL_BUFFER or y_high <= WALL_BUFFER:
            continue

        x = int(rng.integers(WALL_BUFFER, x_high))
        y = int(rng.integers(WALL_BUFFER, y_high))

        candidate = {"x": x, "y": y, "w": w, "h": h}
        if any(_overlaps(candidate, p.model_dump()) for p in placed):
            continue

        placed.append(
            EquipmentItem(
                id=f"{template['name']}-{len(placed):02d}",
                name=str(template["name"]).replace("_", " ").title(),
                x=x,
                y=y,
                w=w,
                h=h,
                heat_watts=float(template["heat_watts"]),
                vibration_db=float(template["vibration_db"]),
            )
        )

    return Scene(seed=int(seed), grid_size=GRID_SIZE, equipment=placed)
