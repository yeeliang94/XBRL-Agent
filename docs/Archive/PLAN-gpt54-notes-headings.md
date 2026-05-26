# Implementation Plan: GPT-5.4 default + Notes headings + Sub-sheet lumping fix

**Overall Progress:** `95%` _(Mac live smoke + Windows live smoke owed to user)_
**PRD Reference:** brainstorm transcript in session (no separate PRD — scope is focused and agreed).
**Last Updated:** 2026-04-24

## Summary

Three coupled changes to improve Windows-proxy extraction quality. (1) Make
`openai.gpt-5.4` the global default model for scout, extraction, and notes
agents on both Mac and Windows. (2) Enforce consistent note headings in every
notes cell via a **structured payload**: agents emit `parent_note` and (optional)
`sub_note` objects, the writer deterministically prepends two `<h3>` lines before
the body so the LLM can no longer drift. (3) Tighten SOFP-Sub-CuNonCu and
SOPL-Analysis-Function prompts so the agent emits one sub-sheet row per note
breakdown line instead of lumping the total onto the face sheet. Built red-green
TDD: every behavioural change lands with a failing test first.

## Key Decisions

- **Flagship GPT-5.4 everywhere, no scout downgrade** — user asked for "5.4 for
  everything". Mini/Nano tiers remain available via the Settings UI for
  individual-run cost tuning, but defaults flip to the flagship across the
  board.
- **`openai.gpt-5.4` id on both platforms** — `server._create_proxy_model` already
  strips the `openai.` prefix for direct-mode OpenAI and preserves it for proxy
  mode. One canonical id keeps `.env`, `XBRL_DEFAULT_MODELS`, and the registry
  (`config/models.json`) aligned; no platform forks.
- **Structured payload, not prompt-only, for notes headings** — user confirmed the
  durability guarantee matters more than the schema-change cost. Two new
  optional-object fields on `NotesPayload`:
  - `parent_note = {"number": "5", "title": "Material Accounting Policies"}` (required when any heading is wanted)
  - `sub_note = {"number": "5.4", "title": "Property, Plant and Equipment"}` (optional — omit for top-level notes)
  The writer prepends `<h3>{number} {title}</h3>` for each present object before
  calling the existing sanitiser/truncator/flattener chain. No changes needed
  in the TipTap editor — `<h3>` already renders.
- **Headings apply to all 5 notes templates** — Corp Info, Acc Policies, List of
  Notes, Issued Capital, Related Party. The writer applies the prepend uniformly;
  prompt language lives once in `prompts/_notes_base.md` so per-template prompts
  don't drift.
- **Sub-sheet lumping is a prompt-discipline fix, not a tool/contract fix** — logs
  show the agent does view the note pages but still writes a lump sum. GPT-5.4's
  better instruction-following plus a sharper "one row per breakdown line or it's
  a bug" rule in `prompts/sofp.md` / `prompts/sopl.md` should close the gap. If
  this still regresses post-change, escalate in a follow-up plan (e.g. force a
  `read_template → view_pdf_pages(note_pages) → fill_workbook` tool-order contract).
- **Commit boundary = phase boundary** — five phases, five commits, each revertable
  in isolation.

## Pre-Implementation Checklist

- [x] 🟩 Brainstorm resolved (model swap + structured payload + prompt tightening agreed)
- [x] 🟩 Clarifications locked (GPT-5.4 flagship everywhere; both platforms; both heading lines; applied to all 5 notes templates)
- [x] 🟩 No conflicting in-progress work (previous `PLAN.md` closed at 100%)
- [ ] 🟥 `OPENAI_API_KEY` confirmed present in `.env` (already present in current working copy)
- [ ] 🟥 Windows-proxy `openai.gpt-5.4` model-id availability confirmed at LiteLLM/enterprise proxy level (user to verify before a live Windows run)

## Tasks

### Phase 1: Default model swap — GPT-5.4

Lowest risk, no logic changes, pure config.

- [x] 🟩 **Step 1: Red — failing test for resolved default model**
  - [x] 🟩 Added `test_default_model_is_gpt_5_4_for_every_agent_role` in `tests/test_settings_api.py`; also updated `test_get_settings_default` to assert `openai.gpt-5.4` instead of the old Gemini default.
  - **Verified:** both tests failed red with the expected "vertex_ai.gemini-3-flash-preview, expected 'openai.gpt-5.4'" diff.

- [x] 🟩 **Step 2: Green — update `.env`, `.env.example`, and registry default flag**
  - [x] 🟩 `.env`: `TEST_MODEL=openai.gpt-5.4`, `SCOUT_MODEL=openai.gpt-5.4`, refreshed `XBRL_DEFAULT_MODELS` JSON so scout + all 5 statement roles point at `openai.gpt-5.4`.
  - [x] 🟩 `.env.example`: mirrored the same defaults; rewrote the comment block to list the full id set (`openai.gpt-5.4-mini`, `openai.gpt-5.4-nano`, Anthropic/Gemini alternates).
  - [x] 🟩 `config/models.json`: reordered so `openai.gpt-5.4` is first (Settings UI renders in file order). All 9 entries preserved.
  - [x] 🟩 Replaced the 5 hardcoded `"google-gla:gemini-3-flash-preview"` defaults in `coordinator.py`, `extraction/agent.py`, `run.py` (×2), `scout/agent.py` (×2), `scout/calibrator.py`, `scout/vision.py` with `"openai.gpt-5.4"` via a scripted bulk replace.
  - [x] 🟩 Replaced the 6 `os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")` fallbacks in `server.py` with `"openai.gpt-5.4"`.
  - **Verified:** `python3 -m pytest tests/test_settings_api.py -v` → 4/4 green; the 2 red tests from Step 1 now pass.

- [x] 🟩 **Step 3: Green — fix tests that hardcode the old default**
  - [x] 🟩 Audited all 7 test files flagged in the plan. Only `test_settings_api.py` needed a default-value change (done in Step 1 alongside the red test). The others — `test_notes_parallel_resolver.py`, `test_provider_routing.py`, `test_token_tracker.py`, `test_connection_endpoint.py`, `test_integration.py`, `test_settings.py`, `web/src/__tests__/api.test.ts` — use Gemini ids either as fixture data (not asserting anything about defaults) or as deliberate Gemini-specific behaviour tests (provider routing, parallelism mapping, pricing math). Changing those would either be no-op noise or would break legitimate Gemini-specific assertions.
  - **Verified:** `python3 -m pytest tests/ -q` → **1167 passed, 2 skipped**; `cd web && npx vitest run` → **469 passed**. Same pass count as pre-change with the 2 new red-test cases now green.

- [ ] 🟨 **Step 4: Manual smoke — one CLI run end-to-end on Mac direct mode** _(user-owed; Phase 2–4 do not depend on it)_
  - [ ] 🟨 `python3 run.py data/FINCO-Audited-Financial-Statement-2021.pdf --statements SOFP` and confirm `output/run_XXX/filled.xlsx` materialises with SOFP values populated.
  - [ ] 🟨 Check the run-summary shows `model: "gpt-5.4"` or `"openai.gpt-5.4"` — not a Gemini id.
  - **Verify:** run completes without `OPENAI_API_KEY` errors; `compare_results.py` returns a non-catastrophic diff.

**Commit 1:** `default: swap to openai.gpt-5.4 for scout + all 5 statement roles`

---

### Phase 2: Notes headings — structured payload (schema + writer) 🟩

Core durability change. Red-green on the payload, then on the writer.

- [x] 🟩 **Step 5: Red — payload-level tests for the new fields**
  - [x] 🟩 Added 9 new cases in `tests/test_notes_payload.py` covering `parent_note`/`sub_note` shape, required-when-non-empty gate, and the empty-payload exemption.
  - **Verified:** 9 failures red (AttributeError + DID NOT RAISE), others still green.

- [x] 🟩 **Step 6: Green — add fields to `NotesPayload`**
  - [x] 🟩 `notes/payload.py`: added `parent_note: Optional[dict]` and `sub_note: Optional[dict]`, plus a `_validate_heading()` helper that enforces non-empty `number` + `title`. Validation in `__post_init__` makes `parent_note` required on any payload with content or numeric_values (mirrors the evidence gate); empty-signal payloads are exempt.
  - [x] 🟩 Updated the file-level docstring to describe the two fields and the writer's role in rendering the markup.
  - **Verified:** `python3 -m pytest tests/test_notes_payload.py -v` → 22/22 green.

- [x] 🟩 **Step 7: Red — writer prepend tests**
  - [x] 🟩 Added 4 new tests at the end of `tests/test_notes_writer.py`: parent-only prepend, parent+sub prepend with correct order, numeric-only payloads have no headings injected, headings survive truncation.
  - **Verified:** 4 failures red (heading text not in cell; ordering assertions failed).

- [x] 🟩 **Step 8: Green — writer prepends `<h3>` lines**
  - [x] 🟩 `notes/writer.py`: added `_inject_headings()` that prepends `<h3>{number} {title}</h3>` for parent, then (if present) sub, to `payload.content`. Wired into `_sanitize_payload()` as the first step so injection runs before sanitise + truncate — whitelisted `<h3>` tags survive the sanitiser, and the char cap applies to the combined text so the footer always sits after the headings.
  - [x] 🟩 Numeric-only payloads (no `content`) return unchanged — numeric cells hold a number, not prose.
  - [x] 🟩 Legacy-payload safety: if a caller constructs a `NotesPayload` with content but no `parent_note` (possible in test paths that predate the change), `_inject_headings` returns unchanged rather than crashing.
  - **Verified:** Step 7 cases green; `python3 -m pytest tests/test_notes_writer.py -v` → 38/38 green.

- [x] 🟩 **Step 9: Update notes agent parser + writer reconstructors to pass through new fields**
  - [x] 🟩 `notes/agent.py` (raw-JSON parser at line 1200): pulls `parent_note` and `sub_note` from the LLM-emitted JSON and forwards to `NotesPayload`. Missing/malformed values fall through to the dataclass validator, which rejects non-empty payloads without parent_note — the resulting "Invalid payload" error lands in the existing errors list.
  - [x] 🟩 `notes/writer.py` (`_sanitize_payload` clone + `_combine_payloads` merge): both reconstructors thread `parent_note` and `sub_note` through so the heading hierarchy survives the sanitise and dedup passes.
  - [x] 🟩 `notes/persistence.py`: overlay pipeline round-trips HTML content — headings are part of `content` at persistence time, so no schema change needed; verified by running `tests/test_notes_cells_persistence.py` + `tests/test_overlay_on_merged_workbook.py`.
  - [x] 🟩 Bulk test-fixture update: 54 `NotesPayload(...)` sites across 21 test files had `parent_note={"number": "1", "title": "Test Note"}` inserted via a balanced-paren Python script. Two sites needed manual fixes (one inline-comment collision; one multi-line `content=(...)` pattern the script didn't detect). Three factory helpers (`_make_payload`, `_p`, explicit NotesPayload in `test_notes_char_limit.py`) were updated manually. Two test assertions were relaxed from `==` / `startswith` to substring checks to accommodate the heading prepend in the cell value.
  - **Verified:** full backend suite → **1180 passed, 2 skipped**; frontend suite → **469 passed**.

**Commit 2:** `notes: structured payload with parent_note + sub_note headings`

---

### Phase 3: Notes headings — prompt contract 🟩

Agent-facing change. Tell the LLM about the new required fields.

- [x] 🟩 **Step 10: Red — prompt-contract invariants test**
  - [x] 🟩 Added 4 new cases in `tests/test_notes_prompts_emit_html_contract.py`: `parent_note` documented, `sub_note` documented, writer-injects-headings rule, worked example with `"number"` / `"title"` keys.
  - **Verified:** 4 failures red (all 4 phrases absent from base prompt).

- [x] 🟩 **Step 11: Green — update `prompts/_notes_base.md`**
  - [x] 🟩 Added two required-field blocks after `source_note_refs` describing `parent_note` and `sub_note` with shape examples.
  - [x] 🟩 Added a "Heading markup is writer-owned" subsection that explicitly forbids prepending `<h3>` manually.
  - [x] 🟩 Added two worked-example JSON payloads (top-level note with `parent_note` only; sub-note with both fields), plus a line describing the expected writer rendering.
  - **Verified:** 13/13 tests in `tests/test_notes_prompts_emit_html_contract.py` green.

- [x] 🟩 **Step 12: Green — update per-template prompts where they show example payloads**
  - [x] 🟩 None of the 5 per-template prompts carry example JSON payloads — they inherit the OUTPUT CONTRACT block from `_notes_base.md`. Single source of truth; nothing to duplicate. Confirmed by grepping for `chosen_row_label` / `"content":` / `"evidence":` across `prompts/notes_*.md` — zero hits.
  - **Verified:** `python3 -m pytest tests/test_notes_prompt_phase1.py tests/test_notes_prompts_emit_html_contract.py -v` → 17/17 green.

**Commit 3:** `prompts: document parent_note + sub_note notes-heading contract`

---

### Phase 4: Sub-sheet lumping — SOFP + SOPL prompt tightening (template-first rule)

No schema change. Sharpen the breakdown rule so it gates on **template
granularity**, not on note line count (see feedback memory
`feedback_sub_sheet_prompt_wording.md`). Malaysian notes often break
things down more granularly than the template supports — a rigid quota
forces the agent to either invent rows or skip valid data. The correct
discipline is "match note lines to sub-sheet fields where both exist".

- [x] 🟩 **Step 13: Red — prompt-content test asserting the template-first rule is present**
  - [x] 🟩 Added 4 new cases in `tests/test_notes_prompt_phase1.py`:
    - Positive: `prompts/sofp.md` names "matching sub-sheet field" (template-first gate).
    - Positive: `prompts/sofp.md` calls out "lump sum" + "face sheet" as the failure mode.
    - Negative: `prompts/sofp.md` does NOT contain "one sub-sheet row per breakdown line" or "must write 5 sub-sheet rows" (the rejected quota rule).
    - Positive: `prompts/sopl.md` references "matching" + "analysis" and the lumping failure.
  - **Verified:** 3 positive cases failed red (missing phrases); negative case already passed (neither prompt carries the rejected wording, as expected for a fresh rewrite).

- [x] 🟩 **Step 14: Green — rewrite `prompts/sofp.md` strategy step with the template-first rule**
  - [x] 🟩 Replaced soft STRATEGY step 3 with a 5-sub-step template-first checklist (follow note reference → read sub-sheet field list → match note lines to fields → roll up when unmatched → leave empty when no matching note line).
  - [x] 🟩 Added a new "FAILURE MODE TO AVOID" section stating the asymmetric case explicitly.
  - [x] 🟩 Added a "WORKED EXAMPLES" section with both shapes: template-granular ("Other payables" → Accruals + Other payables split) and note-granular ("Trade receivables – third parties + related companies" → single combined "Trade receivables" roll-up).
  - **Verified:** Step 13 SOFP positive assertions green.

- [x] 🟩 **Step 15: Green — rewrite `prompts/sopl.md` with the same template-first rule**
  - [x] 🟩 Rewrote STRATEGY step 3 for SOPL-Analysis with the same 5-sub-step checklist, anchored on Analysis sub-sheet fields (Revenue by type, CoS components, Other income, Finance income, Director remuneration, Employee benefits).
  - [x] 🟩 Added "FAILURE MODE TO AVOID" for Revenue-cross-sheet-formula case (face Revenue cell pulls from Analysis sub-sheet; lumping on face sheet leaves Revenue at zero after formula recalc).
  - [x] 🟩 Added two Revenue + Employee-benefits worked examples covering both shapes.
  - **Verified:** Step 13 SOPL positive assertion green.

- [x] 🟩 **Step 16: Green — parity check for SOFP order-of-liquidity and group overlay**
  - [x] 🟩 `prompts/sofp_orderofliquidity.md`: applied the same 5-sub-step checklist. Kept the existing "main sheet is standalone" language since the OrdOfLiq face sheet has no cross-sheet formulas; the sub-sheet is audit-trail only but the match-where-available rule still applies.
  - [x] 🟩 `prompts/_group_overlay.md`: no change — dual-column logic is orthogonal to the breakdown rule.
  - **Verified:** full backend suite `python3 -m pytest tests/ -q` → **1188 passed, 2 skipped**; frontend `vitest run` → **469 passed**.

**Commit 4:** `prompts: tighten SOFP + SOPL breakdown rule (template-first matching)`

---

### Phase 5: Verification — integration + live smoke

End-to-end evidence the three changes work together on a real PDF.

- [x] 🟩 **Step 17: Full automated suite green on the bundled test data**
  - [x] 🟩 `python3 -m pytest tests/ -q` → **1188 passed, 2 skipped** (up from 1167 pre-change — the 21-test increase matches the new red-green pairs across Phases 1–4).
  - [x] 🟩 `cd web && npx vitest run` → **469 passed** (unchanged; no frontend-visible changes).
  - [x] 🟩 `python3 -m pytest tests/test_e2e.py -v` — full 5-agent mocked pipeline green.
  - **Verified:** all three suites green.

- [ ] 🟨 **Step 18: Mac live smoke — SOFP + SOPL + one notes template** _(user-owed)_
  - [ ] 🟨 `python3 run.py data/FINCO-Audited-Financial-Statement-2021.pdf --statements SOFP SOPL --notes accounting_policies`.
  - [ ] 🟨 Open `output/run_XXX/filled.xlsx` in Excel. Spot-check:
    - SOFP-Sub-CuNonCu has values in multiple breakdown rows (Accruals, Deposits, Trade receivables, etc.) — not a single line.
    - SOPL-Analysis-Function has values in Revenue-by-type and Other-income sub-rows — not lumped onto the face sheet.
    - Notes-SummaryofAccPol cells open with heading lines (e.g. "5 Material Accounting Policies\n5.4 Property, Plant and Equipment\n…") before the body.
  - [ ] 🟨 `compare_results.py output/run_XXX/filled.xlsx SOFP-Xbrl-reference-FINCO-filled.xlsx` — no new catastrophic diffs vs. a pre-change baseline.
  - **Verify:** user-acceptance on a concrete run.

- [ ] 🟨 **Step 19: Windows live smoke** _(user-owed)_
  - [ ] 🟨 User runs `start.bat` on the enterprise laptop, triggers a group-filing SOFP+SOPL run, confirms the three behaviours above.
  - **Verify:** user reports sub-sheet breakdowns populated + notes cells carry headings + GPT-5.4 visible in run metadata.

**Commit 5:** `docs: close PLAN — model + notes-heading + sub-sheet fixes verified`

---

## Rollback Plan

Each phase has its own commit; `git revert` the offending phase independently.

- **Phase 1 (model swap)** — revert the commit; defaults fall back to Gemini 3 Flash. No data migration. Historical runs already record their model id in the DB so audit trail is preserved.
- **Phase 2 (structured payload)** — revert the commit pair (payload + writer). Existing `notes_cells` DB rows already carry rendered HTML with headings, so they remain valid after revert (headings just won't be injected on future runs).
  - **If a persisted `notes_cells` row has a heading the user edited:** the edit survives independent of revert (the DB row is the source of truth per CLAUDE.md gotcha #16).
- **Phase 3 (prompt contract)** — pure text. Revert the commit; agents go back to the previous contract. No on-disk state to rebuild.
- **Phase 4 (prompt tightening)** — same as Phase 3.
- **Phase 5 (verification)** — doc-only.

If the model swap itself causes an unexpected failure (e.g. proxy routing misconfig), the quickest rollback is a single `.env` edit (`TEST_MODEL=vertex_ai.gemini-3-flash-preview`) — no redeploy needed. Phase 1 commits are structured so reverting just the `.env`-touching commit is sufficient to restore runtime behaviour while keeping the test-fixture updates.

## Rules

- **TDD discipline:** every behavioural change lands in a red-green pair. A
  step that edits code without a failing test in the prior step is being
  skipped — stop and back up.
- **No scope creep:** do not refactor the notes coordinator, do not touch the
  cross-check framework, do not redo the Excel download pipeline. The writer
  change is a single prepend; the prompt changes are text; the model change
  is config.
- **One canonical model id:** `openai.gpt-5.4`. Don't sprinkle raw `gpt-5.4` or
  `openai:gpt-5.4` variants through the code — the prefix is what lets
  `_create_proxy_model` route correctly on both platforms.
- **Commit boundary = phase boundary.** Five commits, five reverts if needed.
- **Headings are writer-owned, not LLM-owned.** If a prompt or example ever
  re-introduces manual `<h3>` prepending in `content`, the writer double-injects
  and cells ship with duplicate headings. The sanitiser does not dedupe. Guard
  this with the Step 10 prompt-contract test.
