# Implementation Plan: Batched Write Tools for Reviewer + Notes Reviewer

**Overall Progress:** `90%` — Phases 1–4 built + full suite green (3138
passed); only the live-run telemetry check (Step 9) + optional Phase 5 remain.
**PRD Reference:** none — scoped in conversation 2026-07-08 (turn-budget /
wall-clock relief; precedent is the 2026-07-07 coverage-tool batching in
gotcha #27)
**Last Updated:** 2026-07-08
**Branch:** `feat/batched-write-tools`

## Implementation notes (2026-07-08)

- **Grounding is PER ITEM for the notes edit/author batches**, not shared —
  a deviation from the "shared source_pages" wording in Steps 5–6 below.
  Distinct cells author different notes that cite different PDF pages, so each
  `NoteEditItem` / `NoteAuthorItem` carries its own `source_pages` + `evidence`.
  (Shared grounding stays correct for `clear_note_cells` — one duplication seen
  in one place — so that tool is unchanged.)
- **Singular forms removed** (repo precedent): `apply_fix` → `apply_fixes`,
  `edit_note_cell` → `edit_note_cells`, `author_note_cell` → `author_note_cells`.
  `mark_not_disclosed` keeps its name but now takes a list.
- **`move_note_cell` + `raise_flag` intentionally left single** (Key Decision).
- **Per-item isolation is guard-scoped, not schema-scoped (accepted).** Code-review
  raised that the new `ReviewerFixItem`/`NoteAuthorItem` models tighten
  `period`/`entity_scope` to `Literal[...]`, and pydantic-ai validates the WHOLE
  list argument before the tool body runs — so a malformed enum in ONE item
  fails the entire batch call at the validation boundary (items never execute).
  "One rejected item never blocks the others" is therefore true for the semantic
  no-plug / grounding **guards** (the tool-body path), not for a schema-malformed
  call. Accepted as documented behavior: models almost always emit valid enums,
  pydantic-ai retries the whole call with the validation error, and the tightening
  itself is a safety win (it rejects scope typos that the old free-`str` `apply_fix`
  silently passed to `FactWrite`).
- **Two correctness fixes made during implementation** (both caught independently
  and confirmed by code-review): (1) `_summarize_batch` now counts an item as
  applied only when it starts with `ok` — an `error:` outcome from
  `apply_reviewer_fix`'s exception path is reported as not-applied, never bucketed
  as success (pinned by `test_summarize_batch_counts_error_line_as_not_applied`);
  (2) the notes edit/author batch wraps each `_do_write` in `_safe_do_write` so an
  unexpected mid-batch exception becomes a per-item `rejected:` line instead of
  aborting siblings and losing the report (pinned by
  `test_author_note_cells_isolates_unexpected_error_per_item`).

## Summary

The reviewer agent writes fixes one `apply_fix` call per turn, and the notes
reviewer writes prose one `edit_note_cell` / `author_note_cell` call per turn.
Every turn costs a full model round-trip against hard budgets (40-iteration
cap, reviewer 8–25 turn cap, 300s wall-clock — the exact budget that already
timed out the notes coverage pass before its tools were batched). This plan
gives the remaining single-shot **write** tools the same list-shaped batch
form the codebase already proved safe with `clear_note_cells` /
`resolve_coverage_notes` / `verify_subnotes`: one tool call carries many
items, each item is validated **independently** through the existing
grounding / no-plug guards, and the agent gets a per-item outcome report.

## Key Decisions

- **Follow the repo's own batching precedent, including removal of the
  singular forms.** The 2026-07-07 consolidation replaced
  `clear_note_cell` → `clear_note_cells` and deleted the singular variants
  because they had the identical activation scenario. We do the same:
  `apply_fix` → `apply_fixes` (list), and the notes write tools gain
  list-shaped forms. Keeping both forms would invite the model to keep
  using the one-per-turn path.
- **Safety model is unchanged — per-item guards, not per-batch.** Each item
  in a batch runs through the same `apply_reviewer_fix` (no-plug guard,
  grounding requirement, gotcha #17) or `_do_write` (grounding + snapshot
  latch) as today. One rejected item never blocks or poisons its siblings;
  the return message reports each item's ok/rejected verdict so the model
  can re-investigate only the failures.
- **`move_note_cell` and `raise_flag` stay single-shot.** Moves are rare
  one-off placement fixes with a two-coordinate schema; flags are
  deliberately sparing. Batching them adds schema complexity for near-zero
  turn savings.
- **Read-only tools (`read_facts`, `find_candidate_rows`,
  `trace_cascade_source`) are OUT of scope** unless approved separately —
  listed as an optional cut-line phase at the end. The agreed goal was the
  write path.
- **Prompt + tool change land in the same commit as their pinning tests**
  (repo rule: the bar for "done" is the pinning test).

## Pre-Implementation Checklist

- [ ] 🟩 Confirm scope: rename-and-remove singular forms (repo precedent)
      vs. keep singular alongside — plan assumes rename-and-remove
- [ ] 🟩 On `main`, working tree clean (verified 2026-07-08); no conflicting
      in-progress branch touches `correction/reviewer_agent.py` or
      `notes/reviewer_agent.py`
- [ ] 🟩 Run baseline: `./venv/bin/python -m pytest tests/test_reviewer_agent.py
      tests/test_reviewer_tools.py tests/test_notes_reviewer_tools.py
      tests/test_notes_reviewer_authoring.py -q` green before touching code

## Tasks

### Phase 1: Reviewer agent — batch the fact writes

- [ ] 🟩 **Step 1: `apply_fixes` (list-shaped) replaces `apply_fix`** — the
      main turn-burner. One call carries a list of fix items
      (`concept_uuid`, `value`, `reason`, `evidence`, optional `period` /
      `entity_scope` / `children_status`); each item routes through
      `apply_reviewer_fix` independently (guards untouched);
      `writes_performed` increments per ok item; return is a per-item
      ok/rejected summary in the `_summarize_batch` style.
  - [ ] 🟩 Define the item shape (pydantic model or TypedDict) in
        `correction/reviewer_agent.py`
  - [ ] 🟩 Register `apply_fixes` tool; delete the singular `apply_fix`
        registration; empty-list input returns a
        "rejected: pass a non-empty list" message (mirrors
        `clear_note_cells`)
  - [ ] 🟩 Docstring: "always pass a list (a single fix is one-element)",
        per-item grounding wording preserved verbatim from today's
        docstring (evidence MUST cite the PDF page; never plug)
  - **Verify:** new tests in `tests/test_reviewer_tools.py` — (a) a 3-item
    batch where item 2 hits the no-plug guard: items 1+3 land, item 2
    rejected, summary names each; (b) empty list rejected; (c)
    `writes_performed` counts only the ok items. Run
    `./venv/bin/python -m pytest tests/test_reviewer_tools.py -q` → green.

- [ ] 🟩 **Step 2: `mark_not_disclosed` takes a list** — same treatment:
      a list of `{concept_uuid, reason, evidence, period?, entity_scope?}`
      items, per-item guard, per-item report. Tool name can stay (the verb
      already reads naturally for many items).
  - [ ] 🟩 Convert signature + internal loop; empty-list rejection
  - **Verify:** batch test with one absent-line clear + one guard rejection
    in the same call, per-item outcomes asserted. Same test file, green.

- [ ] 🟩 **Step 3: sweep reviewer-side callers/tests for the old name** —
      `tests/test_reviewer_agent.py`, `test_reviewer_pipeline.py`,
      `test_reviewer_routes.py`, `test_reviewer_disposition.py`,
      `test_prompt_residual_plug_rule.py` reference `apply_fix`; update
      mocks/assertions to the batch form.
  - **Verify:** `./venv/bin/python -m pytest tests/test_reviewer_agent.py
    tests/test_reviewer_pipeline.py tests/test_reviewer_routes.py
    tests/test_reviewer_disposition.py -q` → green.

### Phase 2: Reviewer prompts teach the batch habit

- [ ] 🟩 **Step 4: update `prompts/reviewer.md` + `prompts/spot_check.md`** —
      every `apply_fix` mention becomes `apply_fixes`, plus one new line of
      guidance: "when you have diagnosed several independent fixes, submit
      them as ONE `apply_fixes` call, then run `verify_fixes` once." Keep
      the investigate→fix→verify rhythm and the no-plug wording untouched
      (gotcha #17 — do not soften).
  - [ ] 🟩 Grep both prompts for `apply_fix(` and singular phrasing
  - [ ] 🟩 Extend `tests/test_prompt_residual_plug_rule.py` (or the nearest
        prompt-pinning test) to assert the batch guidance line exists and
        the no-plug rule text survives verbatim
  - **Verify:** `./venv/bin/python -m pytest
    tests/test_prompt_residual_plug_rule.py -q` → green; manual read of the
    rendered prompt confirms no stale `apply_fix` reference.

### Phase 3: Notes reviewer — batch the prose writes

- [ ] 🟩 **Step 5: `edit_note_cells` (list of `{sheet, row, html}` items)** —
      shared `source_pages` + `evidence` ground the whole batch (same
      contract as `clear_note_cells`); each item routes through `_do_write`
      independently; per-item report. Singular `edit_note_cell` removed.
  - [ ] 🟩 Respect the existing read-batch cap idea: cap batch size (reuse
        `READ_CELLS_MAX_ROWS` or a sibling constant) with a
        "split the set" message
  - **Verify:** new tests in `tests/test_notes_reviewer_tools.py` /
    `test_notes_reviewer_authoring.py` — mixed-outcome batch (one ok, one
    ungrounded rejection), cap message, empty-list rejection. Green.

- [ ] 🟩 **Step 6: `author_note_cells` (list of `{sheet, row, html,
      note_num}` items)** — same shape and guarantees; singular removed;
      `move_note_cell` deliberately untouched.
  - **Verify:** same test files — authored batch lands `notes_cells` rows,
    tombstone/snapshot behaviour unchanged (assert snapshot latch fires
    once per pass, not per item). Green.

- [ ] 🟩 **Step 7: update `prompts/notes_reviewer.md`** — plural tool names
      + the same "batch independent writes into one call" guidance; sweep
      `tests/test_notes_reviewer_pipeline.py`, `test_notes_reviewer_routes.py`,
      `test_notes_reviewer_coverage.py`, `test_e2e.py` for stale names.
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_reviewer_pipeline.py
    tests/test_notes_reviewer_routes.py tests/test_notes_reviewer_coverage.py
    tests/test_notes_reviewer_authoring.py tests/test_notes_reviewer_tools.py -q`
    → green.

### Phase 4: Whole-suite gate + real-run sanity

- [ ] 🟩 **Step 8: full suite** — `./venv/bin/python -m pytest tests/ -n auto`
      (whole-suite gate per CLAUDE.md; ~60s parallelised).
  - **Verify:** 0 failures; frontend untouched so `web` tests not required,
    but run `cd web && npx vitest run` if any TS type for tool traces was
    touched (not expected).
- [ ] 🟥 **Step 9: one live reviewer pass** (operator step — needs a real PDF + API key; not runnable in CI) — run a document
      with known cross-check failures, open the run's Telemetry tab, and
      confirm (a) the reviewer finishes in visibly fewer turns than a
      comparable prior run, (b) the Review tab diff shows the same fixes it
      would have made one-by-one, (c) the trace file shows one `apply_fixes`
      call carrying several items.
  - **Verify:** turn count in `run_agent_turns` for the reviewer row is
    lower than the prior baseline; no `correction_exhausted` /
    wall-clock outcome.

### Phase 5 (OPTIONAL — needs explicit go-ahead, out of agreed scope):
read-only batching

- [ ] 🟥 `read_facts(concept_uuids: list[str])`,
      `find_candidate_rows(candidates: list[…])`,
      `trace_cascade_source(targets: list[…])` — same pattern, read-only so
      lower risk, but also lower payoff (models already interleave reads
      less rigidly). Only do this if reviewer turn telemetry after Phase 4
      shows reads still dominating.

## Rollback Plan

- **No DB schema change, no data migration** — this touches tool
  registration, prompts, and tests only. A straight `git revert` of the
  commit(s) restores the singular tools; nothing persisted by a batched run
  differs in shape from a single-write run (`run_concept_facts` /
  `notes_cells` rows are identical either way).
- If a live run misbehaves mid-rollout: the reviewer pass is already
  optional per run — set `XBRL_AUTO_REVIEW=0` (and skip manual re-review)
  to take the reviewer out of the path while reverting.
- State to check after any rollback: `reviewer_flags` and `notes_cells` for
  the affected run; "Revert to original" on the Review tab restores the
  pre-reviewer extraction snapshot if a batch wrote something wrong.
