"""Tests for the multi-turn corrector (Stage 5)."""

from __future__ import annotations

import pytest

from agents.corrector import (
    MAX_TURNS,
    CorrectionOutcome,
    classify_errors,
    local_relax,
    run_correction,
)
from agents.schemas import EquipmentItem, Scene, TEMP_LIMIT_C
from core.providers import AllProvidersFailed
from core.simulation import simulate_scene


def _item(**overrides: object) -> EquipmentItem:
    base = dict(
        id="A-00",
        name="Machine",
        x=45,
        y=45,
        w=4,
        h=4,
        heat_watts=400.0,
        vibration_db=95.0,
    )
    base.update(overrides)
    return EquipmentItem(**base)  # type: ignore[arg-type]


class _StubRouter:
    def __init__(self, *, payload=None, fail: bool = False):
        self.payload = payload or {"adjustments": [], "reasoning": "stub"}
        self.fail = fail
        self.calls = 0

    async def complete(self, messages, schema=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise AllProvidersFailed(["stub hard failure"])
        return "stub", self.payload


def test_classify_errors_clean() -> None:
    calm = _item(id="CALM", x=10, y=10, w=3, h=3, heat_watts=80, vibration_db=40)
    sim = simulate_scene(Scene(seed=1, equipment=[calm]))
    assert classify_errors(sim) == []


def test_classify_errors_thermal_only() -> None:
    a = _item(id="A", x=45, y=45, vibration_db=40)
    b = _item(id="B", x=50, y=45, vibration_db=40)  # quiet but hot
    sim = simulate_scene(Scene(seed=1, equipment=[a, b]))
    codes = classify_errors(sim)
    assert "E_TEMP_PEAK" in codes
    assert "E_VIBRATION_CONFLICT" not in codes


def test_classify_errors_both() -> None:
    a = _item(id="A", x=45, y=45)
    b = _item(id="B", x=47, y=45)
    codes = classify_errors(simulate_scene(Scene(seed=1, equipment=[a, b])))
    assert "E_TEMP_PEAK" in codes
    assert "E_VIBRATION_CONFLICT" in codes


def test_local_relax_keeps_items_in_bounds() -> None:
    hot = _item(id="A", x=95, y=95, w=4, h=4)
    result = local_relax([hot])

    assert result[0].x + result[0].w <= 100
    assert result[0].y + result[0].h <= 100


@pytest.mark.asyncio
async def test_clean_scene_skips_llm_entirely() -> None:
    calm = _item(id="CALM", x=10, y=10, w=3, h=3, heat_watts=80, vibration_db=40)
    scene = Scene(seed=3, equipment=[calm])
    stub = _StubRouter()

    outcome = await run_correction(scene, router=stub)  # type: ignore[arg-type]

    assert stub.calls == 0
    assert outcome.providers_used == []
    asserts = [c for c in outcome.corrections if c.phase == "evaluate"]
    assert len(asserts) == 1
    assert asserts[0].error_codes == []


@pytest.mark.asyncio
async def test_model_plan_improves_or_matches_baseline() -> None:
    a = _item(id="A", x=45, y=45)
    b = _item(id="B", x=48, y=45)
    scene = Scene(seed=1, equipment=[a, b])
    before = simulate_scene(scene)
    assert before.peak_c > TEMP_LIMIT_C

    stub = _StubRouter(
        payload={
            "adjustments": [
                {"id": "A", "x": 10, "y": 10},
                {"id": "B", "x": 85, "y": 85},
            ],
            "reasoning": "separated emitters",
        }
    )
    outcome = await run_correction(scene, router=stub)  # type: ignore[arg-type]

    assert isinstance(outcome, CorrectionOutcome)
    assert outcome.providers_used == ["stub"]
    # The corrected layout must be strictly cooler than the conflicting baseline
    assert outcome.after.peak_c < before.peak_c
    assert len(outcome.final_equipment) == 2



@pytest.mark.asyncio
async def test_correction_falls_back_when_all_providers_fail() -> None:
    a = _item(id="A", x=45, y=45)
    b = _item(id="B", x=48, y=45)
    scene = Scene(seed=2, equipment=[a, b])
    before = simulate_scene(scene)
    stub = _StubRouter(fail=True)

    outcome = await run_correction(scene, router=stub)  # type: ignore[arg-type]

    assert stub.calls >= 1
    assert any("E_PROVIDER" in c.error_codes for c in outcome.corrections)
    evals = [c for c in outcome.corrections if c.phase == "evaluate"]
    assert len(evals) <= MAX_TURNS
    assert outcome.after.peak_c <= before.peak_c


@pytest.mark.asyncio
async def test_helpful_model_plan_is_logged_as_correct() -> None:
    """A plan that genuinely wins on the composite score is applied."""
    a = _item(id="A", x=45, y=45)
    b = _item(id="B", x=48, y=45)
    scene = Scene(seed=1, equipment=[a, b])
    before = simulate_scene(scene)

    stub = _StubRouter(
        payload={
            "adjustments": [
                {"id": "A", "x": 10, "y": 10},
                {"id": "B", "x": 85, "y": 85},
            ],
            "reasoning": "separated the conflicting pair",
        }
    )
    outcome = await run_correction(scene, router=stub)  # type: ignore[arg-type]

    # The very first plan is genuinely better, so it must be applied - not
    # replaced by the deterministic fallback. Later turns may still be
    # rejected; that is the gate doing its job, not a failure.
    first_action = next(c for c in outcome.corrections if c.phase != "evaluate")
    assert first_action.phase == "correct"
    assert "E_REJECTED_PLAN" not in first_action.error_codes
    assert outcome.after.peak_c < before.peak_c


@pytest.mark.asyncio
async def test_harmful_model_plan_is_rejected_and_flagged() -> None:
    """A plan that re-simulates worse is refused instead of shipped.

    This is the trust-but-verify gate: the model cannot run the FDM solver, so
    clustering hot machines "in the interior" raises the peak because their
    Gaussian fields overlap and add.
    """
    items = [
        _item(id="A", x=8, y=8, w=6, h=6),
        _item(id="B", x=84, y=84, w=6, h=6),
    ]
    scene = Scene(seed=11, equipment=items)
    before = simulate_scene(scene)

    stub = _StubRouter(
        payload={
            "adjustments": [
                {"id": "A", "x": 46, "y": 46},
                {"id": "B", "x": 50, "y": 46},
            ],
            "reasoning": "piled both hot machines into the interior",
        }
    )
    outcome = await run_correction(scene, router=stub)  # type: ignore[arg-type]

    rejected = [c for c in outcome.corrections if "E_REJECTED_PLAN" in c.error_codes]
    assert rejected, "expected the harmful plan to be rejected"
    assert rejected[0].phase == "verify"
    # The headline guarantee: never ship a hotter layout than we started with.
    assert outcome.after.peak_c <= before.peak_c


@pytest.mark.asyncio
async def test_bounded_to_max_turns() -> None:
    # A dense, hot cluster that physics cannot fully resolve within a few moves.
    items = [
        _item(id=f"M{i}", x=40 + 3 * i, y=40, w=4, h=4)
        for i in range(4)
    ]
    stub = _StubRouter(fail=True)

    outcome = await run_correction(
        Scene(seed=4, equipment=items), router=stub  # type: ignore[arg-type]
    )

    evals = [c for c in outcome.corrections if c.phase == "evaluate"]
    assert len(evals) <= MAX_TURNS
    assert all(1 <= c.turn <= MAX_TURNS for c in outcome.corrections)
