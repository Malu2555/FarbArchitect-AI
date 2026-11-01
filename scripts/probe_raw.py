"""Raw probe — print the exact `choices[0].message.content` the model returns.

Usage::

    python scripts/probe_raw.py [--schema]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

# Must run before any print: a cp1252 Windows console otherwise dies on
# characters such as U+2248 in the model's prose.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

import app  # noqa: E402,F401  (boots settings + loads backend/.env)

from agents.corrector import LAYOUT_DELTA_SCHEMA, SYSTEM_PROMPT  # noqa: E402
from core.providers import FireworksProvider  # noqa: E402


def _arg(name: str) -> str | None:
    argv = sys.argv[1:]
    if name in argv:
        idx = argv.index(name)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


async def main() -> int:
    model_override = _arg("--model")
    if model_override:
        os.environ["FIREWORKS_MODEL_ID"] = model_override

    provider = FireworksProvider()
    numeric = [a for a in sys.argv[1:] if a.isdigit()]
    max_tokens = int(numeric[0]) if numeric else 1024

    user_payload = {
        "errors": ["E_TEMP_PEAK", "E_VIBRATION_CONFLICT"],
        "temp_limit_c": 26,
        "reported_peak_c": 31.49,
        "reported_peak_at": [36, 45],
        "hazards": [{"type": "vibration", "a_id": "LITHO_SCANNER-00", "b_id": "ETCH_CHAMBER-01"}],
        "machines": [
            {"id": "LITHO_SCANNER-00", "x": 10, "y": 71, "w": 8, "h": 6,
             "heat_watts": 380.0, "vibration_db": 92.0},
            {"id": "ETCH_CHAMBER-01", "x": 61, "y": 42, "w": 5, "h": 5,
             "heat_watts": 220.0, "vibration_db": 78.0},
        ],
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Please produce a corrected layout as JSON.\n\n"
         + json.dumps(user_payload, separators=(",", ":"))},
    ]

    payload = {
        "model": provider.model_id,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
        **provider._body_overrides(),
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=120.0, write=120.0, pool=5.0)
    ) as client:
        resp = await client.post(
            f"{provider.base_url}/chat/completions",
            headers=provider.headers,
            json=payload,
        )

    print("status:", resp.status_code)
    body = resp.json()

    # Print finish_reason + usage for diagnosis.
    choice = (body.get("choices") or [{}])[0]
    print("finish_reason:", choice.get("finish_reason"))
    print("usage:", body.get("usage"))

    content = choice.get("message", {}).get("content")
    print("content_type:", type(content).__name__)
    print("content_len:", len(content) if isinstance(content, str) else "n/a")
    print("_body_overrides:", provider._body_overrides())
    print("reasoning_effort attr:", repr(provider.reasoning_effort))
    print("---RAW CONTENT STARTS---")
    print(content if isinstance(content, str) else repr(content))
    print("---RAW CONTENT ENDS---")

    # Try the current parser
    try:
        parsed = json.loads(content) if isinstance(content, str) else None
        print("json.loads: OK")
        print("top_keys:", list(parsed.keys()) if isinstance(parsed, dict) else "n/a")
    except Exception as exc:
        print("json.loads: FAILED ->", type(exc).__name__, str(exc)[:200])

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
