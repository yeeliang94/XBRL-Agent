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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


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
    # Phase 7: per-agent SSE-equivalent events hydrated by get_run_detail().
    # Defaulted via field(default_factory=list) so legacy callers that build
    # RunAgent directly (e.g. fetch_run_agents) don't break — a bare `= []`
    # would be a Python mutable-default bug.
    events: list["AgentEvent"] = field(default_factory=list)


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
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, notes, "
        "session_id, output_dir, run_config_json, scout_enabled, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now, pdf_filename, status, notes or None,
            session_id, output_dir, config_json,
            1 if scout_enabled else 0, started_at,
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
    cur = conn.execute(
        "UPDATE runs SET run_config_json = ? WHERE id = ? AND status = 'draft'",
        (json.dumps(merged), run_id),
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


def finish_run_agent(
    conn: sqlite3.Connection,
    run_agent_id: int,
    status: str,
    workbook_path: str | None = None,
    total_tokens: int = 0,
    total_cost: float = 0.0,
    variant: str | None = None,
) -> None:
    """Mark an agent row as finished with final status + metrics.

    The `variant` parameter updates run_agents.variant only when non-None.
    This matters because Phase 6.5 pre-creates run_agent rows BEFORE the
    coordinator runs, and at that point we only know the user-supplied
    variant (which may be None). The coordinator later resolves a default
    variant from scout or the registry, and we need to persist that
    resolved value — otherwise History shows `variant = NULL` for any run
    where the user didn't explicitly pick a variant.
    """
    if variant is not None:
        conn.execute(
            "UPDATE run_agents SET status = ?, ended_at = ?, workbook_path = ?, "
            "total_tokens = ?, total_cost = ?, variant = ? WHERE id = ?",
            (status, _now(), workbook_path, total_tokens, total_cost, variant, run_agent_id),
        )
    else:
        conn.execute(
            "UPDATE run_agents SET status = ?, ended_at = ?, workbook_path = ?, "
            "total_tokens = ?, total_cost = ? WHERE id = ?",
            (status, _now(), workbook_path, total_tokens, total_cost, run_agent_id),
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
) -> int:
    cur = conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, expected, actual, diff, "
        "tolerance, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, check_name, status, expected, actual, diff, tolerance, message),
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
) -> int:
    """Insert or update a single notes cell and return its id.

    `UNIQUE(run_id, sheet, row)` makes this an upsert: rerunning a notes
    agent overwrites the row the coordinator already cleared (see
    `delete_notes_cells_for_run_sheet`) or, if the delete was skipped,
    replaces the same (sheet, row) slot in place.
    """
    now = _now()
    pages_json = json.dumps(list(source_pages)) if source_pages else None
    existing = conn.execute(
        "SELECT id FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    if existing is not None:
        cell_id = int(existing[0])
        conn.execute(
            "UPDATE notes_cells SET label = ?, html = ?, evidence = ?, "
            "source_pages = ?, updated_at = ? WHERE id = ?",
            (label, html, evidence, pages_json, now, cell_id),
        )
        return cell_id
    cur = conn.execute(
        "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
        "evidence, source_pages, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, sheet, row, label, html, evidence, pages_json, now),
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
        )
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
