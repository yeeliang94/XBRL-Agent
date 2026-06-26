# Implementation Plan: Reviewer Self-Verify — Review Follow-ups

**Overall Progress:** `25%`
**PRD Reference:** none — this plan implements the four follow-ups from the five-axis
code review of commits `0746a25` (feat: agents verify their own fixes) and
`3e6502a` (fix: close the `verify_fixes` false-green loophole). See CLAUDE.md
gotcha #21 (reviewer pass) and the `reviewer_verify_scope_false_green` memory.
**Last Updated:** 2026-06-26

> Replaces the previous (completed, 100%) PLAN.md for the **Design-System Code
> Sweep** — that work is done and preserved in git history (the same
> replace-in-place convention this repo's PLAN.md slot already uses; `docs/Archive/`
> is read-only, so no copy is made there).

## Summary
The close-the-loop reviewer self-verification shipped correct and well-tested.
This plan addresses the four non-blocking follow-ups the review surfaced: one
real refactor (de-duplicate the cross-check scoping logic now spread across
`server.py` and the reviewer) and three small isolated fixes (de-risk a future
validator deletion, refine one fail-safe message, and a provenance nit). Nothing
here changes externally observable behavior except the deliberate message
refinement in Step 2.

## Key Decisions
- **Scope = the 4 reviewed follow-ups only.** No new reviewer behavior, no new
  tools, no prompt changes. Surgical per CLAUDE.md "How to Behave Here".
- **The duplication is two-way, not three-way.** `_recheck_from_facts` already
  reuses `_build_check_template_ids` (via `select_cross_check_backend`). The genuine
  duplication is `_build_check_template_ids` (server.py:429) vs the inline loop in
  `run_verification_checks` (correction/reviewer_agent.py:1191). The shared helper
  collapses both, and lets `_recheck_from_facts` drop its own hand-rolled
  `statements_to_run`/`variants` loop as a bonus.
- **Shared helper lives in `cross_checks/framework.py`.** Both `server.py` and
  `correction/reviewer_agent.py` already import from it; it has no dependency on
  either, so no import cycle. Heavy imports (`statement_types.template_path`,
  `concept_model.parser._derive_template_id`) stay **lazy/inside the function**,
  matching the existing pattern, to keep `cross_checks` import-light.
- **Status filtering stays at the call site.** The helper takes already-resolved
  `(statement_type, variant)` pairs. The callers keep their own status filters
  (`"succeeded"`-only in-memory vs `FACTS_BEARING_AGENT_STATUSES` from the DB) —
  those legitimately differ and must not be flattened into the helper.
- **`load_sidecar_entries` moves to `notes/persistence.py`.** It is plain JSON/file
  I/O with no validator-specific deps, and `notes/persistence.py` already exists and
  is the natural home. The validator (`notes/validator_agent.py`) is dead-but-green
  and slated for deletion; moving this first is the prerequisite that keeps the
  notes reviewer working after that deletion.

## Pre-Implementation Checklist
- [x] 🟩 Scope confirmed with user (all four follow-ups selected)
- [x] 🟩 No conflicting in-progress work (working tree clean; both source commits landed)
- [x] 🟩 Baseline green — new + adjacent suites pass (28 + 130 at review time)

## Tasks

> Verify command convention (per `venv_interpreter_for_tests` memory): run pytest as
> `./venv/bin/python -m pytest …` — bare `python3` is a stale interpreter.

### Phase 1: Low-risk isolated fixes (independent; land first to keep the refactor isolated)

- [x] 🟩 **Step 1: Relocate the whole reviewer-shared surface out of the doomed validator** — de-risks the eventual `notes/validator_agent.py` deletion. **Scope expanded (user-approved): full move, not just `load_sidecar_entries`.** The reviewer imported a *bundle* of 9 names from `validator_agent` (5 detectors + `inventory_coverage_gaps` + `load_inventory_from_db` + `load_provenance_entries` + `_render_single_page`) plus the lazy `load_sidecar_entries`; moving only one would have left the reviewer broken after the deletion. Neutral home = new `notes/detectors.py` (not `notes/persistence.py` — these are pure detectors, not persistence).
  - [x] 🟩 Created `notes/detectors.py` with the full surface: `load_sidecar_entries`, `load_provenance_entries`, `load_inventory_from_db`, the 5 `detect_*`, `inventory_coverage_gaps`, `_render_single_page`, and their private helpers/constants (`_top_note_num`, `_subnote_key`, `_top_note_nums`, `_char_shingles`, `_jaccard`, `_CATCH_ALL_ROW_LABELS`, `_SHINGLE_SIZE`, `_OVERLAP_THRESHOLD`).
  - [x] 🟩 `notes/validator_agent.py` re-exports them all (`from notes.detectors import …`) so its remaining code + `tests/test_notes_validator_agent.py` keep their import surface unchanged.
  - [x] 🟩 Repointed `notes/reviewer_agent.py` (bundle import + 2 lazy imports) and `server.py:5337` to `notes.detectors`.
  - [x] 🟩 Retargeted render/sidecar monkeypatches in 5 test files — `_render_single_page` now resolves `render_pages_to_png_bytes` in `notes.detectors`'s namespace, so every render mock for a reviewer/validator path moved from `va.*` to `det.*` (`test_notes_reviewer_self_verify`, `_tools`, `_authoring`, `_routes`, `test_notes_review_provenance`).
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_notes_reviewer_self_verify.py tests/test_notes_reviewer_tools.py tests/test_notes_review_provenance.py tests/test_notes_validator_agent.py tests/test_notes_reviewer_authoring.py tests/test_notes_reviewer_routes.py -q` → **67 passed**; no live importer left on `from notes.validator_agent import load_sidecar_entries`.

- [x] 🟩 **Step 2: Refine `_format_verification` so an all-advisory result isn't mislabeled** — a result with only `warning` checks (no `passed`) was reported `INCONCLUSIVE`; a warning *is* an evaluation. (TDD — tests written red first.)
  - [x] 🟩 Changed the empty-evidence guard from `if not passed:` to `if not passed and not warnings:` so genuinely-empty/all-pending → `INCONCLUSIVE`, but warning-only → falls through.
  - [x] 🟩 Branched the fall-through so `passed==0` (warning-only) emits "✓ No cross-check is failing … (N advisory warning(s) only …)" instead of the false-green "all 0 … PASS".
  - [x] 🟩 Verified the correction path is unaffected: non-empty `original_failed_names` + empty `passed` → `unconfirmed` non-empty → still `NOT CONFIRMED` (pinned by the new `…with_open_target_is_not_confirmed` test).
  - [x] 🟩 Added the two formatter tests (warning-only + empty original → not INCONCLUSIVE / not "all 0"; warning-only + open target → NOT CONFIRMED).
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_reviewer_self_verify.py -q` → **15 passed** (incl. the unchanged false-green guards).

- [ ] 🟥 **Step 3: Preserve empty-vs-unknown refs in `move_notes_provenance`** (nit) — `[]` (note has no refs) currently collapses to `None` (refs unknown). (db/repository.py:998–1011)
  - [ ] 🟥 Change `refs = json.loads(refs_json) if refs_json else None` + `[str(x) for x in refs] if refs else None` to use an explicit `is not None` test so an empty list round-trips as `[]`, not `None`.
  - [ ] 🟥 Check `upsert_notes_provenance`: if it also `if refs`-collapses on write, the distinction is lost regardless — either fix it there too, or decide `[]`≡`None` is intentional and close this nit with a one-line comment instead.
  - **Verify:** extend `test_move_notes_provenance_relocates_and_preserves_refs` (or add a sibling) with a `source_note_refs=[]` row and assert it reads back as `[]`; `./venv/bin/python -m pytest tests/test_notes_reviewer_self_verify.py -q` green.

### Phase 2: DRY the cross-check scoping (horizontal split — shared helper first, then migrate each consumer one at a time)

- [ ] 🟥 **Step 4: Add the shared `resolve_check_scope` helper (pure addition, no call-site changes)** — single source of truth for "(statement, variant) pairs → template_ids + statements_to_run + variants".
  - [ ] 🟥 In `cross_checks/framework.py`, add a small dataclass `CheckScope(template_ids, statements_to_run, variants)` and `resolve_check_scope(pairs, *, filing_level, filing_standard) -> CheckScope`.
  - [ ] 🟥 Body mirrors today's loop exactly: per pair, `StatementType(value)` (skip pseudo-rows on `ValueError`), `template_path(...)` (skip on `ValueError`/`KeyError`), `_derive_template_id(...)`. Lazy-import the heavy deps inside the function (no module-level cycle).
  - [ ] 🟥 Accept either an enum or a string statement_type in each pair (callers pass both shapes today) — normalize via `getattr(st, "value", st)` before `StatementType(...)`.
  - [ ] 🟥 New unit test file `tests/test_check_scope.py`: pseudo-row skipped, bad-variant skipped, enum-and-string inputs both resolve, all three outputs populated and mutually consistent.
  - **Verify:** `./venv/bin/python -m pytest tests/test_check_scope.py -q` green; no other suite touched yet (helper is unused).

- [ ] 🟥 **Step 5: Migrate `_build_check_template_ids` to delegate to the helper** — covers BOTH the pipeline pass and `_recheck_from_facts` (which reaches it via `select_cross_check_backend`).
  - [ ] 🟥 Reduce `_build_check_template_ids` (server.py:429) to: filter `agent_results` to `status == "succeeded"`, call `resolve_check_scope`, return `.template_ids`. Keep the docstring's gotcha #21 note.
  - [ ] 🟥 Confirm skip-on-error semantics are byte-identical (same `except (ValueError, KeyError)` swallow) so a NotPrepared/variant-mismatch statement still degrades, never crashes.
  - **Verify:** `./venv/bin/python -m pytest tests/test_cross_checks.py tests/test_e2e.py tests/test_download_reexport.py -q` green (pipeline + recheck + re-export all exercise this path).

- [ ] 🟥 **Step 6: Migrate the reviewer's `run_verification_checks` to the helper** — removes the second copy of the loop.
  - [ ] 🟥 Replace the inline `for statement_type, variant in rows:` block (correction/reviewer_agent.py) with `scope = resolve_check_scope(rows, filing_level=…, filing_standard=…)`, then build `check_config` from `scope.statements_to_run`/`scope.variants` and pass `scope.template_ids` into `FactsContext`.
  - [ ] 🟥 Preserve the two scope sources unchanged: explicit in-memory `scope` arg vs the DB `FACTS_BEARING_AGENT_STATUSES` fallback — only the mapping moves, not the status filtering.
  - [ ] 🟥 Keep the early `return []` when `scope.statements_to_run` is empty (the false-green guard relies on empty being possible here; the formatter handles it).
  - **Verify:** `./venv/bin/python -m pytest tests/test_reviewer_self_verify.py tests/test_reviewer_pipeline.py tests/test_reviewer_agent.py -q` green — especially `test_explicit_scope_overrides_unfinalized_db_status` and `test_db_fallback_includes_completed_with_errors`.

- [ ] 🟥 **Step 7: Drop `_recheck_from_facts`' hand-rolled scope loop (bonus dedup)** — it still builds `statements_to_run`/`variants` by hand (server.py:618–636) though the helper now yields them.
  - [ ] 🟥 Build the `(stmt, variant)` pairs from the `FACTS_BEARING_AGENT_STATUSES`-filtered DB agents, call `resolve_check_scope` once, and source `statements_to_run`/`variants` for `check_config` from it. Keep the `SimpleNamespace(status="succeeded", …)` `agent_results` list that `select_cross_check_backend`/`_xlsx_provider` still consume.
  - [ ] 🟥 Confirm the `if not agent_results: return None` early-out still fires for a run with zero facts-bearing statements.
  - **Verify:** `./venv/bin/python -m pytest tests/test_cross_checks.py tests/test_download_reexport.py -q` green; manually confirm the recheck endpoint still returns rows for a `completed_with_errors` run.

### Phase 3: Final sweep
- [ ] 🟥 **Step 8: Full-suite regression + dead-code check**
  - [ ] 🟥 `./venv/bin/python -m pytest tests/ -q` — full backend suite green (review baseline: 2692 passed).
  - [ ] 🟥 `grep` for any now-orphaned helper or stale comment referencing the old inline loops; remove only what is provably unused (ask before deleting anything ambiguous, per the dead-code-hygiene rule).
  - **Verify:** clean full-suite run + no orphaned references.

## Rollback Plan
If something goes wrong:
- Each step is an isolated commit — `git revert <sha>` the offending step. The shared helper (Step 4) is additive, so reverting a migration step (5/6/7) leaves the helper unused but harmless.
- The risk-bearing change is Step 5 (it sits under the live pipeline cross-check pass). If a pipeline check result changes, diff `resolve_check_scope`'s output against the pre-refactor `_build_check_template_ids` for the same `agent_results` — the dict must be identical.
- State to check on any regression: a run's post-correction cross-check results in the ValidatorTab, and the `filled.xlsx` download for a `completed_with_errors` run (the re-export inclusion path) — the two user-visible surfaces the scoping feeds.
