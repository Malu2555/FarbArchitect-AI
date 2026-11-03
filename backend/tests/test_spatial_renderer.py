"""Tests for the spatial renderer (Stage 2 — Muse Image) and PNG encoder."""

from __future__ import annotations

import base64

import numpy as np
import pytest

from agents.schemas import EquipmentItem
from agents.spatial_renderer import (
    build_fallback_result,
    render_spatial,
    sanitize_layout,
    validate_layout,
)
from core.png import PNG_SIGNATURE, png_data_url, png_encode_rgb
from core.providers import ProviderError


def _item(id_: str, x: int, y: int, w: int = 4, h: int = 4) -> EquipmentItem:
    return EquipmentItem(
        id=id_,
        name=f"Machine {id_}",
        x=x,
        y=y,
        w=w,
        h=h,
        heat_watts=200.0,
        vibration_db=60.0,
    )


class _StubImageProvider:
    """Stub that mimics `MetaImageProvider.complete`."""

    name = "meta_image"

    def __init__(self, *, payload=None, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    async def complete(self, messages, schema=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise ProviderError(self.name, "E_STUB", "simulated image failure")
        return self.payload or {"footprints": [], "notes": []}


# ---------------------------------------------------------------------------
# PNG encoder
# ---------------------------------------------------------------------------
def test_png_encoder_emits_valid_stream() -> None:
    image = np.zeros((4, 5, 3), dtype=np.uint8)
    image[:, :] = (255, 0, 0)

    raw = png_encode_rgb(image)

    assert raw.startswith(PNG_SIGNATURE)
    assert raw.endswith(b"IEND\xaeB`\x82")
    assert b"IHDR" in raw
    assert b"IDAT" in raw


def test_png_data_url_round_trips_to_bytes() -> None:
    image = np.zeros((3, 3, 3), dtype=np.uint8)

    url = png_data_url(image)

    assert url.startswith("data:image/png;base64,")
    payload = base64.b64decode(url.split(",", 1)[1])
    assert payload.startswith(PNG_SIGNATURE)


def test_png_encoder_rejects_wrong_dtype() -> None:
    image = np.zeros((4, 5, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        png_encode_rgb(image)


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------
def test_sanitize_clamps_out_of_bounds() -> None:
    big = _item("BIG", x=97, y=10, w=5, h=3)  # x + w = 102, off the grid
    sanitized, notes = sanitize_layout([big])

    assert sanitized[0].x + sanitized[0].w <= 100
    assert any("clamped" in n for n in notes)
    no_overlap, no_clipping = validate_layout(sanitized)
    assert no_overlap and no_clipping


def test_sanitize_pushes_apart_overlap() -> None:
    a = _item("A", x=45, y=45)
    b = _item("B", x=46, y=45)  # heavy overlap with A
    sanitized, notes = sanitize_layout([a, b])

    no_overlap, no_clipping = validate_layout(sanitized)
    assert no_overlap, f"still overlapping after sanitize (notes={notes})"
    assert no_clipping


def test_sanitize_is_deterministic() -> None:
    items = [_item("A", x=30, y=30), _item("B", x=32, y=30)]
    first, _ = sanitize_layout(items)
    second, _ = sanitize_layout(items)
    assert [i.model_dump() for i in first] == [i.model_dump() for i in second]


# ---------------------------------------------------------------------------
# Fallback result
# ---------------------------------------------------------------------------
def test_fallback_result_has_canvas_provenance_and_png() -> None:
    items = [_item("A", x=10, y=10), _item("B", x=60, y=60)]
    result = build_fallback_result(items)

    spec = result.spatial_spec
    assert spec.canvas.source == "FALLBACK"
    assert spec.canvas.grid_size == 100
    assert spec.canvas.cell_size_px == 5
    assert spec.canvas.canvas_px == (500, 500)
    assert spec.canvas.no_overlap is True
    assert spec.canvas.no_clipping is True
    assert len(spec.footprints) == 2
    assert result.preview_png_data_url.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Muse Image path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_render_spatial_uses_muse_image_when_reply_is_valid() -> None:
    items = [_item("A", x=10, y=10)]
    provider = _StubImageProvider(
        payload={
            "footprints": [
                {
                    "id": "A",
                    "origin": [20, 20],
                    "size": [4, 4],
                    "label": "Etcher",
                    "z_index": 1,
                    "color_token": "teal",
                }
            ],
            "notes": ["ratified"],
        }
    )

    result = await render_spatial(items, provider=provider)  # type: ignore[arg-type]

    assert provider.calls == 1
    assert result.spatial_spec.canvas.source == "MUSE_IMAGE"
    assert result.spatial_spec.canvas.no_overlap is True
    assert result.spatial_spec.canvas.no_clipping is True
    box = result.spatial_spec.footprints[0]
    assert box.origin == (20, 20)
    assert box.label == "Etcher"


@pytest.mark.asyncio
async def test_render_spatial_rejects_overlapping_muse_reply() -> None:
    items = [_item("A", x=10, y=10), _item("B", x=60, y=60)]
    provider = _StubImageProvider(
        payload={
            "footprints": [
                {"id": "A", "origin": [10, 10], "size": [4, 4]},
                {"id": "B", "origin": [11, 10], "size": [4, 4]},  # overlaps A
            ]
        }
    )

    result = await render_spatial(items, provider=provider)  # type: ignore[arg-type]

    assert result.spatial_spec.canvas.source == "FALLBACK"
    assert result.spatial_spec.canvas.no_overlap is True


@pytest.mark.asyncio
async def test_render_spatial_rejects_resized_footprints() -> None:
    items = [_item("A", x=10, y=10, w=4, h=4)]
    provider = _StubImageProvider(
        payload={
            "footprints": [
                {"id": "A", "origin": [10, 10], "size": [40, 40]},  # resized!
            ]
        }
    )

    result = await render_spatial(items, provider=provider)  # type: ignore[arg-type]

    assert result.spatial_spec.canvas.source == "FALLBACK"


@pytest.mark.asyncio
async def test_render_spatial_falls_back_when_provider_errors() -> None:
    items = [_item("A", x=10, y=10)]
    provider = _StubImageProvider(fail=True)

    result = await render_spatial(items, provider=provider)  # type: ignore[arg-type]

    assert provider.calls == 1
    assert result.spatial_spec.canvas.source == "FALLBACK"
    assert result.preview_png_data_url.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_render_spatial_uses_second_provider_when_first_errors() -> None:
    """Meta Image fails -> Fireworks Image succeeds -> MUSE_IMAGE provenance."""
    items = [_item("A", x=10, y=10)]
    meta = _StubImageProvider(fail=True)
    fireworks = _StubImageProvider(
        payload={
            "footprints": [
                {
                    "id": "A",
                    "origin": [20, 20],
                    "size": [4, 4],
                    "label": "Etcher",
                    "z_index": 1,
                    "color_token": "teal",
                }
            ],
            "notes": ["fireworks-ratified"],
        }
    )

    result = await render_spatial(items, providers=[meta, fireworks])  # type: ignore[arg-type]

    assert meta.calls == 1
    assert fireworks.calls == 1
    assert result.spatial_spec.canvas.source == "MUSE_IMAGE"
    assert result.spatial_spec.footprints[0].origin == (20, 20)


@pytest.mark.asyncio
async def test_render_spatial_uses_second_provider_when_first_reply_unusable() -> None:
    """Meta Image returns an unusable reply -> Fireworks Image is still tried."""
    items = [_item("A", x=10, y=10)]
    meta = _StubImageProvider(
        payload={
            "footprints": [
                {"id": "A", "origin": [10, 10], "size": [40, 40]}  # resized!
            ]
        }
    )
    fireworks = _StubImageProvider(
        payload={
            "footprints": [
                {"id": "A", "origin": [12, 12], "size": [4, 4], "label": "Etcher"}
            ],
            "notes": [],
        }
    )

    result = await render_spatial(items, providers=[meta, fireworks])  # type: ignore[arg-type]

    assert meta.calls == 1
    assert fireworks.calls == 1
    assert result.spatial_spec.canvas.source == "MUSE_IMAGE"
    assert result.spatial_spec.footprints[0].origin == (12, 12)


@pytest.mark.asyncio
async def test_render_spatial_falls_back_when_entire_chain_errors() -> None:
    items = [_item("A", x=10, y=10)]
    meta = _StubImageProvider(fail=True)
    fireworks = _StubImageProvider(fail=True)

    result = await render_spatial(items, providers=[meta, fireworks])  # type: ignore[arg-type]

    assert meta.calls == 1
    assert fireworks.calls == 1
    assert result.spatial_spec.canvas.source == "FALLBACK"


@pytest.mark.asyncio
async def test_render_spatial_empty_layout() -> None:
    result = await render_spatial([])

    assert result.spatial_spec.footprints == []
    assert result.spatial_spec.canvas.no_overlap is True
    assert result.spatial_spec.canvas.no_clipping is True


