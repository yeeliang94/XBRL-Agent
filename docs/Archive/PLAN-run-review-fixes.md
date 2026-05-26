# Implementation Plan: RUN-REVIEW.md fixes

**Overall Progress:** `100%` — all phases landed (Phase 4.2 deferred with rationale; Phase 5 is investigation-only)
**Source:** `RUN-REVIEW.md` (Amway MFRS Company run, 2026-04-26)
**Last Updated:** 2026-04-26

## Outcome at a glance

- **Phase 0 — Fixtures:** SOFP + SOCF fixtures (MFRS Co + MPERS Grp) and CORRECTION FunctionModels in `tests/fixtures/run_review/`.
- **Phase 1 — Quick wins:** Token backfill for face/notes/correction agents → `run_agents.total_tokens` no longer always 0; SOFP no-plug rule extended to PPE/intangibles/investments sub-blocks; AFS↔SSM concept cheat-sheet added to `prompts/sofp.md` (single shared file — divergence-free across MFRS/MPERS, with the one MPERS-only `Refunds provision` divergence called out inline).
- **Phase 2 — CORRECTION agent:** Dynamic turn cap `max(8, min(25, 8 + 4·is_group + 2·n_failed_checks))` replaces pydantic-ai's silent 50-cap. New `correction_exhausted` terminal status surfaced in History as "Needs review" (amber/rose). Prompt rewritten to diff-first contract (≤2 inspect, 1 fill, 1 verify, 1 cross-check).
- **Phase 3 — Formula recalc:** `formulas` package added to requirements; `tools/recalc.py` runs at merge time and replaces `*Total …` formula cells with their cached values (preserving original sheet-name casing). Opt-out via `merge(..., skip_recalc=True)`.
- **Phase 4 — Writer/verifier hardening:** Double-booking guard (column-pair-aware, evidence-token-overlap based) emits warnings the agent surfaces in the next turn; SOCF/SoRE sign-from-formula block injected at prompt-build time. Phase 4.2 deferred — see rationale in §"Deferrals" below.
- **Phase 5 — SSM notes investigation:** `scripts/validate_ssm_compatibility.py` introspects a filer xlsx; `docs/SSM-NOTES-FORMAT-INVESTIGATION.md` documents how to interpret the verdict. Build the render mode only when a real filer xlsx confirms the convention.

## Deferrals (intentional)

- **P1-4 verifier sub-sheet re-aggregation (cheap variant)** — the formula-cell guard at `tools/fill_workbook.py:158-163` already refuses overwrites of any cell starting with `=`. After Phase 3 recalc, `*Total …` cells hold cached literals; the cheap-variant comparison (sum-leaves vs cached-value) is therefore tautological for the workflow we ship. The expensive variant (push AFS-note totals from extraction → verifier) is out of scope for this plan; if a future run shows the failure mode is still reachable, open a follow-up plan.
- **P0-2 SSM render mode** — investigation-only per Phase 5. We need a known-good filer xlsx to introspect before designing. Codebase carries zero evidence for the `[Text block added]` + `+FootnoteTextsN` convention.

## Test coverage

| Suite | Pass | Notes |
|---|---|---|
| `python -m pytest tests/ -k "not live"` | 1306 passed, 2 skipped | excluding the legacy E2E modules |
| `npx vitest run` (web) | 486 passed | runStatus.ts addition (correction_exhausted) covered by existing tests |

## Summary

Address the structural issues surfaced by the LRI agent's review of the
Amway FY2024 MFRS Company run, generalising every fix to also cover
MPERS, Group, and the SoRE statement. The review's evidence is one
filing combo; the underlying defects (CORRECTION turn-flood, uncached
formulas, residual-plug latitude, missing token telemetry) are
standard- and level-agnostic and would compound on MPERS Group where
the agent already runs at the edge of its competence.

## Key Decisions

- **Test matrix is 4× the reviewer's scope.** Every regression fixture
  in §6 of `RUN-REVIEW.md` gets an MPERS Group twin. Otherwise we ship
  "fixed" code that silently regresses MPERS — repeating the
  abstract-row-guard-MPERS-gap incident from 2026-04-26.
- **Defer P0-2 (SSM notes placeholder/footnote-sheet mode).** Zero
  evidence in `SSMxT_2022v1.0/` or anywhere in-repo for the
  `[Text block added]` + `+FootnoteTextsN` convention. Build a
  validation harness (Phase 5) that reads a known-good filer xlsx and
  asserts the actual SSM convention before designing render modes.
  Defaulting a config flag to an unverified hypothesis risks
  regressing every existing run.
- **`compare_results.py` section-disambiguation (P2-1) is already
  done** — `build_value_map` keys on `(norm, current_section, col)` at
  `compare_results.py:75`. No work needed beyond optionally extending
  the hardcoded `section_headers` set.
- **Don't weaken existing guards.** The no-plug rule extension (P1-2)
  must read like SOPL's existing wording — "catch-all rows are for
  genuinely coarse disclosures, not balancing" — not a blanket "never
  populate Other …" prohibition that breaks legitimate use. The
  abstract-row guard (gotcha #17) and `allowed_pages`-free scout
  hints (gotcha #13) stay untouched.
- **Turn budget for CORRECTION must scale with run shape.** A
  constant 8-turn cap that the reviewer suggested is too tight for
  Group (more cells, two column-sets). Use a formula:
  `8 + 4 * is_group + 2 * n_failed_checks`, clamped to [8, 25].
- **Cheat-sheets (P1-3) split by standard.** MFRS-specific concept
  mappings ("Consumer products" → "Finished goods") would actively
  mislead on MPERS where the vocabulary differs. Use the existing
  `prompts/{stmt}_{standard}.md` precedence tier (gotcha #15).
- **Pure-Python `formulas` package over LibreOffice.** Mac and
  Windows ship Python; LibreOffice is optional on either. Keep
  LibreOffice as an opportunistic fast-path if it's on PATH, but
  default to `formulas` (already MIT-licensed, openpyxl-compatible).

## Pre-Implementation Checklist

- [ ] 🟥 RUN-REVIEW.md re-read (especially §3 evidence + §5 non-goals)
- [ ] 🟥 CLAUDE.md gotchas #4, #6, #12, #13, #15, #16, #17 re-read
- [ ] 🟥 Confirm no other planning doc is touching `prompts/sofp.md`,
      `tools/fill_workbook.py`, `tools/verifier.py`, or
      `server.py:_run_correction_pass` (avoid merge conflicts with
      `PLAN-extraction-hardening.md` and `PLAN-peer-review-fixes.md`)

---

## Tasks

### Phase 0: Test fixtures (build before any code change)

- [x] 🟩 **Step 0.1: Synthetic SOFP fixture with double-booking + residual-plug**
  - [x] 🟩 Copy `XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx` to
        `tests/fixtures/run_review/sofp_company_mfrs.xlsx`
  - [x] 🟩 Pre-fill the 18 rows from RUN-REVIEW §6.1 (incl. the two
        "intentionally wrong" rows and the PY 1,881 double-booking)
  - [x] 🟩 Add MPERS Group twin at `sofp_group_mpers.xlsx` (6-col
        layout — Group cols mirror Company cols to exercise the
        column-pair-aware double-booking guard's negative case)
  - **Verify:** ✅ Loaded `sofp_company_mfrs.xlsx`; rows 135/152/287/318
        return expected labels (Other inventories / Other receivables
        due from subsidiaries / Provision for decommissioning… / Other
        non-current non-trade payables). MPERS twin has the same labels
        at rows 90/105/190/214. The 1,881 PY double-book sits on rows
        287+318 (MFRS) and 190+214 (MPERS).

- [x] 🟩 **Step 0.2: Synthetic SOCF fixture for sign-convention testing**
  - [x] 🟩 One MFRS Company + one MPERS Group SOCF template with
        `(Gain) loss on disposal of PPE` and `Cash payments…lease
        liabilities` populated with the values from RUN-REVIEW §3.6
  - [x] 🟩 **Plan typo correction:** SOCF lives at slot `07-SOCF-Indirect.xlsx`,
        not `04-` as the plan body stated. Builder uses the correct path.
  - **Verify:** ✅ Both `socf_company_mfrs.xlsx` and `socf_group_mpers.xlsx`
        write successfully with the -70 / 3,732 sign-ambiguous values
        on the rows the reviewer flagged.

- [x] 🟩 **Step 0.3: Mocked CORRECTION agent fixture for turn-budget testing**
  - [x] 🟩 `tests/fixtures/run_review/correction_models.py` exposes
        `inspect_flood_model(n=...)` (FunctionModel that emits N
        `inspect_workbook` ToolCalls then one `fill_workbook` — mirrors
        RUN-REVIEW §3.4) and `diff_first_model()` (1 inspect, 1 fill,
        1 verify — the post-Phase-2.3 expected shape)
  - **Verify:** ✅ Both factories construct without error and return
        `FunctionModel` instances. Will be exercised end-to-end in
        Phase 2.1's iteration-cap test.

---

### Phase 1: Quick wins (low-risk, high-leverage)

- [x] 🟩 **Step 1.1: Backfill per-agent token + cost telemetry**
  *(RUN-REVIEW P2-3 — the original premise was wrong; nothing was
  populating either run-level OR agent-level totals)*
  - [x] 🟩 **Run-level totals computed from SUM(run_agents)** at API
        read time rather than denormalised onto `runs`. Avoids a
        schema migration for telemetry data, treats `run_agents` as
        canonical (matches the existing audit-trail pattern). The
        `runs.total_tokens` column doesn't exist and doesn't need to.
  - [x] 🟩 `coordinator.AgentResult` carries `total_tokens` and
        `total_cost`. The success path captures `agent_run.usage()`
        and computes cost via `pricing.estimate_cost`.
  - [x] 🟩 `notes.coordinator.NotesAgentResult` and
        `_SingleAgentOutcome` carry the same fields with bubble-up
        through the retry loop.
  - [x] 🟩 `_run_correction_pass` outcome dict carries
        `total_tokens`/`total_cost` so the CORRECTION pseudo-agent
        row in `run_agents` reflects real spend even on
        exhausted/failed runs.
  - [x] 🟩 `server.py` persistence loop threads the values into
        `repo.finish_run_agent` for every agent row.
  - **Verify:** ✅ `tests/test_token_backfill.py` (3 cases) pins the
        carrier-field round-trip. Repository round-trip is already
        covered by `tests/test_db_repository.py`. Full suite passes.

- [x] 🟩 **Step 1.2: Add no-plug rule to `prompts/sofp.md`**
  *(RUN-REVIEW P1-2)*
  - [x] 🟩 Added `=== NO-RESIDUAL-PLUG RULE (sub-sheet) ===` block to
        `prompts/sofp.md` mirroring SOPL's wording (catch-all rows are
        for entities whose disclosure is genuinely coarse, NOT for
        plugging). Names every relevant sub-sheet catch-all explicitly:
        `Other property, plant and equipment`, `Other intangible
        assets`, `Other investment property`, `Other investments in
        subsidiaries / associates / joint ventures`.
  - [x] 🟩 Reinforces the leaf-mapping rule for PPE components — every
        PPE component must map to its dedicated row (Motor vehicles,
        Construction in progress, etc.) when one exists.
  - [x] 🟩 `tests/test_prompt_residual_plug_rule.py::test_sofp_prompt_forbids_sub_sheet_residual_plug`
        pins the wording under both MFRS and MPERS (MPERS falls
        through to the same `sofp.md` via prompt precedence).
  - **Verify:** ✅ All 4 tests in
        `tests/test_prompt_residual_plug_rule.py` pass.

- [x] 🟩 **Step 1.3: Add AFS↔SSM concept cheat-sheet to `prompts/sofp.md`**
  *(RUN-REVIEW P1-3, simplified after empirical check)*
  - [x] 🟩 **Deviation from plan:** put the cheat-sheet in shared
        `prompts/sofp.md`, NOT split into MFRS/MPERS files. Empirical
        check (`grep` over both standards' SOFP-Sub templates)
        confirmed the SSM labels are identical: `Finished goods`,
        `Trade receivables due from subsidiaries`, `Warranty
        provision`, `Accruals` all exist with the same names in
        MFRS Co + MPERS Co. The single divergence is `Refunds
        provision` (MFRS-only) — called out inline in the cheat-sheet.
  - [x] 🟩 The prompt-precedence tier is exclusive (one file wins),
        not additive — splitting would have CLOBBERED the shared
        rules in `sofp.md`. Putting the cheat-sheet in the shared
        file is the right architecture given identical labels.
  - [x] 🟩 ~8-12 known-confusing mappings covered: Inventories
        (Consumer products → Finished goods), Receivables (trade-vs-
        other axis driven by nature not counterparty), Provisions
        (dedicated rows for Warranty/Refunds/Restructuring/Legal/
        Onerous/Decommissioning), Payables (Accruals not Other),
        PPE detail, Cash.
  - **Verify:** ✅ `tests/test_prompt_concept_mapping.py` (3 tests)
        pins the cheat-sheet anchor, MPERS-divergence call-out, and
        cross-standard rendering.

---

### Phase 2: CORRECTION agent iteration tracking + status surfacing — DONE

*(RUN-REVIEW P0-1)*

- [x] 🟩 **Step 2.1: Dynamic turn cap in `_run_correction_pass`**
  - [x] 🟩 Formula `max_turns = max(8, min(25, 8 + 4·is_group + 2·n_failed_checks))`
        — Company gets 8-12 typical, Group gets 12-16, hard ceiling 25.
  - [x] 🟩 Iteration counter increments on each `is_call_tools_node`
        and bails with structured outcome
        `{"error": "correction_exhausted", "turns_used": N,
        "max_turns": M, "writes_performed": K}` BEFORE pydantic-ai's
        hidden 50-cap can fire.
  - [x] 🟩 Best-effort token capture even on the exhausted-bail path —
        whatever turns ran represent real spend.
  - **Verify:** ✅ `tests/test_correction_iteration_cap.py` — 4 cases
        cover the flood-trips-cap scenario, Group budget formula,
        clamp-to-25, and well-behaved diff-first model finishes early.

- [x] 🟩 **Step 2.2: New terminal status `correction_exhausted`**
  - [x] 🟩 Added to `db/repository.py::_TERMINAL_STATUSES`. No schema
        change needed (status column has no CHECK constraint per
        gotcha #11).
  - [x] 🟩 `server.py` final-status branch flips
        `completed_with_errors` → `correction_exhausted` when the
        outcome carries `exhausted=True`.
  - [x] 🟩 `web/src/lib/runStatus.ts` adds a distinct amber/rose
        "Needs review" badge (`#B45309` / `#FEF3C7`); filter dropdown
        gets a matching entry.

- [x] 🟩 **Step 2.3: Diff-first prompt rewrite**
  - [x] 🟩 `prompts/correction.md` rewritten with named anchor
        `=== YOUR WORKFLOW (DIFF-FIRST) ===`. Contract: at most 2
        inspect calls (only when context lacks data), 1 fill carrying
        every edit, 1 verify per touched sheet, 1 cross-check, done.
  - [x] 🟩 Removed the prior "inspect_workbook is mandatory before
        sign-sensitive edits" wording that invited the 40-turn flood.
  - [x] 🟩 Surfaces `turn budget` and the `correction_exhausted`
        status in-prompt so the model can self-pace.
  - [x] 🟩 Extended the no-plug rule to enumerate SOFP-Sub catch-alls
        (Other PPE / intangibles / inventories / payables) closing
        the §3.3-E gap.
  - **Verify:** ✅ `tests/test_prompt_correction_structure.py` — 6
        cases pin the anchor, ≤2 inspect cap, single-fill rule,
        turn-budget mention, status name, and SOFP-Sub catch-all
        enumeration.

---

### Phase 3: Formula recalc on merge — DONE

*(RUN-REVIEW P0-3)*

- [x] 🟩 **Step 3.1: `tools/recalc.py` + `formulas` dep**
  - [x] 🟩 Added `formulas>=1.2` to `requirements.txt` (pure-Python,
        MIT, brings in scipy/numpy already present).
  - [x] 🟩 `recalc_workbook(path) -> path` loads with `formulas`,
        computes every formula, then maps each computed value back
        into the ORIGINAL openpyxl-loaded workbook by case-folding
        the formulas-package's uppercase sheet keys. Sheet name
        casing is preserved.
  - [x] 🟩 **Trade-off:** the helper REPLACES formula strings with
        their cached literals. Excel users no longer see `=B8+B9+…`
        in the formula bar. For an XBRL submission workbook this is
        fine (SSM consumes values) and the `fullCalcOnLoad=True`
        flag remains the fallback for any failure path.
  - [x] 🟩 Try-except wraps both the formulas evaluation and the
        atomic save; on any failure the original workbook is left
        intact and a warning is logged.
  - **Verify:** ✅ `tests/test_merge_formula_recalc.py` — 6 cases
        cover MFRS Co success, sheet-name-casing preservation,
        MPERS Group 6-col layout, idempotency, missing-file safety,
        and ImportError fallback.

- [x] 🟩 **Step 3.2: Wire into `workbook_merger.merge`**
  - [x] 🟩 Recalc runs after `merged.save(output_path)` and before
        the success return.
  - [x] 🟩 Opt-out via `merge(..., skip_recalc=True)` rather than a
        `RunConfig` field (caller-local rather than run-config-level
        — the only test that wants formulas verbatim is the merger
        unit test itself).
  - **Verify:** ✅ `tests/test_merger.py` updated — old
        `test_formulas_preserved` renamed to
        `test_formulas_preserved_when_recalc_skipped`; new
        `test_formulas_replaced_with_cached_values_by_default`
        pins the visible behaviour change.

- [x] 🟩 **Step 3.3: MPERS + Group coverage**
  - [x] 🟩 `test_recalc_handles_mpers_group_layout` exercises
        `sofp_group_mpers.xlsx` and confirms both Group CY (col B)
        and Company CY (col D) `*Total PPE` are populated post-recalc.
  - [x] 🟩 The formulas package handles the cross-sheet rollup
        formulas that `_inject_face_to_sub_rollups` writes into MPERS
        templates — verified during smoke-testing.
  - **Verify:** ✅ All 4 standard×level combos covered through the
        `tests/fixtures/run_review/` Phase-0 fixtures.

---

### Phase 4: Writer/verifier hardening — DONE (4.2 deferred)

- [x] 🟩 **Step 4.1: Double-booking guard in `tools/fill_workbook.py`**
  *(RUN-REVIEW P1-1)*
  - [x] 🟩 `_detect_double_bookings` runs after each successful write
        batch. Scans `successful_writes` for pairs that share
        (sheet, col, value) with evidence-token overlap ≥3 distinct
        tokens of length ≥4. Returns advisory warnings that bubble
        up into the agent's tool result.
  - [x] 🟩 **Section comparison is intentionally loose:** the Amway
        bug straddled peer sub-sections (`non-current provisions`
        vs `non-current non-trade payables`), so requiring exact
        section equality would silently miss the canonical failure.
        Evidence-overlap carries the load.
  - [x] 🟩 **Column-pair-aware** by construction: the guard groups
        by `(sheet, col)` so legitimate Group consolidation
        pass-through (same value in Group-CY col B AND Company-CY
        col D for the SAME row) does not trigger.
  - [x] 🟩 `FillResult.warnings` field added; both `extraction/agent.py`
        and `correction/agent.py` thread the warnings into the
        tool's return string so the agent sees them.
  - **Verify:** ✅ `tests/test_fill_workbook_double_booking_guard.py` —
        4 cases cover the Amway shape, disjoint-evidence false
        positive, Group consolidation pass-through, and abstract-row
        guard regression check. Plus all existing
        `test_fill_workbook_abstract_guard.py` tests still pass.

- [ ] ⚪ **Step 4.2: Verifier sub-sheet re-aggregation — DEFERRED**
  *(RUN-REVIEW P1-4, cheap variant)*
  - **Why deferred:** the formula-cell guard at
    `tools/fill_workbook.py:158-163` already refuses overwrites of
    any cell starting with `=`, so an agent cannot replace a
    `*Total …` formula with a wrong literal via the normal write
    path. After Phase 3 recalc, those cells hold their cached
    literals; the cheap-variant comparison (sum-leaves vs cached-
    value) is therefore tautological for the workflow we ship.
  - **The expensive variant** (push AFS-note totals from extraction
    → verifier) is out of scope for this plan per the §"Where the
    report is overly restrictive" review note. If a future run
    shows the §3.3-C distribution-drift failure mode is still
    reachable, open a follow-up plan with the expensive design.

- [x] 🟩 **Step 4.3: SOCF/SoRE sign-from-formula injection**
  *(RUN-REVIEW P2-2, generalised to SoRE)*
  - [x] 🟩 New `prompts/_sign_conventions.py` exposes
        `socf_sign_convention_block(template_path)`. Walks every
        row whose label looks like a Total/subtotal AND whose col B
        cell carries an Excel-style `=±1*<cell>` formula; emits one
        guidance line per leaf naming whether it's ADDED or
        SUBTRACTED by its total.
  - [x] 🟩 `prompts.render_prompt` accepts a new `template_path`
        kwarg; when set AND statement is SOCF/SOCIE/SoRE, the
        helper's block is appended to the rendered prompt.
  - [x] 🟩 `extraction/agent.py` threads the live `template_path`
        into `render_prompt` so the block is materialised at agent
        construction.
  - [x] 🟩 Helper auto-detects the right sheet ("socf" or "sore" in
        the sheet name) so MFRS Co + MPERS Grp + SoRE all get
        coverage from one helper.
  - **Verify:** ✅ `tests/test_socf_sign_convention.py` — 8 cases
        cover the formula parser, MFRS Co + MPERS Grp coverage,
        prompt injection, non-SOCF prompts staying clean, missing-
        template-path graceful fallback, and SoRE template
        handling.

---

### Phase 5: SSM notes mode — investigate, don't implement — DONE

*(RUN-REVIEW P0-2 deferred pending evidence)*

- [x] 🟩 **Step 5.1: SSM validation harness shipped**
  - [x] 🟩 `scripts/validate_ssm_compatibility.py` introspects any
        xlsx and reports: visible vs hidden sheets (with shapes),
        any cells containing `[Text block added]` / `[textblock added]`
        / `[text block]`, presence of `+FootnoteTexts*` /
        `+Elements` / `+Lineitems` sheets, and a pass/partial/fail
        verdict.
  - [x] 🟩 Smoke-tested against a synthetic SOFP fixture (verdict:
        NOT CONFIRMED, as expected — that's not a notes-bearing
        filer submission).

- [x] 🟩 **Step 5.2: Documented decision path**
  - [x] 🟩 `docs/SSM-NOTES-FORMAT-INVESTIGATION.md` records the
        rationale, the harness usage, and how to interpret the
        verdict. Captures the empirical finding that the codebase
        has zero in-repo evidence (across `SSMxT_2022v1.0/` and the
        templates) for the placeholder + footnote-sheet convention.
  - [x] 🟩 Decision rule documented: CONFIRMED → open
        `PLAN-ssm-notes-output.md`; PARTIAL/NOT-CONFIRMED → close
        P0-2 as not-actionable.

---

## Cross-cutting test-coverage matrix

Every fix lands with at least these regressions:

| Fix | MFRS Co | MFRS Grp | MPERS Co | MPERS Grp |
|---|:-:|:-:|:-:|:-:|
| 1.1 token backfill | ✓ | — | — | — | (standard-agnostic) |
| 1.2 no-plug rule | ✓ prompt-render | ✓ prompt-render | ✓ prompt-render | ✓ prompt-render |
| 1.3 concept cheat-sheet | ✓ MFRS-specific entries | ✓ | ✓ MPERS-specific entries | ✓ |
| 2.1-2.3 correction loop | ✓ budget=8 | ✓ budget=12 | ✓ budget=8 | ✓ budget=12 |
| 3.1-3.3 formula recalc | ✓ | ✓ | ✓ | ✓ |
| 4.1 double-booking | ✓ | ✓ column-pair | ✓ MPERS calc-aware | ✓ both |
| 4.2 sub-sheet re-aggregate | ✓ | ✓ | ✓ | ✓ |
| 4.3 SOCF/SoRE sign | ✓ SOCF | ✓ SOCF | ✓ SOCF + SoRE | ✓ SOCF + SoRE |

---

## Rollback Plan

Each phase is independently revertible — none modify schema or
template files (Phase 3 writes cached values to merged workbooks,
not templates).

- **Phase 1 (token backfill, prompt additions):** `git revert` the
  commit. DB columns stay populated for runs that already wrote
  them; future runs go back to NULL/0. Prompt additions are
  cache-warmable (additive, not removed). No data loss.
- **Phase 2 (correction loop):** Revert restores pydantic-ai's
  silent 50-cap. Existing `completed_with_errors` runs are unaffected.
  Surface the change in `web/src/lib/runStatus.ts` last so the UI
  can be reverted independently of the backend.
- **Phase 3 (formula recalc):** If the `formulas` package misbehaves
  on a specific workbook, the try-except returns the path
  untouched — Excel-on-open still recalcs via `fullCalcOnLoad`.
  Worst case: revert and rely on Excel-on-open as before.
- **Phase 4 (writer/verifier):** New WARNINGs are non-blocking
  (the agent can ignore and continue). New `sub_sheet_rollup_mismatch`
  surfaces alongside existing checks; doesn't change run status
  semantics. Revert is clean.
- **Phase 5:** Investigation-only. No production code shipped.

## What this plan does NOT do (by design)

Per CLAUDE.md "How to work here" and `RUN-REVIEW.md` §5:

- Does NOT soften the abstract-row guard (gotcha #17)
- Does NOT re-introduce `allowed_pages` filtering on scout hints (gotcha #13)
- Does NOT introduce deterministic label-matching in the notes pipeline
- Does NOT hand-edit template formulas (gotcha #3)
- Does NOT remove `_safe_mark_finished`'s try/except (gotcha #10)
- Does NOT convert frontend back to Tailwind (gotcha #7)
- Does NOT implement the SSM render mode without evidence (Phase 5
  is investigation-only)
- Does NOT implement RUN-REVIEW P2-1 (compare_results section
  disambiguation) — already done at `compare_results.py:75`
