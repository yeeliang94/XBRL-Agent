# Implementation Plan: Excel-free verification pipeline (item 32)

**Overall Progress:** `0%`
**Status:** design / charter → **implementation plan** (expanded 2026-06-13)
**PRD / context:** orchestration-hardening item 32; this file is the spec.
**Last Updated:** 2026-06-13

> This expands the original charter into an ordered, shadow-gated implementation
> plan. The charter's framing (Problem / End state / Invariants) is preserved
> below; each phase now has concrete steps, file targets, and a parity-proof
> **Verify** gate. Nothing here changes scope — it removes openpyxl from the
> *verification* path only (export stays xlsx, gotcha #21 / Non-goals).

---

## Summary

Facts are already DB-canonical (`run_concept_facts`, gotcha #21) and the
**cascade already persists every COMPUTED total back into `run_concept_facts`**
(`concept_model/cascade.py::_recompute_scope`, `source='cascade'`,
`value_status='observed'`). That single fact is the enabler: the *math truth*
the formula cells encode is already in the DB by concept uuid. This plan rewires
the three remaining xlsx consumers in the verification path — **cross-checks**
(32a), **`verify_totals`** (32b), and the agent's **`read_template`** summary
(32c) — to read the DB instead of opening workbooks, proving each equal to the
xlsx path on the e2e fixtures **before** the xlsx code is retired. The approach
throughout is the `eval/grader.py` uuid-join idiom: read facts keyed by
`(concept_uuid, period, entity_scope)`, scoped to the run's `template_id`.

## Why now / what it kills

- `_recheck_from_facts` (`server.py:400`) literally **rebuilds a workbook from
  DB facts just to run xlsx checks on it** (`_export_canonical_workbooks` at
  `server.py:162`) — the most telling symptom.
- Keeps gotcha #22's concurrent-save race class alive (load→save→load in the hot
  path).
- Keeps the DB↔xlsx split-brain alive (facts move, xlsx lags — items 11/12 added
  signals for exactly this).
- Every check is slow because it round-trips openpyxl (a reason item 16 had to
  move checks off the event loop at all).

## End state

A run completes **extraction → verification → cross-checks → review without
opening a single workbook**. openpyxl survives only at the *edges*: template
**import** (`concept_model/parser.py`), final **export**
(`concept_model/exporter.py`), **eval ingest** (`eval/ingest.py`), and
**generator scripts**. xlsx is generated **exactly once**, at export.

---

## Key Decisions

- **Read the cascade's persisted totals, not a re-derived sum.** The cascade
  already writes COMPUTED parents into `run_concept_facts`. Verification and
  cross-checks read those rows by uuid — they do **not** re-walk edges
  themselves. This keeps gotcha #3 (calc linkbase = math truth) intact: the
  edges drove the cascade write; we just read the result.
- **Shadow-first, retire-second, per phase.** During transition each migrated
  consumer runs **both** paths and asserts equality on the e2e fixtures. The
  xlsx path is deleted only after its fact-based replacement is proven equal.
  This is the load-bearing rule (charter "Pinning-test discipline").
- **Gate the authoritative source behind a per-phase env flag** during rollout
  (`XBRL_FACT_BASED_CHECKS`, `XBRL_FACT_BASED_VERIFY`, `XBRL_DB_READ_TEMPLATE`),
  consistent with the repo's `XBRL_*` flag pattern. Default off → flip on once
  shadow-diff is green → remove flag + xlsx path in the cleanup phase.
- **Message/format byte-compatibility is a hard contract.** Cross-check
  `message` strings, `CrossCheckResult` shape, `comparands_json` (v14), the SSE
  event payloads (gotcha #19), `VerificationResult` shape, the SOFP imbalance +
  no-residual-plug feedback wording (gotcha #17), and the `read_template`
  summary string must be **identical** to today. Refactors are invisible to the
  pinning tests.
- **`read_template` parity via a cached rendered summary, not field-by-field
  reconstruction.** `concept_nodes` carries `kind`/labels/coords but **not the
  literal formula text** the current summary prints (`[FORMULA: ='SOFP-Sub'!B39]`).
  Rather than re-derive that string from edges, precompute and cache the *whole*
  rendered summary at template-import time, keyed by `template_id`. `read_template`
  then fetches the cached string — byte-identical output, zero per-agent xlsx
  parsing. (See Open Question Q3.)
- **`verify_totals` / checks gain a `(db_path, run_id)` context.** Today they're
  file-path-only. The extraction agent already has `run_id` in `ctx.deps`; the
  post-extraction orchestrator already has both. No new plumbing to *find* the
  context, only to *pass* it.
- **No schema migration.** All phases read existing tables (`run_concept_facts`,
  `concept_nodes`, `concept_edges`, `concept_targets`, `concept_render_aliases`).
  One possible exception is a cached-summary store for 32c — see Q3; prefer an
  in-memory/derived cache over a new column if it holds.

---

## Architecture: current vs target data flow

```
CURRENT (verification round-trips xlsx):
  facts (DB) ──cascade──> facts incl. COMPUTED totals (DB)
       │                          │
       │   agent loop: read_template ──openpyxl──> summary string
       │   agent loop: verify_totals ──openpyxl(load, eval formulas)──> result
       ▼
  export_run_to_xlsx ──> {stmt}_canonical.xlsx ──merge──> filled.xlsx
                                                   │
  cross_checks: open_workbook ──openpyxl(load, eval formulas)──> results
  _recheck_from_facts: REBUILD workbook from facts ──> run xlsx checks   ← the smell

TARGET (verification reads DB by uuid):
  facts (DB) ──cascade──> facts incl. COMPUTED totals (DB)
       │
       ├─ read_template  ──> cached summary string (built once at import)
       ├─ verify_totals  ──> read COMPUTED totals from run_concept_facts by uuid
       ├─ cross_checks   ──> read facts from run_concept_facts by uuid
       └─ _recheck       ──> re-read facts (NO workbook rebuild)
       ▼
  export_run_to_xlsx ──> filled.xlsx   (xlsx generated EXACTLY ONCE, at export)
```

---

## Pre-Implementation Checklist

- [ ] 🟥 Confirm P1–P6 of orchestration-hardening have stabilised (charter
      scheduling condition) — or that xlsx races / split-brain bugs have
      recurred, justifying the start.
- [ ] 🟥 Green baseline: `./venv/bin/python -m pytest tests/ -q` and
      `cd web && npx vitest run` both pass on `main` before starting (memory:
      use `./venv/bin/python`, not bare `python3`).
- [ ] 🟥 No conflicting in-progress work on `cross_checks/`, `tools/verifier.py`,
      `extraction/agent.py`, `server.py` (the four hot files).
- [ ] 🟥 Confirm the e2e fixtures (`tests/test_e2e.py` mocked 5-agent pipeline)
      exercise a run that ends with populated `run_concept_facts` **including
      cascade-written COMPUTED totals** — this is the shadow-diff substrate.
- [ ] 🟥 Decide Q1–Q4 (Open Questions) with the user before Phase 1.

---

## Open Questions (resolve before/with the relevant phase)

- **Q1 (32a/32b):** Cross-checks/verifier reference rows by **label**
  ("total assets"). Facts are keyed by **uuid**. Resolve label→uuid by (a) a
  new `concept_nodes` label-substring lookup scoped to `template_id`, mirroring
  `find_value_by_label`, or (b) hard-pin each check to a stable concept anchor.
  **Recommend (a)** for parity with today's fuzzy matching; (b) is more robust
  but changes check authoring. Which?
- **Q2 (parity tolerance):** Cascade rounds to cents (`_money`); openpyxl
  evaluates formulas exactly. Both render via `:.2f`. Shadow-diff should compare
  at **display precision (2dp)** or within `_balance_tolerance`, not raw float
  equality. Confirm acceptable.
- **Q3 (32c):** Cache the rendered `read_template` summary where? Options:
  in-process LRU keyed by `template_id` (no schema change, rebuilt on boot from
  `concept_*` tables or a one-shot xlsx parse at import) **(recommended)**, or a
  new `template_summaries` table. The summary needs the literal formula text,
  which is NOT in `concept_nodes` — so a pure-DB reconstruction would need a new
  `concept_nodes.formula` column. Caching the string sidesteps that. Confirm
  no-schema-change is acceptable.
- **Q4 (cascade freshness for 32b):** `verify_totals` is an agent tool; the
  cascade runs at the coordinator turn boundary. Must verify reads **after** the
  cascade has run for the latest writes. Either (a) ensure the tool ordering
  guarantees cascade-before-verify, or (b) have `verify_totals` trigger a
  `recompute_after_turn` on entry. **Recommend (b)** for self-containedness
  (idempotent, cheap). Confirm.

---

## Tasks

### Phase 0: Foundation — shared fact-read + label-resolve + shadow harness

*No behaviour change. Build the read primitives and the parity harness that all
three migrations reuse. Ship-able on its own (pure additions, zero call-site
changes).*

- [ ] 🟥 **Step 0.1: Fact-read helper keyed by uuid** — one place to load a run's
      facts as `{(concept_uuid, period, entity_scope): (value, value_status)}`,
      scoped to a `template_id` set, reusing the `eval/grader.py::_gradeable_facts`
      idiom but **including COMPUTED** (cross-checks/verifier need totals).
  - [ ] 🟥 Add `read_run_facts(conn, run_id, template_ids, kinds=None)` to
        `concept_model/facts_api.py` (returns the dict above; `kinds=None`
        means all, including COMPUTED — distinct from grader's LEAF/MATRIX-only).
  - [ ] 🟥 Add `entity_scope`/`period` constants module or reuse the
        `Literal["CY","PY"]` / `Literal["Company","Group"]` already in
        `facts_api.py:70-71` (no constants module exists today — decide one).
  - **Verify:** unit test seeds facts + a cascade-written COMPUTED total, asserts
        `read_run_facts` returns the total row and the leaves, scoped correctly
        per `template_id`. `./venv/bin/python -m pytest tests/test_facts_read.py -q`.

- [ ] 🟥 **Step 0.2: Label→uuid resolver (Q1)** — given `template_id` + a label
      substring (+ optional period/scope), return `(concept_uuid, sheet, row,
      col)` so callers can both read the fact and build `Comparand`s with real
      coords.
  - [ ] 🟥 Implement against `concept_nodes` (render coords) with
        `concept_targets` / `concept_render_aliases` fallback, mirroring
        `cell_resolver.resolve_cell` scoping (`WHERE n.template_id = ?`) and
        `find_value_by_label`'s exact-then-substring, `*`-stripped matching.
  - **Verify:** unit test resolves "total assets" / "total equity and
        liabilities" on a real SOFP `template_id` to the same rows the xlsx
        `find_label_row` returns. Assert exact-match-wins-over-substring.

- [ ] 🟥 **Step 0.3: Shadow-diff harness** — a test utility that, given a run,
      executes a consumer **both ways** (xlsx and fact-based) and asserts the
      results are byte/format-equal at display precision (Q2).
  - [ ] 🟥 Harness loads the e2e fixture run, exposes `assert_parity(xlsx_result,
        fact_result)` comparing `message`, `status`, `expected`/`actual`/`diff`
        (at 2dp), `target_sheet`/`target_row`, and `comparands`.
  - **Verify:** harness self-test: feeding two identical results passes; a
        1-cent or wording difference fails loudly.

---

### Phase 1: 32a — Fact-based cross-checks

*Re-implement each check to read `run_concept_facts` by uuid. Keep the `run()`
contract, `CrossCheckResult` shape, message format, comparands, and SSE events
identical. Gate behind `XBRL_FACT_BASED_CHECKS`.*

- [ ] 🟥 **Step 1.1: New check entry point that takes DB context** — add a
      `run_facts(conn, run_id, tolerance, filing_level, filing_standard,
      template_ids)` alongside the existing `run(workbook_paths, ...)` on the
      `CrossCheck` protocol (`cross_checks/framework.py:129`). Do **not** remove
      `run()` yet.
  - [ ] 🟥 Framework's runner (`build_default_cross_checks` / the loop in
        `framework.py` + `server.py:3714,4043`) dispatches to `run_facts` when
        `XBRL_FACT_BASED_CHECKS` is on, else `run()`.
  - **Verify:** with flag off, every existing `tests/test_cross_checks.py` /
        `tests/test_cross_checks_impl.py` test passes unchanged.

- [ ] 🟥 **Step 1.2: Migrate the 6 checks** — `sofp_balance`,
      `sopl_to_socie_profit`, `soci_to_socie_tci`, `socie_to_sofp_equity`,
      `socf_to_sofp_cash`, `sore_to_sofp_retained_earnings`. Each reads its
      comparand values via Step 0.1/0.2 instead of `open_workbook` +
      `find_value_by_label`.
  - [ ] 🟥 Honour `applies_to_standard` (gotcha #15) — unchanged, gated in the
        framework before dispatch.
  - [ ] 🟥 Group dual-pass (gotcha #12): read `entity_scope='Group'` then
        `'Company'` facts instead of cols B/C then D/E. SOCIE block ranges
        (`SOCIE_GROUP_BLOCKS`) become entity_scope+period reads — no row-range
        math. MFRS-vs-MPERS SOCIE column branch (`socie_column`) disappears (it
        was a column-selection artifact; uuid read is column-agnostic).
  - [ ] 🟥 Build `Comparand`s with real `sheet`/`row` from Step 0.2 so
        `comparands_json` (v14) and the Review workspace click-to-cell stay
        intact.
  - [ ] 🟥 Preserve every `message` string verbatim (e.g. `"Group CY: assets
        (…) vs equity+liab (…), diff=…"`, `"SOFP cash is 0; SOCF closing cash is
        non-zero (…). Fill SOFP cash cell with SOCF value."`).
  - **Verify (shadow-diff, the gate):** new `tests/test_cross_checks_shadow.py`
        runs each check both ways on the e2e fixtures via the Step 0.3 harness;
        asserts identical `message`/`status`/values/comparands. This is the
        parity proof — **the xlsx path is not removed until this is green.**

- [ ] 🟥 **Step 1.3: De-workbook `_recheck_from_facts`** (`server.py:400`) — the
      headline smell. With fact-based checks, drop the `_export_canonical_workbooks`
      rebuild (`server.py:464`); re-run checks directly against current facts.
  - [ ] 🟥 Keep `_export_canonical_workbooks` for the actual **export/merge**
        path (it stays — Non-goal); only the *recheck* stops rebuilding.
  - **Verify:** integration test edits a fact via the facts API, calls the
        recheck endpoint, asserts the cross-check result reflects the edit with
        **no workbook written** (assert no new `*_canonical.xlsx` mtime change /
        spy that `export_run_to_xlsx` is not called on the recheck path).

- [ ] 🟥 **Step 1.4: SSE parity** — confirm `cross_check_start` /
      `cross_check_result` / `cross_check_complete` payloads (gotcha #19,
      `server.py:3678-3707`, `_emit_cross_check_summary`) are byte-identical
      (comparands are DB-only, not in SSE — keep it that way).
  - **Verify:** `tests/test_cross_check_progress_events.py` passes unchanged
        with the flag on.

- [ ] 🟥 **Step 1.5: Flip default on** — set `XBRL_FACT_BASED_CHECKS` default
      true once 1.2 shadow-diff is green across MFRS+MPERS × Company+Group
      fixtures.
  - **Verify:** full `pytest tests/ -q` green with flag on by default; the
        shadow-diff suite still green (now comparing the on-by-default path to
        the still-present xlsx path).

---

### Phase 2: 32b — Cascade-backed `verify_totals`

*Replace formula evaluation in `tools/verifier.py` with reads of the cascade's
computed totals from `run_concept_facts`. Keep `VerificationResult`, tolerance,
and all feedback wording byte-compatible. Gate behind `XBRL_FACT_BASED_VERIFY`.*

- [ ] 🟥 **Step 2.1: Give the verifier DB context** — thread `db_path` + `run_id`
      into `verify_statement` / `verify_totals` (`tools/verifier.py:579,928`) and
      the agent tool wrapper (`extraction/agent.py:804`, `ctx.deps` already has
      `run_id`). Add a fact-based code path behind the flag; keep the
      formula-eval path intact.
  - [ ] 🟥 On entry, trigger `recompute_after_turn(db_path, run_id)` so totals
        are fresh (Q4, recommended option b — idempotent).
  - **Verify:** with flag off, all `tools/verifier.py` behaviour and
        `tests/test_verifier.py` pass unchanged.

- [ ] 🟥 **Step 2.2: Read totals from facts, not formulas** — populate
      `computed_totals` (e.g. `total_assets_cy`, `total_equity_liabilities_cy`,
      and per-statement totals) by uuid lookup (Step 0.1/0.2) instead of
      `_evaluate_formula` / `_get_cell_value`.
  - [ ] 🟥 Keep `_balance_tolerance` (magnitude-scaled, `verifier.py:38-49`)
        exactly.
  - [ ] 🟥 Keep the magnitude / scale-unit warning (item 24, `_scan_magnitude_warnings`):
        read CY/PY pairs from facts (cascade exposes both periods) instead of
        iterating data-entry cells.
  - [ ] 🟥 Group dual-pass: read `entity_scope='Group'` then `'Company'` facts
        instead of cols B/C/D/E.
  - **Verify:** the no-residual-plug + imbalance-direction wording is untouched —
        `tests/test_verifier_feedback_wording.py` (all 4 branches, sign-error
        diagnostics) passes with the flag **on**. This is the gotcha #17 guard.

- [ ] 🟥 **Step 2.3: Save-gate parity** — `_verify_is_clean` /
      `_check_save_gate` (`coordinator.py:125`, `extraction/agent.py:849`) read
      `is_balanced` / `mandatory_unfilled` / `mismatches` off `VerificationResult`.
      Ensure the fact-based result populates these identically (incl.
      `mandatory_unfilled` — derive from `value_status='not_disclosed'`/blank on
      mandatory concepts, matching the xlsx "blank CY cell" semantics).
  - **Verify:** save-gate tests pass; the `acknowledge_unresolved=true` honest-
        completion path (memory: `save_gate_acknowledge_unresolved`) still
        finalises a flagged imbalance.

- [ ] 🟥 **Step 2.4: Shadow-diff + flip on** — new
      `tests/test_verifier_shadow.py` runs `verify_statement` both ways on the
      e2e fixtures for SOFP/SOCIE/SOCF/SOPL/SOCI × MFRS/MPERS × Company/Group;
      asserts identical `VerificationResult.feedback` + `computed_totals` (at
      2dp) + `is_balanced`. Flip `XBRL_FACT_BASED_VERIFY` default on once green.
  - **Verify:** shadow suite green; `pytest tests/ -q` green with flag default on.

---

### Phase 3: 32c — DB-rendered `read_template`

*Serve the agent's template summary without parsing xlsx per agent. Cache the
rendered summary at import (Q3, recommended: in-process cache keyed by
`template_id`). Gate behind `XBRL_DB_READ_TEMPLATE`.*

- [ ] 🟥 **Step 3.1: Build the summary cache** — produce the exact
      `_summarize_template` string (`extraction/agent.py:474`) once per
      `template_id` and cache it. Source the string from the existing
      `tools/template_reader.read_template` + `_summarize_template` at first
      request (or at import-time bootstrap), then memoise.
  - [ ] 🟥 Confirm the summary is fully determined by `template_id` (it is — the
        template file already encodes standard+level+variant); abstract flags
        come from `section_headers.discover_section_headers` (fill colour) which
        is deterministic per template.
  - **Verify:** cache returns a string **byte-identical** to the live
        `_summarize_template` output for every template family (assert in a test
        iterating all template files).

- [ ] 🟥 **Step 3.2: Route `read_template` tool through the cache** — the agent
      tool (`extraction/agent.py:665`) fetches the cached string when
      `XBRL_DB_READ_TEMPLATE` is on; no openpyxl load in the agent loop.
  - [ ] 🟥 `strip_duplicate_template` (`extraction/history_processors.py:365`)
        and item 30 compaction operate on the rendered string regardless of
        source — confirm `_is_template_summary` still matches the cached string.
  - **Verify:** `tests/test_template_reader.py` (abstract-row marking,
        MPERS header-fill parity, `[ABSTRACT …]`/`[DATA_ENTRY]`/`[FORMULA: …]`
        labelling) passes with the flag on. Mocked-agent e2e
        (`tests/test_e2e.py`) shows the agent receives the identical summary.

- [ ] 🟥 **Step 3.3: Resolve the "mandatory markers" gap** — the charter lists
      "mandatory markers" but the current summary does **not** emit them (no such
      field in `TemplateField`). Keep parity: do **not** add markers in this
      phase (out of scope; would change what the agent sees). Note the gap in the
      plan + CLAUDE.md if a future item wants them.
  - **Verify:** diff confirms no new tokens appear in the summary vs today.

- [ ] 🟥 **Step 3.4: Flip default on** — `XBRL_DB_READ_TEMPLATE` default true
      once 3.1/3.2 are green.
  - **Verify:** `pytest tests/ -q` green with flag default on.

---

### Phase 4: Retire xlsx from the verification path + lock the end state

*Only after Phases 1–3 are green on the default-on flags. Delete the dead xlsx
read paths, drop the transition flags, update docs, and add the end-state guard.*

- [ ] 🟥 **Step 4.1: Delete dead xlsx read code** — remove the
      `run(workbook_paths,…)` check methods + `cross_checks/util.py` openpyxl
      helpers (`open_workbook`, `find_value_by_label`, `find_value_in_block`,
      `_resolve_cell_value`), the verifier's `_evaluate_formula` /
      `_get_cell_value` / formula tokenizer, and the per-agent xlsx parse in
      `read_template`. Keep import/export/eval-ingest/generator openpyxl usage.
  - **Verify:** `grep -rn "open_workbook\|load_workbook" cross_checks/ tools/verifier.py extraction/agent.py` returns only export/edge usages; full suite green.

- [ ] 🟥 **Step 4.2: Drop transition flags** — remove `XBRL_FACT_BASED_CHECKS`,
      `XBRL_FACT_BASED_VERIFY`, `XBRL_DB_READ_TEMPLATE` and the dual-dispatch
      branches once parity is locked.
  - **Verify:** no references to the flags remain; suite green.

- [ ] 🟥 **Step 4.3: End-state guard test** — add a test asserting a full mocked
      run completes extraction→verify→cross-checks→recheck with **zero workbook
      opens** outside export (spy/monkeypatch `openpyxl.load_workbook` to count
      calls; assert the verification path makes none).
  - **Verify:** the guard fails if anyone re-introduces an xlsx read into the
        hot path (this is the durable regression fence for item 32).

- [ ] 🟥 **Step 4.4: Docs sync** — update CLAUDE.md gotchas: #3 (note math truth
      now read from cascade-persisted totals, edges still the source), #6/#22
      (race class closed by removing the read pattern, not just atomic saves),
      and the SYNC-MATRIX row for verification. Move this plan's "done" note into
      the gotcha trail. Keep `docs/Archive/` discipline.
  - **Verify:** CLAUDE.md + `docs/SYNC-MATRIX.md` reflect the new data flow;
        no stale "verifier opens workbook" claims remain.

---

## Invariants honoured

- **Gotcha #3** — the calculation linkbase stays the source of math truth; 32b
  reads the cascade-persisted totals the edges produced, not the formula cells.
- **Gotcha #4** — the reference-file cell-diff trap disappears *with cell-diffing
  itself*; everything compares by concept uuid.
- **Gotcha #12 / #15** — Group dual-pass and `applies_to_standard` preserved as
  entity_scope/standard-scoped fact reads.
- **Gotcha #17** — no-residual-plug + imbalance-direction feedback wording is
  byte-compatible (pinned by `test_verifier_feedback_wording.py`).
- **Gotcha #19** — cross-check SSE event families unchanged.
- **Gotcha #21** — `run_concept_facts` is already canonical; this removes the
  last consumers that round-trip through xlsx.
- **Gotcha #22** — closes the race class by removing the concurrent
  load→save→load pattern from the hot path entirely (not just making each save
  atomic, which item 8 did).

## Pinning-test discipline

Per phase: **shadow-diff tests first** (fact-based result == xlsx-based result on
the e2e fixtures), THEN retire the xlsx path. `tests/test_cross_checks*.py` and
the verifier suites migrate **with** their checks — never deleted ahead of parity
proof. No xlsx path is removed until its fact-based replacement is proven equal
on the fixtures (Phase 4 gates on Phases 1–3 being default-on and green).

## Reuse

`concept_model/cascade.py` (already persists COMPUTED totals),
`eval/grader.py::_gradeable_facts` uuid-join idiom, `cell_resolver.resolve_cell`
scoping model, `concept_targets` / `concept_render_aliases` (v11),
`comparands_json` (v14). Item 16's threading/timeout work makes the transition
safe even before checks are fast.

## Risks

- **Parity float drift (Q2):** cascade cents-rounding vs openpyxl exact eval.
  Mitigation: compare at display precision / within `_balance_tolerance` in the
  shadow harness.
- **Cascade staleness (Q4):** verifier reading before the cascade ran. Mitigation:
  `recompute_after_turn` on verifier entry (idempotent).
- **Label→uuid ambiguity (Q1):** duplicate labels (header vs leaf, gotcha #17).
  Mitigation: mirror `find_value_by_label`'s exact-then-substring + leaf-preferred
  resolution; pin with a resolver test.
- **`read_template` formula-text parity (Q3):** literal formula strings aren't in
  `concept_nodes`. Mitigation: cache the rendered string rather than reconstruct.

## Rollback Plan

- **During transition:** each phase is behind an env flag defaulting off, then on.
  If a default-on flip regresses, flip the flag off in the env — the xlsx path is
  still present and authoritative. No code revert needed until Phase 4.
- **After Phase 4 (flags + xlsx code removed):** revert the phase's commit range;
  the shadow-diff and end-state-guard commits identify exactly what changed.
- **Data safety:** no schema change, no data migration — `run_concept_facts` is
  untouched by this work (it's read, not rewritten), so there is no DB state to
  roll back. Verify a rollback by re-running `pytest tests/ -q` and confirming a
  live run's `filled.xlsx` download still matches the DB facts.

## Non-goals

- Not a rewrite of the export path — xlsx export (`exporter.py`) stays; this
  removes xlsx from the *verification* path only.
- Not a schema change — all phases read existing tables; no migration expected
  (pending Q3 resolution).
- Not adding mandatory-row markers to the agent summary (Step 3.3) — parity only.
