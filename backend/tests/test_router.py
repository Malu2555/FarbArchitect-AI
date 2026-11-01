"""API surface tests: /health, /health/deep and CORS preflight behaviour."""

from __future__ import annotations

import pytest

from core.middleware import CorsMiddleware


def _make_request(cls, *, origin: str = "", method: str = "GET"):
    req = cls()
    req.method = method
    if origin:
        req.META["HTTP_ORIGIN"] = origin
    req.META["HTTP_HOST"] = "test"
    return req


def test_app_imports_with_ninja_router() -> None:
    import app

    paths = [getattr(u, "pattern", None) for u in app.urlpatterns]
    assert any(str(p) == "api/" for p in paths)


def test_health_view_returns_expected_shape() -> None:
    from api.router import health

    body = health(request=None)  # type: ignore[arg-type]
    assert body["status"] == "ok"
    assert body["service"] == "farbarchitect-ai"
    assert body["features"]["fdm_solver"] is True


def test_cors_middleware_sets_headers_only_for_allowlisted_origin() -> None:
    from django.http import HttpRequest, HttpResponse

    def get_response(_req):
        return HttpResponse("ok")

    mw = CorsMiddleware(get_response)

    # Allowed origin
    req = _make_request(HttpRequest, origin="http://localhost:5173")
    resp = mw(req)
    assert resp["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "POST" in resp["Access-Control-Allow-Methods"]

    # Disallowed origin
    req2 = _make_request(HttpRequest, origin="https://evil.example.com")
    resp2 = mw(req2)
    assert "Access-Control-Allow-Origin" not in resp2


def test_cors_middleware_short_circuits_preflight() -> None:
    from django.http import HttpRequest

    called = {"view": 0}

    def get_response(_req):
        called["view"] += 1
        raise AssertionError("view must not run for preflight")

    mw = CorsMiddleware(get_response)

    req = _make_request(HttpRequest, origin="http://localhost:5173", method="OPTIONS")
    req.META["HTTP_ACCESS_CONTROL_REQUEST_METHOD"] = "POST"

    resp = mw(req)
    assert resp.status_code == 204
    assert called["view"] == 0
    assert resp["Access-Control-Allow-Origin"] == "http://localhost:5173"


@pytest.mark.asyncio
async def test_harness_health_route_end_to_end() -> None:
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"

        pre = await client.options(
            "/api/v1/simulate",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-origin"] == "http://localhost:5173"

        # Disallowed origin gets no CORS grant
        bad = await client.options(
            "/api/v1/simulate",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert bad.status_code == 204
        assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------------------
# Stage 6 — POST /api/v1/simulate
# ---------------------------------------------------------------------------
def _quiet_equipment() -> list[dict]:
    return [
        {
            "id": "METROLOGY-00",
            "name": "Metrology",
            "x": 10,
            "y": 10,
            "w": 3,
            "h": 3,
            "heat_watts": 80.0,
            "vibration_db": 40.0,
        }
    ]


def _dirty_equipment() -> list[dict]:
    return [
        {
            "id": "A-00",
            "name": "Heater A",
            "x": 45,
            "y": 45,
            "w": 4,
            "h": 4,
            "heat_watts": 400.0,
            "vibration_db": 95.0,
        },
        {
            "id": "B-00",
            "name": "Heater B",
            "x": 48,
            "y": 45,
            "w": 4,
            "h": 4,
            "heat_watts": 400.0,
            "vibration_db": 95.0,
        },
    ]


@pytest.mark.asyncio
async def test_simulate_clean_layout_takes_fast_path() -> None:
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/simulate",
            json={"seed": 999, "equipment": _quiet_equipment()},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["seed"] == 999
    assert body["providers_used"] == []
    assert body["delta_t"] == 0.0
    assert body["corrections"][0]["error_codes"] == []
    assert body["before"]["peak_c"] == body["after"]["peak_c"]
    assert len(body["final_layout"]) == 1

    # Stage 2 always runs its deterministic local path on the clean-layout
    # fast path, so the SPA still receives a canvas spec + PNG preview.
    spatial = body["spatial"]
    assert spatial is not None
    assert spatial["spatial_spec"]["canvas"]["source"] == "FALLBACK"
    assert spatial["spatial_spec"]["canvas"]["no_overlap"] is True
    assert spatial["spatial_spec"]["canvas"]["no_clipping"] is True
    assert len(spatial["spatial_spec"]["footprints"]) == 1
    assert spatial["preview_png_data_url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_simulate_dirty_layout_runs_corrector_and_falls_back() -> None:
    # No keys are set in .env, so this exercises the deterministic fallback path
    # rather than making an outbound HTTP call.
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/simulate",
            json={"seed": 7, "equipment": _dirty_equipment()},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["seed"] == 7
    assert len(body["final_layout"]) == 2
    assert len(body["corrections"]) >= 1
    assert {"before", "after"} <= body.keys()
    # Every correction turn must be within the hard 3-turn bound
    assert all(1 <= c["turn"] <= 3 for c in body["corrections"])


@pytest.mark.asyncio
async def test_simulate_generated_scene_shape() -> None:
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/simulate", json={"seed": 42})

    assert resp.status_code == 200
    body = resp.json()
    assert body["seed"] == 42
    assert body["scene"]["seed"] == 42
    assert len(body["scene"]["equipment"]) == 8
    assert "temp_grid_rle" in body["before"]
    assert "temp_grid_rle" in body["after"]

    # Explicit serialized payload for the frontend canvas.
    assert body["spatial"]["spatial_spec"]["canvas"]["canvas_px"] == [500, 500]
    assert body["spatial"]["spatial_spec"]["canvas"]["cell_size_px"] == 5
    assert len(body["spatial"]["spatial_spec"]["footprints"]) == 8
    assert body["spatial_source"] == "FALLBACK"


@pytest.mark.asyncio
async def test_simulate_rejects_unknown_payload_keys() -> None:
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/simulate",
            json={"seed": 1, "surprise": True},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario editor — POST /api/v1/simulate/synopsis + GET /equipment/catalog
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_synopsis_endpoint_never_calls_a_provider() -> None:
    """Live drag preview must be physics-only: the badge updates on mouse-up."""
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/simulate/synopsis",
            json={"equipment": _dirty_equipment()},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["providers_used"] == []
    assert body["equipment_count"] == 2
    assert body["peak_c"] > 0
    assert isinstance(body["peak_at"], list) and len(body["peak_at"]) == 2
    assert "E_TEMP_PEAK" in body["error_codes"]
    assert len(body["hazards"]) >= 1


@pytest.mark.asyncio
async def test_synopsis_endpoint_accepts_a_clean_user_layout() -> None:
    import httpx

    import app as backend

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/simulate/synopsis",
            json={"equipment": _quiet_equipment()},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["error_codes"] == []
    assert body["equipment_count"] == 1


@pytest.mark.asyncio
async def test_synopsis_endpoint_rejects_out_of_bounds_equipment() -> None:
    """x/y are validated against the grid, so a bad drop is a 422 not a 500."""
    import httpx

    import app as backend

    bogus = _quiet_equipment()
    bogus[0]["x"] = 500  # >> GRID_SIZE

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/simulate/synopsis", json={"equipment": bogus})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_equipment_catalog_endpoint_matches_generator_catalog() -> None:
    import httpx

    import app as backend
    from core.synthetic_gen import CATALOG

    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/equipment/catalog")

    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == len(CATALOG)

    # The catalog is the single source of truth: every row must round-trip the
    # generator's numbers so a dragged-in machine matches a generated one.
    by_id = {row["id"]: row for row in rows}
    for template in CATALOG:
        name = str(template["name"])
        assert name in by_id, f"{name} missing from /equipment/catalog"
        row = by_id[name]
        assert row["w"] == int(template["w"])
        assert row["h"] == int(template["h"])
        assert row["heat_watts"] == float(template["heat_watts"])
        assert row["vibration_db"] == float(template["vibration_db"])
        assert row["glyph"]
        assert row["name"]

