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
    """Read a run's facts ONCE, inside a single consistent read.

    The numeric figures and the prose notes used to be read at different
    moments in the request; between them a reviewer could change a fact, so the
    workbook wouldn't correspond to any single revision of the data. This reads
    the fact set in one deferred transaction and returns it together with an
    identity for that revision:

    ``{"fact_count", "digest", "max_updated_at"}`` — the digest is a hash over
    the ordered ``(concept, period, scope, value, status)`` tuples, so any edit
    between two fills produces a different digest even when the counts match.
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
                   n.template_id,
                   tpl.shape AS shape
            FROM run_concept_facts f
            JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
            JOIN concept_templates tpl ON tpl.template_id = n.template_id
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
) -> int:
    """Insert one receipt and return its id.

    RAISES on any write failure — the caller must treat a missing receipt as
    a failed fill and withhold the artifact (module docstring). Losing the
    audit trail silently is the one outcome this table exists to prevent.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO mtool_fill_receipts("
            "run_id, snapshot_fact_count, snapshot_digest, "
            "snapshot_max_updated, source_sha256, output_sha256, "
            "template_fingerprint, column_map_json, translation_version, "
            "preflight_json, preflight_override, status, report_json, "
            "operator, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                (snapshot or {}).get("fact_count"),
                (snapshot or {}).get("digest"),
                (snapshot or {}).get("max_updated_at"),
                source_sha256,
                output_sha256,
                template_fingerprint,
                json.dumps(column_map) if column_map is not None else None,
                translation_version,
                json.dumps(preflight) if preflight is not None else None,
                preflight_override,
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
                    (_now(), acknowledgement, receipt_id))
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
        rows = conn.execute(
            "SELECT * FROM mtool_fill_receipts WHERE run_id = ? "
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
            "column_map": _decode(r["column_map_json"]),
            "preflight": _decode(r["preflight_json"]),
            "preflight_override": r["preflight_override"],
            "degraded_ack": r["degraded_ack"],
            "report": _decode(r["report_json"]),
        }
        for r in rows
    ]


__all__ = ["snapshot_facts", "write_fill_receipt", "record_receipt_download",
           "fetch_receipts"]
