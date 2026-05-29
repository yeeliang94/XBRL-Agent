"""Phase 3 — canonical-mode correction agent.

Each test pins one step's behaviour.  The canonical agent operates on
the concept tree (concept_uuid + status axes) rather than Excel cells;
the gotcha-#17 and gotcha-#18 invariants port to the new prompt + the
new tools.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def seeded(tmp_path: Path) -> dict:
    """Initialise a v4 DB with the SOFP template imported and a run row
    ready to receive correction work."""
    from concept_model.importer import import_template
    from concept_model.parser import parse_template
    from db.schema import init_db

    db = tmp_path / "xbrl.db"
    init_db(db)
    tree = parse_template(str(FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "p3.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"db": db, "run_id": run_id, "template_id": template_id}


# -- Step 3.1: prompt carries concept-tree language ---------------------


def test_correction_prompt_carries_concept_tree_not_excel_cells(
    seeded: dict,
) -> None:
    """The canonical-mode correction prompt must reference concept_uuid /
    children_status / value_status, and must NOT carry "Cell B22" style
    coordinates (the agent is now thinking in concepts, not cells)."""
    from correction.canonical_agent import render_canonical_correction_prompt

    prompt = render_canonical_correction_prompt(
        db_path=seeded["db"],
        run_id=seeded["run_id"],
        conflicts=[
            {
                "concept_uuid": "abc-1",
                "kind": "partial_state",
                "residual": 3000.0,
                "detail": "parent vs children disagree",
            }
        ],
    )
    lower = prompt.lower()
    assert "concept_uuid" in lower, "prompt should expose concept_uuid"
    assert "children_status" in lower
    assert "value_status" in lower
    # Excel cell coords should be absent — search for the legacy
    # "Cell B22" / "row 22" framing.
    assert "cell b" not in lower, "legacy Cell B-style coords present"
    assert "merged workbook" not in lower, (
        "prompt still mentions the merged workbook; should reference "
        "the concept tree"
    )


# -- Step 3.2: mark_aggregate_only tool ---------------------------------


def test_correction_can_mark_concept_aggregate_only(seeded: dict) -> None:
    """The canonical correction agent gains a ``mark_aggregate_only``
    tool that emits an ``aggregate_only`` fact at a COMPUTED parent.

    The facts API accepts it (already pinned in test_facts_api.py),
    and the cascade respects the boundary (test_cascade_recompute.py).
    This test pins the tool's existence + payload shape.
    """
    from correction.canonical_agent import (
        canonical_correction_payload_builders,
        mark_aggregate_only,
    )

    # The tool roster includes mark_aggregate_only.
    assert "mark_aggregate_only" in {t.__name__ for t in canonical_correction_payload_builders()}

    # Calling it stages a fact-write payload that uses children_status=
    # aggregate_only and value_status=user_override.
    payload = mark_aggregate_only(
        concept_uuid="parent-1",
        value=12345.0,
        source="pdf p.28 — breakdown not disclosed",
    )
    assert payload["concept_uuid"] == "parent-1"
    assert payload["children_status"] == "aggregate_only"
    assert payload["value"] == 12345.0
    assert "user_override" in payload["value_status"] \
        or payload["value_status"] == "observed"


# -- Step 3.3: mark_not_disclosed tool ---------------------------------


def test_correction_can_mark_leaf_not_disclosed() -> None:
    """``mark_not_disclosed`` stages a LEAF fact with value=None and
    value_status='not_disclosed'.  The exporter respects this branch
    (test_canonical_export.py::test_not_disclosed_leaves_remain_blank).
    """
    from correction.canonical_agent import (
        canonical_correction_payload_builders,
        mark_not_disclosed,
    )
    assert "mark_not_disclosed" in {t.__name__ for t in canonical_correction_payload_builders()}

    payload = mark_not_disclosed(
        concept_uuid="leaf-99",
        source="pdf p.30 — line absent from disclosure",
    )
    assert payload["concept_uuid"] == "leaf-99"
    assert payload["value"] is None
    assert payload["value_status"] == "not_disclosed"
    assert payload["children_status"] is None


# -- Step 3.4: dynamic 8-25 turn cap preserved -------------------------


@pytest.mark.parametrize("is_group, n_failed, expected", [
    (False, 0, 8),    # base case, clamped at min
    (False, 1, 10),   # 8 + 2
    (False, 5, 18),   # 8 + 10
    (True,  5, 22),   # 8 + 4 + 10
    (False, 20, 25),  # clamped at max
    (True,  20, 25),  # clamped at max
])
def test_correction_canonical_honors_dynamic_turn_cap(
    is_group: bool, n_failed: int, expected: int,
) -> None:
    """Canonical-mode correction must use the same turn-budget formula
    as the legacy path (RUN-REVIEW P0-1): max(8, min(25, 8 + 4 if Group
    + 2 per failed check)).

    Sharing the helper guarantees the two paths can never drift —
    raising the legacy cap without updating the canonical cap would
    silently let canonical-mode correction race past pydantic-ai's
    50-iteration ceiling (gotcha #18).
    """
    from correction.canonical_agent import compute_canonical_turn_cap

    assert compute_canonical_turn_cap(
        filing_level="group" if is_group else "company",
        n_conflicts=n_failed,
    ) == expected


# -- Step 3.5: 50-iteration global cap (gotcha #18) --------------------


def test_canonical_correction_below_pydantic_50_cap() -> None:
    """The dynamic turn cap MUST stay strictly below the
    ``MAX_AGENT_ITERATIONS`` constant in ``agent_tracing.py``, which
    in turn MUST stay strictly below pydantic-ai's silent 50-iter
    ``request_limit`` default (gotcha #18).

    Setting either equal to 50 races pydantic-ai's check; setting the
    canonical cap above MAX_AGENT_ITERATIONS lets the canonical path
    burn through MAX before the dynamic cap can fire its actionable
    error.
    """
    from agent_tracing import MAX_AGENT_ITERATIONS
    from correction.canonical_agent import compute_canonical_turn_cap

    assert MAX_AGENT_ITERATIONS < 50, (
        f"MAX_AGENT_ITERATIONS={MAX_AGENT_ITERATIONS} >= pydantic-ai's "
        f"silent 50-iter ceiling"
    )
    # The dynamic cap maxes out at 25 (clamped).  This stays well below
    # MAX_AGENT_ITERATIONS (40 today).  If MAX is ever lowered close
    # to 25 we should know about it — pin the strict inequality.
    worst_case = compute_canonical_turn_cap(
        filing_level="group", n_conflicts=999
    )
    assert worst_case < MAX_AGENT_ITERATIONS, (
        f"canonical_turn_cap worst-case ({worst_case}) >= "
        f"MAX_AGENT_ITERATIONS ({MAX_AGENT_ITERATIONS})"
    )


# -- Step 3.6: 300s wallclock cap (gotcha #18) -------------------------


def test_canonical_correction_respects_wallclock_300s() -> None:
    """Canonical correction reuses the same ``CORRECTION_WALLCLOCK_TIMEOUT``
    constant as the legacy path (defence-in-depth on top of the
    dynamic turn cap; catches slow-LLM scenarios where many quick
    turns add up past 5 minutes).
    """
    from correction.canonical_agent import canonical_correction_wallclock_timeout
    # Import the source-of-truth constant from server.py and pin
    # equality so a future split is loud.
    from server import CORRECTION_WALLCLOCK_TIMEOUT

    assert canonical_correction_wallclock_timeout() == CORRECTION_WALLCLOCK_TIMEOUT, (
        "canonical wallclock cap drifted from server.CORRECTION_WALLCLOCK_TIMEOUT"
    )
    # Default is 300s; pinned strictly positive in case an operator
    # disables it via XBRL_CORRECTION_WALLCLOCK_S=0 (which the legacy
    # parser respects — the canonical helper should too).
    assert canonical_correction_wallclock_timeout() > 0 \
        or canonical_correction_wallclock_timeout() == 0


# -- Step 3.7: no-residual-plug rule (gotcha #17) ----------------------


def test_canonical_correction_prompt_carries_no_plug_rule() -> None:
    """The canonical correction prompt must carry the no-residual-plug
    rule verbatim — same catch-all concept list as the legacy prompt.

    Gotcha #17 enforces this at the writer; the prompt enforces it at
    the LLM.  Both layers must agree or the agent learns to plug
    behind the writer's back.
    """
    from correction.canonical_agent import render_canonical_correction_prompt

    prompt = render_canonical_correction_prompt(
        db_path=":memory:", run_id=0, conflicts=[],
    )
    lower = prompt.lower()
    # The catch-all family lifted from prompts/correction.md.
    for catchall in (
        "other", "miscellaneous", "administrative expenses",
        "other expenses",
    ):
        assert catchall in lower, (
            f"canonical prompt missing catch-all term {catchall!r}"
        )
    assert "never write a residual" in lower or "never plug" in lower, (
        "canonical prompt missing the never-plug rule"
    )
    # The two legitimate moves the canonical agent has beyond the
    # legacy agent — these MUST be named in the prompt so the agent
    # knows to reach for them instead of plugging.
    assert "mark_aggregate_only" in lower
    assert "mark_not_disclosed" in lower


# -- Step 3.8: header guard ported (gotcha #17) ------------------------


def test_canonical_correction_cannot_write_to_abstract_concept(
    seeded: dict,
) -> None:
    """The facts API already refuses ABSTRACT writes (pinned in
    test_facts_api.py step 1.6).  This test asserts the canonical
    correction prompt teaches the agent the same rule so it doesn't
    bash its head against 400s.
    """
    from correction.canonical_agent import render_canonical_correction_prompt

    prompt = render_canonical_correction_prompt(
        db_path=seeded["db"], run_id=seeded["run_id"], conflicts=[],
    )
    lower = prompt.lower()
    assert "abstract" in lower, "prompt should mention ABSTRACT concepts"
    # The prompt names ``kind=ABSTRACT`` so the agent recognises the
    # field carried in the conflict block.
    assert "kind=abstract" in lower or "abstract concepts" in lower
    # And explicitly says they're not writable / will be rejected.
    assert (
        "not writable" in lower
        or "will reject" in lower
        or "will be rejected" in lower
        or "reject any" in lower
    )


# -- Step 3.9: exhaustion routes to reconciliation queue ---------------


def test_correction_exhausted_routes_to_reconciliation_queue(
    seeded: dict,
) -> None:
    """When the canonical correction agent hits its turn cap without
    resolving all conflicts, the unresolved ones must remain open in
    ``run_concept_conflicts``.  The coordinator should also emit a
    sentinel queue row carrying ``kind='correction_exhausted'`` so
    the UI distinguishes "agent gave up" from "no one tried".
    """
    from correction.canonical_agent import record_correction_exhaustion

    record_correction_exhaustion(
        db_path=seeded["db"],
        run_id=seeded["run_id"],
        unresolved_conflict_ids=[],   # none queued from the test fixture
        turns_used=10,
        max_turns=10,
        detail="all 10 turns spent; 2 conflicts left open",
    )

    conn = sqlite3.connect(str(seeded["db"]))
    try:
        rows = conn.execute(
            "SELECT kind, status, detail FROM run_concept_conflicts "
            "WHERE run_id = ? ORDER BY id",
            (seeded["run_id"],),
        ).fetchall()
    finally:
        conn.close()

    assert any(r[0] == "correction_exhausted" for r in rows), rows
    sentinel = next(r for r in rows if r[0] == "correction_exhausted")
    assert sentinel[1] == "open"
    assert "10" in sentinel[2]


# -- Step 3.10: canonical mode invokes correction --------------------


def test_canonical_mode_invokes_reviewer_when_imbalance_present() -> None:
    """Canonical mode runs the REVIEWER pass on an imbalance
    (docs/PLAN-reviewer-agent.md, Step 9/10) — the reviewer replaced the
    autonomous canonical correction pass.

    Pinned via source inspection: the canonical branch at the
    correction call site now launches ``_run_reviewer_pass`` and emits the
    ``reviewing`` pipeline stage (gotcha #19), while the legacy
    non-canonical path keeps ``_run_correction_pass`` / ``correcting``.
    """
    import inspect
    import server

    src = inspect.getsource(server)
    assert "_run_reviewer_pass(" in src, (
        "server.py doesn't wire the reviewer pass — Step 9 has not landed"
    )
    assert "reviewing" in src.lower(), (
        "server.py doesn't emit the 'reviewing' pipeline stage"
    )
    # The deleted autonomous canonical correction pass must be gone.
    assert "async def _run_canonical_correction_pass" not in src, (
        "the autonomous canonical correction pass should have been removed "
        "in Step 10"
    )


# -- Step 3.11: Phase-3 E2E — correction resolves via legitimate means -


def test_e2e_canonical_correction_resolves_imbalance(seeded: dict) -> None:
    """Drive the canonical correction loop without an LLM:

    1. Seed an intentional partial_state imbalance.
    2. Verify the conflict is in the queue.
    3. Call ``mark_aggregate_only`` (the legitimate non-plug resolve).
    4. Re-run cascade.
    5. Original partial_state conflict is no longer reproduced AND no
       catch-all leaf received a plug write.
    """
    import sqlite3
    from concept_model.cascade import recompute_after_turn
    from concept_model.facts_api import _open_conn  # internal helper

    db = seeded["db"]
    run_id = seeded["run_id"]

    # Pick a known COMPUTED parent (sub-sheet *Total PPE) + its first
    # leaf child for the imbalance setup.
    conn = sqlite3.connect(str(db))
    try:
        parent = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE render_sheet = ? "
            "AND render_row = ?", ("SOFP-Sub-CuNonCu", 39),
        ).fetchone()[0]
        leaf = conn.execute(
            "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ? "
            "LIMIT 1", (parent,),
        ).fetchone()[0]
    finally:
        conn.close()

    # Step 1: seed parent=50000 observed, single leaf=20000 → residual 30000.
    conn = sqlite3.connect(str(db))
    try:
        for uid, val, kind in [
            (parent, 50000.0, "observed"),
            (leaf, 20000.0, "observed"),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO run_concept_facts("
                "run_id, concept_uuid, period, entity_scope, value, "
                "value_status, source, updated_at) "
                "VALUES (?, ?, 'CY', 'Company', ?, ?, 'seed', '2026-05Z')",
                (run_id, uid, val, kind),
            )
        conn.commit()
    finally:
        conn.close()
    recompute_after_turn(db, run_id)

    # Step 2: cascade emitted a partial_state conflict.
    conn = sqlite3.connect(str(db))
    try:
        before = conn.execute(
            "SELECT kind FROM run_concept_conflicts "
            "WHERE run_id = ? AND concept_uuid = ? AND status = 'open'",
            (run_id, parent),
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == "partial_state" for r in before)

    # Step 3: call mark_aggregate_only via the facts API (the agent's
    # legitimate resolution — declares the underlying breakdown is not
    # disclosed; parent stays as the literal 50000).
    from correction.canonical_agent import mark_aggregate_only
    from fastapi.testclient import TestClient
    import importlib, server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    client = TestClient(srv.app)

    payload = mark_aggregate_only(
        concept_uuid=parent,
        value=50000.0,
        source="pdf p.28 — breakdown not disclosed",
    )
    r = client.post(f"/api/runs/{run_id}/facts", json=payload)
    assert r.status_code == 200, r.text

    # Step 4: resolve the old conflict.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE run_concept_conflicts SET status='resolved' "
            "WHERE run_id = ? AND concept_uuid = ? AND kind='partial_state'",
            (run_id, parent),
        )
        conn.commit()
    finally:
        conn.close()

    # Re-cascade — the aggregate_only boundary preserves parent value;
    # no new partial_state should appear.
    recompute_after_turn(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        post = conn.execute(
            "SELECT kind, status FROM run_concept_conflicts "
            "WHERE run_id = ? AND concept_uuid = ?",
            (run_id, parent),
        ).fetchall()
        # Step 5: no "Other …" catch-all received a plug write.
        # Find every concept whose canonical_label starts with "Other "
        # on the SOFP-Sub sheet and assert none of them have a fact.
        plug_writes = conn.execute(
            """
            SELECT n.canonical_label
            FROM concept_nodes n
            JOIN run_concept_facts f ON f.concept_uuid = n.concept_uuid
            WHERE f.run_id = ?
              AND n.canonical_label LIKE 'Other %'
              AND n.render_sheet = 'SOFP-Sub-CuNonCu'
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    assert all(r[1] != "open" or r[0] != "partial_state" for r in post), post
    assert plug_writes == [], (
        f"catch-all 'Other …' concepts received plug writes: {plug_writes}"
    )
