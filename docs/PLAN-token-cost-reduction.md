# Implementation Plan: Agent Token-Cost Reduction (History Replay + Tool-Output Hygiene)

**Overall Progress:** `60%` — all provider-independent code changes (Phases 1, 2, 4) landed + unit-tested. Phases 0/3 (live measurement) and Phases 5/6 (caching/compaction, gated on measurement) are outstanding and need live LLM runs.
**PRD Reference:** none — shaped via `/brainstorm` + peer review (2026-05-26). This doc is the source of truth.
**Last Updated:** 2026-05-26

## Summary
Agent runs re-send the entire conversation history on every turn (no trimming —
`agent.iter()` at [coordinator.py:448](../coordinator.py)). The dominant waste is
**old `view_pdf_pages` image blobs and the one-time bulky `read_template` summary
being re-billed on every subsequent model turn**, not the final JSON output
(~2% of tokens). We install a PydanticAI `history_processors` layer to strip stale
image/template payloads before each model call, measure the token/cost delta, then
trim verbose success-path tool text, and only then chase provider-specific prompt
caching.

## Key Decisions
- **Output format is NOT the problem** — final JSON is ~2% of tokens; agents write
  via tools and return short strings ([extraction/agent.py:528](../extraction/agent.py)).
  We are not changing output format.
- **History replay is the root cause** — `history_processors` (confirmed present in
  the `./venv` pydantic-ai 1.77.0 runtime) is the surgical hook; it edits the message
  list passed to the model without touching extraction logic.
- **Images first, not text trims** — per peer review, retained image payloads dwarf
  `verify_totals`/`fill_workbook` text. Reorder accordingly.
- **Caching is demoted** — DB spend is dominated by `gemini-3.5-flash` then `gpt-5.4`;
  OpenAI auto-caching wouldn't touch the top bucket and Gemini-via-proxy caching is
  unproven. Caching is step 4, after measurement.
- **Measure in tokens, not bytes** — the alarming "MB per trace" trace figures are
  payload bytes; image *token* cost is roughly fixed per image. Size all wins in tokens.
- **Reuse existing `cache_template=True`** for the template-dedup path rather than
  inventing a new cache ([extraction/agent.py:373](../extraction/agent.py)).

## Pre-Implementation Checklist
- [ ] 🟥 Confirm baseline: capture token/cost on a fixed PDF + model set before any change
- [ ] 🟥 Confirm the uncommitted canonical-concept-model work (CLAUDE.md gotcha #21) won't collide with `extraction/agent.py` / `coordinator.py` edits
- [ ] 🟥 Pick the fixed measurement fixture (PDF + statements + model) and record the exact command

## Tasks

### Phase 0: Baseline Measurement (no behavior change) — ⏸ OUTSTANDING (needs live LLM run)
- [ ] 🟥 **Step 0: Establish a repeatable token/cost baseline** — we cannot claim a saving without a before number.
  - [ ] 🟥 Choose one fixed input: `data/FINCO-Audited-Financial-Statement-2021.pdf`, a fixed `--statements` set, and one model (default `TEST_MODEL`).
  - [ ] 🟥 Run it 2–3× and record per-agent input/output tokens + cost. **Face + notes agents** have `run_agents` rows (`total_tokens`, `total_cost`); **scout does NOT** — rows are pre-created for face + notes only ([server.py:2680](../server.py), [server.py:2694](../server.py)). For scout, read usage from the scout runner's `result.usage()` / output logs, or add a scout telemetry row first.
  - [ ] 🟥 Write the numbers into the Baseline Measurements table at the bottom of this plan (mean + spread, since LLM runs vary).
  - **Verify:** Re-running the same command produces token counts within a stable band; baseline table is filled in (face, notes, scout). No code changed yet.

### Phase 1: History Processor for Stale Images (biggest, lowest-risk win) — 🟩 DONE
- [x] 🟩 **Step 1: Add a `history_processors` layer that omits old image blobs** — keep only the most recent image batch as real images; replace older images with a text placeholder so the agent can re-fetch on demand.
  - [ ] 🟥 Write a pure function `strip_stale_images(messages) -> messages` in a new `extraction/history_processors.py` (unit-testable in isolation).
  - [ ] 🟥 **Be generic over tool name.** Scout's tool is `view_pages` ([scout/agent.py:667](../scout/agent.py)), extraction + notes use `view_pdf_pages` ([extraction/agent.py:412](../extraction/agent.py), [notes/agent.py:1166](../notes/agent.py)). Match on **any** `ToolReturnPart` whose content carries `BinaryContent` rather than a hard-coded tool name — otherwise scout's step is a literal no-op.
  - [ ] 🟥 Rule: for all but the most-recent image batch, replace each `BinaryContent` with `"Page N image omitted from history; call <tool>([N]) again if needed."` Preserve the surrounding `=== Page N ===` text markers.
  - [ ] 🟥 **Do not mutate in place.** PydanticAI message parts are mutable dataclasses; mutating `ToolReturnPart.content` would also corrupt the in-memory conversation and the saved traces, not just the outbound request. Build new parts with `dataclasses.replace(...)` and a copied list.
  - [ ] 🟥 Wire `history_processors=[strip_stale_images]` into the `Agent(...)` constructor at [extraction/agent.py:394](../extraction/agent.py).
  - **Verify:** Unit test feeds a synthetic message list with 3 image batches → asserts only the last batch retains `BinaryContent`, earlier ones are text placeholders, page markers intact, **and the original input list is unchanged** (purity assertion). Then run the Phase 0 fixture and confirm token/cost drop vs baseline with `test_e2e.py` still green.

- [x] 🟩 **Step 2: Apply the same processor to scout AND the notes pipeline** — DONE. `strip_stale_images` wired into scout ([scout/agent.py](../scout/agent.py)) and notes ([notes/agent.py](../notes/agent.py)) `Agent(...)` constructors. Generic over tool name, so `view_pages` and `view_pdf_pages` both covered. — all three subsystems share the image-replay pattern; trimming is transport hygiene and does NOT touch the notes all-LLM-judgement invariant (CLAUDE.md #14).
  - [ ] 🟥 Scout: wire `strip_stale_images` into the scout `Agent(...)` at [scout/agent.py:618](../scout/agent.py) (tool `view_pages`).
  - [ ] 🟥 Notes: wire it into the notes `Agent(...)` at [notes/agent.py:1136](../notes/agent.py) (no `history_processors` today; runs via `agent.iter` at [notes/coordinator.py:687](../notes/coordinator.py) and [notes/listofnotes_subcoordinator.py:685](../notes/listofnotes_subcoordinator.py)).
  - **Verify:** Scout run still produces a valid infopack (statements + variants + note pages); a notes run still fills sheets correctly and respects the cell cap; token drop recorded for both.

### Phase 2: Stop the Bulky Template Summary Being Re-billed Every Turn — 🟩 DONE (history-trim mechanism)
- [x] 🟩 **Step 3: Trim/de-duplicate the `read_template` payload in history** — DONE via `strip_duplicate_template` (keeps first summary, replaces later copies with `"Template structure already provided above."`) + companion change so `read_template` returns a pointer when `cache_template=True` ([extraction/agent.py](../extraction/agent.py)). System-prompt embedding remains a Phase-5 caching-dependent optimization, not claimed as the token win here. — the ~12k-token summary is sent every turn.
  - [ ] 🟥 **Important correction:** `cache_template=True` is **not** a standalone token saver. It embeds the summary into the system prompt via [prompts/__init__.py:90](../prompts/__init__.py), and the system prompt is still resent on every turn. Worse, `read_template` *still* returns the full summary unconditionally ([extraction/agent.py:404-409](../extraction/agent.py)), so with `cache_template=True` the summary can land **twice**. Embedding only helps tokens if provider prompt-caching hits (Phase 5) — by itself it reorders cost, it does not remove it.
  - [ ] 🟥 **The real per-turn removal is a history-processor trim.** Keep the first `read_template` return (or the prompt-embedded copy) and replace later/duplicate copies with a one-line pointer `"Template structure already provided above."`. This is the mechanism that actually cuts tokens regardless of provider.
  - [ ] 🟥 Companion change: when `cache_template=True`, make `read_template` return `"Template structure already embedded in the system prompt above."` instead of the full summary, so the two copies never coexist.
  - [ ] 🟥 Treat system-prompt embedding strictly as a **caching-dependent** optimization, validated in Phase 5 — not as the token win here.
  - **Verify:** Fixture run shows further token drop attributable to the history trim (not just reordering); `test_e2e.py`, `test_template_reader.py`, and the abstract-row guard tests (`test_fill_workbook_abstract_guard.py`) stay green — the agent must still respect `[ABSTRACT ...]` markers and the no-residual-plug rule (CLAUDE.md #17).

### Phase 3: Measure the Delta — ⏸ OUTSTANDING (needs live LLM run, depends on Phase 0)
- [ ] 🟥 **Step 4: Re-measure on the identical fixture and quantify** — compare against Phase 0.
  - [ ] 🟥 Produce a before/after token + cost table (in tokens, not MB) for extraction and scout.
  - [ ] 🟥 Sanity-check extraction accuracy: filled workbook still balances; cross-checks pass at the same rate as baseline (compare `verify_totals` / cross-check outcomes).
  - **Verify:** Documented before/after table with a clear % reduction and an explicit "no accuracy regression" statement backed by cross-check results.

### Phase 4: Trim Verbose Success-Path Tool Output (provider-independent hygiene) — 🟩 DONE
- [x] 🟩 **Step 5: Slim `verify_totals` on the success path** — DONE. `_format_verify_result` omits the `computed_totals` dump when `is_balanced` is True; failure-path detail (mismatches, mandatory_unfilled, feedback) untouched ([extraction/agent.py](../extraction/agent.py)). — drop the full `computed_totals` JSON dump when balanced; the agent only acts on failures.
  - [ ] 🟥 Keep failure-path detail (mismatches + `mandatory_unfilled`) fully intact so self-correction still works.
  - **Verify:** `test_cross_checks.py` + verifier tests green; a deliberately-unbalanced fixture still gets the full failure feedback; balanced runs show shorter `verify_totals` returns.
- [x] 🟩 **Step 6: Collapse `fill_workbook` error/warning arrays** to counts + one-line summary, and stop returning the cost-report body from `save_result` — DONE. fill_workbook success message renders `N error(s)/warning(s):` + `; `-joined messages (double-booking content preserved); `save_result` writes the cost report to file only and returns just the confirmed paths ([extraction/agent.py](../extraction/agent.py)).
  - **Verify:** `test_fill_workbook_abstract_guard.py` + `test_fill_workbook_double_booking_guard.py` + `test_extraction_agent.py` green (53 passed). Guards assert on the `FillResult` object, not the tool string, so the slimmed message is safe.

### Phase 5: Provider-Specific Caching Experiments (only after measurement justifies it) — ⏸ OUTSTANDING (gated on Phase 3 measurement; mostly live experiments, not deterministic code)
- [ ] 🟥 **Step 7: Run caching as per-provider experiments, not a generic switch** — guided by the DB spend split (gemini-3.5-flash, then gpt-5.4). `CachePoint` semantics differ sharply by provider, so this is not a single "add a CachePoint after the system prompt" step.
  - [ ] 🟥 For OpenAI (`gpt-5.4`): automatic prefix caching — no code change; just confirm cache-read tokens appear in usage. Note `CachePoint` markers are explicitly filtered out for OpenAI ([openai.py:1406](../venv)), so don't add them.
  - [ ] 🟥 For Anthropic paths: insert a `CachePoint` into `UserPromptPart.content` ([messages.py:672](../venv)) — NOT "after the system prompt" generically — and confirm cache-read tokens appear.
  - [ ] 🟥 For Gemini-via-proxy (top spend bucket): inline `CachePoint` markers are ignored by the Google model ([google.py:972](../venv)). Probe whether the proxy itself reports any cache hits; do NOT assume caching works — document the finding either way.
  - **Verify:** Usage breakdown shows cache-read tokens on at least one provider path; net cost on the fixture drops further, or it's documented as "no cache support on the dominant (Gemini) path" — which would mean Phases 1–2 + 4 carry the load.

### Phase 6 (Optional, last): Semantic History Compaction — ⏸ OUTSTANDING (optional; only if Phases 1–5 leave material cost)
- [ ] 🟥 **Step 8: Collapse old fill/verify cycles into one-line summaries** — highest ceiling, highest behavioral risk; only if Phases 1–5 leave material cost on long/failing runs.
  - **Verify:** Long/failing-run fixture shows token drop AND extraction accuracy unchanged across `test_e2e.py` + cross-checks. Abandon if accuracy regresses.

## Rollback Plan
If something goes wrong:
- Each phase is an isolated change. Revert the specific commit — `history_processors` is a single constructor kwarg; removing it restores exact prior behavior.
- The processors are **pure functions over the message list** — they never mutate workbook/DB state, so a bad processor can only affect token usage and (worst case) agent accuracy on that run, never persisted data.
- Watch for accuracy regressions, not crashes: after each phase, the canary is the filled workbook still balancing and cross-checks passing at baseline rate. If cross-check pass-rate drops, revert that phase before proceeding.
- Caching (Phase 5) changes no logic; disabling is removing the `CachePoint` / config.

## Out of Scope (explicitly not doing)
- Changing the final output format (it's ~2% of tokens).
- Reducing PDF render DPI or image quality (accuracy risk, separate decision).
- Touching the notes pipeline's all-LLM-judgement *design* (CLAUDE.md #14) — note that Phase 1 *does* add image-history trimming to notes agents, which is transport hygiene only and leaves the judgement logic untouched.
- Raising/altering `MAX_AGENT_ITERATIONS` (CLAUDE.md #18).

## Baseline Measurements (fill during Phase 0)
| Run | Model | Extraction tokens | Scout tokens | Cost | Notes |
|-----|-------|-------------------|--------------|------|-------|
| _tbd_ | | | | | |
