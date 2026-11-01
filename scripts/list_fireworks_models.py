"""List the Fireworks models actually available to the configured API key.

Probes the OpenAI-compatible ``/models`` endpoint (and falls back to Fireworks'
account-scoped catalogue endpoint). Prints only slugs, never the key.

Usage::

    python scripts/list_fireworks_models.py
    python scripts/list_fireworks_models.py llama        # filter by substring
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from core.providers import FireworksProvider  # noqa: E402

CANDIDATE_PATHS = ("/models", "/v1/models")


def _get(url: str, key: str) -> tuple[int, object]:
    req = urllib.request.Request(
        url,
        headers={"authorization": f"Bearer {key}", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def _extract(payload: object) -> list[str]:
    """Normalize every plausible shape into a flat list of ids."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    else:
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        elif isinstance(row, dict):
            for key in ("id", "name", "model", "public"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    out.append(value)
                    break
    return out


def main() -> int:
    needle = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    provider = FireworksProvider()
    root = provider.base_url.rsplit("/inference/v1", 1)[0]

    for path in CANDIDATE_PATHS:
        status, payload = _get(f"https://api.fireworks.ai/inference/v1{path}", provider.api_key)
        if status == 200 and payload is not None:
            print(f"GET /inference/v1{path} -> 200")
            rows = _extract(payload)
            break
        print(f"GET /inference/v1{path} -> {status}")
    else:
        print("\nNeither /models path answered. The endpoint may be account-scoped:")
        print("  https://fireworks.ai/account/models")
        return 1

    meta = sorted({r for r in rows if "llama" in r.lower()})
    print(f"\ntotal models visible: {len(rows)}")
    print(f"llama-ish ({len(meta)}):")
    for row in meta:
        print(f"  {row}")

    print(f"\nALL {len(rows)} visible ids:")
    for row in sorted(set(rows)):
        print(f"  {row}")

    if needle:
        hits = sorted({r for r in rows if needle in r.lower()})
        print(f"\nmatch '{needle}' ({len(hits)}):")
        for row in hits[:80]:
            print(f"  {row}")

    print(f"\nroot={root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
