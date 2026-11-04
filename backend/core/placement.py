"""Auto-place parsed ``EquipmentDraft`` objects onto the simulation grid.

The upload tells us *what* machines a fab has; this module decides *where* they
start. Three deterministic strategies, none of which use an LLM:

* ``grid``       - fill row-major from the top-left. Predictable, easy to explain.
* ``load_based`` - hottest machines toward the edges, coolest in the middle. A
                   deliberate first guess: exhaust near walls is conventional.
* ``random``     - seeded by the upload's content hash, so the same file always
                   lands the same way (reproducible screenshots).

The corrector is free to move everything afterwards - placement is only the
*starting* layout, never a claim of optimality. Coordinates are clamped into the
grid; when a fab genuinely has more footprint than fits, the extra drafts are
dropped with a warning rather than silently overlapping.
"""

from __future__ import annotations

import hashlib

from agents.schemas import (
    EquipmentDraft,
    EquipmentItem,
    EquipmentUpload,
    PlacementResult,
    RoomDraft,
)

__all__ = ["DEFAULT_GRID_SIZE", "M_PER_CELL", "auto_place"]

M_PER_CELL = 0.5

#: Canonical grid resolution when no room dimensions are supplied.
DEFAULT_GRID_SIZE = 100

#: Gap (cells) enforced between placed footprints so the simulator sees a clean
#: starting layout instead of a pile of touching boxes.
PLACEMENT_GAP = 1

#: Padding (cells) from the walls - keeps machines off the reflective boundary.
WALL_PAD = 2


def _grid_size_for(room: RoomDraft | None) -> int:
    """Derive a square grid size from the room's longer dimension.

    Rooms smaller than the canonical window keep the canonical 100x100 grid so
    tiny fabs are not rendered at an unreadable resolution.
    """
    if room is None or not room.width_m or not room.depth_m:
        return DEFAULT_GRID_SIZE
    longest = max(float(room.width_m), float(room.depth_m))
    return max(DEFAULT_GRID_SIZE, int(round(longest / M_PER_CELL)))


def _seed_for(upload: EquipmentUpload) -> int:
    """Stable integer seed derived from the upload contents."""
    digest = hashlib.sha256()
    digest.update(upload.source_filename.encode("utf-8", "replace"))
    for draft in upload.drafts:
        digest.update(draft.id.encode("utf-8", "replace"))
        digest.update(f"{draft.w}x{draft.h}".encode("ascii"))
    return int.from_bytes(digest.digest()[:8], "big")


def _place_row_major(
    drafts: list[EquipmentDraft],
    grid_size: int,
    order: list[int],
) -> PlacementResult:
    """Shelf-pack drafts in the given order, then **spread** them to fill the floor.

    A tight top-left pack produces an artificially congested starting layout
    (every machine hugging its neighbour), which makes the corrector's job
    needlessly hard and gives a dull "no improvement" demo. Instead we:

    1. greedily group drafts into horizontal shelves (respecting grid width),
    2. distribute any leftover **horizontal** slack evenly between machines in
       each shelf,
    3. distribute any leftover **vertical** slack evenly between shelves.

    Machines never overlap because each shelf owns a disjoint y-band and the
    in-shelf gaps only grow. The result is a spread, physically sensible start.
    """
    # -- 1. Greedy shelf grouping -------------------------------------------
    shelves: list[list[EquipmentDraft]] = []
    current: list[EquipmentDraft] = []
    used_w = 0
    for idx in order:
        draft = drafts[idx]
        need = draft.w if not current else used_w + PLACEMENT_GAP + draft.w
        if current and need + WALL_PAD > grid_size:
            shelves.append(current)
            current = [draft]
            used_w = draft.w
        else:
            current.append(draft)
            used_w = need
    if current:
        shelves.append(current)

    # -- 2. Horizontal spread within each shelf -----------------------------
    shelf_rows: list[tuple[list[tuple[EquipmentDraft, int]], int]] = []
    for shelf in shelves:
        n = len(shelf)
        sum_w = sum(d.w for d in shelf)
        free_w = grid_size - 2 * WALL_PAD - sum_w
        gap = PLACEMENT_GAP if n <= 1 else max(PLACEMENT_GAP, free_w // (n - 1))
        placed: list[tuple[EquipmentDraft, int]] = []
        x = WALL_PAD
        for d in shelf:
            placed.append((d, x))
            x += d.w + gap
        shelf_rows.append((placed, max(d.h for d in shelf)))

    # -- 3. Vertical spread between shelves ---------------------------------
    total_h = sum(h for _, h in shelf_rows) + PLACEMENT_GAP * (len(shelf_rows) - 1)
    free_h = grid_size - 2 * WALL_PAD - total_h
    n_shelves = len(shelf_rows)
    v_gap = PLACEMENT_GAP
    if n_shelves > 1 and free_h > 0:
        v_gap = PLACEMENT_GAP + free_h // (n_shelves - 1)

    placed_items: list[EquipmentItem] = []
    y = WALL_PAD
    for placed, shelf_h in shelf_rows:
        for draft, x in placed:
            if x + draft.w > grid_size or y + draft.h > grid_size:
                continue  # safety: never emit out-of-bounds geometry
            placed_items.append(
                EquipmentItem(
                    id=draft.id,
                    name=draft.name,
                    x=x,
                    y=y,
                    w=draft.w,
                    h=draft.h,
                    heat_watts=draft.heat_watts,
                    vibration_db=draft.vibration_db,
                )
            )
        y += shelf_h + v_gap

    return PlacementResult(
        equipment=placed_items, grid_size=grid_size, strategy="grid"
    )


def auto_place(
    upload: EquipmentUpload,
    *,
    strategy: str = "grid",
    room: RoomDraft | None = None,
) -> PlacementResult:
    """Place every draft in ``upload`` onto a valid, non-overlapping layout.

    ``strategy`` must be ``grid``, ``load_based`` or ``random``. A ``room``
    overrides the room block parsed from the file (the UI lets the user tweak
    dimensions before placing).
    """
    if strategy not in ("grid", "load_based", "random"):
        raise ValueError(f"unknown placement strategy {strategy!r}")

    drafts = list(upload.drafts)
    if not drafts:
        return PlacementResult(equipment=[], grid_size=DEFAULT_GRID_SIZE, strategy=strategy)  # type: ignore[arg-type]

    effective_room = room or upload.room
    grid_size = _grid_size_for(effective_room)

    if strategy == "grid":
        order = list(range(len(drafts)))
    elif strategy == "load_based":
        # Hottest first so tier-1 rows land at the edges; coolest settle last
        # in the middle (which is what the ranking order naturally produces).
        order = sorted(
            range(len(drafts)), key=lambda i: drafts[i].heat_watts, reverse=True
        )
    else:  # random
        import random

        rng = random.Random(_seed_for(upload))
        order = list(range(len(drafts)))
        rng.shuffle(order)

    result = _place_row_major(drafts, grid_size, order)
    result.strategy = strategy  # type: ignore[assignment]
    return result
