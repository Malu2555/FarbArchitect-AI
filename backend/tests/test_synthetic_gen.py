"""Determinism + geometry constraints for the synthetic scene generator."""

from __future__ import annotations

from core.synthetic_gen import GRID_SIZE, generate_scene


def test_seed_is_deterministic() -> None:
    a = generate_scene(42)
    b = generate_scene(42)

    assert [e.model_dump() for e in a.equipment] == [e.model_dump() for e in b.equipment]
    assert a.seed == b.seed == 42


def test_different_seeds_diverge() -> None:
    a = generate_scene(1)
    b = generate_scene(2)

    assert [e.model_dump() for e in a.equipment] != [
        e.model_dump() for e in b.equipment
    ]


def test_no_overlapping_footprints() -> None:
    scene = generate_scene(7)

    assert scene.grid_size == GRID_SIZE
    assert len(scene.equipment) == 8  # full catalog populated

    for i, a in enumerate(scene.equipment):
        for b in scene.equipment[i + 1 :]:
            ax2 = a.x + a.w
            bx2 = b.x + b.w
            ay2 = a.y + a.h
            by2 = b.y + b.h
            separated = ax2 <= b.x or bx2 <= a.x or ay2 <= b.y or by2 <= a.y
            assert separated, f"overlap: {a.id} <-> {b.id}"


def test_all_items_inside_canvas() -> None:
    scene = generate_scene(99)

    for item in scene.equipment:
        assert 0 <= item.x <= GRID_SIZE - item.w
        assert 0 <= item.y <= GRID_SIZE - item.h


def test_catalog_energy_spread() -> None:
    scene = generate_scene(3)

    watts = [e.heat_watts for e in scene.equipment]
    db = [e.vibration_db for e in scene.equipment]
    assert max(watts) - min(watts) > 150, "heat watts must spread across scenes"
    assert max(db) - min(db) > 30, "vibration db must spread across scenes"
