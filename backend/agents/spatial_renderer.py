"""Stage 2 of the dual-Muse pipeline — spatial layout renderer (Muse Image).

Flow
----
1. **Deterministic sanitize** — clamp every footprint into the grid and push
   apart any overlaps. This guarantees a structurally valid layout *before*
   any model sees it, and is what the frontend canvas receives if Muse Image
   is unavailable.
2. **Muse Image ratification** — send the sanitized layout to the Muse Image
   model with a strict JSON schema. It returns canvas-ready ``footprints``.
   Everything it returns is re-validated locally (never trusted blindly).
3. **PNG preview** — rasterize the final spec with the dependency-free encoder
   in ``core.png`` and ship a ``data:image/png;base64,...`` thumbnail.

The result always carries provenance so the UI can show whether the layout was
ratified by the model (``MUSE_IMAGE``) or produced by the local fallback
(``FALLBACK``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.schemas import (
    CanvasProvenance,
    EquipmentItem,
    FootprintBox,
    SpatialResult,
    SpatialSpec,
)
from core.png import (
    CELL_SIZE_PX,
    GRID_SIZE,
    png_data_url,
    rasterize_cleanroom,
)
from core.providers import MetaImageProvider, ProviderError

logger = logging.getLogger(__name__)

__all__ = [
    "SPATIAL_SCHEMA",
    "SPATIAL_SYSTEM_PROMPT",
    "build_fallback_result",
    "render_spatial",
    "sanitize_layout",
    "validate_layout",
]

MAX_PUSH_ITERATIONS = 60
PUSH_RADIUS = 40

SPATIAL_SYSTEM_PROMPT = (
    "You are fabArchitect AI's spatial layout renderer (Muse Image). "
    "Input: a cleanroom machine layout on a 100x100 cell grid, rendered to a "
    "500x500 pixel canvas at 5 px per cell. "
    "Task: emit canvas-ready footprints as JSON. "
    "Hard rules: (1) every footprint lies fully inside [0,100) on both axes; "
    "(2) no two footprints overlap; (3) width/height are unchanged from input; "
    "(4) origin is the top-left cell. "
    "If an input machine would clip or overlap, nudge it and add a note. "
    "Reply with ONE JSON object and nothing else: "
    '{"footprints": [{"id": str, "origin": [x, y], "size": [w, h], '
    '"label": str, "z_index": int, "color_token": "teal"|"warn"|"danger"}], '
    '"notes": [str]}'
)

SPATIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["footprints"],
    "properties": {
        "footprints": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "origin", "size"],
                "properties": {
                    "id": {"type": "string"},
                    "origin": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "size": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "label": {"type": "string"},
                    "z_index": {"type": "integer"},
                    "color_token": {"type": "string"},
                },
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _rects_overlap(a: EquipmentItem, b: EquipmentItem) -> bool:
    return not (
        a.x + a.w <= b.x
        or b.x + b.w <= a.x
        or a.y + a.h <= b.y
        or b.y + b.h <= a.y
    )


def _clamp(item: EquipmentItem) -> EquipmentItem:
    nx = max(0, min(item.x, GRID_SIZE - item.w))
    ny = max(0, min(item.y, GRID_SIZE - item.h))
    if (nx, ny) == (item.x, item.y):
        return item
    return item.model_copy(update={"x": nx, "y": ny})


def _try_move(item: EquipmentItem, dx: int, dy: int) -> EquipmentItem | None:
    nx = max(0, min(item.x + dx, GRID_SIZE - item.w))
    ny = max(0, min(item.y + dy, GRID_SIZE - item.h))
    if (nx, ny) == (item.x, item.y):
        return None
    return item.model_copy(update={"x": nx, "y": ny})


def _resolve_overlap(anchor: EquipmentItem, mover: EquipmentItem) -> EquipmentItem:
    """Push ``mover`` away from ``anchor`` until they no longer intersect."""
    ax = anchor.x + anchor.w / 2.0
    ay = anchor.y + anchor.h / 2.0
    bx = mover.x + mover.w / 2.0
    by = mover.y + mover.h / 2.0
    sign_x = 1 if bx >= ax else -1
    sign_y = 1 if by >= ay else -1

    current = mover
    for step in range(1, PUSH_RADIUS + 1):
        for dx, dy in (
            (sign_x * step, 0),
            (0, sign_y * step),
            (sign_x * step, sign_y * step),
            (-sign_x * step, 0),
            (0, -sign_y * step),
        ):
            candidate = _try_move(current, dx, dy)
            if candidate is None:
                continue
            if not _rects_overlap(anchor, candidate):
                return candidate
    return current


def sanitize_layout(
    equipment: list[EquipmentItem],
) -> tuple[list[EquipmentItem], list[str]]:
    """Clamp into the grid, then push apart pairwise overlaps. Deterministic."""
    notes: list[str] = []
    working: list[EquipmentItem] = []

    for item in equipment:
        clamped = _clamp(item)
        if (clamped.x, clamped.y) != (item.x, item.y):
            notes.append(
                f"clamped {item.id} from ({item.x},{item.y}) to ({clamped.x},{clamped.y})"
            )
        working.append(clamped)

    for _ in range(MAX_PUSH_ITERATIONS):
        conflicts = 0
        for i in range(len(working)):
            for j in range(i + 1, len(working)):
                anchor, mover = working[i], working[j]
                if not _rects_overlap(anchor, mover):
                    continue
                conflicts += 1
                resolved = _resolve_overlap(anchor, mover)
                if resolved is not mover:
                    working[j] = resolved
                    notes.append(f"pushed {mover.id} away from {anchor.id}")
        if conflicts == 0:
            break

    return working, notes


def validate_layout(equipment: list[EquipmentItem]) -> tuple[bool, bool]:
    """Return ``(no_overlap, no_clipping)`` for a layout."""
    no_clipping = all(
        item.x >= 0
        and item.y >= 0
        and item.x + item.w <= GRID_SIZE
        and item.y + item.h <= GRID_SIZE
        for item in equipment
    )
    no_overlap = True
    for i in range(len(equipment)):
        for j in range(i + 1, len(equipment)):
            if _rects_overlap(equipment[i], equipment[j]):
                no_overlap = False
                break
        if not no_overlap:
            break
    return no_overlap, no_clipping


# ---------------------------------------------------------------------------
# Spec construction
# ---------------------------------------------------------------------------
def _boxes_from_layout(equipment: list[EquipmentItem]) -> list[FootprintBox]:
    return [
        FootprintBox(
            id=item.id,
            origin=(item.x, item.y),
            size=(item.w, item.h),
            label=item.name,
            z_index=index,
            color_token="teal",
        )
        for index, item in enumerate(equipment)
    ]


def _spec_from_layout(
    equipment: list[EquipmentItem],
    notes: list[str],
    source: str,
) -> SpatialSpec:
    no_overlap, no_clipping = validate_layout(equipment)
    return SpatialSpec(
        footprints=_boxes_from_layout(equipment),
        canvas=CanvasProvenance(
            grid_size=GRID_SIZE,
            cell_size_px=CELL_SIZE_PX,
            canvas_px=(GRID_SIZE * CELL_SIZE_PX, GRID_SIZE * CELL_SIZE_PX),
            no_overlap=no_overlap,
            no_clipping=no_clipping,
            source=source,  # type: ignore[arg-type]
            notes=notes,
        ),
    )


def _spec_to_footprint_dicts(spec: SpatialSpec) -> list[dict]:
    """Adapter for the rasterizer, which expects the wire-format dict shape."""
    return [
        {
            "origin": [box.origin[0], box.origin[1]],
            "size": [box.size[0], box.size[1]],
            "label": box.label,
            "color_token": box.color_token,
        }
        for box in spec.footprints
    ]


def _result_from_spec(spec: SpatialSpec) -> SpatialResult:
    image = rasterize_cleanroom(_spec_to_footprint_dicts(spec))
    return SpatialResult(spatial_spec=spec, preview_png_data_url=png_data_url(image))


def build_fallback_result(equipment: list[EquipmentItem]) -> SpatialResult:
    """Full local pipeline: sanitize -> spec -> PNG. No network calls."""
    sanitized, notes = sanitize_layout(equipment)
    spec = _spec_from_layout(sanitized, notes, source="FALLBACK")
    return _result_from_spec(spec)


# ---------------------------------------------------------------------------
# Muse Image call + validation of its reply
# ---------------------------------------------------------------------------
def _build_messages(
    equipment: list[EquipmentItem], notes: list[str]
) -> list[dict[str, Any]]:
    payload = {
        "grid_size": GRID_SIZE,
        "cell_size_px": CELL_SIZE_PX,
        "canvas_px": [GRID_SIZE * CELL_SIZE_PX, GRID_SIZE * CELL_SIZE_PX],
        "machines": [
            {
                "id": item.id,
                "name": item.name,
                "origin": [item.x, item.y],
                "size": [item.w, item.h],
            }
            for item in equipment
        ],
        "sanitizer_notes": notes,
    }
    return [
        {"role": "system", "content": SPATIAL_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Render this layout as canvas footprints.\n\n"
            + json.dumps(payload, separators=(",", ":")),
        },
    ]


def _parse_muse_reply(
    payload: dict[str, Any],
    sanitized: list[EquipmentItem],
) -> SpatialSpec | None:
    """Trust-but-verify: rein the model's reply back onto known geometry.

    Returns ``None`` when the reply is unusable, in which case the caller
    keeps the deterministic fallback.
    """
    raw_boxes = payload.get("footprints") if isinstance(payload, dict) else None
    if not isinstance(raw_boxes, list) or not raw_boxes:
        return None

    by_id = {item.id: item for item in sanitized}
    notes = [str(n)[:200] for n in (payload.get("notes") or [])][:8]
    boxes: list[FootprintBox] = []

    for index, raw in enumerate(raw_boxes):
        if not isinstance(raw, dict):
            continue
        machine = by_id.get(str(raw.get("id", "")))
        if machine is None:
            continue  # never invent machines
        try:
            x = int(raw["origin"][0])
            y = int(raw["origin"][1])
            w = int(raw["size"][0])
            h = int(raw["size"][1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if w != machine.w or h != machine.h or w <= 0 or h <= 0:
            continue  # sizes are immutable
        x = max(0, min(x, GRID_SIZE - w))
        y = max(0, min(y, GRID_SIZE - h))
        token = str(raw.get("color_token", "teal"))
        if token not in ("teal", "warn", "danger"):
            token = "teal"
        boxes.append(
            FootprintBox(
                id=machine.id,
                origin=(x, y),
                size=(w, h),
                label=str(raw.get("label") or machine.name)[:120],
                z_index=int(raw.get("z_index", index) or index),
                color_token=token,  # type: ignore[arg-type]
            )
        )

    if len(boxes) != len(sanitized):
        return None

    # Re-validate the model's geometry with our own checker. If it introduced
    # an overlap we reject the whole reply rather than ship a broken layout.
    rebuilt = [
        EquipmentItem(
            id=box.id,
            name=box.label,
            x=box.origin[0],
            y=box.origin[1],
            w=box.size[0],
            h=box.size[1],
            heat_watts=by_id[box.id].heat_watts,
            vibration_db=by_id[box.id].vibration_db,
        )
        for box in boxes
    ]
    no_overlap, no_clipping = validate_layout(rebuilt)
    if not (no_overlap and no_clipping):
        logger.warning(
            "muse_image_reply_failed_validation overlaps=%s clipping=%s",
            not no_overlap,
            not no_clipping,
        )
        return None

    return SpatialSpec(
        footprints=sorted(boxes, key=lambda b: (b.z_index, b.id)),
        canvas=CanvasProvenance(
            grid_size=GRID_SIZE,
            cell_size_px=CELL_SIZE_PX,
            canvas_px=(GRID_SIZE * CELL_SIZE_PX, GRID_SIZE * CELL_SIZE_PX),
            no_overlap=no_overlap,
            no_clipping=no_clipping,
            source="MUSE_IMAGE",
            notes=notes,
        ),
    )


async def render_spatial(
    equipment: list[EquipmentItem],
    *,
    provider: MetaImageProvider | None = None,
) -> SpatialResult:
    """Run the full spatial stage: sanitize -> (Muse Image) -> spec + PNG."""
    if not equipment:
        return _result_from_spec(_spec_from_layout([], ["empty layout"], "FALLBACK"))

    sanitized, notes = sanitize_layout(equipment)

    if provider is not None:
        try:
            payload = await provider.complete(
                _build_messages(sanitized, notes),
                schema=SPATIAL_SCHEMA,
                max_tokens=1500,
                temperature=0.0,
            )
        except ProviderError as exc:
            logger.warning("muse_image_unavailable code=%s", exc.code)
        else:
            spec = _parse_muse_reply(payload, sanitized)
            if spec is not None:
                return _result_from_spec(spec)
            logger.warning("muse_image_reply_unusable falling back")

    spec = _spec_from_layout(sanitized, notes, source="FALLBACK")
    return _result_from_spec(spec)


