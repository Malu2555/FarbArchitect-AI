"""End-to-end check of the scenario-editor flow (catalog -> preview -> agent).

Boots a real uvicorn process and drives the three new/extended endpoints the
way the SPA does:

    1. GET  /api/v1/equipment/catalog        -> catalog rows
    2. POST /api/v1/simulate/synopsis        -> physics-only live verdict
    3. POST /api/v1/simulate  {equipment}    -> full agentic correction

Assertions encode the contract the frontend relies on, in particular that the
live preview **never** calls a provider.

Usage::

    python scripts/verify_scenario_flow.py [seed]
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _call(url: str, body: dict | None = None, timeout: float = 300.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _wait(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} never became ready")


def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    port = _free_port()
    server = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        mark = "OK  " if condition else "FAIL"
        print(f"  [{mark}] {label}{f' - {detail}' if detail else ''}")
        if not condition:
            failures.append(label)

    try:
        _wait(f"{base}/api/v1/health")
        print(f"server up on {base}\n")
        return _run_checks(base, seed, check)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        if failures:
            print(f"\nFAILED ({len(failures)}): " + "; ".join(failures))
        else:
            print("\nPASS - scenario editor contract holds end to end")


def _run_checks(base: str, seed: int, check) -> int:  # noqa: ANN001
    # -- 1. catalog ----------------------------------------------------------
    status, catalog = _call(f"{base}/api/v1/equipment/catalog")
    print("1. GET /api/v1/equipment/catalog")
    check("returns 200", status == 200, f"status={status}")
    check(
        "has entries",
        isinstance(catalog, list) and len(catalog) > 0,
        f"{len(catalog) if isinstance(catalog, list) else '?'} rows",
    )
    if not isinstance(catalog, list) or not catalog:
        raise SystemExit("catalog unusable")

    required = {"id", "name", "w", "h", "heat_watts", "vibration_db"}
    check("rows carry the full template", required <= set(catalog[0]))
    by_id = {row["id"]: row for row in catalog}
    print(f"        ids: {', '.join(sorted(by_id))}\n")

    # -- 2. a deliberately hot, wall-hugging pair ----------------------------
    litho = by_id["LITHO_SCANNER"]
    thermal = by_id["THERMAL_CHAMBER"]

    def machine(template: dict, ident: str, x: int, y: int) -> dict:
        return {
            "id": ident,
            "name": template["name"],
            "x": x,
            "y": y,
            "w": template["w"],
            "h": template["h"],
            "heat_watts": template["heat_watts"],
            "vibration_db": template["vibration_db"],
        }

    layout = [
        machine(litho, "LITHO_SCANNER-01", 2, 2),
        machine(thermal, "THERMAL_CHAMBER-02", 4, 4),
    ]

    # -- 3. live physics preview --------------------------------------------
    started = time.time()
    status, synopsis = _call(f"{base}/api/v1/simulate/synopsis", {"equipment": layout})
    elapsed = time.time() - started
    print("2. POST /api/v1/simulate/synopsis (live preview)")
    check("returns 200", status == 200, f"status={status}")
    check(
        "never calls a provider",
        synopsis.get("providers_used") == [],
        f"providers_used={synopsis.get('providers_used')}",
    )
    check("counts the machines", synopsis.get("equipment_count") == 2)
    check(
        "reports a peak",
        isinstance(synopsis.get("peak_c"), (int, float)),
        f"peak_c={synopsis.get('peak_c')}",
    )
    check(
        "flags a thermal violation",
        "E_TEMP_PEAK" in synopsis.get("error_codes", []),
        f"codes={synopsis.get('error_codes')}",
    )
    check("is fast (< 5 s)", elapsed < 5.0, f"{elapsed:.2f}s")
    hot_peak = synopsis.get("peak_c")
    print(f"        peak={hot_peak} C, hazards={len(synopsis.get('hazards', []))}\n")

    # -- 4. drag them apart -> the preview must improve ----------------------
    spread = [
        machine(litho, "LITHO_SCANNER-01", 30, 30),
        machine(thermal, "THERMAL_CHAMBER-02", 70, 70),
    ]
    status, synopsis2 = _call(f"{base}/api/v1/simulate/synopsis", {"equipment": spread})
    print("3. POST /api/v1/simulate/synopsis (after dragging apart)")
    check("returns 200", status == 200, f"status={status}")
    check(
        "dragging apart cools the floor",
        synopsis2["peak_c"] < hot_peak,
        f"{hot_peak} -> {synopsis2['peak_c']} C",
    )
    print()

    # -- 5. the agent runs on the user's own layout --------------------------
    started = time.time()
    status, sim = _call(f"{base}/api/v1/simulate", {"seed": seed, "equipment": layout})
    elapsed = time.time() - started
    print("4. POST /api/v1/simulate {equipment} (agent on a custom layout)")
    check("returns 200", status == 200, f"status={status}")
    check("echoes the seed", sim.get("seed") == seed, f"seed={sim.get('seed')}")
    check(
        "kept both machines",
        len(sim.get("final_layout", [])) == 2,
        f"{len(sim.get('final_layout', []))} final",
    )
    check(
        "never regresses the peak",
        sim["after"]["peak_c"] <= sim["before"]["peak_c"] + 1e-9,
        f"{sim['before']['peak_c']} -> {sim['after']['peak_c']} C",
    )
    check("shows the correction trail", len(sim.get("corrections", [])) >= 1)
    print(f"        providers_used={sim.get('providers_used')}")
    print(f"        delta_t={sim.get('delta_t'):+.2f} C in {elapsed:.1f}s")
    for turn in sim.get("corrections", []):
        codes = ",".join(turn.get("error_codes") or []) or "-"
        summary = (turn.get("summary") or "")[:90]
        print(f"        turn {turn['turn']} {turn['phase']:<9} [{codes}] {summary}")
    print()

    # -- 6. validation still bites -------------------------------------------
    bad = [dict(layout[0], x=999)]
    status, _ = _call(f"{base}/api/v1/simulate/synopsis", {"equipment": bad})
    print("5. POST /api/v1/simulate/synopsis (out-of-bounds machine)")
    check("rejects with 422", status == 422, f"status={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
