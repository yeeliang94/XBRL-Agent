# Implementation Plan: Excel-free verification pipeline (item 32)

**Overall Progress:** `~90%` (Phase 0 ✅; Phase 1 ✅ fact checks DEFAULT ON; Phase 2 ✅ verify DEFAULT ON (flipped 2026-06-15); Phase 3 ✅ read_template cache DEFAULT ON; Phase 4 — xlsx retirement NOT started, deliberately deferred)
**Status:** design / charter → **implementation plan** (expanded 2026-06-13) → **in progress** (Phases 1–3 landed; Phase 4 deferred 2026-06-15)
**PRD / context:** orchestration-hardening item 32; this file is the spec.
**Last Updated:** 2026-06-15

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

- **Bounding constraint — the exported xlsx keeps its live formulas.** Users
  edit the downloaded file in Excel and depend on totals recalculating, so
  formulas stay in the output (and in the blank templates that seed it). Item 32
  removes Excel from the *verification path only*. Going formula-free in the
  export was considered and **declined** for this reason.
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
- [x] 🟩 Q1–Q4 decided with the product owner (2026-06-13) — see Decisions.

---

## Decisions (resolved 2026-06-13 with product owner)

**Scope = "Verification only."** The product owner confirmed users **edit the
downloaded Excel file by hand and rely on totals auto-recalculating**, so the
exported xlsx **must keep its live formulas**. Item 32 therefore strips Excel
from the *internal verification path only*; the export path and the formulas in
the downloaded file are unchanged. (The broader "formula-free output" and
"fully formula-free" options were explicitly declined for this reason.)

- **Q1 (label→uuid) → (a) fuzzy label lookup.** Resolve "total assets" etc. via
  a `concept_nodes` label-substring lookup scoped to `template_id`, mirroring
  `find_value_by_label`'s exact-then-substring matching. Chosen for parity with
  today's behaviour.
- **Q2 (parity tolerance) → compare at display precision (2dp) / within
  `_balance_tolerance`.** NOTE this is now a **permanent invariant, not a
  migration scaffold**: because the exported file keeps formula-computed totals
  AND verification reads the cascade-computed totals, the two must agree forever
  — otherwise the file a user downloads would disagree with what we verified.
  The shadow-diff and Phase-4 guard therefore stay as ongoing parity fences.
- **Q3 (read_template summary) → cache the rendered string (incl. formula
  text), no schema change.** Formulas stay in the templates, so the agent's
  summary keeps showing `[FORMULA: …]` byte-for-byte. Build the exact
  `_summarize_template` output once per `template_id` and memoise in-process; do
  NOT reconstruct from `concept_nodes` (which lacks the literal formula text)
  and do NOT add a `concept_nodes.formula` column.
- **Q4 (cascade freshness) → (b) recompute on entry.** `verify_totals` triggers
  an idempotent `recompute_after_turn(db_path, run_id)` before reading totals,
  so it never reads stale figures regardless of tool ordering.

---

## Tasks

### Phase 0: Foundation — shared fact-read + label-resolve + shadow harness

*No behaviour change. Build the read primitives and the parity harness that all
three migrations reuse. Ship-able on its own (pure additions, zero call-site
changes).*

- [x] 🟩 **Step 0.1: Fact-read helper keyed by uuid** — `read_run_facts(conn,
      run_id, template_ids, kinds=None)` added to `concept_model/facts_api.py`.
      Returns `{(uuid, period, entity_scope): {value, value_status,
      children_status, source}}`; **includes COMPUTED by default** (the cascade-
      written totals verification reads); `kinds=` restricts. Scoped by
      `template_id`. Reused the existing `Literal["CY","PY"]` /
      `Literal["Company","Group"]` from `facts_api.py` — no new constants module
      (kept surgical).
  - **Verify:** `./venv/bin/python -m pytest tests/test_facts_read.py -q` — 11
        passed (incl. COMPUTED inclusion, kind filter, template scoping,
        CY/PY/Group key distinctness).

- [x] 🟩 **Step 0.2: Label→uuid resolver (Q1=fuzzy)** — new
      `concept_model/label_resolver.py::resolve_label(conn, template_id, label,
      prefer_leaf=True)` → `(uuid, sheet, row, col)`. Mirrors
      `find_value_by_label` matching (case-insensitive, `*`-stripped, exact-over-
      substring) and `cell_resolver` scoping (`WHERE template_id=?`); leaf-over-
      header preference (gotcha #17).
  - **Verify:** covered in `tests/test_facts_read.py` — resolves total
        assets/equity, prefers leaf over same-named header, template-scoped,
        returns None when absent.
  - **NOTE / deviation:** matches against `concept_nodes` render coords only.
        `concept_targets`/`concept_render_aliases` fallback was **not** needed
        for the cross-check/verifier labels (they target face/sub totals that
        carry primary render coords). If a Group/SOCIE check later needs an
        alias coord, extend here — flagged for Phase 1.

- [x] 🟩 **Step 0.3: Shadow-diff harness** — `tests/shadow_diff.py` (helper, not
      collected): `nums_equal` (2dp), `cross_check_diff`, `assert_cross_check_parity`
      comparing name/status/message/target coords exactly + numeric fields at 2dp
      + comparands.
  - **Verify:** self-test in `tests/test_facts_read.py` — identical results pass,
        sub-cent diff treated equal, wording / >1-cent diff flagged.

---

### Phase 1: 32a — Fact-based cross-checks

*Re-implement each check to read `run_concept_facts` by uuid. Keep the `run()`
contract, `CrossCheckResult` shape, message format, comparands, and SSE events
identical. Gate behind `XBRL_FACT_BASED_CHECKS`.*

**Status (2026-06-14):** Phase 1 ✅ **COMPLETE** — Steps 1.1–1.5b all done;
`XBRL_FACT_BASED_CHECKS` now defaults **ON** (set `=0` to fall back to xlsx).
The flip-on gate (full-pipeline e2e parity + mocked-test migration) is met; see
Step 1.5b below for the landing details. New/changed files:
`cross_checks/framework.py` (`FactsContext`, `run_all_facts`),
`cross_checks/facts_util.py` (new), `concept_model/label_resolver.py`
(`resolve_matrix_cell` added), all 6 check files (`run_facts`),
`server.py` (`_fact_based_checks_enabled`, `_build_check_template_ids`,
`_fact_ctx_for_run`, recheck + both pipeline sites wired),
`tests/test_cross_checks_shadow.py` (new, 9 parity tests),
`tests/test_recheck_endpoint.py` (de-workbook proof).

- [x] 🟩 **Step 1.1: New check entry point that takes DB context** — added
      `run_facts(ctx, tolerance)` to all 6 checks + `run_all_facts` runner +
      `FactsContext`. The xlsx `run()` path is byte-for-byte untouched.
      Dispatch is flag-gated in `server.py` (not the framework) to keep
      `run_all` pristine.

- [x] 🟩 **Step 1.2: Migrate the 6 checks** — SOFP balance, SOCF→SOFP cash
      (linear), SOPL→SOCIE profit / SOCI→SOCIE TCI / SOCIE→SOFP equity
      (matrix), SoRE→SOFP RE (MPERS). SOCIE matrix resolved via
      `resolve_matrix_cell` (filter by `matrix_col`: Total=X/B, Retained=C/B);
      NCI-conditional profit column via fact-space `socie_has_nci` (any
      `matrix_col='W'` fact non-zero). Group dual-pass reads `entity_scope`
      facts (no row-blocks — simpler than xlsx). `applies_to_standard` +
      `applies_to` gating preserved in `run_all_facts`.
  - **Verify:** `tests/test_cross_checks_shadow.py` — 9 parity tests green
        (SOFP balance company×3 + group; equity; TCI; profit no-NCI;
        profit with-NCI; SOCF-cash lineage message). All assert byte-equal
        `CrossCheckResult` (message normalised for float-repr at 2dp).
  - **DEVIATION:** shadow proofs are **hand-built fixtures** (prove both code
        paths agree on identical logical data), NOT the full import→cascade→
        export e2e the plan's "Verify" specifies. Full-pipeline parity is the
        gate for Step 1.5 flip-on and is still TODO.

- [x] 🟩 **Step 1.3: De-workbook `_recheck_from_facts`** — with the flag on,
      the recheck reads facts directly; the `_export_canonical_workbooks`
      rebuild is skipped. Export path itself unchanged (Non-goal).
  - **Verify:** `tests/test_recheck_endpoint.py
        ::test_recheck_fact_based_does_not_rebuild_workbook` — trip-wires
        `_export_canonical_workbooks`; green.

- [x] 🟩 **Step 1.4: SSE parity** — `run_all_facts` reuses the same `on_check`
      callback contract; `_run_cross_checks_bounded` threads it identically.
      Comparands stay DB-only (not in SSE).
  - **Verify:** `tests/test_cross_check_progress_events.py` green (flag off);
        SSE payload code path unchanged.

- [x] 🟩 **Step 1.5a: Full-pipeline e2e parity harness** — built
      `tests/test_cross_checks_e2e_parity.py`: imports the real SOFP template,
      seeds leaf facts, runs the cascade, exports a real workbook with live
      formulas, then asserts the xlsx check (evaluating formulas) ==
      `run_facts` (reading cascade totals) for balanced + imbalanced. **This is
      the real gate** the plan demanded; green for SOFP balance.

- [x] 🟩 **Step 1.5b: Flip default on — DONE (2026-06-14).**
      `XBRL_FACT_BASED_CHECKS` now defaults **ON** (`server._fact_based_checks_enabled`,
      set `=0` to fall back to the xlsx path). Three coupled changes unblocked it:
      1. **Test migration (uniform).** Every `patch("cross_checks.framework.run_all",
         …)` site (42 single-line + 2 multiline across 15 files) now ALSO patches
         `cross_checks.framework.run_all_facts` with the same `return_value`/
         `side_effect`. The fakes are positionally compatible with
         `run_all_facts(checks, ctx, run_config, …)`, so one fake drives both
         paths. This tests the orchestration around BOTH paths and collapses
         cleanly when Phase 4 deletes `run_all`. (The plan's "populate facts in
         mocked runs" option was rejected — it wouldn't work for the
         exception/timeout behaviour-injection tests, which need to make the
         check CALL raise regardless of facts.)
      2. **Wiring robustness.** `server._build_check_template_ids` caught only
         `ValueError`; a `None`/unknown variant (the CLI mock shape) raises
         `KeyError` from `get_variant`, which crashed the whole run. Now catches
         `(ValueError, KeyError)` — degrades identically to the xlsx path, which
         tolerates the same case via `workbook_paths`.
      3. **Proof.** Full backend suite green with the flag default-on
         (`2243 passed, 2 skipped`). Shadow suite (all 6 checks) + extended e2e
         harness (Step 1.5c) green.
  - **e2e harness coverage (Step 1.5c).** `tests/test_cross_checks_e2e_parity.py`
    now covers SOFP balance (linear cascade sum), SOCF→SOFP cash (leaf-to-leaf
    cross-statement), and SOCIE→SOFP equity (matrix Total col + cascade) on REAL
    templates. These exercise the three representative arithmetic shapes. The
    other 3 checks (SOPL→SOCIE profit, SOCI→SOCIE TCI — same matrix machinery as
    SOCIE equity; SoRE→SOFP RE — MPERS) are covered by the shadow suite + the
    full-suite flag-on proof; individual real-template e2e for them is a
    nice-to-have, not a gate.
  - **KNOWN advisory divergence surfaced by the e2e gate (flagged for Phase 1
    alias-coord follow-up).** For cross-sheet-rolled SOFP cash, the xlsx path's
    comparand reports the FACE coord (`SOFP-CuNonCu`, where it scanned col A)
    while the fact path resolves to the editable sub-sheet LEAF
    (`SOFP-Sub-CuNonCu`). Values/status/message/diff are byte-identical; only the
    advisory comparand `sheet` differs (comparands never affect pass/fail). This
    is exactly the alias-coord case `label_resolver` already flags as a Phase-1
    follow-up. The e2e test asserts everything BUT this one sheet coord
    (`assert_cross_check_parity_modulo_rollup_sheet`) so the divergence stays
    visible rather than hidden.

---

### Phase 2: 32b — Cascade-backed `verify_totals`

*Replace formula evaluation in `tools/verifier.py` with reads of the cascade's
computed totals from `run_concept_facts`. Keep `VerificationResult`, tolerance,
and all feedback wording byte-compatible. Gate behind `XBRL_FACT_BASED_VERIFY`.*

**Status (2026-06-15): ✅ COMPLETE — ALL FIVE statements ported + shadow-green;
flag default ON (Step 2.4 flipped 2026-06-15).** `tools/verifier_facts.py` implements the fact
path for SOFP, SOCIE (matrix), SOCF, SOPL, SOCI;
`tools/verifier.py::verify_statement` gained `(db_path, run_id, template_id)`
kwargs + `_fact_based_verify_enabled()` + a `recompute_after_turn`-on-entry
dispatch (Q4); `extraction/agent.py` threads the DB context from `ctx.deps`.
Proven by `tests/test_verifier_shadow.py` (SOFP balanced/imbalanced; SOCF-Direct
/ SOPL / SOCI / SOCIE e2e import→cascade→export parity; not_disclosed contract;
flag-off-uses-xlsx). **Full suite green BOTH with the flag off (2251) and on
(2255)** — unlike the cross-check flip, the verify flip breaks no mocked tests
(they don't invoke the real verify tool with a DB context).

**DECISION (2026-06-15): FLIPPED ON.** Originally shipped OFF / opt-in; on a
follow-up product call the owner chose to flip `XBRL_FACT_BASED_VERIFY` default
**ON** (set `=0` to fall back to the still-present xlsx path). The SOCF-Indirect
investigation is resolved (see below) and the full backend suite is green with
the flag default-on (`2257 passed, 2 skipped`). The stricter `mandatory_unfilled`
behaviour (below) now ships live; it remains overridable via the env var if a
real filing surfaces over-blocking.

**Behaviour change shipped by the default flip-on:**
1. *Stricter `mandatory_unfilled`* (the documented product decision below) —
   broadly increases save-gate blocks; overridable via `XBRL_FACT_BASED_VERIFY=0`.

**RESOLVED (2026-06-15) — the SOCF-Indirect "divergence" was a legacy-evaluator
bug, now FIXED.** The shadow test's uniform "every line = 100" seeding surfaced a
1700-vs-1800 disagreement on the SOCF-Indirect operating total. Root cause was
NOT a template or cascade error (both were always correct): `tools/verifier.py`'s
hand-rolled `_evaluate_formula`/`_resolve_cell_value` used a permanent `visited`
set that doubled as a "don't re-count" guard, so a line referenced via two
cancelling paths (added +1 directly to the operating total, subtracted −1 inside
"cash generated from operations" — a diamond) had its −1 path silently dropped,
over-counting by that line. This was a **live bug in the default (flag-off) xlsx
verification path** for any diamond-reference template. Fixed by making `visited`
a stack-based cycle guard (discard on exit). The fact path always computed the
correct value; this fix makes the legacy xlsx path agree. Pinned by
`tests/test_verifier_formula.py::test_diamond_reference_counts_each_path`; the
verifier shadow suite now uses SOCF-Indirect (byte-parity) again.

> **PRODUCT DECISION (2026-06-14) — `mandatory_unfilled` is INTENTIONALLY
> stricter, NOT byte-parity.** The xlsx `_collect_unfilled_mandatory` treats any
> formula cell as "filled", and the SOFP main sheet pre-fills every line item
> with a cross-sheet formula (`='SOFP-Sub'!Bn`) — so the xlsx scan is near-inert
> (it flags almost nothing). The fact path flags a mandatory (`*`) leaf whose
> fact is genuinely ABSENT (a `not_disclosed` fact counts as resolved). This
> catches real gaps the xlsx scan hid and feeds the save gate (gotcha #17), so
> it stays behind the off-by-default flag until validated. The shadow suite
> asserts the fact set is a **superset** of the xlsx set (never silently equal).
> All OTHER fields (`computed_totals`/`is_balanced`/`mismatches`/`feedback`/
> `matches_pdf`/`magnitude_warnings`) remain byte-compatible.

- [x] 🟩 **Step 2.1: Give the verifier DB context (DONE, SOFP).** Threaded `db_path` + `run_id`
      into `verify_statement` / `verify_totals` (`tools/verifier.py:579,928`) and
      the agent tool wrapper (`extraction/agent.py:804`, `ctx.deps` already has
      `run_id`). Add a fact-based code path behind the flag; keep the
      formula-eval path intact.
  - [x] 🟩 On entry, trigger `recompute_after_turn(db_path, run_id)` so totals
        are fresh (Q4, recommended option b — idempotent).
  - **Verify:** with flag off, all `tools/verifier.py` behaviour and
        `tests/test_verifier.py` pass unchanged.

- [x] 🟩 **Step 2.2: Read totals from facts, not formulas** — populate
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

- [x] 🟩 **Step 2.3: Save-gate parity** — `_verify_is_clean` /
      `_check_save_gate` (`coordinator.py:125`, `extraction/agent.py:849`) read
      `is_balanced` / `mandatory_unfilled` / `mismatches` off `VerificationResult`.
      Ensure the fact-based result populates these identically (incl.
      `mandatory_unfilled` — derive from `value_status='not_disclosed'`/blank on
      mandatory concepts, matching the xlsx "blank CY cell" semantics).
  - **Verify:** save-gate tests pass; the `acknowledge_unresolved=true` honest-
        completion path (memory: `save_gate_acknowledge_unresolved`) still
        finalises a flagged imbalance.

- [x] 🟩 **Step 2.4: Shadow-diff + flip on (DONE 2026-06-15)** —
      `tests/test_verifier_shadow.py` runs `verify_statement` both ways on the
      e2e fixtures for SOFP/SOCIE/SOCF/SOPL/SOCI × MFRS/MPERS × Company/Group;
      asserts identical `VerificationResult.feedback` + `computed_totals` (at
      2dp) + `is_balanced`. `XBRL_FACT_BASED_VERIFY` default flipped to **ON**.
  - **Verify:** shadow suite green; `pytest tests/ -q` green with flag default
        on (`2257 passed, 2 skipped`).

---

### Phase 3: 32c — DB-rendered `read_template`

*Serve the agent's template summary without parsing xlsx per agent. Cache the
rendered summary in-process (Q3: in-process cache keyed by `template_id`). Gate
behind `XBRL_DB_READ_TEMPLATE`.*

**Status (2026-06-15): ✅ COMPLETE — flag default ON.** Implemented as an
in-process memo cache (`extraction/agent.py::_TEMPLATE_SUMMARY_CACHE` +
`_render_template_summary`), NOT a DB read (Q3: `concept_nodes` lacks the literal
formula text). The first `read_template` for a given `template_id` parses the
xlsx once and memoises the rendered string; every later call (any agent, any
run, same process) is served from cache with zero openpyxl load. Falls through
to the legacy per-`deps` parse when the flag is off or no `template_id` is
available (some CLI paths) — graceful degradation. New file:
`tests/test_read_template_cache.py` (6 tests). Full suite green with both Phase 2
and Phase 3 flags default-on (`2263 passed, 2 skipped`).

- [x] 🟩 **Step 3.1: Build the summary cache** — `_render_template_summary`
      produces the exact `_summarize_template` string once per `template_id` and
      memoises it in `_TEMPLATE_SUMMARY_CACHE`. Sourced from the existing
      `tools/template_reader.read_template` + `_summarize_template` at first
      request (lazy), then reused.
  - [x] 🟩 Summary is fully determined by `template_id` (the template file
        encodes standard+level+variant 1:1); abstract flags come from
        `section_headers.discover_section_headers` (fill colour), deterministic
        per template.
  - **Verify:** `tests/test_read_template_cache.py::test_cached_summary_is_byte_
        identical_for_every_template` — iterates all 58 Company/Group templates
        across both standards; cached string == live `_summarize_template`
        output, byte-for-byte. Green.

- [x] 🟩 **Step 3.2: Route `read_template` tool through the cache** — the agent
      tool (`extraction/agent.py`) now calls `_render_template_summary(ctx.deps)`;
      no openpyxl load in the agent loop on a cache hit.
  - [x] 🟩 `strip_duplicate_template` / item-30 compaction operate on the
        `"=== Sheet:"` banner, which the byte-identical cached string still
        carries — `_is_template_summary` matches it
        (`…::test_cached_summary_still_matches_compaction_marker`).
  - **Verify:** `tests/test_template_reader.py` + `tests/test_e2e.py` +
        `tests/test_history_processors.py` green with the flag on (29 passed).

- [x] 🟩 **Step 3.3: "mandatory markers" gap — kept parity, NOT emitted.** The
      charter listed "mandatory markers" but the summary has no such field in
      `TemplateField`; adding them would change what the agent sees. Out of
      scope — `…::test_no_new_marker_tokens_vs_legacy` pins that the cached
      summary introduces no new tokens. A future item wanting mandatory markers
      in the agent summary must add them to both paths deliberately.
  - **Verify:** byte-equality test confirms no new tokens vs today.

- [x] 🟩 **Step 3.4: Flip default on** — `XBRL_DB_READ_TEMPLATE` defaults `1`
      (`_db_read_template_enabled`); set `=0` for the legacy per-call parse.
  - **Verify:** `pytest tests/ -q` green with flag default on
        (`2263 passed, 2 skipped`).

---

### Phase 4: Retire xlsx from the verification path + lock the end state

*Only after Phases 1–3 are green on the default-on flags. Delete the dead xlsx
read paths, drop the transition flags, update docs, and add the end-state guard.*

**Status (2026-06-15): PREREQUISITES MET, DELIBERATELY DEFERRED.** Phases 1–3
are all default-on and green, so Phase 4 is now *unblocked*. It is being held
back on purpose: it is irreversible (it deletes the xlsx fallback that today
still answers when any `XBRL_*` flag is set `=0`), and the product owner wants
the fact-based verify (Phase 2) to soak on real filings — particularly the
stricter `mandatory_unfilled` save-gate behaviour — before the escape hatch is
removed. Keep the three flags and the xlsx code in place until that soak passes,
then execute Steps 4.1–4.4. The dual-path tests (every mocked check site patches
both `run_all` and `run_all_facts`) already collapse cleanly when `run_all` is
deleted.

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

- Not a rewrite of the export path — xlsx export (`exporter.py`) stays, **and
  the exported file keeps its live formulas** (users edit it in Excel and rely on
  recalculation). This removes xlsx from the *verification* path only.
- Not a schema change — all phases read existing tables; no migration expected
  (pending Q3 resolution).
- Not adding mandatory-row markers to the agent summary (Step 3.3) — parity only.
