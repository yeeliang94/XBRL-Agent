# Implementation Plan: Extraction Pipeline Hardening

**Overall Progress:** `0%`
**Last Updated:** 2026-04-22
**Trigger:** Post-run QA over multiple companies surfaced five recurring failure modes in face-statement + notes output (see Motivation).

## Motivation

Reviewing output across several company runs turned up five patterns the pipeline does not currently catch:

1. **SOPL Row 26 (`*Profit (loss)`) ≠ Row 31 (`*Total profit (loss)`).** Intra-statement verifier catches it (`tools/verifier.py:867`) but reaches the output anyway because agents are not required to act on `verify_totals` feedback before `save_result`.
2. **Mandatory fields (labels prefixed with `*`) left unfilled.** No validator checks completeness today.
3. **Notes duplicated across Sheet 11 (Accounting Policies) and Sheet 12 (List of Notes).** Parallel notes agents have zero cross-sheet awareness; `notes/writer.py` only dedupes at the label level, not the note level.
4. **"Material Accounting Policies" classification is fuzzy.** The heading convention is stated in prompts but not enforced — policies sometimes land in both sheets.
5. **Sub-notes (5, 5.1, 5.2) have no identifier.** `NotesPayload` carries no note reference, so duplication across cells is invisible to the writer.

## Goals

- Force every extraction agent to reach a verified-clean state before finalising output.
- Detect unfilled mandatory fields on face sheets and surface them through the same feedback channel as balance mismatches.
- Eliminate cross-sheet note duplication via a dedicated post-validator agent that runs after the parallel notes pass.
- Loop cross-statement validation failures back to an agent for correction instead of only surfacing them in the UI.
- Keep the changes additive — no breaking changes to `RunConfig`, SSE event shape, or DB schema.

## Non-Goals

- Rewriting the notes prompts from scratch (we reinforce existing conventions, we do not restructure).
- Making the writer reject late writes — user explicitly ruled this out (first-attempt error would block correction).
- Blocking runs on mandatory-field failure — it surfaces as validator feedback; agents retry; worst case output still ships with the gap flagged.

## Key Decisions

- **`verify_totals` becomes mandatory before `save_result`.** `save_result` refuses to finalise until the most recent `verify_totals` returned `is_balanced=True` and `mandatory_unfilled=[]`. Same feedback loop already used for balance errors.
- **Mandatory-field check runs inside `verify_totals`**, not as a new cross-check. Reason: existing retry path already routes verifier feedback to the agent; reusing it avoids new orchestration.
- **Face sheets only for mandatory-field coverage** (per user). Notes sheets often have legitimately empty `*` rows.
- **Cross-check correction is a new agent**, not a re-spawn of the original. Mirrors the notes pattern the user proposed: independent agent with its own PDF + workbook access, runs after cross-checks fail, attempts corrections, re-runs checks.
- **Notes post-validator is a new agent** running after the parallel notes pass. Not a writer-level reject (writer-reject fails on first-attempt errors per user feedback).
- **Sub-note identifier lives on `NotesPayload.source_note_refs: list[str]`.** Optional, agent-populated during notes extraction. Post-validator uses it as the primary dedup signal; falls back to content overlap when absent.
- **No DB schema change.** All new signals ride existing `run_config_json` / cross-check result structures.
- **Red-green TDD throughout.** Each step begins with a failing test.

## Confirmed Decisions (2026-04-22)

- [x] **D1.** `save_result` returns an error string on verifier-refuse — agent keeps iterating within its existing budget.
- [x] **D2.** Correction agent reuses scout's `infopack` from the original run (no re-scout).
- [x] **D3.** When the same note-ref appears on both sheet 11 and sheet 12, the notes post-validator agent **reasons through it using the heading rule from the prompts** — no deterministic classifier. The agent inspects the PDF, applies "Material Accounting Policies → Sheet 11; otherwise → Sheet 12", and rewrites the wrong cell. Agent rationale is recorded in the correction log.
- [x] **D4.** Correction agent and notes post-validator: **1 iteration max each**. If failures persist, they surface in the Validator tab + History detail for human review. No further automated retry.
- [x] **D5.** MPERS face sheets use the identical `*` prefix convention as MFRS — mandatory-field validator is standard-agnostic.

## Pre-Implementation Checklist

- [ ] 🟥 User acks Open Questions Q1–Q4
- [ ] 🟥 No in-flight work on `tools/verifier.py`, `extraction/agent.py`, `notes/writer.py`, `notes/coordinator.py`
- [ ] 🟥 User confirms MPERS face sheets use the same `*`-prefix convention as MFRS (spot-check `XBRL-template-MPERS/Company/03-SOPL-Function.xlsx`)
- [ ] 🟥 Baseline test run green: `python -m pytest tests/ -v && cd web && npx vitest run`

## TDD Rules

1. **Red first.** Every step begins with a test that fails for the right reason.
2. **Green is minimum.** Only code needed to pass; no drive-by cleanups.
3. **Full suite green between steps.** Backend `pytest` + frontend `vitest` both green before moving on.
4. **No test = no feature.** If something cannot be asserted, either skip or reframe until it can.

---

## Tasks

### Phase 1: Enforce Verifier Discipline (root cause of SOPL Row 26/31 reaching output)

Goal: the agent cannot finalise a sheet without the verifier passing and mandatory `*` fields populated.

- [ ] 🟥 **Step 1.1: Extend `VerificationResult` with `mandatory_unfilled: list[str]`**
  - **R:** `test_verification_result_has_mandatory_unfilled_field` — dataclass access fails.
  - **G:** Add field to `VerificationResult` (`tools/verifier.py:13-26`), default `[]`.
  - **R:** `test_verify_sopl_reports_unfilled_asterisks` — seed a SOPL xlsx with `*Revenue` row blank; assert `result.mandatory_unfilled` contains `"*Revenue"` (and the SOPL Row 26 check still runs).
  - **G:** Add `_collect_unfilled_mandatory(ws, filing_level) -> list[str]` helper. Call from each `_verify_{sofp,sopl,soci,socf,socie}` function. Helper scans col A for `*`-prefixed labels, reads the CY column(s) per filing level, flags any that are `None` or empty string.
  - **Verified:** all existing verifier tests still green.

- [ ] 🟥 **Step 1.2: Surface `mandatory_unfilled` in `verify_totals` feedback**
  - **R:** `test_verify_totals_tool_output_includes_mandatory_unfilled` in `tests/test_extraction_agent.py` — mock `_verify_statement_impl` to return `mandatory_unfilled=["*Revenue", "*Finance income"]`, assert the string returned by the tool contains both labels and an "Action required:" directive.
  - **G:** Edit `extraction/agent.py:270-295` — append mandatory-unfilled section to the `lines` list before returning.

- [ ] 🟥 **Step 1.3: `save_result` refuses to finalise until last verify was clean**
  - **R:** `test_save_result_blocks_when_verify_totals_failed` — feed an agent loop where `verify_totals` returned `is_balanced=False`, then call `save_result`; assert returned string is an error directing the agent to re-verify.
  - **R:** `test_save_result_blocks_when_verify_totals_never_called` — similar, no verify call at all → error string.
  - **G:** Track last-verify state on `ExtractionDeps` (new field `last_verify_result: Optional[VerificationResult]`, reset in `fill_workbook`, set in `verify_totals`). In `save_result`, refuse to proceed unless `last_verify_result` exists, `is_balanced is True`, and `mandatory_unfilled == []`.
  - **Edge:** allow a "force save" flag if `MAX_AGENT_ITERATIONS-3` already reached — audit log records forced save. Prevents infinite loops on genuinely impossible-to-balance PDFs.

- [ ] 🟥 **Step 1.4: Integration test — agent actually retries after forced verifier feedback**
  - **R:** E2E test with a mocked LLM that first submits wrong SOPL values (Row 26 ≠ Row 31), reads verifier feedback, corrects, re-verifies, saves. Assert final output has matching totals.
  - **G:** None — pipeline already supports this. Step proves the loop works end-to-end.

### Phase 2: MFRS/MPERS Validation Survey + Fill Gaps

Goal: close the remaining intra-statement gaps the verifier doesn't cover, and fix an existing MPERS bug.

- [ ] 🟥 **Step 2.0: Fix `_verify_sopl` MPERS label matching (existing bug)**
  - Current `_verify_sopl` matches the normalised label `"profit (loss)"` (`tools/verifier.py:832`). On MPERS SOPL-Function, the equivalent row is **`*Profit (loss) from continuing operations, net`** (row 26), and the attribution total is **`*Total Profit (Loss)`** (row 34, not row 31). Today the verifier returns `"SOPL verification failed: missing 'Profit (loss)' label"` on every MPERS run.
  - **R:** `test_verify_sopl_mpers_function_matches_continuing_ops_row` — load `XBRL-template-MPERS/Company/03-SOPL-Function.xlsx` (or a fixture derived from it) with valid values in row 26 + row 34 → expect pass, not the current "missing label" failure.
  - **R:** `test_verify_sopl_mpers_detects_row_26_vs_34_mismatch` — same template with row 26 = 100, row 34 = 120 → expect failure reporting both values.
  - **G:** Extend the label-matcher to accept either `"profit (loss)"` (MFRS) or `"profit (loss) from continuing operations, net"` (MPERS) as the profit_loss_row. Attribution-total matching via `"total profit (loss)"` already works on both standards. Confirm by running against both template variants.

- [ ] 🟥 **Step 2.1: SOPL Group attribution (Row 28 + Row 30 = Row 26 on MFRS; analogous rows on MPERS)**
  - Currently `_verify_sopl` checks profit-row ≠ attribution-total but not the owners+NCI sum.
  - **R:** `test_verify_sopl_group_checks_attribution` — Group filing with owners=100, NCI=10, profit=120 → fail.
  - **G:** Extend `_verify_sopl` with attribution sum check gated on `filing_level == "group"`. Uses label matching for "attributable to owners" / "non-controlling interests" so it works on both MFRS and MPERS.

- [ ] 🟥 **Step 2.2: SOFP Group equity attribution (owners + NCI = Total equity)**
  - **R:** `test_verify_sofp_group_checks_equity_attribution` — Group SOFP where `Equity attributable to owners` + `Non-controlling interests` ≠ `Total equity`.
  - **G:** Extend `_verify_sofp`. Handle both formula-computed and data-entry cells. Works identically on MFRS + MPERS.

- [ ] 🟥 **Step 2.3: (Optional — user discretion)** Direct SOPL → SOCI profit match as a cross-check
  - Currently caught indirectly via `sopl_to_socie_profit` → `soci_to_socie_tci`, but a direct check is clearer when SOCIE is absent.
  - Skip unless user requests — existing cross-check chain already catches this.

### Phase 3: Cross-Check Correction Agent

Goal: when cross-checks fail after merge, spawn a correction agent instead of surfacing-and-forgetting.

- [ ] 🟥 **Step 3.1: `CorrectionAgentDeps` + agent factory**
  - **R:** `test_correction_agent_factory_returns_agent_with_expected_tools` — agent has `view_pdf_pages`, `fill_workbook`, `verify_totals`, `run_cross_checks`.
  - **G:** New module `correction/agent.py`. Deps carry: merged workbook path, PDF path, list of failed `CrossCheckResult`, scout infopack, filing level/standard.

- [ ] 🟥 **Step 3.2: Coordinator hook — spawn correction agent after cross-check failure**
  - **R:** `test_run_multi_agent_stream_invokes_correction_on_cross_check_failure` — mock cross-checks to fail SOPL→SOCIE; assert correction agent is invoked with failure context.
  - **G:** Extend `server.py` `run_multi_agent_stream` — after `cross_checks.run_all()`, if any result is `status="failed"`, invoke correction agent once, then re-run cross-checks on the updated workbook.
  - **Bounded:** max 1 correction pass; remaining failures surface in validator tab as before.
  - **SSE:** new event type `correction_started` / `correction_completed` mirroring agent events; DB persists it as a pseudo-agent run.

- [ ] 🟥 **Step 3.3: Prompt for correction agent**
  - `prompts/correction.md`: describe failure-driven correction workflow. Agent receives `failed_checks` as structured input, views relevant PDF pages, rewrites cells via `fill_workbook`, re-verifies intra-statement, re-runs cross-checks.

### Phase 4: Notes Sub-Note Identifier + Payload Schema

Goal: make sub-notes first-class so the post-validator can dedupe reliably.

- [ ] 🟥 **Step 4.1: `NotesPayload.source_note_refs: list[str] = []`**
  - **R:** `test_notes_payload_has_source_note_refs`.
  - **G:** Add to `notes/payload.py`. Defaults empty; no migration needed.

- [ ] 🟥 **Step 4.2: Prompt updates — agents populate `source_note_refs` during extraction**
  - **R:** `test_notes_prompt_instructs_source_note_refs` — pin that the rendered prompt contains the `source_note_refs` contract.
  - **G:** Update `prompts/_notes_base.md` with a "Note reference" section: "When you write a cell, populate `source_note_refs` with every PDF note number the content is drawn from (e.g. `['5', '5.1']`). Use the numbering shown in the PDF note heading. If no numbering is visible, leave empty."
  - Update schema the agents see (whatever `NotesPayload` rendering exposes) to surface the field.

- [ ] 🟥 **Step 4.3: Writer persists `source_note_refs` in evidence metadata**
  - **R:** `test_notes_writer_persists_source_note_refs_for_post_validator` — payload with `source_note_refs=["5.1"]` → writer records it in a side-channel the post-validator can read (either an evidence-cell suffix or a payloads-json dumped alongside `filled.xlsx`).
  - **G:** Write `notes/writer.py` to also emit a `notes_payloads.json` next to the merged workbook: `[{sheet, row, col, source_note_refs, content_preview}, ...]`. Post-validator reads this instead of re-parsing cells.

### Phase 5: Notes Post-Validator Agent

Goal: a dedicated agent runs after the parallel notes pass to dedupe across sheets 11 & 12.

- [ ] 🟥 **Step 5.1: `NotesValidatorAgentDeps` + factory**
  - **R:** `test_notes_validator_agent_factory_returns_agent` — agent has tools `view_pdf_pages`, `read_cell`, `rewrite_cell`, `flag_duplication`.
  - **G:** New module `notes/validator_agent.py`. Deps: merged workbook path, PDF path, `notes_payloads.json` path, filing level/standard.

- [ ] 🟥 **Step 5.2: `rewrite_cell` tool — safe overwrite including deletion**
  - **R:** `test_rewrite_cell_tool_replaces_content_and_evidence` — call with `content=""` to delete, assert cell cleared including evidence column.
  - **G:** Implement `rewrite_cell(sheet, row, col, content, evidence)`. Validates the cell is a data-entry cell (not a formula) before writing. Records the operation to a correction log.

- [ ] 🟥 **Step 5.3: Detection — duplicate `source_note_refs` across sheets 11 & 12**
  - **R:** `test_notes_validator_detects_cross_sheet_duplicate_by_note_ref` — payloads where `source_note_refs=["5.1"]` exists on both Sheet 11 row X and Sheet 12 row Y → agent prompt reports the duplicate as a candidate to resolve.
  - **G:** Prompt tells the agent to: (1) load `notes_payloads.json`, (2) group by note_ref, (3) for any ref on both sheets, view the PDF pages, **reason through whether the content is a material accounting policy or a disclosure note using the same rules in the notes prompts** (heading containing "Material Accounting Policies" / "Significant Accounting Policies" / "Summary of Material Accounting Policies" → Sheet 11; otherwise → Sheet 12). The agent makes the call, not a deterministic rule. (4) Rewrite the wrong cell to remove that content; log the agent's rationale.
  - **Note on D3:** no keyword classifier in code — the heading-rule reasoning lives entirely in the agent prompt, consistent with how the Sheet 11 / Sheet 12 split is taught today.

- [ ] 🟥 **Step 5.4: Detection — content overlap fallback when `source_note_refs` is missing**
  - **R:** `test_notes_validator_detects_overlap_when_refs_missing` — two cells share ≥50% of a normalised text snippet, ref field empty, agent still flags.
  - **G:** Add a content-hash / shingle check (simple Jaccard over word 5-grams, `>= 0.5` threshold). Surface candidates to the agent prompt as "probable duplicates — verify".

- [ ] 🟥 **Step 5.5: Coordinator hook — run post-validator after notes parallel pass**
  - **R:** `test_notes_coordinator_runs_post_validator_when_notes_ran` — mock notes agents to completion, assert post-validator invoked exactly once.
  - **G:** Edit `notes/coordinator.py` — after aggregate write, if any of the notes-11 / notes-12 sheets ran, invoke post-validator. Bounded to 1 iteration. Emits SSE events under pseudo-agent id `NOTES_VALIDATOR`.

### Phase 6: Prompt Reinforcement

Goal: make the "one note, one cell" + "material policies → 11; general → 12" rules ergonomic for the LLM.

- [ ] 🟥 **Step 6.1: `prompts/_notes_base.md` — explicit non-duplication invariant**
  - **R:** `test_notes_base_prompt_contains_non_duplication_rule`.
  - **G:** Add a "Invariants" section: "Each note reference (e.g. 5, 5.1) appears in exactly one cell across the entire workbook. Sub-notes can be grouped with their parent (5, 5.1, 5.2 in one cell), but the same sub-note cannot appear in two cells."

- [ ] 🟥 **Step 6.2: `prompts/notes_accounting_policies.md` — heading rule pin**
  - **R:** `test_accounting_policies_prompt_has_heading_rule`.
  - **G:** Strengthen existing guidance: "The heading in the PDF determines the split. If the subheader says 'Material Accounting Policies' (or a close variant: 'Significant Accounting Policies', 'Summary of Material Accounting Policies'), that content belongs on Sheet 11. Otherwise it belongs on Sheet 12. No content belongs on both."

- [ ] 🟥 **Step 6.3: `prompts/notes_listofnotes.md` — mirror the invariant**
  - **R:** `test_listofnotes_prompt_has_heading_rule`.
  - **G:** Add symmetric reminder — Sheet-12 agents must skip any note whose heading matches the material-policies pattern.

### Phase 7: UI + History Surface

Goal: surface correction-agent + notes-validator runs in the existing Validator tab and History detail.

- [ ] 🟥 **Step 7.1: SSE event types for correction + notes-validator**
  - **R:** `test_sse_events_include_correction_agent` backend + `test_appReducer_handles_correction_agent` frontend.
  - **G:** Add `agent_id="CORRECTION"` and `agent_id="NOTES_VALIDATOR"` to `appReducer`. Label them in `deriveAgentLabel`. They appear as tabs after the face/notes tabs.

- [ ] 🟥 **Step 7.2: History detail renders correction events**
  - **R:** `test_history_detail_renders_correction_agent`.
  - **G:** Extend `RunDetailView.tsx` to display the new pseudo-agents alongside existing ones.

---

## Risks

- **Latency.** Post-validator + correction agents add sequential phases after parallel fan-out. Expected +60-180s per run on hard cases. Mitigation: bound to 1 iteration each; skip if no failures detected.
- **False-positive dedup.** Content-overlap fallback could flag legitimate cross-references (same accounting term used in policy + note). Mitigation: threshold tuning + agent has final say (prompt it to double-check via PDF).
- **Blocking `save_result` on verifier pass** could cause agents to hit `MAX_AGENT_ITERATIONS=50` on PDFs where totals genuinely can't be reconciled (bad PDF, missing pages). Mitigation: force-save escape hatch at iteration 47+ with audit trail.
- **`source_note_refs` adoption is agent-dependent.** If the agent doesn't reliably populate the field, the post-validator falls back to content overlap, which is weaker. Mitigation: prompt tests pin the contract; monitor rollout.
- **MPERS parity.** Phase 2 gap-fill checks (SOPL attribution, SOFP equity attribution) must run under both MFRS and MPERS. Existing `_cy_columns(filing_level)` helper handles Group columns generically — extend the new checks to use it.

## Follow-Ups (Out of Scope for This Plan)

- **Direct SOPL→SOCI cross-check** (Phase 2.3 optional) — defer unless output review turns up cases where SOCIE isn't filed.
- **Content-similarity threshold auto-tuning** — start at Jaccard ≥ 0.5; revisit after first production run.
- **Human-in-the-loop correction UI** — a "manual fix" panel in the Validator tab for cases where the correction agent fails. Not needed for this iteration per user direction.

## Files That Must Stay in Sync

| Change | Also update |
|--------|-------------|
| `VerificationResult` schema | `tools/verifier.py`, `extraction/agent.py` (`verify_totals` + `save_result`), `tests/test_cross_checks.py`, `tests/test_verifier.py` (new), `tests/test_extraction_agent.py` (new) |
| Mandatory-field helper | `tools/verifier.py` (`_collect_unfilled_mandatory`), all `_verify_{sofp,sopl,soci,socf,socie,sore}` dispatches |
| `save_result` gating | `extraction/agent.py` (`ExtractionDeps.last_verify_result`, `fill_workbook` resets it, `verify_totals` sets it, `save_result` gates on it) |
| Correction agent | `correction/agent.py` (new), `prompts/correction.md` (new), `server.py` (`run_multi_agent_stream` hook), `db/repository.py` (record pseudo-agent run), `web/src/lib/appReducer.ts` (`deriveAgentLabel`), `web/src/components/AgentTabs.tsx`, `web/src/components/RunDetailView.tsx` |
| `NotesPayload.source_note_refs` | `notes/payload.py`, `notes/writer.py` (emit `notes_payloads.json`), `prompts/_notes_base.md`, `prompts/notes_*.md`, `notes/agent.py` (render prompts), tests in `tests/test_notes_payload.py`, `tests/test_notes_writer.py` |
| Notes post-validator | `notes/validator_agent.py` (new), `prompts/notes_validator.md` (new), `notes/coordinator.py` (invoke after notes pass), `server.py` (SSE wiring), `web/src/lib/appReducer.ts`, `web/src/components/RunDetailView.tsx` |
| Tool: `rewrite_cell` | `tools/fill_workbook.py` (or a new `tools/rewrite_cell.py`), `tests/test_fill_workbook.py` |
