"""Rate limiting for the public API - Django Ninja throttling, zero extra deps.

Policy
------
* ``POST /simulate``    - expensive (physics + up to 3 LLM calls) -> strict cap
* ``GET  /health``      - cheap liveness probe                    -> generous cap
* ``GET  /health/deep`` - dependency introspection                -> moderate cap

Rates come from the environment at import time so they can be tuned per
deployment without a code change::

    RATE_SIMULATE=12/minute
    RATE_HEALTH=120/minute
    RATE_HEALTH_DEEP=30/minute
    TRUSTED_PROXY_HOPS=1

Storage
-------
Django's default ``LocMemCache`` is used (see ``CACHES`` in ``app.py``).

This means limits are enforced **per container**, not globally: with N warm
Modal containers the aggregate ceiling is roughly N x the configured rate.
That trade-off is deliberate - a shared Redis would reintroduce external
state and violate the "no databases" invariant of this project.

Client identity
---------------
``AnonRateThrottle`` keys on the client IP. Behind Modal's proxy the real
client address arrives in ``X-Forwarded-For``; ``NINJA_NUM_PROXIES`` tells
Django Ninja how many trusted hops to strip. Locally (no proxy header) it
falls back to ``REMOTE_ADDR`` automatically.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from ninja.errors import Throttled
from ninja.throttling import AnonRateThrottle

__all__ = [
    "CATALOG_THROTTLE",
    "HEALTH_DEEP_THROTTLE",
    "HEALTH_THROTTLE",
    "PREVIEW_THROTTLE",
    "SIMULATE_THROTTLE",
    "ScopedAnonThrottle",
    "default_rate",
    "normalize_rate",
    "throttled_handler",
]

logger = logging.getLogger(__name__)


class ScopedAnonThrottle(AnonRateThrottle):
    """Per-IP throttle with an explicit scope.

    ``AnonRateThrottle`` hard-codes ``scope = "anon"`` and builds its cache key
    as ``throttle_<scope>_<ip>``. Using the stock class for several endpoints
    would make them all **share one counter bucket**, so hitting ``/simulate``
    would eat into the ``/health`` budget. Giving each endpoint its own scope
    keeps the budgets independent.
    """

    def __init__(self, scope_name: str, rate: str) -> None:
        # Set before super().__init__ so get_cache_key() picks it up.
        self.scope = scope_name
        super().__init__(rate)


#: Django Ninja's ``parse_rate`` only understands the short period suffixes
#: below. Long human spellings ("minute", "hour") raise ``ValueError`` at
#: import time, so we normalize them here instead.
_PERIOD_ALIASES: Final[dict[str, str]] = {
    "s": "s",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "m": "m",
    "min": "m",
    "mins": "m",
    "minute": "m",
    "minutes": "m",
    "h": "h",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "d": "d",
    "day": "d",
    "days": "d",
}


def normalize_rate(raw: str, fallback: str) -> str:
    """Return a ``<count>/<period>`` string Django Ninja can actually parse.

    Accepts both ``12/minute`` and ``12/min`` and ``12/m``. Anything
    unparsable falls back to ``fallback`` (with a warning) rather than
    crashing the container at boot.
    """
    raw = (raw or "").strip()
    count_part, _, unit_part = raw.partition("/")
    count_part = count_part.strip()
    unit_key = _PERIOD_ALIASES.get(unit_part.strip().lower())

    if not count_part.isdigit() or unit_key is None or int(count_part) <= 0:
        logger.warning("invalid_rate_setting value=%r using=%r", raw, fallback)
        return fallback
    return f"{int(count_part)}/{unit_key}"


def default_rate(env_key: str, fallback: str) -> str:
    """Read + normalize a rate string from the environment.

    An absent/blank variable silently uses ``fallback`` (the common case).
    A value that is *set but malformed* falls back to the default with a
    warning instead of crashing the container at import time.
    """
    raw = os.environ.get(env_key)
    if raw is None or not raw.strip():
        return normalize_rate(fallback, fallback)
    return normalize_rate(raw, fallback)


#: Per-IP cap on the expensive simulation endpoint (physics + LLM loop).
SIMULATE_THROTTLE: Final[ScopedAnonThrottle] = ScopedAnonThrottle(
    "simulate", default_rate("RATE_SIMULATE", "12/min")
)

#: Generous cap so platform liveness probes are never starved.
HEALTH_THROTTLE: Final[ScopedAnonThrottle] = ScopedAnonThrottle(
    "health", default_rate("RATE_HEALTH", "120/min")
)

#: Moderate cap for the introspection endpoint.
HEALTH_DEEP_THROTTLE: Final[ScopedAnonThrottle] = ScopedAnonThrottle(
    "health_deep", default_rate("RATE_HEALTH_DEEP", "30/min")
)

#: Physics-only synopsis used by the live drag preview in the scenario editor.
#: Much cheaper than ``/simulate`` (no LLM call at all), so the budget is far
#: more generous - a user dragging a machine must never trip the corrector cap.
PREVIEW_THROTTLE: Final[ScopedAnonThrottle] = ScopedAnonThrottle(
    "preview", default_rate("RATE_PREVIEW", "60/min")
)

#: Static equipment catalog. Cheap and cacheable, so the cap is generous.
CATALOG_THROTTLE: Final[ScopedAnonThrottle] = ScopedAnonThrottle(
    "catalog", default_rate("RATE_CATALOG", "120/min")
)



def throttled_handler(request, exc: Throttled):
    """Return a structured 429 with a ``Retry-After`` header.

    Kept consistent with the rest of the API's error envelope so the frontend
    can distinguish rate limiting from genuine failures.
    """
    from django.http import JsonResponse

    try:
        wait = int(exc.wait) if exc.wait is not None else 60
    except (TypeError, ValueError):
        wait = 60
    wait = max(1, wait)

    return JsonResponse(
        {
            "detail": "Rate limit exceeded. Slow down and retry shortly.",
            "code": "E_RATE_LIMITED",
            "retry_after_seconds": wait,
        },
        status=429,
        headers={"Retry-After": str(wait)},
    )
