"""Composition layer: run physics and produce a `SimulationResult`.

Wraps the FDM solver + vibration analyzer into one deterministic call used by
the corrector loop and by the final API response builder.
"""

from __future__ import annotations

from agents.schemas import EquipmentItem, Scene, SimulationResult

from core.heat_solver import build_result, simulate_heat
from core.vibration import detect_vibration

__all__ = ["simulate_scene"]


def simulate_scene(
    scene: Scene | list[EquipmentItem],
) -> SimulationResult:
    """Run the full physics pipeline on a scene or bare equipment list."""
    equipment = scene.equipment if isinstance(scene, Scene) else list(scene)
    field = simulate_heat(equipment)
    vib = detect_vibration(equipment)
    return build_result(field, equipment, extra_hazards=vib)
