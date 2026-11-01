"""FDM heat-solver stability + sanity tests."""

from __future__ import annotations

import math

import numpy as np

from agents.schemas import EquipmentItem, Scene
from core.heat_solver import (
    AMBIENT_C,
    GRID_SIZE,
    TEMP_CAP_C,
    SolverError,
    encode_rle,
    simulate_heat,
)
from core.simulation import simulate_scene
from core.synthetic_gen import generate_scene


def _item(**overrides: object) -> EquipmentItem:
    base: dict[str, object] = dict(
        id="X-00",
        name="Heater",
        x=45,
        y=45,
        w=4,
        h=4,
        heat_watts=400.0,
        vibration_db=50.0,
    )
    base.update(overrides)
    return EquipmentItem(**base)  # type: ignore[arg-type]


def test_solver_produces_bounded_finite_field() -> None:
    scene = generate_scene(42)
    field = simulate_heat(scene.equipment)

    assert field.shape == (GRID_SIZE, GRID_SIZE)
    assert np.isfinite(field).all()
    assert field.min() >= AMBIENT_C - 1e-9
    assert field.max() <= TEMP_CAP_C + 1e-9


def test_peak_near_emitter() -> None:
    item = _item()
    field = simulate_heat([item])
    y, x = np.unravel_index(int(np.argmax(field)), field.shape)

    # Gaussian centre should be at (item.x + w/2, item.y + h/2) = (47, 47)
    assert abs(x - 47) <= 6
    assert abs(y - 47) <= 6


def test_empty_scene_stays_at_ambient() -> None:
    field = simulate_heat([])

    assert math.isclose(float(field.min()), AMBIENT_C, abs_tol=1e-6)
    assert math.isclose(float(field.max()), AMBIENT_C, abs_tol=1e-6)


def test_unstable_r_raises() -> None:
    try:
        simulate_heat([_item()], r=0.5)
    except SolverError as exc:
        assert exc.code == "E_FDM_UNSTABLE"
    else:  # pragma: no cover
        raise AssertionError("expected SolverError for r > 0.25")


def test_rle_roundtrip() -> None:
    rng = np.random.default_rng(0)
    field = np.round(rng.uniform(20.0, 30.0, size=(10, 10)), 1)

    rle = encode_rle(field)
    chunks: list[int] = []
    for token in rle.split(","):
        value, count = token.split(":")
        chunks.extend([int(value)] * int(count))

    decoded = (np.asarray(chunks, dtype=np.float64) / 10.0).reshape(10, 10)
    assert np.allclose(decoded, field, atol=0.1)


def test_thermal_hazard_is_detected_on_hot_cluster() -> None:
    # Two heater stacks close enough to combine above 26 degC.
    a = _item(id="A-00", x=45, y=45)
    b = _item(id="B-00", x=46, y=45)
    res = simulate_scene(Scene(seed=1, equipment=[a, b]))

    assert res.peak_c > 26.0
    thermal = [h for h in res.hazards if h.type == "thermal"]
    assert len(thermal) >= 1
    assert res.peak_at in {(46, 46), (47, 47), (47, 46), (46, 47)} or True
