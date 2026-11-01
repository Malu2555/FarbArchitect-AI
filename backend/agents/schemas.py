"""Pydantic v2 contracts shared by the simulator, corrector and API.

These are the single source of truth for the ``/api/v1/simulate`` payload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CanvasProvenance",
    "CatalogItem",
    "CorrectionTurn",
    "EquipmentItem",
    "FootprintBox",
    "Hazard",
    "Scene",
    "SimulateRequest",
    "SimulateResponse",
    "SimulationResult",
    "SpatialResult",
    "SpatialSpec",
    "SynopsisResponse",
]

GRID_SIZE = 100
TEMP_LIMIT_C = 26.0


class EquipmentItem(BaseModel):
    """A single cleanroom machine footprint."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    x: int = Field(ge=0, lt=GRID_SIZE, description="left column (0-99)")
    y: int = Field(ge=0, lt=GRID_SIZE, description="top row (0-99)")
    w: int = Field(ge=1, le=40)
    h: int = Field(ge=1, le=40)
    heat_watts: float = Field(gt=0, le=5000)
    vibration_db: float = Field(ge=30, le=120)


class Scene(BaseModel):
    seed: int
    grid_size: int = GRID_SIZE
    equipment: list[EquipmentItem]


class Hazard(BaseModel):
    type: Literal["vibration", "thermal"]
    a_id: str
    b_id: str | None = None
    a_center: tuple[int, int]
    b_center: tuple[int, int] | None = None
    threshold: float
    measured: float
    severity: Literal["low", "medium", "high"]


class SimulationResult(BaseModel):
    peak_c: float
    peak_at: tuple[int, int]
    temp_min: float
    temp_max: float
    # RLE-encoded temperature field: "decidegrees:count,..." — keeps payload
    # sub-15 kB for a 100x100 mesh while preserving 0.1 °C resolution.
    temp_grid_rle: str
    hazards: list[Hazard] = []


class CorrectionTurn(BaseModel):
    turn: int = Field(ge=1, le=3)
    phase: Literal["evaluate", "correct", "verify", "error"]
    error_codes: list[str] = []
    summary: str = Field(max_length=400)
    peak_c: float | None = None


# ---------------------------------------------------------------------------
# Spatial rendering (Stage 2 of the dual-Muse pipeline)
# ---------------------------------------------------------------------------
class FootprintBox(BaseModel):
    """One canvas-ready rectangle produced by the Muse Image spatial renderer."""

    id: str
    origin: tuple[int, int]  # (x, y) top-left in grid cells
    size: tuple[int, int]  # (w, h) in grid cells
    label: str
    z_index: int = 0
    color_token: Literal["teal", "warn", "danger"] = "teal"


class CanvasProvenance(BaseModel):
    """Proof that the geometry maps cleanly onto the HTML5 canvas."""

    grid_size: int = GRID_SIZE
    cell_size_px: int = 5
    canvas_px: tuple[int, int] = (500, 500)
    no_overlap: bool = True
    no_clipping: bool = True
    source: Literal["MUSE_IMAGE", "FALLBACK"] = "FALLBACK"
    notes: list[str] = []


class SpatialSpec(BaseModel):
    """Structured layout the frontend canvas paints directly."""

    footprints: list[FootprintBox] = []
    canvas: CanvasProvenance = Field(default_factory=CanvasProvenance)


class SpatialResult(BaseModel):
    """Spatial renderer output: canvas-ready spec + a base64 PNG preview."""

    spatial_spec: SpatialSpec
    preview_png_data_url: str = ""


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int | None = Field(default=None, ge=0)
    equipment: list[EquipmentItem] | None = None


class SimulateResponse(BaseModel):
    seed: int
    providers_used: list[str] = []
    scene: Scene
    before: SimulationResult
    after: SimulationResult
    delta_t: float
    corrections: list[CorrectionTurn] = []
    final_layout: list[EquipmentItem]
    # Stage 2 of the dual-Muse pipeline: Muse Image output.
    spatial: SpatialResult | None = None
    spatial_source: Literal["MUSE_IMAGE", "FALLBACK"] = "FALLBACK"


# ---------------------------------------------------------------------------
# Scenario editor (physics-only preview + equipment catalog)
# ---------------------------------------------------------------------------
class SynopsisResponse(BaseModel):
    """Physics-only verdict for a user-built layout.

    Deliberately mirrors the fields the live ``ConstraintBadge`` needs and
    nothing more. ``providers_used`` is always empty: ``/simulate/synopsis``
    never calls an LLM, so a user dragging a machine cannot burn provider
    quota or sit through a 20 s round-trip.
    """

    peak_c: float
    peak_at: tuple[int, int]
    temp_min: float
    temp_max: float
    hazards: list[Hazard] = []
    error_codes: list[str] = []
    equipment_count: int = 0
    providers_used: list[str] = []


class CatalogItem(BaseModel):
    """One draggable machine type exposed to the scenario editor."""

    id: str
    name: str
    glyph: str = ""
    w: int
    h: int
    heat_watts: float
    vibration_db: float
