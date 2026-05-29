# Implementation Plan: Reviewer Agent

**Overall Progress:** `97%` — Steps 1-17 done (all code + tests green: 1933+ backend, 633 frontend). Only Step 18 (manual e2e on a real PDF via `./start.sh`) remains — requires an interactive run with a live LLM.
**PRD Reference:** [docs/PRD.md](PRD.md)
**Last Updated:** 2026-05-29

## Summary

We're replacing the autonomous canonical correction pass with a **reviewer agent** that
investigates the face↔sub-sheet↔PDF chain to find the *root* cause of cross-check failures,
applies its grounded fixes into a **fully-reversible reviewer version** of the run, and
**flags** only the cases it's stuck on or where it disputes the first pass. Safety comes from
**versioning** (snapshot the original facts first; one-click revert) plus a **deterministic
no-plug guard** on the write tool — not from gating the agent's writes. The user reviews the
diff on a new **Review** tab and can revert, answer flags, and re-review.

## Key Decisions

- **Safety = versioning, not write-gating** — snapshot original facts before the pass; the
  reviewer writes freely into the live (reviewer) version; "Revert to original" restores the
  snapshot. Reversibility removes the risk that made the old pass scary.
- **Flags are narrow** — only `stuck` (can't reconcile/ground) or `disputes_prior` (thinks a
  prior agent erred). Grounded fixes are just shown in the diff, not flagged.
- **No-plug guard is code, not prompt** — `apply_fix` rejects ungrounded writes and residual
  plugs into catch-all/abstract rows (invariant #17), reporting the rejection back to the agent.
- **One combined "needs attention" surface** — existing `run_concept_conflicts` are fed into
  the reviewer as input; reviewer flags are the only user-facing list.
- **Backup = facts only** — `run_fact_snapshots` mirrors `run_concept_facts`; the workbook
  rebuilds from facts, so no file snapshot.
- **Cutover is outright** — the reviewer replaces `_run_canonical_correction_pass`; no toggle.
  Legacy non-canonical `_run_correction_pass` is untouched.
- **Scope = face + sub-sheets + PDF** — the reviewer does not write to notes-template sheets.

## Pre-Implementation Checklist

- [x] 🟩 All shaping questions resolved (PRD has no open questions)
- [x] 🟩 PRD approved / up to date
- [x] 🟩 **Fact-write entry point confirmed** (Flag 1 resolved 2026-05-29): `apply_fix` wraps
  `concept_model/facts_api.py::apply_fact(conn, run_id, FactWrite, commit=True)` — the
  same core the existing correction agent uses. Reuse the `_apply_correction_fact`
  (`correction/canonical_agent.py:199`) pattern: it catches `apply_fact`'s `HTTPException`
  and returns a `"rejected: …"` string instead of crashing the agent — exactly what the
  guard needs. Write with `actor="reviewer"`. **Grounding maps to the `FactWrite.evidence`
  field** (PDF page + quote) / `source`. **Cascade is a separate explicit call** —
  `apply_fact` upserts + journals but does NOT recompute totals; the reviewer pass must run
  `concept_model/cascade.py::recompute_after_turn(db_path, run_id)` after its writes, before
  re-export (mirrors `server.py:3521`).
- [x] 🟩 No conflicting in-progress work on `correction/`, `server.py` correction wiring, or
  `RunDetailView.tsx`

---

## Tasks

### Phase 1: Data foundation (schema v12)

- [x] 🟩 **Step 1: Add the two new tables + bump schema to v12** — the durable stores the
  whole feature rests on: the original-facts backup and the reviewer flags. **DONE.**
  - [x] 🟩 In `db/schema.py`: bump `CURRENT_SCHEMA_VERSION` 11 → 12.
  - [x] 🟩 Add `run_fact_snapshots` (mirror of `run_concept_facts` columns: `run_id`,
    `concept_uuid`, `period`, `entity_scope`, `value`, `value_status`, `children_status`,
    `source`, `evidence`, plus `snapshot_at`) with `CREATE TABLE IF NOT EXISTS` + an index on
    `run_id`.
  - [x] 🟩 Add `reviewer_flags` (`id`, `run_id`, `concept_uuid` nullable, `target_sheet`,
    `target_row`, `category` ∈ `stuck`/`disputes_prior`, `reasoning` TEXT, `pdf_page` nullable,
    `applied_fix` flag/ref nullable, `status` ∈ `open`/`answered`/`resolved`/`dismissed`,
    `human_answer` TEXT nullable, `created_at`, `updated_at`).
  - [x] 🟩 Followed the idempotent additive pattern (both are new tables → `CREATE IF NOT EXISTS`
    handles fresh + upgrade; v11→v12 block just bumps the marker, no `_V12_MIGRATION_COLUMNS`).
  - **Verified:** `tests/test_db_schema_v12.py` — 6 passed.
  - **Verify:** New `tests/test_db_schema_v12.py` (modeled on `tests/test_db_schema_v2.py`):
    `init_db()` on a fresh DB and on a seeded-v11 DB both report version 12 and expose both
    tables via `PRAGMA table_info`; re-running `init_db()` is a no-op. `python -m pytest
    tests/test_db_schema_v12.py -v` green.

### Phase 2: Snapshot + revert core (pure backend, no agent yet)

- [x] 🟩 **Step 2: Snapshot helper** — copy a run's current facts into `run_fact_snapshots`.
  - [x] 🟩 New function (e.g. `concept_model/versioning.py::snapshot_facts(db_path, run_id)`)
    that copies all `run_concept_facts` rows for the run into `run_fact_snapshots`,
    overwriting any prior snapshot for that run (snapshot is taken once, before the first pass).
  - **Verify:** Unit test: seed N facts, snapshot, assert `run_fact_snapshots` has exactly those
    N rows with identical values.

- [x] 🟩 **Step 3: Revert-to-original helper** — restore facts from the snapshot.
  - [x] 🟩 `revert_to_original(db_path, run_id)`: replace the run's `run_concept_facts` with the
    snapshot rows, then call `concept_model/cascade.py::recompute_after_turn(db_path, run_id)`.
  - [x] 🟩 Clear/mark reviewer flags + diff state for the run (move to `dismissed`/discarded).
  - **Verify:** Unit test: snapshot → mutate several facts → `revert_to_original` → every fact
    equals its snapshot value; cascade totals recomputed. (This is Success Criterion #1.)

- [x] 🟩 **Step 4: Diff helper** — compute original → reviewer changes for display.
  - [x] 🟩 `compute_review_diff(db_path, run_id)`: compare `run_concept_facts` against
    `run_fact_snapshots`, returning changed cells with `{concept, sheet, row, original, current}`
    plus `reason`/`grounding` pulled from the latest `concept_fact_events` row (actor = reviewer).
  - **Verify:** Unit test: snapshot → apply 2 changes via the facts API → diff returns exactly
    those 2 with correct old/new and the reason/grounding text.

### Phase 3: Reviewer agent + tools

- [x] 🟩 **Step 5: `trace_cascade_source` read tool** — lets the agent walk *down* from a
  failing face cell to the sub-sheet total + children that feed it.
  - [x] 🟩 Build on the shared `concept_uuid` / `concept_render_aliases` / `concept_edges`
    linkage (gotcha #21). Given a `(sheet,row)` or concept, return the source concept, its
    child rows + coefficients, and current values.
  - **Verify:** New `tests/test_reviewer_tools.py`: seed a face row aliased to a sub-sheet total
    with children; tool returns the sub total + child list. Green.

- [x] 🟩 **Step 6: `apply_fix` guarded write tool** — the only value-write path; reversible.
  - [x] 🟩 Model on `_apply_correction_fact` (`correction/canonical_agent.py:199`): build a
    `FactWrite(..., actor="reviewer", evidence=<grounding>, source=<reason>)` and call
    `apply_fact(conn, run_id, body)`; catch `HTTPException` and return a `"rejected: …"`
    string (don't crash the loop). `apply_fact`'s own kind-validation (e.g. observed literal
    aimed at a COMPUTED row) is the first line of rejection — the guard below layers on top.
  - [x] 🟩 **Guard (deterministic, runs before `apply_fact`):** reject the write if (a) no
    grounding (`evidence` empty and not the `arithmetic` marker), or (b) the target row is a
    catch-all/"Other"/abstract section-header row (reuse the header-fill / abstract detection
    from `tools/section_headers.py` / `tools/fill_workbook.py`, invariant #17). Return the
    same `"rejected: …"` string shape so the agent can read and re-investigate.
  - [x] 🟩 **Do NOT cascade per-write here** — `apply_fact` doesn't recompute; the pass runs
    `recompute_after_turn` once after the agent finishes (Step 9), keeping writes cheap.
  - **Verify:** `tests/test_reviewer_tools.py`: grounded fix to a leaf → applied; ungrounded fix
    → rejected; plug into an abstract/catch-all row → rejected with the no-plug message. Green.

- [x] 🟩 **Step 7: `raise_flag` tool** — write a row into `reviewer_flags`.
  - [x] 🟩 Accept `category` (`stuck`/`disputes_prior`), `reasoning`, optional cell + pdf_page,
    optional `applied_fix` link (set when the agent both fixed and flagged a dispute).
  - **Verify:** `tests/test_reviewer_tools.py`: calling the tool inserts an `open` flag with the
    right category/reasoning; a dispute-with-fix flag links to the applied change. Green.

- [x] 🟩 **Step 8: Reviewer agent module + prompt** — assemble the agent.
  - [x] 🟩 New `correction/reviewer_agent.py` + `prompts/reviewer.md` (PydanticAI, like
    `correction/canonical_agent.py`). Wire read tools (`read_facts`, `trace_cascade_source`,
    `view_pdf_pages`, `calculator`) + write tools (`apply_fix`, `raise_flag`). Prompt encodes:
    investigate root cause down the chain; apply grounded fixes; never plug; flag only when
    stuck or disputing the first pass.
  - [x] 🟩 Turn cap reusing the dynamic-cap pattern, staying below pydantic-ai's 50 (gotcha #18).
  - **Verify:** `tests/test_reviewer_agent.py` (modeled on `tests/test_correction_canonical.py`)
    with a mocked model: agent constructs, a scripted run stages one grounded fix + one flag,
    and the turn cap is < 50. Green.

### Phase 4: Server orchestration — replace the old pass

- [x] 🟩 **Step 9: Wire the reviewer into `run_multi_agent_stream`** — at the current
  `_run_canonical_correction_pass` call site (`server.py:3957`).
  - [x] 🟩 Emit `pipeline_stage: reviewing` (new label, gotcha #19 pattern).
  - [x] 🟩 **Snapshot first** (Step 2) before launching the reviewer.
  - [x] 🟩 Build the review packet: failing cross-checks (with `target_sheet`/`target_row`) +
    open `run_concept_conflicts` as investigation input; launch `reviewer_agent`.
  - [x] 🟩 Reuse the existing aftermath: if facts changed → `recompute_after_turn` (so totals
    reflect the reviewer's leaf fixes) → `_export_canonical_workbooks` → `merge_workbooks` →
    `mark_run_merged` → re-run cross-checks (`post_correction` phase).
  - [x] 🟩 Wrap in try/except emitting a `reviewer_exception` structured SSE error (gotcha #20);
    because the snapshot exists, a crash never leaves a half-written run.
  - **Verify:** Extend the mocked e2e (`tests/test_e2e.py`-style): a run with a seeded
    cross-check failure runs the reviewer, applies a grounded fix, re-exports/re-merges, and
    lands terminal. New `tests/test_reviewer_pipeline.py`. Green.

- [x] 🟩 **Step 10: Cut over — remove the old canonical correction pass** — outright, per
  decision.
  - [x] 🟩 Delete/disable `_run_canonical_correction_pass` and its call; keep
    `_run_correction_pass` (legacy non-canonical) intact.
  - [x] 🟩 Update `tests/test_correction_canonical.py` (remove/redirect to the reviewer) so the
    suite reflects the new path.
  - **Verify:** `grep` shows `_run_canonical_correction_pass` no longer invoked; full backend
    suite `python -m pytest tests/ -v` green.

### Phase 5: API endpoints

- [x] 🟩 **Step 11: `GET /api/runs/{id}/review`** — serve the diff + flags for the Review tab.
  - [x] 🟩 Returns `{ has_reviewer_version, diff: [...], flags: [...], cross_checks: [...] }`
    using Step 4's diff + a `reviewer_flags` query. Add alongside `concept_model/concepts_routes.py`.
  - **Verify:** New `tests/test_reviewer_routes.py` (modeled on `tests/test_concepts_routes.py`):
    seed a run with a snapshot + 1 applied fix + 1 flag; endpoint returns both correctly.

- [x] 🟩 **Step 12: `POST /api/runs/{id}/flags/{flag_id}/answer`** — attach free-text guidance.
  - [x] 🟩 Saves `human_answer`, moves flag `open → answered`.
  - **Verify:** `tests/test_reviewer_routes.py`: posting an answer updates status + text.

- [x] 🟩 **Step 13: `POST /api/runs/{id}/re-review`** — manual re-review trigger.
  - [x] 🟩 Accepts optional free-text guidance; relaunches the reviewer with current facts +
    open/answered flags + guidance in the packet (keeps the same original snapshot).
  - **Verify:** `tests/test_reviewer_routes.py`: trigger with and without guidance both start a
    pass; the original snapshot is unchanged (revert still goes to first extraction).

- [x] 🟩 **Step 14: `POST /api/runs/{id}/revert-to-original`** — one-button revert.
  - [x] 🟩 Calls Step 3, re-exports + re-merges, clears reviewer flags/diff.
  - **Verify:** `tests/test_reviewer_routes.py`: after revert, `GET /review` shows no reviewer
    version and the download equals the original extraction.

### Phase 6: Frontend Review tab

- [x] 🟩 **Step 15: Register the "Review" tab** — gated on canonical mode.
  - [x] 🟩 Add `"review"` to the `RunTabKey` union and the `tabs` array in
    `web/src/components/RunDetailView.tsx`, inside the `canonicalEnabled` block (next to Values).
  - **Verify:** `web/src/__tests__/RunDetailView.test.tsx`: tab appears when canonical on, hidden
    when off; arrow-key roving nav still scoped to `aria-label="Run detail sections"` (gotcha #7).
    `cd web && npx vitest run` green.

- [x] 🟩 **Step 16: Review tab UI** — diff + flags + controls.
  - [x] 🟩 New `web/src/components/ReviewTab.tsx` (inline styles, `theme.ts` tokens — gotcha #7),
    lazy-mounted like other heavy tabs. Shows: "reviewer version exists" indicator + **Revert to
    original** button; the original → reviewer **diff** (each row: old → new, reason, grounding,
    clickable PDF page); the **Flags** list (category + reasoning + per-flag answer box); an
    optional **guidance** textarea; a single **Re-review** button. Wire to Steps 11–14.
  - **Verify:** `web/src/__tests__/ReviewTab.test.tsx`: renders a diff row + a flag from mock
    data; Re-review and Revert buttons call the right endpoints; answer box posts. Plus one
    manual run end-to-end (next step).

### Phase 7: Integration, docs, manual verification

- [x] 🟩 **Step 17: Docs + invariant sync** — keep the context pack honest.
  - [x] 🟩 Update `CLAUDE.md`: gotcha #21 (reviewer replaces the canonical correction pass),
    schema section (v12 + the two tables), and the Review tab in gotcha #7's tab list. Update
    `docs/SYNC-MATRIX.md` for the new cross-file touch points.
  - **Verify:** Docs reflect reality; full backend + `cd web && npx vitest run` suites green.

- [ ] 🟥 **Step 18: Manual end-to-end on a real PDF** — the "looks right in Excel" check.
  - [ ] 🟥 Run `./start.sh`, process `data/FINCO-Audited-Financial-Statement-2021.pdf`, force a
    cross-check failure scenario, confirm: reviewing stage shows, diff appears on Review tab,
    a grounded fix lands, a stuck case flags, Re-review with guidance changes the result, and
    Revert restores the original download.
  - **Verify:** All six behaviors observed manually; downloaded workbook opens clean in Excel
    (gotcha #4 — open it, don't trust the diff tool).

---

## Rollback Plan

If something goes badly wrong:

- **Code:** the work is additive plus one deletion (the old canonical pass). Revert the
  feature commit(s) to restore `_run_canonical_correction_pass` and remove the reviewer wiring.
  Keep changes on a feature branch off `main` until Step 18 passes.
- **Schema:** the v12 tables are additive and idempotent — leaving them in place is harmless
  even after a code rollback (older code simply ignores them). Do **not** attempt to downgrade
  `CURRENT_SCHEMA_VERSION`.
- **Data safety net:** the feature's own design is the rollback for user data — every reviewer
  run is reverted via the `run_fact_snapshots` original, so a misbehaving reviewer can never
  destroy an extraction. If the snapshot/revert path itself is suspect, check that
  `snapshot_facts` ran before any `apply_fix` (Step 9 ordering) — that ordering is the load-
  bearing invariant for reversibility.
- **State to check on trouble:** `run_fact_snapshots` (does the original exist for the run?),
  `concept_fact_events` (actor=reviewer writes), `reviewer_flags` (orphaned open flags), and
  that the run landed in a terminal status (gotcha #10).
