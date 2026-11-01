"""Run one /simulate and dump the full corrections trail with per-turn detail.

Usage::

    python scripts/debug_corrections.py [seed]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

import app as backend  # noqa: E402,F401


async def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42

    transport = httpx.ASGITransport(app=backend.application)
    started = time.time()
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=180.0
    ) as client:
        resp = await client.post("/api/v1/simulate", json={"seed": seed})
    elapsed = time.time() - started

    print(f"status={resp.status_code}  elapsed={elapsed:.1f}s")
    if resp.status_code != 200:
        print(resp.text[:800])
        return 1

    body = resp.json()
    print(f"seed={body['seed']}")
    print(f"providers_used={body['providers_used']}")
    print(f"spatial_source={body['spatial_source']}")
    print(
        f"before_peak={body['before']['peak_c']:.2f}  "
        f"after_peak={body['after']['peak_c']:.2f}  delta={body['delta_t']:.2f}"
    )
    print()
    print("corrections:")
    for entry in body["corrections"]:
        codes = ",".join(entry.get("error_codes") or []) or "-"
        peak = entry.get("peak_c")
        peak_txt = f"{peak:.2f}C" if peak is not None else "   -  "
        summary = (entry.get("summary") or "").replace("\n", " ")[:160]
        print(f"  turn {entry['turn']}  {entry['phase']:<9s} {peak_txt}  [{codes}]")
        print(f"      {summary}")

    print()
    print("layout diff (original -> final):")
    original = {e["id"]: e for e in body["scene"]["equipment"]}
    for item in body["final_layout"]:
        orig = original.get(item["id"], {})
        moved = (orig.get("x"), orig.get("y")) != (item["x"], item["y"])
        marker = "MOVED" if moved else "  -  "
        print(
            f"  {marker} {item['id']:<26s} "
            f"({orig.get('x', '?'):>3},{orig.get('y', '?'):>3}) -> "
            f"({item['x']:>3},{item['y']:>3})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
