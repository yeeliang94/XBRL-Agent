# Implementation Plan: Reviewer Self-Verify — Review Follow-ups

**Overall Progress:** `100%`
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
- **The reviewer-shared surface moves to a new `notes/detectors.py` (Step 1, scope
  expanded with user approval).** The reviewer didn't import just `load_sidecar_entries`
  from the dead-but-green `notes/validator_agent.py` — it imported a *bundle* of 9
  names (the 5 detectors + the three loaders + `_render_single_page`). Moving one
  wouldn't de-risk the deletion, so the **whole** pure-detector surface moved to a
  new neutral module (not `notes/persistence.py` — these are detectors, not
  persistence). `validator_agent.py` re-exports them so its own code + tests are
  untouched until it's deleted.

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

- [x] 🟩 **Step 3: `move_notes_provenance` refs nit — investigated, resolved as "intentional, document + pin"** (NO behavior change). Investigation showed the nit is inert: `upsert_notes_provenance` already collapses `[]`→SQL NULL on write (`if source_note_refs`), `fetch_notes_provenance` normalizes NULL→`[]` on read, and the detectors treat empty/absent refs identically. So `[]`≡`None`≡NULL is a subsystem-wide invariant; "fixing" `move` alone would be a no-op (upsert re-collapses) and making it meaningful would require changing that invariant for zero functional gain.
  - [x] 🟩 Added a clarifying comment in `move_notes_provenance` so a future reader doesn't "fix" the intentional collapse.
  - [x] 🟩 Pinned the behavior: new `test_move_notes_provenance_empty_refs_round_trip` asserts an empty-refs row moves and reads back as `[]` (never `None`).
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_notes_reviewer_self_verify.py -q -k "provenance or move"` → **4 passed**.

### Phase 2: DRY the cross-check scoping (horizontal split — shared helper first, then migrate each consumer one at a time)

- [x] 🟩 **Step 4: Add the shared `resolve_check_scope` helper (pure addition, no call-site changes)** — single source of truth for "(statement, variant) pairs → template_ids + statements_to_run + variants".
  - [x] 🟩 Added `CheckScope(template_ids, statements_to_run, variants)` dataclass + `resolve_check_scope(pairs, *, filing_level, filing_standard)` in `cross_checks/framework.py`.
  - [x] 🟩 Body mirrors the existing loop: `StatementType(...)` (skip pseudo-rows on `ValueError`), `template_path(...)` (skip on `ValueError`/`KeyError`), `_derive_template_id(...)`. Heavy deps lazy-imported inside the function.
  - [x] 🟩 `StatementType(...)` normalises both an enum and its `.value` string, so no separate getattr is needed (pinned by `test_enum_and_string_statement_type_resolve_identically`).
  - [x] 🟩 New `tests/test_check_scope.py`: pseudo-row skipped, unresolvable-variant skipped, enum-and-string both resolve, mixed input keeps valid + drops invalid, outputs mutually consistent.
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_check_scope.py -q` → **6 passed**; no consumer touched yet (helper unused).

- [x] 🟩 **Step 5: Migrate `_build_check_template_ids` to delegate to the helper** — covers BOTH the pipeline pass and `_recheck_from_facts` (which reaches it via `select_cross_check_backend`).
  - [x] 🟩 Reduced `_build_check_template_ids` to: filter `agent_results` to `status == "succeeded"`, call `resolve_check_scope`, return `.template_ids`. gotcha #21 note kept.
  - [x] 🟩 Skip-on-error semantics preserved — the helper applies the same `except (ValueError, KeyError)` swallow.
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_cross_checks.py tests/test_e2e.py tests/test_download_reexport.py -q` → **24 passed**.

- [x] 🟩 **Step 6: Migrate the reviewer's `run_verification_checks` to the helper** — removes the second copy of the loop.
  - [x] 🟩 Replaced the inline `for statement_type, variant in rows:` block with `check_scope = resolve_check_scope(rows, …)`; `check_config` reads `check_scope.statements_to_run`/`.variants` and `FactsContext` takes `check_scope.template_ids`. Dropped the now-unused `template_path`/`StatementType`/`_derive_template_id` imports.
  - [x] 🟩 Both scope sources unchanged: explicit in-memory `scope` arg vs DB `FACTS_BEARING_AGENT_STATUSES` fallback — only the mapping moved.
  - [x] 🟩 Kept the early `return []` on empty `check_scope.statements_to_run` so the false-green formatter guards still fire.
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_reviewer_self_verify.py tests/test_reviewer_pipeline.py tests/test_reviewer_agent.py -q` → **46 passed** (incl. `test_explicit_scope_overrides_unfinalized_db_status`, `test_db_fallback_includes_completed_with_errors`).

- [x] 🟩 **Step 7: Drop `_recheck_from_facts`' hand-rolled scope loop (bonus dedup)** — built `statements_to_run`/`variants` by hand though the helper now yields them.
  - [x] 🟩 Build the `(stmt, variant)` pairs from the `FACTS_BEARING_AGENT_STATUSES`-filtered DB agents, call `resolve_check_scope` once, source `statements_to_run`/`variants` from it. Kept the `SimpleNamespace(status="succeeded", …)` `agent_results` list `select_cross_check_backend`/`_xlsx_provider` consume. **Equivalent in practice, not byte-identical** (review follow-up): the old loop added every StatementType-valid row unconditionally; the helper additionally requires `template_path` to resolve, so it skips a degenerate NULL/unresolvable-variant row the old loop kept — which a *succeeded* agent never produces (it always carries its extracted variant). In that unreachable case a needing-check surfaces as `pending` rather than the old `failed: workbook missing` (if anything more accurate). Comment tightened to say this precisely.
  - [x] 🟩 `if not agent_results: return None` early-out preserved.
  - **Verify:** ✅ `./venv/bin/python -m pytest tests/test_cross_checks.py tests/test_download_reexport.py tests/test_reviewer_routes.py -q` → **50 passed**.

### Phase 3: Final sweep
- [x] 🟩 **Step 8: Full-suite regression + dead-code check**
  - [x] 🟩 `./venv/bin/python -m pytest tests/ -q` → **2701 passed, 2 skipped, 0 failed** (the first full run surfaced one stale render-mock in `test_notes_reviewer_pipeline.py` — the Step-1 sweep missed it because `setattr(` and the function name sit on separate lines; fixed and re-run clean).
  - [x] 🟩 `py_compile` of all touched modules OK; orphan scan clean — the only `_derive_template_id` / `_tpl_path` references left are docstrings, and `resolve_check_scope` has exactly its 3 intended consumers. No code deleted beyond the relocated/replaced blocks.
  - **Verify:** ✅ clean full-suite run, no orphaned references.

## Rollback Plan
If something goes wrong:
- Each step is an isolated commit — `git revert <sha>` the offending step. The shared helper (Step 4) is additive, so reverting a migration step (5/6/7) leaves the helper unused but harmless.
- The risk-bearing change is Step 5 (it sits under the live pipeline cross-check pass). If a pipeline check result changes, diff `resolve_check_scope`'s output against the pre-refactor `_build_check_template_ids` for the same `agent_results` — the dict must be identical.
- State to check on any regression: a run's post-correction cross-check results in the ValidatorTab, and the `filled.xlsx` download for a `completed_with_errors` run (the re-export inclusion path) — the two user-visible surfaces the scoping feeds.
