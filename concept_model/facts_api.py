"""FastAPI router for the canonical-mode facts API.

Phase 1 step 1.4 - 1.8 — every canonical-mode agent write lands here,
not directly into the .xlsx workbook.  Validation is kind-aware so the
DB echoes the writer-side guarantees that gotcha #17 enforces today:

* writes to ABSTRACT concepts (section headers) are refused with a
  clear error message — the agent must pick the leaf row below;
* writes to COMPUTED concepts are refused unless the agent explicitly
  flags the parent as ``aggregate_only`` (children_status) — the
  default cascade computes those values from the leaves;
* ``children_status`` is meaningless on a LEAF and refused there.

Composite key on ``run_concept_facts`` is
``(run_id, concept_uuid, period, entity_scope)``, so a Group filing's
CY+Company and CY+Group rows live as distinct facts.

Every successful write appends an audit row to ``concept_fact_events``
so the reconciliation queue (step 1.10) and any future "show me what
the correction agent did" UI have a durable history.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic body — eight fields per PRD §6.
# ---------------------------------------------------------------------------


_VALUE_STATUSES = {
    "observed",
    "explicit_zero",
    "not_disclosed",
    "user_override",
    "conflict",
}

_CHILDREN_STATUSES = {"itemised", "aggregate_only", "partial"}


class FactWrite(BaseModel):
    """Agent → DB write contract for canonical mode.

    ``value`` may be ``None`` for non-numeric statuses (e.g.
    ``not_disclosed``).  ``children_status`` is restricted to COMPUTED
    concepts; LEAF rows must leave it unset.  Per-turn provenance lives
    in ``source`` and ``evidence``.
    """

    # Scalar (face-statement) writes carry a concept_uuid; notes (HTML)
    # writes may omit it (the server mints a deterministic one from
    # sheet/row/label). Phase 7 makes it optional so one endpoint serves
    # both stores.
    concept_uuid: Optional[str] = None
    # Constrained to the two-period / two-scope dimensions.  Free
    # strings used to be accepted and could silently default a Group
    # export column via the exporter's COALESCE fallback (peer-review
    # #4); Literal makes a bad value a 422 at the API boundary.
    period: Literal["CY", "PY"] = "CY"
    entity_scope: Literal["Company", "Group"] = "Company"
    value: Optional[float] = None
    value_status: str = Field(default="observed")
    children_status: Optional[str] = None
    source: Optional[str] = None
    evidence: Optional[str] = None
    actor: Optional[str] = None
    turn: Optional[int] = None
    # Phase 7 — notes (HTML) branch. When `html` is present the write is
    # routed to notes_cells (sanitised + 30k-rendered-char capped) instead
    # of run_concept_facts; sheet/row/label locate the template cell.
    html: Optional[str] = None
    sheet: Optional[str] = None
    row: Optional[int] = None
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _open_conn(db_path: str) -> sqlite3.Connection:
    """Open the audit DB with the canonical pragmas.  Caller closes."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def _lookup_concept(conn: sqlite3.Connection, concept_uuid: str):
    """Return the concept row or ``None`` for unknown UUIDs."""
    return conn.execute(
        "SELECT concept_uuid, kind, canonical_label, render_sheet, "
        "render_row FROM concept_nodes WHERE concept_uuid = ?",
        (concept_uuid,),
    ).fetchone()


def _concept_owns_formula(conn: sqlite3.Connection, concept_uuid: str, concept) -> bool:
    """True when the concept carries a formula (has outgoing edges).

    COMPUTED rows always do. SOCIE totals are stored as MATRIX_CELL WITH
    edges; a data-entry MATRIX_CELL (a component-movement cell) has none.
    Gating the formula-cell guard on this — not on kind == 'COMPUTED' —
    keeps matrix totals from accepting observed literals (peer-review).
    """
    if concept["kind"] == "COMPUTED":
        return True
    return conn.execute(
        "SELECT 1 FROM concept_edges WHERE parent_uuid = ? LIMIT 1",
        (concept_uuid,),
    ).fetchone() is not None


def _validate(fact: FactWrite, concept, is_formula: bool) -> None:
    """Apply kind-aware + status-axis rules.  Raises HTTPException(400).

    Error messages mirror ``tools/fill_workbook.py``'s wording so that
    the agent receives the same feedback whether canonical mode or the
    legacy direct-write path is active.

    ``is_formula`` is True for any concept that owns a formula (COMPUTED, or
    a MATRIX_CELL with edges) — the formula-cell guard keys on it so SOCIE
    totals get the same protection as linear COMPUTED rows.
    """
    if fact.value_status not in _VALUE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown value_status {fact.value_status!r}. "
                f"Expected one of: {sorted(_VALUE_STATUSES)}."
            ),
        )
    if (
        fact.children_status is not None
        and fact.children_status not in _CHILDREN_STATUSES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown children_status {fact.children_status!r}. "
                f"Expected one of: {sorted(_CHILDREN_STATUSES)}."
            ),
        )

    kind = concept["kind"]
    sheet = concept["render_sheet"]
    row = concept["render_row"]

    if kind == "ABSTRACT":
        # gotcha #17 — section-header rows are never writable.  Point
        # the agent at the leaf rows below as the writer guard does.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refusing write to ABSTRACT concept "
                f"({sheet} row {row}): this is a section header — pick "
                f"a leaf row inside the section instead."
            ),
        )

    if (
        is_formula
        and fact.value_status == "observed"
        and fact.children_status != "aggregate_only"
    ):
        # Formula concepts (COMPUTED, or a MATRIX_CELL with edges) get
        # their value from the cascade.  The legitimate escape is an
        # explicit ``children_status=aggregate_only`` marker (the agent
        # has declared the underlying breakdown is not disclosed); we
        # translate that to ``value_status='user_override'`` below and
        # accept the write.  Without the marker, refuse — the agent should
        # write the underlying leaves / component cells instead.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refusing observed write to formula concept "
                f"({kind} at {sheet} row {row}): this cell carries a "
                f"formula. Write the underlying cells instead, or mark "
                f"children_status=aggregate_only to keep it as a literal."
            ),
        )

    if fact.children_status is not None and not is_formula:
        # children_status only means something on a formula-owning concept
        # (COMPUTED, or a MATRIX_CELL with edges) — it declares whether the
        # cascade should compute the value from children or treat the parent
        # as authoritative. A LEAF or a data-entry MATRIX_CELL (no edges) has
        # no children, so the marker is meaningless and refused (peer-review).
        raise HTTPException(
            status_code=400,
            detail=(
                f"children_status is only valid on formula concepts "
                f"(COMPUTED / matrix totals); {sheet} row {row} is a "
                f"{kind} with no children."
            ),
        )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def apply_fact(
    conn: sqlite3.Connection,
    run_id: int,
    body: "FactWrite",
    *,
    commit: bool = True,
) -> dict:
    """In-process core of ``POST /api/runs/{id}/facts``.

    Runs the full kind-aware validation, conflict detection, upsert and
    audit-journalling against the caller-supplied ``conn`` and commits on
    success. The caller owns the connection lifecycle (open + close); this
    lets the extraction reroute and the canonical correction agent write
    facts without an HTTP round-trip, while the FastAPI route stays a thin
    wrapper. Raises ``HTTPException`` for the same 404/400 cases the route
    does so both surfaces share one error contract.

    Pass ``commit=False`` to batch many writes into one transaction (the
    caller is then responsible for committing once at the end). Conflict
    detection still sees this connection's uncommitted writes, so parent /
    child cells in the same batch reconcile correctly.
    """
    run_row = conn.execute(
        "SELECT id FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Phase 7 — scalar-vs-HTML branch. A notes write carries
    # `html`; it's sanitised + capped and routed to notes_cells
    # (gotcha #16 invariants), not run_concept_facts.
    if body.html is not None:
        return _post_notes_fact(conn, run_id, body)

    if not body.concept_uuid:
        raise HTTPException(
            status_code=400,
            detail="concept_uuid is required for a scalar fact write "
                   "(omit it only for an HTML notes write).",
        )

    concept = _lookup_concept(conn, body.concept_uuid)
    if concept is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown concept_uuid {body.concept_uuid!r}. "
                "Use the importer's concept_nodes table for the "
                "canonical set."
            ),
        )

    # A concept owns a formula when it has outgoing edges. COMPUTED
    # rows always do; SOCIE totals are stored as MATRIX_CELL WITH
    # edges (a data-entry MATRIX_CELL has none). The formula-cell
    # guard below keys on this, not on kind, so a matrix total can't
    # be POSTed an observed literal without aggregate_only the way a
    # COMPUTED row can't.
    is_formula = _concept_owns_formula(conn, body.concept_uuid, concept)

    _validate(body, concept, is_formula)

    # The "parent and child both written" detection lives here
    # so neither the extraction nor the correction agent has to
    # carry duplicate logic (decision 3 in PRD §6).  See step
    # 1.7 contract: writing aggregate_only at a parent and then
    # observed at a child raises a conflict row.
    #
    # Two directions, both must be covered (peer-review #5):
    #   a) aggregate_only parent written, child already observed
    #      → handled by _detect_parent_child_conflicts
    #   b) child observed written, ancestor already aggregate_only
    #      → handled by _detect_aggregate_only_ancestor
    if body.children_status:
        _detect_parent_child_conflicts(
            conn, run_id, body, concept
        )
    else:
        _detect_aggregate_only_ancestor(
            conn, run_id, body, concept
        )

    # Read the current row so we can journal the before/after
    # state into concept_fact_events even when the row is new.
    before = conn.execute(
        "SELECT value, value_status, children_status, source, "
        "evidence FROM run_concept_facts WHERE run_id = ? "
        "AND concept_uuid = ? AND period = ? AND entity_scope = ?",
        (run_id, body.concept_uuid, body.period, body.entity_scope),
    ).fetchone()

    # Aggregate_only marker — the COMPUTED concept now carries
    # an authoritative literal value, so its value_status flips
    # to user_override (we won't recompute it in the cascade).
    effective_value_status = body.value_status
    if is_formula and body.children_status == "aggregate_only":
        effective_value_status = "user_override"

    now = _now()
    conn.execute(
        """
        INSERT INTO run_concept_facts(
            run_id, concept_uuid, period, entity_scope, value,
            value_status, children_status, source, evidence,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, concept_uuid, period, entity_scope)
        DO UPDATE SET
            value = excluded.value,
            value_status = excluded.value_status,
            children_status = excluded.children_status,
            source = excluded.source,
            evidence = excluded.evidence,
            updated_at = excluded.updated_at
        """,
        (
            run_id, body.concept_uuid, body.period,
            body.entity_scope, body.value, effective_value_status,
            body.children_status, body.source, body.evidence, now,
        ),
    )

    conn.execute(
        """
        INSERT INTO concept_fact_events(
            run_id, concept_uuid, period, entity_scope,
            actor, turn, ts, before_json, after_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, body.concept_uuid, body.period,
            body.entity_scope, body.actor or "agent", body.turn,
            now,
            json.dumps(dict(before)) if before else None,
            json.dumps({
                "value": body.value,
                "value_status": effective_value_status,
                "children_status": body.children_status,
            }),
        ),
    )
    if commit:
        conn.commit()

    return {
        "ok": True,
        "concept_uuid": body.concept_uuid,
        "kind": concept["kind"],
        "period": body.period,
        "entity_scope": body.entity_scope,
        "value": body.value,
        "value_status": effective_value_status,
        "children_status": body.children_status,
    }


def write_fact(db_path, run_id: int, body: "FactWrite") -> dict:
    """Open the audit DB, apply one fact write, close. Convenience wrapper
    around :func:`apply_fact` for in-process callers that don't already hold
    a connection (extraction reroute, correction agent). Rolls back and
    re-raises on failure so a bad write never leaves a half-committed row.
    """
    conn = _open_conn(str(db_path))
    try:
        return apply_fact(conn, run_id, body)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 1.1/1.2 — user value edits from the review UI.
# ---------------------------------------------------------------------------


class FactValuePatch(BaseModel):
    """Review-UI → DB contract for editing one face-statement value.

    Deliberately narrower than ``FactWrite``: the user picks a cell
    (concept_uuid + period + entity_scope) and types a number. ``value``
    of ``None`` means "clear this cell" (the breakdown is not disclosed).
    The endpoint owns the ``value_status`` — the client never sets it —
    so the audit trail can always tell a human edit apart from an agent
    observation or a cascade recompute.
    """

    value: Optional[float] = None
    period: Literal["CY", "PY"] = "CY"
    entity_scope: Literal["Company", "Group"] = "Company"


def _ancestor_facts(
    conn: sqlite3.Connection,
    run_id: int,
    concept_uuid: str,
    period: str,
    entity_scope: str,
) -> list[dict]:
    """Return the current facts of every formula ancestor of a concept.

    After a leaf edit the cascade recomputes the subtotals above it; the
    review UI needs the new totals to update in place without refetching
    the whole tree. We walk ``concept_edges`` upward (child → parent)
    breadth-first — the same traversal ``_detect_aggregate_only_ancestor``
    uses — and read each ancestor's fact for this (period, scope).
    """
    seen: set[str] = set()
    frontier = [concept_uuid]
    out: list[dict] = []
    while frontier:
        node = frontier.pop()
        parents = [
            r[0]
            for r in conn.execute(
                "SELECT parent_uuid FROM concept_edges WHERE child_uuid = ?",
                (node,),
            ).fetchall()
        ]
        for parent_uuid in parents:
            if parent_uuid in seen:
                continue
            seen.add(parent_uuid)
            frontier.append(parent_uuid)
            fact = conn.execute(
                "SELECT value, value_status FROM run_concept_facts "
                "WHERE run_id = ? AND concept_uuid = ? AND period = ? "
                "AND entity_scope = ?",
                (run_id, parent_uuid, period, entity_scope),
            ).fetchone()
            if fact is not None:
                out.append(
                    {
                        "concept_uuid": parent_uuid,
                        "value": fact["value"],
                        "value_status": fact["value_status"],
                        "period": period,
                        "entity_scope": entity_scope,
                    }
                )
    return out


def patch_fact_value(
    db_path,
    run_id: int,
    concept_uuid: str,
    body: "FactValuePatch",
) -> dict:
    """Apply one user value edit, recompute subtotals, return the deltas.

    Phase 1.1 + 1.2 of the editable-review plan. Steps:

    1. Validate the target is editable — refuse ABSTRACT (section header,
       gotcha #17) and formula-owning concepts (COMPUTED / SOCIE matrix
       totals). The user edits the leaves; the cascade owns the totals.
    2. Write the value through :func:`apply_fact` so the edit inherits the
       same conflict detection + audit journalling agent writes get. We
       stamp ``value_status='user_override'`` (a typed number) or
       ``not_disclosed`` (cleared cell) and ``actor='user'``.
    3. Run the cascade so every dependent subtotal is recomputed and any
       new partial-state conflict is recorded.
    4. Return the edited fact plus the recomputed ancestor totals so the
       UI updates without a full refetch.
    """
    import math

    from concept_model.cascade import recompute_after_turn

    # Reject non-finite values at the boundary. JSON parsers can admit
    # NaN/Infinity, and a NaN in run_concept_facts would silently corrupt
    # every cascade subtotal it feeds (NaN propagates through the sum) and
    # land an unopenable cell in the exported Excel.
    if body.value is not None and not math.isfinite(body.value):
        raise HTTPException(
            status_code=400,
            detail="value must be a finite number (got NaN/Infinity).",
        )

    # Status is endpoint-owned (see FactValuePatch): a typed number is a
    # deliberate human override (including a typed 0), a cleared cell means
    # "not disclosed".
    value_status = "not_disclosed" if body.value is None else "user_override"

    conn = _open_conn(str(db_path))
    try:
        concept = _lookup_concept(conn, concept_uuid)
        if concept is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown concept_uuid {concept_uuid!r}.",
            )
        if concept["kind"] == "ABSTRACT":
            raise HTTPException(
                status_code=400,
                detail=(
                    "This is a section header and is never editable — edit "
                    "a leaf row inside the section instead."
                ),
            )
        if _concept_owns_formula(conn, concept_uuid, concept):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This value is computed from the rows beneath it and "
                    "updates automatically — edit those rows instead."
                ),
            )

        write_body = FactWrite(
            concept_uuid=concept_uuid,
            period=body.period,
            entity_scope=body.entity_scope,
            value=body.value,
            value_status=value_status,
            source="manual edit",
            actor="user",
        )
        result = apply_fact(conn, run_id, write_body)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # The cascade opens its own connection and commits; run it after the
    # edit is durable so a recompute failure can't roll back the edit.
    recompute_after_turn(db_path, run_id)

    conn = _open_conn(str(db_path))
    try:
        recomputed = _ancestor_facts(
            conn, run_id, concept_uuid, body.period, body.entity_scope
        )
    finally:
        conn.close()

    result["recomputed"] = recomputed
    return result


def register_facts_routes(app, audit_db_getter) -> None:
    """Attach the facts API to a FastAPI app.

    ``audit_db_getter`` is a zero-arg callable returning the path to
    the audit DB — that indirection lets tests swap the path at runtime
    via ``server.AUDIT_DB_PATH``.
    """

    @app.post("/api/runs/{run_id}/facts")
    def post_fact(run_id: int, body: FactWrite):
        return write_fact(audit_db_getter(), run_id, body)

    @app.patch("/api/runs/{run_id}/facts/{concept_uuid}")
    def patch_fact(run_id: int, concept_uuid: str, body: FactValuePatch):
        return patch_fact_value(audit_db_getter(), run_id, concept_uuid, body)


def _post_notes_fact(conn: sqlite3.Connection, run_id: int, body: "FactWrite"):
    """Route an HTML notes write to notes_cells (Phase 7).

    Preserves the gotcha #16 invariants the PATCH endpoint and writer
    enforce: HTML is sanitised against the shared tag whitelist, then
    rejected with 413 if it exceeds the 30k *rendered* character cap. The
    notes row gets a deterministic concept_uuid (minted from
    sheet/row/label when the caller omits one) so it's addressable in the
    unified store.
    """
    from notes.html_sanitize import sanitize_notes_html
    from notes.html_to_text import rendered_length
    from notes.writer import CELL_CHAR_LIMIT
    from db.repository import upsert_notes_cell
    from concept_model.parser import mint_notes_concept_uuid

    if not body.sheet or body.row is None or not body.label:
        raise HTTPException(
            status_code=400,
            detail="An HTML notes write requires sheet, row and label.",
        )

    # Bound the RAW payload before sanitising. The 30k cap below is on
    # *rendered* length, so a multi-megabyte body of nested tags/attributes
    # can sanitise down under the cap while forcing unbounded parser CPU and
    # memory first. Reject anything wildly larger than the rendered cap could
    # justify (legitimate markup is a small multiple of its rendered text).
    raw_limit = CELL_CHAR_LIMIT * 10
    if body.html is not None and len(body.html) > raw_limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Notes HTML payload exceeds the {raw_limit}-character raw cap "
                f"({len(body.html)})."
            ),
        )

    cleaned, warnings = sanitize_notes_html(body.html)
    if rendered_length(cleaned) > CELL_CHAR_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Notes content exceeds the {CELL_CHAR_LIMIT} rendered-character "
                f"cap ({rendered_length(cleaned)})."
            ),
        )

    # A notes cell's identity is fully determined by (sheet, row, label) —
    # always mint it server-side. A caller-supplied concept_uuid is
    # ignored (peer-review): trusting it would let a client attach an
    # arbitrary UUID — e.g. a face-statement concept's — to a notes row,
    # corrupting the unified store's identity invariant. If the caller did
    # send one, it must match the deterministic value or we reject, so a
    # genuine round-trip still works but a cross-link is refused.
    deterministic = mint_notes_concept_uuid(body.sheet, body.row, body.label)
    if body.concept_uuid is not None and body.concept_uuid != deterministic:
        raise HTTPException(
            status_code=400,
            detail=(
                "concept_uuid for a notes write must equal the deterministic "
                "UUID for (sheet, row, label); omit it to have the server "
                "derive it."
            ),
        )
    concept_uuid = deterministic
    upsert_notes_cell(
        conn,
        run_id=run_id,
        sheet=body.sheet,
        row=body.row,
        label=body.label,
        html=cleaned,
        evidence=body.evidence,
        concept_uuid=concept_uuid,
    )
    conn.commit()
    return {
        "ok": True,
        "kind": "NOTE",
        "concept_uuid": concept_uuid,
        "sheet": body.sheet,
        "row": body.row,
        "sanitizer_warnings": warnings,
    }


def _detect_parent_child_conflicts(
    conn: sqlite3.Connection,
    run_id: int,
    body: FactWrite,
    concept,
) -> None:
    """Conflict-row generator for the partial-state case.

    When the agent writes ``aggregate_only`` at a parent, then later
    writes ``observed`` at one of its leaves (or vice versa), the
    cascade has two competing sources of truth.  We surface that here
    so the reconciliation queue picks it up; the write itself is still
    accepted.
    """
    if body.children_status != "aggregate_only":
        return
    # Walk the FULL subtree, not just direct children. In SOFP/SOPL a
    # formula parent often depends on intermediate COMPUTED subtotals whose
    # facts don't exist yet at write time (the cascade fills them at the
    # turn boundary) — only the leaves further down carry observed values.
    # A direct-child-only check would miss that an aggregate_only parent
    # already has observed descendants under an as-yet-uncomputed subtotal
    # (peer-review). Mirror of the upward walk in
    # _detect_aggregate_only_ancestor.
    descendants: list[str] = []
    seen: set[str] = set()
    frontier = [body.concept_uuid]
    while frontier:
        node = frontier.pop()
        children = [
            r[0] for r in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (node,),
            ).fetchall()
        ]
        for child_uuid in children:
            if child_uuid in seen:
                continue
            seen.add(child_uuid)
            descendants.append(child_uuid)
            frontier.append(child_uuid)
    if not descendants:
        return
    placeholders = ",".join("?" for _ in descendants)
    observed = conn.execute(
        f"SELECT COUNT(*) FROM run_concept_facts WHERE run_id = ? "
        f"AND period = ? AND entity_scope = ? AND value_status = 'observed' "
        f"AND concept_uuid IN ({placeholders})",
        (run_id, body.period, body.entity_scope, *descendants),
    ).fetchone()[0]
    if not observed:
        return
    now = _now()
    conn.execute(
        """
        INSERT INTO run_concept_conflicts(
            run_id, concept_uuid, period, entity_scope, kind, residual,
            detail, status, created_at
        ) VALUES (?, ?, ?, ?, 'parent_child_disagree', NULL, ?, 'open', ?)
        """,
        (
            run_id, body.concept_uuid, body.period, body.entity_scope,
            f"aggregate_only parent has {observed} observed descendant(s)",
            now,
        ),
    )


def _detect_aggregate_only_ancestor(
    conn: sqlite3.Connection,
    run_id: int,
    body: FactWrite,
    concept,
) -> None:
    """Conflict-row generator for the reverse direction (peer-review #5).

    When a LEAF (or any non-aggregate concept) is written and one of
    its COMPUTED ancestors is ALREADY marked ``aggregate_only`` for the
    same (run, period, entity_scope), the two sources of truth disagree
    — the parent says "breakdown not disclosed" but a child just got a
    value.  We flag a conflict against the offending ANCESTOR so the
    queue points the reviewer at the parent that needs revisiting.
    """
    if body.value_status != "observed":
        return

    # Walk upward through concept_edges (child → parent) breadth-first,
    # checking each ancestor for an existing aggregate_only fact.
    seen: set[str] = set()
    frontier = [body.concept_uuid]
    while frontier:
        node = frontier.pop()
        parents = [
            r[0] for r in conn.execute(
                "SELECT parent_uuid FROM concept_edges WHERE child_uuid = ?",
                (node,),
            ).fetchall()
        ]
        for parent_uuid in parents:
            if parent_uuid in seen:
                continue
            seen.add(parent_uuid)
            agg = conn.execute(
                "SELECT 1 FROM run_concept_facts WHERE run_id = ? "
                "AND concept_uuid = ? AND period = ? AND entity_scope = ? "
                "AND children_status = 'aggregate_only'",
                (run_id, parent_uuid, body.period, body.entity_scope),
            ).fetchone()
            if agg is not None:
                # Avoid duplicate open conflicts for the same ancestor.
                existing = conn.execute(
                    "SELECT 1 FROM run_concept_conflicts WHERE run_id = ? "
                    "AND concept_uuid = ? AND period = ? AND entity_scope = ? "
                    "AND kind = 'parent_child_disagree' AND status = 'open'",
                    (run_id, parent_uuid, body.period, body.entity_scope),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO run_concept_conflicts(
                            run_id, concept_uuid, period, entity_scope,
                            kind, residual, detail, status, created_at
                        ) VALUES (?, ?, ?, ?, 'parent_child_disagree',
                                  NULL, ?, 'open', ?)
                        """,
                        (
                            run_id, parent_uuid, body.period,
                            body.entity_scope,
                            "aggregate_only ancestor has a newly-written "
                            "observed descendant",
                            _now(),
                        ),
                    )
            frontier.append(parent_uuid)
