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
    SimulationResult,
    SpatialResult,
    SpatialSpec,
    TEMP_LIMIT_C,
)
from core.png import (
    CELL_SIZE_PX,
    GRID_SIZE,
    png_data_url,
    rasterize_cleanroom,
)
from core.providers import (
    FireworksImageProvider,
    MetaImageProvider,
    ProviderError,
)

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
    "You may use color_token=\"warn\"/\"danger\" on any machine flagged in a "
    "machine's \"hazards\" field (thermal or vibration) - hazards are provided "
    "by the physics solver, so never invent or drop them. "
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
# Hazard derivation (per-machine, drives the canvas edge stripes)
# ---------------------------------------------------------------------------
def _derive_hazards(
    equipment: list[EquipmentItem],
    sims: "SimulationResult | list[SimulationResult] | None",
) -> dict[str, list[str]]:
    """Map ``machine_id -> ordered list of hazard classes``.

    The solver (``core/heat_solver.py`` / ``core/vibration.py``) is the single
    source of truth for *which* machines are in trouble - we do not re-guess
    geometry here. ``sims`` may be a single result or a list; when a list is
    given (typically ``[baseline, best]``) the hazard tags are **unioned by
    machine id**, so a machine that was a thermal/vibration offender at *any*
    point in the agentic run keeps its indicator on the final canvas. This is an
    explainability view: the stripes say "this machine had a thermal / vibration
    problem", even if the corrector has since moved it clear.

    Two hazard kinds are surfaced:

    * ``"thermal"`` - every machine that produced a ``type=="thermal"`` hazard
      (i.e. its footprint centre exceeded the 26 C limit). The solver emits one
      such hazard per violating machine, so all of them get a stripe.
    * ``"vibration"`` - both sides of every ``type=="vibration"`` hazard pair.

    Safety net: if a run's *global* peak exceeds the limit but no per-machine
    thermal hazard was emitted (possible when two Gaussian heat fields overlap
    between machines and each individual centre stays below the limit), mark the
    single machine nearest ``peak_at`` so the heatmap always has at least one
    corresponding box indicator.

    Returns an empty mapping when ``sims`` is ``None`` (deterministic fallback
    path), in which case every box is rendered without stripes.
    """
    if sims is None:
        return {}

    if isinstance(sims, SimulationResult):
        sim_list: list[SimulationResult] = [sims]
    else:
        sim_list = list(sims)

    known_ids = {item.id for item in equipment}
    hazards: dict[str, list[str]] = {}

    def _mark(machine_id: str | None, kind: str) -> None:
        if not machine_id:
            return
        bucket = hazards.setdefault(machine_id, [])
        if kind not in bucket:
            bucket.append(kind)

    for sim in sim_list:
        marked_thermal = False
        for hazard in sim.hazards:
            if hazard.type == "vibration":
                _mark(hazard.a_id, "vibration")
                _mark(hazard.b_id, "vibration")
            elif hazard.type == "thermal":
                _mark(hazard.a_id, "thermal")
                if hazard.a_id in known_ids:
                    marked_thermal = True

        # Safety net: global peak above limit but no *known* machine carries a
        # per-machine thermal hazard. Mark the machine nearest the peak so the
        # heatmap always has at least one corresponding box indicator.
        if not marked_thermal and sim.peak_c > TEMP_LIMIT_C and equipment:
            px, py = sim.peak_at
            best_id: str | None = None
            best_dist = float("inf")
            for item in equipment:
                cx = item.x + item.w / 2.0
                cy = item.y + item.h / 2.0
                dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_id = item.id
            if best_id is not None:
                _mark(best_id, "thermal")

    # Drop ids that are not part of the layout we are about to render (e.g. a
    # hazard recorded against a machine that sanitisation dropped).
    return {k: v for k, v in hazards.items() if k in known_ids}


# ---------------------------------------------------------------------------
# Spec construction
# ---------------------------------------------------------------------------
def _boxes_from_layout(
    equipment: list[EquipmentItem],
    hazards_by_id: dict[str, list[str]] | None = None,
) -> list[FootprintBox]:
    hazards_by_id = hazards_by_id or {}
    boxes: list[FootprintBox] = []
    for index, item in enumerate(equipment):
        hazards = list(hazards_by_id.get(item.id, []))
        boxes.append(
            FootprintBox(
                id=item.id,
                origin=(item.x, item.y),
                size=(item.w, item.h),
                label=item.name,
                z_index=index,
                color_token="teal",
                hazards=hazards,  # type: ignore[arg-type]
            )
        )
    return boxes


def _spec_from_layout(
    equipment: list[EquipmentItem],
    notes: list[str],
    source: str,
    hazards_by_id: dict[str, list[str]] | None = None,
) -> SpatialSpec:
    no_overlap, no_clipping = validate_layout(equipment)
    return SpatialSpec(
        footprints=_boxes_from_layout(equipment, hazards_by_id),
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
            "hazards": list(box.hazards),
        }
        for box in spec.footprints
    ]


def _result_from_spec(spec: SpatialSpec) -> SpatialResult:
    image = rasterize_cleanroom(_spec_to_footprint_dicts(spec))
    return SpatialResult(spatial_spec=spec, preview_png_data_url=png_data_url(image))


def build_fallback_result(
    equipment: list[EquipmentItem],
    sim: SimulationResult | list[SimulationResult] | None = None,
) -> SpatialResult:
    """Full local pipeline: sanitize -> spec -> PNG. No network calls."""
    sanitized, notes = sanitize_layout(equipment)
    hazards_by_id = _derive_hazards(sanitized, sim)
    spec = _spec_from_layout(
        sanitized, notes, source="FALLBACK", hazards_by_id=hazards_by_id
    )
    return _result_from_spec(spec)


# ---------------------------------------------------------------------------
# Muse Image call + validation of its reply
# ---------------------------------------------------------------------------
def _build_messages(
    equipment: list[EquipmentItem],
    notes: list[str],
    hazards_by_id: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    hazards_by_id = hazards_by_id or {}
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
                "hazards": list(hazards_by_id.get(item.id, [])),
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
    hazards_by_id: dict[str, list[str]] | None = None,
) -> SpatialSpec | None:
    """Trust-but-verify: rein the model's reply back onto known geometry.

    Returns ``None`` when the reply is unusable, in which case the caller
    keeps the deterministic fallback.

    The model cannot be trusted to identify *hazards* (it cannot run the
    solver), so any hazard stickers we computed locally from the post-correction
    simulation are re-attached here by machine id. That preserves the visual
    edge stripes in the Muse Image path regardless of what the model returned.
    """
    hazards_by_id = hazards_by_id or {}
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
        hazards = list(hazards_by_id.get(machine.id, []))
        boxes.append(
            FootprintBox(
                id=machine.id,
                origin=(x, y),
                size=(w, h),
                label=str(raw.get("label") or machine.name)[:120],
                z_index=int(raw.get("z_index", index) or index),
                color_token=token,  # type: ignore[arg-type]
                hazards=hazards,  # type: ignore[arg-type]
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
    sim: SimulationResult | list[SimulationResult] | None = None,
    provider: MetaImageProvider | FireworksImageProvider | None = None,
    providers: list[MetaImageProvider | FireworksImageProvider] | None = None,
) -> SpatialResult:
    """Run the full spatial stage: sanitize -> (Muse Image -> Fireworks) -> spec + PNG.

    ``sim`` is the simulation result(s). Accepts either a single result (usually
    the post-correction ``best`` sim) or a list (typically ``[baseline, best]``).
    When a list is given, hazard tags are unioned per machine so the canvas shows
    every machine that had a thermal/vibration problem at any point in the run -
    including conflicts the corrector later resolved. When ``sim`` is ``None``
    the stripes are simply omitted.

    ``providers`` is the ordered fallback chain (Meta Image first, Fireworks
    Image second); the first provider that returns a layout that passes the
    local geometry validator wins. ``provider`` is kept as a single-provider
    convenience for callers that inject a custom/stub provider (tests).

    If every provider errors out or returns an unusable reply, the deterministic
    local rasteriser is used as the last stop (existing FALLBACK behaviour).
    """
    if not equipment:
        return _result_from_spec(_spec_from_layout([], ["empty layout"], "FALLBACK"))

    sanitized, notes = sanitize_layout(equipment)
    # Re-derive hazards against the sanitized (post-clamp) geometry so the
    # machine ids resolve against the same equipment the frontend receives.
    hazards_by_id = _derive_hazards(sanitized, sim)

    chain: list[MetaImageProvider | FireworksImageProvider] = []
    if providers is not None:
        chain = list(providers)
    elif provider is not None:
        chain = [provider]

    for candidate_provider in chain:
        try:
            payload = await candidate_provider.complete(
                _build_messages(sanitized, notes, hazards_by_id),
                schema=SPATIAL_SCHEMA,
                # 4096 accommodates reasoning models (Muse Spark needs ~640
                # reasoning tokens before it emits the footprints JSON). The
                # chat-instruct Muse Image reply finishes far below this cap.
                max_tokens=4096,
                temperature=0.0,
            )
        except ProviderError as exc:
            logger.warning(
                "muse_image_unavailable provider=%s code=%s",
                candidate_provider.name,
                exc.code,
            )
            continue
        else:
            spec = _parse_muse_reply(payload, sanitized, hazards_by_id)
            if spec is not None:
                return _result_from_spec(spec)
            logger.warning(
                "muse_image_reply_unusable provider=%s falling back",
                candidate_provider.name,
            )

    spec = _spec_from_layout(
        sanitized, notes, source="FALLBACK", hazards_by_id=hazards_by_id
    )
    return _result_from_spec(spec)


