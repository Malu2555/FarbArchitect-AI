"""Manual smoke test — boot ASGI in-process and exercise health + CORS + simulate.

Usage::

    python scripts/smoke_stage1.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

import app as backend  # noqa: E402  (module boot: settings.configure + NinjaAPI)


async def main() -> None:
    transport = httpx.ASGITransport(app=backend.application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/health")
        print("GET /api/v1/health ->", r.status_code)
        print("body:", r.json())

        r = await c.options(
            "/api/v1/simulate",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        print("OPTIONS /api/v1/simulate (allowed) ->", r.status_code)
        for key in (
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-max-age",
        ):
            print(f"  {key}: {r.headers.get(key)}")

        r = await c.options(
            "/api/v1/simulate",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        print("OPTIONS /api/v1/simulate (blocked) ->", r.status_code)
        print("  ACAO (should be None):", r.headers.get("access-control-allow-origin"))

        r = await c.post("/api/v1/simulate", json={"seed": 42})
        print("POST /api/v1/simulate (seed=42) ->", r.status_code)
        body = r.json()
        print(
            "  before peak =", round(body["before"]["peak_c"], 2),
            "| after peak =", round(body["after"]["peak_c"], 2),
            "| delta =", round(body["delta_t"], 2),
            "| turns =", len(body["corrections"]),
            "| providers =", body["providers_used"],
        )


if __name__ == "__main__":
    asyncio.run(main())



if __name__ == "__main__":
    asyncio.run(main())
