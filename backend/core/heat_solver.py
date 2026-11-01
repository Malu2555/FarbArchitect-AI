"""2D Finite-Difference heat equation solver on the 100x100 cleanroom grid.

Model (non-dimensional form)
-----------------------------
    T^(n+1)_ij = T^n_ij
               + r * L(T^n)_ij                 # diffusion (5-point stencil)
               - lam * (T^n_ij - T_ambient)    # convective loss
               + Q_ij                          # Gaussian heat injection

Stability: explicit Euler on 2D heat requires ``r <= 0.25``. We default to
``r = 0.20`` for a comfortable margin. ``np.pad(..., mode="edge")`` implements
zero-flux (Neumann) boundaries without periodic wraparound artifacts.

All non-dimensional coefficients were chosen so that a ~400 W emitter alone
settles at ~35 degC (above the 26 degC corrective threshold) while two
overlapping 400 W emitters settle around ~45 degC (a strong trigger).
"""

from __future__ import annotations

import numpy as np

from agents.schemas import EquipmentItem, Hazard, SimulationResult

__all__ = [
    "AMBIENT_C",
    "GRID_SIZE",
    "SolverError",
    "encode_rle",
    "simulate_heat",
    "thermal_hazards",
]

GRID_SIZE = 100
AMBIENT_C = 20.0
TEMP_CAP_C = 45.0
R_COEF = 0.20  # stability margin: r <= 0.25
LAMBDA = 0.02  # convective loss per step
Q_PER_WATT = 8.0e-4  # tuning constant (see module docstring)
STEPS = 150
FINITE_CHECK_EVERY = 10


class SolverError(RuntimeError):
    """Raised when the numerical solution becomes non-finite."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail or code}")


def _gaussian_field(item: EquipmentItem, grid: int = GRID_SIZE) -> np.ndarray:
    """Normalized Gaussian contribution centred on the item footprint."""
    cx = item.x + item.w / 2.0
    cy = item.y + item.h / 2.0
    sigma = max(item.w, item.h) * 0.9
    yy, xx = np.mgrid[0:grid, 0:grid]
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    return np.exp(-d2 / (2.0 * sigma * sigma))


def _laplacian(field: np.ndarray) -> np.ndarray:
    """5-point Laplacian with zero-flux boundaries."""
    padded = np.pad(field, 1, mode="edge")
    return (
        padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
        - 4.0 * field
    )


def build_heat_source(equipment: list[EquipmentItem]) -> np.ndarray:
    """Sum of Gaussian injections from every machine, scaled by wattage."""
    Q = np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.float64)
    for item in equipment:
        Q += item.heat_watts * Q_PER_WATT * _gaussian_field(item)
    return Q


def simulate_heat(
    equipment: list[EquipmentItem],
    *,
    steps: int = STEPS,
    r: float = R_COEF,
    lam: float = LAMBDA,
) -> np.ndarray:
    """Return the final temperature field (degC) for the given equipment layout."""
    if not (0.0 < r <= 0.25):
        raise SolverError("E_FDM_UNSTABLE", f"r={r} outside (0, 0.25]")

    Q = build_heat_source(equipment)
    T = np.full((GRID_SIZE, GRID_SIZE), AMBIENT_C, dtype=np.float64)

    for step in range(steps):
        T = T + r * _laplacian(T) - lam * (T - AMBIENT_C) + Q

        if (step + 1) % FINITE_CHECK_EVERY == 0 and not np.isfinite(T).all():
            raise SolverError(
                "E_FDM_NAN",
                f"non-finite temperature at step {step + 1}; consider lowering r",
            )

    if not np.isfinite(T).all():
        raise SolverError("E_FDM_NAN", "non-finite final field")
    return np.clip(T, AMBIENT_C, TEMP_CAP_C)


def encode_rle(matrix: np.ndarray) -> str:
    """Run-length encode the matrix at 0.1 degC resolution (decidegrees)."""
    if matrix.size == 0:
        return ""
    flat = np.round(np.asarray(matrix, dtype=np.float64) * 10.0).astype(np.int32).ravel()
    change = np.flatnonzero(np.diff(flat) != 0) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [flat.size]))
    return ",".join(f"{int(flat[s])}:{int(e - s)}" for s, e in zip(starts, ends))


def decode_rle(rle: str, *, shape: tuple[int, int] = (GRID_SIZE, GRID_SIZE)) -> np.ndarray:
    """Inverse of `encode_rle` (also useful for tests + frontend reference)."""
    if not rle:
        return np.zeros(shape, dtype=np.float64)
    chunks: list[int] = []
    for token in rle.split(","):
        value, count = token.split(":")
        chunks.extend([int(value)] * int(count))
    return (np.asarray(chunks, dtype=np.float64) / 10.0).reshape(shape)


def thermal_hazards(
    field: np.ndarray,
    equipment: list[EquipmentItem],
    *,
    limit_c: float = 26.0,
) -> list[Hazard]:
    """One thermal hazard per machine whose footprint centre exceeds the limit."""
    out: list[Hazard] = []
    for item in equipment:
        cx = int(min(GRID_SIZE - 1, item.x + item.w / 2))
        cy = int(min(GRID_SIZE - 1, item.y + item.h / 2))
        t = float(field[cy, cx])
        if t > limit_c:
            out.append(
                Hazard(
                    type="thermal",
                    a_id=item.id,
                    a_center=(cx, cy),
                    threshold=limit_c,
                    measured=t,
                    severity="high" if t > limit_c + 6.0 else "medium",
                )
            )
    return out


def build_result(
    field: np.ndarray,
    equipment: list[EquipmentItem],
    *,
    extra_hazards: list[Hazard] | None = None,
) -> SimulationResult:
    """Compose a `SimulationResult` from a solved temperature field."""
    idx = np.unravel_index(int(np.argmax(field)), field.shape)
    hazards = list(extra_hazards or [])
    hazards.extend(thermal_hazards(field, equipment))
    return SimulationResult(
        peak_c=float(field.max()),
        peak_at=(int(idx[1]), int(idx[0])),  # (x=col, y=row)
        temp_min=float(field.min()),
        temp_max=float(field.max()),
        temp_grid_rle=encode_rle(field),
        hazards=hazards,
    )
