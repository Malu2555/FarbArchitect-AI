"""Shared pytest configuration.

Adds ``backend/`` to ``sys.path`` so tests can be launched from any cwd, and
makes sure Django is configured once before Django-dependent tests run.
Also resets the in-process rate-limit cache between tests so throttling state
never leaks across cases, and blanks real provider credentials so the suite
never accidentally makes a live API call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

#: Provider credentials that must not leak from ``backend/.env`` into tests.
#: ``monkeypatch.setenv`` inside individual tests still works — it overrides
#: the blank we set here.
_LIVE_PROVIDER_KEYS: tuple[str, ...] = (
    "FIREWORKS_API_KEY",
    "META_LLAMA_KEY",
    "META_IMAGE_MODEL_ID",
)


def pytest_configure() -> None:
    """Configure Django lazily so provider/physics tests stay lightweight.

    ``import app`` triggers ``load_dotenv`` which copies any *real* keys from
    ``backend/.env`` into ``os.environ``. Immediately after that we blank the
    credentials so ``pytest`` never accidentally spends Fireworks/Meta quota
    or turns ``test_router.py`` into a slow live-integration test.
    """
    try:
        import app  # noqa: F401  (runs settings.configure once)
    except Exception:  # pragma: no cover - surface only if requested
        pass

    for key in _LIVE_PROVIDER_KEYS:
        os.environ[key] = ""


@pytest.fixture(autouse=True)
def _reset_throttle_cache():
    """Clear the LocMemCache-backed throttle counters around every test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


