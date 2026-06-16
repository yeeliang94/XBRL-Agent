"""Phase 1 step 1.9-1.10 — cascade recompute at turn boundary.

For every COMPUTED concept in topological order (leaves first), we
gather the children's facts and write the signed-sum into
``run_concept_facts``.  Two boundaries the walker respects:

* ``children_status='aggregate_only'`` on a parent — the agent has
  declared the parent authoritative.  Skip recompute; leave the
  parent's value alone.
* The parent is itemised (or unset) AND already carries an observed
  value AND the recomputed value differs — emit a ``partial_state``
  conflict row carrying the residual.
* Blank children behave like spreadsheet blanks: once at least one child
  has a numeric value, missing / not-disclosed siblings contribute zero.
  If no child has a numeric value, the parent stays blank.

The algorithm runs once per turn at the coordinator boundary; it is
not meant to fire on every individual fact write.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Monetary tolerance for cascade convergence and residual detection.
# Values are money in the statement's reporting unit, so equality must be
# judged at cent scale — never at sub-ULP 1e-6. For figures in the millions
# 1e-6 absolute is below float64 precision, so accumulated rounding error
# could flip `changed` forever (burning all passes) or raise a phantom
# partial_state residual. We round every accumulated sum to cents and
# compare with a cent-scaled tolerance instead.
_MONEY_TOLERANCE = 0.01


def _money(value: float) -> float:
    """Round an accumulated monetary sum to cents."""
    return round(float(value), 2)


def _value_json(value) -> str:
    """JSON-encode a {"value": ...} payload safely (handles None/NaN/inf)."""
    if value is not None:
        value = float(value)
        if not math.isfinite(value):  # NaN / inf — invalid JSON
            value = None
    return json.dumps({"value": value})


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def recompute_after_turn(db_path: str | Path, run_id: int) -> None:
    """Topologically walk COMPUTED concepts and update facts.

    The implementation is deliberately straightforward: we repeatedly
    scan COMPUTED concepts whose children all have facts and update
    them, stopping when a full pass produces no changes.  For SOFP-
    sized templates (~75 rows / sheet) this is more than fast enough;
    if we ever hit hundreds-of-thousands of concepts we can switch to
    Kahn's algorithm with an explicit indegree table.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    # recompute_after_turn now runs on the per-tool verify path (item 32), so
    # it races concurrent agents' project_writes commits on the shared run DB.
    # Match the writer's retry window (cell_resolver / facts_api both set 5000)
    # so a collision waits rather than raising an immediate "database is locked".
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        # Collect every (period, entity_scope) tuple that has any fact
        # for this run — we recompute per tuple independently so a
        # Group filing's Company-side cascade doesn't leak into Group.
        scope_pairs = conn.execute(
            "SELECT DISTINCT period, entity_scope FROM run_concept_facts "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchall()

        for period, entity_scope in scope_pairs:
            _recompute_scope(conn, run_id, period, entity_scope)
        conn.commit()
    finally:
        conn.close()


def _recompute_scope(
    conn: sqlite3.Connection,
    run_id: int,
    period: str,
    entity_scope: str,
) -> None:
    # Recomputable concept rows, keyed by uuid. A concept is recomputable
    # when it owns a formula — i.e. has outgoing edges. For linear
    # statements that's every COMPUTED row. For SOCIE matrix templates the
    # totals (Total increase, Equity at end, the X total column) are stored
    # as MATRIX_CELL with edges, NOT COMPUTED; selecting only COMPUTED would
    # leave those totals blank/stale even though their edges exist. Gate on
    # edge-presence (not kind) so a data-entry MATRIX_CELL — a component
    # movement with no formula — is still treated as a leaf and left alone.
    computed = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT n.concept_uuid, n.canonical_label FROM concept_nodes n "
            "WHERE n.kind = 'COMPUTED' "
            "OR (n.kind = 'MATRIX_CELL' AND EXISTS("
            "    SELECT 1 FROM concept_edges e "
            "    WHERE e.parent_uuid = n.concept_uuid))"
        ).fetchall()
    }
    # All edges, grouped by parent.
    edges_by_parent: dict[str, list[tuple[str, float]]] = {}
    for parent_uuid, child_uuid, coef in conn.execute(
        "SELECT parent_uuid, child_uuid, coefficient FROM concept_edges"
    ).fetchall():
        edges_by_parent.setdefault(parent_uuid, []).append(
            (child_uuid, float(coef))
        )

    # Preload every fact for this scope ONCE, keyed by concept_uuid, and read
    # from this dict inside the fixed-point loop instead of issuing a SELECT
    # per parent + per child on every pass. The loop's own writes update this
    # dict in lock-step (same connection, same transaction) so a later pass
    # sees an earlier pass's recomputed parent — identical semantics to the
    # old per-row reads, minus the O(passes × parents × children) query storm.
    facts: dict[str, dict] = {
        r[0]: {
            "value": r[1],
            "value_status": r[2],
            "children_status": r[3],
            "source": r[4],
        }
        for r in conn.execute(
            "SELECT concept_uuid, value, value_status, children_status, source "
            "FROM run_concept_facts WHERE run_id = ? AND period = ? "
            "AND entity_scope = ?",
            (run_id, period, entity_scope),
        ).fetchall()
    }

    # Iterate to a fixed point — each pass aggregates parents whose
    # children are all known.
    changed = True
    max_passes = 50
    while changed and max_passes > 0:
        changed = False
        max_passes -= 1
        for parent_uuid in list(computed.keys()):
            edges = edges_by_parent.get(parent_uuid)
            if not edges:
                continue
            parent_fact = facts.get(parent_uuid)
            # aggregate_only ⇒ parent value is authoritative; skip.
            if parent_fact and parent_fact["children_status"] == "aggregate_only":
                continue

            # Gather child values. Blank spreadsheet cells contribute zero
            # to formulas, but a parent with no numeric child at all should
            # stay blank. Track whether at least one child has a number so
            # partially-filled review edits still update their totals without
            # turning an entirely empty section into a visible zero.
            total = 0.0
            missing = False
            has_numeric_child = False
            for child_uuid, coef in edges:
                row = facts.get(child_uuid)
                if row is None or row["value"] is None:
                    missing = True
                    continue
                has_numeric_child = True
                total += coef * float(row["value"])
            total = _money(total)

            # "observed" means a genuine external observation (an agent read
            # the total off the PDF, or a user typed it) — the cascade must
            # preserve it and flag a residual rather than silently overwrite.
            # But the cascade ALSO writes parents with value_status='observed'
            # (source='cascade'). Treating its own prior write as authoritative
            # would lock the total: after the first recompute the parent would
            # refuse to update when children change and instead raise a phantom
            # partial_state conflict. So a cascade-sourced value is never an
            # authoritative observation — it's free to re-derive.
            parent_observed = (
                parent_fact is not None
                and parent_fact["value_status"] == "observed"
                and parent_fact["value"] is not None
                and parent_fact["source"] != "cascade"
            )
            if missing and not parent_observed and not has_numeric_child:
                # No numeric child exists beneath this formula.
                existing_value = parent_fact["value"] if parent_fact else None
                if existing_value is None:
                    # Parent has no value yet — keep it blank. Don't publish a
                    # spurious 0 for an untouched section. Once one child has a
                    # number, the blank siblings are spreadsheet-zeroes and the
                    # parent can publish.
                    continue
                # Parent WAS computed but the children that drove it have since
                # been cleared — blank the parent too rather than leaving a
                # stale total (review UI: removing all values must clear the
                # dependent total). Journal it and close any open partial_state.
                now = _now()
                conn.execute(
                    "UPDATE run_concept_facts SET value = NULL, "
                    "value_status = 'not_disclosed', updated_at = ? "
                    "WHERE run_id = ? AND concept_uuid = ? AND period = ? "
                    "AND entity_scope = ?",
                    (now, run_id, parent_uuid, period, entity_scope),
                )
                conn.execute(
                    """
                    INSERT INTO concept_fact_events(
                        run_id, concept_uuid, period, entity_scope,
                        actor, turn, ts, before_json, after_json
                    ) VALUES (?, ?, ?, ?, 'cascade', NULL, ?, ?, ?)
                    """,
                    (
                        run_id, parent_uuid, period, entity_scope, now,
                        _value_json(existing_value),
                        _value_json(None),
                    ),
                )
                conn.execute(
                    "UPDATE run_concept_conflicts SET status = 'resolved', "
                    "resolved_at = ? WHERE run_id = ? AND concept_uuid = ? "
                    "AND period = ? AND entity_scope = ? "
                    "AND kind = 'partial_state' AND status = 'open'",
                    (now, run_id, parent_uuid, period, entity_scope),
                )
                # Keep the in-memory snapshot in lock-step with the DB write
                # so a later pass / parent sees the blanked value.
                if parent_fact is not None:
                    parent_fact["value"] = None
                    parent_fact["value_status"] = "not_disclosed"
                changed = True
                continue

            # Partial-state guard: parent has an observed value already
            # and it differs from the recomputed total (with missing
            # children counted as zero).
            if parent_observed and abs(_money(parent_fact["value"]) - total) > _MONEY_TOLERANCE:
                residual = _money(_money(parent_fact["value"]) - total)
                # Dedupe (peer-review #7): a recompute that runs every
                # turn must not stack duplicate open conflicts for the
                # same unresolved residual.  If an open partial_state
                # conflict already exists for this (run, concept,
                # period, scope), update its residual in place instead
                # of inserting a fresh row.
                existing = conn.execute(
                    "SELECT id FROM run_concept_conflicts WHERE run_id = ? "
                    "AND concept_uuid = ? AND period = ? AND entity_scope = ? "
                    "AND kind = 'partial_state' AND status = 'open'",
                    (run_id, parent_uuid, period, entity_scope),
                ).fetchone()
                detail = (
                    f"observed parent={parent_fact['value']} but children "
                    f"sum to {total}; residual={residual}"
                )
                if existing is not None:
                    conn.execute(
                        "UPDATE run_concept_conflicts SET residual = ?, "
                        "detail = ? WHERE id = ?",
                        (residual, detail, existing[0]),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO run_concept_conflicts(
                            run_id, concept_uuid, period, entity_scope,
                            kind, residual, detail, status, created_at
                        ) VALUES (?, ?, ?, ?, 'partial_state', ?, ?, 'open', ?)
                        """,
                        (
                            run_id, parent_uuid, period, entity_scope,
                            residual, detail, _now(),
                        ),
                    )
                # We do NOT overwrite the observed parent — the user
                # decides which side to trust via the reconciliation
                # queue.
                continue

            # Reaching here means the parent is about to be (re)computed from
            # its children — either it's an observation that now reconciles
            # (the residual branch above didn't fire), or it's cascade-owned
            # (parent_observed is False). In BOTH cases any open partial_state
            # conflict on this parent is now stale: a correction-agent leaf fix
            # may have cleared an observed imbalance, or — for cascade-owned
            # totals — the conflict was a phantom that the old self-lock bug
            # raised against the cascade's own prior write. Close it so the
            # reconciliation queue reflects reality (peer-review + self-lock fix).
            conn.execute(
                "UPDATE run_concept_conflicts SET status = 'resolved', "
                "resolved_at = ? WHERE run_id = ? AND concept_uuid = ? "
                "AND period = ? AND entity_scope = ? "
                "AND kind = 'partial_state' AND status = 'open'",
                (_now(), run_id, parent_uuid, period, entity_scope),
            )

            # Write the recomputed value back.  If we wrote a row that
            # didn't exist before, mark it changed so the outer loop
            # keeps walking.
            now = _now()
            existing_value = parent_fact["value"] if parent_fact else None
            if existing_value is None or abs(_money(existing_value) - total) > _MONEY_TOLERANCE:
                conn.execute(
                    """
                    INSERT INTO run_concept_facts(
                        run_id, concept_uuid, period, entity_scope, value,
                        value_status, children_status, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'observed', 'itemised',
                              'cascade', ?)
                    ON CONFLICT(run_id, concept_uuid, period, entity_scope)
                    DO UPDATE SET value = excluded.value,
                                  updated_at = excluded.updated_at
                    """,
                    (
                        run_id, parent_uuid, period, entity_scope,
                        total, now,
                    ),
                )
                # Audit-log invariant (peer-review #8): every fact change
                # is journaled, including cascade-driven recomputes.  The
                # facts API does this for agent writes; the cascade must
                # match so "what changed this value" is always traceable.
                conn.execute(
                    """
                    INSERT INTO concept_fact_events(
                        run_id, concept_uuid, period, entity_scope,
                        actor, turn, ts, before_json, after_json
                    ) VALUES (?, ?, ?, ?, 'cascade', NULL, ?, ?, ?)
                    """,
                    (
                        run_id, parent_uuid, period, entity_scope, now,
                        None if existing_value is None
                        else _value_json(existing_value),
                        _value_json(total),
                    ),
                )
                # Keep the in-memory snapshot in lock-step with the DB write.
                # On insert (new row) the SQL stamps observed/itemised/cascade;
                # on conflict it only updates the value — mirror both so a
                # later pass reads the same state the DB now holds.
                if parent_fact is None:
                    facts[parent_uuid] = {
                        "value": total,
                        "value_status": "observed",
                        "children_status": "itemised",
                        "source": "cascade",
                    }
                else:
                    parent_fact["value"] = total
                changed = True

    # If we ran out of passes while still making changes, the formula graph
    # likely contains a cycle (import should reject these, but INSERT OR IGNORE
    # on concept_edges does not detect them). Values are left half-propagated;
    # surface it rather than silently capping.
    if changed and max_passes == 0:
        logger.warning(
            "cascade.recompute hit max_passes for run=%s period=%s scope=%s "
            "— possible cycle in concept_edges; totals may be incomplete",
            run_id, period, entity_scope,
        )
