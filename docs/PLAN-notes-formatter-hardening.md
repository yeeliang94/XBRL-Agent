# Implementation Plan: Notes Formatter — Prototype → Production Hardening

**Overall Progress:** `100%` — all phases complete (backend 2740 + web 838 green)
**PRD Reference:** none — scoped via `/agent-skills:review` findings + hardening
discussion on 2026-07-02. Builds on the uncommitted formatter prototype
(CLAUDE.md gotcha #16, "Notes formatter agent (2026-07-01 prototype)").
**Last Updated:** 2026-07-02

## Summary

Promote the notes formatter agent from a prototype to a production feature. The
patch/verify core (constrained JSON patches, deterministic content-preservation
gate, sanitiser re-check) stays as-is; this plan adds what production requires
around it: write-time compare-and-swap so concurrent edits are never clobbered,
snapshot + one-click revert (the reviewer's "safety is versioning" philosophy),
lifecycle interlocks, structured error taxonomy, trace + token observability,
first-class model/threshold configuration, and frontend pinning tests — plus
the five code-review findings from 2026-07-02.

## Key Decisions

- **Panel/clipboard-only styling**: formatter styles reach the Review panel and
  clipboard paste; the xlsx download stays a text overlay. Native xlsx styling
  remains deferred (as it has been since notes editor v2) — it is a separate,
  large project. The UI will say so explicitly.
- **Manual-only trigger**: no `XBRL_AUTO_FORMAT_NOTES` auto-pass for now. Add
  the toggle later if usage proves it's wanted (same pattern as
  `XBRL_AUTO_REVIEW` when the time comes).
- **Numeric sheets (13/14) stay excluded** (422): their formatting is mostly
  the theme's job already.
- **Concurrency safety = CAS + versioning, not locking**: re-check each row at
  write time and skip changed/missing rows; recoverability via a pre-format
  snapshot table + revert endpoint. No editor locks, no long-lived DB locks.
- **Schema goes v26 → v27** rather than amending the (uncommitted) v26 block:
  local dev DBs may already have walked to 26, and `CREATE TABLE IF NOT EXISTS`
  would silently skip amended columns. v27 = `notes_format_snapshots` table +
  additive columns on `notes_format_tasks` (`error_type`, token telemetry) via
  `_V27_MIGRATION_COLUMNS` — follows the house migration discipline exactly.
- **`before_text_hash`/`after_text_hash` columns stay** (they are equal by
  construction since the verifier requires text equality) — kept as audit
  surface; documented, not removed, to avoid a pointless schema churn.

## Pre-Implementation Checklist

- [x] 🟩 Commit the current formatter prototype as the baseline commit — done:
  `feat/notes-formatter` branched off `feat/reviewer-verify-followups`,
  baseline commit c0f7985.
- [x] 🟩 Decisions above confirmed by the user (`/implement all phases`).
- [x] 🟩 No conflicting in-progress work on `notes_cells` / notes reviewer.

## Tasks

### Phase 1: Write Safety (backend)

- [x] 🟩 **Step 1: Write-time compare-and-swap** — the formatter snapshots rows
  at launch and writes up to 300s later; today it clobbers anything written in
  between and *resurrects* rows a regenerate deleted.
  - [x] 🟩 In `run_notes_formatter`'s final `db_session` block, re-read each
    target row; upsert only when current HTML == `rows_for_patch[row]`.
  - [x] 🟩 Treat a **missing** row (regenerate deleted it) as changed → skip.
  - [x] 🟩 Report `skipped_rows: [...]` in the result dict and append
    "N row(s) skipped — edited during formatting" to the summary when non-zero.
  - **Verify:** new tests in `tests/test_notes_format_patch.py` /
    `test_notes_formatter_routes.py`: (a) mutate a cell between launch and
    write → row skipped, user content intact; (b) delete a row mid-pass → not
    resurrected. `./venv/bin/python -m pytest tests/test_notes_format*.py -q`
    green.

- [x] 🟩 **Step 2: Lifecycle interlocks on launch** — formatting mid-extraction
  or mid-review is meaningless even when CAS makes it safe.
  - [x] 🟩 `launch_notes_formatter` refuses (409) when the run status is not
    terminal (`draft`/`running`).
  - [x] 🟩 Refuse (409) when `notes_review_tasks` has a `running` row for the
    run; add the mirror-image guard to the notes reviewer launch
    (`api/notes_reviewer.py`) so neither pass starts over the other.
  - **Verify:** route tests assert both 409s (and that terminal-status runs
    still launch). Existing reviewer route tests stay green.

- [x] 🟩 **Step 3: Code-quality findings (#4, #5, nits)** — no behavior change
  beyond the `blocks` fix.
  - [x] 🟩 Collapse the triplicated parse → confidence → sheet-match → apply
    block in `run_notes_formatter` into one `_validate_and_apply` helper
    (initial / repair / self-check paths share it).
  - [x] 🟩 `{"blocks": "all"}` in `notes/format_patch.py::_resolve_target`
    excludes elements with a `<table>` ancestor.
  - [x] 🟩 Nits: reuse `config` at `api/notes_formatter.py:80`; direct
    attribute access instead of `getattr(server, "NOTES_FORMATTER_…")`; comment
    on the (intentionally redundant) numeric-token check in
    `notes/format_verify.py`.
  - **Verify:** new unit test for the `blocks` table-descendant exclusion; full
    `tests/test_notes_format_patch.py` + `test_notes_formatter_routes.py`
    green with identical outcomes (helper refactor is invisible).

### Phase 2: Recoverability + Taxonomy (schema v27)

- [x] 🟩 **Step 4: v27 migration** — one bump carrying everything Phase 2/3
  needs on disk.
  - [x] 🟩 `notes_format_snapshots` table: `(run_id, sheet, row)` unique, `html`
    pre-format payload, FK → runs ON DELETE CASCADE, per-run index. Pure
    CREATE TABLE IF NOT EXISTS walk-forward.
  - [x] 🟩 `_V27_MIGRATION_COLUMNS` on `notes_format_tasks`: `error_type TEXT`
    (nullable, no CHECK — same rationale as `runs.status`), `prompt_tokens` /
    `completion_tokens` / `cache_read_tokens` / `cache_write_tokens`
    (`INTEGER DEFAULT 0`, mirroring the v15 columns).
  - [x] 🟩 `CURRENT_SCHEMA_VERSION = 27`; v26→v27 block with the BEGIN
    IMMEDIATE + re-check discipline; re-read marker after the v26 block.
  - [x] 🟩 Update CLAUDE.md gotcha #11 with the v26 and v27 entries.
  - **Verify:** new `tests/test_db_schema_v27.py` (fresh init has the table +
    columns; a v25 and a v26 DB both walk forward; idempotent re-run). Existing
    `test_db_schema_v26.py` green.

- [x] 🟩 **Step 5: Snapshot + revert endpoint** — "safety is versioning": a
  verifier-passing but ugly style pass must be one click to undo.
  - [x] 🟩 Repo helpers: `save_notes_format_snapshot` (written ONCE per pass,
    before the first row write, overwriting the previous pass's snapshot for
    that sheet) + `fetch/restore` counterparts.
  - [x] 🟩 `POST /api/runs/{id}/notes-format/revert` (body: `{sheet}`): restores
    snapshot HTML into `notes_cells`, clears the task row to a terminal
    "reverted" state; 409 while a pass is `running`; 404 with no snapshot.
    Revert is pure-style (verifier guaranteed content equality) so it never
    needs its own content check.
  - **Verify:** route test — format (mock model), assert styled HTML in DB,
    revert, assert byte-identical pre-format HTML restored; revert-while-running
    → 409; revert-without-snapshot → 404.

- [x] 🟩 **Step 6: Structured error taxonomy** — branch on codes, not prose.
  - [x] 🟩 Vocabulary next to the formatter: `timeout · turn_budget ·
    low_confidence · validation_failed · wrong_sheet · model_error · restarted
    · reverted`.
  - [x] 🟩 Worker (`api/notes_formatter.py::_thread_main`) and
    `run_notes_formatter` failure returns set `error_type`;
    `reconcile_stale_notes_format_tasks` sets `restarted`; status endpoint
    returns it; `NotesFormatStatus` TS type gains the field.
  - **Verify:** route tests assert the exact `error_type` for the timeout,
    turn-budget, low-confidence, and restart-reconcile paths.

### Phase 3: Observability + Configuration

- [x] 🟩 **Step 7: Trace persistence** — a formatter pass must be debuggable
  after the fact, exactly like every other agent pass (gotcha #6).
  - [x] 🟩 Dump the conversation (all up-to-three `agent.run` passes, including
    the timeout/budget/exception failure paths via the `save_messages_trace`
    fallback pattern) to `{output_dir}/notes_format_{sheet}_trace.json`.
  - [x] 🟩 `GET /api/runs/{id}/notes-format/trace?sheet=…` serving it, with the
    same resolved-path-stays-under-`output_dir` guard the agent-trace endpoint
    uses.
  - **Verify:** tests assert the trace file exists after success AND after a
    forced failure; path-traversal attempt on the endpoint → 404/400.

- [x] 🟩 **Step 8: Token accounting** — the shared `RunUsage` already
  accumulates across passes; persist it instead of dropping it.
  - [x] 🟩 Write prompt/completion/cache token totals onto the task row (v27
    columns) at completion; status endpoint returns them; show a small
    "~N tokens" line in the format summary UI.
  - **Verify:** route test with a mocked usage object asserts the persisted
    totals round-trip through the status endpoint.

- [x] 🟩 **Step 9: First-class configuration** — stop silently borrowing the
  notes-reviewer model.
  - [x] 🟩 Add `notes_formatter` to `_AGENT_ROLES` (server.py:2743) so
    `XBRL_DEFAULT_MODELS["notes_formatter"]` round-trips through
    `/api/settings` + `/api/config` and the General settings tab; launch
    fallback chain becomes: request override → formatter default → run model →
    `TEST_MODEL`.
  - [x] 🟩 `XBRL_NOTES_FORMATTER_MIN_CONFIDENCE` env resolver (validate +
    clamp to [0,1], default 0.70) replacing the hardcoded constant.
  - **Verify:** `tests/test_settings_api.py` round-trip for the new role;
    resolver unit tests (bad value → default, out-of-range → clamped).
    (Deviation: no web-test change — no frontend surface renders per-role
    dropdowns for this role; the round-trip is backend-validated.)

### Phase 4: Frontend UX + Pinning Tests

- [x] 🟩 **Step 10: `rowSaveStatuses` unmount cleanup (finding #3)** —
  collapsing a section while a row is `dirty`/`failed` currently wedges the
  Format button at "Save pending".
  - [x] 🟩 The reporting effect in `CellRow` deletes its row's entry (or
    reports `idle`) on unmount.
  - **Verify:** vitest — edit a row, collapse the section, Format button
    re-enables.

- [x] 🟩 **Step 11: UX for skip / revert / in-progress**
  - [x] 🟩 Summary line renders `skipped_rows` ("N rows skipped — edited during
    formatting") and the token count.
  - [x] 🟩 "Revert formatting" button next to the summary (confirm dialog),
    wired to the Step-5 endpoint, refetches cells on success.
  - [x] 🟩 While a sheet's pass is `running`: banner over that sheet's editors
    ("Formatting in progress — edits made now are preserved and skipped") and a
    note that styling applies to preview + paste, not the Excel download.
  - **Verify:** vitest for all three states (skipped message, revert flow with
    mocked API, running banner).

- [x] 🟩 **Step 12: Frontend pinning tests for the core flow (finding #2)** —
  mocked `launchNotesFormatter` / `fetchNotesFormatStatus`.
  - [x] 🟩 Launch → polling → `done` → cells refetched; error → `role="alert"`.
  - [x] 🟩 Hydration on mount resumes a running pass / shows a finished one.
  - [x] 🟩 Save-pending gate disables the button with the tooltip.
  - **Verify:** `cd web && npx vitest run src/__tests__/NotesReviewTab.test.tsx`
    green (new tests + existing 46).

### Phase 5: Docs + Wrap-Up

- [x] 🟩 **Step 13: Documentation + full-suite gate**
  - [x] 🟩 Rewrite the CLAUDE.md gotcha #16 formatter paragraph: drop
    "prototype", document the production invariants (CAS skip semantics,
    snapshot/revert, interlocks, taxonomy, panel-only scope) and the full
    pinning-test list; gotcha #11 already updated in Step 4.
  - [x] 🟩 AGENTS.md "don't let extraction/reviewer agents rewrite formatting"
    rule already landed with the prototype — confirm wording still holds.
  - [x] 🟩 Update the auto-memory `notes_wysiwyg_formatting` /
    formatter-related memory with the production status.
  - **Verify:** full `./venv/bin/python -m pytest tests/ -q` and
    `cd web && npx vitest run` green — the definition of done for the branch.

## Recorded Deviations (post-review, 2026-07-02)

Findings from the two-axis branch review, either fixed or accepted:

- **Step 7 trace fallback (accepted):** the trace is written after every
  COMPLETED `agent.run` pass — a timeout/turn-budget raised during the FIRST
  pass leaves no trace, because `agent.run` (unlike the coordinator's
  `agent.iter`) exposes no partial message history on the raised exception.
  Later-pass failures keep the completed passes' trace, which is the common
  case. Honoring the clause exactly would mean migrating to `agent.iter` —
  deferred. Token totals likewise persist 0 on raised-exception outcomes.
- **Step 6 vocabulary (extended):** `precondition_failed` was added beyond
  the spec's eight codes for the no-PDF / no-cells / missing-source-pages
  launch failures — more precise than overloading `validation_failed`.
- **Step 3 (partial by design):** `_screen_patch` collapses the parse /
  confidence / sheet gates; the `apply_sheet_patch` try/except remains at
  each call site because the three paths handle an apply failure
  differently (repair / return / return-with-revised-context).
- **Fixed post-review:** revert now re-runs `sanitize_notes_html` on
  snapshot HTML (gotcha #16 flow) and carries the reviewer interlock;
  token totals have a real persistence round-trip route test; the skipped-
  rows summary note has a vitest; `notes_formatter_trace` re-exported from
  server.py alongside its siblings.
- **Fixed post-review round 2 (concurrency audit, 3 HIGH findings):**
  1. The formatter's final write had a read-then-upsert window — now a
     statement-atomic conditional UPDATE (`cas_update_notes_cell_html`,
     `WHERE html = <launch snapshot>`) under `BEGIN IMMEDIATE`; the snapshot
     covers only rows actually written, in the same transaction.
  2. The formatter/reviewer interlocks were a cross-table TOCTOU (both
     launches could pass each other's "other not running" check, then each
     claim its own slot) — check-other + claim-mine now happen inside one
     `BEGIN IMMEDIATE` repo helper per direction
     (`claim_notes_format_task_guarded` / `claim_notes_review_task_guarded`).
  3. Revert could clobber a content edit made AFTER the formatter pass —
     each row is now gated on `verify_format_only(snapshot, current)`;
     content-edited rows are kept and reported in `skipped_rows`, and the
     whole revert runs under `BEGIN IMMEDIATE`.

## Rollback Plan

If something goes badly wrong:

- The feature is **additive and manual-trigger only** — no extraction-pipeline
  path depends on it. Reverting the commits restores the prototype (or, from
  the baseline commit, removes the feature entirely); no other subsystem needs
  changes.
- **Schema:** v26/v27 tables/columns stay in place as inert artifacts if code
  is reverted — the migration chain must keep replaying every step (gotcha #11
  convention, same as `doc_conversions`). Never roll the version marker back.
- **Data:** formatter writes only touch `notes_cells.html` on rows it styled,
  and every pass snapshots first (from Step 5 onward) — `POST
  …/notes-format/revert` per sheet restores pre-format HTML. For passes made
  before Step 5 lands, content is verifier-guaranteed intact; only styling
  would need manual reset ("Reset cell to theme").
- **State to check after a rollback:** `notes_format_tasks` rows left
  `running` (startup reconcile retires them), and any sheet whose summary
  reported changed rows (spot-check in the Review panel against the PDF).
