"""Tests for the equipment-list upload parser (XLSX + CSV)."""

from __future__ import annotations

import io

import pytest

from core.equipment_parser import (
    MAX_FILE_BYTES,
    MachineAlias,
    UnsupportedFormat,
    parse_upload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _xlsx(rows: list[list[object]]) -> bytes:
    """Build an in-memory .xlsx with one sheet from ``rows``."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CSV_FAB = (
    b"Cleanroom: Room A, 30, 20\n"
    b"\n"
    b"name,type,power (W),noise (dB),width (m),height (m)\n"
    b"ASML Stepper 1980,stepper,380,92,4,3\n"
    b"LAM Etcher,etch,220,78,2.5,2.5\n"
    b"CMP Polisher 3,cmp,1800,96,3,2\n"
    b"KLA Metrology,inspection,80,40,1.5,1.5\n"
)


# ---------------------------------------------------------------------------
# MachineAlias
# ---------------------------------------------------------------------------
def test_alias_resolves_exact_and_fuzzy() -> None:
    assert MachineAlias.resolve("stepper") == "LITHO_SCANNER"
    assert MachineAlias.resolve("ASML Stepper 1980") == "LITHO_SCANNER"
    assert MachineAlias.resolve("dual CMP polisher #3") == "CMP_POLISHER"
    assert MachineAlias.resolve("Diffusion Furnace") == "DIFFUSION_FURNACE"
    assert MachineAlias.resolve("wet bench") == "WAFER_CLEAN"


def test_alias_returns_none_for_unknown() -> None:
    assert MachineAlias.resolve("mystery gadget 5000") is None
    assert MachineAlias.resolve("") is None


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------
def test_parse_csv_extracts_room_and_drafts() -> None:
    upload = parse_upload(CSV_FAB, "fab.csv")

    assert upload.source_kind == "csv"
    assert upload.room.width_m == 30.0
    assert upload.room.depth_m == 20.0
    assert upload.summary.rows_parsed == 4
    assert upload.summary.rows_skipped == 0
    assert "LITHO_SCANNER" in upload.summary.types_recognized
    assert upload.summary.types_unrecognized == []

    # 4 m x 3 m -> 8 x 6 cells (1 cell == 0.5 m).
    stepper = next(d for d in upload.drafts if "Stepper" in d.name)
    assert stepper.machine_type == "LITHO_SCANNER"
    assert (stepper.w, stepper.h) == (8, 6)
    assert stepper.heat_watts == 380.0
    assert stepper.vibration_db == 92.0


def test_parse_csv_header_aliases() -> None:
    """Alternate header spellings still resolve to the same columns."""
    raw = b"tool,category,watts,dba,width,height\nReactor A,etch,250,80,2,2\n"
    upload = parse_upload(raw, "alt.csv")
    assert upload.summary.rows_parsed == 1
    assert upload.drafts[0].machine_type == "ETCH_CHAMBER"
    assert (upload.drafts[0].w, upload.drafts[0].h) == (4, 4)


def test_parse_csv_skips_rows_missing_watts_or_db() -> None:
    raw = (
        b"name,watts,db\n"
        b"Good,100,60\n"
        b"Missing dB,120,\n"
        b"Good2,200,70\n"
    )
    upload = parse_upload(raw, "partial.csv")
    assert upload.summary.rows_parsed == 2
    assert any("missing watts or dB" in w for w in upload.summary.warnings)


def test_parse_csv_without_header_reports_warning() -> None:
    raw = b"just,some,random,text\nfoo,bar,baz,qux\n"
    upload = parse_upload(raw, "nope.csv")
    assert upload.drafts == []
    assert any("could not find a header row" in w for w in upload.summary.warnings)


def test_unrecognized_type_is_surfaced_and_defaults_footprint() -> None:
    raw = b"name,watt,dba\nMystery Gadget 5000,150,55\n"
    upload = parse_upload(raw, "mystery.csv")
    assert upload.summary.rows_parsed == 1
    draft = upload.drafts[0]
    assert draft.machine_type == ""
    assert "Mystery Gadget 5000" in upload.summary.types_unrecognized
    assert (draft.w, draft.h) == (3, 3)
    assert any("no footprint" in w for w in upload.summary.warnings)


def test_footprint_values_are_clamped_to_schema_range() -> None:
    raw = (
        b"name,watts,db,width,height\n"
        b"Huge Machine,500,80,40,40\n"  # 40 m -> 80 cells -> clamped to 40
    )
    upload = parse_upload(raw, "huge.csv")
    assert upload.drafts[0].w == 40
    assert upload.drafts[0].h == 40


# ---------------------------------------------------------------------------
# XLSX parsing
# ---------------------------------------------------------------------------
def test_parse_xlsx_round_trips() -> None:
    data = _xlsx(
        [
            ["Cleanroom: Bay 7", 40, 25],
            [],
            ["name", "type", "power (W)", "noise (dB)", "width (m)", "height (m)"],
            ["ASML Stepper", "stepper", 380, 92, 4, 3],
            ["Ion Implanter", "implant", 340, 88, 3.5, 3.5],
        ]
    )
    upload = parse_upload(data, "bay7.xlsx")

    assert upload.source_kind == "xlsx"
    assert upload.room.width_m == 40.0
    assert upload.room.depth_m == 25.0
    assert upload.summary.rows_parsed == 2
    types = {d.machine_type for d in upload.drafts}
    assert types == {"LITHO_SCANNER", "ION_IMPLANTER"}


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------
def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedFormat):
        parse_upload(b"whatever", "sheet.pdf")


def test_empty_file_returns_empty_upload() -> None:
    upload = parse_upload(b"", "empty.csv")
    assert upload.drafts == []
    assert upload.summary.rows_parsed == 0


def test_oversized_payload_raises() -> None:
    big = b"x" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError):
        parse_upload(big, "big.csv")


def test_parse_committed_sample_fixture() -> None:
    """The committed ``tests/fixtures/sample_fab.xlsx`` parses cleanly.

    Guards against the sample drifting out of sync with the parser and gives the
    frontend/README a known-good file to demo with.
    """
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "sample_fab.xlsx"
    upload = parse_upload(path.read_bytes(), path.name)

    assert upload.source_kind == "xlsx"
    assert upload.summary.rows_parsed == 8
    assert upload.summary.rows_skipped == 0
    assert upload.summary.types_unrecognized == []
    assert upload.room.width_m == 30.0
    assert upload.room.depth_m == 20.0
    # Every canonical type present.
    assert len(upload.summary.types_recognized) == 8


def test_parse_stops_at_first_blank_row() -> None:
    raw = b"name,watts,db\nA,100,60\n\nB,200,70\n"
    upload = parse_upload(raw, "gap.csv")
    assert upload.summary.rows_parsed == 1
    assert upload.drafts[0].name == "A"