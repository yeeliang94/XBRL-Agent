"""Typed read/write helpers for the audit DB.

Why: the server and coordinator shouldn't write raw SQL. This module is the
only place that knows about SQLite schema details, so we can evolve the
schema in one place later.

All functions take an explicit `sqlite3.Connection` so the caller controls
the connection lifecycle. Use `db_session()` for the usual commit/rollback
dance.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


def _now() -> str:
    """UTC timestamp in ISO-8601 format. One place so every table agrees."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_date_bound(value: Optional[str], *, end_of_day: bool) -> Optional[str]:
    """Coerce a History date filter to an ISO timestamp aligned with the
    `created_at` column format ("YYYY-MM-DDTHH:MM:SSZ").

    The frontend's HTML <input type="date"> emits "YYYY-MM-DD" with no time
    component. A naive lexicographic comparison against "YYYY-MM-DDTHH:MM:SSZ"
    would exclude every run on the boundary day — typing today's date in the
    "to" filter would hide today's runs entirely.

    Behavior:
      - None / empty → None (no filter applied)
      - "YYYY-MM-DD" → expanded to "YYYY-MM-DDT00:00:00Z" (date_from) or
        "YYYY-MM-DDT23:59:59Z" (date_to)
      - already-ISO timestamps pass through unchanged so callers that pass
        full timestamps (existing tests, programmatic use) keep working.
    """
    if value is None or value == "":
        return None
    # Already a full timestamp (contains 'T' or 'Z' or ' ' separating date+time).
    if "T" in value or " " in value:
        return value
    # Pure date-only — expand. We do not validate the format strictly
    # (callers can already pass anything via the HTTP layer); SQLite's
    # lexicographic compare will simply miss everything if the value is
    # garbage, which is the same as today's behavior.
    suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
    return f"{value}{suffix}"


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------

@dataclass
class Run:
    id: int
    created_at: str
    pdf_filename: str
    status: str
    notes: Optional[str] = None
    # v2 lifecycle fields — see db/schema.py CURRENT_SCHEMA_VERSION comment.
    session_id: str = ""
    output_dir: str = ""
    merged_workbook_path: Optional[str] = None
    # `config` is the hydrated Python dict (None if the row was created before
    # the migration or with config=None). Stored as JSON in run_config_json.
    config: Optional[dict[str, Any]] = None
    scout_enabled: bool = False
    started_at: str = ""
    ended_at: Optional[str] = None
    # v10 audit column, sourced from `runs.orchestration`. Originally the
    # monolith-experiment flag; the experiment was removed in the rewrite
    # (Phase 1) and the column is now always 'split' (retained for schema
    # stability + History read-back).
    orchestration: str = "split"
    # v16 gold-standard eval: the benchmark this run is graded against, or
    # None on every normal (non-eval) run.
    benchmark_id: Optional[int] = None
    # v22 per-run notes-table style override (docs/PLAN-notes-table-theme.md).
    # The hydrated Python dict, or None when the run inherits the firm default.
    notes_table_style: Optional[dict[str, Any]] = None
    # v30 evals-workspace fields (docs/PLAN-evals-workspace.md). `app_version`
    # is the build that produced the run (None on legacy rows). `repeat_group_id`
    # / `repeat_index` link a run into a consistency repeat group.
    app_version: Optional[str] = None
    repeat_group_id: Optional[int] = None
    repeat_index: Optional[int] = None
    # v31 evals-workspace: links a suite child run back to its batch (E6).
    suite_run_id: Optional[int] = None


@dataclass
class RunAgent:
    id: int
    run_id: int
    statement_type: str
    variant: Optional[str]
    model: Optional[str]
    status: str
    started_at: str
    ended_at: Optional[str] = None
    workbook_path: Optional[str] = None
    total_tokens: int = 0
    total_cost: float = 0.0
    # v8 telemetry rollups (docs/PLAN-run-page-and-telemetry.md). Defaulted so
    # legacy rows / pre-v8 callers read 0.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turn_count: int = 0
    tool_call_count: int = 0
    # v15 cache telemetry rollups. Defaulted so pre-v15 rows read 0.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # v17 (item 9): machine-readable failure class. None on success and on
    # legacy rows.
    error_type: Optional[str] = None
    # Phase 7: per-agent SSE-equivalent events hydrated by get_run_detail().
    # Defaulted via field(default_factory=list) so legacy callers that build
    # RunAgent directly (e.g. fetch_run_agents) don't break — a bare `= []`
    # would be a Python mutable-default bug.
    events: list["AgentEvent"] = field(default_factory=list)
    # v8: per-turn metrics rows hydrated by get_run_detail() (list of dicts
    # keyed to run_agent_turns columns). Empty for legacy runs.
    turns: list[dict] = field(default_factory=list)


@dataclass
class AgentEvent:
    id: int
    run_agent_id: int
    ts: str
    event_type: str
    phase: Optional[str]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedField:
    id: int
    run_agent_id: int
    sheet: str
    field_label: str
    col: int
    value: Optional[float]
    section: Optional[str] = None
    row_num: Optional[int] = None
    evidence: Optional[str] = None


@dataclass
class NotesCell:
    """One row of the `notes_cells` table — canonical per-run notes payload.

    `html` is the authored HTML the post-run editor reads and edits; the
    Excel download path flattens it via `notes.html_to_text` at write time.
    `evidence` mirrors what the agent put in col D/F of the workbook; it's
    surfaced to the editor as read-only.
    """
    id: int
    run_id: int
    sheet: str
    row: int
    label: str
    html: str
    evidence: Optional[str] = None
    source_pages: list[int] = field(default_factory=list)
    updated_at: str = ""
    # v29: how the cell got its styling — 'ops' (agent format_ops), 'floor'
    # (deterministic house style), 'unstyled' (plain), or None (legacy /
    # reviewer-authored). Read-only signal for the operator.
    style_source: Optional[str] = None


@dataclass
class CrossCheck:
    id: int
    run_id: int
    check_name: str
    status: str
    expected: Optional[float] = None
    actual: Optional[float] = None
    diff: Optional[float] = None
    tolerance: Optional[float] = None
    message: Optional[str] = None
    # Review Workspace Step 8 — click-to-cell target (nullable).
    target_sheet: Optional[str] = None
    target_row: Optional[int] = None
    # v14 (reviewer holistic audit) — JSON list of the values the check
    # compared, decoded for the reviewer packet. Nullable; see
    # cross_checks.framework.Comparand.
    comparands_json: Optional[str] = None


# ---------------------------------------------------------------------------
# History-facing composite row types (Phase 2)
# ---------------------------------------------------------------------------

@dataclass
class RunSummary:
    """Lightweight row shape returned by list_runs for the History list view.

    `models_used` is aggregated from run_agents.model (the effective resolved
    model), NEVER from runs.run_config_json. See the plan's Key Decisions and
    `test_list_runs_models_used_sourced_from_run_agents_not_config`.
    """
    id: int
    created_at: str
    pdf_filename: str
    status: str
    session_id: str
    statements_run: list[str] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    duration_seconds: Optional[float] = None
    scout_enabled: bool = False
    merged_workbook_path: Optional[str] = None
    filing_level: str = "company"
    # Which taxonomy the templates came from. Stored on runs.run_config_json
    # under the same key; legacy rows (pre-MPERS wiring) default to MFRS.
    filing_standard: str = "mfrs"
    # User-declared presentation denomination, stored on runs.run_config_json
    # under the same key. Legacy rows (pre-denomination wiring) default to
    # "thousands" (RM '000), the common Malaysian case.
    denomination: str = "thousands"
    # v10 audit column, sourced from `runs.orchestration`. Always 'split'
    # now — the monolith experiment was removed in the rewrite (Phase 1);
    # the column is retained for schema stability + History read-back.
    orchestration: str = "split"
    # v16 gold-standard eval: the benchmark this run graded against (None on
    # normal runs) and its headline accuracy score (matched / gold_cells, in
    # [0, 1]); None when the run wasn't graded. Powers the History score
    # column + sparkline.
    benchmark_id: Optional[int] = None
    eval_score: Optional[float] = None
    # v30 evals workspace: the build that produced this run, so History/trends
    # can attribute quality to a version. None on legacy rows.
    app_version: Optional[str] = None


@dataclass
class RunDetail:
    """Full per-run view for the History detail panel: run + agents + checks."""
    run: "Run"
    agents: list["RunAgent"] = field(default_factory=list)
    cross_checks: list["CrossCheck"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def db_session(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection, turn on FKs, commit on success, rollback on error.

    Callers typically don't open connections themselves — they wrap a block
    of repository calls in this context manager.

    Pragmas match `SSEEventRecorder.start` in `db/recorder.py`:
      * `journal_mode = WAL` — lets readers and the recorder writer coexist
        without blocking. WAL is a DB-level setting (persists across
        connections once set), so recording it here is belt-and-braces.
      * `busy_timeout = 5000` — per-connection; without it, readers under
        `db_session` would raise `SQLITE_BUSY` when the recorder is
        mid-commit (peer-review finding I5).
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

# Terminal statuses: once a run is in one of these, mark_run_finished() will
# not overwrite ended_at on a repeat call with the same status. See
# test_mark_run_finished_is_idempotent_for_terminal_states for why: the
# except-then-finally block in run_multi_agent_stream can finalize twice.
_TERMINAL_STATUSES = frozenset(
    # `correction_exhausted` (RUN-REVIEW P0-1) is a distinct terminal
    # status: the run finished but the corrector hit its turn budget
    # without converging. Surfaces in History as "needs review" so
    # operators don't conflate it with generic completed_with_errors.
    {"completed", "completed_with_errors", "correction_exhausted",
     "failed", "aborted"}
)


def create_run(
    conn: sqlite3.Connection,
    pdf_filename: str = "",
    notes: str = "",
    *,
    session_id: str = "",
    output_dir: str = "",
    config: Optional[dict[str, Any]] = None,
    scout_enabled: bool = False,
    status: str = "running",
    orchestration: str = "split",
    app_version: Optional[str] = None,
    repeat_group_id: Optional[int] = None,
    repeat_index: Optional[int] = None,
    suite_run_id: Optional[int] = None,
) -> int:
    """Insert a new run row and return its id.

    The `pdf_filename` / `notes` positional args are preserved for backward
    compatibility with legacy callers (tests + db/recorder.py). New callers
    in the v2 lifecycle path should pass `session_id`, `output_dir`,
    `config`, and `scout_enabled` as keyword arguments so History can
    display the run meaningfully even if it later crashes.

    `status` defaults to "running" — that is the existing post-Phase-1.6
    contract for run-start callers. The upload endpoint passes status="draft"
    to record an unstarted run so the URL is shareable from the moment the
    PDF lands on disk (PLAN-persistent-draft-uploads.md).

    For drafts (`status="draft"`), `started_at` is left as the empty string
    so the History page can distinguish "never started" from "ran in zero
    seconds". Run-start flips this to a real timestamp.
    """
    now = _now()
    config_json = json.dumps(config) if config is not None else None
    # Drafts have no started_at — the run hasn't begun. The History page
    # uses started_at to compute wall-clock duration; an empty string means
    # "no duration yet" (vs ended_at-minus-started_at for finished runs).
    started_at = "" if status == "draft" else now
    # Stamp the running build so the Evals workspace can trend quality across
    # versions (v30). Resolved lazily and cached, so this is cheap after the
    # first run. Callers may override (tests pin a value).
    if app_version is None:
        from utils.app_version import get_app_version

        app_version = get_app_version()
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, notes, "
        "session_id, output_dir, run_config_json, scout_enabled, started_at, "
        "orchestration, app_version, repeat_group_id, repeat_index, suite_run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now, pdf_filename, status, notes or None,
            session_id, output_dir, config_json,
            1 if scout_enabled else 0, started_at,
            orchestration or "split",
            app_version, repeat_group_id, repeat_index, suite_run_id,
        ),
    )
    return int(cur.lastrowid)


def update_run_status(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))


def mark_draft_started(
    conn: sqlite3.Connection,
    run_id: int,
) -> bool:
    """Flip a draft row to status='running' and stamp `started_at` now.

    Used by the persistent-draft start path (POST /api/runs/{id}/start)
    so the existing draft becomes the same kind of "row created BEFORE
    coordinator runs" record that the legacy upload-then-run path
    produces. Returns True if the flip happened, False if the row was
    not in draft state (caller should 409).
    """
    cur = conn.execute(
        "UPDATE runs SET status = 'running', started_at = ? "
        "WHERE id = ? AND status = 'draft'",
        (_now(), run_id),
    )
    return cur.rowcount > 0


def _parse_notes_table_style(raw: Any) -> Optional[dict[str, Any]]:
    """Hydrate the runs.notes_table_style JSON blob to a dict (or None). A
    corrupt blob degrades to None (inherit firm default) rather than crashing
    the run-detail read."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def set_run_notes_table_style(
    conn: sqlite3.Connection,
    run_id: int,
    style: Optional[dict[str, Any]],
) -> bool:
    """Persist (or clear) a run's notes-table style override.

    Unlike `update_run_config`, this works on ANY run status — notes review
    happens AFTER extraction, so the override must be editable on a completed
    run (docs/PLAN-notes-table-theme.md). `style=None` clears it (the run falls
    back to the firm default). Returns True if a row was updated.
    """
    cur = conn.execute(
        "UPDATE runs SET notes_table_style = ? WHERE id = ?",
        (json.dumps(style) if style is not None else None, run_id),
    )
    return cur.rowcount > 0


def update_run_config(
    conn: sqlite3.Connection,
    run_id: int,
    patch: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Merge `patch` into a draft run's stored config and return the result.

    Used by `PATCH /api/runs/{id}` to persist pre-run config edits as the
    user picks statements, level, standard, models, and notes selection.

    Atomicity (peer-review MEDIUM #4): the UPDATE is guarded by
    `status='draft'` so a request that races against `mark_draft_started`
    cannot mutate a started run's stored config — that record is the
    audit-trail of what was actually extracted. Returns:

      - ``None`` when the row does not exist OR when the row exists but
        its status is no longer 'draft' (caller distinguishes via a
        prior fetch_run for the 404 path; a None return after a
        successful fetch implies the race took the row out of 'draft').
      - The merged dict on success.

    Top-level keys in `patch` overwrite their counterparts; nested dicts
    (e.g. `models`, `notes_models`) are NOT deep-merged because the
    frontend always sends the full dict for those fields.
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT run_config_json FROM runs WHERE id = ? AND status = 'draft'",
            (run_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        # Either the row doesn't exist or it isn't a draft any more.
        return None
    raw = row["run_config_json"]
    try:
        existing = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        # Corrupt blob — start fresh rather than erroring. The user's PATCH
        # is a strictly newer source of truth than the unparseable record.
        existing = {}
    merged = {**existing, **patch}
    # Mirror `orchestration` from the merged config into the canonical
    # `runs.orchestration` column (peer-review HIGH #1, 2026-05-28).
    # History list/detail source orchestration from the column, NOT the
    # JSON — see fetch_run/list_runs at the bottom of this file. The monolith
    # experiment that gave this column a second value was removed in the
    # rewrite (Phase 1); the API now normalizes any value to 'split', so the
    # column is always 'split' and is retained only for schema/History
    # read-back stability.
    new_orchestration = merged.get("orchestration") or "split"
    cur = conn.execute(
        "UPDATE runs SET run_config_json = ?, orchestration = ? "
        "WHERE id = ? AND status = 'draft'",
        (json.dumps(merged), new_orchestration, run_id),
    )
    if cur.rowcount == 0:
        # Race window between SELECT and UPDATE — another request flipped
        # the row to 'running' in between. Surface as None so the caller
        # returns 409.
        return None
    return merged


def mark_run_merged(
    conn: sqlite3.Connection, run_id: int, merged_workbook_path: str
) -> None:
    """Record the path to the final merged workbook.

    Called on the happy path right after `workbook_merger.merge` succeeds,
    BEFORE the final run-status update. History's download endpoint reads
    this column as its single source of truth — never derived from
    session_id — so a past run can be downloaded even if other output files
    get moved around later.
    """
    conn.execute(
        "UPDATE runs SET merged_workbook_path = ? WHERE id = ?",
        (merged_workbook_path, run_id),
    )


def mark_run_finished(
    conn: sqlite3.Connection, run_id: int, status: str
) -> None:
    """Transition a run to a terminal state and stamp ended_at.

    Idempotent for terminal→same-terminal transitions: if the row is already
    in `status`, this is a no-op. That guarantees the except-then-finally
    dance in `run_multi_agent_stream` cannot clobber the real finish
    timestamp when the finally block runs after an exception handler has
    already finalized the row.
    """
    row = conn.execute(
        "SELECT status, ended_at FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return
    current_status = row[0] if not isinstance(row, sqlite3.Row) else row["status"]
    if current_status == status and current_status in _TERMINAL_STATUSES:
        # Already finalized in this status — leave ended_at alone.
        return

    conn.execute(
        "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
        (status, _now(), run_id),
    )


def create_run_agent(
    conn: sqlite3.Connection,
    run_id: int,
    statement_type: str,
    variant: str | None = None,
    model: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO run_agents(run_id, statement_type, variant, model, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, statement_type, variant, model, "running", _now()),
    )
    return int(cur.lastrowid)


def reset_or_create_scout_agent_row(
    conn: sqlite3.Connection,
    run_id: int,
    model: str | None = None,
) -> int:
    """Idempotent SCOUT `run_agents` row — exactly one per run.

    The scout endpoint can fire more than once against the same run (the
    common case: the user cancels Auto-detect midway, then re-runs it).
    `create_run_agent` would INSERT a second SCOUT row each time, leaving
    History showing two SCOUT cards that both resolve to the single
    `SCOUT_conversation_trace.json` on disk (the second scout overwrites
    the first) — the "two scout traces" bug.

    Instead, REUSE the latest SCOUT row for the run when one exists: reset
    it to `running`, restamp `started_at`, refresh the model, and clear the
    prior terminal fields so the re-run reads as a fresh attempt (matching
    the single trace file it will overwrite). Only INSERT when no SCOUT row
    exists yet. Returns the row id either way.
    """
    row = conn.execute(
        "SELECT id FROM run_agents "
        "WHERE run_id = ? AND statement_type = 'SCOUT' "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is not None:
        agent_row_id = int(row[0])
        conn.execute(
            "UPDATE run_agents SET status = 'running', started_at = ?, "
            "ended_at = NULL, model = ?, error_type = NULL, "
            "total_tokens = 0, total_cost = 0 "
            "WHERE id = ?",
            (_now(), model, agent_row_id),
        )
        return agent_row_id
    return create_run_agent(conn, run_id, "SCOUT", variant=None, model=model)


def finish_run_agent(
    conn: sqlite3.Connection,
    run_agent_id: int,
    status: str,
    workbook_path: str | None = None,
    total_tokens: int = 0,
    total_cost: float = 0.0,
    variant: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    turn_count: int = 0,
    tool_call_count: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    error_type: str | None = None,
) -> None:
    """Mark an agent row as finished with final status + metrics.

    The `variant` parameter updates run_agents.variant only when non-None.
    This matters because Phase 6.5 pre-creates run_agent rows BEFORE the
    coordinator runs, and at that point we only know the user-supplied
    variant (which may be None). The coordinator later resolves a default
    variant from scout or the registry, and we need to persist that
    resolved value — otherwise History shows `variant = NULL` for any run
    where the user didn't explicitly pick a variant.

    v8 (telemetry): `prompt_tokens` / `completion_tokens` / `turn_count` /
    `tool_call_count` are the run-level rollups the Telemetry tab reads.
    They default to 0 so the many legacy call sites (notes, correction,
    validator) keep working unchanged.

    v17 (item 9): `error_type` is the machine-readable failure class
    (coordinator.py ERROR_TYPE_* constants). Defaults to None so legacy
    call sites keep working — same compatibility pattern as the v8 rollups.
    """
    if variant is not None:
        conn.execute(
            "UPDATE run_agents SET status = ?, ended_at = ?, workbook_path = ?, "
            "total_tokens = ?, total_cost = ?, prompt_tokens = ?, "
            "completion_tokens = ?, turn_count = ?, tool_call_count = ?, "
            "cache_read_tokens = ?, cache_write_tokens = ?, error_type = ?, "
            "variant = ? WHERE id = ?",
            (status, _now(), workbook_path, total_tokens, total_cost,
             prompt_tokens, completion_tokens, turn_count, tool_call_count,
             cache_read_tokens, cache_write_tokens, error_type,
             variant, run_agent_id),
        )
    else:
        conn.execute(
            "UPDATE run_agents SET status = ?, ended_at = ?, workbook_path = ?, "
            "total_tokens = ?, total_cost = ?, prompt_tokens = ?, "
            "completion_tokens = ?, turn_count = ?, tool_call_count = ?, "
            "cache_read_tokens = ?, cache_write_tokens = ?, error_type = ? "
            "WHERE id = ?",
            (status, _now(), workbook_path, total_tokens, total_cost,
             prompt_tokens, completion_tokens, turn_count, tool_call_count,
             cache_read_tokens, cache_write_tokens, error_type,
             run_agent_id),
        )


def insert_agent_turns(
    conn: sqlite3.Connection,
    run_agent_id: int,
    turns: list[dict[str, Any]],
) -> None:
    """Persist per-turn telemetry rows for one agent (v8).

    Each dict mirrors the run_agent_turns columns. Extra keys (e.g. the
    coordinator's `_n_tool_calls`) are ignored. Best-effort by contract —
    the caller wraps this so a telemetry write can never fault a run.
    """
    if not turns:
        return
    ts = _now()
    conn.executemany(
        "INSERT INTO run_agent_turns(run_agent_id, turn_index, node_kind, "
        "tool_names, prompt_tokens, completion_tokens, total_tokens, "
        "cumulative_tokens, cost_estimate, duration_ms, "
        "cache_read_tokens, cache_write_tokens, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_agent_id,
                int(t.get("turn_index") or 0),
                t.get("node_kind"),
                t.get("tool_names"),
                int(t.get("prompt_tokens") or 0),
                int(t.get("completion_tokens") or 0),
                int(t.get("total_tokens") or 0),
                int(t.get("cumulative_tokens") or 0),
                float(t.get("cost_estimate") or 0.0),
                int(t.get("duration_ms") or 0),
                int(t.get("cache_read_tokens") or 0),
                int(t.get("cache_write_tokens") or 0),
                ts,
            )
            for t in turns
        ],
    )


def log_event(
    conn: sqlite3.Connection,
    run_agent_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    phase: str | None = None,
) -> int:
    """Append one SSE-equivalent event. Payload is stored as JSON."""
    cur = conn.execute(
        "INSERT INTO agent_events(run_agent_id, ts, event_type, phase, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_agent_id, _now(), event_type, phase, json.dumps(payload or {})),
    )
    return int(cur.lastrowid)


def save_extracted_field(
    conn: sqlite3.Connection,
    run_agent_id: int,
    sheet: str,
    field_label: str,
    col: int,
    value: float | None,
    section: str | None = None,
    row_num: int | None = None,
    evidence: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO extracted_fields(run_agent_id, sheet, field_label, section, col, "
        "row_num, value, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_agent_id, sheet, field_label, section, col, row_num, value, evidence),
    )
    return int(cur.lastrowid)


def save_cross_check(
    conn: sqlite3.Connection,
    run_id: int,
    check_name: str,
    status: str,
    expected: float | None = None,
    actual: float | None = None,
    diff: float | None = None,
    tolerance: float | None = None,
    message: str | None = None,
    target_sheet: str | None = None,
    target_row: int | None = None,
    comparands_json: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, expected, actual, diff, "
        "tolerance, message, target_sheet, target_row, comparands_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, check_name, status, expected, actual, diff, tolerance, message,
         target_sheet, target_row, comparands_json),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# notes_cells (v3) — canonical per-run HTML payloads for the post-run editor
# ---------------------------------------------------------------------------

def upsert_notes_cell(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    sheet: str,
    row: int,
    label: str,
    html: str,
    evidence: Optional[str] = None,
    source_pages: Optional[list[int]] = None,
    concept_uuid: Optional[str] = None,
    style_source: Optional[str] = None,
) -> int:
    """Insert or update a single notes cell and return its id.

    `UNIQUE(run_id, sheet, row)` makes this an upsert: rerunning a notes
    agent overwrites the row the coordinator already cleared (see
    `delete_notes_cells_for_run_sheet`) or, if the delete was skipped,
    replaces the same (sheet, row) slot in place.

    Phase 7: every notes row gets a stable canonical ``concept_uuid``. If
    the caller doesn't supply one, it's minted deterministically from
    (sheet, row, label) — so the live coordinator path and the shared
    facts API agree on identity for the same cell (incl. Sheet-12
    LIST_OF_NOTES fan-out rows).
    """
    from concept_model.parser import mint_notes_concept_uuid

    now = _now()
    pages_json = json.dumps(list(source_pages)) if source_pages else None
    existing = conn.execute(
        "SELECT id, concept_uuid, style_source FROM notes_cells "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    if existing is not None:
        cell_id = int(existing[0])
        # Preserve a previously-stamped concept_uuid on update: a caller that
        # omits concept_uuid (e.g. the prose PATCH update path) must NOT
        # silently downgrade a template-scoped node_uuid back to the legacy
        # (sheet, row, label) mint. Only mint when neither the caller nor the
        # existing row supplies one (a legacy row that never had one).
        cuid = (
            concept_uuid
            or existing[1]
            or mint_notes_concept_uuid(sheet, row, label)
        )
        # Preserve a previously-recorded style_source when the caller omits one
        # (e.g. the reviewer's edit/author path, which doesn't run the styling
        # sidecar) — same "don't silently downgrade" rule as concept_uuid.
        style = style_source if style_source is not None else existing[2]
        conn.execute(
            "UPDATE notes_cells SET label = ?, html = ?, evidence = ?, "
            "source_pages = ?, updated_at = ?, concept_uuid = ?, "
            "style_source = ? WHERE id = ?",
            (label, html, evidence, pages_json, now, cuid, style, cell_id),
        )
        return cell_id
    cuid = concept_uuid or mint_notes_concept_uuid(sheet, row, label)
    cur = conn.execute(
        "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
        "evidence, source_pages, updated_at, concept_uuid, style_source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, sheet, row, label, html, evidence, pages_json, now, cuid,
         style_source),
    )
    return int(cur.lastrowid)


def list_notes_cells_for_run(
    conn: sqlite3.Connection, run_id: int,
) -> list[NotesCell]:
    """Return every notes cell for a run, ordered by (sheet, row).

    Ordering matches the template walk so the editor UI can build its
    per-sheet sections by scanning the list once.
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM notes_cells WHERE run_id = ? "
            "ORDER BY sheet, row",
            (run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior_factory

    cells: list[NotesCell] = []
    for r in rows:
        cells.append(NotesCell(
            id=r["id"], run_id=r["run_id"], sheet=r["sheet"],
            row=r["row"], label=r["label"], html=r["html"],
            evidence=r["evidence"],
            source_pages=decode_source_pages(r["source_pages"]),
            updated_at=r["updated_at"] or "",
            style_source=r["style_source"],
        ))
    return cells


def decode_source_pages(raw: Optional[str]) -> list[int]:
    """Decode a `notes_cells.source_pages` JSON blob into a clean `list[int]`.

    Fully defensive against every shape of malformation we've observed or
    could reasonably expect from legacy rows, ad-hoc DB writes, or a future
    buggy writer:

    - `None` / empty string → `[]`
    - Malformed JSON → `[]`
    - JSON decoded to a non-list (e.g. `42`, `"abc"`, `{}`) → `[]`
    - List with elements that `int(x)` rejects (None, nested lists,
      non-numeric strings like ``"abc"``) → those elements are filtered;
      the remaining ints are kept in first-seen order.
    - List with bools (True/False) → rejected. bool is an int subclass,
      so `int(True) == 1` would silently coerce to page 1.

    Peer-review S-5 clarification: numeric-looking *strings* are
    **coerced**, not filtered. ``["1", "2", "abc", "3"]`` decodes to
    ``[1, 2, 3]`` — `int("1")` succeeds, `int("abc")` raises and
    that element is dropped. This is the intended resilient-decode
    behaviour (the writer never persists strings, but a legacy row
    with JSON-encoded strings shouldn't break the editor).

    One corrupt blob should never block the editor listing for the rest
    of the run's cells — element-level filtering is the contract.
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    pages: list[int] = []
    for p in decoded:
        if isinstance(p, bool):
            continue
        try:
            pages.append(int(p))
        except (TypeError, ValueError):
            continue
    return pages


def delete_notes_cells_for_run_sheet(
    conn: sqlite3.Connection, *, run_id: int, sheet: str,
) -> int:
    """Clobber every cell for (run_id, sheet). Returns rows deleted.

    Called before a notes-agent rerun writes a fresh batch — re-run
    semantics per the plan are "replace the sheet's contents wholesale",
    not "merge on top of prior edits". The editor's confirm dialog is
    the user-facing guard; this helper is the backend enforcement.
    """
    cur = conn.execute(
        "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ?",
        (run_id, sheet),
    )
    return int(cur.rowcount)


# ---------------------------------------------------------------------------
# notes_cell_provenance + run_notes_inventory (v23) — durable detector inputs
# ---------------------------------------------------------------------------
#
# The notes reviewer's structural detectors need each written cell's source
# note refs (notes_cell_provenance) and the scout sub-note inventory
# (run_notes_inventory). Both live only in on-disk run-dir files today
# (`*_payloads.json`, `infopack.json`), which a manual re-review on a fresh
# process can't rely on. We mirror them into the DB at extraction completion so
# the reviewer recomputes findings from the database. See docs/PLAN.md Step 1.


def upsert_notes_provenance(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    sheet: str,
    row: int,
    row_label: str = "",
    source_note_refs: Optional[list[str]] = None,
    content_preview: Optional[str] = None,
) -> int:
    """Insert/replace one provenance row (UNIQUE(run_id, sheet, row))."""
    refs_json = json.dumps(list(source_note_refs)) if source_note_refs else None
    existing = conn.execute(
        "SELECT id FROM notes_cell_provenance "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    if existing is not None:
        pid = int(existing[0])
        conn.execute(
            "UPDATE notes_cell_provenance SET row_label = ?, "
            "source_note_refs = ?, content_preview = ? WHERE id = ?",
            (row_label, refs_json, content_preview, pid),
        )
        return pid
    cur = conn.execute(
        "INSERT INTO notes_cell_provenance(run_id, sheet, row, row_label, "
        "source_note_refs, content_preview) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, sheet, row, row_label, refs_json, content_preview),
    )
    return int(cur.lastrowid)


def fetch_notes_provenance(
    conn: sqlite3.Connection, run_id: int,
) -> list[dict]:
    """Return provenance rows for a run in the shape the detectors consume:
    ``[{"sheet", "row", "row_label", "source_note_refs", "content_preview"}]``.

    ``source_note_refs`` is decoded back to ``list[str]`` (malformed JSON
    degrades to ``[]`` rather than crashing the reviewer)."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT sheet, row, row_label, source_note_refs, content_preview "
            "FROM notes_cell_provenance WHERE run_id = ? ORDER BY sheet, row",
            (run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out: list[dict] = []
    for r in rows:
        try:
            refs = json.loads(r["source_note_refs"]) if r["source_note_refs"] else []
            if not isinstance(refs, list):
                refs = []
        except (TypeError, json.JSONDecodeError):
            refs = []
        out.append({
            "sheet": r["sheet"],
            "row": r["row"],
            "row_label": r["row_label"] or "",
            "source_note_refs": [str(x) for x in refs],
            "content_preview": r["content_preview"] or "",
        })
    return out


def delete_notes_provenance(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
) -> None:
    """Drop the provenance row for a (run, sheet, row).

    Keeps ``notes_cell_provenance`` in step with a reviewer ``clear`` so the
    structural detectors (which key on these rows) no longer see the cleared
    cell — without this the notes reviewer's self-verification (and a later
    manual re-review) would recompute STALE findings."""
    conn.execute(
        "DELETE FROM notes_cell_provenance "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    )


def move_notes_provenance(
    conn: sqlite3.Connection, *,
    run_id: int, from_sheet: str, from_row: int,
    to_sheet: str, to_row: int, to_label: str = "",
) -> bool:
    """Relocate a provenance row, PRESERVING its ``source_note_refs``.

    The detectors key on note refs, so a reviewer ``move`` must carry the
    refs to the destination — otherwise re-running the detectors would report
    a false coverage gap (the moved note would look uncited). Returns False
    (no-op) when the source has no provenance row (legacy / sidecar-only run)."""
    r = conn.execute(
        "SELECT row_label, source_note_refs, content_preview "
        "FROM notes_cell_provenance WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, from_sheet, from_row),
    ).fetchone()
    if r is None:
        return False
    label, refs_json, preview = r[0], r[1], r[2]
    # `[]` and `None` are deliberately equivalent across the whole provenance
    # subsystem: upsert_notes_provenance stores an empty list as SQL NULL
    # (`if source_note_refs`), fetch_notes_provenance reads NULL back as `[]`,
    # and the detectors treat "no refs" and "absent refs" identically (a note
    # with no refs contributes nothing to coverage). So collapsing falsy refs to
    # None here is consistent end-to-end — not a lossy bug. (docs/PLAN.md Step 3)
    try:
        refs = json.loads(refs_json) if refs_json else None
        if refs is not None and not isinstance(refs, list):
            refs = None
    except (TypeError, json.JSONDecodeError):
        refs = None
    conn.execute(
        "DELETE FROM notes_cell_provenance "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, from_sheet, from_row),
    )
    upsert_notes_provenance(
        conn, run_id=run_id, sheet=to_sheet, row=to_row,
        row_label=to_label or label or "",
        source_note_refs=[str(x) for x in refs] if refs else None,
        content_preview=preview,
    )
    return True


def fetch_notes_node(
    conn: sqlite3.Connection,
    *,
    sheet: str,
    row: int,
    template_prefix: str,
) -> Optional[dict]:
    """Return ``{"row","label","kind"}`` for a notes_nodes registry row, or None.

    Scoped by ``template_prefix`` (``"{standard}-{level}-"``) so the lookup
    resolves the row in the RUN's template family — the same family-scoping the
    face reviewer applies (gotcha #21). This is coordinate validation (by
    sheet+row), NOT label matching, so it respects the notes-pipeline
    all-LLM-judgement invariant: the reviewer's write tools use it to confirm a
    target is a real LEAF row, never to pick a row by label similarity.
    """
    rows = conn.execute(
        "SELECT row, label, kind, template_id FROM notes_nodes "
        "WHERE sheet = ? AND row = ?",
        (sheet, row),
    ).fetchall()
    for r in rows:
        if str(r[3] or "").startswith(template_prefix):
            return {"row": int(r[0]), "label": r[1], "kind": r[2]}
    return None


def list_notes_node_rows(
    conn: sqlite3.Connection, *, sheet: str, template_prefix: str,
) -> list[dict]:
    """Return ``[{"row","label","kind"}]`` for a sheet in the run's family,
    ordered by row. Feeds the reviewer's ``read_template_labels`` tool so it can
    pick an explicit target coordinate."""
    rows = conn.execute(
        "SELECT row, label, kind, template_id FROM notes_nodes "
        "WHERE sheet = ? ORDER BY row",
        (sheet,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        if str(r[3] or "").startswith(template_prefix):
            out.append({"row": int(r[0]), "label": r[1], "kind": r[2]})
    return out


def upsert_notes_inventory(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    note_num: int,
    title: str = "",
    subnote_refs: Optional[list[str]] = None,
    page_lo: Optional[int] = None,
    page_hi: Optional[int] = None,
) -> int:
    """Insert/replace one scout-inventory note (UNIQUE(run_id, note_num))."""
    subs_json = json.dumps(list(subnote_refs)) if subnote_refs else None
    existing = conn.execute(
        "SELECT id FROM run_notes_inventory WHERE run_id = ? AND note_num = ?",
        (run_id, note_num),
    ).fetchone()
    if existing is not None:
        iid = int(existing[0])
        conn.execute(
            "UPDATE run_notes_inventory SET title = ?, subnote_refs = ?, "
            "page_lo = ?, page_hi = ? WHERE id = ?",
            (title, subs_json, page_lo, page_hi, iid),
        )
        return iid
    cur = conn.execute(
        "INSERT INTO run_notes_inventory(run_id, note_num, title, "
        "subnote_refs, page_lo, page_hi) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, note_num, title, subs_json, page_lo, page_hi),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# notes_review_flags + notes_review_tasks (v24) — reviewer flags + async state
# ---------------------------------------------------------------------------


def insert_notes_review_flag(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    kind: str,
    reason: str = "",
    sheet: Optional[str] = None,
    row: Optional[int] = None,
) -> int:
    """Record one notes-reviewer flag (status defaults to 'open')."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO notes_review_flags(run_id, kind, reason, sheet, row, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
        (run_id, kind, reason, sheet, row, now, now),
    )
    return int(cur.lastrowid)


def fetch_notes_review_flags(
    conn: sqlite3.Connection, run_id: int,
) -> list[dict]:
    """Return every notes-reviewer flag for a run (newest first)."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, kind, reason, sheet, row, status, answer, created_at "
            "FROM notes_review_flags WHERE run_id = ? ORDER BY id DESC",
            (run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    return [
        {"id": r["id"], "kind": r["kind"], "reason": r["reason"],
         "sheet": r["sheet"], "row": r["row"], "status": r["status"],
         "answer": r["answer"], "created_at": r["created_at"]}
        for r in rows
    ]


def answer_notes_review_flag(
    conn: sqlite3.Connection, *, flag_id: int, run_id: int, answer: str,
) -> bool:
    """Attach a human answer to a flag (open → answered). Returns True if a row
    matched (scoped to run_id so a stray id can't touch another run)."""
    cur = conn.execute(
        "UPDATE notes_review_flags SET answer = ?, status = 'answered', "
        "updated_at = ? WHERE id = ? AND run_id = ?",
        (answer, _now(), flag_id, run_id),
    )
    return cur.rowcount > 0


def upsert_notes_review_task(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    model: Optional[str] = None,
    outcome: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Insert/update the durable async notes re-review task (run_id PK).

    Mirrors :func:`upsert_review_task`: a fresh launch overwrites any prior
    pass; ``created_at`` is preserved across running → done. ``outcome`` is
    serialised to JSON (NULL while running)."""
    now = _now()
    outcome_json = json.dumps(outcome) if outcome is not None else None
    existing = conn.execute(
        "SELECT created_at FROM notes_review_tasks WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if existing is not None and status != "running":
        conn.execute(
            "UPDATE notes_review_tasks SET status = ?, model = ?, "
            "outcome = ?, error = ?, updated_at = ? WHERE run_id = ?",
            (status, model, outcome_json, error, now, run_id),
        )
        return
    conn.execute(
        "INSERT INTO notes_review_tasks(run_id, status, model, outcome, error, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
        "model = excluded.model, outcome = excluded.outcome, "
        "error = excluded.error, created_at = excluded.created_at, "
        "updated_at = excluded.updated_at",
        (run_id, status, model, outcome_json, error, now, now),
    )


def claim_notes_review_task(
    conn: sqlite3.Connection, run_id: int, *, model: Optional[str] = None,
) -> bool:
    """Atomically CLAIM the run's notes re-review slot.

    Returns ``True`` when THIS caller claimed it (no pass was running), ``False``
    when one is already running. A single conditional upsert — the ``DO UPDATE``
    only fires when ``status != 'running'`` — so two concurrent POSTs can't both
    win: SQLite serialises writers, and the loser's update matches 0 rows. This
    replaces the non-atomic read-then-upsert that let two threads launch over the
    same run's notes_cells. Caller commits."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO notes_review_tasks(run_id, status, model, outcome, error, "
        "created_at, updated_at) VALUES (?, 'running', ?, NULL, NULL, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET status = 'running', "
        "model = excluded.model, outcome = NULL, error = NULL, "
        "updated_at = excluded.updated_at "
        "WHERE notes_review_tasks.status != 'running'",
        (run_id, model, now, now),
    )
    return cur.rowcount > 0


def fetch_notes_review_task(
    conn: sqlite3.Connection, run_id: int,
) -> Optional[dict[str, Any]]:
    """Return the durable notes re-review task for a run, or None."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, model, outcome, error FROM notes_review_tasks "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior
    if row is None:
        return None
    try:
        outcome = json.loads(row["outcome"]) if row["outcome"] else None
    except (TypeError, json.JSONDecodeError):
        outcome = None
    return {"status": row["status"], "model": row["model"],
            "outcome": outcome, "error": row["error"]}


def reconcile_stale_notes_review_tasks(conn: sqlite3.Connection) -> int:
    """Retire notes re-reviews orphaned by a process restart (mirrors
    :func:`reconcile_stale_review_tasks`). Returns rows reconciled."""
    now = _now()
    outcome_json = json.dumps({
        "ok": False, "invoked": False,
        "error": "Server restarted while the notes re-review was running. "
                 "Relaunch it to retry.",
    })
    cur = conn.execute(
        "UPDATE notes_review_tasks SET status = 'done', outcome = ?, "
        "error = 'restarted', updated_at = ? WHERE status = 'running'",
        (outcome_json, now),
    )
    return int(cur.rowcount)


def claim_notes_format_task(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    *,
    model: Optional[str] = None,
) -> bool:
    """Atomically claim the latest formatter slot for one run+sheet."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO notes_format_tasks("
        "run_id, sheet, status, model, summary, confidence, changed_rows, "
        "result_json, error, before_text_hash, after_text_hash, "
        "error_type, prompt_tokens, completion_tokens, "
        "cache_read_tokens, cache_write_tokens, created_at, updated_at"
        ") VALUES (?, ?, 'running', ?, NULL, NULL, 0, NULL, NULL, NULL, NULL, "
        "NULL, 0, 0, 0, 0, ?, ?) "
        "ON CONFLICT(run_id, sheet) DO UPDATE SET status = 'running', "
        "model = excluded.model, summary = NULL, confidence = NULL, "
        "changed_rows = 0, result_json = NULL, error = NULL, "
        "before_text_hash = NULL, after_text_hash = NULL, "
        "error_type = NULL, prompt_tokens = 0, completion_tokens = 0, "
        "cache_read_tokens = 0, cache_write_tokens = 0, "
        "updated_at = excluded.updated_at "
        "WHERE notes_format_tasks.status != 'running'",
        (run_id, sheet, model, now, now),
    )
    return cur.rowcount > 0


def upsert_notes_format_task(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    status: str,
    *,
    model: Optional[str] = None,
    summary: Optional[str] = None,
    confidence: Optional[float] = None,
    changed_rows: int = 0,
    result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    before_text_hash: Optional[str] = None,
    after_text_hash: Optional[str] = None,
    error_type: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    """Insert/update the durable async notes formatter task."""
    now = _now()
    result_json = json.dumps(result) if result is not None else None
    conn.execute(
        "INSERT INTO notes_format_tasks("
        "run_id, sheet, status, model, summary, confidence, changed_rows, "
        "result_json, error, before_text_hash, after_text_hash, "
        "error_type, prompt_tokens, completion_tokens, "
        "cache_read_tokens, cache_write_tokens, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, sheet) DO UPDATE SET status = excluded.status, "
        "model = excluded.model, summary = excluded.summary, "
        "confidence = excluded.confidence, changed_rows = excluded.changed_rows, "
        "result_json = excluded.result_json, error = excluded.error, "
        "before_text_hash = excluded.before_text_hash, "
        "after_text_hash = excluded.after_text_hash, "
        "error_type = excluded.error_type, "
        "prompt_tokens = excluded.prompt_tokens, "
        "completion_tokens = excluded.completion_tokens, "
        "cache_read_tokens = excluded.cache_read_tokens, "
        "cache_write_tokens = excluded.cache_write_tokens, "
        "updated_at = excluded.updated_at",
        (
            run_id, sheet, status, model, summary, confidence, changed_rows,
            result_json, error, before_text_hash, after_text_hash,
            error_type, prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, now, now,
        ),
    )


def fetch_notes_format_task(
    conn: sqlite3.Connection, run_id: int, sheet: str,
) -> Optional[dict[str, Any]]:
    """Return the latest notes formatter task for one run+sheet, or None."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, model, summary, confidence, changed_rows, "
            "result_json, error, before_text_hash, after_text_hash, "
            "error_type, prompt_tokens, completion_tokens, "
            "cache_read_tokens, cache_write_tokens, updated_at "
            "FROM notes_format_tasks WHERE run_id = ? AND sheet = ?",
            (run_id, sheet),
        ).fetchone()
    finally:
        conn.row_factory = prior
    if row is None:
        return None
    try:
        result = json.loads(row["result_json"]) if row["result_json"] else None
    except (TypeError, json.JSONDecodeError):
        result = None
    return {
        "status": row["status"], "model": row["model"],
        "summary": row["summary"], "confidence": row["confidence"],
        "changed_rows": row["changed_rows"], "result": result,
        "error": row["error"], "before_text_hash": row["before_text_hash"],
        "after_text_hash": row["after_text_hash"],
        "error_type": row["error_type"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_write_tokens": row["cache_write_tokens"],
        "updated_at": row["updated_at"],
    }


def cas_update_notes_cell_html(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    sheet: str,
    row: int,
    expected_html: str,
    new_html: str,
    style_source: Optional[str] = None,
) -> bool:
    """Statement-atomic compare-and-swap on one notes cell's HTML.

    The ``WHERE html = ?`` clause makes check-and-write ONE statement, so a
    concurrent PATCH can never land between a read and this write. Returns
    False (nothing written) when the row was edited away from
    ``expected_html`` — or no longer exists at all (sheet regenerate).
    Touches only ``html`` + ``updated_at`` (and ``style_source`` when the
    caller supplies one); label/evidence/concept_uuid are preserved.

    Pass ``style_source`` when the write re-styles the cell (the notes
    formatter passes ``'formatter'``) so the stale "unstyled"/"floor" chip
    clears — omit it (None) to leave the existing tag untouched.
    """
    if style_source is not None:
        cur = conn.execute(
            "UPDATE notes_cells SET html = ?, updated_at = ?, style_source = ? "
            "WHERE run_id = ? AND sheet = ? AND row = ? AND html = ?",
            (new_html, _now(), style_source, run_id, sheet, row, expected_html),
        )
    else:
        cur = conn.execute(
            "UPDATE notes_cells SET html = ?, updated_at = ? "
            "WHERE run_id = ? AND sheet = ? AND row = ? AND html = ?",
            (new_html, _now(), run_id, sheet, row, expected_html),
        )
    return cur.rowcount > 0


def claim_notes_format_task_guarded(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    *,
    model: Optional[str] = None,
) -> str:
    """Atomically check the notes reviewer isn't running AND claim the
    formatter slot — one BEGIN IMMEDIATE transaction, so two concurrent
    launches (formatter + reviewer) can't both pass each other's
    "other task not running" check and both claim.

    Returns ``'claimed' | 'format_running' | 'reviewer_running'``. Commits /
    rolls back itself; the caller must not wrap it in another transaction.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        other = conn.execute(
            "SELECT 1 FROM notes_review_tasks "
            "WHERE run_id = ? AND status = 'running' LIMIT 1",
            (run_id,),
        ).fetchone()
        if other is not None:
            conn.rollback()
            return "reviewer_running"
        claimed = claim_notes_format_task(conn, run_id, sheet, model=model)
        conn.commit()
        return "claimed" if claimed else "format_running"
    except Exception:
        conn.rollback()
        raise


def claim_notes_review_task_guarded(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    model: Optional[str] = None,
) -> str:
    """Mirror of :func:`claim_notes_format_task_guarded` for the reviewer
    launch: check no formatter pass is running, then claim, atomically.

    Returns ``'claimed' | 'review_running' | 'formatter_running'``.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if any_notes_format_task_running(conn, run_id):
            conn.rollback()
            return "formatter_running"
        claimed = claim_notes_review_task(conn, run_id, model=model)
        conn.commit()
        return "claimed" if claimed else "review_running"
    except Exception:
        conn.rollback()
        raise


def any_notes_format_task_running(
    conn: sqlite3.Connection, run_id: int,
) -> bool:
    """True when any sheet of this run has a formatter pass in flight.

    Interlock input: the notes reviewer must not launch over a running
    formatter (and vice versa) — both write notes_cells prose rows.
    """
    row = conn.execute(
        "SELECT 1 FROM notes_format_tasks "
        "WHERE run_id = ? AND status = 'running' LIMIT 1",
        (run_id,),
    ).fetchone()
    return row is not None


def reconcile_stale_notes_format_tasks(conn: sqlite3.Connection) -> int:
    """Retire notes formatter tasks orphaned by a process restart."""
    now = _now()
    result_json = json.dumps({
        "ok": False,
        "error": "Server restarted while the notes formatter was running. "
                 "Relaunch it to retry.",
    })
    cur = conn.execute(
        "UPDATE notes_format_tasks SET status = 'done', result_json = ?, "
        "error = 'restarted', error_type = 'restarted', "
        "summary = 'Formatter interrupted by server restart.', "
        "updated_at = ? WHERE status = 'running'",
        (result_json, now),
    )
    return int(cur.rowcount)


def save_notes_format_snapshots(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    rows: dict[int, str],
) -> None:
    """Overwrite the sheet's pre-format snapshot (schema v27).

    Written ONCE per formatter pass, before its first row write, holding the
    pre-format HTML of exactly the rows the pass is about to restyle —
    "Revert formatting" restores from here. A later pass overwrites the
    previous pass's snapshot (last-pass-only revert, by design).
    """
    now = _now()
    conn.execute(
        "DELETE FROM notes_format_snapshots WHERE run_id = ? AND sheet = ?",
        (run_id, sheet),
    )
    conn.executemany(
        "INSERT INTO notes_format_snapshots(run_id, sheet, row, html, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(run_id, sheet, row, html, now) for row, html in sorted(rows.items())],
    )


def fetch_notes_format_snapshots(
    conn: sqlite3.Connection, run_id: int, sheet: str,
) -> dict[int, str]:
    """Return the sheet's pre-format snapshot as row -> html (empty if none)."""
    return {
        int(r[0]): r[1]
        for r in conn.execute(
            "SELECT row, html FROM notes_format_snapshots "
            "WHERE run_id = ? AND sheet = ? ORDER BY row",
            (run_id, sheet),
        ).fetchall()
    }


def fetch_notes_inventory(
    conn: sqlite3.Connection, run_id: int,
) -> list[dict]:
    """Return inventory rows: ``[{"note_num", "title", "subnote_refs",
    "page_lo", "page_hi"}]`` with ``subnote_refs`` decoded to ``list[str]``."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT note_num, title, subnote_refs, page_lo, page_hi "
            "FROM run_notes_inventory WHERE run_id = ? ORDER BY note_num",
            (run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out: list[dict] = []
    for r in rows:
        try:
            subs = json.loads(r["subnote_refs"]) if r["subnote_refs"] else []
            if not isinstance(subs, list):
                subs = []
        except (TypeError, json.JSONDecodeError):
            subs = []
        out.append({
            "note_num": r["note_num"],
            "title": r["title"] or "",
            "subnote_refs": [str(x) for x in subs],
            "page_lo": r["page_lo"],
            "page_hi": r["page_hi"],
        })
    return out


# ---------------------------------------------------------------------------
# notes_coverage_rows (v28) — durable holistic coverage checklist
# ---------------------------------------------------------------------------
#
# One row per top-level note (subnote_ref NULL) plus optional per-sub-ref
# child rows. The checklist is recomputed WHOLESALE (inventory × provenance ×
# reviewer verdicts) at draft time and again after the reviewer pass, so the
# writer replaces the whole run's rows in one transaction rather than diffing.


def replace_notes_coverage_for_run(
    conn: sqlite3.Connection, run_id: int, rows: list[dict],
) -> None:
    """Delete + re-insert the run's whole coverage checklist in one go.

    ``rows`` is the flattened DB shape (top-level rows carry ``subnote_ref``
    None; child rows carry the sub-ref string). ``placements`` may be a list —
    it is JSON-encoded here. Caller commits (or relies on ``db_session``)."""
    now = _now()
    conn.execute("DELETE FROM notes_coverage_rows WHERE run_id = ?", (run_id,))
    for r in rows:
        placements = r.get("placements")
        placements_json = (
            r.get("placements_json")
            if placements is None
            else json.dumps(placements)
        )
        conn.execute(
            "INSERT INTO notes_coverage_rows("
            "run_id, note_num, subnote_ref, status, reason, placements_json, "
            "reviewer_added, reviewer_verdict, title, page_lo, page_hi, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                int(r["note_num"]),
                r.get("subnote_ref"),
                str(r.get("status", "")),
                r.get("reason") or None,
                placements_json,
                1 if r.get("reviewer_added") else 0,
                r.get("reviewer_verdict") or None,
                r.get("title") or None,
                r.get("page_lo"),
                r.get("page_hi"),
                now,
            ),
        )


def fetch_notes_coverage(
    conn: sqlite3.Connection, run_id: int,
) -> list[dict]:
    """Return the run's coverage rows (top-level rows before their children,
    each in note-number order). ``placements`` is decoded back to a list."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT note_num, subnote_ref, status, reason, placements_json, "
            "reviewer_added, reviewer_verdict, title, page_lo, page_hi "
            "FROM notes_coverage_rows WHERE run_id = ? "
            # NULL subnote_ref (the top-level row) sorts before its children.
            "ORDER BY note_num, (subnote_ref IS NOT NULL), subnote_ref",
            (run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out: list[dict] = []
    for r in rows:
        try:
            placements = (
                json.loads(r["placements_json"]) if r["placements_json"] else []
            )
            if not isinstance(placements, list):
                placements = []
        except (TypeError, json.JSONDecodeError):
            placements = []
        out.append({
            "note_num": r["note_num"],
            "subnote_ref": r["subnote_ref"],
            "status": r["status"],
            "reason": r["reason"] or "",
            "placements": placements,
            "reviewer_added": bool(r["reviewer_added"]),
            "reviewer_verdict": r["reviewer_verdict"],
            "title": r["title"] or "",
            "page_lo": r["page_lo"],
            "page_hi": r["page_hi"],
        })
    return out


# ---------------------------------------------------------------------------
# notes_cell_tombstones (v25) — durable "this cell was emptied"
# ---------------------------------------------------------------------------
#
# The notes overlay is additive (writes only surviving notes_cells rows), so it
# cannot represent a reviewer clear / move-out on its own — the original prose
# written at merge time stays in the xlsx and is reintroduced on download. A
# tombstone records each emptied coordinate so the overlay blanks it (prose +
# evidence). See notes/persistence.overlay_notes_cells_into_workbook + the
# revert path in notes/versioning.py.


def add_notes_tombstone(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
) -> None:
    """Record that a notes cell was emptied (idempotent on (run_id,sheet,row))."""
    conn.execute(
        "INSERT INTO notes_cell_tombstones(run_id, sheet, row, created_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(run_id, sheet, row) DO NOTHING",
        (run_id, sheet, row, _now()),
    )


def remove_notes_tombstone(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
) -> None:
    """Drop a tombstone — the cell was (re-)written, so it must not be blanked."""
    conn.execute(
        "DELETE FROM notes_cell_tombstones WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    )


def fetch_notes_tombstones(
    conn: sqlite3.Connection, run_id: int,
) -> list[tuple[str, int]]:
    """Return the emptied (sheet, row) coordinates for a run."""
    rows = conn.execute(
        "SELECT sheet, row FROM notes_cell_tombstones WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def clear_notes_tombstones(conn: sqlite3.Connection, run_id: int) -> int:
    """Delete every tombstone for a run (used by revert). Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM notes_cell_tombstones WHERE run_id = ?", (run_id,)
    )
    return int(cur.rowcount)


def clear_notes_tombstones_for_sheet(
    conn: sqlite3.Connection, run_id: int, sheet: str,
) -> int:
    """Delete tombstones for one (run, sheet). Called when a notes-agent rerun
    clobbers + repopulates a sheet: the reviewer's prior tombstones reference a
    superseded extraction, so a stale tombstone must not blank a freshly-written
    cell in the overlay. Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM notes_cell_tombstones WHERE run_id = ? AND sheet = ?",
        (run_id, sheet),
    )
    return int(cur.rowcount)


# ---------------------------------------------------------------------------
# run_review_tasks (v13) — durable manual re-review task state
# ---------------------------------------------------------------------------
#
# Replaces the in-process `_REVIEW_TASKS` dict in server.py (Phase 5.3). The
# status endpoint reads `fetch_review_task`; the POST launcher and the
# background thread write via `upsert_review_task`; startup calls
# `reconcile_stale_review_tasks` to retire passes orphaned by a crash.

def upsert_review_task(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    model_name: Optional[str] = None,
    outcome: Optional[dict[str, Any]] = None,
) -> None:
    """Insert or update the latest re-review task for a run.

    `run_id` is the PRIMARY KEY, so a fresh launch overwrites any prior
    pass (mirrors the old dict's ``_REVIEW_TASKS[run_id] = state``). On a
    'running' upsert `started_at` is (re)stamped; on a 'done' upsert the
    original `started_at` is preserved if a row already exists. `outcome`
    is serialised to JSON and stored only when provided (NULL while
    running).
    """
    now = _now()
    outcome_json = json.dumps(outcome) if outcome is not None else None
    existing = conn.execute(
        "SELECT started_at FROM run_review_tasks WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if existing is not None and status != "running":
        # Preserve the launch timestamp when transitioning running → done.
        prior_started = existing[0] if not isinstance(existing, sqlite3.Row) \
            else existing["started_at"]
        conn.execute(
            "UPDATE run_review_tasks SET status = ?, model_name = ?, "
            "outcome_json = ?, updated_at = ? WHERE run_id = ?",
            (status, model_name, outcome_json, now, run_id),
        )
        return
    # Fresh launch (or a row that didn't exist) — set started_at = now.
    conn.execute(
        "INSERT INTO run_review_tasks(run_id, status, model_name, "
        "outcome_json, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
        "model_name = excluded.model_name, outcome_json = excluded.outcome_json, "
        "started_at = excluded.started_at, updated_at = excluded.updated_at",
        (run_id, status, model_name, outcome_json, now, now),
    )


def fetch_review_task(
    conn: sqlite3.Connection, run_id: int
) -> Optional[dict[str, Any]]:
    """Return the latest re-review task for a run, or None if none launched.

    The dict carries ``status`` ('running' | 'done'), ``model_name``, and
    the decoded ``outcome`` (None while running). The status endpoint maps
    None → {"status": "idle"} (mirrors the old ``_REVIEW_TASKS.get`` miss).
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, model_name, outcome_json FROM run_review_tasks "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    raw = row["outcome_json"]
    try:
        outcome = json.loads(raw) if raw else None
    except (TypeError, json.JSONDecodeError):
        outcome = None
    return {
        "status": row["status"],
        "model_name": row["model_name"],
        "outcome": outcome,
    }


def reconcile_stale_runs(conn: sqlite3.Connection, max_age_hours: float = 6.0) -> int:
    """Retire extraction runs orphaned by a process restart (UX-QA #2).

    A run executes inside a streaming request; if the process dies mid-run the
    `runs` row is left `status='running'` forever. On the History page such a
    row shows Download/Delete disabled and no live indicator — a dead-end the
    user can't escape. This mirrors `reconcile_stale_review_tasks`: at startup,
    flip every `running` run older than ``max_age_hours`` to a terminal
    ``aborted`` so gotcha #10's "no row stuck non-terminal" contract holds
    across restarts. A row with a blank ``started_at`` (definitionally broken —
    run-start always stamps it) is reaped regardless of age.

    A genuinely fresh `running` row (started within the window) is left alone so
    a very recent restart during an in-flight run doesn't kill live work.
    Returns rows reconciled. Called once at startup, before any request served.
    """
    now = _now()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    cur = conn.execute(
        "UPDATE runs SET status = 'aborted', ended_at = ? "
        "WHERE status = 'running' AND (started_at = '' OR started_at < ?)",
        (now, cutoff),
    )
    return int(cur.rowcount)


def reconcile_stale_review_tasks(conn: sqlite3.Connection) -> int:
    """Retire re-review passes orphaned by a process restart (Phase 5.3).

    A pass runs on a daemon thread that dies with the process; a row left
    `status='running'` after a restart can never complete, so a poll would
    hang forever and a relaunch would be blocked by the re-entrancy guard.
    Flip every such row to a terminal `done` carrying an honest error so the
    poll resolves and the user can relaunch. Returns rows reconciled.
    Called once at startup, before any request is served.
    """
    now = _now()
    outcome_json = json.dumps({
        "ok": False,
        "invoked": False,
        "error": "Server restarted while the re-review was running. "
                 "Relaunch it to retry.",
    })
    cur = conn.execute(
        "UPDATE run_review_tasks SET status = 'done', outcome_json = ?, "
        "updated_at = ? WHERE status = 'running'",
        (outcome_json, now),
    )
    return int(cur.rowcount)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _row_to_run(row: sqlite3.Row) -> Run:
    """Hydrate a full `runs` row into the Run dataclass including v2 fields.

    Used by fetch_run and by repository.list_runs detail queries. Keeping
    this in one place means the History code never has to know which
    column names are optional vs required.
    """
    # Some callers use non-Row connections; fall back to key lookups that
    # work for both sqlite3.Row and tuple/dict shapes.
    def _get(name: str, default=None):
        try:
            value = row[name]
        except (IndexError, KeyError):
            return default
        return value if value is not None else default

    raw_config = _get("run_config_json")
    try:
        config = json.loads(raw_config) if raw_config else None
    except (TypeError, json.JSONDecodeError):
        # A corrupt blob should not crash the History page — degrade to None.
        config = None

    return Run(
        id=row["id"],
        created_at=row["created_at"],
        pdf_filename=row["pdf_filename"],
        status=row["status"],
        notes=_get("notes"),
        session_id=_get("session_id", "") or "",
        output_dir=_get("output_dir", "") or "",
        merged_workbook_path=_get("merged_workbook_path"),
        config=config,
        scout_enabled=bool(_get("scout_enabled", 0)),
        started_at=_get("started_at", "") or "",
        ended_at=_get("ended_at"),
        orchestration=_get("orchestration", "split") or "split",
        benchmark_id=_get("benchmark_id"),
        notes_table_style=_parse_notes_table_style(_get("notes_table_style")),
        app_version=_get("app_version"),
        repeat_group_id=_get("repeat_group_id"),
        repeat_index=_get("repeat_index"),
        suite_run_id=_get("suite_run_id"),
    )


def fetch_run(conn: sqlite3.Connection, run_id: int) -> Optional[Run]:
    # fetch_run is called from contexts that did not set row_factory (e.g.
    # server.py's raw sqlite3.connect). Force sqlite3.Row for this query so
    # _row_to_run's keyword lookups always work regardless of caller setup.
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    return _row_to_run(row)


def fetch_run_agents(conn: sqlite3.Connection, run_id: int) -> list[RunAgent]:
    rows = conn.execute(
        "SELECT * FROM run_agents WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [
        RunAgent(
            id=r["id"], run_id=r["run_id"], statement_type=r["statement_type"],
            variant=r["variant"], model=r["model"], status=r["status"],
            started_at=r["started_at"], ended_at=r["ended_at"],
            workbook_path=r["workbook_path"], total_tokens=r["total_tokens"] or 0,
            total_cost=r["total_cost"] or 0.0,
            # v8 rollups — `_get`-style guard so a pre-v8 row (column absent on
            # a connection that predates migration) still hydrates as 0.
            prompt_tokens=_row_get(r, "prompt_tokens", 0),
            completion_tokens=_row_get(r, "completion_tokens", 0),
            turn_count=_row_get(r, "turn_count", 0),
            tool_call_count=_row_get(r, "tool_call_count", 0),
            # v15 cache rollups — _row_get guard so a pre-v15 row hydrates as 0.
            cache_read_tokens=_row_get(r, "cache_read_tokens", 0),
            cache_write_tokens=_row_get(r, "cache_write_tokens", 0),
            # v17 (item 9) — pre-v17 rows hydrate as None.
            error_type=_row_get(r, "error_type", None),
        )
        for r in rows
    ]


def _row_get(row: sqlite3.Row, name: str, default=0):
    """Read a column that may be absent on very old rows / connections.

    run_agents gained the v8 rollup columns via ALTER; a row read before
    migration (or a non-Row cursor) can lack them. Degrade to `default`
    rather than raising so the History page never 500s on a legacy run."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return value if value is not None else default


def _parse_json_dict(raw: Any) -> Optional[dict[str, Any]]:
    """Hydrate a JSON blob to a dict, or None if empty/corrupt (v30 eval
    taxonomy + per-statement columns). A bad blob degrades to None rather than
    crashing a scorecard read."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def fetch_agent_turns(conn: sqlite3.Connection, run_agent_id: int) -> list[dict]:
    """Per-turn telemetry rows for one agent, ordered by turn index (v8)."""
    rows = conn.execute(
        "SELECT turn_index, node_kind, tool_names, prompt_tokens, "
        "completion_tokens, total_tokens, cumulative_tokens, cost_estimate, "
        "duration_ms, cache_read_tokens, cache_write_tokens, ts "
        "FROM run_agent_turns WHERE run_agent_id = ? "
        "ORDER BY turn_index",
        (run_agent_id,),
    ).fetchall()
    return [
        {
            "turn_index": r["turn_index"],
            "node_kind": r["node_kind"],
            "tool_names": r["tool_names"],
            "prompt_tokens": r["prompt_tokens"] or 0,
            "completion_tokens": r["completion_tokens"] or 0,
            "total_tokens": r["total_tokens"] or 0,
            "cumulative_tokens": r["cumulative_tokens"] or 0,
            "cost_estimate": r["cost_estimate"] or 0.0,
            "duration_ms": r["duration_ms"] or 0,
            "cache_read_tokens": _row_get(r, "cache_read_tokens", 0),
            "cache_write_tokens": _row_get(r, "cache_write_tokens", 0),
        }
        for r in rows
    ]


def fetch_events(conn: sqlite3.Connection, run_agent_id: int) -> list[AgentEvent]:
    rows = conn.execute(
        "SELECT * FROM agent_events WHERE run_agent_id = ? ORDER BY id", (run_agent_id,)
    ).fetchall()
    return [
        AgentEvent(
            id=r["id"], run_agent_id=r["run_agent_id"], ts=r["ts"],
            event_type=r["event_type"], phase=r["phase"],
            payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
        )
        for r in rows
    ]


def fetch_fields(conn: sqlite3.Connection, run_id: int) -> list[ExtractedField]:
    """All extracted fields across every agent in the run."""
    rows = conn.execute(
        "SELECT f.* FROM extracted_fields f "
        "JOIN run_agents a ON a.id = f.run_agent_id "
        "WHERE a.run_id = ? ORDER BY f.id",
        (run_id,),
    ).fetchall()
    return [
        ExtractedField(
            id=r["id"], run_agent_id=r["run_agent_id"], sheet=r["sheet"],
            field_label=r["field_label"], col=r["col"], value=r["value"],
            section=r["section"], row_num=r["row_num"], evidence=r["evidence"],
        )
        for r in rows
    ]


def fetch_cross_checks(conn: sqlite3.Connection, run_id: int) -> list[CrossCheck]:
    rows = conn.execute(
        "SELECT * FROM cross_checks WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [
        CrossCheck(
            id=r["id"], run_id=r["run_id"], check_name=r["check_name"],
            status=r["status"], expected=r["expected"], actual=r["actual"],
            diff=r["diff"], tolerance=r["tolerance"], message=r["message"],
            target_sheet=(r["target_sheet"] if "target_sheet" in r.keys() else None),
            target_row=(r["target_row"] if "target_row" in r.keys() else None),
            comparands_json=(
                r["comparands_json"] if "comparands_json" in r.keys() else None
            ),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# History list / detail / delete (Phase 2)
# ---------------------------------------------------------------------------

def _parse_iso_duration(start: str, end: str) -> Optional[float]:
    """Return seconds between two ISO 8601 timestamps, or None if unparseable.

    Used by list_runs to fill RunSummary.duration_seconds. Degrading to None
    on malformed input keeps the History page usable even if a legacy row
    has an odd started_at/ended_at format.
    """
    if not start or not end:
        return None
    try:
        # Python 3.9's datetime.fromisoformat cannot parse a trailing 'Z'
        # timezone marker, so swap it for '+00:00' first.
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (e - s).total_seconds()
    except ValueError:
        return None


def _escape_like(value: str) -> str:
    """Escape SQL LIKE metachars so users searching for literal `_` or `%`
    in a filename get exact matches instead of wildcard behaviour
    (peer-review I9). Pair the returned pattern with `ESCAPE '\\'` in SQL.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_runs(
    conn: sqlite3.Connection,
    *,
    filename_substring: Optional[str] = None,
    status: Optional[str] = None,
    model: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_suite_children: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[RunSummary]:
    """Return RunSummary rows for the History list view.

    Ordering: created_at DESC (newest first). Filters are ANDed together.

    `model` filter matches against run_agents.model — the effective resolved
    model per agent. This is the ONLY authoritative source for per-run model
    attribution; runs.run_config_json only holds per-statement overrides
    from the request body.
    """
    # Build the WHERE clause dynamically from optional filters. We use
    # string concatenation with parameterised placeholders, never raw
    # values, so this remains injection-safe.
    clauses: list[str] = []
    params: list[Any] = []

    if filename_substring:
        clauses.append("LOWER(r.pdf_filename) LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(filename_substring.lower())}%")
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    # Evals workspace (E6): suite child runs are hidden from History by default
    # (decision #1) so a 30-doc suite run doesn't bury the list. The toggle
    # passes include_suite_children=True to show them.
    if not include_suite_children:
        clauses.append("r.suite_run_id IS NULL")
    # Normalize date-only filters to full ISO timestamps so the lexicographic
    # comparison against `created_at` covers the full day on both ends.
    date_from_norm = _normalize_date_bound(date_from, end_of_day=False)
    date_to_norm = _normalize_date_bound(date_to, end_of_day=True)
    if date_from_norm:
        clauses.append("r.created_at >= ?")
        params.append(date_from_norm)
    if date_to_norm:
        clauses.append("r.created_at <= ?")
        params.append(date_to_norm)
    if model:
        # Match if ANY agent on this run has the given effective model.
        clauses.append(
            "EXISTS (SELECT 1 FROM run_agents ra "
            "WHERE ra.run_id = r.id AND ra.model = ? "
            "AND ra.statement_type != 'SCOUT')"
        )
        params.append(model)

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT r.* FROM runs r"
        + where_sql
        + " ORDER BY r.created_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([int(limit), int(offset)])

    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
        if not rows:
            return []

        # Peer-review fix: batch-load run_agents for the whole page in ONE
        # query instead of per-run. Previously this was an N+1: up to
        # (limit) extra queries on every list call.
        run_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" for _ in run_ids)
        agents_sql = (
            "SELECT run_id, statement_type, model FROM run_agents "
            f"WHERE run_id IN ({placeholders}) AND statement_type != 'SCOUT'"
        )
        agent_rows = conn.execute(agents_sql, tuple(run_ids)).fetchall()

        # Fold the agent rows into per-run sets of (statement, model).
        statements_by_run: dict[int, set[str]] = {rid: set() for rid in run_ids}
        models_by_run: dict[int, set[str]] = {rid: set() for rid in run_ids}
        for ar in agent_rows:
            rid = ar["run_id"]
            statements_by_run[rid].add(ar["statement_type"])
            if ar["model"]:
                models_by_run[rid].add(ar["model"])

        # v16: batch-load eval scores for the whole page in ONE query (avoid an
        # N+1). A run has at most one score; compute matched/gold_cells here so
        # the History column + sparkline need no further math.
        score_by_run: dict[int, float] = {}
        score_sql = (
            "SELECT run_id, gold_cells, matched_cells FROM eval_scores "
            f"WHERE run_id IN ({placeholders})"
        )
        for sr in conn.execute(score_sql, tuple(run_ids)).fetchall():
            gold = int(sr["gold_cells"])
            if gold > 0:
                score_by_run[sr["run_id"]] = int(sr["matched_cells"]) / gold

        summaries: list[RunSummary] = []
        for r in rows:
            started = r["started_at"] if "started_at" in r.keys() else ""
            ended = r["ended_at"] if "ended_at" in r.keys() else None
            run = _row_to_run(r)
            summaries.append(
                RunSummary(
                    id=run.id,
                    created_at=run.created_at,
                    pdf_filename=run.pdf_filename,
                    status=run.status,
                    session_id=run.session_id,
                    statements_run=sorted(statements_by_run.get(run.id, set())),
                    models_used=sorted(models_by_run.get(run.id, set())),
                    duration_seconds=_parse_iso_duration(started or "", ended or ""),
                    scout_enabled=run.scout_enabled,
                    merged_workbook_path=run.merged_workbook_path,
                    filing_level=(run.config or {}).get("filing_level", "company"),
                    filing_standard=(run.config or {}).get("filing_standard", "mfrs"),
                    denomination=(run.config or {}).get("denomination", "thousands"),
                    # Source orchestration from the canonical `runs.orchestration`
                    # column (via Run.orchestration), NOT from the config JSON.
                    # A row with a corrupt or pre-v10 config still surfaces the
                    # right path via the dedicated column.
                    orchestration=run.orchestration,
                    benchmark_id=run.benchmark_id,
                    eval_score=score_by_run.get(run.id),
                    app_version=run.app_version,
                )
            )
        return summaries
    finally:
        conn.row_factory = prior_factory


def count_runs(
    conn: sqlite3.Connection,
    *,
    filename_substring: Optional[str] = None,
    status: Optional[str] = None,
    model: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_suite_children: bool = False,
) -> int:
    """Companion to list_runs — returns the total matching count for the UI
    pagination footer. Keeps the SQL filter logic in one shape by mirroring
    list_runs's WHERE-clause construction."""
    clauses: list[str] = []
    params: list[Any] = []
    if filename_substring:
        clauses.append("LOWER(r.pdf_filename) LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(filename_substring.lower())}%")
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    if not include_suite_children:
        clauses.append("r.suite_run_id IS NULL")
    # Mirror list_runs date normalization so the pagination footer count
    # matches the visible row set.
    date_from_norm = _normalize_date_bound(date_from, end_of_day=False)
    date_to_norm = _normalize_date_bound(date_to, end_of_day=True)
    if date_from_norm:
        clauses.append("r.created_at >= ?")
        params.append(date_from_norm)
    if date_to_norm:
        clauses.append("r.created_at <= ?")
        params.append(date_to_norm)
    if model:
        clauses.append(
            "EXISTS (SELECT 1 FROM run_agents ra "
            "WHERE ra.run_id = r.id AND ra.model = ? "
            "AND ra.statement_type != 'SCOUT')"
        )
        params.append(model)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = "SELECT COUNT(*) FROM runs r" + where_sql
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def get_run_detail(conn: sqlite3.Connection, run_id: int) -> Optional[RunDetail]:
    """Return a hydrated RunDetail for the History drawer / page.

    Returns None if the run doesn't exist. The caller (HTTP handler)
    translates None into a 404 so the UI can show a "gone" state.

    Phase 7: also hydrates each RunAgent with its persisted agent_events so
    the History detail page can replay the tool timeline via the same
    buildToolTimeline() reducer the live view uses.
    """
    run = fetch_run(conn, run_id)
    if run is None:
        return None
    agents = fetch_run_agents(conn, run_id)

    # Batch-fetch all events for this run's agents in one SQL round-trip
    # (peer-review I7). The previous per-agent `fetch_events` loop was O(n)
    # trips; for Group filings with 5+ agents and event-cap-sized payloads
    # this added noticeable latency to the History detail page.
    if agents:
        agent_ids = [a.id for a in agents]
        placeholders = ",".join("?" * len(agent_ids))
        rows = conn.execute(
            f"SELECT * FROM agent_events WHERE run_agent_id IN ({placeholders}) "
            f"ORDER BY run_agent_id, id",
            agent_ids,
        ).fetchall()
        events_by_agent: dict[int, list[AgentEvent]] = {aid: [] for aid in agent_ids}
        for r in rows:
            events_by_agent[r["run_agent_id"]].append(
                AgentEvent(
                    id=r["id"], run_agent_id=r["run_agent_id"], ts=r["ts"],
                    event_type=r["event_type"], phase=r["phase"],
                    payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
                )
            )
        for agent in agents:
            agent.events = events_by_agent.get(agent.id, [])

        # v8: batch-hydrate per-turn telemetry the same way as events, so the
        # Telemetry tab can render the per-turn table without an N+1 fetch.
        turn_rows = conn.execute(
            f"SELECT * FROM run_agent_turns WHERE run_agent_id IN ({placeholders}) "
            f"ORDER BY run_agent_id, turn_index",
            agent_ids,
        ).fetchall()
        turns_by_agent: dict[int, list[dict]] = {aid: [] for aid in agent_ids}
        for r in turn_rows:
            turns_by_agent[r["run_agent_id"]].append({
                "turn_index": r["turn_index"],
                "node_kind": r["node_kind"],
                "tool_names": r["tool_names"],
                "prompt_tokens": r["prompt_tokens"] or 0,
                "completion_tokens": r["completion_tokens"] or 0,
                "total_tokens": r["total_tokens"] or 0,
                "cumulative_tokens": r["cumulative_tokens"] or 0,
                "cost_estimate": r["cost_estimate"] or 0.0,
                "duration_ms": r["duration_ms"] or 0,
            })
        for agent in agents:
            agent.turns = turns_by_agent.get(agent.id, [])

    checks = fetch_cross_checks(conn, run_id)
    return RunDetail(run=run, agents=agents, cross_checks=checks)


def delete_run(conn: sqlite3.Connection, run_id: int) -> bool:
    """Hard-delete a run and everything that hangs off it (run_agents,
    agent_events, extracted_fields, cross_checks) via ON DELETE CASCADE.

    Returns True if a row was removed, False if the id was unknown. By
    design this does NOT touch on-disk output directories — disk cleanup
    is explicitly out of scope for the current phase (see plan Key
    Decisions and tests/test_history_api.py).
    """
    # FK cascade is only enforced when PRAGMA foreign_keys is ON. db_session
    # already sets it, but other callers (server.py's raw connection) also
    # set it — we double up here to make delete_run safe regardless of how
    # the connection was opened.
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    return cur.rowcount > 0


def delete_draft_runs(
    conn: sqlite3.Connection,
    protected_session_ids: "Iterable[str]" = (),
) -> int:
    """Bulk-delete every abandoned draft (``status = 'draft'``), returning the
    count removed. Draft-only by construction — the WHERE clause can never match
    a started/terminal run, so this cannot delete real work. A draft whose
    session is mid-start (its id in ``protected_session_ids``) is skipped so the
    cleanup can't race a run that's just been launched. Same cascade + no
    on-disk deletion as ``delete_run``.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    protected = tuple(s for s in protected_session_ids if s)
    if protected:
        placeholders = ",".join("?" * len(protected))
        cur = conn.execute(
            "DELETE FROM runs WHERE status = 'draft' "
            f"AND (session_id IS NULL OR session_id NOT IN ({placeholders}))",
            protected,
        )
    else:
        cur = conn.execute("DELETE FROM runs WHERE status = 'draft'")
    return cur.rowcount


# ---------------------------------------------------------------------------
# Repeat groups (v30) — consistency scoring across N runs of one document.
# ---------------------------------------------------------------------------

def create_repeat_group(
    conn: sqlite3.Connection,
    *,
    config: Optional[dict[str, Any]] = None,
    repeats_requested: int = 1,
    benchmark_id: Optional[int] = None,
) -> int:
    """Create a repeat group and return its id. The N child runs link to it via
    create_run(repeat_group_id=..., repeat_index=...)."""
    cur = conn.execute(
        "INSERT INTO repeat_groups(created_at, config_json, repeats_requested, "
        "benchmark_id, status) VALUES (?, ?, ?, ?, 'running')",
        (_now(), json.dumps(config) if config is not None else None,
         int(repeats_requested), benchmark_id),
    )
    return int(cur.lastrowid)


def list_repeat_group_run_ids(
    conn: sqlite3.Connection, group_id: int, *, statuses: Optional[list[str]] = None
) -> list[int]:
    """Run ids in a repeat group, ordered by repeat_index. Optionally filtered to
    a set of statuses (e.g. the finished ones for consistency)."""
    sql = "SELECT id FROM runs WHERE repeat_group_id = ?"
    params: list[Any] = [group_id]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY repeat_index"
    return [r[0] for r in conn.execute(sql, tuple(params)).fetchall()]


def save_repeat_group_consistency(
    conn: sqlite3.Connection,
    group_id: int,
    consistency: Optional[dict[str, Any]],
    status: str,
) -> None:
    """Persist the computed consistency result + terminal status on the group."""
    conn.execute(
        "UPDATE repeat_groups SET consistency_json = ?, status = ? WHERE id = ?",
        (json.dumps(consistency) if consistency is not None else None,
         status, group_id),
    )


def fetch_repeat_group(
    conn: sqlite3.Connection, group_id: int
) -> Optional[dict[str, Any]]:
    """The group row + its child run ids/statuses, or None."""
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, created_at, repeats_requested, benchmark_id, status, "
            "config_json, consistency_json FROM repeat_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        # Each child carries its own accuracy (PRD: "each repeat's accuracy
        # score, so the user sees both 'how right' and 'how stable'") — the
        # LEFT JOIN reads the stamped eval_scores row when the repeat was
        # graded, NULL otherwise.
        children = conn.execute(
            "SELECT r.id, r.status, r.repeat_index, "
            "s.gold_cells, s.matched_cells "
            "FROM runs r LEFT JOIN eval_scores s ON s.run_id = r.id "
            "WHERE r.repeat_group_id = ? ORDER BY r.repeat_index",
            (group_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior_factory
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "repeats_requested": row["repeats_requested"],
        "benchmark_id": row["benchmark_id"],
        "status": row["status"],
        "config": _parse_json_dict(row["config_json"]),
        "consistency": _parse_json_dict(row["consistency_json"]),
        "runs": [
            {
                "id": c["id"],
                "status": c["status"],
                "repeat_index": c["repeat_index"],
                "accuracy": (
                    (c["matched_cells"] / c["gold_cells"])
                    if c["gold_cells"] else None
                ),
            }
            for c in children
        ],
    }


# ---------------------------------------------------------------------------
# Evals workspace Phase 2 (v31) — suites + suite runs + suite docs.
# A Suite is a named corpus; a Suite Run is one batch execution over it. Child
# runs link back via runs.suite_run_id. Managed source files live on disk
# (hybrid storage); only the pointers live here.
# ---------------------------------------------------------------------------
def create_suite(conn: sqlite3.Connection, *, name: str) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO eval_suites(name, created_at, updated_at) VALUES (?, ?, ?)",
        (name, now, now),
    )
    return int(cur.lastrowid)


def rename_suite(conn: sqlite3.Connection, suite_id: int, name: str) -> None:
    conn.execute(
        "UPDATE eval_suites SET name = ?, updated_at = ? WHERE id = ?",
        (name, _now(), suite_id),
    )


def delete_suite(conn: sqlite3.Connection, suite_id: int) -> bool:
    cur = conn.execute("DELETE FROM eval_suites WHERE id = ?", (suite_id,))
    return cur.rowcount > 0


def list_suites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every suite with its document + suite-run counts, newest first."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.created_at, s.updated_at, "
            "(SELECT COUNT(*) FROM eval_suite_docs d WHERE d.suite_id = s.id) AS doc_count, "
            "(SELECT COUNT(*) FROM eval_suite_runs r WHERE r.suite_id = s.id) AS run_count "
            "FROM eval_suites s ORDER BY s.id DESC"
        ).fetchall()
    finally:
        conn.row_factory = prior
    return [dict(r) for r in rows]


def get_suite(conn: sqlite3.Connection, suite_id: int) -> Optional[dict[str, Any]]:
    """A suite + its documents, or None."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, name, created_at, updated_at FROM eval_suites WHERE id = ?",
            (suite_id,),
        ).fetchone()
        if row is None:
            return None
        docs = conn.execute(
            "SELECT id, label, source_filename, filing_standard, filing_level, "
            "benchmark_id, denomination, created_at FROM eval_suite_docs "
            "WHERE suite_id = ? ORDER BY id",
            (suite_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out = dict(row)
    out["docs"] = [dict(d) for d in docs]
    return out


def add_suite_doc(
    conn: sqlite3.Connection,
    *,
    suite_id: int,
    label: str,
    source_path: str,
    source_filename: str,
    filing_standard: str = "mfrs",
    filing_level: str = "company",
    benchmark_id: Optional[int] = None,
    denomination: str = "thousands",
) -> int:
    cur = conn.execute(
        "INSERT INTO eval_suite_docs(suite_id, label, source_path, "
        "source_filename, filing_standard, filing_level, benchmark_id, "
        "denomination, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (suite_id, label, source_path, source_filename, filing_standard,
         filing_level, benchmark_id, denomination, _now()),
    )
    return int(cur.lastrowid)


def delete_suite_doc(conn: sqlite3.Connection, doc_id: int) -> bool:
    cur = conn.execute("DELETE FROM eval_suite_docs WHERE id = ?", (doc_id,))
    return cur.rowcount > 0


def list_suite_docs(conn: sqlite3.Connection, suite_id: int) -> list[dict[str, Any]]:
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, suite_id, label, source_path, source_filename, "
            "filing_standard, filing_level, benchmark_id, denomination, "
            "created_at FROM eval_suite_docs WHERE suite_id = ? ORDER BY id",
            (suite_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    return [dict(r) for r in rows]


def create_suite_run(
    conn: sqlite3.Connection,
    *,
    suite_id: int,
    label: str = "",
    config: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    app_version: Optional[str] = None,
) -> int:
    if app_version is None:
        from utils.app_version import get_app_version
        app_version = get_app_version()
    cur = conn.execute(
        "INSERT INTO eval_suite_runs(suite_id, label, config_json, model, "
        "app_version, status, created_at) VALUES (?, ?, ?, ?, ?, 'running', ?)",
        (suite_id, label, json.dumps(config) if config is not None else None,
         model, app_version, _now()),
    )
    return int(cur.lastrowid)


def update_suite_run_status(
    conn: sqlite3.Connection, suite_run_id: int, status: str,
    *, ended: bool = False,
) -> None:
    if ended:
        conn.execute(
            "UPDATE eval_suite_runs SET status = ?, ended_at = ? WHERE id = ?",
            (status, _now(), suite_run_id),
        )
    else:
        conn.execute(
            "UPDATE eval_suite_runs SET status = ? WHERE id = ?",
            (status, suite_run_id),
        )


def get_suite_run(
    conn: sqlite3.Connection, suite_run_id: int
) -> Optional[dict[str, Any]]:
    """A suite run + its child run ids/statuses (ordered), or None."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, suite_id, label, config_json, model, app_version, "
            "status, created_at, ended_at FROM eval_suite_runs WHERE id = ?",
            (suite_run_id,),
        ).fetchone()
        if row is None:
            return None
        children = conn.execute(
            "SELECT id, status, pdf_filename, benchmark_id FROM runs "
            "WHERE suite_run_id = ? ORDER BY id",
            (suite_run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out = dict(row)
    out["config"] = _parse_json_dict(out.pop("config_json"))
    out["runs"] = [dict(c) for c in children]
    return out


def list_suite_runs(
    conn: sqlite3.Connection, suite_id: int
) -> list[dict[str, Any]]:
    """Suite runs for a suite, newest first (no child expansion)."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, suite_id, label, model, app_version, status, "
            "created_at, ended_at FROM eval_suite_runs WHERE suite_id = ? "
            "ORDER BY id DESC",
            (suite_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    return [dict(r) for r in rows]


def snapshot_suite_run_docs(
    conn: sqlite3.Connection, suite_run_id: int, docs: list[dict[str, Any]],
) -> None:
    """Freeze the suite's document list onto this suite run (v32, PLAN-evals-
    hardening Step 2). Written at launch BEFORE any execution; the runner only
    ever reads this snapshot, so later suite edits can't change a run's corpus.
    Each doc dict is a live eval_suite_docs row, optionally enriched with
    ``source_sha256`` and resolved per-doc ``variants``."""
    now = _now()
    for d in docs:
        variants = d.get("variants")
        conn.execute(
            "INSERT OR IGNORE INTO eval_suite_run_docs("
            "suite_run_id, suite_doc_id, label, source_path, source_filename, "
            "source_sha256, filing_standard, filing_level, benchmark_id, "
            "denomination, variants_json, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (
                suite_run_id, d["id"], d.get("label", ""),
                d.get("source_path", ""), d.get("source_filename", ""),
                d.get("source_sha256", ""),
                d.get("filing_standard", "mfrs"), d.get("filing_level", "company"),
                d.get("benchmark_id"),
                d.get("denomination", "thousands"),
                json.dumps(variants) if variants else None,
                now, now,
            ),
        )


def list_suite_run_docs(
    conn: sqlite3.Connection, suite_run_id: int
) -> list[dict[str, Any]]:
    """The frozen document list of one suite run. ``id`` is aliased to the
    ORIGINAL suite_doc_id so runner code (session ids, resume matching) treats
    snapshot rows exactly like live doc rows."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT suite_doc_id AS id, suite_run_id, label, source_path, "
            "source_filename, source_sha256, filing_standard, filing_level, "
            "benchmark_id, denomination, variants_json, state, error, "
            "created_at, updated_at "
            "FROM eval_suite_run_docs WHERE suite_run_id = ? ORDER BY suite_doc_id",
            (suite_run_id,),
        ).fetchall()
    finally:
        conn.row_factory = prior
    out = []
    for r in rows:
        d = dict(r)
        d["variants"] = _parse_json_dict(d.pop("variants_json"))
        out.append(d)
    return out


def update_suite_run_doc_state(
    conn: sqlite3.Connection, suite_run_id: int, suite_doc_id: int,
    state: str, *, error: Optional[str] = None,
) -> None:
    conn.execute(
        "UPDATE eval_suite_run_docs SET state = ?, error = ?, updated_at = ? "
        "WHERE suite_run_id = ? AND suite_doc_id = ?",
        (state, error, _now(), suite_run_id, suite_doc_id),
    )


def reconcile_stale_suite_runs(conn: sqlite3.Connection) -> int:
    """Retire suite runs left 'running' by a crash (mirrors
    reconcile_stale_review_tasks). Called at startup. Returns the count."""
    cur = conn.execute(
        "UPDATE eval_suite_runs SET status = 'partial', ended_at = ? "
        "WHERE status = 'running'",
        (_now(),),
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Gold-standard eval / benchmark (v16) — scorecard persistence.
# Benchmark + gold-fact CRUD lives in eval/store.py (the eval subsystem owns
# its own writes); only the per-(run, benchmark) scorecard is persisted here,
# alongside the other run-scoped tables.
# ---------------------------------------------------------------------------

def save_eval_score(
    conn: sqlite3.Connection,
    run_id: int,
    benchmark_id: int,
    card: Any,
) -> None:
    """Upsert the scorecard for a ``(run, benchmark)`` pair.

    ``card`` is duck-typed (an ``eval.grader.ScoreCard``): we read its count
    attributes without importing the eval package here, keeping the repo
    layer free of subsystem dependencies. ``UNIQUE(run_id, benchmark_id)``
    makes a re-grade overwrite the prior row.
    """
    now = _now()
    # v30: taxonomy + per-statement breakdown persisted as JSON. Duck-typed:
    # a card built by an older path (no taxonomy attr) serialises as NULL.
    taxonomy = getattr(card, "taxonomy", None) or None
    per_statement = getattr(card, "per_statement", None) or None
    taxonomy_json = json.dumps(taxonomy) if taxonomy else None
    per_statement_json = json.dumps(per_statement) if per_statement else None
    # v33: stamp the gold content hash at grade time so any later gold change
    # (edit / deletion / reassignment) is detectable against this score.
    # Best-effort: a fingerprint failure must never block persisting the score.
    try:
        from eval.store import gold_fingerprint
        fingerprint: Optional[str] = gold_fingerprint(conn, benchmark_id)
    except Exception:
        fingerprint = None
    conn.execute(
        "INSERT INTO eval_scores(run_id, benchmark_id, gold_cells, "
        "matched_cells, missing_cells, mismatch_cells, extra_cells, "
        "scale_mismatch, created_at, taxonomy_json, per_statement_json, "
        "gold_fingerprint) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, benchmark_id) DO UPDATE SET "
        "gold_cells = excluded.gold_cells, "
        "matched_cells = excluded.matched_cells, "
        "missing_cells = excluded.missing_cells, "
        "mismatch_cells = excluded.mismatch_cells, "
        "extra_cells = excluded.extra_cells, "
        "scale_mismatch = excluded.scale_mismatch, "
        "created_at = excluded.created_at, "
        "taxonomy_json = excluded.taxonomy_json, "
        "per_statement_json = excluded.per_statement_json, "
        "gold_fingerprint = excluded.gold_fingerprint",
        (
            run_id, benchmark_id,
            int(card.gold_cells), int(card.matched), int(card.missing),
            int(card.mismatch), int(card.extra), int(card.scale_mismatch),
            now, taxonomy_json, per_statement_json, fingerprint,
        ),
    )


def fetch_eval_score(
    conn: sqlite3.Connection, run_id: int, benchmark_id: int
) -> Optional[dict[str, Any]]:
    """Return the scorecard dict for a ``(run, benchmark)`` pair, or ``None``.

    The dict carries the raw counts plus a derived ``score`` (matched /
    gold_cells, 0.0 when there are no gold cells) so the UI doesn't re-derive
    it. ``benchmark_id`` is required because a run could in principle be graded
    against more than one benchmark over its lifetime, though the MVP attaches
    exactly one.
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT gold_cells, matched_cells, missing_cells, mismatch_cells, "
            "extra_cells, scale_mismatch, created_at, taxonomy_json, "
            "per_statement_json, gold_fingerprint "
            "FROM eval_scores WHERE run_id = ? AND benchmark_id = ?",
            (run_id, benchmark_id),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    gold_cells = int(row["gold_cells"])
    matched = int(row["matched_cells"])
    return {
        "benchmark_id": benchmark_id,
        "gold_cells": gold_cells,
        "matched_cells": matched,
        "missing_cells": int(row["missing_cells"]),
        "mismatch_cells": int(row["mismatch_cells"]),
        "extra_cells": int(row["extra_cells"]),
        "scale_mismatch": int(row["scale_mismatch"]),
        "score": (matched / gold_cells) if gold_cells > 0 else 0.0,
        "created_at": row["created_at"],
        # v30 — NULL on legacy scorecards; the UI degrades gracefully.
        "taxonomy": _parse_json_dict(_row_get(row, "taxonomy_json")),
        "per_statement": _parse_json_dict(_row_get(row, "per_statement_json")),
        # v33 — the gold content hash this score was graded against (NULL on
        # legacy rows: "unknown gold version", never a false "unchanged").
        "gold_fingerprint": _row_get(row, "gold_fingerprint"),
    }


def fetch_eval_score_for_run(
    conn: sqlite3.Connection, run_id: int
) -> Optional[dict[str, Any]]:
    """Return the scorecard for a run regardless of which benchmark it used.

    Convenience for the History list + run page, where the run row already
    carries its single ``benchmark_id`` and we just want "the score for this
    run". Returns the most recent row if (unexpectedly) more than one exists.
    """
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT benchmark_id FROM eval_scores WHERE run_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    return fetch_eval_score(conn, run_id, int(row["benchmark_id"]))


# ---------------------------------------------------------------------------
# Auth (v18) — accounts + server-side sessions
# ---------------------------------------------------------------------------
# These helpers are the ONLY place that touches the auth_users / auth_sessions
# tables. The password hash is opaque here — hashing/verification lives in
# auth/passwords.py; this module just stores and reads the hash string.

@dataclass
class AuthUser:
    email: str
    display_name: str = ""
    password_hash: Optional[str] = None
    disabled: bool = False
    created_at: str = ""
    password_set_at: Optional[str] = None
    # is_admin (schema v20) gates web user-management. Default False so any
    # caller constructing an AuthUser by hand (and any pre-v20 row) is treated
    # as an ordinary user until explicitly promoted.
    is_admin: bool = False


@dataclass
class AuthSession:
    session_id: str
    email: str
    display_name: str = ""
    provider: str = "password"
    created_at: str = ""
    last_seen_at: str = ""


def _normalize_email(email: str) -> str:
    """Lower-case + strip so lookups are case-insensitive (the PK is lowercased).

    A small fixed team typing "You@Firm.com" must match the seeded
    "you@firm.com" row, so every read and write funnels through this.
    """
    return (email or "").strip().lower()


def _row_to_auth_user(row: sqlite3.Row) -> AuthUser:
    """Map an auth_users row to an AuthUser. Single place so fetch + list stay
    in sync as columns are added. `is_admin` is read defensively (guard on the
    column being present) so a read against a not-yet-migrated DB degrades to a
    non-admin user rather than raising."""
    has_admin = "is_admin" in row.keys()
    return AuthUser(
        email=row["email"],
        display_name=row["display_name"] or "",
        password_hash=row["password_hash"],
        disabled=bool(row["disabled"]),
        created_at=row["created_at"] or "",
        password_set_at=row["password_set_at"],
        is_admin=bool(row["is_admin"]) if has_admin else False,
    )


def upsert_auth_user(
    conn: sqlite3.Connection,
    email: str,
    display_name: str,
    password_hash: Optional[str],
) -> None:
    """Create or update an account. Used by the provisioning CLI.

    On a re-run for an existing email it refreshes the display name + hash
    (the "set-password" / rotate path) and stamps password_set_at whenever a
    hash is supplied, leaving `disabled` untouched.
    """
    norm = _normalize_email(email)
    now = _now()
    conn.execute(
        """
        INSERT INTO auth_users(email, display_name, password_hash, disabled,
                               created_at, password_set_at)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            display_name    = excluded.display_name,
            password_hash   = excluded.password_hash,
            password_set_at = excluded.password_set_at
        """,
        (norm, display_name or "", password_hash, now,
         now if password_hash is not None else None),
    )


def set_auth_user_disabled(
    conn: sqlite3.Connection, email: str, disabled: bool
) -> bool:
    """Block (or re-enable) an account without deleting it (keeps the audit
    trail). Returns False if no such account exists.

    Disabling also REVOKES the account's live sessions immediately — a flipped
    `disabled` flag must lock the user out now, not whenever their session
    happens to idle out. (resolve_session also fails closed on a disabled user
    as defence in depth, but deleting the rows is the clean primary revocation.)
    """
    norm = _normalize_email(email)
    cur = conn.execute(
        "UPDATE auth_users SET disabled = ? WHERE email = ?",
        (1 if disabled else 0, norm),
    )
    if disabled and cur.rowcount > 0:
        conn.execute("DELETE FROM auth_sessions WHERE email = ?", (norm,))
    return cur.rowcount > 0


def fetch_auth_user(
    conn: sqlite3.Connection, email: str
) -> Optional[AuthUser]:
    """Look up one account by (case-folded) email, or None."""
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM auth_users WHERE email = ?",
            (_normalize_email(email),),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    return _row_to_auth_user(row)


def list_auth_users(conn: sqlite3.Connection) -> list[AuthUser]:
    """All accounts, ordered by email. Never exposes the hash to callers that
    print (the CLI list view selects fields explicitly)."""
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM auth_users ORDER BY email"
        ).fetchall()
    finally:
        conn.row_factory = prior_factory
    return [_row_to_auth_user(r) for r in rows]


def count_auth_users(conn: sqlite3.Connection, *, enabled_only: bool = False) -> int:
    """How many accounts exist. The production fail-closed startup check uses
    `enabled_only=True` — an all-disabled table is as much a lockout as an
    empty one."""
    sql = "SELECT COUNT(*) FROM auth_users"
    if enabled_only:
        sql += " WHERE disabled = 0"
    return int(conn.execute(sql).fetchone()[0])


def count_admins(conn: sqlite3.Connection, *, enabled_only: bool = True) -> int:
    """How many admin accounts exist. The last-admin guard uses the default
    (`enabled_only=True`): a disabled admin can't manage anyone, so demoting or
    disabling the only ENABLED admin would lock everyone out of user
    management. Counts admins that can actually act."""
    sql = "SELECT COUNT(*) FROM auth_users WHERE is_admin = 1"
    if enabled_only:
        sql += " AND disabled = 0"
    return int(conn.execute(sql).fetchone()[0])


def set_auth_user_admin(
    conn: sqlite3.Connection, email: str, is_admin: bool
) -> bool:
    """Promote or demote an account's admin role. Returns False if no such
    account exists. Mirrors set_auth_user_disabled — a pure flag flip that
    keeps the row (and its sessions) intact."""
    norm = _normalize_email(email)
    cur = conn.execute(
        "UPDATE auth_users SET is_admin = ? WHERE email = ?",
        (1 if is_admin else 0, norm),
    )
    return cur.rowcount > 0


def create_auth_session(
    conn: sqlite3.Connection,
    session_id: str,
    email: str,
    display_name: str,
    provider: str = "password",
) -> None:
    """Persist a new server-side session. last_seen_at starts at creation time
    so the very first idle-timeout comparison is meaningful."""
    now = _now()
    conn.execute(
        "INSERT INTO auth_sessions(session_id, email, display_name, provider, "
        "created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, _normalize_email(email), display_name or "", provider, now, now),
    )


def fetch_auth_session(
    conn: sqlite3.Connection, session_id: str
) -> Optional[AuthSession]:
    """Look up a live session by its opaque id, or None if revoked/never existed."""
    if not session_id:
        return None
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.row_factory = prior_factory
    if row is None:
        return None
    return AuthSession(
        session_id=row["session_id"],
        email=row["email"],
        display_name=row["display_name"] or "",
        provider=row["provider"] or "password",
        created_at=row["created_at"] or "",
        last_seen_at=row["last_seen_at"] or "",
    )


def touch_auth_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Bump last_seen_at to now — the sliding-window 'activity' write. Called
    only for real user activity, never for background polls/SSE (so an idle tab
    still times out)."""
    conn.execute(
        "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
        (_now(), session_id),
    )


def delete_auth_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Revoke a single session (logout, or expiry cleanup)."""
    conn.execute(
        "DELETE FROM auth_sessions WHERE session_id = ?", (session_id,)
    )


def sweep_expired_auth_sessions(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    """Delete sessions whose last_seen_at is at or before `cutoff_iso` and return
    how many were removed.

    resolve_session already deletes a session lazily the next time it's touched,
    so this only matters for sessions that are never accessed again (the user
    closed the tab) — without a sweep they'd accumulate forever. Called at
    startup; `cutoff_iso` is `now - idle_timeout` formatted like last_seen_at.
    """
    cur = conn.execute(
        "DELETE FROM auth_sessions WHERE last_seen_at <= ?", (cutoff_iso,)
    )
    return cur.rowcount
