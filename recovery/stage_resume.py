"""Stage-level resume: reuse a failed run's completed statements — Phase 4A.

A resume never reopens the parent run. It creates a NEW child run (the
parent stays terminal and immutable, per the run-lifecycle contract), copies
forward only the parent's explicitly-reusable statement outputs, and reruns
the rest through the completely normal pipeline. Durable domain state — the
canonical facts in ``run_concept_facts``, per-agent statuses, the on-disk
per-statement workbooks — is the restart anchor; conversation state is never
resumed here (that is Phase 4B research).

The decision logic is pure functions over the audit DB so every eligibility
rule is unit-testable without the pipeline (the plan's Step 4A.1 rule).
Staging is one transaction: a failure copies nothing (drill: "copied fact
failure rolls back the child staging transaction").

Face statements only in this release. Notes are OUT OF SCOPE entirely —
never reused (their cells carry human edits and tombstones whose reuse
semantics are unresolved) and not rerun by the resume child either; the CLI
preview says so explicitly when the parent had notes.

Reviewed-state caveat: a parent that ran the reviewer has REVIEWED facts as
its final state. v1 copies that final state and says so in each copied
fact's provenance prefix and the plan's decision reason — there is no
"resume from raw extraction" mode yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FACE_STATEMENTS = ("SOFP", "SOPL", "SOCI", "SOCF", "SOCIE")

# Parent statuses a resume may start from. `running`/`draft` are refused —
# resuming a live run would race its own writers.
RESUMABLE_PARENT_STATUSES = {
    "failed", "aborted", "completed_with_errors", "completed",
}


def stage_resume_enabled() -> bool:
    """``XBRL_STAGE_RESUME`` (default off), read at call time."""
    return os.environ.get(
        "XBRL_STAGE_RESUME", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def compute_source_sha256(pdf_path: str | Path) -> str:
    """Hash of the parent's kept ``uploaded.pdf``.

    No hash is recorded at upload time (the file itself is the identity
    anchor, gotcha #29) — so resume computes it lazily from the parent's
    output dir and stores it on the lineage row for the audit trail.
    """
    h = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def template_prefix_for(
    filing_standard: str, filing_level: str, statement: str,
) -> str:
    """The concept-template id prefix for one statement in one family.

    Template ids are ``{std}-{level}-{stmt}[-{variant}]-v1`` (e.g.
    ``mfrs-company-sofp-cunoncu-v1``, ``mfrs-company-socie-v1``), so the
    prefix through the statement segment scopes both the family AND the
    statement — a Group or MPERS node can never match a Company-MFRS prefix
    (drill: cross-family copy impossible by construction).
    """
    return f"{filing_standard.lower()}-{filing_level.lower()}-{statement.lower()}-"


@dataclass
class StatementDecision:
    statement: str
    variant: Optional[str]
    decision: str          # "reuse" | "rerun"
    reason: str


@dataclass
class ResumePlan:
    parent_run_id: int
    filing_standard: str = "mfrs"
    filing_level: str = "company"
    parent_status: str = ""
    parent_output_dir: str = ""
    parent_config: dict = field(default_factory=dict)
    parent_model: Optional[str] = None
    source_pdf: Optional[str] = None
    decisions: list[StatementDecision] = field(default_factory=list)
    refusal: Optional[str] = None      # set ⇒ the whole resume is refused
    parent_had_reviewer_writes: bool = False

    @property
    def reused(self) -> list[StatementDecision]:
        return [d for d in self.decisions if d.decision == "reuse"]

    @property
    def rerun(self) -> list[StatementDecision]:
        return [d for d in self.decisions if d.decision == "rerun"]


def build_resume_plan(
    db_path: str | Path,
    parent_run_id: int,
    *,
    expected_standard: Optional[str] = None,
    expected_level: Optional[str] = None,
) -> ResumePlan:
    """Pure decision pass: what is reusable, what must rerun, or why not.

    ``expected_standard`` / ``expected_level`` are identity guards for
    callers that carry their own filing configuration — a mismatch refuses
    the whole resume (drill: source/config mismatch is refused). The CLI
    resumes from the parent's own config, so it passes neither.
    """
    plan = ResumePlan(parent_run_id=parent_run_id)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT status, output_dir, run_config_json FROM runs WHERE id=?",
            (parent_run_id,)).fetchone()
        if run is None:
            plan.refusal = f"run {parent_run_id} does not exist"
            return plan
        plan.parent_status = run["status"] or ""
        plan.parent_output_dir = run["output_dir"] or ""
        if plan.parent_status not in RESUMABLE_PARENT_STATUSES:
            plan.refusal = (
                f"parent status {plan.parent_status!r} is not resumable "
                f"(needs one of {sorted(RESUMABLE_PARENT_STATUSES)})")
            return plan
        try:
            cfg = json.loads(run["run_config_json"] or "{}")
        except Exception:
            cfg = {}
        plan.parent_config = cfg
        plan.filing_standard = (cfg.get("filing_standard") or "mfrs").lower()
        plan.filing_level = (cfg.get("filing_level") or "company").lower()
        if expected_standard and expected_standard.lower() != plan.filing_standard:
            plan.refusal = (
                f"filing standard mismatch: parent is {plan.filing_standard}, "
                f"requested {expected_standard.lower()}")
            return plan
        if expected_level and expected_level.lower() != plan.filing_level:
            plan.refusal = (
                f"filing level mismatch: parent is {plan.filing_level}, "
                f"requested {expected_level.lower()}")
            return plan

        pdf = Path(plan.parent_output_dir or "") / "uploaded.pdf"
        if not plan.parent_output_dir or not pdf.exists():
            plan.refusal = (
                "parent's uploaded.pdf is missing — the source document is "
                "the identity anchor; without it nothing can be safely "
                "reused or rerun")
            return plan
        plan.source_pdf = str(pdf)

        # Reviewer-modified state? (run_fact_snapshots rows exist only once
        # the reviewer snapshotted before its first write.)
        plan.parent_had_reviewer_writes = bool(conn.execute(
            "SELECT 1 FROM run_fact_snapshots WHERE run_id=? LIMIT 1",
            (parent_run_id,)).fetchone())

        selected = [s for s in (cfg.get("statements") or [])
                    if s in FACE_STATEMENTS]
        agents = {
            r["statement_type"]: r for r in conn.execute(
                "SELECT statement_type, variant, status, model, workbook_path "
                "FROM run_agents WHERE run_id=? ORDER BY id",
                (parent_run_id,)).fetchall()
        }
        if agents:
            models = [r["model"] for r in agents.values() if r["model"]]
            plan.parent_model = models[0] if models else None

        for stmt in selected:
            row = agents.get(stmt)
            variant = (cfg.get("variants") or {}).get(stmt) or (
                row["variant"] if row else None)
            if row is None:
                plan.decisions.append(StatementDecision(
                    stmt, variant, "rerun", "never started in the parent"))
                continue
            if row["status"] != "succeeded":
                plan.decisions.append(StatementDecision(
                    stmt, variant, "rerun",
                    f"parent agent ended {row['status']!r}"))
                continue
            prefix = template_prefix_for(
                plan.filing_standard, plan.filing_level, stmt)
            n_facts = conn.execute(
                "SELECT count(*) FROM run_concept_facts f "
                "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
                "WHERE f.run_id=? AND n.template_id LIKE ?",
                (parent_run_id, prefix + "%")).fetchone()[0]
            if not n_facts:
                plan.decisions.append(StatementDecision(
                    stmt, variant, "rerun",
                    "succeeded but left no canonical facts"))
                continue
            reason = f"succeeded with {n_facts} canonical facts"
            if plan.parent_had_reviewer_writes:
                reason += " (reviewed state — the parent's final facts)"
            plan.decisions.append(StatementDecision(
                stmt, variant, "reuse", reason))
    finally:
        conn.close()
    return plan


def stage_resume(
    db_path: str | Path,
    plan: ResumePlan,
    child_run_id: int,
    child_dir: str | Path,
) -> dict:
    """Copy the plan's reusable outputs into the child run. One transaction.

    * Facts: new child-run rows (never shared), provenance-prefixed
      ``resume:run_{parent}:{original_source}``.
    * run_agents: one ``succeeded`` row per reused statement so the
      download-time re-export (which reads DB rows) includes them.
    * Workbooks: the parent's ``{STMT}_filled.xlsx`` copied into the child
      dir when present (the canonical exporter re-renders from facts anyway).
    * Lineage: one ``run_lineage`` row with the source hash.

    Any DB failure rolls the whole staging back — the child ends with zero
    copied facts and no lineage, never a partial copy.
    """
    if plan.refusal:
        raise ValueError(f"refused plan cannot be staged: {plan.refusal}")
    child_dir = Path(child_dir)
    source_sha = compute_source_sha256(plan.source_pdf)

    copied_facts = 0
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        for d in plan.reused:
            prefix = template_prefix_for(
                plan.filing_standard, plan.filing_level, d.statement)
            cur = conn.execute(
                "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
                "entity_scope, value, value_status, children_status, source, "
                "evidence, updated_at) "
                "SELECT ?, f.concept_uuid, f.period, f.entity_scope, f.value, "
                "f.value_status, f.children_status, "
                "'resume:run_' || ? || ':' || COALESCE(f.source, ''), "
                "f.evidence, ? "
                "FROM run_concept_facts f "
                "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
                "WHERE f.run_id = ? AND n.template_id LIKE ?",
                (child_run_id, plan.parent_run_id, _now(),
                 plan.parent_run_id, prefix + "%"))
            copied_facts += cur.rowcount
            conn.execute(
                "INSERT INTO run_agents(run_id, statement_type, variant, "
                "model, status, started_at, ended_at, workbook_path) "
                "VALUES (?,?,?,?,'succeeded',?,?,?)",
                (child_run_id, d.statement, d.variant,
                 f"reused:run_{plan.parent_run_id}",
                 _now(), _now(),
                 str(child_dir / f"{d.statement}_filled.xlsx")))
        conn.execute(
            "INSERT INTO run_lineage(child_run_id, parent_run_id, "
            "source_sha256, reused_statements, rerun_statements, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (child_run_id, plan.parent_run_id, source_sha,
             json.dumps([f"{d.statement}/{d.variant or ''}"
                         for d in plan.reused]),
             json.dumps([f"{d.statement}/{d.variant or ''}"
                         for d in plan.rerun]),
             _now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # File copies AFTER the durable staging committed — a missing scratch
    # workbook is not fatal (the exporter re-renders from the copied facts).
    workbooks_copied = []
    for d in plan.reused:
        src = Path(plan.parent_output_dir) / f"{d.statement}_filled.xlsx"
        if src.exists():
            child_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, child_dir / src.name)
            workbooks_copied.append(d.statement)

    return {
        "copied_facts": copied_facts,
        "workbooks_copied": workbooks_copied,
        "source_sha256": source_sha,
        "reused": [d.statement for d in plan.reused],
        "rerun": [d.statement for d in plan.rerun],
    }
