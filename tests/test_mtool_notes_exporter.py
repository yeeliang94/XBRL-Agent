"""Tests for the notes bridge (mtool/notes_exporter.py): notes_cells -> a
footnotes fill doc that mtool.offline_fill.fill_footnotes consumes."""
import sqlite3
from pathlib import Path

import pytest

from db.schema import init_db
from mtool.notes_exporter import build_notes_fill_doc
from mtool.offline_fill import validate_notes_input


def _init_run(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-07-05T00:00:00Z", "x.pdf", "completed",
             "2026-07-05T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _add_note(db: Path, run_id: int, sheet: str, row: int, label: str,
              html: str):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, sheet, row, label, html, "2026-07-05T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def notes_db(tmp_path: Path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    run_id = _init_run(db)
    return db, run_id


def test_notes_become_footnote_writes(notes_db):
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-Listofnotes", 17,
              "Property, plant and equipment", "<h3>PPE</h3><p>policy</p>")
    _add_note(db, run_id, "Notes-CI", 12,
              "Corporate information", "<p>Acme Bhd is incorporated…</p>")
    # decorate=False isolates the bridge's row selection / labels / provenance
    # from the (separately tested) render decoration.
    doc = build_notes_fill_doc(db, run_id, decorate=False)

    assert doc["strict"] is True
    assert doc["meta"]["counts"]["notes"] == 2
    labels = {f["label"] for f in doc["footnotes"]}
    assert labels == {"Property, plant and equipment", "Corporate information"}
    ppe = next(f for f in doc["footnotes"]
               if f["label"] == "Property, plant and equipment")
    assert ppe["html"] == "<h3>PPE</h3><p>policy</p>"
    assert ppe["source_sheet"] == "Notes-Listofnotes"
    assert ppe["source_row"] == 17


def test_html_is_render_decorated_by_default(notes_db):
    """By default the emitted HTML carries the mTool-render inline styles so
    TX27 renders formatting instead of flat text (the reported bug)."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-Listofnotes", 17,
              "Property, plant and equipment",
              "<p>policy</p><table><tbody><tr><td>Land</td><td>1,500</td>"
              "</tr></tbody></table>")
    doc = build_notes_fill_doc(db, run_id)
    html = doc["footnotes"][0]["html"]
    assert "font-family: Arial" in html          # face injected
    assert "border: 1px solid" in html           # cell grid
    assert "text-align: right" in html           # numeric cell aligned
    # still valid fill-notes input after decoration
    assert validate_notes_input(doc) == []


def test_decorate_false_keeps_raw_html(notes_db):
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information",
              "<p>Acme</p>")
    doc = build_notes_fill_doc(db, run_id, decorate=False)
    assert doc["footnotes"][0]["html"] == "<p>Acme</p>"


def test_meta_labels_the_no_styling_diagnostic_fill(notes_db):
    """The doc carries an honest styling flag so the fill report / modal can
    say 'plain on purpose' instead of looking like a formatting bug."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    assert build_notes_fill_doc(db, run_id)["meta"][
        "styling_disabled"] is False
    assert build_notes_fill_doc(db, run_id, decorate=False)["meta"][
        "styling_disabled"] is True


def _wide_table(n_rows: int, n_cols: int = 10, user_style: bool = False) -> str:
    """All-numeric wide table; ``user_style`` puts a user-owned (WYSIWYG)
    border on the first cell, which makes the table compact-INELIGIBLE (the
    compact tier must not fight deliberate per-cell styling)."""
    cells = "".join("<td>1,234</td>" for _ in range(n_cols))
    first = ('<td style="border: 2px solid #000">1,234</td>'
             + "".join("<td>1,234</td>" for _ in range(n_cols - 1)))
    rows = []
    for i in range(n_rows):
        rows.append(f"<tr>{first if (user_style and i == 0) else cells}</tr>")
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def test_big_note_degrades_to_compact_tier(notes_db):
    """Full decoration over the limit but the compact decoration fits: the
    note keeps its VISIBLE formatting (table grid via the legacy attrs,
    numeric right-alignment) with per-cell boilerplate dropped."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Movement table",
              _wide_table(9, 40))                     # measured -> compact
    doc = build_notes_fill_doc(db, run_id)
    fn = doc["footnotes"][0]
    assert fn.get("format_tier") == "compact"
    assert "formatting_dropped" not in fn
    assert 'border="1"' in fn["html"]                 # table-level grid KEPT
    assert "text-align: right" in fn["html"]          # numeric alignment KEPT
    assert "border: 1px solid" not in fn["html"]      # per-cell boilerplate gone
    assert doc["meta"]["counts"]["formatting_compacted"] == 1
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
    assert len(wrap_footnote_html(fn["html"])) <= EXCEL_CELL_CHAR_LIMIT


def test_near_limit_note_degrades_to_lite_tier(notes_db):
    """Full is over the limit AND the table is compact-ineligible (a cell owns
    a user border, so compact keeps the full per-cell form and is over too) —
    the lighter 'lite' decoration is the first rung that fits: the note keeps
    borders/font/alignment (cosmetic props dropped)."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Movement table",
              _wide_table(9, 40, user_style=True))    # measured -> lite
    doc = build_notes_fill_doc(db, run_id)
    fn = doc["footnotes"][0]
    assert fn.get("format_tier") == "lite"
    assert "formatting_dropped" not in fn
    assert "border: 1px solid" in fn["html"]          # formatting KEPT
    assert "vertical-align: top" not in fn["html"]    # cosmetics dropped
    assert doc["meta"]["counts"]["formatting_reduced"] == 1
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
    assert len(wrap_footnote_html(fn["html"])) <= EXCEL_CELL_CHAR_LIMIT


def test_oversize_note_degrades_to_flat_content(notes_db):
    """Even compact + lite are over the limit but raw fits: emit FLAT (content
    preserved, flagged) rather than being skipped by the fill guard."""
    db, run_id = notes_db
    raw = _wide_table(30, 40)                           # measured -> flat
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Big movement table", raw)
    doc = build_notes_fill_doc(db, run_id)
    fn = doc["footnotes"][0]
    assert fn.get("format_tier") == "flat"
    assert fn.get("formatting_dropped") is True
    assert fn["html"] == raw                           # flat, undecorated
    assert doc["meta"]["counts"]["formatting_dropped"] == 1
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
    assert len(wrap_footnote_html(fn["html"])) <= EXCEL_CELL_CHAR_LIMIT  # fits


def test_too_big_even_flat_is_left_for_the_fill_guard(notes_db):
    """A note too large even unstyled is emitted raw with no formatting flag —
    the fill's hard guard skips + flags it as `oversize` (split the CONTENT)."""
    db, run_id = notes_db
    raw = _wide_table(240)                             # raw itself over limit
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Enormous table", raw)
    doc = build_notes_fill_doc(db, run_id)
    fn = doc["footnotes"][0]
    assert "format_tier" not in fn                     # not a formatting issue
    assert doc["meta"]["counts"]["formatting_dropped"] == 0
    assert doc["meta"]["counts"]["formatting_reduced"] == 0
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
    assert len(wrap_footnote_html(fn["html"])) > EXCEL_CELL_CHAR_LIMIT  # guard skips


def test_normal_note_stays_decorated(notes_db):
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information",
              "<table><tbody><tr><td>x</td></tr></tbody></table>")
    doc = build_notes_fill_doc(db, run_id)
    fn = doc["footnotes"][0]
    assert "format_tier" not in fn
    assert "border: 1px solid" in fn["html"]          # full decoration applied
    assert doc["meta"]["counts"]["formatting_dropped"] == 0
    assert doc["meta"]["counts"]["formatting_reduced"] == 0


def test_empty_and_unlabelled_notes_are_skipped_not_emitted(notes_db):
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    _add_note(db, run_id, "Notes-CI", 13, "Blank note", "   ")   # empty html
    _add_note(db, run_id, "Notes-CI", 14, "", "<p>orphan label</p>")  # no label
    doc = build_notes_fill_doc(db, run_id)

    assert doc["meta"]["counts"] == {
        "notes": 1, "skipped_empty": 1, "skipped_no_label": 1,
        "formatting_compacted": 0, "formatting_reduced": 0,
        "formatting_dropped": 0}
    assert [f["label"] for f in doc["footnotes"]] == ["Corporate information"]


def test_doc_is_valid_fill_notes_input(notes_db):
    """The doc must satisfy fill_footnotes' own input contract."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-Listofnotes", 17,
              "Property, plant and equipment", "<h3>PPE</h3>")
    doc = build_notes_fill_doc(db, run_id)
    assert validate_notes_input(doc) == []


def test_notes_are_scoped_to_the_run(notes_db):
    db, run_id = notes_db
    other = _init_run(db)
    _add_note(db, run_id, "Notes-CI", 12, "Mine", "<p>mine</p>")
    _add_note(db, other, "Notes-CI", 12, "Theirs", "<p>theirs</p>")
    doc = build_notes_fill_doc(db, run_id)
    assert [f["label"] for f in doc["footnotes"]] == ["Mine"]


def test_empty_run_yields_no_footnotes(notes_db):
    db, run_id = notes_db
    doc = build_notes_fill_doc(db, run_id)
    assert doc["footnotes"] == []
    assert doc["meta"]["counts"]["notes"] == 0
