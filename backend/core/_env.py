"""Tiny .env loader - no python-dotenv dependency.

Only sets variables that are NOT already present in ``os.environ``; Modal
secrets and platform env vars therefore always win over the local file.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_dotenv"]


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from a dotenv file. Returns applied pairs."""
    path = Path(path)
    applied: dict[str, str] = {}
    if not path.exists():
        return applied

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
