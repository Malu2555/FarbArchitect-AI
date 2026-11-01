"""Multi-turn agentic self-correction loop (bounded to 3 turns).

Per turn
--------
1. Simulate the current layout with the FDM + vibration pipeline.
2. Classify errors (``E_TEMP_PEAK`` / ``E_VIBRATION_CONFLICT``).
3. If the layout is clean, stop early — **no LLM call**.
4. Otherwise ask ``ModelRouter`` for a bounded JSON adjustment plan.
5. **Trust-but-verify**: the model only *proposes* coordinates. Every plan is
   re-simulated and kept only if it beats the current layout on the composite
   score; otherwise the deterministic ``local_relax`` takes over. This is the
   load-bearing rule — an LLM that cannot run the solver will happily cluster
   hot racks "in the interior", where overlapping Gaussians raise the peak.
6. If every provider fails, run ``local_relax`` (deterministic physics-only
   fallback) so the loop always makes forward progress.

The loop preserves the *best* layout seen (lowest composite score) and ships
that as the ``after`` result, guaranteeing never to regress.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from agents.schemas import (
    CorrectionTurn,
    EquipmentItem,
    GRID_SIZE,
    Scene,
    SimulationResult,
    SpatialResult,
    TEMP_LIMIT_C,
)
from agents.spatial_renderer import build_fallback_result, render_spatial
from core.providers import (
    AllProvidersFailed,
    MetaImageProvider,
    ModelRouter,
    build_image_provider,
)
from core.simulation import simulate_scene

logger = logging.getLogger(__name__)

__all__ = [
    "LAYOUT_DELTA_SCHEMA",
    "MAX_TURNS",
    "SYSTEM_PROMPT",
    "CorrectionOutcome",
    "classify_errors",
    "local_relax",
    "run_correction",
]

MAX_TURNS = 3

SYSTEM_PROMPT = (
    "You are fabArchitect AI, an expert semiconductor cleanroom layout agent. "
    "You ALWAYS reply with ONE JSON object and nothing else. "
    'Schema: {"adjustments": [{"id": str, "x": int, "y": int}], "reasoning": str}. '
    "Coordinates are grid cells in [0, 99]; a machine keeps its footprint (w, h) "
    "and (x, y) is the top-left corner. "
    "PHYSICS you must respect: "
    "(1) The thermal solver uses ZERO-FLUX (reflective) boundaries, so heat "
    "concentrates at walls and corners. Never place a high-wattage machine "
    "within 8 cells of any wall - keep hot equipment in the interior. "
    "(2) Each machine emits a Gaussian heat field; overlapping fields ADD. "
    "Spreading emitters apart lowers the peak far more than clustering them. "
    "(3) A vibration conflict exists when two machines are closer than "
    "(max(w_a,w_b) + max(w_b,h_b))/2 + 4 cells apart and either is >= 70 dB. "
    "Separate such pairs along their strongest axis first. "
    "(4) Machines must not overlap and must stay fully inside [0, 100). "
    "Plan: fix vibration pairs first by separating them, then spread high-wattage "
    "emitters toward open interior space while keeping >= 8 cells of clearance "
    "from every wall. Aim to satisfy: peak temperature <= 26 C and no vibration "
    "conflicts. Report your reasoning in one or two sentences."
)

LAYOUT_DELTA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["adjustments"],
    "properties": {
        "adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "x", "y"],
                "properties": {
                    "id": {"type": "string"},
                    "x": {"type": "integer", "minimum": 0, "maximum": 99},
                    "y": {"type": "integer", "minimum": 0, "maximum": 99},
                },
            },
        },
        "reasoning": {"type": "string"},
    },
}


@dataclass
class CorrectionOutcome:
    after: SimulationResult
    corrections: list[CorrectionTurn] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    final_equipment: list[EquipmentItem] = field(default_factory=list)
    # Stage 2 — spatial renderer (Muse Image) output.
    spatial_result: SpatialResult | None = None
    spatial_source: str = "FALLBACK"


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
def classify_errors(sim: SimulationResult) -> list[str]:
    """Return the set of error codes that a self-correcting agent should fix."""
    codes: list[str] = []
    if sim.peak_c > TEMP_LIMIT_C:
        codes.append("E_TEMP_PEAK")
    if any(h.type == "vibration" for h in sim.hazards):
        codes.append("E_VIBRATION_CONFLICT")
    return codes


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _reposition(item: EquipmentItem, new_x: int, new_y: int) -> EquipmentItem:
    clamped_x = max(0, min(int(new_x), GRID_SIZE - item.w))
    clamped_y = max(0, min(int(new_y), GRID_SIZE - item.h))
    return item.model_copy(update={"x": clamped_x, "y": clamped_y})


def _clamp_layout(
    equipment: list[EquipmentItem], adjustments: list[dict[str, Any]]
) -> list[EquipmentItem]:
    """Apply LLM-supplied deltas, dropping unknown ids and clamping bounds."""
    by_id = {item.id: item for item in equipment}
    changed = False
    for adj in adjustments or []:
        if not isinstance(adj, dict):
            continue
        target = by_id.get(str(adj.get("id", "")))
        if target is None:
            continue
        try:
            nx = int(adj.get("x", target.x))
            ny = int(adj.get("y", target.y))
        except (TypeError, ValueError):
            continue
        by_id[target.id] = _reposition(target, nx, ny)
        changed = True
    if not changed:
        return []
    return [by_id[item.id] for item in equipment]


# ---------------------------------------------------------------------------
# Deterministic physics-only fallback (used when every provider fails)
# ---------------------------------------------------------------------------
def _simple_relax(equipment: list[EquipmentItem]) -> list[EquipmentItem]:
    """One-shot directional nudge used on turns >= 2 (cheap)."""
    sim = simulate_scene(Scene(seed=0, equipment=equipment))
    working = [item.model_copy() for item in equipment]
    index = {item.id: i for i, item in enumerate(working)}

    if sim.peak_c > TEMP_LIMIT_C and working:
        hottest = max(working, key=lambda e: e.heat_watts)
        px, py = sim.peak_at
        cx = hottest.x + hottest.w / 2.0
        cy = hottest.y + hottest.h / 2.0
        dx = -6 if cx >= px else 6
        dy = -6 if cy >= py else 6
        working[index[hottest.id]] = _reposition(
            hottest, hottest.x + dx, hottest.y + dy
        )

    for hazard in sim.hazards:
        if hazard.type != "vibration" or not hazard.b_id:
            continue
        a = next((e for e in working if e.id == hazard.a_id), None)
        b = next((e for e in working if e.id == hazard.b_id), None)
        if a is None or b is None:
            continue
        mover, other = (a, b) if a.vibration_db <= b.vibration_db else (b, a)
        ax = mover.x + mover.w / 2.0
        ay = mover.y + mover.h / 2.0
        bx = other.x + other.w / 2.0
        by = other.y + other.h / 2.0
        dx = 6 if ax >= bx else -6
        dy = 6 if ay >= by else -6
        working[index[mover.id]] = _reposition(mover, mover.x + dx, mover.y + dy)

    return working


def _generate_moves(
    equipment: list[EquipmentItem], base_sim: SimulationResult
) -> list[list[EquipmentItem]]:
    """Produce a handful of candidate layouts for greedy evaluation."""
    candidates: list[list[EquipmentItem]] = []
    deltas = ((-8, 0), (8, 0), (0, -8), (0, 8), (-6, -6), (6, 6), (6, -6), (-6, 6))

    hottest = sorted(equipment, key=lambda e: e.heat_watts, reverse=True)[:2]
    for hot in hottest:
        for dx, dy in deltas:
            candidates.append(
                [
                    _reposition(e, e.x + dx, e.y + dy) if e.id == hot.id else e
                    for e in equipment
                ]
            )

    for hazard in base_sim.hazards:
        if hazard.type != "vibration" or not hazard.b_id:
            continue
        a = next((e for e in equipment if e.id == hazard.a_id), None)
        b = next((e for e in equipment if e.id == hazard.b_id), None)
        if a is None or b is None:
            continue
        mover, other = (a, b) if a.vibration_db <= b.vibration_db else (b, a)
        ax = mover.x + mover.w / 2.0
        ay = mover.y + mover.h / 2.0
        bx = other.x + other.w / 2.0
        by = other.y + other.h / 2.0
        dx = 8 if ax >= bx else -8
        dy = 8 if ay >= by else -8
        candidates.append(
            [
                _reposition(e, e.x + dx, e.y + dy) if e.id == mover.id else e
                for e in equipment
            ]
        )
    return candidates


def local_relax(
    equipment: list[EquipmentItem], *, greedy: bool = True
) -> list[EquipmentItem]:
    """Deterministic physics-only fallback.

    ``greedy=True`` enumerates a small set of candidate moves and *verifies*
    each against the FDM solver, keeping only real improvements. ``False``
    uses a single cheap nudge (subsequent turns). Bounded cost: at most
    ``2 * 8 + hazard_count`` simulations.
    """
    if not greedy:
        return _simple_relax(equipment)

    base_sim = simulate_scene(Scene(seed=0, equipment=equipment))
    baseline_peak = base_sim.peak_c
    best_equipment = [item.model_copy() for item in equipment]
    best_score = _layout_score(base_sim, baseline_peak)

    for candidate in _generate_moves(equipment, base_sim):
        sim = simulate_scene(Scene(seed=0, equipment=candidate))
        score = _layout_score(sim, baseline_peak)
        if score < best_score:
            best_score = score
            best_equipment = candidate

    return best_equipment


# ---------------------------------------------------------------------------
# Layout scoring
# ---------------------------------------------------------------------------
def _layout_score(sim: SimulationResult, baseline_peak: float) -> float:
    """Composite layout quality score (lower is better).

    A lexicographic ``(peak, hazard_count)`` comparison is wrong: a turn that
    drops vibration conflicts from 6 to 4 while losing 0.03 C on peak would
    then be discarded, so the response would ship the untouched original.

    This score blends two terms:

    * ``2.0`` per remaining hazard - vibration conflicts corrupt lithography,
      so clearing them is worth real thermal cost.
    * ``3.0`` per degree the peak exceeds ``baseline_peak`` - *worsening* the
      peak is penalised harder than hazards are rewarded, so the corrector
      never ships a thermally worse layout just to knock out a conflict.

    A fully clean layout (peak <= limit, zero hazards) scores exactly ``0.0``.
    """
    overage = max(0.0, sim.peak_c - TEMP_LIMIT_C)
    regression = max(0.0, sim.peak_c - baseline_peak)
    return round(overage + len(sim.hazards) * 2.0 + regression * 3.0, 3)





# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _build_messages(
    equipment: list[EquipmentItem],
    sim: SimulationResult,
    error_codes: list[str],
) -> list[dict[str, Any]]:
    payload = {
        "errors": error_codes,
        "temp_limit_c": TEMP_LIMIT_C,
        "reported_peak_c": round(sim.peak_c, 2),
        "reported_peak_at": list(sim.peak_at),
        "hazards": [h.model_dump() for h in sim.hazards],
        "machines": [
            {
                "id": e.id,
                "x": e.x,
                "y": e.y,
                "w": e.w,
                "h": e.h,
                "heat_watts": e.heat_watts,
                "vibration_db": e.vibration_db,
            }
            for e in equipment
        ],
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Please produce a corrected layout as JSON.\n\n"
            + json.dumps(payload, separators=(",", ":")),
        },
    ]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def run_correction(
    scene: Scene,
    router: ModelRouter | None = None,
    image_provider: MetaImageProvider | None = None,
    *,
    use_spatial_stage: bool = True,
) -> CorrectionOutcome:
    """Run the closed-loop self-correction and return the outcome."""
    router = router or ModelRouter()
    working = list(scene.equipment)
    corrections: list[CorrectionTurn] = []
    providers_used: list[str] = []

    best_sim: SimulationResult | None = None
    best_equipment: list[EquipmentItem] = list(working)
    best_score: float | None = None
    baseline_peak = simulate_scene(
        Scene(seed=scene.seed, grid_size=scene.grid_size, equipment=list(scene.equipment))
    ).peak_c

    prev_score: float | None = None

    for turn in range(1, MAX_TURNS + 1):
        current_scene = Scene(
            seed=scene.seed, grid_size=scene.grid_size, equipment=working
        )
        sim = simulate_scene(current_scene)
        codes = classify_errors(sim)

        score = _layout_score(sim, baseline_peak)

        # Stop early when the previous turn's LLM response produced no further
        # measurable progress — each turn costs one live completion, so a
        # stalled loop would only add latency without improving the layout.
        # The very first evaluation (turn 1) always runs.
        if turn > 1 and prev_score is not None and score >= prev_score:
            break
        prev_score = score

        if best_score is None or score < best_score:
            best_score = score
            best_sim = sim
            best_equipment = [item.model_copy() for item in working]

        corrections.append(
            CorrectionTurn(
                turn=turn,
                phase="evaluate",
                error_codes=codes,
                summary=(
                    f"turn {turn}: peak {sim.peak_c:.2f} C, "
                    f"{len(sim.hazards)} hazard(s)"
                ),
                peak_c=float(sim.peak_c),
            )
        )

        if not codes:
            break

        try:
            provider_name, payload = await router.complete(
                _build_messages(working, sim, codes),
                schema=LAYOUT_DELTA_SCHEMA,
                temperature=0.0,
            )
        except AllProvidersFailed as exc:
            logger.warning("corrector_llm_unavailable: %s", exc)
            candidate = local_relax(working, greedy=True)
            corrections.append(
                CorrectionTurn(
                    turn=turn,
                    phase="error",
                    error_codes=codes + ["E_PROVIDER"],
                    summary="llm unavailable — deterministic physics-only relaxation",
                )
            )
        else:
            if provider_name not in providers_used:
                providers_used.append(provider_name)
            raw_adjust = payload.get("adjustments") if isinstance(payload, dict) else None
            candidate = _clamp_layout(working, list(raw_adjust or []))
            reasoning = ""
            if isinstance(payload, dict):
                reasoning = str(payload.get("reasoning") or "").strip()

            if not candidate:
                candidate = local_relax(working, greedy=True)
                corrections.append(
                    CorrectionTurn(
                        turn=turn,
                        phase="error",
                        error_codes=codes + ["E_EMPTY_PLAN"],
                        summary=(reasoning[:280] or "model returned no usable deltas"),
                    )
                )
            else:
                # ---------------------------------------------------------
                # Trust-but-verify gate.
                #
                # The model is a *proposer*, not an authority: it cannot run
                # the FDM solver, so a plausible-sounding plan ("move every
                # hot rack into the interior") can still RAISE the peak,
                # because overlapping Gaussian heat fields add and the
                # interior is precisely where they overlap. Verify the
                # proposal against the same composite score the loop uses
                # and keep it only if it beats the layout it targets.
                # ---------------------------------------------------------
                proposal_sim = simulate_scene(
                    Scene(
                        seed=scene.seed,
                        grid_size=scene.grid_size,
                        equipment=candidate,
                    )
                )
                if _layout_score(proposal_sim, baseline_peak) < score:
                    corrections.append(
                        CorrectionTurn(
                            turn=turn,
                            phase="correct",
                            error_codes=codes,
                            summary=(
                                reasoning[:280]
                                or f"{provider_name}: applied "
                                f"{len(raw_adjust or [])} move(s)"
                            ),
                        )
                    )
                else:
                    candidate = local_relax(working, greedy=True)
                    corrections.append(
                        CorrectionTurn(
                            turn=turn,
                            phase="verify",
                            error_codes=codes + ["E_REJECTED_PLAN"],
                            summary=(
                                f"rejected model plan (peak "
                                f"{proposal_sim.peak_c:.2f} C, "
                                f"{len(proposal_sim.hazards)} hazard(s) does not "
                                f"beat {sim.peak_c:.2f} C, "
                                f"{len(sim.hazards)} hazard(s)); used "
                                f"deterministic physics relaxation instead"
                            ),
                        )
                    )

        if [item.model_dump() for item in candidate] == [
            item.model_dump() for item in working
        ]:
            break  # no measurable progress — leave the loop early
        working = candidate

    if best_sim is None:
        best_sim = simulate_scene(Scene(seed=scene.seed, equipment=working))
        best_equipment = list(working)

    # ------------------------------------------------------------------
    # Stage 2 — spatial renderer (Muse Image)
    # ------------------------------------------------------------------
    spatial_result: SpatialResult | None = None
    spatial_source = "FALLBACK"
    if use_spatial_stage:
        try:
            provider = image_provider
            if provider is None:
                provider = build_image_provider()
            spatial_result = await render_spatial(best_equipment, provider=provider)
            spatial_source = spatial_result.spatial_spec.canvas.source
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("spatial_stage_failed: %s", exc)
            spatial_result = build_fallback_result(best_equipment)
            spatial_source = "FALLBACK"

    return CorrectionOutcome(
        after=best_sim,
        corrections=corrections,
        providers_used=providers_used,
        final_equipment=best_equipment,
        spatial_result=spatial_result,
        spatial_source=spatial_source,
    )