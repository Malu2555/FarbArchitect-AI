"""Live verification for the Stage-2 Muse Image spatial renderer.

Boots `app:app` on a real uvicorn port under two configurations and asserts
the `/simulate` payload always carries a valid canvas spec + PNG preview:

  A. No Meta credentials  -> deterministic LOCAL fallback
  B. Muse Image configured but unreachable -> graceful FALLBACK (no 5xx)

Run::

    python scripts/verify_muse_image.py
"""

from __future__ import annotations

import base64
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
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str, timeout_s: float = 40.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"server at {url} did not become ready")


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _assert_spatial(body: dict, expected_source: str, label: str) -> None:
    spatial = body.get("spatial")
    assert spatial, f"[{label}] response missing 'spatial'"

    spec = spatial.get("spatial_spec") or {}
    canvas = spec.get("canvas") or {}
    footprints = spec.get("footprints") or []

    assert footprints, f"[{label}] spatial spec has no footprints"
    assert canvas.get("grid_size") == 100, f"[{label}] bad grid_size"
    assert canvas.get("cell_size_px") == 5, f"[{label}] bad cell_size_px"
    assert list(canvas.get("canvas_px") or []) == [500, 500], (
        f"[{label}] bad canvas_px {canvas.get('canvas_px')}"
    )
    assert canvas.get("no_overlap") is True, f"[{label}] overlap reported"
    assert canvas.get("no_clipping") is True, f"[{label}] clipping reported"
    assert canvas.get("source") == expected_source, (
        f"[{label}] expected source={expected_source}, got {canvas.get('source')}"
    )

    url = spatial.get("preview_png_data_url") or ""
    assert url.startswith("data:image/png;base64,"), f"[{label}] bad preview data URL"
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw.startswith(PNG_SIGNATURE), f"[{label}] PNG signature missing"
    assert raw.rstrip().endswith(b"IEND\xaeB`\x82"), f"[{label}] PNG IEND missing"

    print(
        f"  [{label}] source={canvas.get('source')} "
        f"footprints={len(footprints)} png={len(raw)}B OK"
    )


def _run_case(label: str, env_overrides: dict[str, str], expected_source: str) -> int:
    port = _free_port()
    env = os.environ.copy()
    env.update(env_overrides)

    server = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for(f"{base}/api/v1/health")
        status, body = _post(f"{base}/api/v1/simulate", {"seed": 42})
        assert status == 200, f"[{label}] /simulate returned {status}"
        _assert_spatial(body, expected_source, label)
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def main() -> int:
    print("case A: no Meta credentials -> LOCAL fallback")
    _run_case(
        "A",
        {
            "META_LLAMA_KEY": "",
            "META_IMAGE_MODEL_ID": "",
            "DJANGO_DEBUG": "true",
        },
        "FALLBACK",
    )

    print("case B: Muse Image configured but unreachable -> graceful FALLBACK")
    _run_case(
        "B",
        {
            "META_LLAMA_KEY": "test-key-not-real",
            "META_IMAGE_MODEL_ID": "meta-muse-image-2026",
            "META_IMAGE_BASE_URL": "http://127.0.0.1:1/v1",
            "META_IMAGE_TIMEOUT_S": "3",
            "DJANGO_DEBUG": "true",
        },
        "FALLBACK",
    )

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
