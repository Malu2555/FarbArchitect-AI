"""Measure what the deterministic (LLM-free) corrector can do on its own.

For each seed: baseline peak/hazards, then the result of ``local_relax`` in
both modes. This is the floor the LLM has to beat — if the LLM cannot beat it,
``run_correction`` should fall back to it.

Usage::

    python scripts/probe_local_relax.py [seed ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from agents.corrector import TEMP_LIMIT_C, _layout_score, local_relax  # noqa: E402
from agents.schemas import Scene  # noqa: E402
from core.simulation import simulate_scene  # noqa: E402
from core.synthetic_gen import generate_scene  # noqa: E402


def main() -> int:
    seeds = [int(a) for a in sys.argv[1:] if a.isdigit()] or [42]
    print(f"temp limit {TEMP_LIMIT_C} C")
    for seed in seeds:
        scene = generate_scene(seed)
        base = simulate_scene(scene)
        baseline_peak = base.peak_c
        print(f"\nseed {seed}: baseline peak {base.peak_c:.2f} C, "
              f"hazards {len(base.hazards)}, score "
              f"{_layout_score(base, baseline_peak):.2f}")

        for label, greedy in (("greedy", True), ("simple", False)):
            out = local_relax(list(scene.equipment), greedy=greedy)
            sim = simulate_scene(Scene(seed=seed, equipment=out))
            moved = sum(
                1 for a, b in zip(scene.equipment, out) if (a.x, a.y) != (b.x, b.y)
            )
            print(
                f"  local_relax({label:<6}): peak {sim.peak_c:>6.2f} C "
                f"({sim.peak_c - base.peak_c:+.2f}), "
                f"hazards {len(sim.hazards)}, moved {moved}, "
                f"score {_layout_score(sim, baseline_peak):.2f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
