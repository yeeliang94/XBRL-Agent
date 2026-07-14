"""SQLite schema for the XBRL-agent audit store.

The schema is deliberately small and additive. `init_db` is idempotent so it
can run on every server start without a separate migration step. Real
migrations (if they become necessary) will go through `schema_version`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema version written by the current code. Bump this when you add a
# backward-incompatible change and write a migration block that upgrades
# older databases on init.
#
# v2 (frontend-upgrade-history): runs table gained seven lifecycle columns so
# the History page can surface every run — including failed / aborted ones —
# and download its merged workbook from `run_id` alone without guessing
# filesystem paths.
# v3 (notes-rich-editor): adds the `notes_cells` table that holds canonical
# HTML per notes row. Excel downloads flatten HTML at write time; the
# post-run editor edits the HTML in place. Additive-only — rollback is a
# code revert; the stray table is harmless (no FK points at it from any
# legacy reader).
# v4 (canonical-concept-model Phase 1, docs/PRD-canonical-concept-model):
# adds seven additive tables that back the concept tree, fact store,
# audit log, and reconciliation queue. As of the first-principles rewrite
# (Phase 1.1) canonical mode is MANDATORY — the legacy direct-Excel-write
# path and the `XBRL_CANONICAL_MODE` opt-out were removed.
# v5 (canonical-concept-model Phase 5, SOCIE matrix variant): adds one
# nullable column `concept_nodes.matrix_col` carrying the equity-component
# column label on MATRIX_CELL concepts. NULL on every linear concept, so
# the migration is a single idempotent ALTER TABLE (same shape as v2).
# v6 (canonical-concept-model Phase 7, notes integration): adds one
# nullable column `notes_cells.concept_uuid` so a notes row can be linked
# to the canonical concept store. NULL preserves back-compat for the
# coordinator's existing notes-write path. Single idempotent ALTER.
# v7 (review-workspace): adds the nullable `cross_checks.target_sheet` /
# `target_row` click-to-cell columns so the validator UI can jump from a
# failed check straight to the offending cell.
# v8 (run-page-and-telemetry): adds the `run_agent_turns` per-turn metrics
# table (one row per agent iteration: token delta, node kind, tool names,
# duration) and four nullable rollup columns on `run_agents`
# (prompt_tokens, completion_tokens, turn_count, tool_call_count). Metrics
# only — the full per-iteration request/response content stays in the
# on-disk `{stmt}_conversation_trace.json` (hybrid storage; see
# docs/PLAN-run-page-and-telemetry.md). Additive: rollback is a code revert
# and the orphaned table/columns are harmless to legacy readers.
# v9 (SOCIE review labels): adds nullable `concept_nodes.matrix_col_label`,
# the human row-2 SOCIE component header displayed in the review grid. The
# routing key remains `matrix_col` (Excel column letter), so existing facts
# and exporters keep their geometry unchanged.
# v10: adds nullable `runs.orchestration` (TEXT DEFAULT 'split'). Originally
# the flag selected between the split-pipeline coordinator and an experimental
# single-agent monolith path; the monolith experiment was removed in the
# rewrite (Phase 1) and the column is now always 'split' (historical-only,
# retained for schema/History stability). Nullable with a safe default keeps
# legacy rows readable; SQLite ALTER TABLE can't add NOT NULL without a
# default anyway (gotcha #11).
# v11 (concept_render_aliases): a single concept_uuid can have more than
# one physical render coord on a workbook. The motivating case is face-
# sheet rows whose value cross-rolls-up from a sub-sheet total (e.g.
# SOFP-CuNonCu "Property, plant and equipment" with formula
# ='SOFP-Sub-CuNonCu'!Bn). Parser already shares one concept_uuid across
# both render keys; v11 introduces a secondary table so the dedup'd
# face render coord is preserved alongside the primary sub coord. The
# importer reads it during edge resolution (fixes silently-dropped
# cross-sheet child edges), cell_resolver consults it on miss, and the
# concepts endpoint emits one row per alias so the Review/Values page
# mirrors the workbook (one face row + one sub row, same concept).
# v12 (reviewer-agent, docs/Archive/PLAN-reviewer-agent.md): adds two additive
# tables backing the reviewer pass that replaces the autonomous
# canonical correction pass. `run_fact_snapshots` mirrors
# run_concept_facts and stores the ORIGINAL extraction facts before the
# reviewer touches anything — the one-click "Revert to original" restores
# from here, which is what makes the reviewer's free writes safe.
# `reviewer_flags` is the narrow user-facing list of cases the reviewer
# is stuck on (`stuck`) or where it disputes a prior agent
# (`disputes_prior`). Both are brand-new tables, so the v11→v12 step is a
# pure CREATE TABLE IF NOT EXISTS walk-forward with no ALTER columns.
# v13 (rewrite Phase 5.3, durable re-review tasks): adds the
# `run_review_tasks` table that replaces the in-process `_REVIEW_TASKS`
# dict in server.py. A manual re-review runs on a background thread and
# can take minutes; the old dict lost both in-flight and finished passes
# on a process restart. The table persists the latest pass per run
# (run_id is the PK — a new launch overwrites the slot, mirroring the
# dict), so a finished outcome survives a restart and a poll can still
# fetch it. Brand-new table -> the v12->v13 step is a pure CREATE TABLE
# IF NOT EXISTS walk-forward with no ALTER columns. Startup reconciles any
# row left `running` by a dead process into a terminal error state
# (see server._lifespan).
# v16 (gold-standard eval/benchmark, docs/PLAN-eval-benchmark.md): adds four
# additive tables backing the benchmark library + grading, plus one nullable
# `runs.benchmark_id` column so a run knows which benchmark to grade against.
#   - eval_benchmarks            — one row per benchmark document in the library
#   - eval_benchmark_templates   — the EXACT statement variants a benchmark
#                                  covers (template_id encodes the variant; a
#                                  loose '{standard}-{level}-' prefix is
#                                  insufficient — gotcha #21)
#   - gold_concept_facts         — gold answers, mirrors run_concept_facts but
#                                  keyed by benchmark_id instead of run_id
#   - eval_scores                — one scorecard per (run, benchmark)
# The three new tables are pure CREATE TABLE IF NOT EXISTS walk-forward; the
# `runs.benchmark_id` ALTER is nullable so every legacy run reads NULL. Grading
# is wrapped in try/except and gated on benchmark_id, so a normal run is
# byte-for-byte unchanged. Pinned by tests/test_db_schema_v16.py.
#
# v17 (PLAN-orchestration-hardening item 9) adds run_agents.error_type — the
# machine-readable failure class (turn_timeout · iteration_capped · wallclock
# · token_budget_exceeded · projection_failed · save_gate_refused ·
# tool_exception · cancelled · no_write; constants in coordinator.py). One
# nullable additive ALTER; no CHECK constraint on purpose (same rationale as
# runs.status — a new value must not require a migration). Pinned by
# tests/test_db_schema_v17.py.
#
# v18 (PLAN-azure-auth-deployment Phase 1.1) adds the two auth tables —
# auth_users (the account list = the allowlist; argon2id password hash) and
# auth_sessions (server-side session store for the sliding 15-minute idle
# timeout). Both are pure CREATE TABLE IF NOT EXISTS walk-forward steps (new
# tables, no ALTER), so the migration block only bumps the version marker —
# the tables themselves are created above in _CREATE_STATEMENTS. Pinned by
# tests/test_db_schema_v18.py.
#
# v19 (PLAN-notes-template-registry, Track A) adds the `notes_nodes` table — a
# persistent registry of every PROSE notes-template row (sheets 10/11/12),
# parallel to concept_nodes but kept separate because notes are HTML text-blocks,
# not numeric facts (numeric notes 13/14 reuse concept_nodes instead). It lets
# the Notes Review tab project the FULL template (blanks included), and reserves
# a nullable `xbrl_concept_id` to anchor future full-XBRL-filing generation.
# node_uuid is template-scoped (uuid5 of template_id::sheet::row::label) so the
# same row under MFRS/MPERS × Company/Group does not collide. Pure CREATE TABLE
# IF NOT EXISTS walk-forward (new table, no ALTER) — the step only bumps the
# marker. Pinned by tests/test_db_schema_v19.py.
#
# v20 (Settings page + admin user management) adds the `auth_users.is_admin`
# column — the privilege boundary for web-based user management. One additive
# nullable-or-defaulted ALTER (NOT NULL DEFAULT 0). Pinned by
# tests/test_db_schema_v20.py.
#
# v21 (formerly the scanned-PDF → readable-document feature, now REMOVED — see
# docs/PLAN-deprecate-docconvert.md; the table is retained but unused)
# adds the `doc_conversions` table — durable conversion-job state, independent
# of the extraction pipeline. Pure CREATE TABLE IF NOT EXISTS walk-forward (new
# table, no ALTER). Pinned by tests/test_db_schema_v21.py.
# v26 adds `notes_format_tasks`, the durable latest async formatter pass per
# run+sheet for the Notes Review panel.
# v27 adds `notes_format_snapshots` (pre-format HTML per row — "Revert
# formatting" restores from here) + taxonomy/token columns on
# `notes_format_tasks` (docs/PLAN-notes-formatter-hardening.md).
# v28 adds `notes_coverage_rows` — the durable per-run holistic notes coverage
# checklist (docs/PLAN-notes-coverage-and-routing.md Phase 4). One row per
# top-level note (subnote_ref NULL) plus optional per-sub-ref child rows; the
# checklist is recomputed wholesale after the notes reviewer pass and rewritten
# for the Coverage panel. Pure CREATE TABLE IF NOT EXISTS walk-forward (new
# table, no ALTER). Pinned by tests/test_db_schema_v28.py.
# v29 adds `notes_cells.style_source` — how each prose cell got its styling:
# 'ops' (agent format_ops observation), 'floor' (deterministic house style),
# 'unstyled' (plain), or NULL (legacy / reviewer-authored). Surfaced so the
# operator can see which notes fell back to plain and need a manual formatter
# pass. Nullable ALTER TABLE column. Pinned by tests/test_db_schema_v29.py.
CURRENT_SCHEMA_VERSION = 33


# Every CREATE is guarded with IF NOT EXISTS so init_db is safe to call
# repeatedly. Foreign keys use ON DELETE CASCADE so deleting a run sweeps
# up all dependent rows.
_CREATE_STATEMENTS: tuple[str, ...] = (
    # Top-level run: one per user-initiated extraction (web UI or CLI).
    #
    # v2 fields (see CURRENT_SCHEMA_VERSION note above):
    #   session_id            — output directory name; the single source of
    #                            truth that ties a DB row to its on-disk files.
    #   output_dir            — absolute path to the session output folder.
    #   merged_workbook_path  — absolute path to the final filled.xlsx, set
    #                            only after a successful merge. Nullable so
    #                            failed runs can still be listed.
    #   run_config_json       — raw RunConfigRequest body; display-only. It is
    #                            NEVER authoritative for per-agent model
    #                            attribution — use run_agents.model for that.
    #   scout_enabled         — whether scout was run before extraction.
    #   started_at / ended_at — explicit lifecycle timestamps so History can
    #                            show wall-clock duration without re-reading
    #                            the last run_agent row.
    #
    # `status` now accepts the enum {running, completed, completed_with_errors,
    # failed, aborted}. No CHECK constraint: a future enum addition would
    # otherwise require a full-table migration.
    """
    CREATE TABLE IF NOT EXISTS runs (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at            TEXT NOT NULL,           -- ISO 8601 UTC
        pdf_filename          TEXT NOT NULL,
        status                TEXT NOT NULL,           -- 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'aborted'
        notes                 TEXT,
        session_id            TEXT NOT NULL DEFAULT '',
        output_dir            TEXT NOT NULL DEFAULT '',
        merged_workbook_path  TEXT,
        run_config_json       TEXT,
        scout_enabled         INTEGER NOT NULL DEFAULT 0,
        started_at            TEXT NOT NULL DEFAULT '',
        ended_at              TEXT,
        -- v10 audit column (was the monolith-experiment flag; experiment
        -- removed in the rewrite, always 'split' now). NULL on pre-v10 rows;
        -- SQLite default 'split' for new rows.
        orchestration         TEXT DEFAULT 'split',
        -- v16 eval column: the benchmark this run is graded against. NULL on
        -- every normal (non-eval) run, so the grading hook stays inert unless
        -- a benchmark was explicitly attached at run-start. No FK CASCADE
        -- choice matters here (it points OUT to eval_benchmarks); ON DELETE
        -- SET NULL keeps the run row if its benchmark is later deleted.
        benchmark_id          INTEGER REFERENCES eval_benchmarks(id) ON DELETE SET NULL,
        -- v22 per-run notes-table style override (docs/PLAN-notes-table-theme.md).
        -- Nullable JSON; NULL = inherit the firm default. Editable post-run
        -- (review happens after extraction), hence its own column, not the
        -- draft-only run_config_json path.
        notes_table_style     TEXT,
        -- v30 evals-workspace columns (docs/PLAN-evals-workspace.md). All
        -- nullable: app_version stamps which build produced the run;
        -- repeat_group_id/repeat_index link repeats for consistency scoring.
        -- The REFERENCES points forward to repeat_groups (created later in this
        -- statement list — SQLite resolves FK targets lazily).
        app_version           TEXT,
        repeat_group_id       INTEGER REFERENCES repeat_groups(id) ON DELETE SET NULL,
        repeat_index          INTEGER,
        -- v31 evals-workspace Phase 2: links a suite child run back to its
        -- batch (eval_suite_runs, created later in this list). NULL on every
        -- non-suite run. History hides suite children by default (E6).
        suite_run_id          INTEGER REFERENCES eval_suite_runs(id) ON DELETE SET NULL
    )
    """,

    # One row per (run, statement) agent invocation. Scout is also recorded
    # here with statement_type='SCOUT' so all agent activity is uniform.
    """
    CREATE TABLE IF NOT EXISTS run_agents (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        statement_type  TEXT NOT NULL,           -- 'SOFP' | 'SOPL' | ... | 'SCOUT'
        variant         TEXT,                    -- e.g. 'CuNonCu'
        model           TEXT,
        status          TEXT NOT NULL,           -- 'running' | 'succeeded' | 'failed'
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        workbook_path   TEXT,
        total_tokens    INTEGER DEFAULT 0,
        total_cost      REAL DEFAULT 0,
        -- v8 rollups: prompt/completion split + iteration counters so the
        -- telemetry UI can show cost breakdown and turn/tool activity without
        -- re-summing the per-turn rows. Default 0 keeps legacy rows readable.
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        turn_count        INTEGER DEFAULT 0,
        tool_call_count   INTEGER DEFAULT 0,
        -- v15 cache telemetry: cache_read = prompt-cache hits (proof caching
        -- works); cache_write = tokens written to cache (Anthropic prices
        -- these at a premium, so cost accounting must see them). Default 0.
        cache_read_tokens  INTEGER DEFAULT 0,
        cache_write_tokens INTEGER DEFAULT 0,
        -- v17: machine-readable failure class (item 9 taxonomy; see
        -- coordinator.py ERROR_TYPE_* constants). NULL on success and on
        -- legacy rows. No CHECK constraint on purpose.
        error_type      TEXT
    )
    """,

    # v8: one row per agent iteration ("turn"). Metrics only — the full
    # request/response content for a turn lives in the on-disk conversation
    # trace, not here (hybrid storage keeps the SQLite DB small). token deltas
    # are this turn's contribution; cumulative_tokens is the running total
    # after the turn so the UI can plot a trend without re-summing.
    """
    CREATE TABLE IF NOT EXISTS run_agent_turns (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_agent_id      INTEGER NOT NULL REFERENCES run_agents(id) ON DELETE CASCADE,
        turn_index        INTEGER NOT NULL,        -- 1-based iteration number
        node_kind         TEXT,                    -- 'model_request' | 'call_tools'
        tool_names        TEXT,                    -- comma-joined tool names invoked this turn; NULL for pure model turns
        prompt_tokens     INTEGER DEFAULT 0,       -- delta vs previous turn
        completion_tokens INTEGER DEFAULT 0,       -- delta vs previous turn
        total_tokens      INTEGER DEFAULT 0,       -- delta vs previous turn
        cumulative_tokens INTEGER DEFAULT 0,       -- running total after this turn
        cost_estimate     REAL DEFAULT 0,          -- delta cost for this turn
        duration_ms       INTEGER DEFAULT 0,
        cache_read_tokens  INTEGER DEFAULT 0,      -- v15: cache-hit delta this turn
        cache_write_tokens INTEGER DEFAULT 0,      -- v15: cache-write delta this turn
        ts                TEXT NOT NULL
    )
    """,

    # Every SSE event (tool call, thinking, status, error, ...) for auditing.
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_agent_id    INTEGER NOT NULL REFERENCES run_agents(id) ON DELETE CASCADE,
        ts              TEXT NOT NULL,
        event_type      TEXT NOT NULL,           -- matches SSE 'event' field
        phase           TEXT,
        payload_json    TEXT                     -- the SSE 'data' blob, JSON-encoded
    )
    """,

    # Structured data-entry cells written to each workbook, for downstream
    # cross-checks and UI display without re-reading the xlsx file.
    """
    CREATE TABLE IF NOT EXISTS extracted_fields (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_agent_id    INTEGER NOT NULL REFERENCES run_agents(id) ON DELETE CASCADE,
        sheet           TEXT NOT NULL,
        field_label     TEXT NOT NULL,
        section         TEXT,
        col             INTEGER NOT NULL,
        row_num         INTEGER,
        value           REAL,
        evidence        TEXT
    )
    """,

    # Cross-statement reconciliation results (Phase 5).
    """
    CREATE TABLE IF NOT EXISTS cross_checks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        check_name      TEXT NOT NULL,
        status          TEXT NOT NULL,           -- passed | failed | not_applicable | pending
        expected        REAL,
        actual          REAL,
        diff            REAL,
        tolerance       REAL,
        message         TEXT,
        -- Review Workspace M2/Step 8: the cell a failed check points at, so
        -- the validator UI can jump straight to it. Nullable — most check
        -- statuses (pending/not_applicable) and checks without a natural
        -- anchor leave these NULL.
        target_sheet    TEXT,
        target_row      INTEGER,
        -- v14 (reviewer holistic audit, Phase 2): JSON list of the values
        -- this check compared (both sides of a cross-statement equality, or
        -- the leaves of a balance) so the reviewer gets concrete entry points
        -- instead of a bare diff. Nullable — older rows + non-numeric checks
        -- read NULL. See cross_checks.framework.Comparand.
        comparands_json TEXT
    )
    """,

    # Schema metadata — single-row version marker.
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version         INTEGER PRIMARY KEY
    )
    """,

    # -----------------------------------------------------------------
    # v4: canonical concept model — read-mostly template registry +
    # per-run fact store + audit log + reconciliation queue. Every CREATE
    # is guarded with IF NOT EXISTS so fresh-init unions v1..v4 cleanly.
    # -----------------------------------------------------------------

    # One row per parsed template. `source_path` is the on-disk xlsx;
    # `imported_at` lets us audit when a template was last refreshed.
    """
    CREATE TABLE IF NOT EXISTS concept_templates (
        template_id   TEXT PRIMARY KEY,
        source_path   TEXT NOT NULL,
        imported_at   TEXT,
        shape         TEXT NOT NULL DEFAULT 'linear'  -- 'linear' | 'matrix' (P5)
    )
    """,

    # One row per concept in the tree. `concept_uuid` is the immutable
    # PK; `display_label` is the UI-overridable label (NULL = use
    # canonical). `render_*` is the canonical cell coordinate for the
    # exporter; `concept_targets` carries per-scope columns (Phase 4).
    """
    CREATE TABLE IF NOT EXISTS concept_nodes (
        concept_uuid     TEXT PRIMARY KEY,
        template_id      TEXT NOT NULL REFERENCES concept_templates(template_id) ON DELETE CASCADE,
        parent_uuid      TEXT REFERENCES concept_nodes(concept_uuid) ON DELETE SET NULL,
        kind             TEXT NOT NULL,           -- 'ABSTRACT' | 'LEAF' | 'COMPUTED' | 'MATRIX_CELL' (P5)
        canonical_label  TEXT NOT NULL,
        display_label    TEXT,                    -- UI override; NULL = use canonical
        render_sheet     TEXT NOT NULL,
        render_row       INTEGER NOT NULL,
        render_col       TEXT NOT NULL,
        matrix_col       TEXT,                    -- P5: equity-component column letter on MATRIX_CELL; NULL on linear concepts
        matrix_col_label TEXT                     -- P9: human SOCIE component header; NULL on linear concepts
    )
    """,

    # Directed edges from a parent COMPUTED concept to its summands.
    # `coefficient` is signed (+1, -1, etc.). One row per edge.
    """
    CREATE TABLE IF NOT EXISTS concept_edges (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_uuid      TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        child_uuid       TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        coefficient      REAL NOT NULL DEFAULT 1.0,
        UNIQUE(parent_uuid, child_uuid)
    )
    """,

    # Per-scope render targets — Phase 4 fills the Company / Group
    # columns and (eventually) SOCIE matrix cells. Phase 1 is allowed to
    # leave this empty for Company-only filings (render_col on
    # concept_nodes is sufficient).
    """
    CREATE TABLE IF NOT EXISTS concept_targets (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        concept_uuid     TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        entity_scope     TEXT NOT NULL,           -- 'Company' | 'Group'
        period           TEXT NOT NULL,           -- 'CY' | 'PY'
        target_sheet     TEXT NOT NULL,
        target_row       INTEGER NOT NULL,
        target_col       TEXT NOT NULL,
        UNIQUE(concept_uuid, entity_scope, period)
    )
    """,

    # v11: secondary render coordinates for a concept that surfaces on
    # more than one physical sheet/row. Today the only producer is the
    # importer's cross-sheet linkage step: when a face row points at a
    # sub-sheet total via ='Sub-Sheet'!Bn, both rows share one
    # concept_uuid but live at different (sheet, row) coordinates. The
    # primary coord stays on concept_nodes (anchored at the formula-
    # owning sub-sheet row); every other physical location for the same
    # concept lands here. Consumers: importer edge resolver (so face
    # coords still resolve to the canonical UUID), cell_resolver (so an
    # agent write to a face cell still maps to the canonical UUID), and
    # the concepts endpoint (emits one extra view-row per alias so the
    # Review/Values page mirrors the workbook).
    """
    CREATE TABLE IF NOT EXISTS concept_render_aliases (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        concept_uuid     TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        alias_sheet      TEXT NOT NULL,
        alias_row        INTEGER NOT NULL,
        alias_col        TEXT NOT NULL,
        UNIQUE(concept_uuid, alias_sheet, alias_row, alias_col)
    )
    """,

    # Per-run facts (the heart of the canonical model). Composite key:
    # (run_id, concept_uuid, period, entity_scope). Two status axes:
    #   value_status     — observed | explicit_zero | not_disclosed | user_override | conflict
    #   children_status  — itemised | aggregate_only | partial (only on COMPUTED concepts)
    """
    CREATE TABLE IF NOT EXISTS run_concept_facts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        concept_uuid     TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        period           TEXT NOT NULL,
        entity_scope     TEXT NOT NULL,
        value            REAL,
        value_status     TEXT NOT NULL,
        children_status  TEXT,
        source           TEXT,                    -- free-form provenance
        evidence         TEXT,                    -- pdf page + quoted text
        updated_at       TEXT NOT NULL DEFAULT '',
        UNIQUE(run_id, concept_uuid, period, entity_scope)
    )
    """,

    # Append-only audit log: every change to run_concept_facts lands a
    # row here. Used by the reconciliation queue UI and by any future
    # "show me what the correction agent did" view.
    """
    CREATE TABLE IF NOT EXISTS concept_fact_events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        concept_uuid     TEXT NOT NULL,
        period           TEXT NOT NULL,
        entity_scope     TEXT NOT NULL,
        actor            TEXT,                    -- agent name | 'user' | 'cascade'
        turn             INTEGER,
        ts               TEXT NOT NULL,
        before_json      TEXT,
        after_json       TEXT
    )
    """,

    # Reconciliation queue — anything the auto-correction agent (Phase 3+)
    # or the cascade can't resolve lands here for the user to triage.
    """
    CREATE TABLE IF NOT EXISTS run_concept_conflicts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        concept_uuid     TEXT NOT NULL,
        period           TEXT NOT NULL,
        entity_scope     TEXT NOT NULL,
        kind             TEXT NOT NULL,           -- 'parent_child_disagree' | 'partial_state' | 'cross_check_failure'
        residual         REAL,
        detail           TEXT,
        status           TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'resolved' | 'dismissed'
        created_at       TEXT NOT NULL DEFAULT '',
        resolved_at      TEXT
    )
    """,

    # v3: canonical per-run store for notes HTML payloads. Every notes
    # agent write lands here; the Excel download path flattens HTML to
    # plaintext on demand, and the post-run editor reads/writes HTML
    # directly against this table. UNIQUE(run_id, sheet, row) is the
    # upsert key — one row per template cell per run.
    """
    CREATE TABLE IF NOT EXISTS notes_cells (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet         TEXT NOT NULL,
        row           INTEGER NOT NULL,
        label         TEXT NOT NULL,
        html          TEXT NOT NULL,
        evidence      TEXT,
        source_pages  TEXT,
        updated_at    TEXT NOT NULL,
        concept_uuid  TEXT,                    -- P7: link to canonical concept store; NULL = legacy notes write
        style_source  TEXT,                    -- v29: 'ops'|'floor'|'unstyled'; NULL = legacy / reviewer-authored
        UNIQUE(run_id, sheet, row)
    )
    """,

    # v19: persistent registry of PROSE notes-template rows (Track A of
    # PLAN-notes-template-registry). One row per (template_id, sheet, row) of
    # the prose notes templates (10/11/12) — the run-independent description of
    # the template, so the Notes Review tab can project the FULL template with
    # blanks and overlay per-run `notes_cells`. Deliberately NOT in concept_nodes
    # (those drive the numeric cascade/cross-checks/exporter, which prose has no
    # part in) and with NO FK to concept_templates (prose notes are not in the
    # concept pipeline). `node_uuid` is template-scoped — uuid5 of
    # template_id::sheet::row::label — so the same prose row under MFRS/MPERS ×
    # Company/Group gets DISTINCT ids and the PK never collides. `xbrl_concept_id`
    # is reserved (NULL until the XBRL-generation follow-up populates the real
    # SSM element id). `kind` is 'ABSTRACT' (section header — not projected) or
    # 'LEAF' (fillable). UNIQUE(template_id, sheet, row) is the upsert key and
    # serves template_id-prefixed lookups (no separate index needed).
    """
    CREATE TABLE IF NOT EXISTS notes_nodes (
        node_uuid        TEXT PRIMARY KEY,
        template_id      TEXT NOT NULL,           -- e.g. 'mfrs-company-notes-corporateinfo-v1'
        sheet            TEXT NOT NULL,
        row              INTEGER NOT NULL,
        label            TEXT NOT NULL,
        kind             TEXT NOT NULL,           -- 'ABSTRACT' (header) | 'LEAF' (fillable)
        xbrl_concept_id  TEXT,                    -- reserved; NULL until XBRL-gen follow-up populates the SSM element id
        UNIQUE(template_id, sheet, row)
    )
    """,

    # -----------------------------------------------------------------
    # v12: reviewer-agent backing tables (docs/Archive/PLAN-reviewer-agent.md).
    # -----------------------------------------------------------------

    # Original-facts backup. Taken ONCE per run, immediately before the
    # reviewer pass writes anything, by snapshot_facts(). It mirrors the
    # run_concept_facts columns the exporter / cascade care about, plus a
    # snapshot_at stamp. "Revert to original" replaces a run's live facts
    # with these rows — this is the load-bearing reversibility invariant
    # that lets the reviewer write freely instead of being write-gated.
    # No FK to concept_nodes: the snapshot must survive even if a template
    # is re-imported, and run_id's CASCADE already sweeps it on run delete.
    """
    CREATE TABLE IF NOT EXISTS run_fact_snapshots (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        concept_uuid     TEXT NOT NULL,
        period           TEXT NOT NULL,
        entity_scope     TEXT NOT NULL,
        value            REAL,
        value_status     TEXT NOT NULL,
        children_status  TEXT,
        source           TEXT,
        evidence         TEXT,
        snapshot_at      TEXT NOT NULL DEFAULT '',
        UNIQUE(run_id, concept_uuid, period, entity_scope)
    )
    """,

    # Reviewer flags — the only user-facing "needs attention" list. The
    # reviewer raises one when it is `stuck` (can't reconcile or ground a
    # figure) or `disputes_prior` (it believes an earlier agent erred).
    # Grounded fixes are NOT flagged — they just appear in the diff. The
    # status axis (open → answered → resolved/dismissed) tracks the human's
    # triage; `human_answer` carries free-text guidance fed back into a
    # re-review. `applied_fix` optionally references a change the reviewer
    # made alongside a dispute. concept_uuid is nullable because a stuck
    # case may not map cleanly to one concept.
    """
    CREATE TABLE IF NOT EXISTS reviewer_flags (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        concept_uuid  TEXT,
        target_sheet  TEXT,
        target_row    INTEGER,
        category      TEXT NOT NULL,           -- 'stuck' | 'disputes_prior'
        reasoning     TEXT,
        pdf_page      INTEGER,
        applied_fix   TEXT,                    -- optional ref to a change made alongside a dispute
        status        TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'answered' | 'resolved' | 'dismissed'
        human_answer  TEXT,
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    )
    """,

    # -----------------------------------------------------------------
    # v13: durable manual re-review task state (rewrite Phase 5.3).
    # -----------------------------------------------------------------

    # Replaces the in-process `_REVIEW_TASKS` dict in server.py. A manual
    # re-review launches a background thread (minutes-long) and returns
    # immediately; the Review tab polls for the outcome. The old dict lost
    # every pass — in-flight AND finished — on a process restart. This
    # table keeps the latest pass per run: `run_id` is the PRIMARY KEY so a
    # new launch overwrites the slot (the dict's `[run_id] = state`
    # semantics), the implicit PK index serves the per-run poll, and a
    # finished outcome survives a restart. Absence of a row == 'idle'.
    # `outcome_json` is NULL while running and carries the full outcome
    # dict (ok / error / invoked / writes_performed / flags_raised / model)
    # once done. No CHECK on `status`: only {running, done} today, but a
    # future value shouldn't force a table rebuild (same rationale as runs).
    """
    CREATE TABLE IF NOT EXISTS run_review_tasks (
        run_id        INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
        status        TEXT NOT NULL,           -- 'running' | 'done'
        model_name    TEXT,
        outcome_json  TEXT,                    -- NULL while running; outcome dict JSON when done
        started_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    )
    """,

    # -----------------------------------------------------------------
    # v16: gold-standard eval / benchmark library (docs/PLAN-eval-benchmark.md).
    # All four tables are additive; deleting a benchmark cascades to its
    # template set + gold facts + scores via ON DELETE CASCADE.
    # -----------------------------------------------------------------

    # One row per benchmark document in the library. A benchmark is a
    # financial-statement document with human-verified gold answers, tagged
    # by filing standard + level (the picker filters on these).
    """
    CREATE TABLE IF NOT EXISTS eval_benchmarks (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL,             -- human label, e.g. "FINCO 2021 MFRS Company"
        document         TEXT,                      -- source PDF name / ref
        filing_standard  TEXT NOT NULL,             -- 'mfrs' | 'mpers'
        filing_level     TEXT NOT NULL,             -- 'company' | 'group'
        created_at       TEXT NOT NULL DEFAULT '',
        -- v33 (PLAN-evals-hardening Steps 9/14): archive instead of hard-delete
        -- (historical scores survive), where the gold came from
        -- ('run' | 'workbook' | 'mtool' | NULL legacy), and whether an
        -- mTool-derived benchmark's scale has been verified against a real
        -- human-filled file (0 = show the "scale unverified" badge).
        is_archived      INTEGER NOT NULL DEFAULT 0,
        source           TEXT,
        scale_verified   INTEGER NOT NULL DEFAULT 1
    )
    """,

    # The EXACT statement variants this benchmark covers. `template_id`
    # encodes the variant ('mfrs-company-sofp-cunoncu-v1' vs
    # '...-sofp-orderofliquidity-v1') — a loose '{standard}-{level}-' prefix is
    # INSUFFICIENT because it spans both variants of every statement, whose
    # concept uuids differ (gotcha #21). Grading + ingestion scope by
    # `template_id IN (this set)`. `statement_type` is denormalised so the
    # extract-page picker can filter without re-parsing template_ids.
    """
    CREATE TABLE IF NOT EXISTS eval_benchmark_templates (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
        template_id      TEXT NOT NULL REFERENCES concept_templates(template_id),
        statement_type   TEXT NOT NULL,             -- 'SOFP' | 'SOPL' | ... (for the picker)
        UNIQUE(benchmark_id, template_id)
    )
    """,

    # Gold facts — mirrors run_concept_facts, keyed by benchmark instead of
    # run. The composite UNIQUE key is the upsert anchor. `value_status`
    # follows the run-fact vocabulary ('observed' | 'explicit_zero' |
    # 'not_disclosed'); grading treats 'not_disclosed' gold as out-of-scope.
    """
    CREATE TABLE IF NOT EXISTS gold_concept_facts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
        concept_uuid     TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
        period           TEXT NOT NULL,             -- 'CY' | 'PY'
        entity_scope     TEXT NOT NULL,             -- 'Company' | 'Group'
        value            REAL,
        value_status     TEXT NOT NULL DEFAULT 'observed',
        source           TEXT,
        updated_at       TEXT NOT NULL DEFAULT '',
        UNIQUE(benchmark_id, concept_uuid, period, entity_scope)
    )
    """,

    # One scorecard per (run, benchmark). Aggregate counts only — the MVP
    # needs a number, not a per-cell drill-down list. `gold_cells` is the
    # headline denominator (matched + missing + mismatch); `extra_cells` and
    # `scale_mismatch` are surfaced as flags, NOT folded into the score.
    """
    CREATE TABLE IF NOT EXISTS eval_scores (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
        gold_cells       INTEGER NOT NULL,          -- denominator = gradeable gold cells
        matched_cells    INTEGER NOT NULL,          -- numerator
        missing_cells    INTEGER NOT NULL,          -- gold has value, run empty/absent (counts wrong)
        mismatch_cells   INTEGER NOT NULL,          -- both present, values differ (counts wrong)
        extra_cells      INTEGER NOT NULL,          -- run filled, gold blank (WARNING, not in denominator)
        scale_mismatch   INTEGER NOT NULL,          -- subset of mismatch that match after 10^k scaling (flag)
        created_at       TEXT NOT NULL DEFAULT '',
        -- v30 (docs/PLAN-evals-workspace.md): failure-diagnosis taxonomy and
        -- per-statement breakdown as JSON. Nullable — legacy scorecards graded
        -- before the taxonomy read NULL and the frontend degrades gracefully.
        taxonomy_json    TEXT,
        per_statement_json TEXT,
        -- v33 (PLAN-evals-hardening Step 7): content hash of the benchmark's
        -- gold facts AT GRADE TIME. Comparing it to the current hash detects
        -- ANY later gold change — edits, deletions, benchmark reassignment —
        -- which the old timestamp-window heuristic missed. NULL on legacy rows.
        gold_fingerprint TEXT,
        UNIQUE(run_id, benchmark_id)
    )
    """,
    # --- v30: Evals workspace (docs/PLAN-evals-workspace.md) ---
    # A repeat group links N runs of the SAME document launched together so the
    # consistency scorer can compare them (PRD Family 2). `config_json` is the
    # frozen launch config all repeats share; `consistency_json` is the computed
    # result (NULL until the last repeat finishes). `benchmark_id` is copied here
    # so the group knows its gold without re-reading a child run. `status` has no
    # CHECK constraint on purpose (gotcha #11): running | complete | partial.
    """
    CREATE TABLE IF NOT EXISTS repeat_groups (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at         TEXT NOT NULL DEFAULT '',
        config_json        TEXT,                      -- frozen launch config snapshot
        repeats_requested  INTEGER NOT NULL DEFAULT 1,
        benchmark_id       INTEGER REFERENCES eval_benchmarks(id) ON DELETE SET NULL,
        consistency_json   TEXT,                      -- computed consistency result (NULL = not yet)
        status             TEXT NOT NULL DEFAULT 'running'
    )
    """,

    # Gold prose captured from a human-filled mTool file's footnote payloads at
    # ingest time (PRD Family 3 — "capture now, grade later"). Numeric gold lives
    # in gold_concept_facts; this is the narrative side-channel, stored so a
    # future prose-fidelity pass has ground truth without a second ingest. Nothing
    # grades it in Phase 1. `note_key` is the mTool join key / note ref.
    """
    CREATE TABLE IF NOT EXISTS gold_note_texts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
        note_key         TEXT NOT NULL,
        text             TEXT NOT NULL,
        updated_at       TEXT NOT NULL DEFAULT '',
        UNIQUE(benchmark_id, note_key)
    )
    """,

    # --- v31: Evals workspace Phase 2 — suites + batch runner ---
    # A Suite is a named corpus of documents run together as a regression set
    # (PRD Flow 3). Documents (eval_suite_docs) copy their source file into
    # managed storage so a re-run months later uses byte-identical inputs.
    # A Suite Run (eval_suite_runs) is one batch execution: a frozen config
    # snapshot + label + status; its child runs link back via runs.suite_run_id.
    # No CHECK constraints on the status-like columns (gotcha #11).
    """
    CREATE TABLE IF NOT EXISTS eval_suites (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    )
    """,
    # One document per row. `source_path` is the managed copy of the PDF/.docx.
    # `benchmark_id` is optional gold (a doc without gold still contributes
    # consistency + health). filing_standard/level pin how the doc is extracted.
    """
    CREATE TABLE IF NOT EXISTS eval_suite_docs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        suite_id         INTEGER NOT NULL REFERENCES eval_suites(id) ON DELETE CASCADE,
        label            TEXT NOT NULL DEFAULT '',
        source_path      TEXT NOT NULL DEFAULT '',       -- managed copy of the input file
        source_filename  TEXT NOT NULL DEFAULT '',       -- original user-visible name
        filing_standard  TEXT NOT NULL DEFAULT 'mfrs',
        filing_level     TEXT NOT NULL DEFAULT 'company',
        benchmark_id     INTEGER REFERENCES eval_benchmarks(id) ON DELETE SET NULL,
        created_at       TEXT NOT NULL DEFAULT '',
        -- v32: how figures are printed in this document (extraction denomination)
        denomination     TEXT NOT NULL DEFAULT 'thousands'
    )
    """,
    # One batch execution of a suite. `config_json` freezes model/repeats/toggles/
    # label; `status` (running | complete | partial | failed) has no CHECK
    # constraint (gotcha #11). `app_version` is stamped so trends are comparable.
    """
    CREATE TABLE IF NOT EXISTS eval_suite_runs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        suite_id       INTEGER NOT NULL REFERENCES eval_suites(id) ON DELETE CASCADE,
        label          TEXT NOT NULL DEFAULT '',
        config_json    TEXT,                              -- frozen launch config snapshot
        model          TEXT,
        app_version    TEXT,
        status         TEXT NOT NULL DEFAULT 'running',
        created_at     TEXT NOT NULL DEFAULT '',
        ended_at       TEXT
    )
    """,

    # --- v32: suite-run corpus snapshot (docs/PLAN-evals-hardening.md Step 2) ---
    # The frozen document list of ONE suite run, written at launch BEFORE any
    # execution. The runner (start / resume / finalize) reads only this table,
    # never the live eval_suite_docs — so editing the suite later can't change
    # what a partial run resumes or how its completion is judged. `suite_doc_id`
    # is a plain INTEGER (no FK on purpose): the snapshot must survive deletion
    # of the live doc. `state` (queued | running | finished | failed) doubles as
    # the durable per-doc execution record — a doc that fails to stage gets
    # state='failed' + error instead of vanishing. No CHECK constraints on
    # status-like columns (gotcha #11).
    """
    CREATE TABLE IF NOT EXISTS eval_suite_run_docs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        suite_run_id     INTEGER NOT NULL REFERENCES eval_suite_runs(id) ON DELETE CASCADE,
        suite_doc_id     INTEGER NOT NULL,                -- frozen copy, no FK
        label            TEXT NOT NULL DEFAULT '',
        source_path      TEXT NOT NULL DEFAULT '',
        source_filename  TEXT NOT NULL DEFAULT '',
        source_sha256    TEXT NOT NULL DEFAULT '',
        filing_standard  TEXT NOT NULL DEFAULT 'mfrs',
        filing_level     TEXT NOT NULL DEFAULT 'company',
        benchmark_id     INTEGER,                         -- frozen copy, no FK
        denomination     TEXT NOT NULL DEFAULT 'thousands',
        variants_json    TEXT,                            -- resolved per-doc variants
        state            TEXT NOT NULL DEFAULT 'queued',
        error            TEXT,
        created_at       TEXT NOT NULL DEFAULT '',
        updated_at       TEXT NOT NULL DEFAULT '',
        UNIQUE(suite_run_id, suite_doc_id)
    )
    """,

    # --- v18: authentication layer (PLAN-azure-auth-deployment Phase 1) ---
    # auth_users IS the allowlist: a non-disabled row = an authorised account.
    # email is the primary key, stored lowercased so lookups are case-folded.
    # password_hash is nullable so a future SSO-only user can exist with no
    # password (the deferred Microsoft phase); the password path always sets it.
    """
    CREATE TABLE IF NOT EXISTS auth_users (
        email           TEXT PRIMARY KEY,          -- lowercased; the login identity + allowlist key
        display_name    TEXT NOT NULL DEFAULT '',
        password_hash   TEXT,                      -- argon2id; NULL = SSO-only account (no password login)
        disabled        INTEGER NOT NULL DEFAULT 0, -- 1 blocks login without deleting the row (audit trail)
        created_at      TEXT NOT NULL DEFAULT '',
        password_set_at TEXT,                      -- ISO 8601 UTC of the last password set/rotate
        is_admin        INTEGER NOT NULL DEFAULT 0  -- 1 = may manage other accounts (v20); 0 = ordinary user
    )
    """,
    # auth_sessions is the server-side session store (not stateless JWT) so
    # logout + the sliding 15-minute idle timeout are enforceable/revocable.
    # session_id is a random opaque 256-bit token; last_seen_at drives expiry.
    # Deleting a user sweeps their live sessions (ON DELETE CASCADE).
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        session_id    TEXT PRIMARY KEY,            -- random 256-bit opaque token (hex)
        email         TEXT NOT NULL REFERENCES auth_users(email) ON DELETE CASCADE,
        display_name  TEXT NOT NULL DEFAULT '',
        provider      TEXT NOT NULL DEFAULT 'password',  -- 'password' now; 'microsoft' when SSO lands
        created_at    TEXT NOT NULL DEFAULT '',
        last_seen_at  TEXT NOT NULL DEFAULT ''     -- bumped on real activity; sliding-window expiry compares against now
    )
    """,

    # -----------------------------------------------------------------
    # v21: scanned-PDF → readable-document conversion jobs.
    # DEPRECATED / UNUSED — the scanned-PDF → readable-doc feature was removed
    # (see docs/PLAN-deprecate-docconvert.md). This CREATE is RETAINED on purpose:
    # the schema migrates one version at a time, so deleting the v20→v21 step
    # would break the upgrade chain for any existing DB. No code reads or writes
    # this table anymore; it is an inert artifact in databases created at/after
    # v21. Do not add a drop migration without weighing the full chain.
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS doc_conversions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        source_pdf_path   TEXT NOT NULL,            -- absolute path to the uploaded PDF
        original_filename TEXT NOT NULL DEFAULT '', -- display name (used for the .docx download)
        status            TEXT NOT NULL,            -- 'queued' | 'running' | 'done' | 'failed'
        total_pages       INTEGER NOT NULL DEFAULT 0,
        current_page      INTEGER NOT NULL DEFAULT 0,
        result_html_path  TEXT,                     -- on-disk converted HTML; NULL until done
        error             TEXT,                     -- failure message; NULL unless failed
        created_at        TEXT NOT NULL DEFAULT '',
        updated_at        TEXT NOT NULL DEFAULT ''
    )
    """,

    # -----------------------------------------------------------------
    # v23: notes-reviewer backing tables (docs/PLAN.md — Notes Reviewer).
    # -----------------------------------------------------------------

    # Durable detector provenance. The notes reviewer's structural detectors
    # (sub-note gaps, same-sheet collisions, …) need each written cell's
    # source note refs — which today live ONLY in the on-disk
    # `*_payloads.json` sidecars (not durable for a manual re-review on a
    # fresh process). We copy them into the DB at extraction completion so a
    # re-review recomputes findings from the database, never from run-dir
    # files. One row per written prose cell. `source_note_refs` is a JSON list.
    """
    CREATE TABLE IF NOT EXISTS notes_cell_provenance (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet            TEXT NOT NULL,
        row              INTEGER NOT NULL,
        row_label        TEXT NOT NULL DEFAULT '',
        source_note_refs TEXT,                    -- JSON list[str], e.g. ["3","3.3","(b)"]
        content_preview  TEXT,                    -- short snippet, mirrors the sidecar
        UNIQUE(run_id, sheet, row)
    )
    """,

    # Durable scout notes inventory. The sub-note-coverage detector needs the
    # scout-discovered sub-references per top-level note (e.g. 3 → 3.1/3.2/3.3/
    # (a)/(b)); these live only in infopack.json today. One row per top-level
    # note. `subnote_refs` is a JSON list[str].
    """
    CREATE TABLE IF NOT EXISTS run_notes_inventory (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        note_num      INTEGER NOT NULL,
        title         TEXT NOT NULL DEFAULT '',
        subnote_refs  TEXT,                       -- JSON list[str]
        page_lo       INTEGER,
        page_hi       INTEGER,
        UNIQUE(run_id, note_num)
    )
    """,

    # Original-prose backup for "Revert to original". Taken ONCE per run,
    # immediately before the reviewer pass writes anything — the notes
    # analogue of run_fact_snapshots. Revert deletes all live prose rows then
    # restores this set, so a reviewer-AUTHORED (previously-blank) row is
    # correctly removed on revert. Mirrors notes_cells columns.
    """
    CREATE TABLE IF NOT EXISTS run_notes_cell_snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet         TEXT NOT NULL,
        row           INTEGER NOT NULL,
        label         TEXT NOT NULL,
        html          TEXT NOT NULL,
        evidence      TEXT,
        source_pages  TEXT,                       -- JSON list[int]
        concept_uuid  TEXT,
        style_source  TEXT,                        -- v29: mirror notes_cells so revert restores the chip
        snapshot_at   TEXT NOT NULL,
        UNIQUE(run_id, sheet, row)
    )
    """,

    # Per-run "snapshot taken" marker. The snapshot ABOVE may legitimately
    # capture ZERO rows — a run whose prose was empty when the reviewer first
    # authored a cell. Row-count can't then signal "a snapshot exists", so the
    # taken-fact lives here as one row per run. Revert keys off THIS marker
    # (not row count), so it can correctly wipe reviewer-authored cells back to
    # the empty original.
    """
    CREATE TABLE IF NOT EXISTS run_notes_review_state (
        run_id        INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
        snapshot_at   TEXT NOT NULL
    )
    """,

    # -----------------------------------------------------------------
    # v24: notes-reviewer flags + durable manual re-review task state.
    # -----------------------------------------------------------------

    # Flags the notes reviewer raised for a human — the notes analogue of
    # reviewer_flags. kind ∈ stuck | disputes_prior | needs_human. status ∈
    # open | answered | dismissed (revert dismisses). Kept SEPARATE from
    # reviewer_flags so the Notes-tab UI and the face reviewer don't entangle.
    """
    CREATE TABLE IF NOT EXISTS notes_review_flags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        kind        TEXT NOT NULL,
        reason      TEXT NOT NULL DEFAULT '',
        sheet       TEXT,
        row         INTEGER,
        status      TEXT NOT NULL DEFAULT 'open',
        answer      TEXT,
        created_at  TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL DEFAULT ''
    )
    """,

    # Durable per-run state for the async manual notes re-review (the notes
    # analogue of run_review_tasks). One row per run; a relaunch overwrites it.
    # A finished outcome survives a restart so a poll can still fetch it; a row
    # left 'running' by a crash is reconciled to a terminal error at startup.
    """
    CREATE TABLE IF NOT EXISTS notes_review_tasks (
        run_id      INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
        status      TEXT NOT NULL,              -- running | done
        model       TEXT,
        outcome     TEXT,                       -- JSON outcome dict; NULL while running
        error       TEXT,
        created_at  TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL DEFAULT ''
    )
    """,

    # -----------------------------------------------------------------
    # v25: notes-reviewer tombstones — durable "this cell was emptied".
    # -----------------------------------------------------------------

    # The download/finalize path overlays `notes_cells` onto the merged
    # workbook ADDITIVELY (one pass per surviving row), so it cannot, on its
    # own, represent a DELETION: a reviewer clear / move-out removes the
    # notes_cells row but the original prose written at merge time stays in the
    # xlsx and is reintroduced on every download (duplicate / stale content).
    # This table records each coordinate the reviewer emptied so the overlay
    # can blank that workbook cell (prose + evidence). Revert clears the run's
    # tombstones and re-tombstones any reviewer-AUTHORED row (absent from the
    # snapshot) so the authored prose is blanked too. One row per emptied cell.
    """
    CREATE TABLE IF NOT EXISTS notes_cell_tombstones (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet       TEXT NOT NULL,
        row         INTEGER NOT NULL,
        created_at  TEXT NOT NULL DEFAULT '',
        UNIQUE(run_id, sheet, row)
    )
    """,

    # -----------------------------------------------------------------
    # v26: notes formatter task state.
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS notes_format_tasks (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id            INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet             TEXT NOT NULL,
        status            TEXT NOT NULL,       -- running | done
        model             TEXT,
        summary           TEXT,
        confidence        REAL,
        changed_rows      INTEGER NOT NULL DEFAULT 0,
        result_json       TEXT,
        error             TEXT,
        before_text_hash  TEXT,
        after_text_hash   TEXT,
        -- v27 columns (ALTERed in for DBs that walked to v26 first):
        -- failure taxonomy + per-pass token telemetry.
        error_type        TEXT,
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0,
        cache_write_tokens INTEGER DEFAULT 0,
        created_at        TEXT NOT NULL DEFAULT '',
        updated_at        TEXT NOT NULL DEFAULT '',
        UNIQUE(run_id, sheet)
    )
    """,

    # -----------------------------------------------------------------
    # v27: notes formatter pre-format snapshots. One snapshot per
    # (run, sheet), overwritten by each pass BEFORE its first row write —
    # "Revert formatting" restores from here (safety is versioning, not
    # write-gating; mirrors the reviewer's run_fact_snapshots philosophy).
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS notes_format_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet       TEXT NOT NULL,
        row         INTEGER NOT NULL,
        html        TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT '',
        UNIQUE(run_id, sheet, row)
    )
    """,

    # -----------------------------------------------------------------
    # v28: durable notes coverage checklist (one row per top-level note,
    # plus optional per-sub-ref child rows). Recomputed wholesale after the
    # notes reviewer pass; `replace_notes_coverage_for_run` delete+inserts
    # the whole set. `subnote_ref` NULL marks the top-level note row; the
    # unique index coalesces NULL to '' so a top-level row and its children
    # never collide. See docs/PLAN-notes-coverage-and-routing.md Phase 4.
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS notes_coverage_rows (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        note_num        INTEGER NOT NULL,
        subnote_ref     TEXT,                -- NULL = top-level note row
        status          TEXT NOT NULL,       -- placed|missing|skipped|suspected_gap (top);
                                             -- cited|not_verified|verified|missing (sub)
        reason          TEXT,
        placements_json TEXT,                -- JSON list of {sheet,row,row_label,kind}
        reviewer_added  INTEGER NOT NULL DEFAULT 0,
        reviewer_verdict TEXT,               -- confirmed_absent|not_applicable (top rows)
        title           TEXT,
        page_lo         INTEGER,
        page_hi         INTEGER,
        updated_at      TEXT NOT NULL DEFAULT ''
    )
    """,
)


# Indexes on foreign-key columns so per-run queries stay cheap.
# ix_runs_created_at supports the History list's default DESC sort.
_CREATE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_run_agents_run_id ON run_agents(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_events_run_agent_id ON agent_events(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_extracted_fields_run_agent_id ON extracted_fields(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_cross_checks_run_id ON cross_checks(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_created_at ON runs(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_notes_cells_run_id ON notes_cells(run_id)",
    # v4 indexes — every per-run query the canonical model needs.
    "CREATE INDEX IF NOT EXISTS ix_concept_nodes_template_id ON concept_nodes(template_id)",
    "CREATE INDEX IF NOT EXISTS ix_concept_edges_parent_uuid ON concept_edges(parent_uuid)",
    "CREATE INDEX IF NOT EXISTS ix_concept_edges_child_uuid ON concept_edges(child_uuid)",
    "CREATE INDEX IF NOT EXISTS ix_concept_targets_concept_uuid ON concept_targets(concept_uuid)",
    # v11: aliases are queried two ways — by uuid (concepts endpoint
    # joins from concept_nodes to emit alias view-rows) and by
    # (alias_sheet, alias_row) (cell_resolver fallback + importer edge
    # resolution build a coord→uuid map). Index the join key; the
    # composite UNIQUE constraint already serves the coord lookup.
    "CREATE INDEX IF NOT EXISTS ix_concept_render_aliases_concept_uuid ON concept_render_aliases(concept_uuid)",
    "CREATE INDEX IF NOT EXISTS ix_run_concept_facts_run_id ON run_concept_facts(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_concept_fact_events_run_id ON concept_fact_events(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_run_concept_conflicts_run_id ON run_concept_conflicts(run_id)",
    # v8: per-turn metrics are always queried by their owning agent.
    "CREATE INDEX IF NOT EXISTS ix_run_agent_turns_run_agent_id ON run_agent_turns(run_agent_id)",
    # v12: both reviewer tables are always queried per-run.
    "CREATE INDEX IF NOT EXISTS ix_run_fact_snapshots_run_id ON run_fact_snapshots(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_reviewer_flags_run_id ON reviewer_flags(run_id)",
    # v16: eval tables are queried per-benchmark (template set, gold grid) and
    # per-run (scorecard lookup on the run page).
    "CREATE INDEX IF NOT EXISTS ix_eval_benchmark_templates_benchmark_id ON eval_benchmark_templates(benchmark_id)",
    "CREATE INDEX IF NOT EXISTS ix_gold_concept_facts_benchmark_id ON gold_concept_facts(benchmark_id)",
    "CREATE INDEX IF NOT EXISTS ix_eval_scores_run_id ON eval_scores(run_id)",
    # v31: suite child runs are queried per suite run (trend/compare/detail/
    # finished-doc/stop) and hidden from History via the same column.
    "CREATE INDEX IF NOT EXISTS ix_runs_suite_run_id ON runs(suite_run_id)",
    # v23: notes-reviewer tables are always queried per-run.
    "CREATE INDEX IF NOT EXISTS ix_notes_cell_provenance_run_id ON notes_cell_provenance(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_run_notes_inventory_run_id ON run_notes_inventory(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_run_notes_cell_snapshots_run_id ON run_notes_cell_snapshots(run_id)",
    # v24: notes-reviewer flags queried per-run.
    "CREATE INDEX IF NOT EXISTS ix_notes_review_flags_run_id ON notes_review_flags(run_id)",
    # v25: notes-reviewer tombstones queried per-run at overlay time.
    "CREATE INDEX IF NOT EXISTS ix_notes_cell_tombstones_run_id ON notes_cell_tombstones(run_id)",
    # v26: notes formatter task poll/reconciliation queries.
    "CREATE INDEX IF NOT EXISTS ix_notes_format_tasks_run_id ON notes_format_tasks(run_id)",
    # v27: formatter snapshots fetched per (run, sheet) at revert time.
    "CREATE INDEX IF NOT EXISTS ix_notes_format_snapshots_run_id ON notes_format_snapshots(run_id)",
    # v28: coverage rows are always read per-run; the unique index coalesces a
    # NULL subnote_ref to '' so a top-level row and its children never collide.
    "CREATE INDEX IF NOT EXISTS ix_notes_coverage_rows_run_id ON notes_coverage_rows(run_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_notes_coverage_rows "
    "ON notes_coverage_rows(run_id, note_num, COALESCE(subnote_ref, ''))",
)


# v2 columns that need to be added via ALTER TABLE when migrating an
# existing v1 database. SQLite ALTER TABLE cannot add a NOT NULL column
# without a default, so every entry here either is nullable or carries a
# safe default. Order matters: later migrations may read earlier columns.
_V2_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("session_id",            "TEXT NOT NULL DEFAULT ''"),
    ("output_dir",             "TEXT NOT NULL DEFAULT ''"),
    ("merged_workbook_path",   "TEXT"),
    ("run_config_json",        "TEXT"),
    ("scout_enabled",          "INTEGER NOT NULL DEFAULT 0"),
    ("started_at",             "TEXT NOT NULL DEFAULT ''"),
    ("ended_at",               "TEXT"),
)


# v5 columns added via ALTER TABLE when migrating an existing v4 database.
# Nullable (no default) so SQLite's ALTER TABLE accepts them and existing
# linear concepts read NULL.
_V5_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("concept_nodes", "matrix_col", "TEXT"),
)


# v6 columns added via ALTER TABLE when migrating an existing v5 database.
# Nullable so existing notes rows read NULL.
_V6_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("notes_cells", "concept_uuid", "TEXT"),
)


# v7 columns: the click-to-cell target on a failed cross-check. Nullable so
# existing cross_checks rows read NULL.
_V7_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("cross_checks", "target_sheet", "TEXT"),
    ("cross_checks", "target_row", "INTEGER"),
)


# v8 columns: per-agent token-split + iteration rollups. The `run_agent_turns`
# table itself is created via CREATE TABLE IF NOT EXISTS above; only these
# ALTERs are needed to walk an existing v7 DB forward. Each has a default so
# SQLite's ALTER TABLE accepts it and legacy rows read 0.
_V8_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("run_agents", "prompt_tokens", "INTEGER DEFAULT 0"),
    ("run_agents", "completion_tokens", "INTEGER DEFAULT 0"),
    ("run_agents", "turn_count", "INTEGER DEFAULT 0"),
    ("run_agents", "tool_call_count", "INTEGER DEFAULT 0"),
)


# v9 column: nullable so existing concepts read NULL until the startup
# bootstrap re-imports templates and hydrates it from SOCIE row-2 headers.
_V9_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("concept_nodes", "matrix_col_label", "TEXT"),
)


# v10 column: `runs.orchestration` (was the monolith-experiment flag;
# experiment removed in the rewrite, always 'split' now). Nullable with a
# safe default ('split') keeps every existing run readable.
_V10_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "orchestration", "TEXT DEFAULT 'split'"),
)


# v14 columns: the reviewer-facing comparands on a cross-check (reviewer
# holistic audit, Phase 2). Nullable so existing cross_checks rows + non-
# numeric checks read NULL. SQLite ALTER TABLE accepts a nullable column.
_V14_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("cross_checks", "comparands_json", "TEXT"),
)


# v15 columns: cache telemetry (§6 caching work — measure before optimizing).
# Rollups on run_agents + per-turn deltas on run_agent_turns. All nullable with
# default 0 so every pre-v15 row reads 0; SQLite ALTER TABLE accepts them.
_V15_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("run_agents", "cache_read_tokens", "INTEGER DEFAULT 0"),
    ("run_agents", "cache_write_tokens", "INTEGER DEFAULT 0"),
    ("run_agent_turns", "cache_read_tokens", "INTEGER DEFAULT 0"),
    ("run_agent_turns", "cache_write_tokens", "INTEGER DEFAULT 0"),
)


# v16 column: the eval benchmark a run is graded against (gold-standard eval).
# Nullable, no default, so every legacy run reads NULL and the grading hook
# stays inert. The four eval tables are created via CREATE TABLE IF NOT EXISTS
# above; only this ALTER walks an existing v15 DB forward.
_V16_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "benchmark_id", "INTEGER REFERENCES eval_benchmarks(id) ON DELETE SET NULL"),
)


# v17 column: structured failure taxonomy for agent rows (item 9 of
# PLAN-orchestration-hardening). Nullable TEXT, no default — every legacy
# row and every succeeded agent reads NULL.
_V17_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("run_agents", "error_type", "TEXT"),
)


# v20 column: the admin role that gates web user-management (Settings → Users
# tab + /api/admin/* routes). NOT NULL with a 0 default so every legacy account
# walks forward as an ordinary (non-admin) user — admin #1 is minted explicitly
# via `python -m auth.manage make-admin`. SQLite permits ADD COLUMN NOT NULL
# only because a constant default is supplied (see the constraint note below).
_V20_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("auth_users", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
)


# v22 column: the per-run notes-table style override (docs/PLAN-notes-table-theme.md).
# A nullable JSON string; NULL means the run inherits the firm default. Unlike
# run_config_json this is editable AFTER a run finishes (review happens post-run),
# so it gets its own column + endpoint rather than the draft-only config path.
_V22_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "notes_table_style", "TEXT"),
)


# v27 columns on notes_format_tasks: the failure taxonomy code (`error_type`,
# nullable, no CHECK — same rationale as runs.status: a new code must not
# require a full-table migration) and per-pass token telemetry (mirrors the
# v15 run_agents cache columns). All nullable-or-defaulted so the ALTER is
# legal on SQLite.
_V27_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("notes_format_tasks", "error_type", "TEXT"),
    ("notes_format_tasks", "prompt_tokens", "INTEGER DEFAULT 0"),
    ("notes_format_tasks", "completion_tokens", "INTEGER DEFAULT 0"),
    ("notes_format_tasks", "cache_read_tokens", "INTEGER DEFAULT 0"),
    ("notes_format_tasks", "cache_write_tokens", "INTEGER DEFAULT 0"),
)

# v28 → v29: record per-cell styling provenance on notes_cells so the operator
# can see which prose cells rendered plain (and want a manual formatter pass).
# The snapshot table mirrors it so "Revert to original" restores the tag.
# Nullable — legacy rows and reviewer-authored cells stay NULL.
_V29_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("notes_cells", "style_source", "TEXT"),
    ("run_notes_cell_snapshots", "style_source", "TEXT"),
)


# v29 → v30: Evals workspace (docs/PLAN-evals-workspace.md).
#   - runs.app_version       — which build/prompt version produced this run, so
#                              "better over time" is answerable (PRD Technical
#                              Approach). NULL on every legacy run.
#   - runs.repeat_group_id   — links a run to its repeat group (consistency).
#   - runs.repeat_index      — 0-based position within the group.
#   - eval_scores.taxonomy_json     — the failure-diagnosis counts (sign flip,
#                                     period swap, …) as JSON. NULL = legacy
#                                     scorecard graded before the taxonomy.
#   - eval_scores.per_statement_json — per-statement accuracy breakdown as JSON.
# All nullable (gotcha #11): legacy rows read NULL; the frontend renders the
# richer shape only when present.
_V30_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "app_version", "TEXT"),
    ("runs", "repeat_group_id", "INTEGER REFERENCES repeat_groups(id) ON DELETE SET NULL"),
    ("runs", "repeat_index", "INTEGER"),
    ("eval_scores", "taxonomy_json", "TEXT"),
    ("eval_scores", "per_statement_json", "TEXT"),
)

# v30 → v31: Evals workspace Phase 2 (suites + batch runner). New tables
# (eval_suites / eval_suite_docs / eval_suite_runs) are created via CREATE TABLE
# IF NOT EXISTS above; this only walks an existing DB forward by adding the
# suite_run_id linkage column on runs. Nullable (gotcha #11).
_V31_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "suite_run_id", "INTEGER REFERENCES eval_suite_runs(id) ON DELETE SET NULL"),
)

# v31 → v32: suite-run corpus snapshot (docs/PLAN-evals-hardening.md Step 2).
# The eval_suite_run_docs table is created via CREATE TABLE IF NOT EXISTS above;
# this walks an existing DB forward by adding the per-document denomination on
# the live suite-doc rows (defaulted, so legacy docs keep today's behaviour).
_V32_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("eval_suite_docs", "denomination", "TEXT NOT NULL DEFAULT 'thousands'"),
)

# v32 → v33: gold-change guard + benchmark archive (PLAN-evals-hardening
# Steps 7-9/14). All additive: legacy scores read a NULL fingerprint (the UI
# shows "unknown gold version" rather than a false "unchanged"), legacy
# benchmarks are unarchived with an unknown source and verified scale.
_V33_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("eval_scores", "gold_fingerprint", "TEXT"),
    ("eval_benchmarks", "is_archived", "INTEGER NOT NULL DEFAULT 0"),
    ("eval_benchmarks", "source", "TEXT"),
    ("eval_benchmarks", "scale_verified", "INTEGER NOT NULL DEFAULT 1"),
)


def init_db(path: str | Path) -> None:
    """Create (or open) the SQLite database and ensure all tables exist.

    Idempotent: running init_db twice leaves the database in the same state.
    Called on server startup; safe to call from tests against a fresh file.

    Concurrency (peer-review C7): when two processes start simultaneously
    against a v1 DB, both could read version<2 and both try to ALTER TABLE
    the same column. We serialize the migration with `BEGIN IMMEDIATE`
    (acquires the write lock up front), re-check the version inside the
    transaction, and tolerate `duplicate column` errors as idempotent
    success (a racer beat us to the column). `busy_timeout` keeps the
    second starter waiting briefly instead of failing instantly.
    """
    p = Path(path)
    # Make sure the parent directory exists so callers can pass paths like
    # ``output/xbrl_agent.db`` without pre-creating the folder themselves.
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(p))
    try:
        # Foreign keys are off by default in SQLite — turn them on per-conn.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        for sql in _CREATE_STATEMENTS:
            conn.execute(sql)

        # Figure out the current schema version BEFORE running index/migration
        # logic, so we know whether this is a fresh DB or an older one that
        # needs to be walked forward.
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        existing = cur.fetchone()
        current_version = int(existing[0]) if existing is not None else None

        # Migrate v1 → v2 inside an IMMEDIATE write transaction. Two
        # concurrent init_db calls against a v1 DB will serialize here —
        # the loser re-reads schema_version after the winner commits and
        # finds v2, skipping the ALTER loop entirely.
        #
        # Each per-version block advances schema_version by exactly ONE
        # step (peer-review I-1). A block that jumps to
        # CURRENT_SCHEMA_VERSION would cause subsequent per-version blocks
        # to short-circuit on a multi-step walk (e.g. v1 → future-v4 would
        # skip the v2→v3 and v3→v4 bodies). Today both v2→v3 and (when it
        # exists) v3→v4 are additive so the jump is harmless, but the
        # discipline keeps every block runnable independently.
        if current_version is not None and current_version < 2:
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Re-check inside the tx — the racer may have migrated while
                # we waited on the busy_timeout.
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 2:
                    existing_cols = {
                        r[1]
                        for r in conn.execute("PRAGMA table_info(runs)").fetchall()
                    }
                    for col_name, col_ddl in _V2_MIGRATION_COLUMNS:
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE runs ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                # "duplicate column name" — a racing process
                                # added it in between our PRAGMA and ALTER.
                                # Idempotent success.
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE runs SET started_at = created_at "
                        "WHERE (started_at IS NULL OR started_at = '')"
                    )
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (2,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the next per-version block sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v2 → v3: the table was already created above via
        # CREATE TABLE IF NOT EXISTS; we only need to walk the version
        # marker forward. Serialised with BEGIN IMMEDIATE so two
        # concurrent starters don't race on the schema_version UPDATE.
        if current_version is not None and current_version < 3:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 3:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (3,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v3 → v4: the seven concept-model tables were already created
        # above via CREATE TABLE IF NOT EXISTS; we only need to walk the
        # schema_version marker forward. Same BEGIN IMMEDIATE pattern as
        # the v2→v3 block so concurrent starters serialise cleanly.
        if current_version is not None and current_version < 4:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 4:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (4,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v4→v5 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v4 → v5: add the nullable `matrix_col` column to concept_nodes
        # (SOCIE matrix variant). The CREATE TABLE above already carries
        # the column on fresh DBs; this ALTER walks an existing v4 DB
        # forward. Same BEGIN IMMEDIATE + duplicate-column tolerance as the
        # v1→v2 block so concurrent starters serialise cleanly.
        if current_version is not None and current_version < 5:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 5:
                    for table, col_name, col_ddl in _V5_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                # A racing starter added it between our
                                # PRAGMA and ALTER — idempotent success.
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (5,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v5→v6 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v5 → v6: add the nullable `concept_uuid` column to notes_cells
        # (notes integration). Fresh DBs already carry it via CREATE TABLE
        # above; this ALTER walks an existing v5 DB forward. Same
        # BEGIN IMMEDIATE + duplicate-column tolerance as v1→v2.
        if current_version is not None and current_version < 6:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 6:
                    for table, col_name, col_ddl in _V6_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (6,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v6→v7 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v6 → v7: add the nullable click-to-cell target columns to
        # cross_checks. Fresh DBs already carry them via CREATE TABLE above;
        # this ALTER walks an existing v6 DB forward. Same BEGIN IMMEDIATE +
        # duplicate-column tolerance as the earlier steps.
        if current_version is not None and current_version < 7:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 7:
                    for table, col_name, col_ddl in _V7_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (7,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v7→v8 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v7 → v8: add the per-agent token-split + iteration rollup columns to
        # run_agents. The run_agent_turns table is created by CREATE TABLE
        # above on fresh DBs; this ALTER walks an existing v7 DB forward. Same
        # BEGIN IMMEDIATE + duplicate-column tolerance as the earlier steps.
        if current_version is not None and current_version < 8:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 8:
                    for table, col_name, col_ddl in _V8_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (8,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v8→v9 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v8 → v9: add the nullable human SOCIE component header column to
        # concept_nodes. Fresh DBs already carry it via CREATE TABLE above;
        # existing DBs get NULL until startup bootstrap re-imports templates.
        if current_version is not None and current_version < 9:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 9:
                    for table, col_name, col_ddl in _V9_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (9,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v9→v10 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v9 → v10: add the nullable `runs.orchestration` audit column (was the
        # monolith-experiment flag; experiment removed in the rewrite, always
        # 'split' now). Default 'split' keeps every legacy reader working.
        # Fresh DBs already carry it via CREATE TABLE above. Same BEGIN
        # IMMEDIATE + duplicate-column tolerance as the earlier steps.
        if current_version is not None and current_version < 10:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 10:
                    for table, col_name, col_ddl in _V10_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (10,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v10→v11 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v10 → v11: add the concept_render_aliases table. The CREATE
        # TABLE at the top of init_db is idempotent, so older DBs that
        # walk through this block just confirm the table exists and bump
        # the marker. Same BEGIN IMMEDIATE discipline as earlier steps.
        if current_version is not None and current_version < 11:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 11:
                    # Table is already created above (idempotent
                    # CREATE TABLE IF NOT EXISTS); this block just
                    # advances the marker so future migrations see v11.
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (11,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v11→v12 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v11 → v12: add the reviewer-agent tables (run_fact_snapshots +
        # reviewer_flags). Both are created above via the idempotent
        # CREATE TABLE IF NOT EXISTS, so older DBs that walk through this
        # block just confirm the tables exist and bump the marker — no
        # ALTER columns needed. Same BEGIN IMMEDIATE discipline as the
        # earlier additive-table steps (v2→v3, v10→v11).
        if current_version is not None and current_version < 12:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 12:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (12,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v12→v13 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v12 → v13: add the run_review_tasks table (durable manual
        # re-review state, Phase 5.3). Created above via the idempotent
        # CREATE TABLE IF NOT EXISTS, so older DBs that walk through this
        # block just confirm the table exists and bump the marker — no
        # ALTER columns needed. Same BEGIN IMMEDIATE discipline as the
        # earlier additive-table steps (v2→v3, v10→v11, v11→v12).
        if current_version is not None and current_version < 13:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 13:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (13,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v13→v14 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v13 → v14: add cross_checks.comparands_json (reviewer holistic
        # audit, Phase 2). Fresh DBs already carry the column via CREATE TABLE
        # above; this ALTER walks an existing v13 DB forward. Same BEGIN
        # IMMEDIATE + duplicate-column tolerance as the earlier column steps.
        if current_version is not None and current_version < 14:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 14:
                    for table, col_name, col_ddl in _V14_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (14,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v14→v15 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v14 → v15: add cache-telemetry columns (cache_read_tokens /
        # cache_write_tokens) to run_agents + run_agent_turns. Fresh DBs already
        # carry them via CREATE TABLE above; this ALTER walks an existing v14 DB
        # forward. Same BEGIN IMMEDIATE + duplicate-column tolerance as the
        # earlier column steps (v9→v10, v13→v14).
        if current_version is not None and current_version < 15:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 15:
                    for table, col_name, col_ddl in _V15_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (15,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v15→v16 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v15 → v16: add the four eval/benchmark tables + the nullable
        # `runs.benchmark_id` column (gold-standard eval). The tables are
        # created above via CREATE TABLE IF NOT EXISTS, so older DBs just
        # confirm they exist; the ALTER walks an existing v15 DB forward.
        # Same BEGIN IMMEDIATE + duplicate-column tolerance as the earlier
        # column steps (v14→v15). The ALTER's FK targets eval_benchmarks,
        # which the CREATE-TABLE loop above already materialised.
        if current_version is not None and current_version < 16:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 16:
                    for table, col_name, col_ddl in _V16_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (16,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v16→v17 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v16 → v17: add run_agents.error_type (item 9 failure taxonomy).
        # One nullable additive ALTER — same BEGIN IMMEDIATE + duplicate-
        # column tolerance as the earlier column steps.
        if current_version is not None and current_version < 17:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 17:
                    for table, col_name, col_ddl in _V17_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (17,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v17 → v18: add the auth_users + auth_sessions tables. Both are pure
        # CREATE TABLE IF NOT EXISTS (already run above from _CREATE_STATEMENTS),
        # so — like the v12→v13 table-only step — this block only advances the
        # version marker. Same BEGIN IMMEDIATE + re-check discipline as the
        # column steps so two concurrent starters serialize cleanly.
        if current_version is not None and current_version < 18:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 18:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (18,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v18→v19 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v18 → v19: add the notes_nodes table (prose notes registry, Track A of
        # PLAN-notes-template-registry). Created above via the idempotent
        # CREATE TABLE IF NOT EXISTS, so older DBs that walk through this block
        # just confirm the table exists and bump the marker — no ALTER columns
        # needed. Same BEGIN IMMEDIATE discipline as the earlier additive-table
        # steps (v2→v3, v10→v11, v11→v12, v12→v13, v17→v18).
        if current_version is not None and current_version < 19:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 19:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (19,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v19→v20 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v19 → v20: add auth_users.is_admin (web user-management role gate).
        # One additive ALTER with a constant default — same BEGIN IMMEDIATE +
        # duplicate-column tolerance as the earlier column steps (v15/v16/v17).
        if current_version is not None and current_version < 20:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 20:
                    for table, col_name, col_ddl in _V20_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (20,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v20→v21 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v20 → v21: add the doc_conversions table (scanned-PDF → readable-doc
        # feature). Created above via the idempotent CREATE TABLE IF NOT EXISTS,
        # so this is a pure walk-forward that only confirms the table and bumps
        # the marker — no ALTER columns. Same BEGIN IMMEDIATE discipline as the
        # earlier additive-table steps (v18→v19 etc.).
        if current_version is not None and current_version < 21:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 21:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (21,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v21→v22 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v21 → v22: add runs.notes_table_style (per-run theme override). One
        # additive nullable ALTER — same BEGIN IMMEDIATE + duplicate-column
        # tolerance as the earlier column steps (v15/v16/v17/v20).
        if current_version is not None and current_version < 22:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 22:
                    for table, col_name, col_ddl in _V22_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (22,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v22→v23 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v22 → v23: add the three notes-reviewer tables (notes_cell_provenance,
        # run_notes_inventory, run_notes_cell_snapshots). All pure CREATE TABLE
        # IF NOT EXISTS (already run above from _CREATE_STATEMENTS), so — like the
        # other additive-table steps (v17→v18, v18→v19) — this block only
        # advances the version marker, no ALTER columns. Same BEGIN IMMEDIATE +
        # re-check discipline so two concurrent starters serialize cleanly.
        if current_version is not None and current_version < 23:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 23:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (23,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v23→v24 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v23 → v24: add notes_review_flags + notes_review_tasks. Pure CREATE
        # TABLE IF NOT EXISTS (already run above), so this block only advances
        # the marker. Same BEGIN IMMEDIATE + re-check discipline.
        if current_version is not None and current_version < 24:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 24:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (24,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v24→v25 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v24 → v25: add notes_cell_tombstones. Pure CREATE TABLE IF NOT EXISTS
        # (already run above), so this block only advances the marker. Same
        # BEGIN IMMEDIATE + re-check discipline.
        if current_version is not None and current_version < 25:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 25:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (25,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v25→v26 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v25 → v26: add notes_format_tasks. Pure CREATE TABLE IF NOT EXISTS
        # (already run above), so this block only advances the marker. Same
        # BEGIN IMMEDIATE + re-check discipline.
        if current_version is not None and current_version < 26:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 26:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (26,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v26→v27 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v26 → v27: notes_format_snapshots (already created above) + the
        # notes_format_tasks taxonomy/token columns. Same BEGIN IMMEDIATE +
        # re-check + duplicate-column tolerance as the earlier column steps
        # (a DB that never reached v26 got the full CREATE with the columns
        # inline, so the PRAGMA check skips the ALTERs there).
        if current_version is not None and current_version < 27:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 27:
                    for table, col_name, col_ddl in _V27_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (27,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v27→v28 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v27 → v28: add notes_coverage_rows. Pure CREATE TABLE IF NOT EXISTS
        # (already run above), so this block only advances the marker. Same
        # BEGIN IMMEDIATE + re-check discipline as the earlier pure-table steps.
        if current_version is not None and current_version < 28:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 28:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (28,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the v28→v29 block below sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v28 → v29: add notes_cells.style_source. Same BEGIN IMMEDIATE +
        # re-check + duplicate-column tolerance as the earlier column steps
        # (a fresh DB got the column inline in the CREATE, so the PRAGMA
        # check skips the ALTER there).
        if current_version is not None and current_version < 29:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 29:
                    for table, col_name, col_ddl in _V29_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (29,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v29 → v30: Evals workspace. New tables (repeat_groups, gold_note_texts)
        # are created above via CREATE TABLE IF NOT EXISTS; this block walks an
        # existing DB forward by adding the nullable columns. Same BEGIN IMMEDIATE
        # + duplicate-column tolerance as every prior additive step.
        if current_version is not None and current_version < 30:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 30:
                    for table, col_name, col_ddl in _V30_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (30,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v30 → v31: Evals workspace Phase 2 (suites). New tables created above;
        # this adds runs.suite_run_id. Same additive, duplicate-tolerant shape.
        if current_version is not None and current_version < 31:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 31:
                    for table, col_name, col_ddl in _V31_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (31,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v31 → v32: suite-run corpus snapshot. New table created above; this
        # adds eval_suite_docs.denomination. Same additive, duplicate-tolerant
        # shape as every prior step.
        if current_version is not None and current_version < 32:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 32:
                    for table, col_name, col_ddl in _V32_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (32,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # v32 → v33: gold fingerprint + benchmark archive/source. Same
        # additive, duplicate-tolerant shape as every prior step.
        if current_version is not None and current_version < 33:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 33:
                    for table, col_name, col_ddl in _V33_MIGRATION_COLUMNS:
                        existing_cols = {
                            r[1]
                            for r in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (33,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        for sql in _CREATE_INDEXES:
            conn.execute(sql)

        # Record the current schema version if not already set (fresh DB).
        if current_version is None:
            conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
        conn.commit()
    finally:
        conn.close()
