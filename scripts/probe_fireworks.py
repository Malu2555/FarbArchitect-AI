"""Direct probe for the Fireworks provider — prints the exact error / latency.

Usage::

    python scripts/probe_fireworks.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from core.providers import FireworksProvider, ProviderError  # noqa: E402

PROMPT = (
    "Reply with ONE JSON object: "
    '{"adjustments": [{"id": "A", "x": 10, "y": 10}], "reasoning": "test"}'
)


async def main() -> int:
    provider = FireworksProvider()
    print(f"model_id={provider.model_id}")
    print(f"base_url={provider.base_url}")
    print(f"read_timeout={provider.timeout.read}s")
    print(f"max_tokens={provider.max_tokens}")
    print(f"key_len={len(provider.api_key)}")
    print()

    messages = [
        {"role": "system", "content": "You output JSON only."},
        {"role": "user", "content": PROMPT},
    ]

    started = time.time()
    try:
        reply = await provider.complete(
            messages,
            schema={"required": ["adjustments"]},
        )
    except ProviderError as exc:
        elapsed = time.time() - started
        print(f"FAILED after {elapsed:.1f}s  code={exc.code}")
        print(f"message: {exc.message}")
        return 1

    elapsed = time.time() - started
    print(f"OK in {elapsed:.1f}s")
    print(json.dumps(reply, indent=2)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
