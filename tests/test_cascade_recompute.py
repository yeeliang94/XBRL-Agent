"""Phase 1 steps 1.9-1.10 — cascade recompute at turn boundary.

The cascade walks COMPUTED concepts in topological order (leaves first)
and writes their aggregated values into ``run_concept_facts``.  Two
short-circuits keep the algorithm honest:

* ``children_status='aggregate_only'`` on a parent makes the parent
  authoritative for the value at that node (the agent has told us the
  underlying breakdown isn't disclosed); the cascade does NOT
  overwrite it from leaves below.
* If the parent already carries an ``observed`` value AND some children
  are itemised with values that don't add up, the cascade records a
  ``partial_state`` conflict row carrying the residual.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.cascade import recompute_after_turn
from concept_model.importer import import_template
from concept_model.parser import parse_template
from db.schema import init_db


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def seeded_db(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "xbrl.db"
    init_db(db)

    tree = parse_template(str(FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db, jp)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "x.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return db, run_id


def _uuid_for_row(db: Path, sheet: str, row: int) -> str:
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = ? AND render_row = ?",
            (sheet, row),
        ).fetchone()
    finally:
        conn.close()
    assert r is not None, f"no concept at {sheet}!{row}"
    return r[0]


def _seed_fact(db: Path, run_id: int, concept_uuid: str, *,
               value: float, value_status: str = "observed",
               children_status: str | None = None) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts("
            "run_id, concept_uuid, period, entity_scope, value, "
            "value_status, children_status, source, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', ?, ?, ?, 'test', '2026-05-21Z')",
            (run_id, concept_uuid, value, value_status, children_status),
        )
        conn.commit()
    finally:
        conn.close()


def _value_of(db: Path, run_id: int, concept_uuid: str) -> float | None:
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id = ? "
            "AND concept_uuid = ? AND period = 'CY' AND "
            "entity_scope = 'Company'",
            (run_id, concept_uuid),
        ).fetchone()
    finally:
        conn.close()
    return r[0] if r else None


def test_cascade_recomputes_parent_after_leaf_writes(seeded_db) -> None:
    db, run_id = seeded_db
    # Pick a small subtree: SOFP-Sub-CuNonCu row 39 (`*Total property,
    # plant and equipment`) sums 6 leaves above it. We'll seed 3 leaves
    # and verify the sum.
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    # Children of row 39 — first 3 leaves with values; rest left None.
    # Find children via concept_edges.
    conn = sqlite3.connect(str(db))
    try:
        child_uuids = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(child_uuids) >= 3, "expected COMPUTED parent with >=3 edges"

    for i, c in enumerate(child_uuids[:3]):
        _seed_fact(db, run_id, c, value=100.0 * (i + 1))
    # Also seed the rest with 0 so the parent is fully determined.
    for c in child_uuids[3:]:
        _seed_fact(db, run_id, c, value=0.0)

    recompute_after_turn(db, run_id)

    assert _value_of(db, run_id, parent) == 600.0


def test_cascade_treats_blank_siblings_as_zero_after_leaf_write(
    seeded_db,
) -> None:
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    conn = sqlite3.connect(str(db))
    try:
        child_uuids = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(child_uuids) >= 2

    _seed_fact(db, run_id, child_uuids[0], value=100.0)
    # A deliberately cleared blank still behaves like an Excel blank/zero.
    _seed_fact(db, run_id, child_uuids[1], value=None,
               value_status="not_disclosed")

    recompute_after_turn(db, run_id)

    assert _value_of(db, run_id, parent) == 100.0


def test_cascade_stops_at_aggregate_only_boundary(seeded_db) -> None:
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    # Mark the parent aggregate_only with a literal value.
    _seed_fact(db, run_id, parent, value=999.0,
               value_status="user_override",
               children_status="aggregate_only")

    # Seed a couple of children that would otherwise sum to a different
    # value — they MUST be ignored.
    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    for c in children[:2]:
        _seed_fact(db, run_id, c, value=42.0)

    recompute_after_turn(db, run_id)

    # Parent value preserved.
    assert _value_of(db, run_id, parent) == 999.0


def test_partial_state_flagged_when_parent_and_children_both_filled(
    seeded_db,
) -> None:
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    # User wrote 50000 at the parent (observed, itemised).  Then we
    # have 2 of the leaves with values that sum to 47000.  Residual =
    # 3000 → conflict row expected.
    _seed_fact(db, run_id, parent, value=50000.0,
               value_status="observed", children_status="itemised")

    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    _seed_fact(db, run_id, children[0], value=20000.0)
    _seed_fact(db, run_id, children[1], value=27000.0)

    recompute_after_turn(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT kind, residual FROM run_concept_conflicts "
            "WHERE run_id = ? AND concept_uuid = ?",
            (run_id, parent),
        ).fetchall()
    finally:
        conn.close()

    assert any(r[0] == "partial_state" for r in rows), rows
    residuals = [r[1] for r in rows if r[0] == "partial_state"]
    assert any(abs((r or 0) - 3000.0) < 1e-6 for r in residuals)


def test_cascade_closes_partial_state_when_residual_clears(seeded_db) -> None:
    """Peer-review (Phase D): once a correction fixes the leaves so the
    observed parent reconciles, the cascade must CLOSE the open
    partial_state conflict — not leave it open forever."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    _seed_fact(db, run_id, parent, value=50000.0,
               value_status="observed", children_status="itemised")
    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    # First pass: children sum to 47000 ≠ 50000 → opens a partial_state.
    _seed_fact(db, run_id, children[0], value=20000.0)
    _seed_fact(db, run_id, children[1], value=27000.0)
    for c in children[2:]:
        _seed_fact(db, run_id, c, value=0.0)
    recompute_after_turn(db, run_id)

    # Correction fixes a leaf so children now sum to 50000 (reconciled).
    _seed_fact(db, run_id, children[1], value=30000.0)
    recompute_after_turn(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        status = conn.execute(
            "SELECT status FROM run_concept_conflicts WHERE run_id=? AND "
            "concept_uuid=? AND kind='partial_state'", (run_id, parent),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "resolved", "reconciled parent conflict should be closed"


def test_cascade_does_not_duplicate_conflicts_across_runs(seeded_db) -> None:
    """Peer-review #7: running the cascade twice on the same unresolved
    imbalance must not stack duplicate open partial_state conflicts."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    _seed_fact(db, run_id, parent, value=50000.0,
               value_status="observed", children_status="itemised")
    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    _seed_fact(db, run_id, children[0], value=20000.0)
    _seed_fact(db, run_id, children[1], value=27000.0)

    recompute_after_turn(db, run_id)
    recompute_after_turn(db, run_id)
    recompute_after_turn(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM run_concept_conflicts WHERE run_id = ? "
            "AND concept_uuid = ? AND kind = 'partial_state' "
            "AND status = 'open'",
            (run_id, parent),
        ).fetchone()[0]
    finally:
        conn.close()
    assert open_count == 1, f"expected 1 open conflict, got {open_count}"


def test_cascade_recomputes_its_own_total_when_children_change(seeded_db) -> None:
    """Regression: a cascade-derived parent (source='cascade',
    value_status='observed') must NOT lock itself. Editing a child after the
    first recompute must update the total, not raise a phantom partial_state
    conflict against the cascade's own prior write."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(children) >= 2

    # First pass: children sum to 100 → cascade writes parent=100
    # (source='cascade', value_status='observed').
    _seed_fact(db, run_id, children[0], value=100.0)
    for c in children[1:]:
        _seed_fact(db, run_id, c, value=0.0)
    recompute_after_turn(db, run_id)
    assert _value_of(db, run_id, parent) == 100.0

    # Simulate a stale conflict left behind by the old self-lock bug: an
    # open partial_state row against the cascade-owned parent. A later
    # recompute must clear it once the total re-derives cleanly.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
            "entity_scope, kind, residual, detail, status, created_at) "
            "VALUES (?, ?, 'CY', 'Company', 'partial_state', 50.0, 'stale', "
            "'open', '2026-05-21Z')",
            (run_id, parent),
        )
        conn.commit()
    finally:
        conn.close()

    # User edits a child: children now sum to 250. The total must follow.
    _seed_fact(db, run_id, children[1], value=150.0)
    recompute_after_turn(db, run_id)
    assert _value_of(db, run_id, parent) == 250.0

    # The total re-derived AND the stale phantom conflict is closed — no
    # open partial_state against the cascade's own write survives.
    conn = sqlite3.connect(str(db))
    try:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM run_concept_conflicts WHERE run_id = ? "
            "AND concept_uuid = ? AND kind = 'partial_state' "
            "AND status = 'open'",
            (run_id, parent),
        ).fetchone()[0]
    finally:
        conn.close()
    assert open_count == 0, f"cascade self-conflict survived: {open_count}"


def test_cascade_blanks_total_when_all_children_cleared(seeded_db) -> None:
    """Regression: clearing every child of a previously-computed total must
    blank the total too, not leave a stale value. Mirrors the review-UI flow
    where a user removes the values they entered."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    conn = sqlite3.connect(str(db))
    try:
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert len(children) >= 1

    # First pass: one child drives the total to 300.
    _seed_fact(db, run_id, children[0], value=300.0)
    recompute_after_turn(db, run_id)
    assert _value_of(db, run_id, parent) == 300.0

    # User clears that child (review UI sends value=None / not_disclosed).
    _seed_fact(db, run_id, children[0], value=None,
               value_status="not_disclosed")
    recompute_after_turn(db, run_id)

    # The total must blank out, not stay at the stale 300.
    assert _value_of(db, run_id, parent) is None


def test_cascade_leaves_untouched_total_blank(seeded_db) -> None:
    """A formula whose children are all blank and that was never computed
    stays blank — the cascade must not publish a spurious 0."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    # No facts seeded at all for this subtree.
    recompute_after_turn(db, run_id)
    assert _value_of(db, run_id, parent) is None


def test_cascade_writes_audit_events_for_computed_facts(seeded_db) -> None:
    """Peer-review #8: cascade-driven fact writes must append a
    concept_fact_events row (actor='cascade') so the audit-log
    invariant holds for computed facts, same as agent writes."""
    db, run_id = seeded_db
    parent = _uuid_for_row(db, "SOFP-Sub-CuNonCu", 39)
    conn = sqlite3.connect(str(db))
    try:
        child_uuids = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent,),
            ).fetchall()
        ]
    finally:
        conn.close()
    for c in child_uuids:
        _seed_fact(db, run_id, c, value=10.0)

    recompute_after_turn(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        events = conn.execute(
            "SELECT actor FROM concept_fact_events WHERE run_id = ? "
            "AND concept_uuid = ?",
            (run_id, parent),
        ).fetchall()
    finally:
        conn.close()
    assert any(e[0] == "cascade" for e in events), (
        f"no cascade audit event for computed parent; got {events}"
    )
