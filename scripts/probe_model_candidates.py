"""Measure every chat-capable Fireworks model this key can actually reach.

For each model: one minimal JSON-mode call, reporting latency, whether it is a
reasoning model (``reasoning_tokens > 0``), and whether the reply parses as the
JSON object our corrector needs. Never prints the API key.

Usage::

    python scripts/probe_model_candidates.py
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

import httpx  # noqa: E402

from core.providers import FireworksProvider  # noqa: E402

# Anything matching these is not a general chat model.
SKIP_TOKENS = (
    "embedding",
    "reranker",
    "routers/",
    "/auto",
    "spot",
)

PROMPT = (
    "You are a shop-floor layout corrector. Reply with ONE JSON object only:\n"
    '{"adjustments": [{"id": "rack-01", "x": 20, "y": 30}], '
    '"reasoning": "moved rack away from the wall"}\n'
    "No prose, no markdown fences."
)


async def probe(client: httpx.AsyncClient, model_id: str, key: str) -> dict:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "You output JSON only."},
            {"role": "user", "content": PROMPT},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    started = time.time()
    try:
        resp = await client.post(
            "https://api.fireworks.ai/inference/v1/chat/completions",
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json=payload,
        )
    except Exception as exc:  # noqa: BLE001
        return {"model": model_id, "status": 0, "error": type(exc).__name__,
                "elapsed": time.time() - started}
    elapsed = time.time() - started

    if resp.status_code >= 400:
        return {"model": model_id, "status": resp.status_code, "elapsed": elapsed,
                "error": f"HTTP {resp.status_code}"}

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") or 0

    parsed_ok = False
    try:
        parsed = json.loads(content)
        parsed_ok = isinstance(parsed, dict) and "adjustments" in parsed
    except json.JSONDecodeError:
        parsed_ok = False

    return {
        "model": model_id,
        "status": resp.status_code,
        "elapsed": elapsed,
        "reasoning_tokens": reasoning,
        "completion_tokens": usage.get("completion_tokens"),
        "finish": choice.get("finish_reason"),
        "json_ok": parsed_ok,
        "content_len": len(content),
    }


async def main() -> int:
    provider = FireworksProvider()
    key = provider.api_key
    if not key:
        print("FIREWORKS_API_KEY missing")
        return 1

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=10, pool=5)) as client:
        listing = await client.get(
            "https://api.fireworks.ai/inference/v1/models",
            headers={"authorization": f"Bearer {key}"},
        )
        listing.raise_for_status()
        raw = listing.json()
        ids = [row["id"] for row in raw.get("data", []) if "id" in row]

    candidates = [
        i for i in ids
        if i.startswith("accounts/fireworks/models/")
        and not any(token in i for token in SKIP_TOKENS)
    ]

    print(f"probing {len(candidates)} candidate(s) with a corrector-shaped prompt\n")
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=10, pool=5)) as client:
        for model_id in candidates:
            result = await probe(client, model_id, key)
            rows.append(result)
            label = model_id.rsplit("/", 1)[-1]
            if result.get("error"):
                print(f"  {label:<34} FAIL   {result['error']}")
            else:
                kind = "REASONING" if result["reasoning_tokens"] else "chat"
                print(
                    f"  {label:<34} {result['elapsed']:5.1f}s  {kind:<9}"
                    f" reason={result['reasoning_tokens']:<5} "
                    f"out={result['completion_tokens']:<5} "
                    f"json={'ok' if result['json_ok'] else 'BAD'}"
                    f"  finish={result['finish']}"
                )

    print("\n--- recommendation (non-reasoning + valid JSON + fastest) ---")
    usable = [
        r for r in rows
        if not r.get("error") and r["json_ok"] and r["reasoning_tokens"] == 0
    ]
    for r in sorted(usable, key=lambda x: x["elapsed"])[:5]:
        print(f"  {r['elapsed']:5.1f}s  {r['model']}")
    if not usable:
        print("  none - every reachable model either reasons or returned bad JSON")

    print("\n--- reasoning models (avoid: budget burn + latency) ---")
    for r in rows:
        if not r.get("error") and r["reasoning_tokens"]:
            print(f"  reason={r['reasoning_tokens']:<5} {r['elapsed']:5.1f}s  {r['model']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
