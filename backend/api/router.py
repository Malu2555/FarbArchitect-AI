"""Django Ninja router mounted at ``/api/v1/``.

Endpoints
---------
* ``GET  /health``      — liveness + build metadata
* ``GET  /health/deep`` — dependency versions + provider key presence
* ``POST /simulate``    — full closed-loop simulation (physics + agentic loop)
"""

from __future__ import annotations

import os
import platform
from functools import lru_cache

from ninja import Router

from agents.corrector import classify_errors, run_correction
from agents.schemas import (
    CatalogItem,
    CorrectionTurn,
    Scene,
    SimulateRequest,
    SimulateResponse,
    SynopsisResponse,
    TEMP_LIMIT_C,
)
from agents.spatial_renderer import build_fallback_result
from core.simulation import simulate_scene
from core.synthetic_gen import catalog_payload, generate_scene

from api.throttling import (
    CATALOG_THROTTLE,
    HEALTH_DEEP_THROTTLE,
    HEALTH_THROTTLE,
    PREVIEW_THROTTLE,
    SIMULATE_THROTTLE,
)

router = Router(tags=["sim"])

__all__ = ["router"]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.get("/health", summary="Liveness probe + build metadata", throttle=HEALTH_THROTTLE)
def health(request) -> dict:
    return {
        "status": "ok",
        "service": "farbarchitect-ai",
        "version": "0.1.0",
        "python": platform.python_version(),
        "debug": os.environ.get("DJANGO_DEBUG", "false").lower() in {"1", "true", "yes"},
        "features": {
            "synthetic_gen": True,
            "fdm_solver": True,
            "vibration": True,
            "provider_router": True,
            "corrector": True,
        },
    }


@router.get(
    "/health/deep",
    summary="Verify NumPy + SciPy + provider env presence",
    throttle=HEALTH_DEEP_THROTTLE,
)
def health_deep(request) -> dict:
    import numpy
    import scipy

    return {
        "status": "ok",
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "pydantic": _safe_version("pydantic"),
        "httpx": _safe_version("httpx"),
        "providers": {
            "meta_key": bool(os.environ.get("META_LLAMA_KEY")),
            "fireworks_key": bool(os.environ.get("FIREWORKS_API_KEY")),
        },
    }


def _safe_version(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except ImportError:
        return "missing"
    return getattr(module, "__version__", "unknown")


# ---------------------------------------------------------------------------
# Bounded scene cache (deterministic scenes only — never user data)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=30)
def _cached_scene(seed: int) -> Scene:
    return generate_scene(seed)


def _random_seed() -> int:
    return int.from_bytes(os.urandom(3), "big")


def _resolve_scene(payload: SimulateRequest) -> tuple[int, Scene]:
    """Turn a request body into a concrete ``Scene``.

    Two producers, one shape:
      * ``equipment`` present -> the caller supplied a layout (scenario editor).
      * otherwise             -> deterministic synthetic scene for ``seed``.

    The corrector downstream cannot tell the difference, which is exactly the
    point: the agent's logic is identical whether a human built the layout or
    the generator did.
    """
    seed = payload.seed if payload.seed is not None else _random_seed()
    if payload.equipment:
        return seed, Scene(seed=seed, equipment=payload.equipment)
    # Copy so the LRU-cached Scene can never be mutated by a caller.
    return seed, _cached_scene(seed).model_copy(deep=True)


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------
@router.post(
    "/simulate",
    response=SimulateResponse,
    summary="Run the full simulation",
    throttle=SIMULATE_THROTTLE,
)
async def simulate_endpoint(request, payload: SimulateRequest) -> SimulateResponse:
    """Run physics -> agentic self-correction -> structured response.

    Fast path: if the initial layout is already clean (peak <= 26 C and zero
    hazards), we skip the LLM entirely (``providers_used == []``) and return
    immediately. That keeps runtimes low and preserves quota for tricky scenes.
    """
    seed, scene = _resolve_scene(payload)

    before = simulate_scene(scene)

    if before.peak_c <= TEMP_LIMIT_C and not before.hazards:
        # Clean layout: skip BOTH models. The spatial renderer still runs its
        # deterministic local path so the SPA always receives a canvas spec +
        # PNG preview — just without an outbound Meta call.
        spatial = build_fallback_result(scene.equipment)
        return SimulateResponse(
            seed=seed,
            providers_used=[],
            scene=scene,
            before=before,
            after=before,
            delta_t=0.0,
            corrections=[
                CorrectionTurn(
                    turn=1,
                    phase="evaluate",
                    error_codes=[],
                    summary="clean layout — no correction needed",
                    peak_c=before.peak_c,
                )
            ],
            final_layout=scene.equipment,
            spatial=spatial,
            spatial_source="FALLBACK",
        )

    outcome = await run_correction(scene)

    return SimulateResponse(
        seed=seed,
        providers_used=outcome.providers_used,
        scene=scene,
        before=before,
        after=outcome.after,
        delta_t=float(before.peak_c - outcome.after.peak_c),
        corrections=outcome.corrections,
        final_layout=outcome.final_equipment,
        spatial=outcome.spatial_result,
        spatial_source=outcome.spatial_source,
    )


# ---------------------------------------------------------------------------
# Scenario editor
# ---------------------------------------------------------------------------
@router.post(
    "/simulate/synopsis",
    response=SynopsisResponse,
    summary="Physics-only verdict for a user-built layout (no LLM)",
    throttle=PREVIEW_THROTTLE,
)
def simulation_synopsis_endpoint(request, payload: SimulateRequest) -> SynopsisResponse:
    """Run the FDM + vibration pipeline and report violations.

    This is the live-preview endpoint behind the scenario editor's
    ``ConstraintBadge``. It deliberately **never** touches a provider: the user
    is dragging machines around, and each drag must cost one FDM solve, not one
    LLM round-trip. ``providers_used`` is therefore always ``[]``.
    """
    _, scene = _resolve_scene(payload)
    sim = simulate_scene(scene)
    return SynopsisResponse(
        peak_c=sim.peak_c,
        peak_at=sim.peak_at,
        temp_min=sim.temp_min,
        temp_max=sim.temp_max,
        hazards=sim.hazards,
        error_codes=classify_errors(sim),
        equipment_count=len(scene.equipment),
        providers_used=[],
    )


@router.get(
    "/equipment/catalog",
    response=list[CatalogItem],
    summary="Draggable machine types for the scenario editor",
    throttle=CATALOG_THROTTLE,
)
def equipment_catalog_endpoint(request) -> list[CatalogItem]:
    """Expose the synthetic generator's catalog as JSON.

    Keeping this server-side means ``core/synthetic_gen.CATALOG`` stays the one
    source of truth: a machine dropped from the catalog behaves identically to
    one the generator placed, because both read the same numbers.
    """
    return [CatalogItem(**row) for row in catalog_payload()]
