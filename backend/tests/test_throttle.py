"""Rate-limit tests for the public API.

Covers:
* ``/simulate`` returns a structured 429 once the per-IP cap is reached
* ``/health`` keeps a much larger budget than ``/simulate``
* limits are scoped per client IP (``X-Forwarded-For`` under 1 trusted hop)
* the 429 response still carries CORS headers so the SPA can read it
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def simulate_cap():
    """Temporarily shrink the simulate throttle to 2 requests / minute."""
    from api import throttling

    throttle = throttling.SIMULATE_THROTTLE
    saved = (throttle.num_requests, throttle.duration)
    throttle.num_requests, throttle.duration = 2, 60
    try:
        yield throttle
    finally:
        throttle.num_requests, throttle.duration = saved


@pytest.fixture
def health_cap():
    """Temporarily shrink the health throttle to 3 requests / minute."""
    from api import throttling

    throttle = throttling.HEALTH_THROTTLE
    saved = (throttle.num_requests, throttle.duration)
    throttle.num_requests, throttle.duration = 3, 60
    try:
        yield throttle
    finally:
        throttle.num_requests, throttle.duration = saved


async def _client():
    import httpx

    import app as backend

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend.application), base_url="http://test"
    )


async def test_simulate_returns_structured_429(simulate_cap) -> None:
    async with await _client() as client:
        first = await client.post(
            "/api/v1/simulate", json={"seed": 11}, headers={"X-Forwarded-For": "10.0.0.1"}
        )
        second = await client.post(
            "/api/v1/simulate", json={"seed": 12}, headers={"X-Forwarded-For": "10.0.0.1"}
        )
        third = await client.post(
            "/api/v1/simulate", json={"seed": 13}, headers={"X-Forwarded-For": "10.0.0.1"}
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429

    body = third.json()
    assert body["code"] == "E_RATE_LIMITED"
    assert body["retry_after_seconds"] >= 1
    assert "retry-after" in {k.lower() for k in third.headers}


async def test_health_has_a_larger_budget_than_simulate(simulate_cap, health_cap) -> None:
    async with await _client() as client:
        # Exhaust the simulate cap first.
        for i in range(2):
            await client.post("/api/v1/simulate", json={"seed": i})
        blocked = await client.post("/api/v1/simulate", json={"seed": 99})
        assert blocked.status_code == 429

        # Health still has room (cap = 3).
        for _ in range(3):
            ok = await client.get("/api/v1/health")
            assert ok.status_code == 200
        assert (await client.get("/api/v1/health")).status_code == 429


async def test_limits_are_scoped_per_client_ip(simulate_cap) -> None:
    async with await _client() as client:
        for seed in (1, 2):
            r = await client.post(
                "/api/v1/simulate",
                json={"seed": seed},
                headers={"X-Forwarded-For": "203.0.113.7"},
            )
            assert r.status_code == 200

        exhausted = await client.post(
            "/api/v1/simulate",
            json={"seed": 3},
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
        assert exhausted.status_code == 429

        # A different client gets its own budget.
        other = await client.post(
            "/api/v1/simulate",
            json={"seed": 4},
            headers={"X-Forwarded-For": "203.0.113.8"},
        )
        assert other.status_code == 200


async def test_429_carries_cors_headers_for_allowed_origin(simulate_cap) -> None:
    async with await _client() as client:
        headers = {"Origin": "http://localhost:5173", "X-Forwarded-For": "198.51.100.1"}
        await client.post("/api/v1/simulate", json={"seed": 1}, headers=headers)
        await client.post("/api/v1/simulate", json={"seed": 2}, headers=headers)
        limited = await client.post("/api/v1/simulate", json={"seed": 3}, headers=headers)

    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert limited.headers["access-control-expose-headers"].find("retry-after") >= 0


async def test_default_limits_are_generous_enough_for_the_demo() -> None:
    """Guard against a mis-set env var throttling the demo to unusable rates."""
    from api import throttling

    for throttle in (
        throttling.SIMULATE_THROTTLE,
        throttling.HEALTH_THROTTLE,
        throttling.HEALTH_DEEP_THROTTLE,
    ):
        assert throttle.num_requests is not None
        assert throttle.num_requests >= 5
        assert throttle.duration is not None
        assert throttle.duration > 0
