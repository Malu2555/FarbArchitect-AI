"""Probe whether `muse-spark-open` supports disabling reasoning.

Tries several payload shapes and reports `reasoning_tokens` / `content_len`
for each. Any config that yields `reasoning_tokens == 0` is a keeper.

Usage::

    python scripts/probe_reasoning.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from agents.corrector import SYSTEM_PROMPT  # noqa: E402
from core.providers import FireworksProvider  # noqa: E402


MESSAGES = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {
        "role": "user",
        "content": 'Produce a corrected layout. Scene: {"machines": ['
        '{"id":"A","x":45,"y":45,"w":4,"h":4,"heat_watts":400,"vibration_db":95},'
        '{"id":"B","x":48,"y":45,"w":4,"h":4,"heat_watts":400,"vibration_db":95}]}',
    },
]

VARIANTS: list[tuple[str, dict]] = [
    ("baseline (max_tokens=4096)", {"max_tokens": 4096}),
    ("reasoning_effort=low", {"max_tokens": 4096, "reasoning_effort": "low"}),
    ("thinking=disabled", {"max_tokens": 4096, "thinking": {"type": "disabled"}}),
    ("thinking.budget_tokens=64", {"max_tokens": 4096, "thinking": {"type": "enabled", "budget_tokens": 64}}),
]


async def main() -> int:
    provider = FireworksProvider()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=90.0, write=90.0, pool=5.0)
    ) as client:
        for label, extra in VARIANTS:
            payload = {
                "model": provider.model_id,
                "messages": MESSAGES,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "stream": False,
                **extra,
            }
            try:
                resp = await client.post(
                    f"{provider.base_url}/chat/completions",
                    headers=provider.headers,
                    json=payload,
                )
            except Exception as exc:
                print(f"{label:32s} -> NETWORK ERROR: {exc}")
                continue

            if resp.status_code >= 400:
                detail = resp.text[:160].replace("\n", " ")
                print(f"{label:32s} -> HTTP {resp.status_code}: {detail}")
                continue

            body = resp.json()
            choice = (body.get("choices") or [{}])[0]
            usage = body.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            content = choice.get("message", {}).get("content") or ""
            try:
                json.loads(content)
                valid = "valid_json"
            except json.JSONDecodeError:
                valid = "INVALID_JSON"

            print(
                f"{label:32s} -> 200 "
                f"finish={choice.get('finish_reason')!s:8s} "
                f"reasoning={details.get('reasoning_tokens', 0):5d} "
                f"completion={usage.get('completion_tokens', 0):5d} "
                f"content_len={len(content):5d} {valid}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
