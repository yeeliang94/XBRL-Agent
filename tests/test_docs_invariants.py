"""Step 13 of docs/PLAN-NOTES-RICH-EDITOR.md — grep-style invariants on
the project docs.

These guards are cheap: they assert that specific load-bearing phrases
survive in the docs so a future doc refactor doesn't silently erase the
HTML-editor contract that the notes pipeline now depends on.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_notes_pipeline_doc_mentions_html_contract() -> None:
    doc = (REPO_ROOT / "docs" / "NOTES-PIPELINE.md").read_text(encoding="utf-8")
    # HTML is the canonical emit format — prompts require it and the
    # writer enforces a rendered-char cap against it.
    assert "HTML" in doc
    # notes_cells is the DB-backed per-cell payload table introduced in
    # Phase 1/2. The editor reads and writes it; the download path
    # overlays it onto the xlsx at stream time.
    assert "notes_cells" in doc


def test_claude_md_has_notes_html_gotcha() -> None:
    doc = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    # The gotcha lives under the "Load-Bearing Invariants" section and
    # advertises the HTML → DB → editor contract.
    assert "notes_cells" in doc
    # Emphasise the clobber-on-rerun invariant — it's the single most
    # surprising behaviour for a future contributor.
    assert "clobber" in doc.lower() or "clobbers" in doc.lower()


def test_adr_001_records_db_canonical_decision() -> None:
    """Peer-review #13: architectural decision recorded as an ADR so a
    future reader can find the *why* without chasing plan files."""
    adr = REPO_ROOT / "docs" / "ADR-001-notes-db-canonical.md"
    assert adr.exists(), "ADR-001 missing — record the DB-canonical decision"
    content = adr.read_text(encoding="utf-8")
    # Load-bearing phrases: the decision itself and the two alternatives
    # that were weighed against it.
    assert "DB as canonical" in content
    assert "xlsx" in content.lower()
    assert "Consequences" in content
