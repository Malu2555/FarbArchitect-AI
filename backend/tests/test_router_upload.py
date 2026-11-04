"""End-to-end tests for the equipment-upload endpoints (/equipment/parse,
/equipment/place, /simulate/upload)."""

from __future__ import annotations

import io

import httpx
import pytest

import app as backend

CSV_FAB = (
    b"Cleanroom: Room A, 30, 20\n"
    b"\n"
    b"name,type,power (W),noise (dB),width (m),height (m)\n"
    b"ASML Stepper,stepper,380,92,4,3\n"
    b"LAM Etcher,etch,220,78,2.5,2.5\n"
)


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=backend.application)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# /equipment/parse
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_parse_endpoint_returns_drafts() -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/v1/equipment/parse",
            files={"file": ("fab.csv", CSV_FAB, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_kind"] == "csv"
    assert body["summary"]["rows_parsed"] == 2
    assert body["room"]["width_m"] == 30.0
    assert len(body["drafts"]) == 2


@pytest.mark.asyncio
async def test_parse_endpoint_rejects_unsupported_type() -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/v1/equipment/parse",
            files={"file": ("spec.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_parse_endpoint_rejects_unparseable_file() -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/v1/equipment/parse",
            files={"file": ("junk.csv", b"hello,world\nfoo,bar\n", "text/csv")},
        )
    # No header row / no machines -> 422.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /equipment/place
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_place_endpoint_assigns_coordinates() -> None:
    async with _client() as client:
        parsed = (
            await client.post(
                "/api/v1/equipment/parse",
                files={"file": ("fab.csv", CSV_FAB, "text/csv")},
            )
        ).json()
        resp = await client.post(
            "/api/v1/equipment/place",
            json={"upload": parsed, "strategy": "grid"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategy"] == "grid"
    assert len(body["equipment"]) == 2
    for item in body["equipment"]:
        assert 0 <= item["x"] < body["grid_size"]
        assert 0 <= item["y"] < body["grid_size"]


@pytest.mark.asyncio
async def test_place_endpoint_rejects_unknown_strategy() -> None:
    async with _client() as client:
        parsed = (
            await client.post(
                "/api/v1/equipment/parse",
                files={"file": ("fab.csv", CSV_FAB, "text/csv")},
            )
        ).json()
        resp = await client.post(
            "/api/v1/equipment/place",
            json={"upload": parsed, "strategy": "spiral"},
        )
    # Pydantic Literal validation -> 422.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /simulate/upload
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_simulate_upload_runs_full_chain() -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/v1/simulate/upload",
            files={"file": ("fab.csv", CSV_FAB, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["seed"] >= 0
    assert body["scene"]["equipment"]
    assert body["before"] and body["after"]
    assert "spatial" in body
    # The two machines are dense, so before has hazards; the response is a
    # complete simulate envelope either way.
    assert body["delta_t"] >= 0.0


@pytest.mark.asyncio
async def test_simulate_upload_rejects_bad_strategy() -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/v1/simulate/upload?strategy=spiral",
            files={"file": ("fab.csv", CSV_FAB, "text/csv")},
        )
    assert resp.status_code == 422
