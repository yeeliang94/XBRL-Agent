"""Phase 5 step 5.2 — SOCIE matrix parser branch.

SOCIE no longer raises UnsupportedSchemaShape. The parser emits a
matrix-shaped tree: every (movement-row, equity-component-column)
intersection in the FIRST block becomes a MATRIX_CELL concept carrying
`matrix_col` (the component column letter), plus per-(period, entity_scope)
render targets that map the canonical block to every physical block.

Four geometries are covered:
  * MFRS Company  — 23 component cols (B..X) × 2 period blocks
  * MFRS Group    — 23 component cols × 4 period/scope blocks
  * MPERS Company — single value col (B=CY, C=PY) × 1 block
  * MPERS Group   — single value col × 4 period/scope blocks
"""
from __future__ import annotations

from pathlib import Path

import pytest

from concept_model import parser as cp

_ROOT = Path(__file__).resolve().parent.parent
_MFRS = _ROOT / "XBRL-template-MFRS"
_MPERS = _ROOT / "XBRL-template-MPERS"

SOCIE_FIXTURES = {
    "mfrs_company": _MFRS / "Company" / "09-SOCIE.xlsx",
    "mfrs_group": _MFRS / "Group" / "09-SOCIE.xlsx",
    "mpers_company": _MPERS / "Company" / "09-SOCIE.xlsx",
    "mpers_group": _MPERS / "Group" / "09-SOCIE.xlsx",
}


@pytest.mark.parametrize("name", sorted(SOCIE_FIXTURES))
def test_socie_parses_without_raising(name: str) -> None:
    path = SOCIE_FIXTURES[name]
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    tree = cp.parse_template(str(path))
    assert tree.shape == "matrix"
    assert any(n.kind == "MATRIX_CELL" for n in tree.concepts)


def test_mfrs_company_emits_full_component_columns() -> None:
    path = SOCIE_FIXTURES["mfrs_company"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    cols = {
        n.render_key.get("matrix_col")
        for n in tree.concepts
        if n.kind == "MATRIX_CELL"
    }
    # B..X = 23 equity-component columns from row 2 headers.
    assert "B" in cols and "X" in cols
    assert len(cols) == 23


def test_mfrs_company_profit_row_has_matrix_cells() -> None:
    path = SOCIE_FIXTURES["mfrs_company"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    # Row 11 in block 1 is "*Profit (loss)".
    profit_cells = [
        n for n in tree.concepts
        if n.kind == "MATRIX_CELL" and n.render_key.get("row") == 11
    ]
    assert profit_cells, "no MATRIX_CELL at row 11"
    assert all("profit" in n.canonical_label.lower() for n in profit_cells)
    # Each cell carries render targets — block1 (CY) + block2 (PY).
    one = next(n for n in profit_cells if n.render_key.get("matrix_col") == "B")
    periods = {(t["period"], t["entity_scope"]) for t in one.render_key["targets"]}
    assert periods == {("CY", "Company"), ("PY", "Company")}


def test_mfrs_company_formula_cell_keeps_edges() -> None:
    path = SOCIE_FIXTURES["mfrs_company"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    # Row 8 col B = "=B6+B7" (Equity at beginning, restated).
    cell = next(
        (n for n in tree.concepts
         if n.kind == "MATRIX_CELL"
         and n.render_key.get("row") == 8
         and n.render_key.get("matrix_col") == "B"),
        None,
    )
    assert cell is not None
    assert cell.edges, "formula cell should carry dependency edges"


def test_mpers_company_single_value_column() -> None:
    path = SOCIE_FIXTURES["mpers_company"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    cols = {
        n.render_key.get("matrix_col")
        for n in tree.concepts
        if n.kind == "MATRIX_CELL"
    }
    assert cols == {"B"}
    # Period maps to a column (B=CY, C=PY) within the single block.
    cell = next(n for n in tree.concepts if n.kind == "MATRIX_CELL")
    targets = {(t["period"], t["col"]) for t in cell.render_key["targets"]}
    assert ("CY", "B") in targets
    assert ("PY", "C") in targets


def test_mpers_group_four_blocks_map_period_scope() -> None:
    path = SOCIE_FIXTURES["mpers_group"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    cell = next(
        n for n in tree.concepts
        if n.kind == "MATRIX_CELL" and n.render_key.get("row") == 11
    )
    targets = {(t["period"], t["entity_scope"]) for t in cell.render_key["targets"]}
    assert targets == {
        ("CY", "Group"), ("PY", "Group"),
        ("CY", "Company"), ("PY", "Company"),
    }
    # The Company-CY block starts at row 54 → row 11 maps to 11+(54-6)=59.
    co_cy = next(
        t for t in cell.render_key["targets"]
        if t["period"] == "CY" and t["entity_scope"] == "Company"
    )
    assert co_cy["row"] == 59


def test_cli_all_runs_without_parsing_archive_dirs() -> None:
    """Peer-review: `--all` must not parse archive-*/snapshot-*/backup-*
    workbooks as live templates (they'd overwrite the real template's
    JSON output under the same template_id). The repo currently carries
    an `archive-2026-04-28-*` dir under XBRL-template-MFRS/; the batch
    walker must skip it. We assert the skip predicate covers all three
    non-authoritative prefixes."""
    from pathlib import Path as _P

    sample = _P("XBRL-template-MFRS/archive-2026-04-28-pre-linkbase-regeneration/"
                "Company/09-SOCIE.xlsx")
    skip = any(
        part.startswith(("backup", "archive", "snapshot"))
        for part in sample.parts
    )
    assert skip, "archive dir not skipped by the --all walker"

    # backup-originals/ and snapshot-*/ are also covered.
    assert any(
        p.startswith(("backup", "archive", "snapshot"))
        for p in _P("XBRL-template-MFRS/backup-originals/x.xlsx").parts
    )
    assert any(
        p.startswith(("backup", "archive", "snapshot"))
        for p in _P("XBRL-template-MFRS/snapshot-2025/x.xlsx").parts
    )
    # A live template is NOT skipped.
    assert not any(
        p.startswith(("backup", "archive", "snapshot"))
        for p in _P("XBRL-template-MFRS/Company/09-SOCIE.xlsx").parts
    )


def test_to_json_round_trips_shape_and_matrix_col() -> None:
    path = SOCIE_FIXTURES["mpers_company"]
    if not path.exists():
        pytest.skip("fixture missing")
    tree = cp.parse_template(str(path))
    payload = tree.to_json()
    assert payload["shape"] == "matrix"
    mx = [c for c in payload["concepts"] if c["kind"] == "MATRIX_CELL"]
    assert mx and "matrix_col" in mx[0]["render_key"]
    assert "targets" in mx[0]["render_key"]
