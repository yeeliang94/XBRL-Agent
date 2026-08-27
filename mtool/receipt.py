"""Durable record of every mTool fill (Step 19, finding 6).

A filled MBRS workbook is a regulatory artifact. Until this landed, producing
one left no trace — and because a completed run stays editable (facts can be
corrected, notes re-reviewed), two fills of "the same" run could differ with
nothing to show which was which.

One row per fill answers: *which run, at which revision of its data, filled
into which template, producing which file, under whose hand, and what did the
readiness gate say at the time.*

Two invariants:

* **The patcher stays stateless.** ``mtool/offline_fill.py`` is a stdlib-only
  file that also travels to the Windows box; it must never learn about a DB.
  The route writes the receipt, not the patcher.
* **No receipt, no artifact.** ``write_fill_receipt`` RAISES on a write
  failure and the route refuses to release the workbook without a durable
  receipt (peer review, 2026-08-05 — the original "log and swallow" posture
  meant a filing artifact could exist with no trace of what produced it,
  which defeats the table's purpose). The download STAMP
  (``record_receipt_download``) stays best-effort: by then the receipt row
  exists, and a failed timestamp update must not block a file the operator
  is entitled to.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("server")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot_facts(
    db_path: str | Path,
    run_id: int,
    *,
    filing_standard: str,
    filing_level: str,
) -> tuple[list[sqlite3.Row], dict[str, Any]]:
    """Read a run's NUMERIC facts once, and return an identity for that revision.

    ``{"fact_count", "digest", "max_updated_at"}`` — the digest is a hash over
    the ordered ``(concept, period, scope, value, status)`` tuples, so any edit
    between two fills produces a different digest even when the counts match.

    Scope is ``run_concept_facts`` and nothing else. The prose notes live in
    ``notes_cells`` and are read by a separate connection later in the request
    (``mtool.notes_exporter.build_notes_fill_doc``), so this digest cannot
    speak for them — it once claimed to, and a notes edit between the two reads
    produced a workbook whose prose no receipt described. The notes carry their
    OWN revision identity (``meta.notes_snapshot``), and the receipt records
    both (schema v39). Two digests, because there are two reads.
    """
    family_prefix = f"{filing_standard.lower()}-{filing_level.lower()}-"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # One transaction => one consistent view of run_concept_facts for the
        # whole read, rather than two independent point-in-time reads.
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT f.concept_uuid, f.period, f.entity_scope, f.value,
                   f.value_status, f.updated_at,
                   n.canonical_label, n.kind, n.render_sheet, n.render_row,
                   n.matrix_col, n.matrix_col_label, n.template_id,
                   tpl.shape AS shape,
                   t.target_sheet, t.target_row, t.target_col,
                   sa.primary_concept, sa.dimensions_json,
                   sa.taxonomy_version, sa.address_version,
                   EXISTS(
                     SELECT 1 FROM concept_edges e
                     WHERE e.parent_uuid = n.concept_uuid
                   ) AS has_formula_edges
            FROM run_concept_facts f
            JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
            JOIN concept_templates tpl ON tpl.template_id = n.template_id
            LEFT JOIN concept_targets t
              ON t.concept_uuid = f.concept_uuid
             AND t.period = f.period
             AND t.entity_scope = f.entity_scope
            LEFT JOIN concept_semantic_addresses sa
              ON sa.concept_uuid = f.concept_uuid
            WHERE f.run_id = ? AND n.template_id LIKE ?
            ORDER BY n.render_sheet, n.render_row, f.entity_scope, f.period
            """,
            (run_id, family_prefix + "%"),
        ).fetchall()
        conn.commit()
    finally:
        conn.close()

    h = hashlib.sha256()
    max_updated = ""
    for r in rows:
        h.update(
            f"{r['concept_uuid']}|{r['period']}|{r['entity_scope']}|"
            f"{r['value']}|{r['value_status']}\n".encode("utf-8"))
        if (r["updated_at"] or "") > max_updated:
            max_updated = r["updated_at"] or ""
    identity = {
        "fact_count": len(rows),
        "digest": h.hexdigest(),
        "max_updated_at": max_updated or None,
    }
    return rows, identity


# Operator free text (the preflight override, the degraded-download
# acknowledgement) is stored verbatim so the audit trail keeps the operator's
# own words. Bound it anyway: it arrives from a form field / query string with
# no length of its own, and an audit column is not a place to put an unbounded
# body. 4 KB is far more than a reason sentence and far less than a problem.
ACK_TEXT_LIMIT = 4096
_TRUNCATION_SUFFIX = "… [truncated]"


def clamp_ack_text(text: str | None) -> str | None:
    """Bound an operator acknowledgement, marking it when it was cut."""
    if text is None:
        return None
    if len(text) <= ACK_TEXT_LIMIT:
        return text
    return text[:ACK_TEXT_LIMIT - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def write_fill_receipt(
    db_path: str | Path,
    *,
    run_id: int,
    snapshot: dict[str, Any],
    source_sha256: str | None,
    output_sha256: str | None,
    template_fingerprint: str | None,
    column_map: dict | None,
    translation_version: str | None,
    preflight: dict | None,
    preflight_override: str | None,
    operator: str | None,
    report: dict | None,
    notes_snapshot: dict[str, Any] | None = None,
) -> int:
    """Insert one receipt and return its id.

    ``snapshot`` identifies the numeric fact revision; ``notes_snapshot`` the
    prose one (``meta.notes_snapshot`` from the notes fill doc). Both are
    recorded because they come from two separate reads — see
    :func:`snapshot_facts`. ``None`` for the notes half means this fill wrote
    no prose, which is a different fact from "the prose was empty".

    RAISES on any write failure — the caller must treat a missing receipt as
    a failed fill and withhold the artifact (module docstring). Losing the
    audit trail silently is the one outcome this table exists to prevent.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO mtool_fill_receipts("
            "run_id, snapshot_fact_count, snapshot_digest, "
            "snapshot_max_updated, snapshot_notes_count, "
            "snapshot_notes_digest, snapshot_notes_updated, "
            "source_sha256, output_sha256, "
            "template_fingerprint, column_map_json, translation_version, "
            "preflight_json, preflight_override, status, report_json, "
            "operator, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                (snapshot or {}).get("fact_count"),
                (snapshot or {}).get("digest"),
                (snapshot or {}).get("max_updated_at"),
                (notes_snapshot or {}).get("notes_count"),
                (notes_snapshot or {}).get("digest"),
                (notes_snapshot or {}).get("max_updated_at"),
                source_sha256,
                output_sha256,
                template_fingerprint,
                json.dumps(column_map) if column_map is not None else None,
                translation_version,
                json.dumps(preflight) if preflight is not None else None,
                clamp_ack_text(preflight_override),
                (report or {}).get("status"),
                json.dumps(report) if report is not None else None,
                operator,
                _now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def record_receipt_download(
    db_path: str | Path,
    receipt_id: int,
    *,
    acknowledgement: str | None = None,
) -> None:
    """Stamp when the filled workbook was actually taken, and any degraded-fill
    acknowledgement given at that moment. Best-effort, like the insert."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if acknowledgement:
                conn.execute(
                    "UPDATE mtool_fill_receipts SET downloaded_at = ?, "
                    "degraded_ack = ? WHERE id = ?",
                    (_now(), clamp_ack_text(acknowledgement), receipt_id))
            else:
                conn.execute(
                    "UPDATE mtool_fill_receipts SET downloaded_at = ? "
                    "WHERE id = ?", (_now(), receipt_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.warning("mTool receipt download stamp failed for %s",
                       receipt_id, exc_info=True)


def fetch_receipts(db_path: str | Path, run_id: int) -> list[dict[str, Any]]:
    """Every receipt for a run, newest first (the run page's audit list)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Columns named rather than `SELECT *`: a rename then fails here, at
        # the query, instead of as a KeyError while building the response.
        rows = conn.execute(
            "SELECT id, run_id, created_at, downloaded_at, operator, status, "
            "source_sha256, output_sha256, template_fingerprint, "
            "translation_version, snapshot_fact_count, snapshot_digest, "
            "snapshot_max_updated, snapshot_notes_count, "
            "snapshot_notes_digest, snapshot_notes_updated, column_map_json, "
            "preflight_json, preflight_override, degraded_ack, report_json "
            "FROM mtool_fill_receipts WHERE run_id = ? "
            "ORDER BY id DESC", (run_id,)).fetchall()
    finally:
        conn.close()

    def _decode(raw):
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    return [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "downloaded_at": r["downloaded_at"],
            "operator": r["operator"],
            "status": r["status"],
            "source_sha256": r["source_sha256"],
            "output_sha256": r["output_sha256"],
            "template_fingerprint": r["template_fingerprint"],
            "translation_version": r["translation_version"],
            "snapshot": {
                "fact_count": r["snapshot_fact_count"],
                "digest": r["snapshot_digest"],
                "max_updated_at": r["snapshot_max_updated"],
            },
            # The prose revision (v39). `digest: None` = this fill wrote no
            # notes, or the receipt predates the column.
            "notes_snapshot": {
                "notes_count": r["snapshot_notes_count"],
                "digest": r["snapshot_notes_digest"],
                "max_updated_at": r["snapshot_notes_updated"],
            },
            "column_map": _decode(r["column_map_json"]),
            "preflight": _decode(r["preflight_json"]),
            "preflight_override": r["preflight_override"],
            "degraded_ack": r["degraded_ack"],
            "report": _decode(r["report_json"]),
        }
        for r in rows
    ]


__all__ = ["snapshot_facts", "write_fill_receipt", "record_receipt_download",
           "fetch_receipts", "clamp_ack_text", "ACK_TEXT_LIMIT"]
