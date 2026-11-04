"""Parse user-uploaded equipment lists (XLSX / CSV) into ``EquipmentDraft`` objects.

Real fabs keep their machine lists in spreadsheets with loosely-consistent
column names ("Tool", "Machine", "Power (W)", "Noise dBA", "Width (m)" ...).
This module turns one of those into the structured ``EquipmentUpload`` the
placement + simulation stages consume, without ever asking the model to guess
what a column means.

Design rules
------------
* **Data in, structure out; no coordinates.** The spreadsheet never tells us
  *where* a machine sits - only *what* it is. ``core/placement.py`` adds
  positions.
* **No model call.** Parsing is deterministic and auditable; a judge's file
  either parses or raises a precise error, never a plausible-but-wrong layout.
* **Aliases handled explicitly**, then a fuzzy fallback, then skip-with-warning.
  Unknown machine types never silently misclassify.
* **Best-effort, with receipts.** Always return an ``EquipmentUpload`` even on
  partial success; ``summary.warnings`` explains what was skipped and why.

Layout assumptions
------------------
Tolerates, in one file: blank/title rows above the table, an optional "Room"
block (any row containing "room"/"floor"/"cleanroom" and two numbers in metres),
a header row anywhere in the sheet, and the table itself.

Boundaries
----------
* Only ``.xlsx``/``.xlsm`` (openpyxl) and ``.csv``/``.txt`` (stdlib csv) are
  supported. DOCX/PDF are deliberately out of scope for the hackathon tier.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from agents.schemas import EquipmentDraft, EquipmentUpload, ParseSummary, RoomDraft

# Optional dependency: openpyxl ships in requirements.txt.
try:  # pragma: no cover - exercised implicitly by the XLSX tests
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - fallback path in restricted envs
    load_workbook = None  # type: ignore[assignment]

__all__ = [
    "MAX_FILE_BYTES",
    "M_PER_CELL",
    "MachineAlias",
    "UnsupportedFormat",
    "parse_upload",
]

#: 1 grid cell == 0.5 m. An 8x6-cell machine is therefore 4 m x 3 m.
M_PER_CELL = 0.5

#: Hard ceiling on uploads. The router enforces this before calling the parser.
MAX_FILE_BYTES = 5 * 1024 * 1024


class UnsupportedFormat(ValueError):
    """Raised when the uploaded file extension is not xlsx/csv."""


#: Canonical machine types - keep in lockstep with ``core/synthetic_gen.CATALOG``.
CANONICAL_TYPES = (
    "LITHO_SCANNER",
    "ETCH_CHAMBER",
    "CMP_POLISHER",
    "ION_IMPLANTER",
    "THERMAL_CHAMBER",
    "METROLOGY",
    "WAFER_CLEAN",
    "DIFFUSION_FURNACE",
)

#: Hand-curated aliases (lowercase, punctuation-stripped -> canonical type).
_ALIAS_MAP: dict[str, str] = {
    "litho": "LITHO_SCANNER",
    "lithography": "LITHO_SCANNER",
    "stepper": "LITHO_SCANNER",
    "scanner": "LITHO_SCANNER",
    "litho scanner": "LITHO_SCANNER",
    "asml": "LITHO_SCANNER",
    "etch": "ETCH_CHAMBER",
    "etcher": "ETCH_CHAMBER",
    "etch chamber": "ETCH_CHAMBER",
    "plasma": "ETCH_CHAMBER",
    "rie": "ETCH_CHAMBER",
    "cmp": "CMP_POLISHER",
    "polisher": "CMP_POLISHER",
    "cmp polisher": "CMP_POLISHER",
    "planarization": "CMP_POLISHER",
    "implant": "ION_IMPLANTER",
    "ion implant": "ION_IMPLANTER",
    "implanter": "ION_IMPLANTER",
    "ion implanter": "ION_IMPLANTER",
    "thermal": "THERMAL_CHAMBER",
    "thermal chamber": "THERMAL_CHAMBER",
    "oxidation": "THERMAL_CHAMBER",
    "anneal": "THERMAL_CHAMBER",
    "annealer": "THERMAL_CHAMBER",
    "rtp": "THERMAL_CHAMBER",
    "metro": "METROLOGY",
    "metrology": "METROLOGY",
    "inspection": "METROLOGY",
    "measurement": "METROLOGY",
    "clean": "WAFER_CLEAN",
    "wafer clean": "WAFER_CLEAN",
    "wet clean": "WAFER_CLEAN",
    "cleaning": "WAFER_CLEAN",
    "wet bench": "WAFER_CLEAN",
    "diffusion": "DIFFUSION_FURNACE",
    "furnace": "DIFFUSION_FURNACE",
    "diffusion furnace": "DIFFUSION_FURNACE",
    "lpcvd": "DIFFUSION_FURNACE",
}

_NON_WORD = re.compile(r"[^a-z0-9]+")


class MachineAlias:
    """Map a free-text machine name/type to a canonical catalog type."""

    @staticmethod
    def normalize(text: str) -> str:
        """Lowercase, collapse runs of punctuation/whitespace to single spaces."""
        return _NON_WORD.sub(" ", (text or "").strip().lower()).strip()

    @classmethod
    def resolve(cls, text: str) -> str | None:
        """Return the canonical type for ``text``, or ``None`` if unknown.

        Order: exact alias -> exact canonical -> substring alias -> substring
        canonical. Substring passes catch "dual CMP polisher #3"-style names.
        """
        key = cls.normalize(text)
        if not key:
            return None
        if key in _ALIAS_MAP:
            return _ALIAS_MAP[key]

        for canonical in CANONICAL_TYPES:
            if key == canonical.lower():
                return canonical

        for alias, canonical in _ALIAS_MAP.items():
            if alias in key:
                return canonical

        for canonical in CANONICAL_TYPES:
            lowered = canonical.lower().replace("_", " ")
            if lowered in key or all(w in key for w in lowered.split()):
                return canonical

        return None


#: Header aliases, lowercase. First alias that appears wins.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "machine id", "asset id", "tool id", "eq id"),
    "name": ("name", "machine", "equipment", "tool", "asset", "description", "label"),
    "type": (
        "type",
        "machine type",
        "equipment type",
        "category",
        "kind",
        "class",
        "process",
    ),
    "watts": (
        "heat watts",
        "heat_watts",
        "watts",
        "watt",
        "power",
        "power w",
        "power kw",
        "kw",
        "heat",
        "power draw",
    ),
    "db": (
        "vibration db",
        "vibration_db",
        "dba",
        "db a",
        "db(a)",
        "db",
        "noise",
        "noise dba",
        "vibration",
        "acoustic",
    ),
    "w": ("w", "width", "width m", "width (m)", "footprint w", "size w", "width cell"),
    "h": (
        "h",
        "height",
        "height m",
        "height (m)",
        "depth",
        "depth m",
        "footprint h",
        "size h",
        "size",
        "height cell",
    ),
}


def _normalise_header(cell: object) -> str:
    return re.sub(r"[^a-z0-9()]+", " ", str(cell or "").strip().lower()).strip()


def _header_matches(cell: str, alias: str) -> bool:
    """Strict header/alias comparison.

    Exact match, or the alias is a whole-word prefix with a trailing qualifier
    (``"width (m)"`` matches ``"width"``). Deliberately **not** a raw substring
    test: otherwise ``"w"`` would match ``"power (w)"`` and steal the watts
    column.
    """
    if not cell or not alias:
        return False
    if cell == alias:
        return True
    for sep in (" ", "(", "[", "/", "-"):
        if cell.startswith(alias + sep):
            return True
    return False


#: Field priority: watts/db claim their columns before the short ``w``/``h``
#: aliases get a chance to (``"power (w)"`` contains a standalone ``w`` token).
_FIELD_PRIORITY = ("id", "name", "type", "watts", "db", "w", "h")


def _detect_header_map(row: tuple[object, ...]) -> dict[str, int] | None:
    """Return ``{field: column_index}`` for ``row`` if it looks like a header.

    Columns are assigned first-come by :data:`_FIELD_PRIORITY` so a single
    column cannot be claimed by two fields.
    """
    normalised = [_normalise_header(c) for c in row]
    found: dict[str, int] = {}
    used: set[int] = set()
    for field in _FIELD_PRIORITY:
        aliases = _HEADER_ALIASES[field]
        for idx, cell in enumerate(normalised):
            if not cell or idx in used:
                continue
            if any(_header_matches(cell, alias) for alias in aliases):
                found[field] = idx
                used.add(idx)
                break
    # Stricter bar: a header row must carry name/id + watts + db.
    if ("name" in found or "id" in found) and "watts" in found and "db" in found:
        return found
    return None


_NUMBER = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _to_float(raw: object, default: float | None = None) -> float | None:
    """Extract the first number from a cell; ``None`` if nothing parseable."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    match = _NUMBER.search(str(raw))
    if match is None:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _metres_to_cells(raw: object, header: str) -> int | None:
    """Convert a raw width/height value to grid cells.

    Convention: user spreadsheets express footprint in **metres** (1 cell ==
    ``M_PER_CELL``). A header explicitly saying "cell" overrides to raw cells.
    Result is clamped to the schema's [1, 40] range.
    """
    value = _to_float(raw)
    if value is None or value <= 0:
        return None
    cells = int(round(value)) if "cell" in header else int(round(value / M_PER_CELL))
    return max(1, min(40, cells))


_ROOM_KEYWORDS = ("room", "floor", "cleanroom", "clean room", "fab", "area")


def _extract_room(rows: list[tuple[object, ...]]) -> RoomDraft:
    """Look for a row that names the room and carries two numbers (metres).

    Only *wholly* numeric cells count as dimensions — a label like "Bay 7" must
    not contribute the ``7`` to the room size.
    """
    for row in rows:
        cells = [c for c in row if c is not None and str(c).strip()]
        if not cells:
            continue
        joined = " ".join(str(c).strip().lower() for c in cells)
        if not any(kw in joined for kw in _ROOM_KEYWORDS):
            continue
        numbers = [n for n in (_whole_number(c) for c in cells) if n is not None and n > 1]
        if len(numbers) >= 2:
            name = next(
                (str(c).strip() for c in cells if _whole_number(c) is None), ""
            )
            return RoomDraft(
                name=name[:120],
                width_m=round(numbers[0], 2),
                depth_m=round(numbers[1], 2),
            )
    return RoomDraft()


_NUMERIC_ONLY = re.compile(r"^[-+]?[0-9]*\.?[0-9]+$")


def _whole_number(cell: object) -> float | None:
    """Return the cell's value only if the *entire* cell is a number."""
    if isinstance(cell, (int, float)):
        return float(cell)
    text = str(cell).strip()
    if _NUMERIC_ONLY.match(text):
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _make_id(name: str, row_index: int) -> str:
    """Stable, filename-safe id from a name + row number."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()[:48]
    return f"{slug or 'MACHINE'}-{row_index:03d}"


def _row_to_draft(
    row: tuple[object, ...], header: dict[str, int], *, row_index: int
) -> tuple[EquipmentDraft | None, str | None]:
    """Convert one data row to a draft. Returns ``(draft | None, warning | None)``."""

    def cell(field: str) -> object:
        idx = header.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    name_raw = cell("name") or cell("id") or cell("type")
    if name_raw is None or not str(name_raw).strip():
        return None, None  # silently skip blank rows

    raw_name = str(name_raw).strip()[:120]
    resolved_type = MachineAlias.resolve(
        str(cell("type") or "")
    ) or MachineAlias.resolve(raw_name)

    watts = _to_float(cell("watts"))
    db = _to_float(cell("db"))
    if watts is None or db is None:
        return None, f"row {row_index}: '{raw_name}' missing watts or dB - skipped"

    w_cells = _metres_to_cells(cell("w"), "w")
    h_cells = _metres_to_cells(cell("h"), "h")
    warning: str | None = None
    if w_cells is None and h_cells is None:
        w_cells = h_cells = 3
        warning = (
            f"row {row_index}: '{raw_name}' has no footprint - defaulting to 3x3 cells"
        )
    elif w_cells is None:
        w_cells = h_cells
    elif h_cells is None:
        h_cells = w_cells

    draft = EquipmentDraft(
        id=_make_id(raw_name, row_index),
        name=raw_name,
        machine_type=resolved_type or "",
        w=w_cells,  # type: ignore[arg-type]
        h=h_cells,  # type: ignore[arg-type]
        heat_watts=min(max(watts, 1.0), 5000.0),
        vibration_db=min(max(db, 30.0), 120.0),
        source_row=row_index,
    )
    return draft, warning


def _rows_from_xlsx(file_bytes: bytes) -> list[tuple[object, ...]]:
    if load_workbook is None:  # pragma: no cover - requirements guarantee it
        raise UnsupportedFormat("openpyxl is not installed on this deployment")
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    try:
        rows: list[tuple[object, ...]] = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                rows.append(tuple(row))
        return rows
    finally:
        wb.close()  # type: ignore[attr-defined]


def _rows_from_csv(file_bytes: bytes) -> list[tuple[object, ...]]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    return [tuple(row) for row in csv.reader(io.StringIO(text))]


def _detect_kind(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return "xlsx"
    if suffix in (".csv", ".txt"):
        return "csv"
    raise UnsupportedFormat(
        f"unsupported file type {suffix or '(none)'} - expected .xlsx, .xlsm, or .csv"
    )


def parse_upload(file_bytes: bytes, filename: str) -> EquipmentUpload:
    """Parse one uploaded spec file into an :class:`EquipmentUpload`."""
    if len(file_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"file is {len(file_bytes)} bytes; limit is {MAX_FILE_BYTES}")

    kind = _detect_kind(filename)
    rows = _rows_from_xlsx(file_bytes) if kind == "xlsx" else _rows_from_csv(file_bytes)

    summary = ParseSummary(rows_total=len(rows))
    room = _extract_room(rows)

    header: dict[str, int] | None = None
    header_idx = -1
    for idx, row in enumerate(rows):
        candidate = _detect_header_map(row)
        if candidate is not None:
            header = candidate
            header_idx = idx
            break

    if header is None:
        summary.warnings.append(
            "could not find a header row - expected columns for name, watts and dB"
        )
        summary.rows_skipped = len(rows)
        return EquipmentUpload(
            source_filename=filename,
            source_kind=kind,  # type: ignore[arg-type]
            room=room,
            drafts=[],
            summary=summary,
        )

    drafts: list[EquipmentDraft] = []
    skipped = 0
    recognized: set[str] = set()
    unrecognized: set[str] = set()
    for offset, row in enumerate(rows[header_idx + 1 :]):
        if not any(c is not None and str(c).strip() for c in row):
            break
        draft_row = header_idx + 2 + offset  # 1-based, past the header
        draft, warning = _row_to_draft(row, header, row_index=draft_row)
        if warning:
            summary.warnings.append(warning)
        if draft is None:
            skipped += 1
            continue
        drafts.append(draft)
        if draft.machine_type:
            recognized.add(draft.machine_type)
        else:
            unrecognized.add(draft.name)

    summary.rows_parsed = len(drafts)
    summary.rows_skipped = skipped
    summary.types_recognized = sorted(recognized)
    summary.types_unrecognized = sorted(unrecognized)

    return EquipmentUpload(
        source_filename=filename,
        source_kind=kind,  # type: ignore[arg-type]
        room=room,
        drafts=drafts,
        summary=summary,
    )


