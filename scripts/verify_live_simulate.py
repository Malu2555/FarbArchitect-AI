"""Slow-path verification: real backend + real provider, with generous timeout.

Unlike `smoke_stage1.py` (in-process ASGI) this boots uvicorn on a free port
so the whole HTTP stack is exercised, and prints an explicit wall-clock time
so 60-90 s reasoning-model latencies are visible rather than mysterious.

Usage::

    python scripts/verify_live_simulate.py [seed]
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


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
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
    try:
        _wait(f"{base}/api/v1/health")
        print(f"server up on {base}")

        started = time.time()
        status, body = _post(f"{base}/api/v1/simulate", {"seed": seed})
        elapsed = time.time() - started
        print(f"POST /api/v1/simulate -> {status} in {elapsed:.1f}s")

        if status != 200:
            print("FAIL:", json.dumps(body)[:400])
            return 1

        before = body["before"]["peak_c"]
        after = body["after"]["peak_c"]
        delta = body["delta_t"]
        turns = len(body["corrections"])
        providers = body["providers_used"]
        prints = len(body["spatial"]["spatial_spec"]["footprints"])
        png_ok = body["spatial"]["preview_png_data_url"].startswith("data:image/png;base64,")

        print(f"  providers_used = {providers}")
        print(f"  before_peak    = {before:.2f} C")
        print(f"  after_peak     = {after:.2f} C")
        print(f"  delta_t        = {delta:+.2f} C")
        print(f"  corrections    = {turns} turns")
        print(f"  spatial        = {prints} footprints, png_ok={png_ok}")

        if not providers:
            print("WARN: providers_used is empty -> check FIREWORKS_API_KEY / META_LLAMA_KEY")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
