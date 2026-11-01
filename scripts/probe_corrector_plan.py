"""Show exactly what the corrector model proposes for a real seed.

Builds the true ``_build_messages`` payload for the requested seed, calls the
configured Fireworks model once, then prints:

* the parsed ``adjustments`` array as the model returned it,
* which machines would actually move after ``_clamp_layout``,
* and the resulting peak / hazard delta if that plan were applied.

This is the fastest way to tell "the model is silent" apart from
"the model talks but proposes no-op coordinates".

Usage::

    python scripts/probe_corrector_plan.py [seed]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from agents.corrector import (  # noqa: E402
    LAYOUT_DELTA_SCHEMA,
    _build_messages,
    _clamp_layout,
    classify_errors,
)
from agents.schemas import Scene  # noqa: E402
from core.providers import ModelRouter  # noqa: E402
from core.simulation import simulate_scene  # noqa: E402
from core.synthetic_gen import generate_scene  # noqa: E402


async def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42

    scene = generate_scene(seed)
    sim = simulate_scene(scene)
    codes = classify_errors(sim)

    print(f"seed={seed}  peak={sim.peak_c:.2f} C  hazards={len(sim.hazards)}")
    print(f"errors={codes}  peak_at={sim.peak_at}")
    print("\nmachines (x, y, heat_w, db):")
    for e in scene.equipment:
        print(f"  {e.id:<22} ({e.x:>2},{e.y:>2})  {e.heat_watts:>6.1f}W  {e.vibration_db:>5.1f}dB")

    router = ModelRouter()
    provider_name, payload = await router.complete(
        _build_messages(list(scene.equipment), sim, codes),
        schema=LAYOUT_DELTA_SCHEMA,
        temperature=0.0,
    )
    print(f"\nprovider={provider_name}")

    adjustments = payload.get("adjustments") if isinstance(payload, dict) else None
    print(f"adjustments returned: {len(adjustments or [])}")
    print(json.dumps(adjustments, indent=2)[:2000])
    print(f"\nreasoning: {str(payload.get('reasoning') or '')[:600]}")

    candidate = _clamp_layout(list(scene.equipment), list(adjustments or []))
    if not candidate:
        print("\nVERDICT: no usable deltas -> corrector would call local_relax()")
        return 0

    before = {e.id: (e.x, e.y) for e in scene.equipment}
    moved = [
        f"{e.id} ({before[e.id][0]},{before[e.id][1]}) -> ({e.x},{e.y})"
        for e in candidate
        if before[e.id] != (e.x, e.y)
    ]
    print(f"\nwould move {len(moved)} machine(s):")
    for row in moved:
        print(f"  {row}")

    if not moved:
        print("\nVERDICT: adjustments are no-ops (coordinates unchanged)")
        return 0

    after = simulate_scene(Scene(seed=scene.seed, grid_size=scene.grid_size, equipment=candidate))
    print(
        f"\nplan outcome: peak {sim.peak_c:.2f} -> {after.peak_c:.2f} C "
        f"({after.peak_c - sim.peak_c:+.2f}), hazards {len(sim.hazards)} -> {len(after.hazards)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
