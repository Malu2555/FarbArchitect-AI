"""Vibration conflict detection tests."""

from __future__ import annotations

from agents.schemas import EquipmentItem
from core.vibration import DB_FLOOR, detect_vibration


def _item(**overrides: object) -> EquipmentItem:
    base = dict(
        id="A-00",
        name="Machine",
        x=40,
        y=40,
        w=4,
        h=4,
        heat_watts=100.0,
        vibration_db=90.0,
    )
    base.update(overrides)
    return EquipmentItem(**base)  # type: ignore[arg-type]


def test_detects_close_loud_pair() -> None:
    a = _item(id="A", x=45, y=45)
    b = _item(id="B", x=50, y=45)  # gap 1 cell
    hazards = detect_vibration([a, b])

    assert len(hazards) == 1
    h = hazards[0]
    assert h.type == "vibration"
    assert {h.a_id, h.b_id} == {"A", "B"}
    assert h.severity in {"low", "medium", "high"}


def test_ignores_far_pair() -> None:
    a = _item(id="A", x=5, y=5)
    b = _item(id="B", x=80, y=80)
    assert detect_vibration([a, b]) == []


def test_ignores_quiet_pair() -> None:
    a = _item(id="A", x=45, y=45, vibration_db=DB_FLOOR - 30.0)
    b = _item(id="B", x=50, y=45, vibration_db=DB_FLOOR - 25.0)
    assert detect_vibration([a, b]) == []


def test_severity_scales_with_overlap() -> None:
    close = detect_vibration(
        [_item(id="A", x=45, y=45), _item(id="B", x=47, y=45)]
    )
    far = detect_vibration(
        [_item(id="A", x=45, y=45), _item(id="B", x=52, y=45)]
    )
    assert len(close) == 1 and len(far) == 1
    rank = {"low": 0, "medium": 1, "high": 2}
    assert rank[close[0].severity] >= rank[far[0].severity]
