"""XBRL Agent — FastAPI web server with SSE streaming.

Provides a web UI for uploading PDFs, running extraction agents,
and streaming progress events in real-time via Server-Sent Events.

Run mode: POST /api/run/{session_id} with RunConfigRequest body.
Orchestrates N sub-agents via coordinator, merges workbooks, runs
cross-checks, and persists results to SQLite audit DB.
"""
# Force UTF-8 on Windows (avoids charmap codec errors with Unicode text from PDFs)
import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Trust the OS certificate store for SSL verification. Required on Windows
# behind corporate MITM proxies (e.g. genai-sharedservice-emea.pwc.com) whose
# root CA is installed system-wide but absent from certifi's bundle. Must
# run before any TLS connection is opened. Idempotent; no-op on Py<3.10.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import (
    ThreadPoolExecutor as _CCThreadPoolExecutor,
    # On Python 3.10, concurrent.futures.TimeoutError is NOT the builtin
    # TimeoutError (they were only aliased in 3.11) — `except TimeoutError`
    # alone misses a Future.result(timeout=...) expiry there. Catch both
    # everywhere the executor pattern occurs (harmless no-op on >= 3.11).
    TimeoutError as _FuturesTimeoutError,
)
from contextlib import asynccontextmanager
from pathlib import Path
from typing import (
    AsyncIterator, Callable, Dict, List, Literal, NamedTuple, Optional, Set, Any,
)

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

# Suppress LiteLLM SSL warnings (enterprise firewall blocks GitHub pricing fetch)
try:
    import litellm
    litellm.suppress_debug_info = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
# Load .env into the process environment at import time so startup-time reads
# — the lifespan handler's canonical bootstrap, `/api/config`, and settings
# like XBRL_AUTO_REVIEW / XBRL_DEFAULT_MODELS — see what the user set in .env.
# Without this, those were only loaded inside individual request handlers
# (override=True), so the Concepts UI never lit up from .env alone.
# override=False keeps an explicitly exported shell var winning over .env.
# Loaded BEFORE OUTPUT_DIR below so XBRL_OUTPUT_DIR can be set via .env too,
# not only as a real env var / Azure App Setting.
load_dotenv(ENV_FILE, override=False)
# Where runs, uploads, filled workbooks and the audit DB live. Defaults to
# ./output next to the code (CLAUDE.md gotcha #9). On Azure App Service only
# /home survives restarts, so XBRL_OUTPUT_DIR=/home/data points all durable
# state at persistent storage (PLAN auth/deploy Phase 3). An explicit override
# wins; otherwise the historical default is unchanged.
OUTPUT_DIR = Path(os.environ.get("XBRL_OUTPUT_DIR") or (BASE_DIR / "output"))
CONFIG_DIR = BASE_DIR / "config"
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
# Shared SQLite audit store (one file per installation, grows over time).
AUDIT_DB_PATH = OUTPUT_DIR / "xbrl_agent.db"

# Canonical-mode health: set in the lifespan handler after the face-template
# bootstrap runs. None = not yet attempted (or canonical mode off); True =
# trees imported; False = import failed (Concepts UI will be empty, fact
# projection will no-op). Used to surface a clear error instead of a silent
# empty run (peer-review HIGH, finding 1).
_CANONICAL_BOOTSTRAP_OK: Optional[bool] = None


def _canonical_mode_enabled() -> bool:
    """Canonical mode is now MANDATORY (rewrite Phase 1, step 1.1).

    The concept-model pipeline — extraction fact-projection (Phase B), the
    DB-backed exporter (Phase C), and the reviewer pass (Phase D,
    correction/reviewer_agent.py) — is the only pipeline. The legacy direct-
    xlsx path and its `XBRL_CANONICAL_MODE` opt-out have been removed. This
    helper is retained as a single always-true seam so the many call sites
    don't have to be inlined in one commit; it will dissolve in the Phase 5
    server decomposition.
    """
    return True


def _canonical_facts_enabled() -> bool:
    """True when the startup concept-tree bootstrap succeeded — i.e. fact
    projection can resolve concepts.

    Bootstrap failure is now FATAL (fail-fast contract, rewrite Phase 1.1):
    `run_multi_agent_stream` refuses to start a run when
    ``_CANONICAL_BOOTSTRAP_OK is False`` rather than silently degrading to a
    correction-less run (the old behaviour, when the legacy xlsx path was the
    implicit fallback). So at extraction time this is effectively always True;
    it stays a function because `/api/config` and a few routes report it.
    """
    return _CANONICAL_BOOTSTRAP_OK is not False


def _in_tokens(u) -> int:
    """Prompt/input token count from a pydantic-ai Usage, version-tolerant.

    pydantic-ai renamed ``request_tokens`` → ``input_tokens`` (the old names
    emit DeprecationWarnings). Read the new name first, fall back to the old
    one so we work across versions. (PR-3.)
    """
    val = getattr(u, "input_tokens", None)
    if val is None:
        val = getattr(u, "request_tokens", 0)
    return int(val or 0)


def _out_tokens(u) -> int:
    """Completion/output token count from a pydantic-ai Usage (see _in_tokens)."""
    val = getattr(u, "output_tokens", None)
    if val is None:
        val = getattr(u, "response_tokens", 0)
    return int(val or 0)


def _reporting_periods_from_infopack(infopack) -> tuple[Optional[str], Optional[str]]:
    """Pull (reporting_period_cy, reporting_period_py) from a scout Infopack.

    Accepts an ``Infopack`` instance OR the serialised dict persisted in
    ``runs.run_config_json['infopack']`` (download/recheck paths only have the
    dict). Returns (None, None) when scout didn't run or didn't capture dates —
    the exporter then falls back to the agent's scratch workbook.
    """
    if infopack is None:
        return None, None
    if isinstance(infopack, dict):
        cy = infopack.get("reporting_period_cy")
        py = infopack.get("reporting_period_py")
    else:
        cy = getattr(infopack, "reporting_period_cy", None)
        py = getattr(infopack, "reporting_period_py", None)
    cy = cy if isinstance(cy, str) and cy.strip() else None
    py = py if isinstance(py, str) and py.strip() else None
    return cy, py


def _export_canonical_workbooks(
    *,
    run_id,
    agent_results,
    all_workbook_paths,
    session_dir,
    filing_level: str,
    filing_standard: str,
    db_path,
    reporting_period_cy: Optional[str] = None,
    reporting_period_py: Optional[str] = None,
    event_sink: Optional[Callable[[dict], None]] = None,
):
    """Phase C — replace agent-written workbooks with DB-exported ones.

    For every *succeeded* statement, copy its master template into the run's
    output dir and fill it from ``run_concept_facts`` via
    ``export_run_to_xlsx``, then repoint ``all_workbook_paths`` at the export
    so the merge (and the download) reflect the authoritative DB facts rather
    than the scratch xlsx the agent wrote.

    Best-effort per statement: if a single export fails we log it and leave
    that statement's agent workbook in place, so one bad template can't sink
    the whole download. Returns the list of StatementTypes actually exported.

    ``event_sink`` (optional): when an export RAISES and we silently keep the
    agent's scratch workbook, the download for that statement no longer
    reflects the DB facts (e.g. a reviewer edit). Callers on the live SSE path
    pass ``_enqueue_system_error`` so this degradation is surfaced loudly
    instead of only logged (gotcha #20 / Option C, 2026-06-03). The benign
    zero-fact fallback is NOT surfaced — the scratch workbook still carries
    values there, so the download isn't degraded.
    """
    import shutil
    from statement_types import template_path as _tpl_path
    from concept_model.exporter import export_run_to_xlsx
    from concept_model.parser import _derive_template_id

    exported = []
    for ar in agent_results:
        if ar.status != "succeeded":
            continue
        stmt = ar.statement_type
        try:
            master = _tpl_path(
                stmt, ar.variant, level=filing_level, standard=filing_standard,
            )
        except ValueError:
            # NotPrepared / standard-variant mismatch — nothing to export.
            continue
        canon_path = Path(session_dir) / f"{stmt.value}_canonical.xlsx"
        # Reporting-period dates are run-level metadata (same on every face
        # statement), not per-agent data — the exporter stamps them from the
        # scout period (``reporting_period_cy/py``), falling back to the agent's
        # scratch workbook on no-scout runs. Either way the download isn't left
        # with the "01/01/YYYY" placeholder.
        scratch_path = all_workbook_paths.get(stmt)
        try:
            shutil.copyfile(master, canon_path)
            applied = export_run_to_xlsx(
                db_path, run_id, canon_path,
                filing_level=filing_level,
                template_id=_derive_template_id(Path(master)),
                reporting_period_cy=reporting_period_cy,
                reporting_period_py=reporting_period_py,
                carry_forward_row1_from=scratch_path,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.exception(
                "canonical export failed for %s — keeping agent workbook",
                stmt.value,
            )
            if event_sink is not None:
                event_sink({
                    "type": "canonical_export_degraded",
                    "message": (
                        f"Could not re-export {stmt.value} from the corrected "
                        f"facts ({type(_exc).__name__}); its download will show "
                        f"the pre-review values until this is fixed. The "
                        f"Concepts/Values page still reflects the DB facts."
                    ),
                })
            continue
        # Peer-review finding 1: only repoint the download at the DB export
        # when it actually carries facts. A zero-fact export is a blank
        # template — keep the agent's scratch workbook (which has values)
        # rather than clobbering the download with an empty sheet. This is
        # benign + common (a statement with no canonical facts, a mocked run),
        # so it is NOT surfaced via event_sink — only a genuine export
        # exception (above) masks DB facts behind the scratch fallback.
        if applied <= 0:
            logger.warning(
                "canonical export for %s applied 0 facts — keeping agent "
                "workbook (Concepts UI may be incomplete for this statement)",
                stmt.value,
            )
            continue
        all_workbook_paths[stmt] = str(canon_path)
        exported.append(stmt)
    return exported


def _run_has_facts(db_path, run_id: int) -> bool:
    """True when the run has any canonical fact rows.

    The download re-export path keys on this: a run with facts has its
    authoritative values in the DB (including any user edits from the review
    UI). A run with none either predates the canonical store or had its
    projection fail; its on-disk merged workbook is treated as authoritative
    and left untouched.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(
                "SELECT 1 FROM run_concept_facts WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone() is not None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — a probe failure must not block download
        logger.warning("fact-presence probe failed for run %s", run_id,
                       exc_info=True)
        return False


def _reexport_and_remerge_from_facts(run_id: int) -> Optional[Path]:
    """Rebuild a run's merged workbook from the DB facts into a temp file.

    Phase 1.3 of the editable-review plan, and the structural fix for the
    "manual edit doesn't refresh the download" gap: the DB is the single
    source of truth, so every download rebuilds the face-statement workbooks
    from ``run_concept_facts`` (which already carries cascaded totals + any
    user edits) and re-merges. The on-disk ``merged_workbook_path`` is left
    untouched as the durable fallback — we write to a fresh temp file.

    Returns the temp merged path, or ``None`` when re-export isn't applicable
    (no facts, missing run context, or any failure) so the caller falls back
    to the on-disk workbook. Best-effort by design: a re-export bug must never
    block a download.
    """
    import tempfile
    from types import SimpleNamespace
    from db import repository as repo
    from statement_types import StatementType, FACTS_BEARING_AGENT_STATUSES
    from notes_types import NotesTemplateType
    from workbook_merger import merge as merge_workbooks

    try:
        conn = _open_audit_conn()
        try:
            run = repo.fetch_run(conn, run_id)
            agents = repo.fetch_run_agents(conn, run_id)
        finally:
            conn.close()
        if run is None or not run.merged_workbook_path:
            return None
        session_dir = Path(run.merged_workbook_path).parent
        if not session_dir.exists():
            return None

        config = run.config or {}
        filing_level = config.get("filing_level", "company")
        filing_standard = config.get("filing_standard", "mfrs")

        # Synthesize the agent_results shape _export_canonical_workbooks needs
        # (status / statement_type enum / variant) from the persisted
        # run_agents rows — the in-memory coordinator result is long gone by
        # download time.
        agent_results = []
        for a in agents:
            # Scope IN every facts-bearing statement, not just `succeeded`:
            # a `completed_with_errors` (acknowledge_unresolved) statement has
            # real facts that the reviewer may have edited, so it MUST be
            # re-exported from `run_concept_facts` or the download silently
            # keeps the stale scratch workbook for it. We normalise the
            # synthesized status to "succeeded" so the downstream
            # _export_canonical_workbooks filter keeps it (it mirrors the
            # in-memory pass, where such a statement is already "succeeded").
            if a.status not in FACTS_BEARING_AGENT_STATUSES:
                continue
            try:
                stmt = StatementType(a.statement_type)
            except ValueError:
                continue
            agent_results.append(
                SimpleNamespace(
                    status="succeeded", statement_type=stmt, variant=a.variant
                )
            )
        if not agent_results:
            return None

        # Seed face-statement inputs from whatever is on disk, then let the
        # canonical export repoint each succeeded statement at a DB-filled copy.
        all_workbook_paths: Dict[StatementType, str] = {}
        for stmt in StatementType:
            wb = session_dir / f"{stmt.value}_filled.xlsx"
            if wb.exists():
                all_workbook_paths[stmt] = str(wb)

        _export_canonical_workbooks(
            run_id=run_id,
            agent_results=agent_results,
            all_workbook_paths=all_workbook_paths,
            session_dir=session_dir,
            filing_level=filing_level,
            filing_standard=filing_standard,
            db_path=AUDIT_DB_PATH,
        )

        # Notes sheets are sourced from their on-disk per-template workbooks
        # the same way the original merge did; the notes_cells overlay still
        # runs afterward in the download endpoint for edited HTML.
        all_notes_workbook_paths: Dict[NotesTemplateType, str] = {}
        for nt in NotesTemplateType:
            wb = session_dir / f"NOTES_{nt.value}_filled.xlsx"
            if wb.exists():
                all_notes_workbook_paths[nt] = str(wb)

        fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", prefix=f"reexport_{run_id}_")
        os.close(fd)
        tmp_path = Path(tmp_name)
        result = merge_workbooks(
            all_workbook_paths,
            str(tmp_path),
            notes_workbook_paths=all_notes_workbook_paths,
            skip_recalc=True,
        )
        if not getattr(result, "success", False):
            tmp_path.unlink(missing_ok=True)
            logger.warning(
                "download re-export merge failed for run %s: %s",
                run_id, getattr(result, "errors", None),
            )
            return None
        return tmp_path
    except Exception:  # noqa: BLE001 — never let re-export block a download
        logger.exception(
            "download re-export from facts failed for run %s — serving "
            "on-disk workbook", run_id,
        )
        return None


def _fact_based_checks_enabled() -> bool:
    """Item 32 (32a) transition flag. When on, cross-checks read
    ``run_concept_facts`` by uuid (``run_all_facts``) instead of opening
    workbooks. Default ON as of plan Step 1.5b (2026-06-14): the fact path is
    proven equal to the xlsx path by the shadow suite (all 6 checks) and the
    full-pipeline e2e parity harness, and the mocked orchestration tests were
    migrated to patch both paths. Set ``XBRL_FACT_BASED_CHECKS=0`` to fall back
    to the xlsx path. Read at call time so tests can toggle it via the
    environment."""
    return os.environ.get("XBRL_FACT_BASED_CHECKS", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _build_check_template_ids(
    agent_results, filing_level: str, filing_standard: str
) -> "Dict":
    """Map each succeeded statement to its ``template_id`` (the fact-based
    checks' analogue of ``all_workbook_paths``). Mirrors how
    ``_export_canonical_workbooks`` derives the id from the master template,
    so the scoping stays variant-precise (gotcha #21).

    Thin wrapper over ``cross_checks.framework.resolve_check_scope`` — the
    shared scoping source of truth (docs/PLAN.md Step 5). The ``"succeeded"``
    status filter stays here (in-memory ``agent_results``); the helper resolves
    each surviving pair's ``template_id`` and skips a NotPrepared /
    standard-variant-mismatch / unresolved-variant statement exactly as before."""
    from cross_checks.framework import resolve_check_scope

    return resolve_check_scope(
        [(ar.statement_type, ar.variant)
         for ar in agent_results if ar.status == "succeeded"],
        filing_level=filing_level, filing_standard=filing_standard,
    ).template_ids


@dataclasses.dataclass
class CrossCheckPlan:
    """The resolved cross-check backend choice (PLAN-orchestration-seams Part B).

    ``select_cross_check_backend`` reads ``XBRL_FACT_BASED_CHECKS`` EXACTLY
    once and returns one of these, so both the async pipeline pass and the
    sync review-UI recheck execute the *same* decision without re-reading the
    flag. ``fact_based`` true carries a ready ``fact_ctx``; false carries a
    **lazy** ``workbook_provider`` thunk — evaluated only by the xlsx runner,
    so the facts path never builds (or rebuilds) workbooks (preserving
    ``_recheck_from_facts``' no-rebuild contract)."""

    fact_based: bool
    fact_ctx: Optional[dict] = None
    workbook_provider: Optional[Callable[[], dict]] = None


def select_cross_check_backend(
    *,
    agent_results,
    run_id: int,
    filing_level: str,
    filing_standard: str,
    workbook_provider: Callable[[], dict],
) -> CrossCheckPlan:
    """Choose the cross-check backend, reading the transition flag ONCE.

    The single flag read lives here (the prior ``_fact_ctx_for_run`` +
    ``_run_cross_checks_bounded`` re-check were the redundant pair this seam
    collapses). On the facts path the template-id scoping is built eagerly
    (cheap dict lookups); the xlsx ``workbook_provider`` is left untouched as a
    thunk so it is invoked only if the xlsx backend actually runs."""
    if _fact_based_checks_enabled():
        return CrossCheckPlan(
            fact_based=True,
            fact_ctx={
                "run_id": run_id,
                "template_ids": _build_check_template_ids(
                    agent_results, filing_level, filing_standard,
                ),
                "filing_level": filing_level,
                "filing_standard": filing_standard,
            },
        )
    return CrossCheckPlan(fact_based=False, workbook_provider=workbook_provider)


async def run_cross_check_pass_async(
    plan: CrossCheckPlan,
    checks: list,
    check_config: dict,
    *,
    tolerance: float,
    on_check=None,
) -> list:
    """Execute a :class:`CrossCheckPlan` on the async pipeline path.

    Thin wrapper over ``_run_cross_checks_bounded`` (bounded ``asyncio.wait_for``
    + ``call_soon_threadsafe`` progress dispatch unchanged — gotchas #19/#20).
    The xlsx workbook set is materialised from the lazy provider only when the
    plan chose the xlsx backend."""
    workbook_paths = {} if plan.fact_based else plan.workbook_provider()
    return await _run_cross_checks_bounded(
        checks, workbook_paths, check_config,
        tolerance=tolerance, on_check=on_check, fact_ctx=plan.fact_ctx,
    )


def run_cross_check_pass_sync(
    plan: CrossCheckPlan,
    checks: list,
    check_config: dict,
    *,
    tolerance: float,
    timeout: float,
) -> list:
    """Execute a :class:`CrossCheckPlan` on the sync review-UI recheck path.

    Mirrors ``run_cross_check_pass_async`` but uses the
    ``_CROSS_CHECK_EXECUTOR.submit`` + ``future.result(timeout=...)`` thread-join
    model ``_recheck_from_facts`` needs (it is called from sync request
    handlers). The facts path opens its sqlite connection INSIDE the worker
    thread; the xlsx provider (which rebuilds workbooks from DB facts) is
    evaluated in the calling thread before submit — exactly as before — so on
    the facts path no workbook rebuild ever happens. Raises ``TimeoutError`` on
    cap expiry for the caller to map to its best-effort ``None``."""
    from cross_checks.framework import (
        FactsContext, run_all as run_cross_checks, run_all_facts,
    )

    if plan.fact_based:
        fact_ctx = plan.fact_ctx
        run_id = fact_ctx["run_id"]
        template_ids = fact_ctx["template_ids"]
        filing_level = fact_ctx["filing_level"]
        filing_standard = fact_ctx["filing_standard"]

        def _work():
            conn2 = _open_audit_conn()
            try:
                ctx = FactsContext(
                    conn=conn2, run_id=run_id, template_ids=template_ids,
                    filing_level=filing_level, filing_standard=filing_standard,
                )
                return run_all_facts(
                    checks, ctx, check_config, tolerance=tolerance,
                )
            finally:
                conn2.close()
    else:
        # Materialise (rebuild) the workbooks in the calling thread, exactly
        # as the old hand-rolled branch did, then run the xlsx checks.
        workbook_paths = plan.workbook_provider()

        def _work():
            return run_cross_checks(
                checks, workbook_paths, check_config, tolerance=tolerance,
            )

    future = _CROSS_CHECK_EXECUTOR.submit(_work)
    return future.result(timeout=None if timeout == float("inf") else timeout)


def _recheck_from_facts(run_id: int) -> Optional[list[dict]]:
    """Phase 4.3 — re-run the cross-checks against the current DB facts.

    Cross-checks normally run once during the pipeline on the agent-written
    workbooks. After a user edits values in the review UI those results go
    stale. This rebuilds each succeeded statement's workbook from
    ``run_concept_facts`` (so edits + cascaded totals are reflected) and
    re-runs the same default cross-check registry, returning serialised
    results. Returns ``None`` when the run has no facts / context to check.
    Best-effort: never raises into the request handler.
    """
    from types import SimpleNamespace
    from db import repository as repo
    from statement_types import StatementType, FACTS_BEARING_AGENT_STATUSES
    from cross_checks.framework import build_default_cross_checks

    try:
        conn = _open_audit_conn()
        try:
            run = repo.fetch_run(conn, run_id)
            agents = repo.fetch_run_agents(conn, run_id)
            # Preserve the advisory notes-warnings from the original pass.
            # The numeric registry below can't re-derive them (they compare
            # note TEXT / citations, which a figure edit doesn't change), so
            # re-running without them would silently shrink the check set —
            # the "8/11 → 8/8" review bug. Status "warning" uniquely marks
            # these advisory rows; numeric checks only emit passed/failed/
            # not_applicable/pending. Carried through verbatim (docs/
            # PLAN-design-qa-fixes.md A3, decision (a)).
            advisory_rows = [
                {
                    "name": c.check_name,
                    "status": c.status,
                    "expected": c.expected,
                    "actual": c.actual,
                    "diff": c.diff,
                    "tolerance": c.tolerance,
                    "message": c.message,
                    "target_sheet": c.target_sheet,
                    "target_row": c.target_row,
                    "comparands_json": c.comparands_json,
                }
                for c in repo.fetch_cross_checks(conn, run_id)
                if c.status == "warning"
            ]
        finally:
            conn.close()
        if run is None or not run.merged_workbook_path:
            return None
        session_dir = Path(run.merged_workbook_path).parent
        if not session_dir.exists():
            return None

        config = run.config or {}
        filing_level = config.get("filing_level", "company")
        filing_standard = config.get("filing_standard", "mfrs")

        agent_results = []
        for a in agents:
            # Facts-bearing statements only — but that INCLUDES
            # `completed_with_errors` (acknowledge_unresolved) saves, whose
            # facts are real and checkable. A `succeeded`-only filter would
            # drop them and the recheck would report their checks as pending
            # (diverging from the in-memory pipeline pass, which keeps them).
            if a.status not in FACTS_BEARING_AGENT_STATUSES:
                continue
            try:
                stmt = StatementType(a.statement_type)
            except ValueError:
                continue
            agent_results.append(
                SimpleNamespace(
                    status="succeeded", statement_type=stmt, variant=a.variant
                )
            )
        if not agent_results:
            return None

        # Shared scoping (docs/PLAN.md Step 7) — one resolve_check_scope call
        # replaces the hand-rolled statements_to_run/variants loop, consistent
        # with the pipeline + reviewer paths. Equivalent in practice: a
        # facts-bearing succeeded statement always carries the concrete variant
        # it extracted under, so template_path resolves and the statement is
        # kept. The ONE divergence from the old loop (which added every
        # StatementType-valid row unconditionally) is a degenerate
        # NULL/unresolvable-variant row — which a succeeded agent never
        # produces; there resolve_check_scope drops it, surfacing a check that
        # needs it as 'pending' (not extracted) rather than the old 'failed:
        # workbook missing', if anything more accurate. template_ids is still
        # built by select_cross_check_backend below, which also needs
        # agent_results for the xlsx provider.
        from cross_checks.framework import resolve_check_scope
        check_scope = resolve_check_scope(
            [(ar.statement_type, ar.variant) for ar in agent_results],
            filing_level=filing_level, filing_standard=filing_standard,
        )
        check_config = {
            "statements_to_run": check_scope.statements_to_run,
            "variants": check_scope.variants,
            "filing_level": filing_level,
            "filing_standard": filing_standard,
        }
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
        # Item 16: this helper is called from sync request handlers, so the
        # bound is a thread + join-with-timeout rather than asyncio.wait_for.
        # On expiry the worker is abandoned (see _CROSS_CHECK_EXECUTOR note)
        # and the TimeoutError rides the existing best-effort except → None.
        timeout = float(CROSS_CHECK_TIMEOUT)

        def _xlsx_provider() -> Dict[StatementType, str]:
            # Repoint each succeeded statement at a fresh DB-exported workbook
            # so the xlsx checks see the edited figures, not the agent's
            # scratch values. Only invoked when the xlsx backend is chosen — so
            # the facts path NEVER rebuilds workbooks (the headline smell this
            # seam preserves: a workbook was rebuilt from DB facts just to run
            # xlsx checks on it).
            all_workbook_paths: Dict[StatementType, str] = {}
            for stmt in StatementType:
                wb = session_dir / f"{stmt.value}_filled.xlsx"
                if wb.exists():
                    all_workbook_paths[stmt] = str(wb)
            _export_canonical_workbooks(
                run_id=run_id,
                agent_results=agent_results,
                all_workbook_paths=all_workbook_paths,
                session_dir=session_dir,
                filing_level=filing_level,
                filing_standard=filing_standard,
                db_path=AUDIT_DB_PATH,
            )
            return all_workbook_paths

        # One flag read (inside select), one backend, executed via the SYNC
        # runner (this helper is called from sync request handlers). The facts
        # path reads run_concept_facts directly with no workbook rebuild.
        plan = select_cross_check_backend(
            agent_results=agent_results,
            run_id=run_id,
            filing_level=filing_level,
            filing_standard=filing_standard,
            workbook_provider=_xlsx_provider,
        )
        try:
            results = run_cross_check_pass_sync(
                plan, build_default_cross_checks(), check_config,
                tolerance=tolerance, timeout=timeout,
            )
        except (_FuturesTimeoutError, TimeoutError):
            # Both spellings: on Py 3.10 Future.result raises the distinct
            # concurrent.futures.TimeoutError; on >= 3.11 they're aliases.
            logger.warning(
                "re-check cross-check pass exceeded %.0fs cap for run %s — "
                "abandoning worker thread", timeout, run_id,
            )
            return None
        from cross_checks.framework import comparands_to_json
        numeric_rows = [
            {
                "name": r.name,
                "status": r.status,
                "expected": r.expected,
                "actual": r.actual,
                "diff": r.diff,
                "tolerance": r.tolerance,
                "message": r.message,
                "target_sheet": r.target_sheet,
                "target_row": r.target_row,
                "comparands_json": comparands_to_json(
                    getattr(r, "comparands", None)),
            }
            for r in results
        ]
        # Re-attach the preserved advisory warnings so the check set stays
        # stable across a recheck (no phantom "8/11 → 8/8" shrink).
        return numeric_rows + advisory_rows
    except Exception:  # noqa: BLE001 — a re-check must never 500 the page
        logger.exception("on-demand re-check failed for run %s", run_id)
        return None


def _refresh_persisted_cross_checks(run_id: int) -> bool:
    """Re-run cross-checks from current facts and REPLACE the stored rows.

    The manual re-review and revert paths mutate facts without touching the
    persisted ``cross_checks`` table, but the Review tab and a subsequent
    re-review both read those stored rows — so without a refresh they'd show
    stale pass/fail state (a manual fix keeps showing old failures; a revert of
    an auto-fixed run keeps showing passed checks while the restored workbook
    fails). This re-runs the same default registry against current facts and
    swaps the run's rows atomically. Best-effort: never raises into a request.

    Returns True when the rows were refreshed, False when there was nothing to
    re-check (no facts / no succeeded statements) or the refresh failed.
    """
    from db import repository as repo

    results = _recheck_from_facts(run_id)
    if results is None:
        return False
    try:
        conn = _open_audit_conn()
        try:
            # Replace, don't append: the run already has one set of rows from
            # the original pipeline (it persists cross_checks exactly once).
            conn.execute("DELETE FROM cross_checks WHERE run_id = ?", (run_id,))
            for r in results:
                repo.save_cross_check(
                    conn, run_id, check_name=r["name"], status=r["status"],
                    expected=r["expected"], actual=r["actual"], diff=r["diff"],
                    tolerance=r["tolerance"], message=r["message"],
                    target_sheet=r["target_sheet"], target_row=r["target_row"],
                    comparands_json=r.get("comparands_json"),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — a refresh must never break the request
        logger.exception("cross-check refresh failed for run %s", run_id)
        return False


def _safe_downgrade_run_status(run_id: int) -> bool:
    """One-directional status refresh after a re-review / revert changes facts.

    Recomputes validation state from the now-refreshed cross_checks + open
    conflicts and downgrades a ``completed`` run to ``completed_with_errors``
    when failures exist. The direction is deliberate and SAFE:

    * It fires ONLY when the run is currently ``completed`` — so ``failed`` /
      ``aborted`` / ``correction_exhausted`` runs, and runs already marked
      ``completed_with_errors``, are left untouched.
    * It NEVER promotes a run to ``completed`` — a run that still says
      ``completed_with_errors`` may carry a failed-agent error unrelated to the
      cross-checks, and silently upgrading it would hide that. The accurate
      per-check pass/fail is already visible in the refreshed cross_checks rows.

    Best-effort: never raises into a request. Returns True iff it downgraded.
    """
    from db import repository as repo
    try:
        conn = _open_audit_conn()
        try:
            run = repo.fetch_run(conn, run_id)
            if run is None or run.status != "completed":
                return False
            failed = conn.execute(
                "SELECT COUNT(*) FROM cross_checks "
                "WHERE run_id = ? AND status = 'failed'", (run_id,),
            ).fetchone()[0]
            # Mirrors _open_conflict_count: the correction_exhausted sentinel is
            # surfaced via its own status and must not count here.
            conflicts = conn.execute(
                "SELECT COUNT(*) FROM run_concept_conflicts WHERE run_id = ? "
                "AND status = 'open' AND kind != 'correction_exhausted'",
                (run_id,),
            ).fetchone()[0]
            if failed > 0 or conflicts > 0:
                repo.update_run_status(conn, run_id, "completed_with_errors")
                conn.commit()
                return True
            return False
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — a status refresh must never break a request
        logger.exception("safe status downgrade failed for run %s", run_id)
        return False


def _open_conflict_count(db_path, run_id) -> int:
    """Count unresolved reconciliation conflicts for a run (canonical mode).

    Excludes the ``correction_exhausted`` sentinel — that outcome is already
    surfaced via the dedicated ``correction_exhausted`` run status, so
    counting it here too would double-signal. A non-zero count means the
    canonical facts still carry partial-state / parent-child disagreements a
    human needs to reconcile, which flips the run to completed_with_errors
    (peer-review finding 4 / Phase E).
    """
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM run_concept_conflicts "
                "WHERE run_id = ? AND status = 'open' "
                "AND kind != 'correction_exhausted'",
                (run_id,),
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — telemetry must never sink a run
        logger.warning("open-conflict count failed for run %s", run_id,
                       exc_info=True)
        return 0


def _grade_run_against_benchmark(db_path, run_id: int, benchmark_id: int):
    """Grade a finished run against its benchmark and persist the scorecard.

    Gold-standard eval (v16). Returns the score dict (the same shape
    ``repo.fetch_eval_score`` returns) on success, or ``None`` on any failure.
    Wrapped so a grading error NEVER fails the run — a run with a benchmark
    that can't be graded simply lands without a score, mirroring the
    soft-failure contract for merge / cross-check errors (gotcha #20).
    """
    import sqlite3
    from db import repository as repo
    from eval.grader import grade_run

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        # Match the lifecycle path's pragmas: save_eval_score is a WRITE and
        # grading fires at run-completion when other writers may be active. A
        # default busy_timeout of 0 would raise SQLITE_BUSY on a transient
        # lock, and the broad except below would silently drop the score.
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            card = grade_run(conn, run_id, benchmark_id)
            repo.save_eval_score(conn, run_id, benchmark_id, card)
            conn.commit()
            return repo.fetch_eval_score(conn, run_id, benchmark_id)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — grading must never sink a run
        logger.warning(
            "eval grading failed for run %s vs benchmark %s — run completes "
            "without a score", run_id, benchmark_id, exc_info=True,
        )
        return None

# Phase mapping: tool name → EventPhase
PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "write_facts": "filling_workbook",
    "verify_totals": "verifying",
    "save_result": "complete",
}

# Notes templates that are safe to expose over the public API / CLI.
# Kept separate from NotesTemplateType so the enum stays a superset (new
# template types can be drafted and tested before being exposed). A
# tests/test_server_notes_api.py assertion catches accidental drift
# between this allowlist and the enum.
def _public_notes_templates() -> frozenset:
    from notes_types import NotesTemplateType as _NT
    return frozenset({
        _NT.CORP_INFO,
        _NT.ACC_POLICIES,
        _NT.LIST_OF_NOTES,
        _NT.ISSUED_CAPITAL,
        _NT.RELATED_PARTY,
    })


_PUBLIC_NOTES_TEMPLATES = _public_notes_templates()


# `_build_default_cross_checks` moved to `cross_checks.framework` in the
# peer-review round so `correction.agent` no longer needs a lazy
# `from server import …`. Keep a local alias for back-compat with tests
# that already import from `server` directly (MPERS wiring pins).
from cross_checks.framework import (
    build_default_cross_checks as _build_default_cross_checks,
)


# Lazy imports for multi-agent pipeline — done at call sites to keep startup fast.
# scout.runner.run_scout, coordinator.run_extraction, workbook_merger.merge,
# cross_checks.framework.run_all, etc.


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Return the best available API key: GOOGLE_API_KEY (proxy) or GEMINI_API_KEY (direct)."""
    return os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")


# Provider-prefix forms that appear in config/models.json and PydanticAI
# namespacing. Order matters — the longest match must come first so that
# "bedrock.anthropic." is stripped before any prefix that shares its head.
_PROVIDER_PREFIXES: tuple[str, ...] = (
    "bedrock.anthropic.",
    "vertex_ai.",
    "openai.",
    "google-gla:",
    "google-vertex:",
)


def _strip_provider_prefix(model_name: str) -> str:
    """Return the bare model id with any known registry prefix removed.

    The registry IDs in config/models.json are fully qualified (e.g.
    `openai.gpt-5.4`, `bedrock.anthropic.claude-sonnet-4-6`). Both provider
    detection and direct-mode model construction need the bare name (e.g.
    `gpt-5.4`), so this helper is the single source of truth.
    """
    for prefix in _PROVIDER_PREFIXES:
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


def _detect_provider(model_name: str) -> str:
    """Infer the provider from a model name string.

    Returns 'openai', 'anthropic', or 'google'. Handles both bare names
    (`gpt-5.4`) and prefixed registry IDs (`openai.gpt-5.4`) by stripping
    the prefix before matching.
    """
    bare = _strip_provider_prefix(model_name).lower()
    if bare.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if bare.startswith("claude-"):
        return "anthropic"
    return "google"


def _model_id(model_obj) -> str:
    """Return the human-readable model id for a PydanticAI Model instance.

    PydanticAI's Model classes (`OpenAIChatModel`, `GoogleModel`,
    `AnthropicModel`) all have a useless `__str__` that returns just the
    class name with empty parentheses (e.g. `'OpenAIChatModel()'`). The
    actual configured id (`'gpt-5.4'`, `'gemini-3-flash-preview'`, etc.)
    lives on the `model_name` attribute. Always prefer that attribute when
    persisting model identity to the audit DB so the History UI and the
    `?model=` filter both see the real id, not a class repr.

    Falls back to `str()` only if `model_name` is missing or empty —
    keeping the helper safe for the test stubs that pass plain strings.
    """
    name = getattr(model_obj, "model_name", None)
    if isinstance(name, str) and name:
        return name
    return str(model_obj)


def _is_local_proxy(proxy_url: str) -> bool:
    """True when ``proxy_url`` points at the Mac local-dev LiteLLM proxy.

    The enterprise proxy is a remote PwC host; the dev proxy started by
    ``start.sh`` lives on localhost. Only the local-dev case is allowed to
    bypass the proxy for direct Gemini calls — direct Google is firewall
    -blocked (403) on the Windows enterprise network (CLAUDE.md gotcha #5).
    """
    return "localhost" in proxy_url or "127.0.0.1" in proxy_url


def _create_proxy_model(model_name: str, proxy_url: str, api_key: str):
    """Create a PydanticAI model with multi-provider support.

    Routing logic:
    1. If ``proxy_url`` is set → enterprise LiteLLM proxy (Windows). All
       models go through the OpenAI-compatible proxy endpoint — EXCEPT
       Gemini models on the local-dev proxy, which go direct (see below).
    2. If ``proxy_url`` is empty (Mac / direct API):
       - OpenAI models (gpt-*, o1-*, o3-*, o4-*) → OpenAI API via OPENAI_API_KEY
       - Anthropic models (claude-*) → Anthropic API via ANTHROPIC_API_KEY
       - Everything else → Google Gemini API via GEMINI_API_KEY / GOOGLE_API_KEY
    """
    # Enterprise proxy path — everything goes through one OpenAI-compatible endpoint
    if proxy_url:
        # Gemini-3 thinking models require a `thought_signature` to be echoed
        # back on every prior functionCall part across multi-turn tool calls.
        # The OpenAI chat format can't carry that field, so routing Gemini
        # through the OpenAI-compatible proxy breaks on the second turn with
        # "Function call is missing a thought_signature". pydantic-ai's native
        # GoogleModel round-trips the signature correctly, so on the local-dev
        # proxy (Mac) we bypass the proxy and call Gemini directly. The
        # enterprise proxy is left untouched — direct Google is blocked there.
        if _detect_provider(model_name) == "google" and _is_local_proxy(proxy_url):
            # The proxy's auth key now lives in LLM_PROXY_API_KEY (set by
            # start.sh), so GOOGLE_API_KEY / GEMINI_API_KEY keep their real
            # Google values for this direct bypass. Accept either — the Settings
            # UI writes the user's key to GOOGLE_API_KEY (server.py /api/settings),
            # so reading only GEMINI_API_KEY here silently broke the default flow.
            google_key = (
                os.environ.get("GEMINI_API_KEY", "")
                or os.environ.get("GOOGLE_API_KEY", "")
            )
            if google_key:
                from pydantic_ai.models.google import GoogleModel
                from pydantic_ai.providers.google import GoogleProvider

                bare_name = _strip_provider_prefix(model_name)
                provider = GoogleProvider(api_key=google_key)
                return GoogleModel(bare_name, provider=provider)
            logger.warning(
                "Gemini model %r on the local proxy needs a real GEMINI_API_KEY "
                "or GOOGLE_API_KEY for the direct-call bypass; none set, falling "
                "back to the proxy (multi-turn tool calls will likely fail with a "
                "thought_signature error).", model_name,
            )

        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        # The proxy authenticates with its own master key, not a provider key.
        # Prefer LLM_PROXY_API_KEY (local-dev proxy) and fall back to the passed
        # key (enterprise proxy, where GOOGLE_API_KEY is the real proxy key).
        proxy_auth = os.environ.get("LLM_PROXY_API_KEY", "") or api_key
        provider = OpenAIProvider(base_url=proxy_url, api_key=proxy_auth)
        return OpenAIChatModel(model_name, provider=provider)

    # Direct API paths — route by provider.
    # The registry IDs carry a provider prefix (e.g. "openai.gpt-5.4"); the
    # upstream SDKs expect bare names, so strip once up front and use the
    # bare form for both detection and construction.
    bare_name = _strip_provider_prefix(model_name)
    detected = _detect_provider(model_name)

    if detected == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            raise ValueError(
                f"Model '{model_name}' requires OPENAI_API_KEY in .env but it is not set."
            )
        provider = OpenAIProvider(api_key=openai_key)
        return OpenAIChatModel(bare_name, provider=provider)

    if detected == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise ValueError(
                f"Model '{model_name}' requires ANTHROPIC_API_KEY in .env but it is not set."
            )
        provider = AnthropicProvider(api_key=anthropic_key)
        return AnthropicModel(bare_name, provider=provider)

    # Google Gemini direct path — GoogleModel expects bare names like
    # "gemini-3-flash-preview".
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    provider = GoogleProvider(api_key=api_key)
    return GoogleModel(bare_name, provider=provider)



# ---------------------------------------------------------------------------
# Phase 3: correction-agent helper (module-scope for testability)
# ---------------------------------------------------------------------------

# Agent-id the correction pass emits under. Matches the frontend's CORRECTION
# tab routing; kept here as a single source of truth so backend + frontend +
# tests can't drift.
CORRECTION_AGENT_ID = "CORRECTION"
NOTES_VALIDATOR_AGENT_ID = "NOTES_VALIDATOR"

# Per-turn timeout for the correction agent. Mirrors the notes coordinator's
# NOTES_TURN_TIMEOUT — 180s is comfortably above healthy p99 for a single
# model turn and catches the minute-long stalls we've seen on PydanticAI.
CORRECTION_TURN_TIMEOUT: float = 180.0

# Same bound for the notes post-validator. Without it, a stalled model
# turn after merge leaves the whole run hung in `running` (violating
# gotcha #10 — every exit path must reach a terminal status).
NOTES_VALIDATOR_TURN_TIMEOUT: float = 180.0

# PLAN-stop-and-validation-visibility Phase 3: wall-clock cap on the
# whole correction / notes-validator pass. Defence-in-depth on top of
# the dynamic turn cap (RUN-REVIEW P0-1, max 25 turns) and per-turn
# timeout (180s above): the slow-LLM scenario where every turn takes
# 100s but the agent never stalls would still loop for ~40 minutes
# (25 × 100s) before any cap fires. 5 minutes is a comfortable bound
# for "this is the legitimate work" while still being far short of
# what the user perceives as "stuck".
#
# Operators can tune via XBRL_CORRECTION_WALLCLOCK_S /
# XBRL_NOTES_VALIDATOR_WALLCLOCK_S (positive ints, seconds). 0 or
# negative disables the cap entirely.
def _resolve_wallclock(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, "")
    if not raw:
        return default
    try:
        v = float(raw)
        return v if v > 0 else float("inf")
    except ValueError:
        return default

def _agent_row_error_type(
    status: str, explicit: Optional[str], error: Optional[str],
) -> Optional[str]:
    """Item 9 acceptance guarantee: every failed/cancelled run_agents row
    carries a non-null error_type. Prefer the coordinator's explicit
    classification; derive from status + free-text error otherwise."""
    if explicit:
        return explicit
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return _error_type_for_outcome(error or "unknown")
    return None


def _error_type_for_outcome(error: Optional[str]) -> Optional[str]:
    """Map a reviewer/validator outcome error code onto the item-9 failure
    taxonomy (coordinator.py ERROR_TYPE_*) for run_agents.error_type.

    The pseudo-agents report free-form-ish codes; this keeps the DB column
    on the shared vocabulary so History can group all agents uniformly.
    """
    if not error:
        return None
    exact = {
        "cancelled": "cancelled",
        "reviewer_exhausted": "iteration_capped",
        "reviewer_wallclock_exceeded": "wallclock",
        "validator_wallclock_exceeded": "wallclock",
    }
    if error in exact:
        return exact[error]
    lowered = error.lower()
    if "wall-clock" in lowered or "wallclock" in lowered:
        return "wallclock"
    if "per-turn timeout" in lowered or "stalled past" in lowered:
        return "turn_timeout"
    return "tool_exception"


CORRECTION_WALLCLOCK_TIMEOUT: float = _resolve_wallclock(
    "XBRL_CORRECTION_WALLCLOCK_S", 300.0,
)
NOTES_VALIDATOR_WALLCLOCK_TIMEOUT: float = _resolve_wallclock(
    "XBRL_NOTES_VALIDATOR_WALLCLOCK_S", 300.0,
)
NOTES_FORMATTER_WALLCLOCK_TIMEOUT: float = _resolve_wallclock(
    "XBRL_NOTES_FORMATTER_WALLCLOCK_S", 300.0,
)

# PLAN-orchestration-hardening item 16: cross-checks used to run
# synchronously inside async handlers — while openpyxl walked workbooks the
# event loop couldn't service SSE queues for ANY session, and the pass had
# no timeout at all (unlike every agent pass). Now every pass runs on a
# worker thread, bounded by this wall-clock cap. Same resolver semantics as
# the other caps: positive seconds, 0/negative disables.
CROSS_CHECK_TIMEOUT: float = _resolve_wallclock(
    "XBRL_CROSS_CHECK_TIMEOUT_S", 120.0,
)

# Honest limitation: a timeout abandons the worker — wait_for bounds the
# await, not the thread. The work is openpyxl + arithmetic (slow-pathological
# is plausible, infinite-hang is not), so instead of subprocess isolation we
# bound the blast radius: a small dedicated pool means abandoned workers
# can't pile up unbounded across requests.
_CROSS_CHECK_EXECUTOR = _CCThreadPoolExecutor(
    max_workers=4, thread_name_prefix="cross-check",
)


async def _run_cross_checks_bounded(
    checks: list,
    workbook_paths: dict,
    check_config: dict,
    *,
    tolerance: float,
    on_check=None,
    fact_ctx: Optional[dict] = None,
) -> list:
    """Run ``cross_checks.framework.run_all`` off the event loop, bounded.

    Raises ``TimeoutError`` on cap expiry — callers map it into the existing
    structured ``cross_check_exception`` path (gotcha #20), so a pathological
    workbook lands the run ``completed_with_errors`` instead of pinning it in
    ``running`` or freezing SSE for every session.

    The ``on_check`` progress callback is re-dispatched onto the event loop
    via ``call_soon_threadsafe`` — emission still goes through the event
    queue on the loop thread (gotcha #19), never directly from the worker.
    """
    from cross_checks.framework import run_all as _run_all

    loop = asyncio.get_running_loop()
    # Read the module global at call time so tests can monkeypatch it.
    import server as _self
    timeout = float(getattr(_self, "CROSS_CHECK_TIMEOUT", CROSS_CHECK_TIMEOUT))

    # Peer-review fix (2026-06-12): a timeout abandons the worker, but the
    # worker keeps calling on_check — without this gate, late
    # cross_check_result frames would reach the SSE stream AFTER the pass
    # was classified cross_check_exception, showing results from a
    # timed-out pass. Guarded on BOTH sides: the worker thread stops
    # scheduling new dispatches, and already-scheduled loop callbacks drop
    # on arrival.
    _cb_state = {"active": True}
    cb = None
    if on_check is not None:
        def _forward(idx: int, total: int, result) -> None:  # noqa: ANN001
            if _cb_state["active"]:
                on_check(idx, total, result)

        def cb(idx: int, total: int, result) -> None:  # noqa: ANN001
            if _cb_state["active"]:
                loop.call_soon_threadsafe(_forward, idx, total, result)

    # Item 32 (32a): when the caller supplied a fact context, read
    # run_concept_facts by uuid instead of opening workbooks. The backend
    # choice was already made once by select_cross_check_backend (which is the
    # single XBRL_FACT_BASED_CHECKS read), so a non-None fact_ctx IS the
    # decision — no redundant re-check here. The sqlite connection MUST be
    # created inside the worker thread (sqlite connections aren't shareable
    # across threads).
    if fact_ctx is not None:
        from cross_checks.framework import FactsContext, run_all_facts

        def _call_facts():
            conn2 = _open_audit_conn()
            try:
                ctx = FactsContext(
                    conn=conn2,
                    run_id=fact_ctx["run_id"],
                    template_ids=fact_ctx["template_ids"],
                    filing_level=fact_ctx["filing_level"],
                    filing_standard=fact_ctx["filing_standard"],
                )
                return run_all_facts(
                    checks, ctx, check_config, tolerance=tolerance, on_check=cb,
                )
            finally:
                conn2.close()

        future = loop.run_in_executor(_CROSS_CHECK_EXECUTOR, _call_facts)
    else:
        future = loop.run_in_executor(
            _CROSS_CHECK_EXECUTOR,
            lambda: _run_all(
                checks, workbook_paths, check_config,
                tolerance=tolerance, on_check=cb,
            ),
        )
    try:
        return await asyncio.wait_for(
            future, timeout=None if timeout == float("inf") else timeout,
        )
    except (asyncio.TimeoutError, TimeoutError):
        _cb_state["active"] = False
        logger.warning(
            "cross-check pass exceeded %.0fs cap — abandoning worker thread "
            "(it may still be running in the background)", timeout,
        )
        raise TimeoutError(
            f"cross-check pass exceeded the {timeout:.0f}s wall-clock cap"
        ) from None


async def _run_reviewer_pass(
    *,
    failed_checks: list,
    conflicts: list,
    model,
    filing_level: str,
    event_queue,
    db_path,
    run_id: int,
    filing_standard: str = "mfrs",
    pdf_path: Optional[str] = None,
    guidance: Optional[str] = None,
    agent_id: str = CORRECTION_AGENT_ID,
    spot_check: Optional[str] = None,
    verify_scope: Optional[list] = None,
) -> dict:
    """Reviewer pass (docs/Archive/PLAN-reviewer-agent.md) — replaces the autonomous
    canonical correction pass.

    ``spot_check`` (``"light"`` / ``"full"`` / ``None``) drives the clean-run
    spot-check (issue 1, 2026-06-21): when set, the run had NO failing checks
    and NO open conflicts, but we still run a grounded sanity pass over the
    high-value figures. ``light`` is a tight pass (small turn cap + the
    ``spot_check.md`` body); ``full`` reuses the holistic reviewer. Everything
    downstream — snapshot, re-export, revert — is reused unchanged.

    The reviewer investigates the root cause of each failing cross-check /
    open conflict down the face → sub-sheet → PDF chain, applies grounded
    fixes through the guarded ``apply_fixes`` tool, and flags the cases it's
    stuck on or disputes. Safety is structural: we SNAPSHOT the original
    facts FIRST (the load-bearing reversibility invariant), so a misbehaving
    reviewer can always be reverted with one click — there is no write-gating.

    Returns the same outcome shape ``_run_canonical_correction_pass`` did so
    the downstream re-export / re-merge / re-check machinery is reused
    unchanged. ``writes_performed > 0`` drives the re-export.
    """
    import asyncio as _asyncio
    import time as _wc_time
    import server as _server_self
    from agent_runner import (
        AgentLoopSpec,
        CallToolsCapExceeded,
        WallclockExceeded,
        run_agent_loop,
    )
    from correction.reviewer_agent import (
        create_reviewer_agent, compute_reviewer_turn_cap,
    )
    from concept_model.versioning import ensure_snapshot

    outcome: dict = {
        "invoked": False, "writes_performed": 0, "flags_raised": 0,
        "error": None, "total_tokens": 0, "total_cost": 0.0,
        "exhausted": False, "turns_used": 0, "max_turns": 0,
        # Item 15: wall-clock elapsed for this pass, set on every exit path.
        # "Exhausted at 299s of a 300s cap" vs "exhausted at 120s" is the
        # difference between raising the cap and fixing a slow tool.
        "elapsed_seconds": 0.0,
    }
    _pass_start = _wc_time.monotonic()

    def _stamp_elapsed() -> None:
        outcome["elapsed_seconds"] = round(
            _wc_time.monotonic() - _pass_start, 1,
        )

    # Normalise the failing cross-checks (CrossCheckResult objects) into the
    # plain-dict shape the review packet renderer expects. `comparands` carry
    # the values each check compared (Phase 2) so the reviewer gets concrete
    # entry points; the inline pass has the live objects, so no DB round-trip.
    failed_payload = [
        {
            "name": getattr(c, "name", None),
            "expected": getattr(c, "expected", None),
            "actual": getattr(c, "actual", None),
            "diff": getattr(c, "diff", None),
            "message": getattr(c, "message", None),
            "target_sheet": getattr(c, "target_sheet", None),
            "target_row": getattr(c, "target_row", None),
            "comparands": [
                dataclasses.asdict(cm)
                for cm in (getattr(c, "comparands", None) or [])
            ],
        }
        for c in (failed_checks or [])
    ]
    n_items = len(failed_payload) + len(conflicts or [])
    spot_mode = (spot_check or "").lower() or None
    if spot_mode not in (None, "light", "full"):
        spot_mode = "light"
    # Tag the outcome so the run-status logic can treat a SPOT-CHECK outcome
    # (clean run) differently from a failure-driven reviewer outcome — e.g. a
    # spot-check that merely exhausts its tight turn budget must NOT mark the
    # whole clean run `correction_exhausted` (peer-review HIGH).
    outcome["spot_check"] = spot_mode
    # The failure-driven pass returns immediately when there's nothing to
    # investigate. The spot-check, by definition, fires on a CLEAN run
    # (n_items == 0) — so it must NOT short-circuit here.
    if n_items == 0 and spot_mode is None:
        return outcome
    outcome["invoked"] = True

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        # Phase 6.2: every sub-pass (reviewer / notes-validator) error is
        # recoverable — these passes run AFTER extraction and their failure
        # leaves the run landing ``completed_with_errors``, never terminating
        # it. Stamp the bucket so the audit DB + any future global consumer
        # classify it honestly (these errors carry an ``agent_id`` so the
        # frontend already routes them per-agent, not to the global branch).
        if event_type == "error":
            data = {"bucket": ERROR_BUCKET_RECOVERABLE, **data}
        await event_queue.put({
            "event": event_type,
            "data": {**data, "agent_id": agent_id, "agent_role": agent_id},
        })

    # Reversibility guard: the reviewer's safety net is the snapshot of the
    # ORIGINAL facts, and "snapshot exists" is detected by snapshot ROW
    # presence. If the run carries ZERO canonical facts (a near-total
    # extraction failure), the snapshot is empty — indistinguishable from "no
    # snapshot taken" — so any facts the reviewer then created would be neither
    # diffable (compute_review_diff short-circuits on an empty snapshot) nor
    # revertible (revert_to_original returns "no snapshot"), silently breaking
    # the headline reversibility invariant. The reviewer's job is to fix an
    # existing extraction, not bootstrap one from nothing — so refuse rather
    # than make non-revertible writes (peer-review P1). Re-run extraction
    # first. Checked BEFORE snapshotting so we never write a 0-row snapshot.
    import sqlite3 as _sqlite3_count
    try:
        _cc = _sqlite3_count.connect(str(db_path))
        try:
            _fact_count = _cc.execute(
                "SELECT COUNT(*) FROM run_concept_facts WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        finally:
            _cc.close()
    except Exception:  # noqa: BLE001 — a count error must not block the pass
        _fact_count = 1
    if _fact_count == 0:
        outcome["error"] = "no_extracted_facts_to_review"
        msg = (
            "No extracted facts to review — the reviewer needs an existing "
            "extraction to anchor reversible fixes (re-run extraction first)."
        )
        await _emit("error", {"type": "reviewer_no_facts", "message": msg})
        await _emit("complete", {"success": False,
                                 "error": outcome["error"]})
        _stamp_elapsed()
        return outcome

    # Snapshot the ORIGINAL facts before the reviewer writes anything. This
    # is what makes "Revert to original" possible — if it fails we must NOT
    # run the reviewer, because there'd be no safety net (gotcha #20: surface
    # the failure structurally instead of silently continuing).
    #
    # Take the snapshot ONLY on the first pass. A manual re-review must keep
    # the ORIGINAL extraction snapshot intact so "revert" always goes back to
    # the first extraction, never to a prior reviewer state (Step 13 verify).
    try:
        # ensure_snapshot folds the existence check + create into one
        # BEGIN IMMEDIATE transaction (item 13): two concurrent passes can no
        # longer both pass "no snapshot yet" and double-write the restore point.
        # Create-if-absent only — an existing snapshot is preserved (always the
        # ORIGINAL extraction, never a prior reviewer state; Step 13 verify).
        ensure_snapshot(db_path, run_id)
    except Exception:  # noqa: BLE001
        logger.exception("Reviewer snapshot failed for run %s", run_id)
        outcome["error"] = "snapshot_failed"
        msg = "Reviewer snapshot failed; see server logs for details."
        await _emit("error", {"type": "reviewer_exception", "message": msg})
        await _emit("complete", {"success": False, "error": outcome["error"]})
        _stamp_elapsed()
        return outcome

    if spot_mode is not None:
        from correction.reviewer_agent import compute_spot_check_turn_cap
        max_turns = compute_spot_check_turn_cap(
            filing_level=filing_level, mode=spot_mode,
        )
    else:
        max_turns = compute_reviewer_turn_cap(
            filing_level=filing_level, n_items=n_items,
        )
    outcome["max_turns"] = max_turns

    # Phase 4 (reviewer holistic audit): resolve the run's output dir so we can
    # persist the reviewer's conversation trace there (like extraction agents).
    # Falls back to the PDF's parent (pdf_path is {output_dir}/uploaded.pdf).
    _review_out_dir: Optional[str] = None
    try:
        _cc2 = _sqlite3_count.connect(str(db_path))
        try:
            _row = _cc2.execute(
                "SELECT output_dir FROM runs WHERE id = ?", (run_id,),
            ).fetchone()
            _review_out_dir = _row[0] if _row and _row[0] else None
        finally:
            _cc2.close()
    except Exception:  # noqa: BLE001 — trace dir is best-effort
        _review_out_dir = None
    if not _review_out_dir and pdf_path:
        _review_out_dir = str(Path(pdf_path).parent)

    agent_run = None  # bound by the `async with` below; pre-init so the
    # trace-save `finally` can reference it even if construction/enter fails.

    try:
        agent, deps = create_reviewer_agent(
            model=model, db_path=db_path, run_id=run_id,
            filing_level=filing_level, filing_standard=filing_standard,
            pdf_path=pdf_path,
            failed_checks=failed_payload, conflicts=conflicts, guidance=guidance,
            spot_check_mode=spot_mode,
            # Inline pass: scope verify_fixes off the in-memory succeeded set
            # (the DB run_agents rows aren't 'succeeded' yet — run-58 fix). None
            # on the manual /re-review path → DB fallback (rows are terminal).
            verify_scope=verify_scope,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Reviewer agent construction failed")
        outcome["error"] = "agent_construction_failed"
        msg = "Reviewer agent construction failed; see server logs for details."
        await _emit("error", {"type": "reviewer_exception", "message": msg})
        await _emit("complete", {"success": False, "error": outcome["error"]})
        _stamp_elapsed()
        return outcome

    if spot_mode is not None:
        prompt = (
            "All cross-checks passed and there are no open conflicts. Run a "
            f"{'FULL holistic' if spot_mode == 'full' else 'LIGHT'} spot-check: "
            "start with list_facts, then verify the highest-value figures "
            "(face totals, largest line items, units, signs) against the PDF. "
            "apply_fixes ONLY what you can ground in the PDF; raise_flag anything "
            f"suspicious you can't resolve. You have at most {max_turns} turns. "
            "If everything ties out, make no writes. Never plug a residual."
        )
        await _emit("status", {
            "phase": "started",
            "message": (
                "AI review: all cross-checks passed — doing a quick sanity check "
                "of the key figures against the PDF."
            ),
        })
    else:
        prompt = (
            "Investigate every failing cross-check and open conflict in your "
            "REVIEW PACKET. Trace each failure DOWN to the leaf that's wrong "
            "(trace_cascade_source), ground the fix in the PDF, then "
            "apply_fixes (batch independent fixes into one call). Flag only what "
            "you're stuck on or dispute. You have at "
            f"most {max_turns} turns. Never plug a residual to force a balance."
        )
        await _emit("status", {
            "phase": "started",
            "message": (
                f"AI review started: tracing {len(failed_payload)} failed check(s) "
                f"and {len(conflicts or [])} conflict(s) back to the PDF."
            ),
        })

    turn_count = 0
    _wallclock_cap = float(getattr(
        _server_self, "CORRECTION_WALLCLOCK_TIMEOUT", CORRECTION_WALLCLOCK_TIMEOUT,
    ))

    # Item 17: the hand-rolled iteration loop (per-turn timeout + in-loop
    # wall-clock + call-tools turn cap + tool-event streaming) migrated onto
    # agent_runner.run_agent_loop. Wire-contract preservation: the reviewer
    # never emitted token_update / text_delta / thinking events, so the
    # filtered emit below forwards only the tool events it always emitted.
    # The call-tools cap (NOT a raw node cap) keeps the reviewer's dynamic
    # 8-25 turn budget semantics; max_iters is set far above it so the
    # generic node cap can never fire first.
    async def _loop_emit(event_type: str, data: dict) -> None:
        if event_type in ("tool_call", "tool_result"):
            await _emit(event_type, data)

    _turn_records: list = []

    def _call_tools_turns() -> int:
        return sum(
            1 for t in _turn_records if t.get("node_kind") == "call_tools"
        )

    loop_spec = AgentLoopSpec(
        agent_role=agent_id,
        model=model,
        turn_timeout=(
            _wallclock_cap if _wallclock_cap < CORRECTION_TURN_TIMEOUT
            else CORRECTION_TURN_TIMEOUT
        ),
        phase_map={},
        phase_message=lambda role, phase: "",
        # Never the binding constraint — the call-tools cap is. ~2 nodes per
        # tool turn + slack keeps pydantic-ai's silent request_limit=50
        # relationship unchanged (model requests ≈ max_turns + 1 ≤ 26).
        max_iters=max_turns * 2 + 10,
        call_tools_cap=max_turns,
        wallclock_timeout=_wallclock_cap,
        stream_model_nodes=False,
        # Pre-migration behaviour: only node-to-node advancement was timed,
        # never the inner tool/model streams — so a legitimately long tool
        # call (e.g. apply_fix workbook IO) isn't cancelled mid-execution at
        # the per-turn timeout. Same opt-out as notes/coordinator.py's
        # notes_spec (bound_inner_streams=False precedent).
        bound_inner_streams=False,
    )

    try:
        async with agent.iter(prompt, deps=deps) as agent_run:
            await run_agent_loop(
                agent_run, deps, loop_spec, _loop_emit, _turn_records,
            )
        turn_count = _call_tools_turns()
        outcome["writes_performed"] = deps.writes_performed
        outcome["flags_raised"] = deps.flags_raised
        outcome["turns_used"] = turn_count
        await _emit("complete", {
            "success": True, "writes_performed": deps.writes_performed,
            "flags_raised": deps.flags_raised,
            "turns_used": turn_count, "max_turns": max_turns,
        })
    except _asyncio.CancelledError:
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        outcome["error"] = "cancelled"
        raise
    except CallToolsCapExceeded:
        # Exhausted the turn budget. Record + emit here, then fall through to
        # the shared cascade so pre-exhaustion leaf writes propagate (peer-
        # review P2). run_agent_loop raises BEFORE processing the over-cap
        # node, so the recorded call-tools turns equal the cap exactly.
        turns_used = _call_tools_turns()
        msg = (
            f"Reviewer exhausted its turn budget ({max_turns}) after "
            f"{deps.writes_performed} write(s)."
        )
        logger.warning(msg)
        outcome.update({
            "error": "reviewer_exhausted", "exhausted": True,
            "turns_used": turns_used,
            "writes_performed": deps.writes_performed,
            "flags_raised": deps.flags_raised,
        })
        await _emit("error", {"message": msg})
        await _emit("complete", {
            "success": False, "error": "reviewer_exhausted",
            "writes_performed": deps.writes_performed,
            "turns_used": turns_used, "max_turns": max_turns,
        })
    except WallclockExceeded:
        # Keep the pre-migration message (it names the writes performed,
        # which the generic loop exception can't know).
        msg = (
            f"Reviewer exceeded wall-clock cap of {_wallclock_cap}s "
            f"after {deps.writes_performed} write(s)."
        )
        logger.warning(msg)
        outcome["error"] = "reviewer_wallclock_exceeded"
        outcome["writes_performed"] = deps.writes_performed
        outcome["flags_raised"] = deps.flags_raised
        await _emit("error", {"type": "reviewer_wallclock_exceeded", "message": msg})
        await _emit("complete", {
            "success": False, "error": "reviewer_wallclock_exceeded",
            "writes_performed": deps.writes_performed,
        })
    except Exception:  # noqa: BLE001
        # The agent loop runs LLM calls whose exceptions can embed the bearer
        # token in str(e); surface a stable code, log the trace server-side
        # only (mirrors /test-connection).
        logger.exception("Reviewer run failed")
        outcome["error"] = "reviewer_exception"
        msg = "Reviewer run failed; see server logs for details."
        await _emit("error", {"type": "reviewer_exception", "message": msg})
        await _emit("complete", {"success": False, "error": outcome["error"]})
    finally:
        # Phase 4: persist the reviewer's transcript so its turn-by-turn
        # judgement is auditable in the Review tab (gotcha #6 — a FAILED /
        # exhausted pass is exactly when the trace matters most). Prefer the
        # finished result; fall back to the partial message history on a
        # failure path (mirrors the extraction failure-trace helper). Always
        # best-effort — a trace-save error must never mask the pass outcome.
        if _review_out_dir:
            try:
                from agent_tracing import (
                    save_agent_trace, save_messages_trace,
                )
                # Prefix = agent_id ("CORRECTION") so the file matches the
                # reviewer's run_agents.statement_type and is served by the
                # EXISTING /api/runs/{id}/agents/{stmt}/trace route — no new
                # endpoint needed (that route whitelists by run_agents row).
                _res = getattr(agent_run, "result", None)
                if _res is not None:
                    save_agent_trace(_res, _review_out_dir, agent_id)
                elif agent_run is not None:
                    save_messages_trace(
                        agent_run.ctx.state.message_history,
                        _review_out_dir, agent_id,
                    )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to save reviewer trace for run %s", run_id,
                    exc_info=True,
                )

    # Telemetry rollups for the CORRECTION run_agents row — captured ONCE
    # here so every exit path (success, exhausted, wallclock, exception)
    # reports them: a failed pass still burned real turns and tokens.
    # Run-168 QA finding: the Activity row showed "0 turns · 0 tool calls"
    # next to 364k tokens because only the success path recorded turns and
    # nobody recorded tool calls. Advisory by contract — a capture failure
    # must never mask the pass outcome.
    #
    # ASSIGN, don't setdefault: `outcome` is seeded with turns_used=0 at
    # construction (so the early construction-failure returns report 0),
    # which means a setdefault here is a permanent no-op — the wall-clock and
    # generic-exception handlers never set turns_used, so they would persist 0
    # despite real reviewer activity (Codex review P2). `_call_tools_turns()`
    # is the authoritative count on every path that reaches here (the success
    # and exhausted handlers set the same value earlier), and the cancel path
    # re-raises before this line, so an unconditional assign is correct.
    outcome["turns_used"] = _call_tools_turns()
    outcome["tool_call_count"] = sum(
        int(t.get("_n_tool_calls") or 0) for t in _turn_records
    )
    try:
        from pricing import estimate_cost as _ec
        _u = agent_run.usage()
        outcome["total_tokens"] = int(_u.total_tokens or 0)
        outcome["prompt_tokens"] = _in_tokens(_u)
        outcome["completion_tokens"] = _out_tokens(_u)
        outcome["total_cost"] = _ec(
            _in_tokens(_u), _out_tokens(_u), 0, model)
    except Exception:  # noqa: BLE001
        logger.debug("reviewer token capture skipped")

    # Item 14: surface the per-kind apply_fix rejection tally. deps is bound
    # for every non-cancel exit (construction failures returned earlier), so
    # one assignment here covers success / exhaustion / wallclock / exception.
    outcome["fix_rejections"] = dict(deps.rejections)

    # Re-cascade so the reviewer's leaf fixes propagate to parent totals
    # before the caller re-exports + re-merges from facts. Item 11: a recompute
    # failure here leaves parent totals stale while leaf fixes are committed —
    # surface it on the outcome (rides into run_review_tasks.outcome_json) so
    # the Review tab can warn instead of showing silently-stale totals.
    try:
        from concept_model.cascade import recompute_after_turn
        recompute_after_turn(db_path, run_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("post-reviewer cascade failed for run %s", run_id)
        outcome["cascade_error"] = f"{type(exc).__name__}: {exc}"

    _stamp_elapsed()
    return outcome


# Banner meta-row sentinel: one row per run with note_num = COVERAGE_META_NOTE
# carries the checklist banner state in its `status` column. Lets the API tell
# `inventory_unavailable` (feature ran, empty inventory → rows present but only
# this marker) from `pre_feature` (feature never ran → no rows at all).
COVERAGE_META_NOTE = -1


def _compute_notes_coverage_checklist(
    run_id: int, db_path: str, *,
    note_verdicts=None, subnote_verdicts=None, reviewer_added_notes=None,
    skip_receipts=None,
):
    """Build the holistic coverage checklist for a run straight from the durable
    DB inputs (inventory × provenance), merging any reviewer verdicts. Mirrors
    the reviewer's ``_build_context`` but standalone so the construction-failure
    path (no deps) can still persist a draft. ``skip_receipts`` marks
    intentionally-skipped Sheet-12 notes `skipped` instead of `missing`."""
    from db import repository as repo
    from notes.detectors import load_provenance_entries
    from notes.coverage_checklist import build_draft_checklist

    with repo.db_session(db_path) as conn:
        inventory_rows = repo.fetch_notes_inventory(conn, run_id)
    entries = load_provenance_entries(run_id, db_path)
    return build_draft_checklist(
        inventory_rows=inventory_rows, provenance_entries=entries,
        skip_receipts=skip_receipts,
        note_verdicts=note_verdicts, subnote_verdicts=subnote_verdicts,
        reviewer_added_notes=reviewer_added_notes,
    )


def _persist_notes_coverage(run_id: int, db_path: str, checklist, *, reviewed: bool):
    """Write the checklist to ``notes_coverage_rows`` (wholesale) + a banner
    meta row, and return a summary dict. Best-effort; caller wraps."""
    from db import repository as repo
    from notes.coverage_checklist import checklist_to_db_rows

    if not checklist.inventory_available:
        banner = "inventory_unavailable"
    else:
        banner = "reviewed" if reviewed else "not_reviewed"
    rows = [{"note_num": COVERAGE_META_NOTE, "subnote_ref": None, "status": banner}]
    rows.extend(checklist_to_db_rows(checklist))
    with repo.db_session(db_path) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, rows)
    return {
        "banner": banner,
        "inventory_available": checklist.inventory_available,
        "unresolved": len(checklist.unresolved_rows()),
        "counts": checklist.counts(),
    }


def _jsonsafe_reviewer_context(context: dict) -> dict:
    """A JSON-serialisable shallow copy of a reviewer context: the detector
    families are already plain dicts, but ``coverage_checklist`` is a Checklist
    object (the outcome is serialised into ``notes_review_tasks``)."""
    safe = dict(context)
    cl = safe.get("coverage_checklist")
    if cl is not None and hasattr(cl, "to_dict"):
        safe["coverage_checklist"] = cl.to_dict()
    return safe


async def _run_notes_reviewer_pass(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    filing_level: str,
    filing_standard: str,
    model,
    output_dir: str,
    merged_workbook_path: Optional[str],
    event_queue,
    sidecar_paths: Optional[list] = None,
    inventory_note_nums: Optional[list] = None,
    inventory_subnotes: Optional[dict] = None,
    agent_id: str = NOTES_VALIDATOR_AGENT_ID,
) -> dict:
    """Notes reviewer pass (docs/PLAN.md Step 9) — the acting successor to the
    notes validator. Inspects the five prose-notes check families and FIXES
    them through the guarded, snapshot-protected tools in
    ``notes/reviewer_agent.py``. Writes land in ``notes_cells`` (canonical); the
    download overlay reflects them, and we refresh the durable merged workbook
    on disk too.

    Reversibility is structural: ``ensure_notes_snapshot`` captures the original
    prose ONCE before any write, so the Review tab's "Revert to original"
    restores it. A pass failure is recoverable (the run still lands
    ``completed_with_errors``).
    """
    import asyncio as _asyncio
    import shutil as _shutil
    import time as _wc_time
    import server as _server_self
    from agent_runner import AgentLoopSpec, WallclockExceeded, run_agent_loop
    from notes.reviewer_agent import create_notes_reviewer_agent
    from notes.versioning import ensure_notes_snapshot
    from correction.reviewer_agent import compute_reviewer_turn_cap

    outcome: dict = {
        "invoked": False, "writes_performed": 0, "flags_raised": 0,
        "error": None, "context": {}, "elapsed_seconds": 0.0,
    }
    _pass_start = _wc_time.monotonic()

    def _stamp_elapsed() -> None:
        outcome["elapsed_seconds"] = round(_wc_time.monotonic() - _pass_start, 1)

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        if event_type == "error":
            data = {"bucket": ERROR_BUCKET_RECOVERABLE, **data}
        await event_queue.put({
            "event": event_type,
            "data": {**data, "agent_id": agent_id, "agent_role": agent_id},
        })

    # Holstic coverage checklist (docs/PLAN-notes-coverage-and-routing.md
    # Phase 6). `_deps_box` lets the finalizer read the reviewer's accumulated
    # verdicts once construction succeeds; the construction-failure path builds
    # the DRAFT straight from the DB (no verdicts) under a not_reviewed banner.
    _deps_box: dict = {}

    async def _finalize_coverage(reviewed: bool) -> None:
        """Recompute + persist the coverage checklist and emit notes_coverage.
        Gated on XBRL_NOTES_COVERAGE; best-effort (never fails the pass)."""
        if not _notes_coverage_enabled():
            return
        try:
            from notes.coverage_checklist import load_notes12_skips
            skips = load_notes12_skips(output_dir)
            d = _deps_box.get("deps")
            if d is not None:
                checklist = _compute_notes_coverage_checklist(
                    run_id, db_path, skip_receipts=skips,
                    note_verdicts=d.coverage_note_verdicts,
                    subnote_verdicts=d.coverage_subnote_verdicts,
                    reviewer_added_notes=d.authored_note_nums,
                )
            else:
                checklist = _compute_notes_coverage_checklist(
                    run_id, db_path, skip_receipts=skips)
            summary = _persist_notes_coverage(
                run_id, db_path, checklist, reviewed=reviewed)
            outcome["coverage"] = summary
            await _emit("notes_coverage", {
                "checklist": checklist.to_dict(), **summary})
            # Loud empty-inventory contract (PRD success criterion 2 / gotcha
            # #20): surface a structured warning instead of a silent green.
            if summary["banner"] == "inventory_unavailable":
                await _emit("error", {
                    "type": "notes_inventory_unavailable",
                    "message": "Notes inventory unavailable — coverage could "
                               "not be checked.",
                })
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to finalize notes coverage checklist for run %s",
                run_id, exc_info=True)

    try:
        agent, deps, context = create_notes_reviewer_agent(
            run_id=run_id, db_path=db_path, pdf_path=pdf_path,
            filing_level=filing_level, filing_standard=filing_standard,
            model=model, output_dir=output_dir,
            inventory_note_nums=inventory_note_nums,
            inventory_subnotes=inventory_subnotes,
            sidecar_paths=sidecar_paths,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Notes reviewer construction failed")
        outcome["error"] = f"agent construction failed: {e}"
        # Construction failed → persist the DRAFT checklist under a
        # not_reviewed banner (PRD Flow A error state) so the UI still shows
        # coverage instead of nothing.
        await _finalize_coverage(reviewed=False)
        await _emit("error", {"type": "notes_reviewer_exception",
                              "message": outcome["error"]})
        await _emit("complete", {"success": False, "error": outcome["error"]})
        _stamp_elapsed()
        return outcome

    _deps_box["deps"] = deps
    outcome["context"] = _jsonsafe_reviewer_context(context)
    from notes.reviewer_agent import count_open_items
    # count_open_items folds the coverage checklist's unresolved rows into the
    # gate, so a run whose only problem is a suspected numbering gap still runs
    # the reviewer (no detector family fires for that alone).
    n_items = count_open_items(context)

    # Nothing flagged — skip the model entirely (latency + tokens). Emit a
    # status + success so the tab flips terminal instead of stranding. The
    # draft checklist IS the final state here (nothing to resolve).
    if n_items == 0:
        await _finalize_coverage(reviewed=True)
        await _emit("status", {"phase": "complete",
                               "message": "No notes findings to review — skipped."})
        await _emit("complete", {"success": True, "writes_performed": 0,
                                 "skipped": True})
        _stamp_elapsed()
        return outcome

    # Snapshot the ORIGINAL prose before any write (reversibility). Failure here
    # means no safety net, so refuse to run (surface structurally, gotcha #20).
    try:
        ensure_notes_snapshot(db_path, run_id)
    except Exception:  # noqa: BLE001
        logger.exception("Notes reviewer snapshot failed for run %s", run_id)
        outcome["error"] = "snapshot_failed"
        await _finalize_coverage(reviewed=False)
        await _emit("error", {"type": "notes_reviewer_exception",
                              "message": "Notes reviewer snapshot failed."})
        await _emit("complete", {"success": False, "error": outcome["error"]})
        _stamp_elapsed()
        return outcome

    outcome["invoked"] = True
    max_turns = compute_reviewer_turn_cap(filing_level=filing_level, n_items=n_items)
    await _emit("status", {
        "phase": "started",
        "message": f"AI review of the notes started: checking {n_items} item(s) against the PDF.",
    })

    prompt = (
        "Investigate every finding in your NOTES REVIEW PACKET. For each, view "
        "the PDF page(s) FIRST, then fix what you can ground (clear a duplicate, "
        "move a collision to an empty leaf, author/edit a missing sub-note) and "
        f"raise_flag anything you're unsure of. You have at most {max_turns} "
        "turns. Never fabricate prose; preserve valid content over a risky fix."
    )

    _wallclock_cap = float(getattr(
        _server_self, "NOTES_VALIDATOR_WALLCLOCK_TIMEOUT",
        NOTES_VALIDATOR_WALLCLOCK_TIMEOUT,
    ))

    async def _loop_emit(event_type: str, data: dict) -> None:
        if event_type in ("tool_call", "tool_result"):
            await _emit(event_type, data)

    _turn_records: list = []
    loop_spec = AgentLoopSpec(
        agent_role=agent_id, model=model,
        turn_timeout=(
            _wallclock_cap if _wallclock_cap < NOTES_VALIDATOR_TURN_TIMEOUT
            else NOTES_VALIDATOR_TURN_TIMEOUT
        ),
        phase_map={}, phase_message=lambda role, phase: "",
        wallclock_timeout=_wallclock_cap,
        stream_model_nodes=False,
        bound_inner_streams=False,
    )

    def _persist_flags_and_refresh(replace_flags: bool = True) -> None:
        """Persist the reviewer's flags + refresh the durable merged workbook
        from notes_cells. Best-effort — never fails the pass.

        Flag persistence NEVER touches ``answered`` / ``dismissed`` rows — a human
        answer is durable guidance and must survive a re-review (peer-review HIGH).
        ``replace_flags=True`` (a COMPLETED pass) supersedes only the prior OPEN
        flags with this pass's set; ``replace_flags=False`` (a cancelled / timed-
        out / errored pass) APPENDS whatever this pass raised without deleting,
        so an interrupted rerun can't erase prior open flags either.
        """
        try:
            from db import repository as _repo
            with _repo.db_session(db_path) as conn:
                if replace_flags:
                    conn.execute(
                        "DELETE FROM notes_review_flags WHERE run_id = ? "
                        "AND status = 'open'", (run_id,)
                    )
                for f in deps.flags:
                    _repo.insert_notes_review_flag(
                        conn, run_id=run_id, kind=f.get("kind", "needs_human"),
                        reason=f.get("reason", ""), sheet=f.get("sheet"),
                        row=f.get("row"),
                    )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to persist notes-review flags", exc_info=True)
        # Refresh the on-disk merged workbook so non-download consumers see the
        # reviewer's edits (the download overlay already reflects notes_cells).
        if deps.writes_performed and merged_workbook_path:
            try:
                from notes.persistence import overlay_notes_cells_into_workbook
                nxt = overlay_notes_cells_into_workbook(
                    xlsx_path=merged_workbook_path, run_id=run_id, db_path=db_path,
                    filing_level=filing_level,
                )
                if str(nxt) != str(merged_workbook_path):
                    _shutil.copyfile(nxt, merged_workbook_path)
                    try:
                        Path(nxt).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                logger.warning("Failed to refresh merged workbook after notes review",
                               exc_info=True)

    try:
        async with agent.iter(prompt, deps=deps) as agent_run:
            await run_agent_loop(agent_run, deps, loop_spec, _loop_emit, _turn_records)
        outcome["writes_performed"] = deps.writes_performed
        outcome["flags_raised"] = len(deps.flags)
        _persist_flags_and_refresh()
        try:
            log_path = Path(output_dir) / "notes_reviewer_log.json"
            log_path.write_text(
                json.dumps(deps.correction_log, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to write notes_reviewer_log.json", exc_info=True)
        # FINAL checklist — the reviewer's verdicts + authored notes are now
        # reflected; this post-reviewer state is what the human sees.
        await _finalize_coverage(reviewed=True)
        await _emit("complete", {
            "success": True,
            "writes_performed": deps.writes_performed,
            "flags_raised": len(deps.flags),
        })
    except _asyncio.CancelledError:
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        outcome["error"] = "cancelled"
        # Interrupted — append any raised flags, never delete prior open/answered.
        _persist_flags_and_refresh(replace_flags=False)
        # Persist whatever coverage the (partial) pass reached under a
        # not_reviewed banner so a Stop-All'd notes run still shows a checklist
        # instead of falling back to pre_feature. Best-effort (swallows), so it
        # cannot suppress the re-raise that finalizes the run as aborted.
        await _finalize_coverage(reviewed=False)
        raise
    except (WallclockExceeded, _asyncio.TimeoutError):
        msg = (f"Notes reviewer exceeded wall-clock cap of {_wallclock_cap}s "
               f"after {deps.writes_performed} write(s).")
        logger.warning(msg)
        outcome["error"] = "notes_reviewer_wallclock_exceeded"
        outcome["writes_performed"] = deps.writes_performed
        _persist_flags_and_refresh(replace_flags=False)
        # Reviewer ran but exhausted its budget — persist what it resolved as
        # the FINAL state (any still-missing rows correctly tip run status).
        await _finalize_coverage(reviewed=True)
        await _emit("error", {"type": "notes_reviewer_wallclock_exceeded", "message": msg})
        await _emit("complete", {"success": False,
                                 "error": "notes_reviewer_wallclock_exceeded",
                                 "writes_performed": deps.writes_performed})
    except Exception as e:  # noqa: BLE001
        logger.exception("Notes reviewer pass failed")
        outcome["error"] = str(e)
        _persist_flags_and_refresh(replace_flags=False)
        # The pass crashed mid-way — persist what we have under a not_reviewed
        # banner (PRD Flow A: reviewer pass fails → draft with banner).
        await _finalize_coverage(reviewed=False)
        await _emit("error", {"type": "notes_reviewer_exception", "message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})

    _stamp_elapsed()
    return outcome


# ---------------------------------------------------------------------------
# Conversation trace saving
# ---------------------------------------------------------------------------

def _save_trace(result, output_dir: str):
    """Save conversation trace (minus binary image data) for debugging."""
    import dataclasses

    trace_path = Path(output_dir) / "conversation_trace.json"
    messages = []
    for msg in result.all_messages():
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump(mode="json")
        elif dataclasses.is_dataclass(msg):
            msg_dict = dataclasses.asdict(msg)
        else:
            msg_dict = {"raw": str(msg)}
        _strip_binary(msg_dict)
        messages.append(msg_dict)

    usage_data = None
    if result.usage:
        usage_data = result.usage.model_dump(mode="json") if hasattr(result.usage, "model_dump") else str(result.usage)

    trace = {
        "messages": messages,
        "usage": usage_data,
        "output": result.output if isinstance(result.output, str) else str(result.output),
    }
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")


def _strip_binary(obj):
    """Recursively strip binary/image data from message dicts to keep traces readable."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key == "data" and isinstance(obj.get("media_type"), str) and "image" in obj["media_type"]:
                obj[key] = f"[{obj['media_type']} image data stripped]"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)


# ---------------------------------------------------------------------------
# Async streaming agent runner — replaces the old thread + EventQueue pattern.
# Uses PydanticAI's agent.iter() to get granular streaming events.
# ---------------------------------------------------------------------------

## iter_agent_events was removed in Phase 11.3 — the legacy single-agent
## streaming path has been replaced by the multi-agent coordinator.
## Use POST /api/run/{session_id} with RunConfigRequest instead.


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

def _check_auth_startup_config() -> None:
    """Refuse to start a misconfigured production auth layer (PLAN auth 1.2).

    Three production-only guards (no effect locally):
      1. AUTH_MODE=dev must never reach Azure — the dev auto-session bypass
         would serve confidential data with no login at all.
      2. SESSION_SECRET must be set — otherwise cookies are signed with the
         insecure dev fallback and are forgeable.
      3. At least one ENABLED account must exist — an empty (or all-disabled)
         account table on Azure is either a lockout or, with SSO not yet wired,
         a wide-open misconfiguration.

    Raises RuntimeError (which aborts FastAPI startup) with an actionable
    message rather than degrading.
    """
    from auth import config as auth_config

    if not auth_config.is_production():
        return  # local dev: nothing to enforce

    if auth_config.dev_mode_enabled():
        raise RuntimeError(
            "AUTH_MODE=dev is set on Azure (WEBSITE_SITE_NAME present). The dev "
            "auto-session bypass must never run in production — remove the "
            "AUTH_MODE App Setting."
        )
    if not os.environ.get("SESSION_SECRET"):
        raise RuntimeError(
            "SESSION_SECRET is not set in production. Set it (e.g. 64 random hex "
            "chars) as an App Setting before starting the server."
        )
    conn = _open_audit_conn()
    try:
        from db import repository as repo
        enabled = repo.count_auth_users(conn, enabled_only=True)
    finally:
        conn.close()
    if enabled == 0:
        raise RuntimeError(
            "No enabled login accounts exist. Seed at least one with "
            "`python -m auth.manage add-user EMAIL --name \"...\"` before "
            "starting the server in production."
        )


def _bootstrap_admin_from_env(conn) -> Optional[str]:
    """Idempotently seed a REAL admin account from the environment.

    Local-dev convenience: with BOOTSTRAP_ADMIN_EMAIL + BOOTSTRAP_ADMIN_PASSWORD
    set (e.g. in .env), a `start.bat` / `start.sh` dev gets a working
    email+password admin login WITHOUT the `python -m auth.manage` CLI step.
    Opt-in — does nothing unless BOTH vars are set.

    Non-destructive + idempotent: on an email that already exists it only
    ensures the admin role; it never resets the password or re-enables a
    disabled account, so a dev who later rotates their password (or an admin who
    disabled the row) isn't undone on every reboot. Returns a short status
    string for logging, or None when the feature is off / inert.
    """
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return None  # feature off — neither var set

    from auth import passwords
    from db import repository as repo

    if len(password) < passwords.MIN_PASSWORD_LEN:
        logger.warning(
            "BOOTSTRAP_ADMIN_PASSWORD is shorter than %d chars — skipping "
            "admin bootstrap for %r", passwords.MIN_PASSWORD_LEN, email,
        )
        return "skipped-short-password"

    existing = repo.fetch_auth_user(conn, email)
    if existing is not None:
        # Account already provisioned: only ever PROMOTE (mirrors the CLI's
        # add-user --admin "set, never demote" rule). Leave password + disabled
        # untouched.
        if not existing.is_admin:
            repo.set_auth_user_admin(conn, email, True)
            return "promoted-existing"
        return "already-present"

    name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "").strip() or "Local Admin"
    repo.upsert_auth_user(conn, email, name, passwords.hash_password(password))
    repo.set_auth_user_admin(conn, email, True)
    return "created"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Create / migrate the audit DB once at startup (peer-review #9).

    Previously every `_open_audit_conn` call re-ran `init_db`, which is
    idempotent but paid ~1ms of CREATE-IF-NOT-EXISTS churn on every
    request to the history and notes_cells endpoints. Running it once
    at startup keeps the schema-migration guarantee (v2/v3 migrations
    still land on first boot after deploy) without the per-request cost.
    """
    from db.schema import init_db
    init_db(AUDIT_DB_PATH)

    # Optional env-driven admin bootstrap (local-dev convenience): seed a REAL
    # auth_users admin from BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD so
    # start.bat / start.sh devs get a working login without the
    # `python -m auth.manage` CLI step. Opt-in + idempotent. Runs BEFORE the
    # prod auth-config check below so a freshly-seeded box also satisfies the
    # "at least one enabled account" guard. Best-effort — a bootstrap failure
    # must not block startup (the config check still fails closed in prod).
    try:
        conn = _open_audit_conn()
        try:
            _status = _bootstrap_admin_from_env(conn)
            conn.commit()
        finally:
            conn.close()
        if _status in ("created", "promoted-existing"):
            logger.info(
                "bootstrap admin %r from env (%s)",
                os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip(), _status,
            )
    except Exception:
        logger.warning("admin bootstrap from env failed at startup",
                       exc_info=True)

    # Fail-closed auth config check (PLAN auth Phase 1.2). Mirrors the
    # canonical-bootstrap fail-fast philosophy: a misconfigured production auth
    # layer must NOT boot into a wide-open or self-locked-out state. These
    # checks only bite in production (WEBSITE_SITE_NAME present) — local dev is
    # unaffected.
    _check_auth_startup_config()

    # Retire any manual re-review pass left `running` by a previous process
    # (Phase 5.3). The pass runs on a daemon thread that dies with the
    # process, so a surviving `running` row can never complete: a poll would
    # hang and a relaunch would be blocked by the re-entrancy guard. Flip
    # those rows to a terminal error so the UI resolves and the user can
    # relaunch. Best-effort — a reconcile failure must not block startup.
    try:
        from db import repository as repo
        conn = _open_audit_conn()
        try:
            n = repo.reconcile_stale_review_tasks(conn)
            conn.commit()
        finally:
            conn.close()
        if n:
            logger.info("reconciled %d stale re-review task(s) at startup", n)
    except Exception:
        logger.warning("re-review task reconciliation failed at startup",
                       exc_info=True)

    # Retire notes re-reviews orphaned by a restart (v24) — same discipline.
    try:
        from db import repository as repo
        conn = _open_audit_conn()
        try:
            n = repo.reconcile_stale_notes_review_tasks(conn)
            conn.commit()
        finally:
            conn.close()
        if n:
            logger.info("reconciled %d stale notes re-review task(s) at startup", n)
    except Exception:
        logger.warning("notes re-review task reconciliation failed at startup",
                       exc_info=True)

    # Retire notes formatter tasks orphaned by a restart (v26).
    try:
        from db import repository as repo
        conn = _open_audit_conn()
        try:
            n = repo.reconcile_stale_notes_format_tasks(conn)
            conn.commit()
        finally:
            conn.close()
        if n:
            logger.info("reconciled %d stale notes formatter task(s) at startup", n)
    except Exception:
        logger.warning("notes formatter task reconciliation failed at startup",
                       exc_info=True)

    # Retire extraction runs left `running` by a dead process (UX-QA #2). The
    # run executes inside a streaming request that dies with the process, so a
    # surviving `running` row is a History dead-end (Download/Delete disabled,
    # no live indicator). At startup EVERY `running` row is orphaned — no
    # stream can have started yet (this runs before requests are served and
    # `active_runs` is empty) — so reap all of them (`max_age_hours=0`), not
    # just old ones. Reaping only >6h rows left a crash-then-immediate-restart
    # orphan `running` until the next late restart (peer review). Flipping to
    # `aborted` upholds the lifecycle contract (gotcha #10) across restarts.
    # Best-effort — must not block startup.
    try:
        from db import repository as repo
        conn = _open_audit_conn()
        try:
            n = repo.reconcile_stale_runs(conn, max_age_hours=0)
            conn.commit()
        finally:
            conn.close()
        if n:
            logger.info("reconciled %d orphaned run(s) at startup", n)
    except Exception:
        logger.warning("stale run reconciliation failed at startup",
                       exc_info=True)

    # Sweep auth sessions that idled out and were never accessed again (the user
    # closed the tab). resolve_session deletes expired rows lazily on access, so
    # this only reaps the never-touched-again ones; without it the table grows
    # unboundedly. Best-effort — a sweep failure must not block startup.
    try:
        from datetime import datetime, timedelta, timezone
        from auth import config as auth_config
        from db import repository as repo
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=auth_config.idle_timeout_seconds())
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        conn = _open_audit_conn()
        try:
            swept = repo.sweep_expired_auth_sessions(conn, cutoff)
            conn.commit()
        finally:
            conn.close()
        if swept:
            logger.info("swept %d expired auth session(s) at startup", swept)
    except Exception:
        logger.warning("auth session sweep failed at startup", exc_info=True)
    # Canonical mode: import every face template's concept tree so the
    # Concepts UI has a tree to render and the facts API can resolve
    # concept_uuids. Idempotent (deterministic UUID5), so it's safe on
    # every boot; gated on the flag to keep legacy startup fast.
    if _canonical_mode_enabled():
        global _CANONICAL_BOOTSTRAP_OK
        try:
            from concept_model.bootstrap import (
                import_all_face_templates,
                import_all_notes_templates,
            )
            ids = import_all_face_templates(AUDIT_DB_PATH)
            # Notes templates (PLAN-notes-template-registry): prose → notes_nodes,
            # numeric → concept_nodes. Shares the same fail-fast guard so a notes
            # bootstrap failure is as loud as a face one.
            notes_ids = import_all_notes_templates(AUDIT_DB_PATH)
            _CANONICAL_BOOTSTRAP_OK = True
            logger.info(
                "canonical mode: imported %d face + %d notes templates",
                len(ids),
                len(notes_ids),
            )
        except Exception:
            # Don't hard-crash the server — the legacy UI / History share this
            # process. But mark canonical mode unhealthy and log at ERROR so a
            # silent empty-Concepts-UI run is impossible to miss (peer-review).
            _CANONICAL_BOOTSTRAP_OK = False
            logger.error(
                "canonical mode: face-template bootstrap FAILED — concept "
                "trees are not imported; Concepts UI will be empty and fact "
                "projection will skip every cell. Fix the import and restart.",
                exc_info=True,
            )
    yield


app = FastAPI(title="XBRL Agent", version="0.3.0", lifespan=_lifespan)

# Track active extraction runs by session_id
active_runs: set[str] = set()


# Canonical concept-model facts API. Mounted unconditionally and always
# active — canonical mode is mandatory (rewrite Phase 1.1); there is no
# legacy direct-Excel-write fallback. The router uses a getter for
# AUDIT_DB_PATH so tests can swap the module attribute at runtime.
from concept_model.facts_api import register_facts_routes as _register_facts_routes
_register_facts_routes(app, lambda: AUDIT_DB_PATH)
from concept_model.concepts_routes import register_concept_routes as _register_concept_routes
_register_concept_routes(app, lambda: AUDIT_DB_PATH)
# Reviewer tab read surface (docs/Archive/PLAN-reviewer-agent.md): GET /review +
# POST /flags/{id}/answer. The heavier re-review / revert endpoints that
# need server orchestration are defined further down in this module.
from concept_model.reviewer_routes import register_reviewer_routes as _register_reviewer_routes
_register_reviewer_routes(app, lambda: AUDIT_DB_PATH)


# ---------------------------------------------------------------------------
# Request models for multi-agent endpoints
# ---------------------------------------------------------------------------

class RunConfigRequest(BaseModel):
    """Request body for POST /api/run/{session_id}."""
    statements: List[str]  # e.g. ["SOFP", "SOPL"]
    variants: Dict[str, str] = {}  # e.g. {"SOFP": "CuNonCu"}
    models: Dict[str, str] = {}  # per-statement model overrides
    infopack: Optional[Dict] = None  # serialised Infopack JSON (nullable)
    use_scout: bool = False  # informational — actual infopack presence controls behaviour
    filing_level: Literal["company", "group"] = "company"
    # Filing standard axis, orthogonal to filing_level. Defaults to "mfrs"
    # so existing frontends (and persisted `run_config_json` blobs on
    # legacy rows) continue to resolve to the MFRS template tree without
    # changes. `"mpers"` routes through XBRL-template-MPERS/ and enables
    # the SoRE variant on SOCIE.
    filing_standard: Literal["mfrs", "mpers"] = "mfrs"
    # Presentation denomination the user declares for the source statements.
    # The figures in MBRS statements are reported at a scale ("RM '000",
    # "RM mil", or actual RM); the agent transcribes figures verbatim and uses
    # this to know the unit authoritatively instead of guessing it from the
    # PDF header (a wrong unit silently 1000×'s every value). Vocabulary mirrors
    # scout's `scale_unit`. Default "thousands" (RM '000) is the common
    # Malaysian case; the scout still detects the scale and the run flags a
    # warning if it disagrees with this declaration.
    denomination: Literal["units", "thousands", "millions"] = "thousands"
    # Notes templates to fill, as NotesTemplateType.value strings (e.g.
    # ["CORP_INFO", "ISSUED_CAPITAL"]). Empty = face-only run.
    notes_to_run: List[str] = []
    # Per-notes-template model overrides, keyed by NotesTemplateType.value.
    # Unspecified templates fall back to the run's default model. Mirrors
    # ``models`` for face statements.
    notes_models: Dict[str, str] = {}
    # v10 audit column (kept for schema compatibility). The monolith
    # experiment that this flag once selected has been removed; only
    # `split` (the 5-specialist coordinator) is a live path now.
    orchestration: str = "split"

    @field_validator("orchestration")
    @classmethod
    def _normalize_orchestration(cls, v: str) -> str:
        # Only the split pipeline exists (monolith removed, rewrite Phase 1).
        # Coerce any other/legacy value — e.g. a pre-rewrite draft persisted
        # with "monolith" — to "split" so History never reports a path that
        # cannot actually run. (PR-2.)
        return "split"

    # Optional default-model override carried on the request body. Not read
    # by the split pipeline (per-statement `models` + the resolved RunConfig
    # default govern model selection); retained as a benign optional so older
    # clients posting it don't 422.
    model: Optional[str] = None

    # Gold-standard eval (v16): the benchmark this run is graded against. None
    # on every normal run — grading only fires when the extract-page "Eval
    # testing" toggle attached a benchmark. Persisted on runs.benchmark_id so
    # the end-of-run grading hook and the History/run-page surfaces can find it.
    benchmark_id: Optional[int] = None


class RunConfigPatchRequest(BaseModel):
    """Partial-update body for PATCH /api/runs/{id}.

    Mirrors RunConfigRequest but every field is optional — the frontend
    PreRunPanel emits debounced PATCHes for whichever fields changed
    since the last save (PLAN-persistent-draft-uploads.md, Phase B). The
    Literal validators on `filing_level`/`filing_standard` are preserved
    so a typo in the UI surfaces as a 422 instead of silently corrupting
    the stored config.

    Unset fields are excluded from the merge via `model_dump(exclude_unset=True)`
    in the handler, so a PATCH that only touches `statements` does not
    blank out the previously-saved `filing_level`.
    """

    statements: Optional[List[str]] = None
    variants: Optional[Dict[str, str]] = None
    models: Optional[Dict[str, str]] = None
    use_scout: Optional[bool] = None
    filing_level: Optional[Literal["company", "group"]] = None
    filing_standard: Optional[Literal["mfrs", "mpers"]] = None
    # Mirrors RunConfigRequest.denomination. Must be present here too, or a
    # debounced draft PATCH silently drops a non-default scale and the
    # draft-start path rebuilds the run at the "thousands" default — defeating
    # the whole point of the user-authoritative denomination.
    denomination: Optional[Literal["units", "thousands", "millions"]] = None
    notes_to_run: Optional[List[str]] = None
    notes_models: Optional[Dict[str, str]] = None
    # Only `split` exists (monolith removed). Unset (None) stays None so a
    # partial PATCH that doesn't touch orchestration won't clobber it; any
    # supplied value is coerced to "split". (PR-2.)
    orchestration: Optional[str] = None
    model: Optional[str] = None

    @field_validator("orchestration")
    @classmethod
    def _normalize_orchestration(cls, v):
        return None if v is None else "split"

    # Gold-standard eval (v16): the benchmark this draft run is graded
    # against, persisted with the rest of the draft config. None leaves the
    # run a normal (non-eval) run.
    benchmark_id: Optional[int] = None
    # `infopack` is the scout-derived inventory the legacy `POST /api/run/
    # {session_id}` endpoint receives in its request body. For the
    # persistent-draft flow there is no separate "scout output store" on
    # disk — the infopack only exists in the frontend's PreRunPanel
    # state until it's bundled with the run config. So we MUST persist
    # it here; otherwise PATCH→DB→start strips it and the coordinator
    # runs without scout's page hints (peer-review HIGH #1).
    infopack: Optional[Dict] = None


# --- Settings helpers ---

# Statement type keys used for per-agent model defaults
_AGENT_ROLES = ("scout", "reviewer", "notes_reviewer", "notes_formatter",
                "SOFP", "SOPL", "SOCI", "SOCF", "SOCIE")


def _auto_review_enabled() -> bool:
    """Whether the reviewer pass auto-runs after extraction (canonical mode).

    Controlled by ``XBRL_AUTO_REVIEW`` (default on). When off, a run with
    failing cross-checks / open conflicts simply finishes and the user
    triggers the reviewer manually from the Review tab. Read fresh from the
    environment each call so a Settings toggle takes effect without restart.
    """
    return os.environ.get("XBRL_AUTO_REVIEW", "true").lower() == "true"


def _notes_auto_review_enabled() -> bool:
    """Whether the notes reviewer pass auto-runs after the merge (default on).

    Controlled by ``XBRL_NOTES_AUTO_REVIEW``. Independent of ``XBRL_AUTO_REVIEW``
    (which gates the FACE reviewer). When off, a notes run finishes without the
    reviewer and the user triggers it manually from the Notes tab. Read fresh
    each call so a Settings toggle takes effect without restart.
    """
    return os.environ.get("XBRL_NOTES_AUTO_REVIEW", "true").lower() == "true"


def _notes_coverage_tips_status(coverage: Optional[dict]) -> bool:
    """Whether a run's notes coverage summary tips it to
    ``completed_with_errors`` (PRD Decision 3, docs/PLAN-notes-coverage-and-
    routing.md Phase 6 Step 9).

    Tips when there is at least one unresolved MISSING row / uninvestigated
    SUSPECTED-GAP (``unresolved`` > 0) OR the notes inventory was unavailable.
    A ``None`` summary (coverage didn't run) never tips; ``not_verified``
    sub-refs are counted as advisory and never reach ``unresolved``."""
    if not coverage:
        return False
    return bool(
        coverage.get("unresolved", 0) > 0
        or coverage.get("banner") == "inventory_unavailable"
    )


def _notes_coverage_enabled() -> bool:
    """Whether the holistic notes coverage checklist runs
    (docs/PLAN-notes-coverage-and-routing.md).

    Controlled by ``XBRL_NOTES_COVERAGE`` (default ON), mirroring the
    ``XBRL_SPOT_CHECK`` convention. When off, the reviewer pass skips building /
    persisting the checklist and coverage never tips run status — a config-flip
    rollback (Rollback Plan). Read fresh each call so a Settings toggle takes
    effect without a restart."""
    return os.environ.get("XBRL_NOTES_COVERAGE", "true").lower() == "true"


def _spot_check_enabled() -> bool:
    """Whether a CLEAN run (all cross-checks passed, no open conflicts) still
    gets a grounded spot-check (issue 1, 2026-06-21).

    Controlled by ``XBRL_SPOT_CHECK`` (default ON). Independent of
    ``XBRL_AUTO_REVIEW`` — that gates the FAILURE-driven reviewer; this gates
    the clean-run sanity pass. Read fresh each call so a Settings toggle takes
    effect without a restart.
    """
    return os.environ.get("XBRL_SPOT_CHECK", "true").lower() == "true"


def _spot_check_mode() -> str:
    """Spot-check depth: ``"light"`` (default) or ``"full"``.

    ``XBRL_SPOT_CHECK_MODE`` — ``light`` is a tight sanity pass over the
    high-value figures; ``full`` reuses the holistic reviewer even on a clean
    run. Any unrecognised value falls back to ``light``.
    """
    mode = os.environ.get("XBRL_SPOT_CHECK_MODE", "light").lower()
    return mode if mode in ("light", "full") else "light"


def _notes_table_style() -> dict:
    """The firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md).

    A JSON object in ``XBRL_NOTES_TABLE_STYLE`` (border colour/style, header
    fill, font/padding/spacing) that drives BOTH the notes editor preview and
    the clipboard paste so they match. Empty ``{}`` is the safe default — the
    frontend then falls back to each surface's historic look. Read fresh each
    call so a Settings change takes effect without a restart; a malformed value
    degrades to ``{}`` rather than breaking the settings/config endpoints.
    """
    raw = os.environ.get("XBRL_NOTES_TABLE_STYLE", "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _entity_memory_enabled() -> bool:
    """Whether per-entity advisory memory injects prior-year prompt hints (item 28).

    Controlled by ``XBRL_ENTITY_MEMORY`` (default on). Read fresh each call so a
    Settings toggle takes effect without a restart. Delegates to the single
    resolver in ``entity_memory`` so the server and coordinator agree.
    """
    from entity_memory import entity_memory_enabled

    return entity_memory_enabled()


def _reviewer_model_name() -> Optional[str]:
    """The configured reviewer model id, or None to inherit the run's model.

    Reads ``XBRL_DEFAULT_MODELS["reviewer"]`` if the user set a dedicated
    reviewer model; otherwise None so the caller falls back to the run's
    extraction model (the historical behaviour).
    """
    raw = os.environ.get("XBRL_DEFAULT_MODELS", "")
    try:
        models = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    val = models.get("reviewer")
    return val if isinstance(val, str) and val else None


def _notes_reviewer_model_name() -> Optional[str]:
    """The configured notes-reviewer model id, or None to inherit the run's model.

    Reads ``XBRL_DEFAULT_MODELS["notes_reviewer"]``; otherwise None so the caller
    falls back to the run's extraction model (mirrors ``_reviewer_model_name``).
    """
    raw = os.environ.get("XBRL_DEFAULT_MODELS", "")
    try:
        models = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    val = models.get("notes_reviewer")
    return val if isinstance(val, str) and val else None


def _notes_formatter_model_name() -> Optional[str]:
    """The configured notes-formatter model id, or None to inherit the run's model.

    Reads ``XBRL_DEFAULT_MODELS["notes_formatter"]``; otherwise None so the
    caller falls back to the run's extraction model (mirrors
    ``_notes_reviewer_model_name``).
    """
    raw = os.environ.get("XBRL_DEFAULT_MODELS", "")
    try:
        models = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    val = models.get("notes_formatter")
    return val if isinstance(val, str) and val else None


def _load_available_models() -> list[dict]:
    """Read the pinned model list from config/models.json.

    Re-reads on every call so edits are picked up without a redeploy.
    Returns an empty list if the file is missing or malformed.
    """
    models_file = CONFIG_DIR / "models.json"
    if not models_file.exists():
        return []
    try:
        return json.loads(models_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load config/models.json: %s", exc)
        return []


def _load_extended_settings() -> dict:
    """Read extended settings (default_models, scout toggle, tolerance) from .env.

    Extended keys are stored as dotenv entries with an XBRL_ prefix:
      XBRL_DEFAULT_MODELS = JSON object
      XBRL_SCOUT_ENABLED_DEFAULT = true/false
      XBRL_TOLERANCE_RM = float
    """
    raw_models = os.environ.get("XBRL_DEFAULT_MODELS", "")
    try:
        default_models = json.loads(raw_models) if raw_models else {}
    except json.JSONDecodeError:
        default_models = {}

    # Ensure every agent role has a key (fall back to the global model)
    global_model = os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    for role in _AGENT_ROLES:
        default_models.setdefault(role, global_model)

    scout_enabled = os.environ.get("XBRL_SCOUT_ENABLED_DEFAULT", "true").lower() == "true"

    try:
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
    except ValueError:
        tolerance = 1.0

    return {
        "default_models": default_models,
        "scout_enabled_default": scout_enabled,
        "tolerance_rm": tolerance,
        # Reviewer pass auto-trigger (docs/Archive/PLAN-reviewer-agent.md). Default on.
        "auto_review": _auto_review_enabled(),
        # Notes reviewer auto-trigger (docs/PLAN.md — Notes Reviewer). Default on.
        "notes_auto_review": _notes_auto_review_enabled(),
        # Issue 1 (2026-06-21): clean-run spot-check toggle + depth. Default on/light.
        "spot_check": _spot_check_enabled(),
        "spot_check_mode": _spot_check_mode(),
        # Notes coverage checklist (docs/PLAN-notes-coverage-and-routing.md). Default on.
        "notes_coverage": _notes_coverage_enabled(),
        # Item 28 — per-entity advisory memory (prior-year prompt hints). Default on.
        "entity_memory": _entity_memory_enabled(),
        # Firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md):
        # drives the notes editor preview + clipboard paste. {} = each surface
        # keeps its historic look until the firm sets a colour.
        "notes_table_style": _notes_table_style(),
    }


# --- Settings endpoints ---

# --- Multi-agent SSE run endpoint (Phase 7.2 + 7.3 + 7.4) ---

def _safe_mark_finished(
    db_conn: "Optional[Any]",
    run_id: Optional[int],
    status: str,
) -> bool:
    """Best-effort call to repo.mark_run_finished used from except/finally.

    Returns True on success. Swallows all exceptions so the calling handler
    (which is already dealing with one failure) never gets a second one
    from the audit write. The History page will simply not see this run
    if the DB is unhappy — that's acceptable, since the extraction itself
    has already failed or been cancelled.
    """
    if db_conn is None or run_id is None:
        return False
    try:
        from db import repository as repo
        repo.mark_run_finished(db_conn, run_id, status)
        db_conn.commit()
        return True
    except Exception:
        logger.warning(
            "Failed to mark run %s as %s in audit DB",
            run_id, status, exc_info=True,
        )
        return False


def _attempt_partial_merge(
    session_dir: Path,
    merged_path: str,
    statements_to_run,
    notes_to_run,
    db_conn: "Optional[Any]",
    run_id: Optional[int],
) -> dict:
    """PLAN-stop-and-validation-visibility Phase 2.1.

    Best-effort merge of any per-statement / per-notes workbooks already
    on disk. Called from the Stop-All cancel handler so a user-initiated
    abort doesn't throw away work that completed before the cancel
    signal. Mirrors the success-path merge block in
    ``run_multi_agent_stream`` (around line 1966) — the difference is we
    have no ``CoordinatorResult`` so we infer "what completed" purely
    from disk.

    Hardened against gotcha #10: every step is wrapped so the cancel
    handler never gets a second exception from this helper. If anything
    fails, returns ``merged=False`` with an ``error`` message — the run
    still finalizes as 'aborted' the way it always did.

    Returns a dict suitable for the ``partial_merge`` SSE payload:
        {
          "merged": bool,                 # did filled.xlsx land?
          "merged_path": str,
          "statements_included": [str],   # SOFP/SOPL/... values
          "notes_included": [str],
          "statements_missing": [str],    # requested but not on disk
          "notes_missing": [str],
          "error": Optional[str],
        }
    """
    out = {
        "merged": False,
        "merged_path": merged_path,
        "statements_included": [],
        "notes_included": [],
        "statements_missing": [],
        "notes_missing": [],
        "error": None,
    }
    try:
        # Lazy imports mirror the success-path merge block (server.py:1303-1305)
        # so tests that patch `workbook_merger.merge` see the same module
        # surface in both code paths.
        from db import repository as repo
        from notes_types import NotesTemplateType
        from statement_types import StatementType
        from workbook_merger import merge as merge_workbooks

        face_paths = {}
        for stmt in StatementType:
            wb = session_dir / f"{stmt.value}_filled.xlsx"
            if wb.exists():
                face_paths[stmt] = str(wb)
        notes_paths = {}
        for nt in NotesTemplateType:
            wb = session_dir / f"NOTES_{nt.value}_filled.xlsx"
            if wb.exists():
                notes_paths[nt] = str(wb)

        out["statements_included"] = sorted(s.value for s in face_paths)
        out["notes_included"] = sorted(n.value for n in notes_paths)
        if statements_to_run:
            out["statements_missing"] = sorted(
                s.value for s in statements_to_run if s not in face_paths
            )
        if notes_to_run:
            out["notes_missing"] = sorted(
                n.value for n in notes_to_run if n not in notes_paths
            )

        # Nothing on disk → nothing to merge. Not an error, not a
        # partial_merge event.
        if not face_paths and not notes_paths:
            return out

        try:
            merge_result = merge_workbooks(
                face_paths,
                merged_path,
                notes_workbook_paths=notes_paths,
                # Same skip_recalc reasoning as the success path — no
                # post-merge corrector runs after a Stop-All, so the
                # recalc that normally happens after correction won't
                # fire either. Excel will recalc on open.
                skip_recalc=True,
            )
        except Exception as e:  # noqa: BLE001 — never escape the cancel handler
            out["error"] = f"merge failed: {type(e).__name__}: {e}"
            logger.warning(
                "Partial-merge raised on cancel for run %s",
                run_id, exc_info=True,
            )
            return out

        if not getattr(merge_result, "success", False):
            errs = getattr(merge_result, "errors", None) or []
            out["error"] = "; ".join(errs) or "merge produced no output"
            return out

        out["merged"] = True
        out["merged_path"] = merge_result.output_path or merged_path

        # Best-effort DB pointer write. If this fails the file is still
        # on disk under output/{session}/filled.xlsx — but History won't
        # be able to find it, so the user would have to dig manually.
        if db_conn is not None and run_id is not None:
            try:
                repo.mark_run_merged(db_conn, run_id, out["merged_path"])
                db_conn.commit()
            except Exception:
                logger.warning(
                    "Partial-merge mark_run_merged failed for run %s",
                    run_id, exc_info=True,
                )
        return out
    except Exception:  # noqa: BLE001 — last-ditch swallow per gotcha #10
        logger.exception("_attempt_partial_merge raised unexpectedly")
        out["error"] = "internal error"
        return out


def _run_notes_face_tieouts(merged_path: str, run_id: int,
                            filing_level: str, filing_standard: str) -> list:
    """N1: reconcile curated notes figures against their face counterparts.

    Advisory + never raises — folded as ``warning`` cross-checks so a notes
    numeric value that contradicts the face statement is visible in the
    Cross-checks tab.
    """
    from cross_checks.framework import CrossCheckResult
    try:
        from cross_checks.notes_face_tieouts import check_notes_face_tieouts
        warns = check_notes_face_tieouts(
            merged_path, filing_level=filing_level, filing_standard=filing_standard)
    except Exception:  # noqa: BLE001 — advisory, never fail a run
        logger.warning(
            "notes↔face tie-out check raised on run %s", run_id, exc_info=True)
        return []
    return [
        CrossCheckResult(
            name=f"Notes↔face tie-out: {w.topic}",
            status="warning",
            expected=w.face_value,
            actual=w.notes_value,
            message=w.message,
        )
        for w in warns
    ]


def _run_notes_citation_consistency(merged_path: str, run_id: int) -> list:
    """N4: run the generic citation-consistency pass and fold to CrossCheckResults.

    Advisory + never raises — a failure returns []. Folded as ``warning``-status
    cross-checks so it rides the same persistence + SSE + UI path as the curated
    pass. Inventory page-range tolerance is a future enhancement; the default
    span already catches gross folio-vs-PDF drift.
    """
    from cross_checks.framework import CrossCheckResult
    try:
        from cross_checks.notes_consistency import check_notes_citation_consistency
        warns = check_notes_citation_consistency(merged_path)
    except Exception:  # noqa: BLE001 — advisory, never fail a run
        logger.warning(
            "notes citation-consistency check raised on run %s",
            run_id, exc_info=True,
        )
        return []
    return [
        CrossCheckResult(
            name=f"Notes citation: Note {w.note_ref}",
            status="warning",
            message=w.message,
        )
        for w in warns
    ]


async def _run_notes_advisory_bounded(fn, *args, run_id: int, label: str) -> list:
    """Dispatch an advisory notes check off the event loop, bounded.

    Both advisory passes (`_run_notes_citation_consistency`,
    `_run_notes_face_tieouts`) load the full merged workbook with openpyxl —
    blocking work that must never run on the event loop (the exact failure
    mode ``_run_cross_checks_bounded`` exists for). Same executor + timeout
    bound; but unlike the real cross-check pass these are advisory-only, so
    this helper NEVER raises (invariant #10): a timeout or dispatch failure
    logs + returns [].
    """
    loop = asyncio.get_running_loop()
    # Read the module global at call time so tests can monkeypatch it.
    import server as _self
    timeout = float(getattr(_self, "CROSS_CHECK_TIMEOUT", CROSS_CHECK_TIMEOUT))
    try:
        future = loop.run_in_executor(_CROSS_CHECK_EXECUTOR, lambda: fn(*args))
        return await asyncio.wait_for(
            future, timeout=None if timeout == float("inf") else timeout,
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "%s advisory check exceeded %.0fs cap on run %s — skipping "
            "(worker thread abandoned)", label, timeout, run_id,
        )
        return []
    except Exception:  # noqa: BLE001 — advisory, never fail a run
        logger.warning(
            "%s advisory check failed to dispatch on run %s",
            label, run_id, exc_info=True,
        )
        return []


def _emit_cross_check_summary(
    results,
    phase: str,
    event_queue,
) -> None:
    """PLAN-stop-and-validation-visibility Phase 5.2 +
    peer-review fix (2026-04-27).

    Emit only the ``cross_check_complete`` aggregate summary. The
    pre-loop ``cross_check_start`` and per-check ``cross_check_result``
    events are now emitted live by the server's ``on_check`` callback
    threaded into ``run_all`` (instead of batched after the synchronous
    pass returns). This helper finalizes the pass with the count
    rollup so the frontend can render "Passed N, Failed M, Warning K"
    without re-tallying.

    Also covers cross-check passes that landed via fallback paths
    (e.g. notes-consistency warnings appended after run_all returned)
    by including those results in the summary count.

    The function is sync and uses ``put_nowait`` because it's called
    from the post-extraction code path which is itself running on the
    event-loop thread between async awaits. Failures to enqueue are
    logged but never raised — surfacing cross-check progress is
    nice-to-have; never breaking the run is mandatory (gotcha #10).
    """
    if event_queue is None:
        return
    try:
        passed = sum(1 for r in results if r.status == "passed")
        failed = sum(1 for r in results if r.status == "failed")
        warnings = sum(1 for r in results if r.status == "warning")
        not_applicable = sum(1 for r in results if r.status == "not_applicable")
        pending = sum(1 for r in results if r.status == "pending")
        event_queue.put_nowait({
            "event": "cross_check_complete",
            "data": {
                "phase": phase,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "not_applicable": not_applicable,
                "pending": pending,
            },
        })
    except Exception:  # noqa: BLE001
        logger.warning(
            "Cross-check summary emission failed (phase=%s)",
            phase, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Error taxonomy (rewrite Phase 6.2)
# ---------------------------------------------------------------------------
# Every coordinator-level SSE ``error`` event carries an explicit ``bucket``
# so the frontend drives the run-still-going-vs-terminate decision off a named
# field instead of the legacy "is ``data.type`` present?" heuristic.
#
#   advisory    — a swallowed/fallback condition that never blocks the run.
#                 (Most advisory cases are logged, not emitted; the field is
#                 here for the rare one that surfaces as a non-blocking note.)
#   recoverable — surface the failure but the run continues to ``run_complete``
#                 and lands ``completed_with_errors`` (merge_failed,
#                 cross_check_exception, canonical_reexport_failed, and every
#                 reviewer/notes-validator sub-pass error — the outer run
#                 keeps going).
#   fatal       — the run terminates now → ``failed`` / ``aborted`` (validation
#                 before agents start, stream-drain failure, coordinator crash,
#                 user cancel).
ERROR_BUCKET_ADVISORY = "advisory"
ERROR_BUCKET_RECOVERABLE = "recoverable"
ERROR_BUCKET_FATAL = "fatal"


def _fail_run(db_conn: "Optional[Any]", run_id: Optional[int], msg: str):
    """Build the error + run_complete SSE events and mark the run row failed.

    Extracted to dedupe the five-line failure quartet
    (error → run_complete → mark_run_finished → terminal_status → return)
    that appeared in six input-validation paths inside
    ``run_multi_agent_stream`` (PR B.5).

    Returns a ``(events, new_terminal_status)`` tuple. ``events`` is a list
    of SSE events the caller must yield in order. ``new_terminal_status``
    is ``"failed"`` when the DB write succeeded, ``None`` otherwise — the
    caller updates its own ``terminal_status`` book-keeping accordingly so
    the try/finally block still retries the mark if we couldn't write it.

    Not a generator because the caller is an ``async def`` generator and
    ``yield from`` is a syntax error in that context — see
    https://docs.python.org/3/reference/expressions.html#yieldexpr.

    Usage::

        events, new_status = _fail_run(db_conn, run_id, msg)
        for ev in events:
            yield ev
        terminal_status = new_status or terminal_status
        return
    """
    events = [
        {"event": "error", "data": {"message": msg, "bucket": ERROR_BUCKET_FATAL}},
        {"event": "run_complete", "data": {"success": False, "message": msg}},
    ]
    new_status = "failed" if _safe_mark_finished(db_conn, run_id, "failed") else None
    return events, new_status


class _ValidatedRun(NamedTuple):
    """Structured result of the Validate phase (rewrite Phase 5.2).

    Carries everything the rest of the pipeline needs once the request has
    been parsed, variant/standard constraints checked, models built, the
    infopack resolved, and the canonical-bootstrap guard cleared.
    """
    statements_to_run: "Set[Any]"
    variants: dict
    models: dict
    notes_to_run: set
    notes_models: dict
    infopack: Any
    model: Any
    config: Any


def _validate_and_build_run(
    *,
    run_config: "RunConfigRequest",
    api_key: str,
    proxy_url: str,
    model_name: str,
    session_dir: Path,
    output_dir: str,
    session_id: str,
    run_id: Optional[int],
    db_conn,
) -> "tuple[Optional[_ValidatedRun], list[dict], Optional[str]]":
    """Phase: Validate — parse + validate the request and build the run config.

    The first phase of the run pipeline (rewrite Phase 5.2). Pure, yield-free,
    and returns ONE structured result so the generator shell stays thin: either
    a populated :class:`_ValidatedRun` (success) or, on any validation failure,
    ``(None, fail_events, fail_status)`` carrying the SSE error events the
    generator yields and the terminal status it records. Every failure path
    routes through :func:`_fail_run` exactly as the inline code did — the only
    change is the events are *returned* instead of yielded in place, so this
    logic can live outside the generator without touching the lifecycle
    contract (gotcha #10).
    """
    from coordinator import RunConfig
    from notes_types import NotesTemplateType
    from statement_types import StatementType, get_variant

    statements_to_run: Set[StatementType] = set()
    variants: Dict[StatementType, str] = {}
    models: Dict[StatementType, Any] = {}

    # Parse statement types
    for s in run_config.statements:
        try:
            statements_to_run.add(StatementType(s))
        except ValueError:
            events, new_status = _fail_run(db_conn, run_id, f"Unknown statement type: {s}")
            return None, events, new_status

    # Parse notes templates up-front (before pre-creating run_agents rows),
    # so DB rows and live events line up one-to-one. LIST_OF_NOTES is
    # rejected until Phase C (Sheet-12 sub-coordinator + row-112 unmatched
    # logic) lands — without it the generic agent would run with a
    # placeholder prompt.
    notes_to_run: Set[NotesTemplateType] = set()
    for n in run_config.notes_to_run:
        try:
            parsed_note = NotesTemplateType(n)
        except ValueError:
            events, new_status = _fail_run(db_conn, run_id, f"Unknown notes template: {n}")
            return None, events, new_status
        if parsed_note not in _PUBLIC_NOTES_TEMPLATES:
            events, new_status = _fail_run(db_conn, run_id, f"Notes template not available yet: {n}")
            return None, events, new_status
        notes_to_run.add(parsed_note)

    # Build variant map — fall back to first registered variant if not specified
    for stmt in statements_to_run:
        if stmt.value in run_config.variants:
            variants[stmt] = run_config.variants[stmt.value]
        # else: coordinator will resolve from infopack / registry default.

    # Reject variant/standard mismatches BEFORE launching the coordinator.
    # Without this, the run would progress through row creation, model
    # construction, and task launch before a FileNotFoundError bubbles up
    # mid-extraction, leaving a confusing run_agents trail. Caught here,
    # the user sees a single crisp error naming the offending variant and
    # the standard in play (e.g. "SoRE is not available on MFRS — ...").
    for stmt, variant_name in variants.items():
        try:
            v = get_variant(stmt, variant_name)
        except KeyError as e:
            events, new_status = _fail_run(
                db_conn, run_id, f"Unknown variant for {stmt.value}: {e}",
            )
            return None, events, new_status
        if run_config.filing_standard not in v.applies_to_standard:
            allowed = (
                ", ".join(sorted(v.applies_to_standard)).upper() or "(none)"
            )
            events, new_status = _fail_run(
                db_conn, run_id,
                f"{stmt.value}/{variant_name} is not available on "
                f"{run_config.filing_standard.upper()} filings — "
                f"only {allowed}.",
            )
            return None, events, new_status

    # Build model overrides — resolve each through _create_proxy_model so
    # per-agent overrides use the same proxy/direct wiring as the default.
    # Wrap in try/except so a broken override key also produces a clean
    # failed-row rather than bubbling out of the generator.
    notes_models: Dict[NotesTemplateType, Any] = {}
    try:
        for stmt in statements_to_run:
            if stmt.value in run_config.models:
                override_name = run_config.models[stmt.value]
                models[stmt] = _create_proxy_model(override_name, proxy_url, api_key)
        # Same treatment for per-notes-template overrides. Silently ignore
        # entries whose key isn't a known NotesTemplateType — they won't
        # match any requested template and we don't want a typo in the
        # frontend payload to fail the whole run.
        for nt_key, nt_model_name in run_config.notes_models.items():
            try:
                nt_parsed = NotesTemplateType(nt_key)
            except ValueError:
                continue
            notes_models[nt_parsed] = _create_proxy_model(
                nt_model_name, proxy_url, api_key,
            )
    except Exception as e:
        logger.exception(
            "Override model construction failed for session %s", session_id,
        )
        events, new_status = _fail_run(db_conn, run_id, f"Model override failed: {e}")
        return None, events, new_status

    # Non-fatal pre-flight events surfaced to the client even on the success
    # path (e.g. scout completeness warnings). Failures still route through
    # `_fail_run`; these are advisory and never block the run.
    pre_events: list[dict] = []

    # Resolve infopack
    infopack = None
    if run_config.infopack:
        from scout.infopack import Infopack
        try:
            # from_json expects a JSON string; request body gives us a dict
            infopack = Infopack.from_json(json.dumps(run_config.infopack))
        except Exception as e:
            events, new_status = _fail_run(db_conn, run_id, f"Invalid infopack: {e}")
            return None, events, new_status

        # Completeness probe (Plan 3): a degraded scout pack fans its loss out
        # to every downstream agent, so flag it BEFORE fan-out. Advisory only —
        # warnings are logged + emitted, the run still proceeds (gotcha #13).
        try:
            scout_warnings = infopack.completeness_warnings()
        except Exception:
            # The probe must never break the run — a bug here should not turn
            # an advisory check into a hard failure.
            logger.exception("infopack completeness probe failed; skipping")
            scout_warnings = []
        if scout_warnings:
            for w in scout_warnings:
                logger.warning("scout completeness warning (run %s): %s", run_id, w)
            pre_events.append(
                {"event": "scout_warnings", "data": {"warnings": scout_warnings}}
            )

    # Create the model object for the coordinator. May raise if the
    # proxy is unreachable or the API key is invalid — treat it as an
    # early validation failure (yield error + mark row failed + return)
    # so the user gets a clean SSE close instead of a 500.
    try:
        model = _create_proxy_model(model_name, proxy_url, api_key)
    except Exception as e:
        logger.exception(
            "Model construction failed for session %s", session_id,
        )
        events, new_status = _fail_run(db_conn, run_id, f"Model setup failed: {e}")
        return None, events, new_status

    # Canonical mode is mandatory (rewrite Phase 1.1). The concept tree
    # MUST have imported at startup for fact projection to resolve
    # concepts. If the bootstrap failed, fail the run fast with a clear
    # error instead of degrading to a correction-less workbook — the
    # legacy direct-xlsx fallback that used to cover this case is gone.
    if _CANONICAL_BOOTSTRAP_OK is False:
        events, new_status = _fail_run(
            db_conn, run_id,
            "Canonical concept-tree bootstrap failed at startup — cannot "
            "produce grounded facts. Check the server logs and restart "
            "the server; no run can proceed until the import succeeds.",
        )
        return None, events, new_status

    # Gold-standard eval (v16): if the user attached a benchmark, validate it
    # BEFORE extraction so a stale / mismatched selection fails fast (cheap,
    # pre-agent) instead of running for minutes and then producing a misleading
    # 0% (a standard/level-mismatched benchmark shares no concept uuids with the
    # run, so grading would silently match nothing). A config error here is
    # treated like a bad model/infopack — `_fail_run`, not a soft skip.
    #
    # NOTE: this can only validate standard/level + existence. It CANNOT verify
    # the uploaded PDF is the benchmark's document — two same-(standard,level)
    # benchmarks share template_ids/uuids, so picking the wrong document's
    # benchmark still grades against the wrong gold. That's an inherent user
    # responsibility (cf. picking the wrong PDF); see CLAUDE.md gotcha #23.
    if run_config.benchmark_id is not None and db_conn is not None:
        from eval import store as _eval_store
        bench = None
        try:
            bench = _eval_store.get_benchmark(db_conn, run_config.benchmark_id)
        except Exception:
            # A read failure shouldn't crash validation — fall through to the
            # not-found path below, which fails the run with a clear message.
            logger.warning(
                "benchmark lookup failed for id %s", run_config.benchmark_id,
                exc_info=True,
            )
        if bench is None:
            events, new_status = _fail_run(
                db_conn, run_id,
                f"Eval benchmark {run_config.benchmark_id} not found. "
                "Pick an existing benchmark or turn off eval testing.",
            )
            return None, events, new_status
        if (
            bench["filing_standard"] != run_config.filing_standard
            or bench["filing_level"] != run_config.filing_level
        ):
            events, new_status = _fail_run(
                db_conn, run_id,
                f"Eval benchmark '{bench['name']}' is "
                f"{bench['filing_standard'].upper()} {bench['filing_level']}, "
                f"but this run is {run_config.filing_standard.upper()} "
                f"{run_config.filing_level}. Pick a matching benchmark or turn "
                "off eval testing.",
            )
            return None, events, new_status

    # Item 28 — per-entity advisory memory. Persist this run's infopack so it
    # can seed FUTURE matches, then look up whether this entity was processed
    # before and, if so, build an advisory the coordinator renders into the
    # prompts (advisory-only; entity-name collisions are why every line is
    # framed "verify against THIS PDF"). Best-effort: a failure here never
    # blocks the run — the feature degrades to "no prior-year hint".
    prior_year_advisory = None
    if infopack is not None:
        from entity_memory import (
            entity_memory_enabled,
            fetch_prior_runs,
            find_prior_year_match,
            persist_infopack,
        )

        persist_infopack(output_dir, infopack)
        if entity_memory_enabled() and infopack.entity_name:
            try:
                prior_year_advisory = find_prior_year_match(
                    fetch_prior_runs(db_conn, exclude_run_id=run_id),
                    entity_name=infopack.entity_name,
                    exclude_run_id=run_id,
                )
                if prior_year_advisory is not None:
                    logger.info(
                        "Entity memory: matched run %s (%s) for entity %r",
                        prior_year_advisory.prior_run_id,
                        prior_year_advisory.pdf_filename,
                        infopack.entity_name,
                    )
            except Exception:  # noqa: BLE001 — advisory; never fail the run
                logger.warning("Entity-memory match failed", exc_info=True)

    config = RunConfig(
        pdf_path=str(session_dir / "uploaded.pdf"),
        output_dir=output_dir,
        model=model,
        statements_to_run=statements_to_run,
        variants=variants,
        models=models,
        filing_level=run_config.filing_level,
        filing_standard=run_config.filing_standard,
        denomination=run_config.denomination,
        # Canonical mode is mandatory: always thread the run_id + DB into
        # the coordinator so extraction agents project their writes into
        # run_concept_facts. Bootstrap success is guaranteed by the
        # fail-fast guard above.
        run_id=run_id,
        db_path=str(AUDIT_DB_PATH),
        # Gold-standard eval (v16): carried through so the end-of-run grading
        # hook can find the benchmark. None on every normal run.
        benchmark_id=run_config.benchmark_id,
        # Item 28 — matched prior-year advisory (or None).
        prior_year_advisory=prior_year_advisory,
    )

    return _ValidatedRun(
        statements_to_run=statements_to_run,
        variants=variants,
        models=models,
        notes_to_run=notes_to_run,
        notes_models=notes_models,
        infopack=infopack,
        model=model,
        config=config,
    ), pre_events, None


# --- SSE keepalive + mid-stream session expiry (PLAN auth/deploy Phase 3) ---
#
# Azure App Service's front end drops a response that has been silent for
# ~230 s, and a run has long quiet stretches (gotcha #19). We inject a
# `: keepalive` SSE comment during those gaps so the connection survives. The
# same loop also re-checks the auth session on each tick and closes the stream
# with a `session-expired` event once it idles out — the confidentiality fix
# that the request-level middleware can't do (an open stream only hits the
# middleware once, at connect). Done at the HTTP layer here so the core
# run_multi_agent_stream drain loop (and its GeneratorExit contract) is
# untouched. Override the cadence with XBRL_SSE_KEEPALIVE_S.

# Strong references to in-flight background drain tasks. asyncio only holds a
# WEAK reference to a bare ensure_future() result, so a fire-and-forget drain
# can be garbage-collected mid-await — which would strand the run `running`
# forever (gotcha #10's terminal-status guarantee depends on the drain finishing
# run_multi_agent_stream). Keeping the task here until it completes prevents that.
_DRAIN_TASKS: set = set()


def _spawn_drain(agen, pending) -> "asyncio.Task":
    """Schedule a background drain and pin a strong reference until it finishes."""
    task = asyncio.ensure_future(_drain_generator_to_completion(agen, pending))
    _DRAIN_TASKS.add(task)
    task.add_done_callback(_DRAIN_TASKS.discard)
    return task


def _sse_keepalive_interval() -> float:
    try:
        return float(os.environ.get("XBRL_SSE_KEEPALIVE_S", "25") or "25")
    except ValueError:
        return 25.0


_PROXY_HEADER_DIAG_DONE = False


def _maybe_log_proxy_header_diag(request) -> None:
    """Log the observed client IP vs X-Forwarded-For ONCE, in production only.

    Lets an operator confirm uvicorn --proxy-headers is active so the login
    lockout buckets on the real client IP rather than the App Service front end
    (see auth/routes.py::_client_ip). No-op locally and after the first call.
    """
    global _PROXY_HEADER_DIAG_DONE
    if _PROXY_HEADER_DIAG_DONE:
        return
    from auth import config as auth_config
    if not auth_config.is_production():
        return
    _PROXY_HEADER_DIAG_DONE = True
    peer = request.client.host if request.client else "(none)"
    xff = request.headers.get("x-forwarded-for", "(absent)")
    if xff == "(absent)":
        logger.warning(
            "auth lockout diagnostic: no X-Forwarded-For header on the first "
            "guarded request (peer=%s). If this app is behind the App Service "
            "front end, start uvicorn with --proxy-headers so per-IP login "
            "lockout buckets on the real client, not the shared front end.",
            peer,
        )
    else:
        logger.info(
            "auth lockout diagnostic: peer=%s X-Forwarded-For=%s "
            "(per-IP login lockout buckets on peer).", peer, xff,
        )


def _auth_session_id_from_request(request) -> Optional[str]:
    """Verified auth session id from the request cookie, or None when there is
    no session to watch (dev-mode bypass, or an unsigned/absent cookie)."""
    from auth import config as auth_config
    from auth import sessions as auth_sessions

    if auth_config.dev_bypass_active():
        return None
    return auth_sessions.parse_cookie(request.cookies.get(auth_config.cookie_name()))


def _auth_session_expired(auth_session_id: Optional[str]) -> bool:
    """True if the auth session has idled out (and delete the stale row). False
    when there is nothing to watch — never closes a dev-mode/unauthenticated
    stream."""
    if not auth_session_id:
        return False
    from auth import sessions as auth_sessions
    from db import repository as repo

    conn = _open_audit_conn()
    try:
        sess = repo.fetch_auth_session(conn, auth_session_id)
        if sess is None:
            return True
        if auth_sessions.is_expired(sess):
            repo.delete_auth_session(conn, auth_session_id)
            conn.commit()
            return True
        # Also close the stream if the account was disabled/deleted mid-run.
        user = repo.fetch_auth_user(conn, sess.email)
        if user is None or user.disabled:
            repo.delete_auth_session(conn, auth_session_id)
            conn.commit()
            return True
        return False
    finally:
        conn.close()


async def _drain_generator_to_completion(agen, pending) -> None:
    """Drive a run generator to its natural end with no client listening, so
    the run still merges + finalizes + lands in History ("runs outlive
    sessions"). Events are persisted inside the generator before each yield, so
    discarding them here loses nothing. Used when the client disconnects or the
    session expires mid-stream.

    Crucially it does NOT cancel the generator — it keeps calling __anext__ so
    the generator's own post-pipeline (merge, cross-checks, terminal-status
    finalize — gotcha #10) runs to completion exactly as if a client were still
    attached but silent.
    """
    try:
        try:
            await pending  # resolve the already-in-flight pull first
        except (StopAsyncIteration, Exception):
            return
        while True:
            try:
                await agen.__anext__()
            except (StopAsyncIteration, Exception):
                return
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass


async def sse_stream_with_keepalive(agen, *, auth_session_id: Optional[str] = None):
    """Format run_multi_agent_stream's dict events as SSE frames, inject
    `: keepalive` comments during silent stretches, and close with a
    `session-expired` event once the auth session times out mid-stream.

    Uses a persistent in-flight __anext__ task (NOT asyncio.wait_for, which
    would cancel the pull on every keepalive tick and corrupt the generator —
    the next pull would StopAsyncIteration early and end a healthy run). The
    pending task is left untouched across keepalive ticks. On any early exit
    (expiry / client disconnect) the run is handed to a background drain so it
    finishes server-side.
    """
    interval = _sse_keepalive_interval()
    last_check = time.monotonic()
    pending = asyncio.ensure_future(agen.__anext__())
    # True until the generator finishes on its own; left True on an early exit
    # so the finally hands the still-running run to the background drain.
    detach = True
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                # Silent stretch — `pending` is untouched (no cancellation).
                if _auth_session_expired(auth_session_id):
                    yield "event: session-expired\ndata: {}\n\n"
                    return
                yield ": keepalive\n\n"
                continue
            try:
                evt = pending.result()
            except StopAsyncIteration:
                detach = False  # generator already done — nothing to drain
                return
            # Start the next pull before yielding so the run keeps progressing
            # while the client consumes this frame.
            pending = asyncio.ensure_future(agen.__anext__())
            # Re-check expiry at most once per interval even on an active stream
            # so a run streaming to a walked-away user still closes on timeout.
            now = time.monotonic()
            if now - last_check >= interval:
                last_check = now
                if _auth_session_expired(auth_session_id):
                    yield "event: session-expired\ndata: {}\n\n"
                    return
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
    finally:
        if detach:
            # Client stopped listening but the run isn't done — finish it in the
            # background so it lands in History. _spawn_drain pins a strong
            # reference so the task can't be GC'd mid-run (gotcha #10).
            _spawn_drain(agen, pending)


async def run_multi_agent_stream(
    session_id: str,
    session_dir: Path,
    run_config: RunConfigRequest,
    api_key: str,
    proxy_url: str,
    model_name: str,
    *,
    existing_run_id: Optional[int] = None,
) -> AsyncIterator[dict]:
    """Orchestrates multi-agent extraction with SSE event multiplexing.

    Runs the coordinator with per-agent event tagging, then merges workbooks,
    runs cross-checks, and persists everything to the audit DB.

    Phase pipeline (rewrite Phase 5.2): the run proceeds through explicit
    phases — **Validate** → **Extract** → **Cascade** → **Merge/Render** →
    **Check** → **Review** → **Persist/Finalize**. Phase boundaries are marked
    by ``_emit_stage(...)`` events (extracting | merging | cross_checking |
    reviewing | re_checking | reviewing_notes | done). The first phase is a
    standalone unit (:func:`_validate_and_build_run`) returning one structured
    result; the remaining phases stay inline in this generator because each is
    interleaved with the GeneratorExit-tolerant ``event_queue`` drain (gotcha
    #19) — factoring a ``yield`` out of an async generator would break that
    disconnect-finalization contract. The post-extraction *work* is already
    delegated to named helpers (``_run_reviewer_pass``, ``merge_workbooks``,
    ``run_cross_checks``, ``_export_canonical_workbooks``); this generator is
    the thin orchestration shell that owns the lifecycle + the drain.

    Lifecycle contract (Phase 1.6 refactor):
      1. The `runs` row is created BEFORE the coordinator launches, so
         History captures the run even if the coordinator explodes
         instantly.
      2. The orchestration body is wrapped in try/except/finally. Any path
         out of the function — success, exception, CancelledError, client
         disconnect — leaves the row in a terminal status (never `running`).
      3. `mark_run_merged` is called right after a successful merge, BEFORE
         the final status update, so the download endpoint has a durable
         pointer to filled.xlsx even if later persistence work crashes.
    """
    from coordinator import RunConfig, run_extraction as coordinator_run
    from notes.coordinator import (
        NotesAgentResult,
        NotesRunConfig,
        run_notes_extraction,
        NotesCoordinatorResult,
    )
    from notes_types import NotesTemplateType
    from statement_types import StatementType, get_variant, variants_for
    from workbook_merger import merge as merge_workbooks
    from cross_checks.framework import DEFAULT_TOLERANCE_RM
    from cross_checks.notes_consistency import check_notes_consistency
    from db.schema import init_db
    from db import repository as repo
    import sqlite3

    # --- Pre-validation bookkeeping that cannot fail ---
    # These are the only values create_run needs. Compute them up-front so
    # even a totally malformed request still leaves a History row behind.
    output_dir = str(session_dir)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    # Read the original filename from the sidecar written at upload time.
    # The on-disk file is always "uploaded.pdf" so that downstream tools
    # can find it by a stable name; the sidecar records the user-visible
    # name so History search stays meaningful. Fall back to "uploaded.pdf"
    # if the sidecar is missing (legacy sessions from before this change).
    sidecar = session_dir / "original_filename.txt"
    if sidecar.exists():
        try:
            pdf_filename = sidecar.read_text(encoding="utf-8").strip() or "uploaded.pdf"
        except OSError:
            pdf_filename = "uploaded.pdf"
    else:
        pdf_filename = "uploaded.pdf"
    merged_path = str(session_dir / "filled.xlsx")

    # --- Open the audit connection and create the runs row BEFORE any
    # validation. Peer-review fix: if we parsed statements / infopack /
    # model before this point, early failures (invalid enum, bad infopack,
    # proxy unreachable) would never appear in History. ---
    init_db(AUDIT_DB_PATH)

    db_conn: Optional[sqlite3.Connection] = None
    run_id: Optional[int] = None
    # Tracks whether we've already written a terminal status to the runs
    # row. Prevents the finally block from clobbering an earlier-set state.
    terminal_status: Optional[str] = None
    try:
        db_conn = sqlite3.connect(str(AUDIT_DB_PATH))
        db_conn.execute("PRAGMA foreign_keys = ON")
        db_conn.execute("PRAGMA journal_mode = WAL")
        db_conn.execute("PRAGMA busy_timeout = 5000")
        db_conn.row_factory = sqlite3.Row
    except Exception:
        # Couldn't open the DB at all — there is nothing we can audit.
        # Log loudly and let extraction continue without an audit trail.
        logger.exception(
            "Failed to open audit DB connection for session %s", session_id,
        )
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass
            db_conn = None

    if db_conn is not None:
        if existing_run_id is not None:
            # Persistent-draft start path: `start_run_endpoint` has already
            # done the atomic draft → running flip (peer-review HIGH #3).
            # We trust the row is in 'running' state. We do NOT mark it
            # again here, and there is no defensive create_run fallback —
            # silently creating a fresh row would break the shareable
            # /run/{id} URL the user just navigated to.
            #
            # Refresh the persisted config blob from the live request.
            # `start_run_endpoint` parsed `run_config` from the same DB
            # row, so this is a no-op in the happy path; in the rare case
            # the PATCH/Start race let `start_run_endpoint` see a stale
            # blob, the live `run_config` is still the authoritative
            # source-of-truth for what we are about to extract.
            #
            # Peer-review #2 (HIGH, RUN-REVIEW follow-up): a failure on
            # this cosmetic UPDATE must NOT null db_conn. The row was
            # already flipped to 'running' before this function was
            # called; if we drop the audit connection here, the
            # terminal-status write at the end of the run silently
            # no-ops and the row stays 'running' forever, violating
            # gotcha #10 (every exit path reaches a terminal status).
            run_id = existing_run_id
            try:
                # Also refresh the canonical `runs.orchestration` column
                # — list/detail read the column, not the JSON (peer-
                # review HIGH #1, 2026-05-28). Otherwise the started
                # row keeps whatever orchestration value was written at
                # draft creation, which is stale if the user toggled
                # the orchestration in the pre-run panel mid-draft and
                # the PATCH side leaked it to JSON only.
                db_conn.execute(
                    "UPDATE runs SET run_config_json = ?, "
                    "scout_enabled = ?, orchestration = ? "
                    "WHERE id = ?",
                    (
                        json.dumps(run_config.model_dump()),
                        1 if run_config.use_scout else 0,
                        getattr(run_config, "orchestration", "split")
                        or "split",
                        existing_run_id,
                    ),
                )
                db_conn.commit()
            except Exception:
                logger.warning(
                    "Failed to refresh run_config_json on existing draft %s "
                    "— continuing with stale persisted config; terminal "
                    "status will still be written.",
                    existing_run_id, exc_info=True,
                )
                # Roll back the failed UPDATE so the connection stays
                # usable for downstream writes (terminal status, agent
                # rows, cross-checks). We deliberately do NOT null
                # db_conn — the row is recoverable.
                try:
                    db_conn.rollback()
                except Exception:
                    pass
        else:
            # Fresh-row path: insert the runs row. If THIS fails the run
            # has no row at all, so dropping the connection is correct
            # (there's nothing for the terminal-status writer to find).
            try:
                run_id = repo.create_run(
                    db_conn,
                    pdf_filename=pdf_filename,
                    session_id=session_id,
                    output_dir=output_dir,
                    config=run_config.model_dump(),
                    scout_enabled=run_config.use_scout,
                    orchestration=getattr(run_config, "orchestration", "split"),
                )
                db_conn.commit()
            except Exception:
                logger.exception(
                    "Failed to create runs row for session %s", session_id,
                )
                try:
                    db_conn.close()
                except Exception:
                    pass
                db_conn = None

    # Gold-standard eval (v16): persist the benchmark this run grades against,
    # on whichever runs row we resolved above (draft-start or fresh). Best-
    # effort — a failure here must never abort the run (a missing benchmark_id
    # just means the end-of-run grading hook stays inert, like a normal run).
    if db_conn is not None and run_id is not None and run_config.benchmark_id:
        try:
            db_conn.execute(
                "UPDATE runs SET benchmark_id = ? WHERE id = ?",
                (run_config.benchmark_id, run_id),
            )
            db_conn.commit()
        except Exception:
            logger.warning(
                "Failed to persist benchmark_id=%s on run %s — run will "
                "complete but won't be graded.",
                run_config.benchmark_id, run_id, exc_info=True,
            )
            try:
                db_conn.rollback()
            except Exception:
                pass

    coordinator_result = None  # type: ignore[assignment]
    merge_result = None
    cross_check_results: list = []
    # These are filled in by the validation block below and used by the
    # post-processing / persistence blocks further down.
    statements_to_run: Set[StatementType] = set()
    variants: Dict[StatementType, str] = {}
    models: Dict[StatementType, Any] = {}
    config: Optional[RunConfig] = None

    try:
        # --- Phase 3 fix: validate & construct INSIDE the outer try so
        # any failure path runs through the except block and marks the
        # runs row as failed. Previously these exits happened before the
        # row existed. ---

        # === PHASE: Validate ===
        # Parse + validate the request, build models/infopack, and construct
        # the coordinator RunConfig. Returns ONE structured result; on any
        # failure it carries the SSE error events to yield + the terminal
        # status to record (rewrite Phase 5.2). The yield/return stays in the
        # generator shell so the lifecycle contract (gotcha #10) is untouched.
        validated, pre_events, fail_status = _validate_and_build_run(
            run_config=run_config,
            api_key=api_key,
            proxy_url=proxy_url,
            model_name=model_name,
            session_dir=session_dir,
            output_dir=output_dir,
            session_id=session_id,
            run_id=run_id,
            db_conn=db_conn,
        )
        if validated is None:
            # On failure `pre_events` carries the `_fail_run` error events.
            for ev in pre_events:
                yield ev
            terminal_status = fail_status or terminal_status
            return
        # On success `pre_events` carries any non-fatal pre-flight warnings
        # (e.g. scout completeness) — surface them before extraction starts.
        for ev in pre_events:
            yield ev
        statements_to_run = validated.statements_to_run
        variants = validated.variants
        models = validated.models
        notes_to_run = validated.notes_to_run
        notes_models = validated.notes_models
        infopack = validated.infopack
        model = validated.model
        config = validated.config

        yield {"event": "status", "data": {
            "phase": "starting",
            "message": f"Starting extraction for {len(statements_to_run)} statements...",
            # Surface the new run_id so clients that kicked off a
            # rerun / regenerate (which creates a fresh run row) can
            # navigate to the new run once it finishes, instead of
            # sitting on the stale id they POSTed to. Matches the
            # run_complete event below which also carries it now.
            "run_id": run_id,
        }}

        # Phase 6.5: create run_agents rows UP FRONT so tool events can be
        # keyed to the right agent as they stream out of the coordinator.
        # The old path created these rows at the end of the run, by which
        # point every tool_call had already been missed.
        #
        # We build a mapping {agent_id → run_agent_id} keyed by the SAME
        # agent_id the coordinator puts on every SSE event (lowercase
        # statement value, e.g. "sofp"). This lets persist_event resolve
        # the right run_agent_id in O(1) without re-querying the DB.
        run_agent_ids_by_agent_id: Dict[str, int] = {}
        # We also keep a parallel map keyed by StatementType for the
        # post-run finish_run_agent / save_extracted_field loop.
        run_agent_ids_by_stmt: Dict[StatementType, int] = {}
        # Same idea for notes — keyed by NotesTemplateType so the post-run
        # loop can find the row to finalize.
        run_agent_ids_by_notes: Dict[NotesTemplateType, int] = {}
        if db_conn is not None and run_id is not None:
            try:
                # Iterate in sorted order so run_agents row IDs are
                # deterministic across test runs (statements_to_run is a
                # Set; its iteration order is hash-based and unstable).
                for stmt in sorted(statements_to_run, key=lambda s: s.value):
                    agent_model = config.models.get(stmt, config.model)
                    rai = repo.create_run_agent(
                        db_conn, run_id,
                        statement_type=stmt.value,
                        variant=variants.get(stmt),
                        model=_model_id(agent_model),
                    )
                    run_agent_ids_by_agent_id[stmt.value.lower()] = rai
                    run_agent_ids_by_stmt[stmt] = rai
                # Notes templates — statement_type is prefixed "NOTES_" so the
                # column is unambiguous vs. face statements, and the agent_id
                # key matches notes/coordinator.py's f"notes:{template.value}"
                # emission (lowercased to match persist_event's lookup).
                for nt in sorted(notes_to_run, key=lambda n: n.value):
                    # Resolve the per-template model the coordinator will
                    # actually use so History shows the right model id for
                    # each notes agent (falls back to the run-wide default).
                    nt_model = notes_models.get(nt, config.model)
                    rai = repo.create_run_agent(
                        db_conn, run_id,
                        statement_type=f"NOTES_{nt.value}",
                        variant=None,
                        model=_model_id(nt_model),
                    )
                    run_agent_ids_by_agent_id[f"notes:{nt.value}".lower()] = rai
                    run_agent_ids_by_notes[nt] = rai
                db_conn.commit()
            except Exception:
                logger.warning("Failed to pre-create run_agents rows for %s",
                               session_id, exc_info=True)

        # Phase 6.5: in-place persistence of tool-level SSE events.
        # Mirrors db/recorder.py's _COARSE_EVENT_TYPES — we write status,
        # tool_call, tool_result, error, and complete rows. Thinking/text
        # deltas are intentionally dropped (too high-frequency, low audit
        # value). Failures self-disable for that agent so we never block
        # the live stream on a wedged DB.
        _persist_disabled: Set[int] = set()
        _COARSE_EVENT_TYPES_SET = frozenset({
            "status", "tool_call", "tool_result", "error", "complete",
        })

        # Peer-review fix (2026-04-27): lazily create a SYSTEM
        # pseudo-agent row on first coordinator-level error event so
        # ``merge_failed`` / ``cross_check_exception`` /
        # ``correction_wallclock_exceeded`` show up in History after
        # reload, not just in the live SSE stream. Creating it lazily
        # keeps healthy runs from churning out an extra audit row.
        _system_run_agent_id: Optional[int] = None

        def _ensure_system_pseudo_agent() -> None:
            """Lazy-create the SYSTEM run_agents row + register it in
            ``run_agent_ids_by_agent_id`` so persist_event picks up
            coordinator-level errors stamped with agent_role=SYSTEM."""
            nonlocal _system_run_agent_id
            if _system_run_agent_id is not None:
                return
            if db_conn is None or run_id is None:
                return
            try:
                _system_run_agent_id = repo.create_run_agent(
                    db_conn, run_id,
                    statement_type="SYSTEM",
                    variant=None,
                    model=_model_id(config.model),
                )
                run_agent_ids_by_agent_id["system"] = _system_run_agent_id
                db_conn.commit()
            except Exception:
                logger.warning(
                    "Failed to lazy-create SYSTEM run_agent row for run %s",
                    run_id, exc_info=True,
                )

        def _enqueue_system_error(payload: dict) -> None:
            """Surface a coordinator-level error on both the live SSE
            stream AND the audit DB.

            The wire copy has NO ``agent_id`` so the frontend reducer
            routes it to the global typed-error branch (see I-2 fix in
            appReducer.ts: ``isRunning`` stays true for typed errors so
            the spinner keeps spinning while the backend continues).

            The DB copy is stamped with ``agent_role="SYSTEM"`` so
            persist_event accepts it and History after reload still
            shows the diagnostic. The SYSTEM run_agents row is created
            lazily here on first call.

            Every payload is stamped ``bucket="recoverable"`` (Phase 6.2)
            unless the caller set one explicitly — this helper is, by
            construction, only used for coordinator-level errors that DON'T
            terminate the run (the run continues to ``run_complete``). The
            field tells the frontend to keep the spinner spinning.
            """
            payload.setdefault("bucket", ERROR_BUCKET_RECOVERABLE)
            _ensure_system_pseudo_agent()
            # DB persistence — stamped copy, lazily-created SYSTEM row.
            if _system_run_agent_id is not None:
                try:
                    persist_event({
                        "event": "error",
                        "data": {
                            **payload,
                            "agent_id": "SYSTEM",
                            "agent_role": "SYSTEM",
                        },
                    })
                except Exception:
                    logger.warning(
                        "Failed to persist SYSTEM error to audit DB",
                        exc_info=True,
                    )
            # Wire — bare payload, no agent routing.
            try:
                event_queue.put_nowait({"event": "error", "data": dict(payload)})
            except asyncio.QueueFull:
                logger.warning("event_queue full; SYSTEM error dropped")

        def persist_event(evt: dict) -> None:
            if db_conn is None:
                return
            event_type = str(evt.get("event", ""))
            if event_type not in _COARSE_EVENT_TYPES_SET:
                return
            data = evt.get("data") or {}
            if not isinstance(data, dict):
                return
            agent_id_raw = data.get("agent_id") or data.get("agent_role")
            if not isinstance(agent_id_raw, str) or not agent_id_raw:
                return
            rai = run_agent_ids_by_agent_id.get(agent_id_raw.lower())
            if rai is None or rai in _persist_disabled:
                return
            try:
                phase = data.get("phase") if isinstance(data.get("phase"), str) else None
                repo.log_event(
                    db_conn,
                    run_agent_id=rai,
                    event_type=event_type,
                    payload=data,
                    phase=phase,
                )
                db_conn.commit()
            except Exception:
                # Stop trying for this agent — one failure likely means the
                # DB is wedged and we shouldn't spam warnings on every event.
                logger.warning(
                    "persist_event disabled for run_agent %s after error",
                    rai, exc_info=True,
                )
                _persist_disabled.add(rai)

        # Event bridge: concurrent agents push events into this queue,
        # and the SSE generator drains it in real time. None = all done.
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # PLAN-stop-and-validation-visibility Phase 6: pipeline_stage
        # events surface the coordinator-level stage boundaries that
        # otherwise look like silent dead zones to the user (the long
        # gap between "all face agents finished" and "run_complete"
        # arrived). Emit at each boundary; the frontend reducer
        # captures the latest stage so PipelineStages can label the
        # current activity.
        #
        # Hoist ``client_connected`` initialization above the first
        # stage emit — the original assignment lived just before the
        # drain loop, but we need to ``yield`` from this generator
        # earlier now and don't want an UnboundLocalError sneaking in
        # if the client somehow disconnected between request acceptance
        # and this point (a hypothetical race; the test harness
        # surfaced it deterministically because TestClient sets up the
        # generator fully before consuming).
        client_connected = True
        import time as _time

        def _stage_event(stage: str) -> dict:
            return {
                "event": "pipeline_stage",
                "data": {"stage": stage, "started_at": _time.time()},
            }

        def _emit_stage(stage: str) -> None:
            """Push a pipeline_stage event onto the queue so the drain
            loop yields it the same way it yields agent events. Direct
            ``yield`` from this generator was tried first, but it broke
            the disconnect-finalization contract: GeneratorExit raised
            at a yield outside the drain loop's try/except escaped to
            the outer ``except BaseException`` and marked the run
            ``failed`` even though the agents had succeeded.
            """
            try:
                event_queue.put_nowait(_stage_event(stage))
            except asyncio.QueueFull:
                # Best-effort — pipeline_stage labels are nice-to-have,
                # never block the run.
                logger.warning(
                    "event_queue full; pipeline_stage=%s dropped", stage,
                )

        # Stage 1: extracting — fired the moment we launch the
        # coordinator task and the first per-agent events are about to
        # start streaming.
        _emit_stage("extracting")

        # Warm the PDF text-layer cache OFF-THREAD before any agent factory
        # runs. create_extraction_agent / create_notes_agent / the reviewer
        # factory each probe `pdf_has_text_layer` (the Fix-B scanned advisory)
        # synchronously on construction, which parses the whole text layer the
        # first time. Doing that parse here via to_thread populates the shared
        # memoised cache (tools.pdf_search._load_page_texts), so every in-factory
        # probe is an O(1) cache hit and never blocks the event loop / SSE
        # delivery on a large scanned PDF. Best-effort — a probe failure here
        # must never block the run (the factory probe degrades to "assume text").
        try:
            from tools.pdf_search import pdf_has_text_layer as _warm_text_layer
            await asyncio.to_thread(
                _warm_text_layer, str(session_dir / "uploaded.pdf"))
        except Exception:  # noqa: BLE001 — cache warm is an optimization only
            logger.debug("PDF text-layer cache warm skipped", exc_info=True)

        # Launch coordinator as a background task so we can drain events while agents run.
        # push_sentinel=False: we're multiplexing face + notes into one queue;
        # the orchestrator below pushes a single sentinel after BOTH complete.
        coordinator_task = asyncio.create_task(
            coordinator_run(
                config,
                infopack=infopack,
                event_queue=event_queue,
                session_id=session_id,
                push_sentinel=False,
            )
        )

        # Derive a union of note-bearing pages across every face statement
        # scout scored. Gives the notes agents a tight starting viewport on
        # scanned PDFs where scout's deterministic notes_inventory is empty
        # (observed in real FINCO runs: NOTES_ACC_POLICIES rendered 33 pages
        # for 15 output rows, consuming the majority of total run time).
        # If scout was off or failed, hints stay empty and the notes agents
        # fall back to their previous any-page exploration behaviour.
        notes_page_hints: List[int] = []
        if infopack is not None:
            try:
                notes_page_hints = infopack.notes_page_hints()
            except Exception:  # noqa: BLE001 — advisory only, never block the run
                logger.warning(
                    "Failed to derive notes_page_hints from infopack",
                    extra={"session_id": session_id},
                    exc_info=True,
                )

        notes_config = NotesRunConfig(
            pdf_path=str(session_dir / "uploaded.pdf"),
            output_dir=output_dir,
            model=model,
            notes_to_run=notes_to_run,
            filing_level=run_config.filing_level,
            filing_standard=run_config.filing_standard,
            models=notes_models,
            page_hints=notes_page_hints,
            # Step 6 of the notes rich-editor plan: hand the audit run_id
            # + DB path down so the coordinator persists each agent's
            # per-cell HTML to `notes_cells` on success. Skipped cleanly
            # if the row-creation above failed (run_id is None).
            run_id=run_id,
            audit_db_path=str(AUDIT_DB_PATH),
            # Item 28 — same matched prior-year advisory the face coordinator
            # received (read off the resolved RunConfig).
            prior_year_advisory=getattr(config, "prior_year_advisory", None),
        )
        notes_task = asyncio.create_task(
            run_notes_extraction(
                notes_config,
                infopack=infopack,
                event_queue=event_queue,
                session_id=session_id,
            )
        )

        # Fan-in sentinel: push None onto the queue only after BOTH coords
        # have finished so the drain loop doesn't exit prematurely.
        async def _push_sentinel_when_done() -> None:
            try:
                await asyncio.gather(coordinator_task, notes_task, return_exceptions=True)
            finally:
                await event_queue.put(None)

        sentinel_task = asyncio.create_task(_push_sentinel_when_done())

        # Drain events from the queue as they arrive from concurrent agents.
        #
        # Client-disconnect contract (Option B, April 2026): if the SSE
        # client drops mid-stream we do NOT kill the coordinator. The
        # agents may have already written their workbooks (the real-world
        # trigger was a rerun where save_result had completed on disk but
        # the post-save LLM wrap-up call stalled long enough for the
        # browser to close the stream). Throwing away that work — and
        # leaving the runs row as 'aborted' with run_agents frozen at
        # 'running' — was the original bug.
        #
        # Instead we:
        #   1. Swallow GeneratorExit / CancelledError at the yield point,
        #      flip ``client_connected`` to False, and keep draining so the
        #      coordinator isn't blocked pushing into a full queue.
        #   2. Fall through to the post-pipeline (merge + cross-checks +
        #      DB finalization) as if nothing happened.
        #   3. Skip the trailing ``yield`` of run_complete — once a
        #      generator has caught GeneratorExit it can never yield again
        #      without raising RuntimeError, and there's no one listening.
        client_connected = True
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    # Sentinel: all agents finished
                    break
                persist_event(event)
                if client_connected:
                    try:
                        yield event
                    except (asyncio.CancelledError, GeneratorExit):
                        client_connected = False
                        logger.info(
                            "Client disconnected; continuing post-pipeline",
                            extra={"session_id": session_id},
                        )
        except Exception as e:
            # Drain failure is unrecoverable: the queue contract is broken
            # and we can't safely merge partial results. Cancel the
            # coordinator, mark the run failed, and bail — previously the
            # code fell through to merge, which could corrupt output.
            logger.exception("Event queue drain failed", extra={"session_id": session_id})
            coordinator_task.cancel()
            notes_task.cancel()
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            if client_connected:
                yield {"event": "error", "data": {
                    "message": "The extraction stopped unexpectedly. Please try starting it again.",
                    "traceback": f"{type(e).__name__}: {e}",
                    "bucket": ERROR_BUCKET_FATAL,
                }}
            return

        # Await the coordinator task to get CoordinatorResult for post-processing
        try:
            coordinator_result = await coordinator_task
        except asyncio.CancelledError:
            logger.info("Coordinator cancelled", extra={"session_id": session_id})
            # PLAN-stop-and-validation-visibility Phase 2: best-effort merge
            # of whatever per-statement workbooks already landed on disk
            # before the cancel signal arrived. Without this, History has
            # no downloadable filled.xlsx even though the per-statement
            # files are still in output/{session_id}/. The helper itself
            # is hardened to never raise — gotcha #10 still holds.
            partial = _attempt_partial_merge(
                session_dir=session_dir,
                merged_path=merged_path,
                statements_to_run=statements_to_run,
                notes_to_run=notes_to_run,
                db_conn=db_conn,
                run_id=run_id,
            )
            if _safe_mark_finished(db_conn, run_id, "aborted"):
                terminal_status = "aborted"
            if client_connected:
                # Surface the partial-merge outcome BEFORE the generic
                # cancel error so the frontend can render a "saved partial
                # workbook" banner alongside the cancellation message.
                if partial["merged"] or partial["statements_included"] or partial["notes_included"]:
                    yield {"event": "partial_merge", "data": partial}
                yield {"event": "error", "data": {"message": "Run cancelled", "bucket": ERROR_BUCKET_FATAL}}
            return
        except Exception as e:
            logger.exception("Coordinator failed", extra={"session_id": session_id})
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            if client_connected:
                yield {"event": "error", "data": {
                    "message": "The extraction stopped unexpectedly. Please try starting it again.",
                    "traceback": f"{type(e).__name__}: {e}",
                    "bucket": ERROR_BUCKET_FATAL,
                }}
            return

        # Canonical mode (Phase B7): now that every extraction agent has
        # projected its leaf facts into run_concept_facts, recompute the
        # COMPUTED parents (subtotals/totals) from the leaves so the
        # Concepts UI and the DB exporter (Phase C) see complete figures.
        # Best-effort — a cascade failure must not sink an otherwise good
        # run; the leaves are still persisted.
        if _canonical_facts_enabled():
            try:
                from concept_model.cascade import recompute_after_turn
                recompute_after_turn(AUDIT_DB_PATH, run_id)
            except Exception:
                logger.exception("canonical cascade failed for run %s", run_id)

        # Peer-review C1: the main drain loop has exited (it stopped when
        # the fan-in sentinel arrived). The post-pipeline stages below —
        # correction agent + notes post-validator — still push events into
        # `event_queue`. Without this helper those events would be
        # stranded: no DB persistence, no SSE yields. The helper drains
        # the queue while a helper task runs, persisting and yielding
        # each event through the outer generator so the frontend + DB
        # see live updates from the pseudo-agents.
        async def _drain_while_running(task: asyncio.Task):
            """Yield events from the queue while ``task`` runs, then flush
            anything left behind after it completes. Swallows the sentinel
            value (None) so a stale sentinel doesn't break the outer loop."""
            while not task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    continue
                yield event
            # Final non-blocking sweep — agent events enqueued in the last
            # ms before the task completed might still be sitting here.
            while True:
                try:
                    event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if event is None:
                    continue
                yield event

        # Notes coordinator: per-agent failures are already captured in each
        # NotesAgentResult.status and don't raise here. If the coordinator
        # ITSELF raises (setup bug, unexpected asyncio error, etc.) we must
        # NOT silently drop the failure — otherwise run_complete.success
        # flips to True even though the user asked for notes and got none.
        # Synthesize a failed NotesCoordinatorResult with one entry per
        # requested template so overall-status logic and the finalization
        # loop above both see the failure.
        notes_result: Optional[NotesCoordinatorResult] = None
        try:
            notes_result = await notes_task
        except asyncio.CancelledError:
            logger.info("Notes coordinator cancelled", extra={"session_id": session_id})
            if notes_to_run:
                notes_result = NotesCoordinatorResult(agent_results=[
                    NotesAgentResult(
                        template_type=nt,
                        status="cancelled",
                        error="Cancelled by user",
                    ) for nt in sorted(notes_to_run, key=lambda n: n.value)
                ])
        except Exception as e:
            logger.exception("Notes coordinator failed", extra={"session_id": session_id})
            if notes_to_run:
                notes_result = NotesCoordinatorResult(agent_results=[
                    NotesAgentResult(
                        template_type=nt,
                        status="failed",
                        error=f"Notes coordinator crashed: {e}",
                    ) for nt in sorted(notes_to_run, key=lambda n: n.value)
                ])

        # Generate merged result.json from per-statement files so the
        # preview tab can fetch a single file in both single- and multi-agent modes.
        # Uses a list (not dict) to preserve duplicate labels (e.g. "Lease liabilities"
        # appearing in both current and non-current sections of SOFP).
        merged_fields: list[dict] = []
        for agent_result in coordinator_result.agent_results:
            stmt_result_path = Path(output_dir) / f"{agent_result.statement_type.value}_result.json"
            if stmt_result_path.exists():
                try:
                    stmt_data = json.loads(stmt_result_path.read_text(encoding="utf-8"))
                    stmt_key = agent_result.statement_type.value
                    raw_fields = (
                        stmt_data.get("fields", [])
                        if isinstance(stmt_data, dict) else []
                    )
                    for field in raw_fields:
                        # Same defence as the audit-persist loop below: a
                        # non-dict entry in `fields` (observed on SOCI/SOCIE)
                        # would raise "'list' object has no attribute 'get'"
                        # and the except drops the WHOLE statement from the
                        # preview result.json. Skip the bad entry instead.
                        if not isinstance(field, dict):
                            continue
                        merged_fields.append({
                            "statement": stmt_key,
                            "field_label": field.get("field_label", ""),
                            "value": field.get("value"),
                            "section": field.get("section"),
                        })
                except Exception:
                    logger.warning("Failed to merge result for %s", agent_result.statement_type.value, exc_info=True)
        if merged_fields:
            merged_result_path = Path(output_dir) / "result.json"
            merged_result_path.write_text(
                json.dumps({"fields": merged_fields}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Build workbook paths from ALL *_filled.xlsx in the session directory.
        # This ensures reruns merge with previously successful workbooks.
        all_workbook_paths: Dict[StatementType, str] = {}
        for stmt in StatementType:
            wb_path = session_dir / f"{stmt.value}_filled.xlsx"
            if wb_path.exists():
                all_workbook_paths[stmt] = str(wb_path)
        # Override with any just-completed workbooks from this run
        all_workbook_paths.update(coordinator_result.workbook_paths)

        # Phase C: in canonical mode the DB (run_concept_facts) is the
        # authoritative store — export each succeeded statement's workbook
        # from the facts and repoint the merge inputs at it, so the
        # downloaded filled.xlsx reflects the cascaded DB values (and any
        # later canonical correction) rather than the agent's scratch xlsx.
        if _canonical_facts_enabled():
            try:
                _ip_cy, _ip_py = _reporting_periods_from_infopack(infopack)
                _export_canonical_workbooks(
                    run_id=run_id,
                    agent_results=coordinator_result.agent_results,
                    all_workbook_paths=all_workbook_paths,
                    session_dir=session_dir,
                    filing_level=run_config.filing_level,
                    filing_standard=run_config.filing_standard,
                    db_path=AUDIT_DB_PATH,
                    reporting_period_cy=_ip_cy,
                    reporting_period_py=_ip_py,
                    event_sink=_enqueue_system_error,
                )
            except Exception:
                logger.exception(
                    "canonical export pass failed for run %s — falling back "
                    "to agent-written workbooks", run_id,
                )

        # Same pattern for notes workbooks — pick up prior partial runs + this run's output.
        all_notes_workbook_paths: Dict[NotesTemplateType, str] = {}
        for nt in NotesTemplateType:
            wb_path = session_dir / f"NOTES_{nt.value}_filled.xlsx"
            if wb_path.exists():
                all_notes_workbook_paths[nt] = str(wb_path)
        if notes_result is not None:
            all_notes_workbook_paths.update(notes_result.workbook_paths)

        # Phase 6: stage boundary — extraction is fully drained, we're
        # about to merge per-statement workbooks into filled.xlsx.
        # Push to queue + drain so the event rides the standard
        # GeneratorExit-tolerant yield path.
        _emit_stage("merging")
        while True:
            try:
                evt = event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if evt is None:
                continue
            persist_event(evt)
            if client_connected:
                try:
                    yield evt
                except (asyncio.CancelledError, GeneratorExit):
                    client_connected = False

        # PLAN-stop-and-validation-visibility Phase 4: classify common
        # post-extraction errors with a type discriminator the frontend
        # can route on. Mirrors the per-event ``data.type`` pattern the
        # correction agent already uses for its exhausted-budget event.
        # Distinct buckets keep "merge failed" from looking like
        # "agent crashed" — the user-facing remediation is different.
        # Merge workbooks (Phase 7.4). Notes sheets land after face sheets.
        # RUN-REVIEW peer-review #1 (HIGH): pass skip_recalc=True so
        # formulas survive into the post-merge stage. The CORRECTION
        # agent (when triggered) writes to the merged workbook via
        # fill_workbook, which relies on `cell.value.startswith("=")`
        # to refuse formula-cell overwrites. If recalc replaced
        # *Total formulas with literals at merge time, that guard is
        # silently defeated AND any leaf-only writes from the
        # corrector fail to propagate (totals stay stale). Recalc
        # happens once below, AFTER correction has finished, so the
        # downloaded workbook still carries cached values for
        # downstream programmatic readers (compare_results.py, etc.).
        merge_result = merge_workbooks(
            all_workbook_paths,
            merged_path,
            notes_workbook_paths=all_notes_workbook_paths,
            skip_recalc=True,
        )

        # Record the merged workbook path on the runs row. History's download
        # endpoint reads this as the single source of truth — never derived
        # from session_id. Peer-review C6: we deliberately DO NOT commit
        # here; the write stays in the pending transaction and is flushed
        # alongside per-agent state (line below) so there is no moment
        # between commits where `merged_workbook_path` is durable but the
        # final status is not yet written. Hard-kill (SIGKILL/OOM) between
        # the next commit and `mark_run_finished` can still leave
        # `status='running'` — that corner needs startup recovery, which is
        # explicitly out of scope for this round.
        if merge_result.success and db_conn is not None and run_id is not None:
            try:
                repo.mark_run_merged(db_conn, run_id, merged_path)
            except Exception:
                logger.warning(
                    "Failed to mark run_merged on run %s", run_id, exc_info=True,
                )

        # Phase 4: surface merge failure as an SSE error event. The
        # success path already covers itself via run_complete; the
        # failure path used to log + continue silently, so the user
        # got "run_complete success=false" with no diagnostic.
        if not merge_result.success:
            err_msg = "; ".join(merge_result.errors) or "unknown merge error"
            _enqueue_system_error({
                "type": "merge_failed",
                "message": (
                    "We couldn't assemble the final Excel file from the extracted "
                    "statements. The individual statements may still be available "
                    "to download."
                ),
                "traceback": err_msg,
                "errors": list(merge_result.errors),
            })
            # Drain the event we just pushed.
            while True:
                try:
                    evt = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if evt is None:
                    continue
                persist_event(evt)
                if client_connected:
                    try:
                        yield evt
                    except (asyncio.CancelledError, GeneratorExit):
                        client_connected = False

        # Run cross-checks (Phase 5 wiring). See `_build_default_cross_checks`
        # at module scope for the canonical registry the MPERS wiring tests
        # pin against.
        all_checks = _build_default_cross_checks()
        check_config = {
            "statements_to_run": statements_to_run,
            "variants": {stmt: v for stmt, v in variants.items()},
            "filing_level": run_config.filing_level,
            "filing_standard": run_config.filing_standard,
        }
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
        # Phase 6: stage boundary — about to run the initial
        # cross-check pass. Pushed via the queue so the drain happens
        # together with the cross-check progress emissions below.
        _emit_stage("cross_checking")
        # Phase 4: cross-check exceptions (corrupt workbook, missing
        # sheet, etc.) used to propagate to the outer except and kill
        # the run with a generic "Stream error" message. Catch them
        # here so we surface a structured `cross_check_exception`
        # SSE error and let run_complete still fire — the run lands
        # as completed_with_errors instead of failed/aborted.
        #
        # Peer-review fix (2026-04-27): track whether a cross-check
        # pass crashed so the final-status logic can flip to
        # ``completed_with_errors`` and ``run_complete.success=false``.
        # Without this, an empty cross_check_results list would make
        # ``any_check_failed=False`` and the run would silently report
        # success=true even though validation never ran.
        cross_check_crashed = False

        # Peer-review fix (2026-04-27): live per-check progress —
        # rather than batching cross_check_result events after
        # ``run_cross_checks`` returns, emit them via the on_check
        # callback as each check resolves. Send a ``cross_check_start``
        # frame first so the UI can size its progress block. The
        # post-pass ``cross_check_complete`` aggregator still fires
        # below in ``_emit_cross_check_progress``, so the existing
        # event-shape contract is preserved.
        try:
            event_queue.put_nowait({
                "event": "cross_check_start",
                "data": {"phase": "initial", "total": len(all_checks)},
            })
        except asyncio.QueueFull:
            logger.warning("event_queue full; cross_check_start dropped")

        def _on_initial_check(idx: int, total: int, result) -> None:
            try:
                event_queue.put_nowait({
                    "event": "cross_check_result",
                    "data": {
                        "phase": "initial",
                        "index": idx,
                        "total": total,
                        "name": result.name,
                        "status": result.status,
                        "expected": result.expected,
                        "actual": result.actual,
                        "diff": result.diff,
                        "tolerance": result.tolerance,
                        "message": result.message,
                        "target_sheet": result.target_sheet,
                        "target_row": result.target_row,
                    },
                })
            except asyncio.QueueFull:
                logger.warning(
                    "event_queue full; cross_check_result idx=%d dropped", idx,
                )

        try:
            # Item 16: threaded + bounded so a slow/pathological workbook
            # can't freeze SSE for every session or pin the run in
            # ``running``. A TimeoutError lands on the same structured
            # cross_check_exception path as any other crash.
            cross_check_results = await run_cross_check_pass_async(
                select_cross_check_backend(
                    agent_results=coordinator_result.agent_results,
                    run_id=run_id,
                    filing_level=run_config.filing_level,
                    filing_standard=run_config.filing_standard,
                    # Already-merged workbooks become the lazy provider — no
                    # extra build, evaluated only if the xlsx backend runs.
                    workbook_provider=lambda: all_workbook_paths,
                ),
                all_checks, check_config,
                tolerance=tolerance,
                on_check=_on_initial_check,
            )
        except Exception as _cc_exc:  # noqa: BLE001
            logger.exception(
                "Initial cross-check pass raised on run %s", run_id,
            )
            cross_check_results = []
            cross_check_crashed = True
            _enqueue_system_error({
                "type": "cross_check_exception",
                "phase": "initial",
                "message": (
                    f"Cross-check pass crashed: "
                    f"{type(_cc_exc).__name__}: {_cc_exc}"
                ),
            })

        # Phase 6.1: advisory cross-sheet notes-consistency check. Warns
        # when Sheet 11 and Sheet 12 disagree on the PDF page for the same
        # topic (usually one side cited the printed folio instead of the
        # PDF page). Advisory only — never fails the merge; returns [] on
        # any read error so this block can't break a run.
        #
        # We fold warnings into ``cross_check_results`` with status
        # ``"warning"`` so they ride the same persistence + SSE + UI path
        # as real cross-checks. Deliberately SKIPPED when the merge
        # failed — a missing workbook means there's nothing to compare.
        if merge_result.success:
            try:
                consistency_warnings = check_notes_consistency(merged_path)
            except Exception:
                # The check has its own broad except but defence-in-depth
                # is cheap here: never let an advisory check fail a run.
                logger.warning(
                    "notes-consistency check raised unexpectedly on run %s",
                    run_id, exc_info=True,
                )
                consistency_warnings = []
            from cross_checks.framework import CrossCheckResult
            for w in consistency_warnings:
                cross_check_results.append(CrossCheckResult(
                    name=f"Notes consistency: {w.sheet_11_label} ↔ {w.sheet_12_label}",
                    status="warning",
                    message=w.message,
                ))
            # N4: generic citation-consistency pass — catches folio-vs-PDF
            # drift for ANY note ref, not just the curated topic pairs.
            # Dispatched off-loop (openpyxl full-workbook load blocks).
            cross_check_results.extend(await _run_notes_advisory_bounded(
                _run_notes_citation_consistency, merged_path, run_id,
                run_id=run_id, label="notes-citation"))
            # N1: notes↔face numeric tie-outs — a notes figure that contradicts
            # its face counterpart surfaces as a WARN.
            cross_check_results.extend(await _run_notes_advisory_bounded(
                _run_notes_face_tieouts, merged_path, run_id,
                run_config.filing_level, run_config.filing_standard,
                run_id=run_id, label="notes-face-tieout"))

        # PLAN-stop-and-validation-visibility Phase 5: surface the
        # initial cross-check pass as per-check SSE events so the
        # Validator tab can render rows progressively rather than
        # waiting for the final run_complete event.
        _emit_cross_check_summary(cross_check_results, "initial", event_queue)
        # Drain whatever the emitter just pushed (it's synchronous, so
        # everything is already in the queue). Persist for audit and
        # yield to the SSE client. Same pattern as _drain_while_running
        # but no task to wait on — the events are already there.
        while True:
            try:
                evt = event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if evt is None:
                continue
            persist_event(evt)
            if client_connected:
                try:
                    yield evt
                except (asyncio.CancelledError, GeneratorExit):
                    client_connected = False

        # Peer-review C1: track pseudo-agent outcomes so the persistence
        # block below can finish_run_agent them. None means the helper was
        # never invoked (short-circuited because there was nothing to do)
        # — its row was never created and we shouldn't finalize it.
        correction_outcome: Optional[dict] = None
        validator_outcome: Optional[dict] = None
        correction_run_agent_id: Optional[int] = None
        # Phase D/E: set when the post-correction canonical re-export or
        # re-merge fails. The facts are corrected but the downloadable
        # workbook would be stale — a split-brain. We force the run to
        # completed_with_errors and surface an SSE error rather than
        # silently shipping a stale download (peer-review finding 4).
        canonical_reexport_failed = False
        validator_run_agent_id: Optional[int] = None

        # Phase 3: if any hard cross-check failed, spawn the correction
        # agent once. It edits the merged workbook in place; on completion
        # we re-run the full cross-check registry so the Validator tab
        # shows the post-correction state. Bounded to 1 iteration per
        # PLAN D4 — unresolved failures after this pass surface for human
        # review, they do NOT retry.
        if merge_result.success:
            hard_failures = [cr for cr in cross_check_results if cr.status == "failed"]
            # Phase D — the reviewer pass. Driven by OPEN conflicts in
            # run_concept_conflicts (cascade-detected partial-state /
            # parent-child disagreements) PLUS failing cross-checks. Its tools
            # write resolutions through the facts API, so the fix lands in the
            # authoritative store and the download is re-exported from facts
            # afterwards (no split-brain). The legacy direct-xlsx correction
            # pass has been removed (rewrite Phase 1.1).
            from correction.reviewer_agent import load_open_conflicts
            canonical_conflicts = [
                c for c in load_open_conflicts(AUDIT_DB_PATH, run_id)
                if c.get("kind") != "correction_exhausted"
            ]
            has_issues = bool(hard_failures) or bool(canonical_conflicts)
            should_correct = has_issues
            # Reviewer auto-trigger toggle (Settings → XBRL_AUTO_REVIEW). When
            # off, a run with failures/conflicts simply finishes and the user
            # triggers the reviewer manually from the Review tab.
            if not _auto_review_enabled():
                logger.info(
                    "auto-review disabled (XBRL_AUTO_REVIEW=false) — skipping "
                    "reviewer for run %s; manual re-review still available", run_id,
                )
                should_correct = False
            # Issue 1 (2026-06-21): when the run is CLEAN (no failing checks,
            # no open conflicts), still run a grounded spot-check if enabled.
            # This reuses the whole reviewer pass (snapshot → fix → re-export →
            # revert) with a spot_check framing; `spot_check_mode` picks the
            # depth (light/full). Gated by its OWN toggle, independent of
            # XBRL_AUTO_REVIEW (that gates the failure-driven pass).
            spot_check_mode: Optional[str] = None
            if not has_issues and _spot_check_enabled():
                spot_check_mode = _spot_check_mode()
                should_correct = True
                logger.info(
                    "run %s clean — launching %s spot-check", run_id,
                    spot_check_mode,
                )
            if should_correct:
                # Create + register the CORRECTION run_agent row lazily —
                # only when we actually launch the agent — so runs without
                # failures don't churn out a "skipped" audit row and the
                # counts match the number of real agents that did work.
                if db_conn is not None and run_id is not None:
                    try:
                        correction_run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=CORRECTION_AGENT_ID,
                            variant=None,
                            model=_model_id(config.model),
                        )
                        run_agent_ids_by_agent_id[
                            CORRECTION_AGENT_ID.lower()
                        ] = correction_run_agent_id
                        db_conn.commit()
                    except Exception:
                        logger.warning(
                            "Failed to pre-create correction run_agent row",
                            exc_info=True,
                        )
                # Phase 6: stage boundary — about to launch the reviewer. The
                # most opaque stage of the run historically; this label tells
                # the UI the "10-min silent gap" everyone reports is the agent
                # running. The reviewer snapshots first + investigates root
                # cause, so it gets its own "reviewing" label (gotcha #19).
                _emit_stage("reviewing")
                # Reviewer pass (docs/Archive/PLAN-reviewer-agent.md). Driven by
                # BOTH failing cross-checks and open conflicts, snapshots the
                # original facts first (the reversibility invariant), applies
                # grounded fixes, and flags what it's stuck on.
                #
                # Reviewer model: use the user's dedicated reviewer model
                # (Settings → default_models.reviewer) when set; otherwise
                # inherit the run's extraction model.
                reviewer_model = model
                _rm = _reviewer_model_name()
                if _rm and _rm != model_name:
                    try:
                        reviewer_model = _create_proxy_model(
                            _rm, proxy_url, api_key)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "reviewer model %s failed to build; using the "
                            "run's model instead", _rm, exc_info=True)
                        reviewer_model = model
                # Scope the reviewer's verify_fixes off the SAME in-memory
                # succeeded set the cross-check pass used. The extraction
                # run_agents rows aren't flipped to 'succeeded' in the DB until
                # the finish_run_agent loop further below — AFTER this pass — so
                # a DB-status scope would resolve zero statements and the
                # self-verifier would falsely report "all 0 PASS" (run 58).
                reviewer_verify_scope = [
                    (getattr(ar.statement_type, "value", ar.statement_type),
                     ar.variant)
                    for ar in coordinator_result.agent_results
                    if ar.status == "succeeded"
                ]
                correction_task = asyncio.create_task(
                    _run_reviewer_pass(
                        failed_checks=hard_failures,
                        conflicts=canonical_conflicts,
                        model=reviewer_model,
                        filing_level=run_config.filing_level,
                        filing_standard=run_config.filing_standard,
                        event_queue=event_queue,
                        db_path=AUDIT_DB_PATH,
                        run_id=run_id,
                        spot_check=spot_check_mode,
                        pdf_path=str(session_dir / "uploaded.pdf"),
                        verify_scope=reviewer_verify_scope,
                    ))
                # Register the reviewer task so Stop-All (POST /api/abort →
                # task_registry.cancel_all) can actually cancel it. The reviewer
                # is the LONGEST post-extraction stage (root-cause investigation
                # down the face→sub→PDF chain) and the one users most often need
                # to stop — but only the extraction/notes COORDINATORS registered
                # their tasks, so cancel_all found nothing here, 404'd, and the
                # reviewer ran to completion regardless (the "Stop All doesn't
                # stop the reviewer" bug). Unregistered on every exit below.
                import task_registry
                task_registry.register(
                    session_id, CORRECTION_AGENT_ID, correction_task)
                try:
                    async for event in _drain_while_running(correction_task):
                        persist_event(event)
                        if client_connected:
                            try:
                                yield event
                            except (asyncio.CancelledError, GeneratorExit):
                                client_connected = False
                    correction_outcome = await correction_task
                except asyncio.CancelledError:
                    # User hit Stop All during the reviewer. Mirror the proven
                    # coordinator-cancel path (return cleanly, never re-raise):
                    # the merged workbook is already durable (mark_run_merged ran
                    # at merge time) and the reviewer snapshotted facts before
                    # any write (revert-to-original stays available), so the
                    # run's output survives. Finalize as 'aborted' and stop.
                    logger.info(
                        "Reviewer cancelled by user",
                        extra={"session_id": session_id})
                    # Finalize the CORRECTION pseudo-agent audit row too — this
                    # early return skips the normal finish_run_agent block, so
                    # without this the row stays 'running' under an 'aborted'
                    # run. Commit explicitly (finish_run_agent doesn't).
                    if correction_run_agent_id is not None and db_conn is not None:
                        try:
                            repo.finish_run_agent(
                                db_conn, correction_run_agent_id,
                                status="cancelled", error_type="cancelled")
                            db_conn.commit()
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "Failed to finalize CORRECTION row on cancel",
                                exc_info=True)
                    if _safe_mark_finished(db_conn, run_id, "aborted"):
                        terminal_status = "aborted"
                    if client_connected:
                        yield {"event": "error", "data": {
                            "message": "Run cancelled during review",
                            "bucket": ERROR_BUCKET_FATAL}}
                    return
                finally:
                    task_registry.unregister(session_id, CORRECTION_AGENT_ID)
                if correction_outcome.get("writes_performed", 0) > 0:
                    # Canonical mode: the agent edited FACTS, not the xlsx.
                    # Re-export each statement from the corrected facts and
                    # re-merge so the downloaded workbook matches the Concepts
                    # UI (finding 2). The cascade already re-ran inside the
                    # canonical pass, so facts are consistent here.
                    try:
                        _export_canonical_workbooks(
                            run_id=run_id,
                            agent_results=coordinator_result.agent_results,
                            all_workbook_paths=all_workbook_paths,
                            session_dir=session_dir,
                            filing_level=run_config.filing_level,
                            filing_standard=run_config.filing_standard,
                            db_path=AUDIT_DB_PATH,
                            event_sink=_enqueue_system_error,
                        )
                        remerge = merge_workbooks(
                            all_workbook_paths, merged_path,
                            notes_workbook_paths=all_notes_workbook_paths,
                            skip_recalc=True,
                        )
                        if remerge.success:
                            if db_conn is not None and run_id is not None:
                                repo.mark_run_merged(db_conn, run_id, merged_path)
                        else:
                            # Re-merge failed: the download would be stale
                            # relative to the corrected facts.
                            canonical_reexport_failed = True
                            _enqueue_system_error({
                                "type": "canonical_reexport_failed",
                                "message": (
                                    "Post-correction re-merge failed; the "
                                    "downloaded workbook may not reflect the "
                                    "corrected facts. Errors: "
                                    + "; ".join(remerge.errors or ["unknown"])
                                ),
                            })
                    except Exception as _rx:  # noqa: BLE001
                        logger.exception(
                            "post-correction re-export/re-merge failed "
                            "for run %s", run_id,
                        )
                        canonical_reexport_failed = True
                        _enqueue_system_error({
                            "type": "canonical_reexport_failed",
                            "message": (
                                f"Post-correction re-export crashed "
                                f"({type(_rx).__name__}); the downloaded "
                                f"workbook may not reflect the corrected "
                                f"facts."
                            ),
                        })
                    # Drain the error event(s) we may have enqueued so the
                    # client + DB see them in order.
                    while True:
                        try:
                            _evt = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if _evt is None:
                            continue
                        persist_event(_evt)
                        if client_connected:
                            try:
                                yield _evt
                            except (asyncio.CancelledError, GeneratorExit):
                                client_connected = False
                    # Phase 6: stage boundary — correction edited the
                    # workbook, time to re-run cross-checks against the
                    # corrected state.
                    _emit_stage("re_checking")
                    # Re-run cross-checks against the edited workbook so
                    # the UI + DB see the post-correction state.
                    #
                    # Peer-review C2: point the re-run at merged_path —
                    # the correction agent writes to the merged workbook,
                    # not the per-statement {stmt}_filled.xlsx files.
                    # Feeding all_workbook_paths back in would have the
                    # validator tab parrot the pre-correction failure
                    # status even though filled.xlsx is now correct.
                    # This matches the pattern the correction agent's
                    # own `run_cross_checks` tool uses internally.
                    merged_paths_by_stmt = {
                        stmt: merged_path for stmt in all_workbook_paths
                    }
                    # Peer-review fix (2026-04-27): mirror the initial
                    # pass's try/except so a malformed merged workbook
                    # produced by the correction agent (writes that
                    # break openpyxl readability, etc.) raises a
                    # structured ``cross_check_exception`` event with
                    # phase="post_correction" instead of crashing the
                    # whole run. ALSO mirror the on_check live emission
                    # so the post-correction validator tab fills rows
                    # in progressively.
                    try:
                        event_queue.put_nowait({
                            "event": "cross_check_start",
                            "data": {"phase": "post_correction", "total": len(all_checks)},
                        })
                    except asyncio.QueueFull:
                        logger.warning(
                            "event_queue full; post_correction "
                            "cross_check_start dropped"
                        )

                    def _on_post_check(idx: int, total: int, result) -> None:
                        try:
                            event_queue.put_nowait({
                                "event": "cross_check_result",
                                "data": {
                                    "phase": "post_correction",
                                    "index": idx,
                                    "total": total,
                                    "name": result.name,
                                    "status": result.status,
                                    "expected": result.expected,
                                    "actual": result.actual,
                                    "diff": result.diff,
                                    "tolerance": result.tolerance,
                                    "message": result.message,
                                    "target_sheet": result.target_sheet,
                                    "target_row": result.target_row,
                                },
                            })
                        except asyncio.QueueFull:
                            logger.warning(
                                "event_queue full; post_correction "
                                "cross_check_result idx=%d dropped", idx,
                            )

                    try:
                        # Item 16: threaded + bounded (see the initial pass).
                        cross_check_results = await run_cross_check_pass_async(
                            select_cross_check_backend(
                                agent_results=coordinator_result.agent_results,
                                run_id=run_id,
                                filing_level=run_config.filing_level,
                                filing_standard=run_config.filing_standard,
                                workbook_provider=lambda: merged_paths_by_stmt,
                            ),
                            all_checks, check_config,
                            tolerance=tolerance,
                            on_check=_on_post_check,
                        )
                    except Exception as _cc_exc2:  # noqa: BLE001
                        logger.exception(
                            "Post-correction cross-check re-run raised "
                            "on run %s", run_id,
                        )
                        cross_check_results = []
                        cross_check_crashed = True
                        _enqueue_system_error({
                            "type": "cross_check_exception",
                            "phase": "post_correction",
                            "message": (
                                f"Post-correction cross-check pass "
                                f"crashed: "
                                f"{type(_cc_exc2).__name__}: {_cc_exc2}"
                            ),
                        })
                    if merge_result.success:
                        try:
                            consistency_warnings = check_notes_consistency(merged_path)
                        except Exception:
                            consistency_warnings = []
                        from cross_checks.framework import CrossCheckResult
                        for w in consistency_warnings:
                            cross_check_results.append(CrossCheckResult(
                                name=(
                                    f"Notes consistency: "
                                    f"{w.sheet_11_label} ↔ {w.sheet_12_label}"
                                ),
                                status="warning",
                                message=w.message,
                            ))
                        # N4: generic citation-consistency pass
                        # (off-loop, bounded — see helper).
                        cross_check_results.extend(
                            await _run_notes_advisory_bounded(
                                _run_notes_citation_consistency,
                                merged_path, run_id,
                                run_id=run_id, label="notes-citation"))
                        # N1: notes↔face numeric tie-outs.
                        cross_check_results.extend(
                            await _run_notes_advisory_bounded(
                                _run_notes_face_tieouts,
                                merged_path, run_id,
                                run_config.filing_level,
                                run_config.filing_standard,
                                run_id=run_id, label="notes-face-tieout"))

                    # PLAN-stop-and-validation-visibility Phase 5:
                    # surface the post-correction re-run as a separate
                    # progress phase so the UI can flip rows from "old"
                    # to "new" status as each post-correction result
                    # confirms — instead of the silent dead zone we had
                    # before (run_cross_checks returns; nothing emitted
                    # until run_complete carries everything in one go).
                    _emit_cross_check_summary(
                        cross_check_results, "post_correction", event_queue,
                    )
                    while True:
                        try:
                            evt = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if evt is None:
                            continue
                        persist_event(evt)
                        if client_connected:
                            try:
                                yield evt
                            except (asyncio.CancelledError, GeneratorExit):
                                client_connected = False

        # Phase 5.5: notes post-validator. Runs only when BOTH Sheet 11
        # (ACC_POLICIES) and Sheet 12 (LIST_OF_NOTES) were produced in
        # this run. Operates on the merged workbook (so cross-sheet
        # visibility is real), after cross-checks + any Phase 3
        # correction pass, so it sees the final state the user will
        # download. Bounded to 1 iteration per PLAN D4.
        if merge_result.success and notes_result is not None:
            notes_outputs = {
                r.template_type: r.workbook_path
                for r in notes_result.agent_results
                if r.workbook_path
            }
            # Peer-review #7: the reviewer runs whenever ANY prose notes sheet
            # was targeted (10/11/12) — each check family fires only where its
            # inputs exist (cross-sheet dup still needs both 11 & 12). Gated by
            # the XBRL_NOTES_AUTO_REVIEW toggle (Step 11; default on, off in the
            # test suite via conftest so pipeline counts stay deterministic).
            from notes_types import NOTES_REGISTRY as _NOTES_REG
            _prose_types = {
                t for t, e in _NOTES_REG.items() if not getattr(e, "is_numeric", False)
            }
            have_prose_sheet = any(t in notes_outputs for t in _prose_types)
            if have_prose_sheet and _notes_auto_review_enabled():
                # Lazy pseudo-agent row — created only when the validator
                # will actually run. Without this gate, short-circuit
                # cases (no sheet 11/12) would still mint an audit row
                # and break run_agent counts in tests that expect only
                # the real extraction agents.
                if db_conn is not None and run_id is not None:
                    try:
                        validator_run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=NOTES_VALIDATOR_AGENT_ID,
                            variant=None,
                            model=_model_id(config.model),
                        )
                        run_agent_ids_by_agent_id[
                            NOTES_VALIDATOR_AGENT_ID.lower()
                        ] = validator_run_agent_id
                        db_conn.commit()
                    except Exception:
                        logger.warning(
                            "Failed to pre-create notes-validator row",
                            exc_info=True,
                        )
                # Stage boundary — about to run the notes reviewer. Fires
                # whenever a prose notes sheet was targeted and the auto-review
                # toggle is on.
                _emit_stage("reviewing_notes")
                # N3 Stage 1: hand the scout's inventory note numbers to the
                # validator so it can report coverage gaps (notes with no
                # content on any sheet). Best-effort — degrades to no gaps.
                _inv_nums = []
                # Phase 1b sub-note structure, keyed by top-level note_num, so
                # the validator's detect_subnote_coverage_gaps can spot a note
                # that was only partly covered at sub-reference granularity
                # (e.g. a leases policy citing 3.3 + (b) but dropping (a)).
                _inv_subnotes: dict = {}
                # Rich inventory records (note_num + title + sub-refs + page
                # span) for the durable DB store the reviewer recomputes from.
                _inv_records: list[dict] = []
                try:
                    for _e in getattr(infopack, "notes_inventory", None) or []:
                        _n = getattr(_e, "note_num", None)
                        if _n is None:
                            continue
                        _inv_nums.append(int(_n))
                        _subs = [
                            str(getattr(_s, "subnote_ref", "")).strip()
                            for _s in getattr(_e, "subnotes", None) or []
                        ]
                        _subs = [s for s in _subs if s]
                        if _subs:
                            _inv_subnotes[int(_n)] = _subs
                        _pr = getattr(_e, "page_range", None) or (None, None)
                        _inv_records.append({
                            "note_num": int(_n),
                            "title": str(getattr(_e, "title", "") or ""),
                            "subnote_refs": _subs,
                            "page_lo": _pr[0] if _pr else None,
                            "page_hi": _pr[1] if len(_pr) > 1 else None,
                        })
                except Exception:  # noqa: BLE001
                    _inv_nums = []
                    _inv_subnotes = {}
                    _inv_records = []
                # Step 1: persist the reviewer's detector inputs (per-cell
                # provenance + scout inventory) into the DB so a later manual
                # re-review recomputes findings durably, not from run-dir files.
                # Best-effort: a provenance-write failure must never fail the run
                # (the factory falls back to the on-disk sidecars).
                from notes.writer import payload_sidecar_path as _sidecar_path
                _sidecar_paths = [
                    str(_sidecar_path(p)) for p in notes_outputs.values() if p
                ]
                try:
                    from notes.persistence import persist_notes_review_inputs
                    from notes.detectors import load_sidecar_entries
                    persist_notes_review_inputs(
                        db_path=str(AUDIT_DB_PATH),
                        run_id=run_id,
                        sidecar_entries=load_sidecar_entries(_sidecar_paths),
                        inventory=_inv_records,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to persist notes-review inputs for run %s",
                        run_id, exc_info=True,
                    )
                # Register a durable 'running' notes-review task so a MANUAL
                # re-review (or revert) can't race this auto pass — the manual
                # re-entrancy guard + revert guard both read this row. The auto
                # and manual passes hold independent in-process locks, so the DB
                # task state is the only cross-launch interlock.
                try:
                    _ntc = _open_audit_conn()
                    try:
                        repo.upsert_notes_review_task(
                            _ntc, run_id, "running",
                            model=_model_id(config.model))
                        _ntc.commit()
                    finally:
                        _ntc.close()
                except Exception:  # noqa: BLE001 — guard is best-effort
                    logger.warning(
                        "Failed to register notes-review task for run %s",
                        run_id, exc_info=True)
                # Step 9: the notes REVIEWER (acting successor to the validator)
                # — it FIXES findings in notes_cells, not just flags them.
                validator_outcome = None
                validator_task = asyncio.create_task(_run_notes_reviewer_pass(
                    run_id=run_id,
                    db_path=str(AUDIT_DB_PATH),
                    pdf_path=str(session_dir / "uploaded.pdf"),
                    filing_level=run_config.filing_level,
                    filing_standard=run_config.filing_standard,
                    model=model,
                    output_dir=output_dir,
                    merged_workbook_path=merged_path,
                    event_queue=event_queue,
                    sidecar_paths=_sidecar_paths,
                    inventory_note_nums=_inv_nums,
                    inventory_subnotes=_inv_subnotes,
                ))
                # Register so Stop-All reaches the notes-validator too (same
                # gap the reviewer had — see the reviewer block above).
                import task_registry
                task_registry.register(
                    session_id, NOTES_VALIDATOR_AGENT_ID, validator_task)
                try:
                    async for event in _drain_while_running(validator_task):
                        persist_event(event)
                        if client_connected:
                            try:
                                yield event
                            except (asyncio.CancelledError, GeneratorExit):
                                client_connected = False
                    validator_outcome = await validator_task
                except asyncio.CancelledError:
                    # User hit Stop All during the notes validator. The merged
                    # workbook is already durable; finalize as 'aborted' and
                    # stop (mirrors the reviewer-cancel path above).
                    logger.info(
                        "Notes validator cancelled by user",
                        extra={"session_id": session_id})
                    # Finalize the NOTES_VALIDATOR pseudo-agent row too (same
                    # reason as the reviewer handler above — early return skips
                    # the normal finish_run_agent block).
                    if validator_run_agent_id is not None and db_conn is not None:
                        try:
                            repo.finish_run_agent(
                                db_conn, validator_run_agent_id,
                                status="cancelled", error_type="cancelled")
                            db_conn.commit()
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "Failed to finalize NOTES_VALIDATOR row on cancel",
                                exc_info=True)
                    if _safe_mark_finished(db_conn, run_id, "aborted"):
                        terminal_status = "aborted"
                    if client_connected:
                        yield {"event": "error", "data": {
                            "message": "Run cancelled during notes validation",
                            "bucket": ERROR_BUCKET_FATAL}}
                    return
                finally:
                    task_registry.unregister(
                        session_id, NOTES_VALIDATOR_AGENT_ID)
                    # Release the durable notes-review interlock on every exit
                    # (success / cancel / exception) so a later manual re-review
                    # or revert isn't blocked. Startup reconcile is the backstop
                    # if the process dies before this runs.
                    try:
                        _ntc = _open_audit_conn()
                        try:
                            repo.upsert_notes_review_task(
                                _ntc, run_id, "done",
                                model=_model_id(config.model),
                                outcome=validator_outcome
                                if isinstance(validator_outcome, dict) else None)
                            _ntc.commit()
                        finally:
                            _ntc.close()
                    except Exception:  # noqa: BLE001 — best-effort release
                        logger.warning(
                            "Failed to release notes-review task for run %s",
                            run_id, exc_info=True)

        # RUN-REVIEW peer-review #1 (HIGH): recalc happens HERE — after
        # correction (if any) has had its chance to edit the merged
        # workbook. By deferring the formula-to-literal replacement
        # until now we get three things at once:
        #   1. The CORRECTION fill_workbook guard at fill_workbook.py
        #      :298-303 stays effective — `cell.value.startswith("=")`
        #      still catches *Total cells because they're still
        #      formulas during correction.
        #   2. Agent writes to leaves DO propagate to *Total cells
        #      via the formula evaluator in cross_checks/util.py, so
        #      post-correction cross-checks see the corrected totals.
        #   3. The downloaded workbook still ends up with cached
        #      values for downstream programmatic readers
        #      (compare_results.py and any future verifier).
        # Best-effort — failures fall back to the merger's existing
        # `fullCalcOnLoad=True` flag so Excel users still see correct
        # totals when they open the file.
        if merge_result.success:
            try:
                from tools.recalc import recalc_workbook
                recalc_workbook(merged_path)
            except Exception:  # noqa: BLE001 — recalc is advisory
                logger.warning(
                    "Post-correction recalc skipped — workbook saved "
                    "without cached values; Excel will recalc on first "
                    "open via fullCalcOnLoad.",
                    exc_info=True,
                )

        # Persist per-agent FINAL state + extracted fields + cross-checks.
        # Phase 6.5 moved create_run_agent() UP FRONT so tool events could
        # be persisted live as the stream came in; this block now only
        # finalises each agent row (finish_run_agent) and writes the
        # extracted-field table. The coarse `status:started` and `complete`
        # log_event() calls that lived here have been removed — the live
        # stream already persisted the real complete event with the live
        # `{success: bool, error: str | None}` shape.
        if db_conn is not None and run_id is not None:
            try:
                for agent_result in coordinator_result.agent_results:
                    run_agent_id = run_agent_ids_by_stmt.get(agent_result.statement_type)
                    if run_agent_id is None:
                        # Pre-create didn't happen (DB was unhappy earlier);
                        # fall back to creating the row now so extracted
                        # fields still have somewhere to hang off.
                        agent_model = config.models.get(agent_result.statement_type, config.model)
                        run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=agent_result.statement_type.value,
                            variant=agent_result.variant,
                            model=_model_id(agent_model),
                        )
                    status = agent_result.status
                    # Honest-completion flag (peer-review F1): a statement the
                    # agent finalised via acknowledge_unresolved IS saved, but
                    # carries a known imbalance / unfilled-mandatory that a
                    # human must review. Persist the row as
                    # completed_with_errors so History shows "needs review"
                    # rather than a clean green — the data is still there.
                    if status == "succeeded" and getattr(agent_result, "flag", None):
                        status = "completed_with_errors"
                    # Pass the coordinator-resolved variant so runs where
                    # the user didn't specify one still record which
                    # template was actually used. (Phase 6.5 pre-creates
                    # run_agents with the user-supplied variant, which may
                    # be None.)
                    repo.finish_run_agent(
                        db_conn, run_agent_id,
                        status=status,
                        workbook_path=agent_result.workbook_path,
                        variant=agent_result.variant,
                        # RUN-REVIEW P2-3: backfill token + cost telemetry
                        # so run_agents stops shipping zeros (gotcha #6).
                        total_tokens=agent_result.total_tokens,
                        total_cost=agent_result.total_cost,
                        # v8 per-turn telemetry rollups.
                        prompt_tokens=getattr(agent_result, "prompt_tokens", 0),
                        completion_tokens=getattr(agent_result, "completion_tokens", 0),
                        turn_count=getattr(agent_result, "turn_count", 0),
                        tool_call_count=getattr(agent_result, "tool_call_count", 0),
                        # v15 cache telemetry rollups (§6 rec 1: measure first).
                        cache_read_tokens=getattr(agent_result, "cache_read_tokens", 0),
                        cache_write_tokens=getattr(agent_result, "cache_write_tokens", 0),
                        # v17 (item 9): machine-readable failure class —
                        # explicit from the coordinator, derived otherwise.
                        error_type=_agent_row_error_type(
                            agent_result.status,
                            getattr(agent_result, "error_type", None),
                            agent_result.error,
                        ),
                    )
                    # v8: persist the per-turn metrics rows. Telemetry is
                    # advisory — a write failure here must never fault the
                    # run, so swallow and log (mirrors _safe_usage_backfill).
                    try:
                        repo.insert_agent_turns(
                            db_conn, run_agent_id,
                            getattr(agent_result, "turns", []) or [],
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist per-turn telemetry for %s",
                            agent_result.statement_type.value, exc_info=True,
                        )

                    # Persist extracted fields from per-statement result.json
                    result_json_path = Path(output_dir) / f"{agent_result.statement_type.value}_result.json"
                    if result_json_path.exists():
                        try:
                            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
                            raw_fields = (
                                result_data.get("fields", [])
                                if isinstance(result_data, dict) else []
                            )
                            for field in raw_fields:
                                # Defensive: a malformed result.json can carry a
                                # non-dict entry in `fields` (observed on
                                # SOCI/SOCIE), which would raise "'list' object
                                # has no attribute 'get'" and abort the WHOLE
                                # per-agent persist loop. Skip the bad entry
                                # instead of losing every field for the agent.
                                if not isinstance(field, dict):
                                    logger.warning(
                                        "Skipping non-dict field entry in %s result.json",
                                        agent_result.statement_type.value,
                                    )
                                    continue
                                repo.save_extracted_field(
                                    db_conn, run_agent_id,
                                    sheet=field.get("sheet", ""),
                                    field_label=field.get("field_label", ""),
                                    col=field.get("col", 2),
                                    value=field.get("value"),
                                    section=field.get("section"),
                                    row_num=field.get("row"),
                                    evidence=field.get("evidence"),
                                )
                        except Exception as e:
                            logger.warning("Failed to persist fields for %s: %s",
                                           agent_result.statement_type.value, e)

                # Finalize notes agent rows so History can show their status,
                # workbook path, and model for this run. Mirrors the face
                # loop above. `notes_result` may be None if the coordinator
                # itself crashed — the overall-status block below synthesizes
                # a failed result in that case, so we handle None defensively.
                notes_agent_results = (
                    notes_result.agent_results if notes_result is not None else []
                )
                for notes_agent_result in notes_agent_results:
                    run_agent_id = run_agent_ids_by_notes.get(notes_agent_result.template_type)
                    if run_agent_id is None:
                        # Pre-create didn't happen (DB was unhappy earlier).
                        run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=f"NOTES_{notes_agent_result.template_type.value}",
                            variant=None,
                            model=_model_id(config.model),
                        )
                    repo.finish_run_agent(
                        db_conn, run_agent_id,
                        status=notes_agent_result.status,
                        workbook_path=notes_agent_result.workbook_path,
                        # RUN-REVIEW P2-3: notes agents also backfill.
                        total_tokens=notes_agent_result.total_tokens,
                        total_cost=notes_agent_result.total_cost,
                        # v8 per-turn telemetry rollups (peer-review [2]).
                        prompt_tokens=getattr(notes_agent_result, "prompt_tokens", 0),
                        completion_tokens=getattr(notes_agent_result, "completion_tokens", 0),
                        turn_count=getattr(notes_agent_result, "turn_count", 0),
                        tool_call_count=getattr(notes_agent_result, "tool_call_count", 0),
                        # v15 cache telemetry rollups (§6 rec 1: measure first).
                        cache_read_tokens=getattr(notes_agent_result, "cache_read_tokens", 0),
                        cache_write_tokens=getattr(notes_agent_result, "cache_write_tokens", 0),
                        # v17 (item 9): machine-readable failure class —
                        # explicit from the coordinator, derived otherwise.
                        error_type=_agent_row_error_type(
                            notes_agent_result.status,
                            getattr(notes_agent_result, "error_type", None),
                            notes_agent_result.error,
                        ),
                    )
                    # v8: persist per-turn metrics rows for notes agents too.
                    # Advisory — never fault the run on a telemetry write.
                    try:
                        repo.insert_agent_turns(
                            db_conn, run_agent_id,
                            getattr(notes_agent_result, "turns", []) or [],
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist notes per-turn telemetry for %s",
                            notes_agent_result.template_type.value, exc_info=True,
                        )

                # Peer-review C1: finalise pseudo-agent rows so History
                # doesn't show them stuck at the initial "running" status.
                # `finish_run_agent` is safe to call even if events were
                # persisted live — it just updates the terminal status.
                if correction_run_agent_id is not None:
                    try:
                        if correction_outcome is None:
                            status = "pending"
                        elif correction_outcome.get("error"):
                            status = "failed"
                        else:
                            status = "completed"
                        # RUN-REVIEW P2-3: even when correction failed,
                        # whatever turns it ran are real spend; persist
                        # the captured totals (defaulted 0/0 if unset).
                        _co = correction_outcome or {}
                        repo.finish_run_agent(
                            db_conn, correction_run_agent_id,
                            status=status,
                            workbook_path=None,
                            total_tokens=int(_co.get("total_tokens", 0)),
                            total_cost=float(_co.get("total_cost", 0.0)),
                            # Run-168 QA fix: turn/tool-call rollups used
                            # to be omitted here, so the Activity row read
                            # "0 turns · 0 tool calls" beside real tokens.
                            prompt_tokens=int(_co.get("prompt_tokens", 0)),
                            completion_tokens=int(
                                _co.get("completion_tokens", 0)),
                            turn_count=int(_co.get("turns_used", 0)),
                            tool_call_count=int(
                                _co.get("tool_call_count", 0)),
                            # v17 (item 9): classify the reviewer outcome.
                            error_type=_error_type_for_outcome(
                                _co.get("error")),
                        )
                    except Exception:
                        logger.warning(
                            "Failed to finalize CORRECTION run_agent row",
                            exc_info=True,
                        )
                if validator_run_agent_id is not None:
                    try:
                        if validator_outcome is None:
                            status = "pending"
                        elif validator_outcome.get("error"):
                            status = "failed"
                        else:
                            status = "completed"
                        repo.finish_run_agent(
                            db_conn, validator_run_agent_id,
                            status=status,
                            workbook_path=None,
                            # v17 (item 9): classify the validator outcome.
                            error_type=_error_type_for_outcome(
                                (validator_outcome or {}).get("error")),
                        )
                    except Exception:
                        logger.warning(
                            "Failed to finalize NOTES_VALIDATOR run_agent row",
                            exc_info=True,
                        )

                # Persist cross-check results
                from cross_checks.framework import comparands_to_json
                for check_result in cross_check_results:
                    repo.save_cross_check(
                        db_conn, run_id,
                        check_name=check_result.name,
                        status=check_result.status,
                        expected=check_result.expected,
                        actual=check_result.actual,
                        diff=check_result.diff,
                        tolerance=check_result.tolerance,
                        message=check_result.message,
                        target_sheet=check_result.target_sheet,
                        target_row=check_result.target_row,
                        comparands_json=comparands_to_json(
                            getattr(check_result, "comparands", None)),
                    )
                db_conn.commit()
            except Exception as e:
                logger.warning("Failed to persist run data to audit DB: %s", e)

        # Compute the final run-level status — include merge outcome AND
        # cross-check results — and stamp it on the runs row.
        any_check_failed = any(cr.status == "failed" for cr in cross_check_results)
        notes_all_succeeded = notes_result is None or notes_result.all_succeeded
        all_agents_ok = coordinator_result.all_succeeded and notes_all_succeeded
        # RUN-REVIEW P0-1: a CORRECTION pass that hit its turn budget
        # without converging is a distinct outcome — agents/merge ran
        # fine and checks may even all be green now, but the corrector
        # bailed early. Report `correction_exhausted` so operators see
        # this in History as "needs review" rather than conflating with
        # generic completed_with_errors.
        #
        # Peer-review #5 (MEDIUM): the exhausted check is FIRST in the
        # branch order so that an exhausted run with all-green checks
        # still reports `correction_exhausted`, not `completed`. The
        # earlier ordering (completed-first) contradicted this comment
        # — a corrector that landed enough writes before its budget
        # ran out to coincidentally clear all checks would silently
        # report "completed" with no human-review signal.
        # A SPOT-CHECK (clean-run sanity pass) that merely runs out of its
        # tight turn budget is NOT a convergence failure — there were no
        # failing checks to converge on. Exclude it from `correction_exhausted`
        # so a thorough light spot-check hitting its 6-turn cap doesn't falsely
        # flag an otherwise-clean run as "needs review" (peer-review HIGH).
        correction_exhausted = bool(
            correction_outcome and correction_outcome.get("exhausted")
            and not correction_outcome.get("spot_check")
        )
        # Phase E (folds peer-review finding 4): in canonical mode the DB is
        # the authoritative store, so unresolved reconciliation conflicts mean
        # the run isn't clean even if the xlsx cross-checks pass. Surface the
        # count and let it tip an otherwise-green run to completed_with_errors.
        open_conflicts = (
            _open_conflict_count(AUDIT_DB_PATH, run_id)
            if _canonical_facts_enabled() else 0
        )
        # Honest-completion flag (peer-review F1): any face agent that
        # finalised with an acknowledged gap means the run needs human review,
        # even when every cross-check is green. Tip an otherwise-clean run to
        # completed_with_errors so History/the badge never shows a flagged run
        # as a clean success (mirrors the open_conflicts / correction_exhausted
        # treatment below).
        flagged_statements = sorted(
            r.statement_type.value
            for r in coordinator_result.agent_results
            if getattr(r, "flag", None)
        )
        any_agent_flagged = bool(flagged_statements)
        # Peer-review HIGH: a notes-validator failure marks its pseudo-agent
        # row "failed" (see finish_run_agent above), so a clean "completed"
        # run badge over a failed sub-agent is internally inconsistent. The
        # validator is a soft-fail pass (gotcha #22) — output is intact, dedup
        # just didn't run — but it's still a needs-review signal, so tip an
        # otherwise-green run to completed_with_errors like every other one.
        validator_failed = bool(
            validator_outcome and validator_outcome.get("error")
        )
        # Peer-review HIGH (2026-06-21): a reviewer / spot-check pass that
        # FAILED to run (model build, snapshot, no-facts, tool exception) must
        # not hide under a green run badge while its CORRECTION row shows
        # "failed" — that's the same internal inconsistency the validator_failed
        # / open_conflicts treatment already guards. Exclude `reviewer_exhausted`
        # (a budget exhaustion, not a hard failure): it's handled by
        # `correction_exhausted` on the failure path and is advisory-only for a
        # spot-check (above). A genuine error tips an otherwise-clean run to
        # completed_with_errors. Most relevant to the clean-run spot-check,
        # which is the only path where the reviewer runs WITHOUT failing checks
        # already forcing the run to completed_with_errors.
        reviewer_failed = bool(
            correction_outcome and correction_outcome.get("error")
            and correction_outcome.get("error") != "reviewer_exhausted"
        )
        # Notes coverage checklist (docs/PLAN-notes-coverage-and-routing.md
        # Phase 6 Step 9): after the reviewer pass, any unresolved MISSING row
        # or uninvestigated SUSPECTED-GAP, or an unavailable notes inventory,
        # tips an otherwise-clean run to completed_with_errors (PRD Decision 3).
        # `not_verified` sub-refs warn only and never reach `unresolved`.
        _coverage = (
            validator_outcome.get("coverage")
            if isinstance(validator_outcome, dict) else None
        )
        notes_coverage_unresolved = (
            _notes_coverage_enabled() and _notes_coverage_tips_status(_coverage)
        )
        if all_agents_ok and merge_result.success and correction_exhausted:
            overall_status = "correction_exhausted"
        elif canonical_reexport_failed:
            # Facts were corrected but the workbook couldn't be regenerated —
            # the download is stale relative to the Concepts UI. Never report
            # this as a clean success (peer-review finding 4).
            overall_status = "completed_with_errors"
        elif (all_agents_ok and merge_result.success and not any_check_failed
              and not cross_check_crashed and open_conflicts == 0
              and not any_agent_flagged and not validator_failed
              and not reviewer_failed and not notes_coverage_unresolved):
            # Peer-review fix (2026-04-27): a cross-check pass that
            # crashed produced an empty results list, so
            # ``any_check_failed`` is misleadingly False. Without the
            # explicit ``cross_check_crashed`` guard, validation
            # crashes silently became "completed" runs.
            overall_status = "completed"
        elif all_agents_ok and not merge_result.success:
            overall_status = "completed_with_errors"
        elif all_agents_ok and merge_result.success and open_conflicts > 0:
            # Agents, merge and xlsx cross-checks are clean, but the
            # canonical store still has unreconciled conflicts → needs review.
            overall_status = "completed_with_errors"
        elif all_agents_ok and merge_result.success and any_agent_flagged:
            # Everything ran, but at least one statement finalised with an
            # acknowledged gap (peer-review F1) → needs human review.
            overall_status = "completed_with_errors"
        elif all_agents_ok and merge_result.success and validator_failed:
            # Extraction/merge/cross-checks are clean, but the notes-validator
            # pass failed (peer-review HIGH) → needs review, not a clean badge.
            overall_status = "completed_with_errors"
        elif all_agents_ok and merge_result.success and reviewer_failed:
            # Extraction/merge/cross-checks are clean, but the reviewer /
            # spot-check pass failed to run (peer-review HIGH, 2026-06-21) →
            # needs review, not a clean badge.
            overall_status = "completed_with_errors"
        elif all_agents_ok and merge_result.success and notes_coverage_unresolved:
            # Everything else is clean, but the notes coverage checklist has an
            # unresolved missing note / uninvestigated suspected gap, or the
            # notes inventory was unavailable → needs review (PRD Decision 3).
            overall_status = "completed_with_errors"
        elif all_agents_ok and (any_check_failed or cross_check_crashed):
            overall_status = "completed_with_errors"
        else:
            overall_status = "failed"
        if _safe_mark_finished(db_conn, run_id, overall_status):
            terminal_status = overall_status

        # === Gold-standard eval (v16) ===
        # Grade the FINAL shipped output — after the reviewer pass +
        # re-export/re-merge — so the score matches the workbook the user
        # downloads (user decision 2026-06-04). Gated on the run carrying a
        # benchmark_id; a normal run skips this entirely. Wrapped so a grading
        # failure never changes the run's terminal status (gotcha #20).
        eval_score = None
        _benchmark_id = run_config.benchmark_id
        if _benchmark_id and run_id is not None:
            eval_score = _grade_run_against_benchmark(
                AUDIT_DB_PATH, run_id, _benchmark_id
            )
            if eval_score is not None and client_connected:
                try:
                    yield {"event": "eval_score", "data": eval_score}
                except (asyncio.CancelledError, GeneratorExit):
                    client_connected = False
                    logger.info(
                        "Client disconnected at eval_score yield; finalizing",
                        extra={"session_id": session_id},
                    )

        # Emit cross-check results as SSE events
        checks_data = []
        for cr in cross_check_results:
            checks_data.append({
                "name": cr.name,
                "status": cr.status,
                "expected": cr.expected,
                "actual": cr.actual,
                "diff": cr.diff,
                "tolerance": cr.tolerance,
                "message": cr.message,
                "target_sheet": cr.target_sheet,
                "target_row": cr.target_row,
            })
        cross_checks_partial = False

        # Final run_complete event — success requires agents + merge + cross-checks all passing
        notes_completed = (
            [r.template_type.value for r in notes_result.agent_results if r.status == "succeeded"]
            if notes_result is not None else []
        )
        notes_failed = (
            [r.template_type.value for r in notes_result.agent_results if r.status == "failed"]
            if notes_result is not None else []
        )
        # If the client disconnected mid-stream we cannot yield anymore —
        # a generator that caught GeneratorExit is allowed to run to
        # completion, but any further ``yield`` raises RuntimeError. The
        # run is already fully persisted in the DB (History will show it
        # correctly on reload); the client just won't see this event.
        if client_connected:
            # Phase 6: stage boundary — pipeline finished, run_complete
            # carries the final aggregate. The "done" stage tells the
            # frontend to stop spinning the active-stage indicator.
            try:
                yield _stage_event("done")
            except (asyncio.CancelledError, GeneratorExit):
                client_connected = False
                logger.info(
                    "Client disconnected at done-stage yield; finalizing",
                    extra={"session_id": session_id},
                )
        if client_connected:
            yield {"event": "run_complete", "data": {
                # Derive success from the single authoritative terminal status
                # so the live UI can't show a green badge next to a
                # reconciliation warning (peer-review). overall_status already
                # folds in merge, cross-checks, correction_exhausted, and
                # open canonical conflicts.
                "success": overall_status == "completed",
                # The single authoritative terminal status, passed through so
                # the live UI maps it to ONE honest label (UX-QA #22) instead
                # of collapsing every non-"completed" outcome to "Didn't
                # finish". completed_with_errors is a partial success, not a
                # failure.
                "overall_status": overall_status,
                "merged_workbook": merged_path if merge_result.success else None,
                "merge_errors": merge_result.errors,
                "cross_checks": checks_data,
                "cross_checks_partial": cross_checks_partial,
                "statements_completed": [r.statement_type.value for r in coordinator_result.agent_results
                                          if r.status == "succeeded"],
                "statements_failed": [r.statement_type.value for r in coordinator_result.agent_results
                                       if r.status == "failed"],
                # Honest-completion flag (peer-review F1): statements that
                # finalised with an acknowledged, audited gap. They are also
                # in statements_completed (the data is saved) — this array
                # tells the UI to badge them "needs review".
                "statements_flagged": flagged_statements,
                "notes_completed": notes_completed,
                "notes_failed": notes_failed,
                # Peer-review follow-up for regenerate-flow: surface the
                # run_id here (not just in `status: starting`) so a client
                # that connected mid-stream and missed the starting event
                # can still pick up the new run id to navigate to.
                "run_id": run_id,
                # Phase E: canonical-mode reconciliation signal — how many
                # conflicts remain open after correction. 0 in legacy mode.
                # The Concepts UI / results banner surfaces this so the user
                # knows the run needs human reconciliation.
                "open_conflicts": open_conflicts,
            }}
    except BaseException:
        # Belt-and-braces: if we reach the outer except without having
        # already recorded a terminal state, mark the run failed so History
        # never shows a dangling 'running' row. BaseException catches
        # CancelledError + KeyboardInterrupt too.
        if terminal_status is None:
            _safe_mark_finished(db_conn, run_id, "failed")
            terminal_status = "failed"
        raise
    finally:
        # Last-ditch cleanup: if no other code path left the row in a
        # terminal state (e.g. the event loop was torn down between yields),
        # call it aborted. Idempotent for rows that were already finalized.
        if terminal_status is None:
            _safe_mark_finished(db_conn, run_id, "aborted")
        # Session-wide task_registry cleanup used to live in coordinator.py's
        # finally block, but that erased notes tasks mid-flight whenever face
        # finished before notes. Now the outer orchestrator owns it — one
        # remove_session call covers scout + face + notes after every run.
        try:
            import task_registry
            task_registry.remove_session(session_id)
        except Exception:
            logger.warning(
                "task_registry.remove_session failed for %s", session_id,
                exc_info=True,
            )
        # Peer-review C3: page_cache is a process-global LRU and was
        # previously only reset by tests. On a long-running server it
        # accumulates renders across runs and pays LRU eviction churn
        # at the cap. Bound memory to one in-flight run by clearing
        # at the same teardown point as task_registry. Tests reset
        # the cache themselves so this doesn't disturb them.
        try:
            from tools import page_cache
            page_cache.reset()
        except Exception:
            logger.warning(
                "page_cache.reset failed for %s", session_id,
                exc_info=True,
            )
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Reviewer-pass orchestration helpers — used by api/reviewer.py (which reads
# them as ``server.X``). The re-review / revert ROUTES live in api/reviewer.py;
# these helpers stay here so they share the run_multi_agent_stream / fact-
# export machinery and the test monkeypatch surface (rewrite Phase 5.1).
# ---------------------------------------------------------------------------
def _reexport_remerge_durable(run_id: int) -> bool:
    """Rebuild the run's merged workbook from current facts and overwrite the
    durable ``merged_workbook_path`` so the download reflects the latest facts.

    Best-effort: returns False (and logs) on any failure so a re-export bug
    never 500s the reviewer endpoints. The download path also re-exports from
    facts on demand, so a False here degrades gracefully rather than breaking.
    """
    import shutil
    from db import repository as repo

    tmp = _reexport_and_remerge_from_facts(run_id)
    if tmp is None:
        return False
    # Apply the SAME notes overlays the download endpoint applies, so the
    # durable on-disk file reflects post-run prose (notes_cells) + numeric
    # (run_concept_facts) edits for non-download consumers — not just the
    # download stream. Each overlay returns either the input path unchanged or
    # a fresh temp; track every temp for cleanup. Best-effort: an overlay
    # failure keeps the last good path rather than aborting the durable copy.
    overlay_temps: list[Path] = []
    final = tmp
    try:
        from notes.persistence import (
            overlay_notes_cells_into_workbook,
            overlay_numeric_facts_into_workbook,
        )
        # The prose overlay needs the filing level to target the right evidence
        # column (D=Company / F=Group); the numeric overlay resolves columns
        # from concept_targets and takes no filing_level.
        _filing_level = "company"
        try:
            _rc = _open_audit_conn()
            try:
                _r = repo.fetch_run(_rc, run_id)
                _filing_level = (_r.config or {}).get("filing_level", "company") if _r else "company"
            finally:
                _rc.close()
        except Exception:  # noqa: BLE001 — default to company
            _filing_level = "company"

        def _apply_prose(p):
            return overlay_notes_cells_into_workbook(
                xlsx_path=p, run_id=run_id, db_path=str(AUDIT_DB_PATH),
                filing_level=_filing_level,
            )

        def _apply_numeric(p):
            return overlay_numeric_facts_into_workbook(
                xlsx_path=p, run_id=run_id, db_path=str(AUDIT_DB_PATH),
            )

        for _name, _overlay in (
            ("overlay_notes_cells_into_workbook", _apply_prose),
            ("overlay_numeric_facts_into_workbook", _apply_numeric),
        ):
            try:
                nxt = _overlay(final)
                if nxt != final:
                    overlay_temps.append(nxt)
                    final = nxt
            except Exception:  # noqa: BLE001 — overlay is best-effort
                logger.exception(
                    "durable re-export overlay %s failed for run %s",
                    _name, run_id,
                )
        conn = _open_audit_conn()
        try:
            run = repo.fetch_run(conn, run_id)
            if run is not None and run.merged_workbook_path:
                shutil.copyfile(final, run.merged_workbook_path)
                repo.mark_run_merged(conn, run_id, run.merged_workbook_path)
                conn.commit()
                return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("durable re-export copy failed for run %s", run_id)
    finally:
        for _p in [tmp, *overlay_temps]:
            try:
                _p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    return False


def _active_flag_guidance(run_id: int) -> str:
    """Fold the run's ACTIVE flags (open + answered) into a text block the
    re-review feeds back to the reviewer.

    Includes still-open flags — not just answered ones — so the reviewer
    keeps its own prior stuck / dispute context on a manual re-review and
    doesn't repeat dead ends (peer-review LOW). Each line carries the flag's
    status and reasoning, plus the human's answer when one was given.
    """
    conn = _open_audit_conn()
    try:
        rows = conn.execute(
            "SELECT id, category, reasoning, status, human_answer "
            "FROM reviewer_flags WHERE run_id = ? "
            "AND status IN ('open', 'answered') ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return ""
    lines = ["Your earlier flags on this run (re-investigate these):"]
    for r in rows:
        line = (
            f"- flag {r['id']} ({r['category']}, {r['status']}): "
            f"{r['reasoning']}"
        )
        if r["human_answer"]:
            line += f" → human says: {r['human_answer']}"
        lines.append(line)
    return "\n".join(lines)


# Durable registry of running / finished manual re-review passes, keyed by
# run_id, in the `run_review_tasks` table (schema v13, Phase 5.3). A
# re-review can take minutes (it reads the PDF and traces each failure), so
# the POST launches it as a background task and returns immediately; the
# Review tab polls GET /re-review/status for the outcome. Only the latest
# pass per run is tracked — a new POST overwrites the row (run_id is the PK).
# Persisting to the DB (vs the old in-process dict) means a finished outcome
# survives a server restart and a poll can still fetch it; startup retires
# any pass left `running` by a dead process (see `_lifespan` →
# `reconcile_stale_review_tasks`).
def _save_review_task(
    run_id: int, status: str, *, model_name=None, outcome=None
) -> None:
    """Persist the latest re-review task state for a run (best-effort).

    Wrapped so a telemetry-style DB hiccup on the background thread can
    never crash the daemon thread mid-pass — the worst case is a stale
    `running` row that startup reconciliation later retires.
    """
    from db import repository as repo
    try:
        conn = _open_audit_conn()
        try:
            repo.upsert_review_task(
                conn, run_id, status, model_name=model_name, outcome=outcome
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — never fault the re-review thread on a DB write
        logger.warning("failed to persist re-review task state for run %s",
                       run_id, exc_info=True)


# ---------------------------------------------------------------------------
# History API — Phase 3 of frontend-upgrade-history
#
# Four endpoints under /api/runs that the new History tab consumes:
#   GET    /api/runs                     — list with filters + pagination
#   GET    /api/runs/{id}                — hydrated detail (agents + checks)
#   DELETE /api/runs/{id}                — DB-only delete (leaves disk alone)
#   GET    /api/runs/{id}/download/filled — stream the merged workbook
#
# All reads go through `db.repository`; this module never speaks raw SQL.
# ---------------------------------------------------------------------------

def _open_audit_conn():
    """Open an audit-DB connection with the same pragmas as the lifecycle
    path. Callers must close it themselves (or use the contextmanager via
    `db_session`).

    The schema is initialised once at FastAPI startup
    (`_init_audit_db_once`). Peer-review I-5: callers that bypass the
    startup hook (ad-hoc CLI scripts importing `server`, some test
    harnesses) would otherwise hit `no such table` errors on the first
    query. We self-heal by running `init_db` if the `schema_version`
    table is missing — cheap (one PRAGMA + one SELECT) in the hot path,
    and sqlite's `CREATE TABLE IF NOT EXISTS` makes `init_db` itself
    idempotent, so the extra call is a no-op once the schema is set up.
    """
    import sqlite3
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    # Defensive init for non-lifespan callers. The `sqlite_master` probe
    # is cheap and the `init_db` path short-circuits via `IF NOT EXISTS`
    # when the schema is already present, so the common case (FastAPI
    # has already run lifespan) pays ~one extra query.
    schema_present = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='schema_version' LIMIT 1"
    ).fetchone()
    if schema_present is None:
        conn.close()
        from db.schema import init_db
        init_db(AUDIT_DB_PATH)
        conn = sqlite3.connect(str(AUDIT_DB_PATH))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
    return conn


def _run_summary_to_dict(summary) -> dict:
    """Serialise a repository.RunSummary for the History list JSON payload.

    Kept separate so both the list and the detail endpoint can reuse a
    consistent wire shape if we ever want to embed a summary in the detail.
    """
    return {
        "id": summary.id,
        "created_at": summary.created_at,
        "pdf_filename": summary.pdf_filename,
        "status": summary.status,
        "session_id": summary.session_id,
        "statements_run": summary.statements_run,
        "models_used": summary.models_used,
        "duration_seconds": summary.duration_seconds,
        "scout_enabled": summary.scout_enabled,
        "has_merged_workbook": bool(summary.merged_workbook_path),
        "filing_level": summary.filing_level,
        "filing_standard": summary.filing_standard,
        "denomination": summary.denomination,
        "orchestration": getattr(summary, "orchestration", "split"),
        # v16 gold-standard eval: the benchmark this run graded against (None
        # on normal runs) + the headline accuracy in [0, 1] (None when not
        # graded). Powers the History score column + sparkline.
        "benchmark_id": getattr(summary, "benchmark_id", None),
        "eval_score": getattr(summary, "eval_score", None),
        # v30 evals workspace: the build that produced this run (None on legacy
        # rows). Powers the History version column + trend attribution.
        "app_version": getattr(summary, "app_version", None),
    }


# Because `from` is a Python keyword we cannot name a function parameter
# `from`. FastAPI exposes a `Query(..., alias="from")` pattern but our
# simpler approach is a second entry point that reads raw query params off
# the request and forwards them — keeps the typed handler above clean.
@app.middleware("http")
async def _history_date_range_alias(request: Request, call_next):
    """Rewrite `?from=...&to=...` to `?date_from=...&date_to=...` on /api/runs.

    The frontend uses the human-friendly names; the handler signature uses
    the Python-safe names. Doing the rewrite here keeps both ends ergonomic
    without polluting unrelated endpoints.
    """
    # Only touch /api/runs reads — never anywhere else.
    if request.url.path == "/api/runs" and request.method == "GET":
        params = dict(request.query_params)
        mutated = False
        if "from" in params and "date_from" not in params:
            params["date_from"] = params.pop("from")
            mutated = True
        if "to" in params and "date_to" not in params:
            params["date_to"] = params.pop("to")
            mutated = True
        if mutated:
            # Rebuild the querystring and reassign. Starlette's request
            # query params are immutable; we swap the underlying scope.
            from urllib.parse import urlencode
            new_qs = urlencode(params)
            request.scope["query_string"] = new_qs.encode("utf-8")
    return await call_next(request)


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    """Gate every /api/* route on a valid session (PLAN auth Phase 1.2).

    Exempt: /api/auth/* (so login works) and /api/health. Static SPA assets are
    public — they carry no data and the SPA redirects to login when
    /api/auth/me returns 401. AUTH_MODE=dev (local only) bypasses the gate with
    an auto-session.

    A valid request bumps the sliding-window timer UNLESS the path is a
    background poll (e.g. re-review status) — that keeps an idle-but-polling tab
    from staying logged in forever, while genuine API use refreshes the session.
    """
    from auth import config as auth_config
    from auth import middleware as auth_mw
    from auth import sessions as auth_sessions
    from db import repository as repo

    path = request.url.path
    if auth_config.dev_bypass_active() or not auth_mw.is_guarded(path):
        return await call_next(request)

    # One-time production diagnostic: the per-(email, IP) login lockout buckets
    # on request.client.host, which only reflects the real peer when uvicorn is
    # started with --proxy-headers (Azure terminates at its front end). Log the
    # observed peer vs X-Forwarded-For once so an operator can confirm headers
    # are flowing — if peer is the front end and XFF differs, --proxy-headers is
    # missing and lockout would bucket every user together.
    _maybe_log_proxy_header_diag(request)

    cookie_value = request.cookies.get(auth_config.cookie_name())
    conn = _open_audit_conn()
    try:
        session, _status = auth_mw.resolve_session(conn, cookie_value)
        if session is None:
            # Both "missing" and "expired" surface as 401 — the frontend treats
            # any 401 as "show the login page".
            conn.commit()  # persist the expired-row delete, if any
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated.", "reason": _status},
            )
        # Bump the sliding window only on real activity, and only when the
        # stored timestamp is actually stale (auth_sessions.should_bump_activity)
        # — skipping the write on back-to-back requests avoids one UPDATE per
        # API call on the busy run page.
        if auth_mw.counts_as_activity(path) and auth_sessions.should_bump_activity(session):
            repo.touch_auth_session(conn, session.session_id)
        conn.commit()
    finally:
        conn.close()
    # Make the identity available to downstream handlers (audit/attribution).
    request.state.auth_email = session.email
    return await call_next(request)


# --- Serve built frontend (Vite output in dist/) ---
#
# Two-layer wiring:
#
#  1. A SPA-fallback catch-all is registered BEFORE the StaticFiles mount.
#     For any non-API GET that StaticFiles can't satisfy with a real file,
#     we return `index.html` so the React router can pick the URL up on the
#     client side. Without this, refreshing /history (or any future client
#     route) returns 404 in production.
#
#  2. The StaticFiles mount still serves real assets (JS bundles, CSS,
#     images, the hashed Vite outputs under /assets/...) verbatim. The
#     fallback only fires when StaticFiles itself would 404.
#
# Why register the fallback BEFORE the mount? FastAPI matches routes in
# definition order. The mount at "/" is greedy and would otherwise
# intercept every GET first.
#
# Extracted as a helper so tests can wire it up against a temp dist
# directory without monkeypatching module globals.

def mount_spa(app, dist_directory: Path) -> None:
    """Register the SPA fallback + StaticFiles mount onto a FastAPI app.

    Idempotent only at module-load time — calling this twice on the same
    app will register two catch-all handlers, which is fine for testing
    but should not happen in production code paths.
    """
    index_html = dist_directory / "index.html"
    resolved_dist = dist_directory.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # API routes never fall through to the SPA — a typo'd /api/... must
        # surface as a real 404 so client code doesn't parse HTML as JSON.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="Not found")

        # If the request resolves to a real file inside dist/, serve it.
        # Path-traversal guard: resolve and confirm the result is still
        # under dist_dir before opening it.
        if full_path:
            candidate = (dist_directory / full_path).resolve()
            try:
                candidate.relative_to(resolved_dist)
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found")
            if candidate.is_file():
                return FileResponse(str(candidate))

        # Otherwise hand back the SPA shell. The client router takes over
        # from here and renders the right view based on window.location.
        return FileResponse(str(index_html), media_type="text/html")

    # Mount StaticFiles AFTER the catch-all so the catch-all wins for
    # arbitrary paths but the mount can still handle the bare "/" request
    # (and gives us the asset MIME-type defaults StaticFiles bakes in).
    app.mount("/", StaticFiles(directory=str(dist_directory), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Route modules (api/) — rewrite Phase 5.1
#
# The HTTP route surface lives in cohesive routers under api/. Each handler
# reaches shared state/helpers/models through ``server.X`` at call time, so
# the long-standing test monkeypatch surface (``server._create_proxy_model``,
# ``server.OUTPUT_DIR`` …) keeps working. Imported here — after every shared
# symbol above is defined — and mounted BEFORE the SPA catch-all so /api/*
# routes win over the fallback.
#
# Entry-point alias (rewrite Phase 5.1 fix): when this file is the entry point
# (`python server.py`, per start.sh), it runs under the name "__main__", not
# "server". The api/ routers do `import server` at import time — without this
# alias that would execute THIS file a SECOND time as a fresh module "server"
# and crash on a circular import (the routers aren't defined yet on the first
# pass). Registering the running module under "server" makes that import
# resolve to this already-initialized module. No-op under `uvicorn server:app`
# or `import server` (the module is already named/cached "server").
# ---------------------------------------------------------------------------
sys.modules.setdefault("server", sys.modules[__name__])

from api.config_routes import router as _config_router
from api.uploads import router as _uploads_router
from api.run_control import router as _run_control_router
from api.reviewer import router as _reviewer_router
from api.notes_reviewer import router as _notes_reviewer_router
from api.runs import router as _runs_router
from api.notes import router as _notes_router
from api.notes_formatter import router as _notes_formatter_router
from api.files import router as _files_router
from api.eval import router as _eval_router
from api.mtool import router as _mtool_router
from auth.routes import router as _auth_router

app.include_router(_config_router)
app.include_router(_uploads_router)
app.include_router(_run_control_router)
app.include_router(_reviewer_router)
app.include_router(_notes_reviewer_router)
app.include_router(_runs_router)
app.include_router(_notes_router)
app.include_router(_notes_formatter_router)
app.include_router(_files_router)
app.include_router(_eval_router)
app.include_router(_mtool_router)
app.include_router(_auth_router)

# Re-export the moved handler functions as ``server.<name>`` so existing tests
# that import a handler off this module (e.g. ``server.download_result(...)``)
# keep resolving after the 5.1 route split. The router is the live registration;
# these are the same function objects, surfaced for direct-call test access.
from api.config_routes import (  # noqa: E402,F401
    get_config, get_settings, update_settings, test_connection,
)
from api.uploads import upload_pdf, scout_pdf  # noqa: E402,F401
from api.run_control import (  # noqa: E402,F401
    run_multi_extraction, start_run_endpoint, abort_session, abort_agent,
    rerun_notes, rerun_agent,
)
from api.reviewer import (  # noqa: E402,F401
    re_review, re_review_status, revert_to_original_endpoint,
)
from api.runs import (  # noqa: E402,F401
    list_runs_endpoint, get_run_detail_endpoint, get_agent_trace_endpoint,
    patch_run_config_endpoint, delete_run_endpoint, recheck_endpoint,
)
from api.notes import (  # noqa: E402,F401
    list_notes_cells_endpoint, patch_notes_cell_endpoint,
    notes_cells_edited_count_endpoint, facts_edited_count_endpoint,
)
from api.notes_formatter import (  # noqa: E402,F401
    launch_notes_formatter, notes_formatter_status, notes_formatter_trace,
    revert_notes_formatter,
)
from api.files import (  # noqa: E402,F401
    pdf_info_endpoint, pdf_page_endpoint, download_filled_endpoint,
    download_result,
)


dist_dir = BASE_DIR / "dist"
if dist_dir.exists():
    mount_spa(app, dist_dir)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def resolve_bind_host(env: Optional[dict] = None) -> tuple[str, bool]:
    """Resolve the uvicorn bind host and whether it exposes beyond this machine.

    Loopback by default. The app now has an auth layer gating every /api/*
    route, but binding to 0.0.0.0 still puts the login surface + paid/
    destructive endpoints (e.g. POST /api/runs/{id}/revert-to-original wipes a
    run's facts; POST /re-review spends API tokens) on the network — and under
    AUTH_MODE=dev the gate is bypassed entirely (auto-session), so there is no
    real login at all. The safe default stays 127.0.0.1; set HOST=0.0.0.0 only
    to deliberately expose on a trusted LAN (and never with AUTH_MODE=dev).

    Returns ``(host, is_exposed)`` where ``is_exposed`` is True for any
    non-loopback bind, so the caller can warn. Pure (reads the passed env, or
    ``os.environ``) so it is unit-testable without launching uvicorn.
    """
    src = os.environ if env is None else env
    host = src.get("HOST", "127.0.0.1")
    return host, host not in _LOOPBACK_HOSTS


if __name__ == "__main__":
    import uvicorn

    load_dotenv(ENV_FILE)
    host, exposed = resolve_bind_host()
    port = int(os.environ.get("PORT", "8002"))
    if exposed:
        dev_auth = os.environ.get("AUTH_MODE", "").strip().lower() == "dev"
        logger.warning(
            "XBRL Agent is binding to %s — reachable beyond this machine. "
            "%sThe login surface + destructive/paid-LLM endpoints are exposed; "
            "only do this on a trusted network. Set HOST=127.0.0.1 to restrict "
            "to this machine.",
            host,
            "AUTH_MODE=dev is set, so the auth gate is BYPASSED (no login). "
            if dev_auth else "",
        )
    logger.info(f"Starting SOFP Agent Web UI on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
