"""Tests for auto-placement of parsed equipment drafts."""

from __future__ import annotations

import pytest

from agents.schemas import EquipmentDraft, EquipmentUpload, RoomDraft
from core.placement import DEFAULT_GRID_SIZE, auto_place


def _draft(name: str, *, w=3, h=3, watts=100.0, db=60.0, row=1) -> EquipmentDraft:
    return EquipmentDraft(
        id=f"{name.upper()}-{row:03d}",
        name=name,
        machine_type="METROLOGY",
        w=w,
        h=h,
        heat_watts=watts,
        vibration_db=db,
        source_row=row,
    )


def _upload(drafts, room: RoomDraft | None = None) -> EquipmentUpload:
    return EquipmentUpload(
        source_filename="fab.csv",
        source_kind="csv",
        room=room or RoomDraft(),
        drafts=drafts,
    )


def _overlaps(a, b) -> bool:
    return not (
        a.x + a.w <= b.x
        or b.x + b.w <= a.x
        or a.y + a.h <= b.y
        or b.y + b.h <= a.y
    )


def _assert_no_overlaps(items) -> None:
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            assert not _overlaps(a, b), f"{a.id} overlaps {b.id}"


# ---------------------------------------------------------------------------
# grid strategy
# ---------------------------------------------------------------------------
def test_grid_places_all_and_no_overlap() -> None:
    drafts = [_draft(f"M{i}", row=i) for i in range(1, 10)]
    result = auto_place(_upload(drafts), strategy="grid")

    assert len(result.equipment) == 9
    assert result.strategy == "grid"
    _assert_no_overlaps(result.equipment)


def test_grid_is_deterministic() -> None:
    drafts = [_draft(f"M{i}", row=i) for i in range(1, 6)]
    a = auto_place(_upload(drafts), strategy="grid")
    b = auto_place(_upload(drafts), strategy="grid")
    assert [e.model_dump() for e in a.equipment] == [
        e.model_dump() for e in b.equipment
    ]


def test_empty_upload_places_nothing() -> None:
    result = auto_place(_upload([]), strategy="grid")
    assert result.equipment == []
    assert result.grid_size == DEFAULT_GRID_SIZE


# ---------------------------------------------------------------------------
# grid sizing
# ---------------------------------------------------------------------------
def test_grid_size_grows_with_large_room() -> None:
    drafts = [_draft("A")]
    result = auto_place(
        _upload(drafts, RoomDraft(width_m=120.0, depth_m=60.0)), strategy="grid"
    )
    # 120 m / 0.5 = 240 cells.
    assert result.grid_size == 240


def test_grid_size_never_below_default() -> None:
    drafts = [_draft("A")]
    result = auto_place(
        _upload(drafts, RoomDraft(width_m=5.0, depth_m=4.0)), strategy="grid"
    )
    assert result.grid_size == DEFAULT_GRID_SIZE


# ---------------------------------------------------------------------------
# load_based strategy
# ---------------------------------------------------------------------------
def test_load_based_places_first_draft_at_origin_ish() -> None:
    """Hottest machine is placed first (top-left shelf)."""
    cool = _draft("Cool", watts=50.0, row=1)
    hot = _draft("Hot", watts=4000.0, row=2)
    result = auto_place(_upload([cool, hot]), strategy="load_based")

    assert result.strategy == "load_based"
    hot_item = next(e for e in result.equipment if "HOT" in e.id)
    cool_item = next(e for e in result.equipment if "COOL" in e.id)
    # Hot placed before cool -> smaller x + y (earlier in the shelf order).
    assert (hot_item.x, hot_item.y) <= (cool_item.x, cool_item.y)
    _assert_no_overlaps(result.equipment)


# ---------------------------------------------------------------------------
# random strategy
# ---------------------------------------------------------------------------
def test_random_is_reproducible_for_same_upload() -> None:
    drafts = [_draft(f"M{i}", row=i) for i in range(1, 8)]
    a = auto_place(_upload(drafts), strategy="random")
    b = auto_place(_upload(drafts), strategy="random")
    assert [e.model_dump() for e in a.equipment] == [
        e.model_dump() for e in b.equipment
    ]
    _assert_no_overlaps(a.equipment)


def test_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        auto_place(_upload([_draft("A")]), strategy="spiral")