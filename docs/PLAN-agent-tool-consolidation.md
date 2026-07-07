# Implementation Plan: Agent & Tool Consolidation

**Status:** Phases 1–3 shipped 🟩 (branch `chore/agent-tool-consolidation`).
Phase 4 **deferred** 🟥 by decision (2026-07-07): the JSON-string tools already
return actionable, self-correcting parse errors (the tool-design skill's primary
concern), and converting `write_notes` would trade away its tested per-item
batch tolerance while needing a live extraction run to validate the new schema —
not available in the implementation environment. Revisit Phase 4 when a live
smoke test can confirm a real model emits valid typed calls.

**Overall Progress:** `75%` (Phases 1–3 of 4; Phase 4 deferred)
**PRD Reference:** none — scope is the 2026-07-07 multi-agent architecture & tool-design review
(chat report; findings A1, T1–T4 of that review). Finding A2 (house-style floor
sunset) is **already done** — `notes/format_defaults.py` was deleted in commit
`ead3e85` and `tests/test_notes_format_sidecar.py:113` pins that it stays gone.
**Last Updated:** 2026-07-07

## Summary

Remove the one dead AI agent in the system (the old notes validator, fully
replaced by the notes reviewer), retire the three duplicate "singular" tools
the notes reviewer carries alongside its batch versions, and standardize the
small contract inconsistencies between agents' shared tools (calculator
batching, flag parameter names, one tool name, JSON-string parameters).
No behaviour changes for the operator; every step is a code/prompt/test change
with a pinning test run as its gate. No database schema changes anywhere.

## Key Decisions

- **Delete the validator, keep the detectors**: `notes/detectors.py` (the
  shared duplicate/gap detection logic) stays — it powers the live notes
  reviewer. Only the agent shell, its never-called server pass, and its
  dead-path tests go.
- **Gotcha #22's invariant moves, it doesn't vanish**: the atomic-workbook-save
  rule now anchors solely to `utils/workbook_io.py` (already the real home);
  the validator's `_atomic_save_workbook` re-export alias and its test import
  are re-pointed before the file is deleted.
- **Batch tools win**: `clear_note_cells` / `resolve_coverage_notes` /
  `verify_subnotes` fully subsume their singular twins (one item = a list of
  one) — same precedent as `read_note_cells`. Singulars are deregistered, not
  kept "just in case", because two tools with the same activation scenario is
  the exact selection-ambiguity the review flagged.
- **`kind` / `reason` become the canonical flag parameter names** (the notes
  reviewer's, more recent, shorter). The face reviewer's `raise_flag` renames
  `category`→`kind`, `reasoning`→`reason` at the tool signature only — the
  `reviewer_flags` DB column names are untouched (tool param ≠ column).
- **Scout tool names: sharpen descriptions, don't rename** — `discover_notes`
  vs `discover_notes_inventory` have genuinely distinct jobs; a rename ripples
  through prompts and tests for marginal gain. Docstring disambiguation is the
  surgical fix.
- **Typed parameters land last, one tool at a time** — schema changes alter
  what the model sees, so each conversion gets its own step, its own pinning
  test, and a mocked-E2E gate before the next one starts.
  `write_facts(facts: List[FactWrite])` is the in-repo reference pattern.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm no in-flight branch touches `notes/reviewer_agent.py` or
      `server.py`'s notes-pass region (memory: `feat/skill-first-workflow-references`
      is pending accept/revert — it touches prompts/workflow refs, verify no overlap)
- [ ] 🟥 `main` is clean and the run-63 / floor-removal work is committed (verified: HEAD `ead3e85`)
- [ ] 🟥 Baseline: full backend suite green before starting
      (`./venv/bin/python -m pytest tests/ -q` — conftest sets `AUTH_MODE=dev`)

## Tasks

### Phase 1: Delete the dead notes validator agent (review finding A1)

- [ ] 🟥 **Step 1: Re-home the two tests that borrow validator imports but pin LIVE behaviour**
  - [ ] 🟥 `tests/test_workbook_io_atomic.py:33` — import `atomic_save_workbook`
        from `utils/workbook_io.py` directly instead of the validator's
        `_atomic_save_workbook` alias
  - [ ] 🟥 `tests/test_notes_reviewer_clobber.py:27` — read the test first: if it
        pins live reviewer/persistence clobber protection, re-point its
        `NotesValidatorAgentDeps` / `_rewrite_cell_impl` imports to the live
        equivalents; if it only exercises validator internals, mark it for
        deletion in Step 3 instead
  - **Verify:** `./venv/bin/python -m pytest tests/test_workbook_io_atomic.py tests/test_notes_reviewer_clobber.py -q` passes with zero imports from `notes.validator_agent` remaining in those files

- [ ] 🟥 **Step 2: Remove the never-called server pass**
  - [ ] 🟥 Delete `server.py::_run_notes_validator_pass` (defined ~line 2107,
        zero call sites — the live path at server.py:5573 calls
        `_run_notes_reviewer_pass`), plus `NOTES_VALIDATOR_AGENT_ID` and the
        `NOTES_VALIDATOR_WALLCLOCK_TIMEOUT` constant (gotcha #18 notes the
        validator was never wrapped with the in-loop check anyway)
  - [ ] 🟥 Delete the dead-path test cases in `tests/test_peer_review_codex_fixes.py`
        (lines ~98–239: they monkeypatch `create_notes_validator_agent` and
        `build_validator_prompt_body`) and the validator cases in
        `tests/test_notes_review_provenance.py` — first confirming each deleted
        case's *behaviour* (provenance, crash-handling) is covered by the
        reviewer-pass equivalents; port any that aren't
  - **Verify:** `grep -rn "validator_agent" server.py` returns nothing.
        (Note: the `NOTES_VALIDATOR_*` timeout constants + pseudo-agent id are
        DELIBERATELY retained — the live notes-reviewer pass inherited them per
        gotcha #22, so a bare `grep "validator"` still matches and MUST NOT be
        "cleaned up".)
        `./venv/bin/python -m pytest tests/test_peer_review_codex_fixes.py tests/test_notes_review_provenance.py tests/test_notes_reviewer_coverage.py -q` passes

- [ ] 🟥 **Step 3: Delete the agent file and update the docs that anchor to it**
  - [ ] 🟥 Delete `notes/validator_agent.py` and `tests/test_notes_validator_agent.py`
  - [ ] 🟥 Fix `notes/detectors.py` module docstring (lines 6–11 reference the
        re-export arrangement that no longer exists)
  - [ ] 🟥 CLAUDE.md: rewrite gotcha #22's validator paragraphs to anchor the
        io-lock/atomic-save incident history to `utils/workbook_io.py`; update
        gotcha #18's validator-wallclock sentence; sweep `docs/NOTES-PIPELINE.md`
        and `docs/ARCHITECTURE.md` for validator mentions (`docs/Archive/` stays
        untouched — read-only audit trail)
  - **Verify:** `grep -rn "validator_agent" --include="*.py" . | grep -v venv`
        returns nothing; full suite green: `./venv/bin/python -m pytest tests/ -q`

### Phase 2: Retire the singular notes-reviewer tools (review finding T1)

- [ ] 🟥 **Step 4: Deregister the three singular tool registrations**
  - [ ] 🟥 Remove the `@agent.tool` registrations for `clear_note_cell`
        (reviewer_agent.py:1023), `resolve_coverage_note` (:1119),
        `verify_subnote` (:1206) — keep the shared internal implementations
        (`_do_clear`, verdict recorders) that the batch tools call
  - [ ] 🟥 Confirm the batch tools' guards (grounding, snapshot latch,
        prose-sheet check) are applied per-item, not just per-call — they must
        be exactly as strict as the singulars they replace
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_reviewer_coverage.py -q`
        — expected to FAIL at this point (tests still emit singular calls);
        the failure list is the exact work list for Step 6

- [ ] 🟥 **Step 5: Update the prompt and in-code guidance to batch-only**
  - [ ] 🟥 `prompts/notes_reviewer.md` lines 19, 21, 29–35: make the batch forms
        the only documented forms ("a single row is `rows=[49]`", matching the
        existing `read_note_cells` wording)
  - [ ] 🟥 `notes/reviewer_agent.py` guidance strings (~lines 458, 491, 532,
        547, 558) and docstrings that say "prefer this over calling X
        repeatedly" — X no longer exists
  - **Verify:** `grep -n "clear_note_cell\b\|resolve_coverage_note\b\|verify_subnote\b" prompts/notes_reviewer.md notes/reviewer_agent.py`
        shows only batch names and internal helper references

- [ ] 🟥 **Step 6: Migrate the pinning tests to batch calls**
  - [ ] 🟥 `tests/test_notes_reviewer_coverage.py`, `tests/test_notes_coverage_run_status.py`,
        `tests/test_notes_reviewer_self_verify.py` — convert singular
        `ToolCallPart` emissions to batch form with one-element lists; keep
        every guard assertion (grounding rejection, verdict validation,
        snapshot latch) semantically identical
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_reviewer_coverage.py tests/test_notes_coverage_run_status.py tests/test_notes_reviewer_self_verify.py tests/test_notes_coverage_api.py -q` green; then full suite green

### Phase 3: Standardize shared tool contracts (review findings T2, T4)

- [ ] 🟥 **Step 7: Batch the calculator everywhere**
  - [ ] 🟥 `correction/reviewer_agent.py:1485` and `notes/agent.py:1259`:
        `expression: str` → `expressions: List[str]`, same contract and
        docstring shape as the face agent's (one result per expression)
  - [ ] 🟥 Update `prompts/reviewer.md`, `prompts/spot_check.md`,
        `prompts/_notes_base.md` calculator mentions + affected tests
        (`tests/test_reviewer_tools.py` and notes-agent tool tests)
  - **Verify:** targeted pytest for reviewer + notes tool tests green; grep
        confirms no single-expression `calculator` registration remains

- [ ] 🟥 **Step 8: Align `raise_flag` parameter names across the two reviewers**
  - [ ] 🟥 Face reviewer (`correction/reviewer_agent.py:1769`):
        `category`→`kind`, `reasoning`→`reason` in the tool signature and
        docstring only; internal mapping to `reviewer_flags` columns unchanged
  - [ ] 🟥 Update `prompts/reviewer.md` / `prompts/spot_check.md` references and
        `tests/test_reviewer_tools.py` / `tests/test_reviewer_routes.py` call shapes
  - **Verify:** `./venv/bin/python -m pytest tests/test_reviewer_tools.py tests/test_reviewer_routes.py tests/test_reviewer_agent.py -q` green; flags still land in the DB with unchanged columns (assert in existing tests)

- [ ] 🟥 **Step 9: Rename `trace_cascade_source_tool` → `trace_cascade_source` (tool name only)**
  - [ ] 🟥 Register with an explicit tool name so the module-level pure function
        of the same name isn't shadowed; update `prompts/reviewer.md` line
        references and any test asserting the tool name
  - **Verify:** `./venv/bin/python -m pytest tests/test_reviewer_tools.py -q` green

- [ ] 🟥 **Step 10: Disambiguate the scout's two note-discovery tool descriptions**
  - [ ] 🟥 `scout/agent.py` — rewrite `discover_notes` and
        `discover_notes_inventory` docstrings so the first line states the
        distinct job and when to pick each ("note pages cited by ONE
        statement's face" vs "walk the WHOLE notes section into an inventory");
        no renames
  - **Verify:** docstring-only change — `./venv/bin/python -m pytest tests/test_page_hints.py -q` (and any scout tool tests) green

### Phase 4: Typed tool parameters replacing JSON strings (review finding T3)

Each step converts ONE tool from a raw JSON-string parameter to typed
pydantic models (the `write_facts(facts: List[FactWrite])` pattern), so
pydantic-ai validates before the tool runs and the model self-corrects on
mismatch instead of burning a turn on a parse error.

- [ ] 🟥 **Step 11: `write_notes(payloads_json)` → `payloads: List[...]`**
  - [ ] 🟥 Define the pydantic model mirroring `NotesPayload` (content,
        numeric_values, evidence, source_pages, note_num, source_note_refs,
        parent_note/sub_note, format_ops); accept the list directly
  - [ ] 🟥 Preserve the Sheet-12 sink semantics exactly: identical-content
        re-send still REPLACES (run-63 nudge contract), sub-agent
        payload_sink append path unchanged
  - [ ] 🟥 Update `prompts/_notes_base.md` write instructions + writer tests,
        `tests/test_notes_format_sidecar.py`, retry-budget tests
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_retry_budget.py tests/test_notes_format_sidecar.py tests/test_notes_prompt_phase1.py -q` green; mocked E2E `./venv/bin/python -m pytest tests/test_e2e.py -q` green

- [ ] 🟥 **Step 12: Coverage receipts → typed lists**
  - [ ] 🟥 `submit_batch_coverage(receipt_json)` (notes/agent.py:1573) and
        `submit_face_coverage(receipt_json)` (extraction/agent.py:1010) take
        `receipts: List[CoverageReceiptModel]`; validation errors that today
        come back as string lists become schema-level retries
  - **Verify:** coverage tests (`tests/test_coverage_checklist.py`, Sheet-12
        coverage tests, `tests/test_scout_face_line_refs_wiring.py`) green

- [ ] 🟥 **Step 13: `save_infopack(infopack_json)` → typed `Infopack` parameter**
  - [ ] 🟥 Keep the existing coercion/rejection logic (hallucinated-variant and
        implausible-note-number guards) as post-validation — schema typing
        replaces only the parse layer, not the judgement guards
  - **Verify:** `./venv/bin/python -m pytest tests/test_infopack_context_schema.py tests/test_scout_populates_context.py -q` green

- [ ] 🟥 **Step 14 (optional, decide at the time): face `save_result(fields_json)`**
  - Freeform per-statement fields make this the weakest fit for a rigid model;
    convert only if a stable field set falls out naturally, otherwise document
    why it stays a JSON string
  - **Verify:** if converted — `tests/test_e2e.py` + extraction agent tests green

- [ ] 🟥 **Step 15: Live smoke test of the changed contracts**
  - [ ] 🟥 One live run (`python3 run.py data/FINCO-Audited-Financial-Statement-2021.pdf --statements SOFP SOPL --notes corporate_info list_of_notes`)
        confirming a real model handles the typed schemas — mocked tests can't
        prove the model *generates* valid typed calls
  - **Verify:** run completes, notes cells written, no schema-retry storms in
        the trace files

## Rollback Plan

- Every phase lands as its own commit on branch `chore/agent-tool-consolidation`;
  a bad phase is one `git revert` with no cross-phase entanglement.
- Phases 1–3 are pure code/prompt/test — no DB schema changes, no data
  migrations, nothing to check in existing run databases after a revert.
- Phase 4 changes what the model is asked to emit; if a live run shows schema
  thrash (repeated validation retries in the conversation trace), revert only
  the affected step's commit — each tool converted independently for exactly
  this reason.
- The validator deletion (Phase 1) is recoverable from git history; nothing at
  runtime references it, so a revert is only ever needed for audit reasons.
