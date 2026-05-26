# Implementation Plan: MPERS Notes Pipeline Hardening (Option B, Red-Green TDD)

**Overall Progress:** `100%` (all 6 phases complete — real-PDF rerun pending user confirmation)
**Parent investigation:** debug session 2026-04-23 on run #105 (MPERS + FINCO PDF)
**Related docs:** `docs/MPERS.md`, `docs/NOTES-PIPELINE.md`, `CLAUDE.md` gotcha #15
**Last Updated:** 2026-04-23

## Summary

The MPERS wiring is complete for templates, registry, cross-checks, and
server routing but the **notes prompts and label matcher were never
MPERS-aware**. `render_notes_prompt` ignores `filing_standard`, so every
MPERS notes agent reads MFRS-flavoured instructions (sheet numbers
10-14, bare-concept label vocabulary, no mention of `[text block]`
suffix). The fuzzy matcher's 0.85 threshold silently rejects most short
MPERS labels because the `[text block]` suffix tanks the ratio. Result
on run #105: 3 of 9 disclosure notes landed on Sheet-13, plus three
separate SOCIE cross-check failures.

This plan follows strict Red-Green-Refactor TDD: every implementation
step is gated by a failing test that encodes the desired behaviour
*before* the code that satisfies it. The plan is intentionally scoped
to the notes pipeline + the SOCIE cross-check surface; broader MPERS
feature work is out of scope.

## Key Decisions

- **Suffix stripping over threshold lowering:** normalise `[text block]`
  / `[abstract]` / `[axis]` / `[member]` suffixes in both the writer's
  `_normalize` and the coverage validator's `_normalize_label`, keep
  the 0.85 threshold. Lowering the threshold risks cross-concept
  collisions (e.g. "other income" vs "other expense"); suffix removal
  is a deterministic taxonomy-level rewrite.
- **Seeded label catalog > agent-cached read_template:** render the
  full template label list into the system prompt on turn zero so the
  agent picks labels from an authoritative source instead of its
  model-weighted MFRS memory. Keep `read_template` as a fallback for
  re-retrieval mid-run.
- **Standard-branched prompt, not dual prompts:** `render_notes_prompt`
  takes `filing_standard`; the sheet-map + overlay blocks switch on it.
  Avoids a combinatorial blow-up of `_mfrs.md` / `_mpers.md` files and
  keeps all notes agents on one codepath.
- **SOCIE cross-check fix is in-scope:** the debug session surfaced
  three failed cross-checks driven by the same root cause family
  (MPERS layout divergence). Fixing them here keeps the rerun green;
  deferring would leave `completed_with_errors` even after the notes
  fix lands.
- **TDD discipline — one red test per behaviour:** each step opens
  with a failing test. No implementation touches code before the test
  is red for the right reason. No green step merges without its red
  test in the same commit.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm user approval of Option B scope (this plan)
- [ ] 🟥 Confirm the failing run artefacts are preserved for regression
      comparison: `output/9424b924-8e0a-496f-ae71-e53b3443b6ef/`
- [ ] 🟥 Confirm no in-flight PR touches `notes/agent.py`,
      `notes/writer.py`, `notes/coverage.py`, or
      `prompts/_notes_base.md`
- [ ] 🟥 Snapshot the current MPERS + MFRS template dirs' hashes so
      Phase 6 rerun diffs can be trusted

---

## Tasks

### Phase 1: Wire `filing_standard` into prompt rendering 🟩

**Goal:** `render_notes_prompt` becomes MPERS-aware without changing any
prompt *content* yet. Pure signature + plumbing change so later phases
have a channel to branch on.

- [x] 🟩 **Step 1.1: Red — add failing tests for `render_notes_prompt`
      signature and branching** (6 red tests; 7th default-behaviour test
      locked in green as regression guard).
- [x] 🟩 **Step 1.2: Green — thread `filing_standard` through
      `render_notes_prompt`**. `_render_sheet_map` + `_VALID_FILING_STANDARDS`
      added in `notes/agent.py`; MFRS map stripped from `_notes_base.md`.
      Call site updated.
- [x] 🟩 **Step 1.3: Refactor — `{{CROSS_SHEET:<topic>}}` tokens in
      per-template prompts**. `_apply_cross_sheet_tokens` + unresolved-
      token guard test added. `notes_listofnotes.md` and
      `notes_accounting_policies.md` migrated. 44 prompt tests green.

---

### Phase 2: Suffix normalisation in the writer + coverage validator 🟩

**Goal:** Short MPERS labels stop getting silently rejected by fuzzy
match purely because of the `[text block]` suffix.

- [x] 🟩 **Step 2.1: Red — failing tests for suffix-stripping normaliser**
      (8 red tests across writer + coverage sides.)
- [x] 🟩 **Step 2.2: Green — `notes/labels.normalize_label` drives both
      `notes/writer._normalize` and `notes/coverage._normalize_label`**.
      Stripping covers `[text block]`, `[abstract]`, `[axis]`,
      `[member]`, `[table]` via one pre-compiled regex. 82/82 existing
      writer+coverage tests pass; no regressions.
- [x] 🟩 **Step 2.3: Refactor — `notes/labels.py` is the single source
      of truth**. `grep [text block] notes/` shows the constant lives
      in `notes/labels.py:27` only; call sites are thin wrappers.

---

### Phase 3: Seed the template label catalog into the system prompt 🟩

**Goal:** Agents pick from the *actual* MPERS vocabulary instead of
their MFRS training-prior taxonomy vocabulary.

- [x] 🟩 **Step 3.1: Red — failing tests that prompt contains label
      catalog** (5 tests: presence, omission, standard-awareness,
      truncation, factory-level seeding).
- [x] 🟩 **Step 3.2: Green — render catalog in `render_notes_prompt`**
      via new `_render_label_catalog` helper; 180-row soft cap with
      `read_template` fallback footer; factory loads via
      `_load_template_label_catalog`.
- [x] 🟩 **Step 3.3: Red → Green integration — catalog + Phase 2
      normalisation compose**. `test_notes_agent_catalog_integration.py`
      exercises the run-#105 label set (bare-form writes against MPERS
      template). All 5 drop-casualties now resolve; 3 MFRS-only
      concepts stay correctly rejected (over-match guard).
- [x] 🟩 **Step 3.4: Cache on deps** — `NotesDeps.template_label_catalog`
      populated by the factory. The existing `read_template` tool's
      `template_fields` cache continues to bound workbook loads per
      sub-agent lifetime at exactly one. 429/430 notes tests pass,
      0 regressions.

---

### Phase 4: MPERS-specific overlay 🟩

**Goal:** tell the agent explicitly about (a) the `[text block]`
suffix convention, (b) the smaller MPERS concept set, (c) the
sheet-11-to-15 remapping.

- [x] 🟩 **Step 4.1: Red — failing tests for MPERS overlay content**
      (added to `test_notes_prompt_filing_standard.py`).
- [x] 🟩 **Step 4.2: Green — `_render_mpers_overlay()` emits a 3-point
      block on MPERS runs**. MFRS leak-test locks the gating. 11 prompt
      tests green.
- [x] 🟩 **Step 4.3: Audit — sweep** completed. `grep -i "mfrs"
      prompts/notes_*.md` returns zero matches; new parametrised guard
      test `test_notes_prompts_no_mfrs_leak.py` covers all 7
      notes-side prompts. Extraction prompts (sofp/socf/_base) left
      as-is per plan scope.

---

### Phase 5: SOCIE cross-check hardening for MPERS 🟩

**Goal:** the three failed cross-checks on run #105
(`sopl_to_socie_profit`, `soci_to_socie_tci`, `socie_to_sofp_equity`)
find their TCI / equity values on MPERS workbooks.

- [x] 🟩 **Step 5.1: Red — reproduce the three failures** in
      `tests/test_cross_checks_mpers_socie.py` with synthetic MPERS +
      MFRS SOCIE fixtures. 6 red (3 MPERS, 3 MFRS) on the pre-fix code.
- [x] 🟩 **Step 5.2: Green — `socie_column` honours `filing_standard`**.
      MPERS SOCIE reads col 2 (flat CY/PY layout); MFRS preserves the
      NCI-aware col 24/3 branch. `run_all` threads `filing_standard`
      via a try/except TypeError guard so older check signatures still
      run. 23/23 cross-check tests green.
- [x] 🟩 **Step 5.3: Refactor — `_MPERS_SOCIE_CY_COL` + `socie_py_column`**
      constants live in `cross_checks/util.py` alongside the existing
      `_SOCIE_TOTAL_COL` / `_SOCIE_RETAINED_COL` so the column policy
      is one grep away. No new helper module; same grouping as the
      pre-existing SOCIE constants.

---

### Phase 6: End-to-end validation 🟩 (live rerun pending)

**Goal:** prove the whole fix stack holds on the same PDF that
produced run #105.

- [x] 🟩 **Step 6.1: Red → Green — golden E2E regression lock**.
      `tests/test_e2e_mpers_notes.py` simulates sub-agents emitting
      the bare labels that run-#105 lost; asserts ≥ 8 rows land
      (baseline was 3) and no failures side-log appears. Green post-
      Phases 2-3.
- [ ] 🟥 **Step 6.2: Real rerun of the failing PDF** — requires user
      to trigger via the UI or CLI with API credentials. Baseline to
      beat: 3 rows landed on Sheet-13, 3 cross-checks failed. Target:
      ≥ 9 rows on Sheet-13, 0 cross-check failures (excluding
      SoRE-gated `not_applicable`).
- [x] 🟩 **Step 6.3: Regression sweep**. Full `pytest tests/` = 1043
      passed, 2 skipped, 0 failed. No pre-existing MFRS tests
      regressed. Live `-m live` pass deferred until Step 6.2.
- [x] 🟩 **Step 6.4: Documentation update**. `CLAUDE.md` gotcha #15
      extended with the 2026-04-23 hardening bullet. `docs/MPERS.md`
      gained a new "Notes Pipeline MPERS-Awareness" section covering
      prompt branching, label normalisation, and SOCIE column policy.
      `docs/NOTES-PIPELINE.md` cross-references the new section.

---

## Rollback Plan

If something goes badly wrong:

- **Revert path:** each phase lands as its own commit so
  `git revert <commit>` rolls back one phase without disturbing
  earlier/later work. Phase boundaries are the natural rollback units.
- **Canary check before merge:** run the FINCO MPERS rerun from Step
  6.2 on the pre-merge branch; if the Sheet-13 row count regresses
  below 3 (the current broken baseline), halt the merge.
- **State to inspect on failure:**
  - `output/<new-run-id>/notes12_coverage.json` — which sub-agents
    succeeded / their skip reasons
  - `output/<new-run-id>/NOTES_LIST_OF_NOTES_filled_payloads.json` —
    actual rows landed
  - `sqlite3 output/xbrl_agent.db "SELECT status, notes FROM runs
    ORDER BY created_at DESC LIMIT 1"` — run-level terminal status
  - `sqlite3 output/xbrl_agent.db "SELECT check_name, status, message
    FROM cross_checks WHERE run_id=<new-id>"` — cross-check outcomes
- **Irreversible risk:** none — all changes are code/prompt edits;
  templates under `XBRL-template-*/` are untouched. Audit-DB history
  for old runs is preserved.

## Rules

- Red-Green-Refactor strictly: no implementation commit without its
  failing test in the same or immediately-preceding commit.
- Per `CLAUDE.md`: don't edit `XBRL-template-*/backup-originals/`;
  don't run the MPERS generator without `--snapshot`; don't convert
  inline styles to Tailwind; don't add `allowed_pages` filtering;
  don't add deterministic label-matching to the notes pipeline (the
  label *catalog seeding* in Phase 3 is guidance, not deterministic
  matching — the agent still chooses).
- Scope stays inside the notes pipeline + SOCIE cross-check surface.
  Broader MPERS taxonomy updates, new face-statement variants, or
  template regeneration are out of scope for this plan.
- Each phase independently shippable: after Phase 2, the suffix fix
  alone lifts run #105 from 3 → ~7 landed rows even without later
  phases, so phases can land in sequence behind feature-flag-free
  incremental merges.
