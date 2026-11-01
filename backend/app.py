"""fabArchitect AI - in-memory Django + Django Ninja bootstrap.

Deliberately contains **no** ``manage.py``, no migrations, no database.
All state is request-scoped and lives in RAM inside the Modal container.

Import order:
    1. Load local ``.env`` (no-op inside Modal - secrets come from
       ``modal.Secret.from_name("llm-keys")``).
    2. ``settings.configure(...)`` with the minimum surface needed by
       Django Ninja + our own CORS middleware.
    3. Register routers and expose an ASGI ``application`` object that Modal
       (``@modal.asgi_app``) and uvicorn both understand.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Local .env (never overrides platform-injected env vars)
# ---------------------------------------------------------------------------
from core._env import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# 2. Configure Django once, in memory
# ---------------------------------------------------------------------------
from django.conf import settings  # noqa: E402


def _split_csv(raw: str, *, default: list[str] | None = None) -> list[str]:
    parts = [p.strip() for p in raw.split(",")]
    resolved = [p for p in parts if p]
    return resolved or (default or [])


def _allowed_hosts() -> list[str]:
    """Hosts allowed to hit the ASGI app.

    Includes ``test`` / ``testserver`` so pytest's httpx ASGITransport and
    in-process smoke scripts work out of the box (mirrors Django's own test
    runner behaviour) without weakening production posture.
    """
    hosts = _split_csv(os.environ.get("ALLOWED_HOSTS", ""), default=[])
    if not hosts:
        hosts = ["*"]
    for host in ("test", "testserver"):
        if host not in hosts:
            hosts.append(host)
    return hosts


if not settings.configured:
    settings.configure(
        DEBUG=os.environ.get("DJANGO_DEBUG", "false").lower() in {"1", "true", "yes"},
        SECRET_KEY=os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-secret"),
        ALLOWED_HOSTS=_allowed_hosts(),
        INSTALLED_APPS=[
            # Django Ninja does not strictly need to be installed, but installing
            # it enables template rendering features we do not use - keep minimal.
            "ninja",
        ],
        MIDDLEWARE=[
            "core.middleware.CorsMiddleware",
            "django.middleware.common.CommonMiddleware",
        ],
        ROOT_URLCONF="app",
        # No database: Django only complains if something tries to open a
        # connection, and none of our apps do.
        DATABASES={},
        USE_TZ=True,
        TIME_ZONE="UTC",
        DEFAULT_CHARSET="utf-8",
        LOGGING={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "simple": {"format": "[%(levelname)s] %(name)s: %(message)s"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "simple",
                },
            },
            "root": {"handlers": ["console"], "level": "INFO"},
        },
        # --- CORS (consumed by core.middleware.CorsMiddleware) -------------
        CORS_ALLOWED_ORIGINS=_split_csv(
            os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173"),
        ),
        CORS_ALLOW_METHODS=["GET", "POST", "OPTIONS"],
        CORS_ALLOW_HEADERS=["content-type", "accept", "authorization"],
        CORS_EXPOSE_HEADERS=["x-simulation-id", "retry-after"],
        CORS_ALLOW_CREDENTIALS=False,
        CORS_MAX_AGE=86400,
        # --- Cache -----------------------------------------------------------------
        # LocMemCache only: rate-limit counters live in process RAM, never on disk
        # and never in an external store. Consistent with the stateless invariant.
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "farbarchitect-ratelimit",
                "TIMEOUT": 300,
                "OPTIONS": {"MAX_ENTRIES": 4096},
            }
        },
        # --- Django Ninja throttling ---------------------------------------------
        # 1 trusted proxy hop (Modal) so X-Forwarded-For yields the real client IP.
        NINJA_NUM_PROXIES=int(os.environ.get("TRUSTED_PROXY_HOPS", "1")),
        # Scope-based fallbacks for any future endpoint that omits an explicit rate.
        NINJA_DEFAULT_THROTTLE_RATES={
            "anon": os.environ.get("RATE_ANON", "60/min"),
            "auth": "300/min",
            "user": "300/min",
        },
    )


import django  # noqa: E402

django.setup()

# ---------------------------------------------------------------------------
# 3. Django Ninja API + router registration
# ---------------------------------------------------------------------------
from django.urls import path  # noqa: E402
from ninja import NinjaAPI  # noqa: E402
from ninja.errors import Throttled  # noqa: E402

from api.router import router  # noqa: E402
from api.throttling import throttled_handler  # noqa: E402

api = NinjaAPI(
    title="fabArchitect AI",
    version="0.1.0",
    description="Stateless closed-loop multi-agent CAD simulation engine",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
# Structured 429 with Retry-After, consistent with the rest of the error envelope.
api.add_exception_handler(Throttled, throttled_handler)
api.add_router("/v1/", router)

urlpatterns = [path("api/", api.urls)]

# ---------------------------------------------------------------------------
# 4. ASGI application for uvicorn + Modal
# ---------------------------------------------------------------------------
from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()

# Convenience aliases so `uvicorn app:app` and `uvicorn app:application` both work.
app = application

__all__ = ["api", "app", "application", "urlpatterns"]
