"""Live rate-limit verification against a real uvicorn server.

Boots `app:app` on a random port with a deliberately low `RATE_SIMULATE`,
hammers the endpoint and asserts a 429 with `Retry-After` appears exactly when
the budget is exhausted. Run with::

    python scripts/verify_rate_limit.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
LIMIT = 3


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post(url: str, body: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"raw": payload.decode(errors="replace")}
        return exc.code, parsed, dict(exc.headers)


def main() -> int:
    port = _free_port()
    env = os.environ.copy()
    env["RATE_SIMULATE"] = f"{LIMIT}/min"
    env["RATE_HEALTH"] = "120/min"
    env["DJANGO_DEBUG"] = "true"

    server = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"{base}/api/v1/health", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("FAIL: server did not start")
            return 1

        print(f"server up on :{port}  (RATE_SIMULATE={LIMIT}/min)")

        statuses: list[int] = []
        for i in range(LIMIT + 2):
            status, body, headers = _post(f"{base}/api/v1/simulate", {"seed": 100 + i})
            statuses.append(status)
            marker = "OK " if status == 200 else "BLK"
            print(f"  request {i + 1}: {marker} {status}")

        expected = [200] * LIMIT + [429] * 2
        if statuses != expected:
            print(f"FAIL: expected {expected}, got {statuses}")
            return 1

        _, body, headers = _post(f"{base}/api/v1/simulate", {"seed": 999})
        code = body.get("code")
        wait = body.get("retry_after_seconds")
        retry_header = headers.get("Retry-After") or headers.get("retry-after")

        assert code == "E_RATE_LIMITED", f"unexpected code {code!r}"
        assert isinstance(wait, int) and wait >= 1, f"bad retry_after {wait!r}"
        assert retry_header is not None, "missing Retry-After header"

        print(f"429 body: code={code} retry_after={wait}s header={retry_header}")

        health = urllib.request.urlopen(f"{base}/api/v1/health", timeout=5)
        assert health.status == 200, "health should be unaffected by simulate throttling"
        print("health still 200 -> independent scope confirmed")

        print("PASS")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
