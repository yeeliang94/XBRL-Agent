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
        "formatting_dropped": 0, "source_styling_dropped": 0,
        "white_grid_dropped": 0}
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


# --- verbatim styling + the size ladder (2026-07-19) -----------------------
# Verbatim passthrough (gotcha #16) writes the SOURCE document's own per-cell
# styling into notes_cells. For a big Word table that makes `raw` itself
# exceed Excel's cell cap, which strands every ordinary rung: `compact` only
# slims DECORATOR-added styling (these cells own theirs) and `flat` == `raw`.
# Without the destyle retry a 100-row Word table went `oversize` and the fill
# guard skipped the note entirely — a filed plain note beats a missing one.

def _word_table(rows: int, cols: int = 6) -> str:
    cell = (
        '<td style="padding: 1px 5px; text-align: right; '
        'border-bottom: 1px solid #7F7F7F">1,595</td>'
    )
    body = "".join("<tr>" + cell * cols + "</tr>" for _ in range(rows))
    return f"<table>{body}</table>"


def test_small_verbatim_table_keeps_its_source_styling():
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html

    html, tier, destyled, _grid = _resolve_note_html(
        _word_table(10), NotesTableStyle(), True)
    assert tier == "full"
    assert destyled is False
    assert "padding: 1px 5px" in html


def test_oversized_verbatim_table_degrades_instead_of_being_skipped():
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html

    raw = _word_table(100)
    assert len(raw) > EXCEL_CELL_CHAR_LIMIT  # raw alone is over — no easy rung
    html, tier, destyled, _grid = _resolve_note_html(raw, NotesTableStyle(), True)
    assert tier != "oversize"
    assert len(wrap_footnote_html(html)) <= EXCEL_CELL_CHAR_LIMIT
    # Content survives even though the styling did not — and the loss is
    # REPORTED: a destyled note re-lands on an ordinary tier (here even
    # "full"), so without this flag the fill report would claim the Word
    # formatting arrived intact.
    assert destyled is True
    assert html.count("1,595") == 600


def test_very_large_verbatim_table_still_reports_oversize():
    """Destyling is not a licence to claim success on a note whose CONTENT is
    genuinely too big — that must still surface as `oversize` so the operator
    splits it."""
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html

    _html, tier, destyled, _grid = _resolve_note_html(
        _word_table(400), NotesTableStyle(), True)
    assert tier == "oversize"
    # Oversize emits the raw note untouched — nothing was dropped, so the
    # destyle flag must not fire alongside the skip.
    assert destyled is False


def test_strip_inline_styles_preserves_structure_and_text():
    from mtool.notes_decorate import strip_inline_styles

    out = strip_inline_styles(_word_table(2))
    assert "style=" not in out
    assert out.count("<td") == 12
    assert out.count("1,595") == 12


# --- white-grid fallback rung (run-76 follow-up, 2026-07-20) ----------------
# The run-76 white grid (`fill_white_grid`) costs ~27 chars per silent cell,
# landing on exactly the tables whose `compact` rung is inoperative (source-
# styled / border-none themes). Without a fallback, mid-size tables that used
# to file `full` dropped to `flat` — losing ALL formatting to a cosmetic
# accommodation. The ladder now retries full/compact/lite with the grid off
# (the exact pre-run-76 payload) before flat, and reports the drop.

def _borderless_word_table(rows: int, cols: int = 6) -> str:
    """A source-styled Word table whose cells state only padding — the run-75
    FINCO shape (silence is the look), i.e. maximum white-grid cost."""
    cell = '<td style="padding: 1px 0px">39,827</td>'
    body = "".join("<tr>" + cell * cols + "</tr>" for _ in range(rows))
    return f'<table data-source-styled="true">{body}</table>'


def test_white_grid_dropped_before_formatting_is_lost():
    """A source-styled table too big for any tier WITH the white grid must land
    on a decorated tier WITHOUT it (pre-run-76 payload) — never fall through
    to flat while a fitting decorated form exists. The drop is reported."""
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html

    html, tier, destyled, grid_dropped = _resolve_note_html(
        _borderless_word_table(65), NotesTableStyle(), True)
    assert tier in ("full", "compact", "lite")   # still decorated
    assert grid_dropped is True
    assert destyled is False                     # source styling intact…
    assert "padding: 1px 0px" in html
    assert "#ffffff" not in html                 # …only the white grid went
    assert len(wrap_footnote_html(html)) <= EXCEL_CELL_CHAR_LIMIT


def test_white_grid_kept_when_it_fits():
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html

    html, tier, _destyled, grid_dropped = _resolve_note_html(
        _borderless_word_table(10), NotesTableStyle(), True)
    assert tier == "full"
    assert grid_dropped is False
    assert "#ffffff" in html


def test_no_tier_regression_vs_pre_white_grid_ladder():
    """The invariant the fallback exists for: a note NEVER lands on a worse
    tier than the pre-run-76 ladder gave it (the fallback rungs ARE that
    ladder). Probed across the sizes that regressed in review."""
    from mtool.notes_decorate import NotesTableStyle, decorate_notes_html
    from mtool.notes_exporter import _resolve_note_html
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html

    rank = {"full": 0, "compact": 1, "lite": 2, "flat": 3, "oversize": 4}
    for rows in (45, 65, 80, 100):
        raw = _borderless_word_table(rows)
        _html, tier, _destyled, _grid = _resolve_note_html(
            raw, NotesTableStyle(), True)
        # Pre-run-76 equivalent: the same ladder with the white grid off.
        def fits(h):
            return len(wrap_footnote_html(h)) <= EXCEL_CELL_CHAR_LIMIT
        if fits(decorate_notes_html(raw, NotesTableStyle(),
                                    fill_white_grid=False)):
            old_tier = "full"
        elif fits(decorate_notes_html(raw, NotesTableStyle(), lite=True,
                                      fill_white_grid=False)):
            old_tier = "lite"
        elif fits(raw):
            old_tier = "flat"
        else:
            old_tier = "oversize"
        assert rank[tier] <= rank[old_tier], (rows, tier, old_tier)


def test_white_grid_drop_is_reported_in_the_fill_doc(notes_db):
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Small verbatim",
              _borderless_word_table(10))
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Mid verbatim",
              _borderless_word_table(65))
    doc = build_notes_fill_doc(db, run_id)

    assert doc["meta"]["counts"]["white_grid_dropped"] == 1
    mid = next(f for f in doc["footnotes"] if f["label"] == "Mid verbatim")
    assert mid.get("white_grid_dropped") is True
    small = next(f for f in doc["footnotes"] if f["label"] == "Small verbatim")
    assert "white_grid_dropped" not in small


def test_destyle_retry_does_not_repaint_the_white_grid():
    """strip_inline_styles drops the data-source-styled marker with the
    styles, so the destyle rescue rung re-decorates WITHOUT re-painting
    ~27 chars of white border per cell onto a table that has no styling
    left — the rung exists to recover bytes, not spend them."""
    from mtool.notes_decorate import NotesTableStyle
    from mtool.notes_exporter import _resolve_note_html

    html, tier, destyled, _grid = _resolve_note_html(
        _word_table(200), NotesTableStyle(), True)
    assert destyled is True
    assert tier != "oversize"
    assert "#ffffff" not in html


def test_destyled_note_is_reported_in_the_fill_doc(notes_db):
    """The fill report must say when a note's SOURCE (Word) styling was
    stripped for size — code review 2026-07-20: a destyled note re-lands on
    tier "full", which read as "formatting fully intact" while the operator's
    Word styling had silently become house style."""
    db, run_id = notes_db
    _add_note(db, run_id, "Notes-CI", 12, "Small verbatim", _word_table(10))
    _add_note(db, run_id, "Notes-Listofnotes", 17, "Big verbatim",
              _word_table(100))
    doc = build_notes_fill_doc(db, run_id)

    assert doc["meta"]["counts"]["source_styling_dropped"] == 1
    big = next(f for f in doc["footnotes"] if f["label"] == "Big verbatim")
    assert big.get("source_styling_dropped") is True
    small = next(f for f in doc["footnotes"] if f["label"] == "Small verbatim")
    assert "source_styling_dropped" not in small
