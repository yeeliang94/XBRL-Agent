"""Phase 2 — reviewer-agent versioning core.

Pins the three pure-backend helpers in ``concept_model/versioning.py``:
``snapshot_facts`` (Step 2), ``revert_to_original`` (Step 3), and
``compute_review_diff`` (Step 4).
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from concept_model.versioning import (
    snapshot_facts,
    ensure_snapshot,
    revert_to_original,
    compute_review_diff,
    has_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures: a minimal canonical model — a COMPUTED parent over two LEAVES.
# ---------------------------------------------------------------------------

_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"
LEAF2 = "00000000-0000-0000-0000-0000000000b2"


def _seed(tmp_path):
    """Create a DB with one run, a parent+2-leaf concept tree, and edges."""
    db = tmp_path / "rev.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-29T00:00:00Z', 'x.pdf', 'completed')"
        )
        run_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES (?, 'x.xlsx', 'linear')",
            (_TEMPLATE,),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'COMPUTED', 'Total assets', 'SOFP', 10, 'B')",
            (PARENT, _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')",
            (LEAF1, _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', 'Receivables', 'SOFP', 6, 'B')",
            (LEAF2, _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
            "VALUES (?, ?, 1.0)",
            (PARENT, LEAF1),
        )
        conn.execute(
            "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
            "VALUES (?, ?, 1.0)",
            (PARENT, LEAF2),
        )
        conn.commit()
    finally:
        conn.close()
    return db, run_id


def _write_fact(db, run_id, uuid, value, *, source="extraction",
                evidence=None, actor="agent", value_status="observed",
                children_status=None):
    from concept_model.facts_api import write_fact, FactWrite
    write_fact(db, run_id, FactWrite(
        concept_uuid=uuid, period="CY", entity_scope="Company",
        value=value, value_status=value_status,
        children_status=children_status, source=source, evidence=evidence,
        actor=actor,
    ))


def _fact_value(db, run_id, uuid):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id = ? "
            "AND concept_uuid = ? AND period = 'CY' AND entity_scope = 'Company'",
            (run_id, uuid),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 2 — snapshot_facts
# ---------------------------------------------------------------------------


def test_snapshot_copies_all_facts(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)

    n = snapshot_facts(db, run_id)
    assert n == 2
    assert has_snapshot(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        rows = dict(conn.execute(
            "SELECT concept_uuid, value FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchall())
    finally:
        conn.close()
    assert rows == {LEAF1: 100.0, LEAF2: 50.0}


def test_snapshot_overwrites_prior(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    snapshot_facts(db, run_id)
    # A second snapshot replaces the first wholesale.
    _write_fact(db, run_id, LEAF1, 999.0)
    _write_fact(db, run_id, LEAF2, 1.0)
    n = snapshot_facts(db, run_id)
    assert n == 2
    conn = sqlite3.connect(str(db))
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        leaf1 = conn.execute(
            "SELECT value FROM run_fact_snapshots WHERE run_id = ? "
            "AND concept_uuid = ?", (run_id, LEAF1),
        ).fetchone()[0]
    finally:
        conn.close()
    assert cnt == 2 and leaf1 == 999.0


# ---------------------------------------------------------------------------
# Step 3 — revert_to_original  (Success Criterion #1)
# ---------------------------------------------------------------------------


def test_revert_restores_every_fact_and_recomputes(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)
    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(db, run_id)
    assert _fact_value(db, run_id, PARENT) == 150.0

    snapshot_facts(db, run_id)

    # Mutate several facts as a reviewer would.
    _write_fact(db, run_id, LEAF1, 200.0, source="reviewer", actor="reviewer")
    _write_fact(db, run_id, LEAF2, 80.0, source="reviewer", actor="reviewer")
    recompute_after_turn(db, run_id)
    assert _fact_value(db, run_id, PARENT) == 280.0

    out = revert_to_original(db, run_id)
    assert out["reverted"] is True
    assert _fact_value(db, run_id, LEAF1) == 100.0
    assert _fact_value(db, run_id, LEAF2) == 50.0
    # Cascade re-ran: the parent total reflects the restored leaves.
    assert _fact_value(db, run_id, PARENT) == 150.0


def test_revert_removes_reviewer_added_facts(tmp_path):
    """A fact the reviewer ADDED (absent in the snapshot) disappears on revert."""
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    snapshot_facts(db, run_id)
    # Reviewer adds LEAF2 which wasn't in the original.
    _write_fact(db, run_id, LEAF2, 50.0, actor="reviewer")
    revert_to_original(db, run_id)
    assert _fact_value(db, run_id, LEAF2) is None


def test_revert_dismisses_open_flags(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    snapshot_facts(db, run_id)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status) "
        "VALUES (?, 'stuck', 'x', 'open')", (run_id,),
    )
    conn.commit()
    conn.close()
    revert_to_original(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        status = conn.execute(
            "SELECT status FROM reviewer_flags WHERE run_id = ?", (run_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "dismissed"


def test_revert_without_snapshot_is_safe_noop(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    out = revert_to_original(db, run_id)
    assert out["reverted"] is False
    # Live facts untouched.
    assert _fact_value(db, run_id, LEAF1) == 100.0


# ---------------------------------------------------------------------------
# Item 11 — cascade failure after revert is reported, not swallowed
# ---------------------------------------------------------------------------


def test_revert_surfaces_cascade_failure(tmp_path, monkeypatch):
    """A post-restore recompute crash must surface (cascade_ok=false +
    cascade_error) while the facts ARE still restored — never a silent
    stale-totals window (item 11)."""
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)
    snapshot_facts(db, run_id)
    _write_fact(db, run_id, LEAF1, 200.0, source="reviewer", actor="reviewer")

    # Force the recompute to blow up (revert imports it at call time).
    import concept_model.cascade as cascade_mod

    def _boom(*a, **k):
        raise RuntimeError("cascade exploded")

    monkeypatch.setattr(cascade_mod, "recompute_after_turn", _boom)

    out = revert_to_original(db, run_id)
    assert out["reverted"] is True            # restore committed
    assert out["cascade_ok"] is False
    assert out["recomputed"] is False         # legacy mirror field
    assert "cascade exploded" in (out["cascade_error"] or "")
    # The leaf is back to its original value despite the cascade failure.
    assert _fact_value(db, run_id, LEAF1) == 100.0


def test_revert_cascade_ok_true_on_clean_recompute(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    snapshot_facts(db, run_id)
    _write_fact(db, run_id, LEAF1, 200.0, actor="reviewer")
    out = revert_to_original(db, run_id)
    assert out["cascade_ok"] is True
    assert out["cascade_error"] is None


# ---------------------------------------------------------------------------
# Item 13 — ensure_snapshot is atomic + create-if-absent only
# ---------------------------------------------------------------------------


def test_ensure_snapshot_creates_then_noops(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)
    assert ensure_snapshot(db, run_id) is True   # created
    assert has_snapshot(db, run_id)
    # A second call must NOT re-copy (would clobber the original extraction).
    _write_fact(db, run_id, LEAF1, 999.0)
    assert ensure_snapshot(db, run_id) is False   # no-op
    conn = sqlite3.connect(str(db))
    try:
        leaf1_snap = conn.execute(
            "SELECT value FROM run_fact_snapshots WHERE run_id = ? "
            "AND concept_uuid = ?", (run_id, LEAF1),
        ).fetchone()[0]
        cnt = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    # Still the ORIGINAL 100.0, exactly two rows — the no-op preserved it.
    assert leaf1_snap == 100.0 and cnt == 2


def test_ensure_snapshot_race_creates_exactly_one(tmp_path):
    """Two threads racing ensure_snapshot → exactly one snapshot row-set,
    never a double-write (item 13 — BEGIN IMMEDIATE serialises them)."""
    import threading

    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)

    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def _racer():
        barrier.wait()
        created = ensure_snapshot(db, run_id)
        with lock:
            results.append(created)

    threads = [threading.Thread(target=_racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one creator; the other no-ops.
    assert sorted(results) == [False, True]
    conn = sqlite3.connect(str(db))
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert cnt == 2   # two facts → two snapshot rows, not four


# ---------------------------------------------------------------------------
# Step 4 — compute_review_diff
# ---------------------------------------------------------------------------


def test_diff_returns_changed_cells_with_reason_and_grounding(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    _write_fact(db, run_id, LEAF2, 50.0)
    snapshot_facts(db, run_id)

    # Reviewer changes both leaves with reason + grounding.
    _write_fact(db, run_id, LEAF1, 120.0, source="misread the 0 as 8",
                evidence="page 12: Cash 120", actor="reviewer")
    _write_fact(db, run_id, LEAF2, 55.0, source="transposed digits",
                evidence="page 12: Receivables 55", actor="reviewer")

    diff = compute_review_diff(db, run_id)
    by_uuid = {d["concept_uuid"]: d for d in diff}
    assert set(by_uuid) == {LEAF1, LEAF2}

    d1 = by_uuid[LEAF1]
    assert d1["original"] == 100.0
    assert d1["current"] == 120.0
    assert d1["reason"] == "misread the 0 as 8"
    assert d1["grounding"] == "page 12: Cash 120"
    assert d1["actor"] == "reviewer"
    assert d1["sheet"] == "SOFP" and d1["row"] == 5
    assert d1["label"] == "Cash"


def test_diff_empty_when_no_changes(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    snapshot_facts(db, run_id)
    assert compute_review_diff(db, run_id) == []


def test_diff_empty_when_no_snapshot(tmp_path):
    db, run_id = _seed(tmp_path)
    _write_fact(db, run_id, LEAF1, 100.0)
    assert compute_review_diff(db, run_id) == []


def test_diff_prefers_target_coord_over_render(tmp_path):
    """Group / SOCIE facts route by concept_targets, so the diff cell must show
    the target coord (e.g. Group Company-scope col D), not the primary
    render_col B (peer-review P3)."""
    from concept_model.facts_api import write_fact, FactWrite

    db, run_id = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    # LEAF1 renders at col B primarily, but Group/CY routes to SOFP col D.
    conn.execute(
        "INSERT INTO concept_targets(concept_uuid, entity_scope, period, "
        "target_sheet, target_row, target_col) VALUES "
        "(?, 'Group', 'CY', 'SOFP', 5, 'D')", (LEAF1,))
    conn.commit()
    conn.close()

    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Group", value=100.0,
        value_status="observed", source="extraction", actor="agent"))
    snapshot_facts(db, run_id)
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Group", value=120.0,
        value_status="observed", source="fix", evidence="p3", actor="reviewer"))

    diff = compute_review_diff(db, run_id)
    row = next(d for d in diff
               if d["concept_uuid"] == LEAF1 and d["entity_scope"] == "Group")
    assert row["col"] == "D"        # target coord wins over render_col 'B'
    assert row["sheet"] == "SOFP" and row["row"] == 5
