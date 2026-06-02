# Implementation Plan: Prompt Caching (§6) + Agent Effectiveness (§9)

**Overall Progress:** `45%` — Phases 0,1,2,4,5 ✅ (Phase 3 gated on live telemetry; Step 5.3 moved to Phase 6)
**PRD Reference:** [docs/REVIEW-prompts-and-caching.html](REVIEW-prompts-and-caching.html) — §6 (caching recommendations) and §9 (effectiveness problems)
**Last Updated:** 2026-06-02
**Branch:** `prompt-caching-and-effectiveness`

> **Phase 0.1 finding (resolves the report's open question):** the real runtime is `venv/bin/python` →
> **pydantic-ai 1.77.0** (the `0.8.1` in the review was the *system* python, a red herring). Run all tests with
> `venv/bin/python -m pytest`. Confirmed caching APIs on 1.77.0:
> - Usage: normalized `cache_read_tokens` + `cache_write_tokens` (on `RequestUsage`/`RunUsage`).
> - `CachePoint` exists.
> - **Direct Anthropic:** `AnthropicModelSettings.anthropic_cache_instructions` / `anthropic_cache_tool_definitions` / `anthropic_cache_messages` — clean, no `extra_body` hack.
> - **OpenAI:** `OpenAIChatModelSettings.openai_prompt_cache_key` / `openai_prompt_cache_retention` — first-class.
> - Proxy-routed Claude is still `OpenAIChatModel`, so the `anthropic_*` flags won't apply there — that path stays the harder one (Step 2.2).

## Summary
Two parallel tracks from the peer-reviewed prompt/caching report. **Track A (caching, §6)** reduces LLM cost by first *measuring* cache effectiveness, then enabling provider-correct prompt caching — the rest of the caching work is deliberately gated on the telemetry so we never optimize blind. **Track B (effectiveness, §9)** fixes prompt/loop defects that make agents extract wrong numbers, drop disclosures, or run out of budget. Track B changes agent behavior, so every phase is validated against a real run plus its pinning test.

## Key Decisions
- **Telemetry comes first, and gates the rest of caching** — later §6 items (`cache_template` re-eval, Sheet-12 burst caching, non-determinism audit) are explicitly conditional on hit-rate data. We do not implement them on faith. *Why:* the report's whole argument is "don't cache a bloated prompt and declare victory"; without measurement we can't tell if OpenAI auto-caching already covers the win.
- **Anthropic caching is path-dependent, not one chokepoint** — direct mode is native `AnthropicModel`; the default Mac path routes Claude as `OpenAIChatModel` through LiteLLM (`server.py:768`). Two mechanisms, gated on `proxy_url`. *Why:* a single `_create_proxy_model` change would silently no-op on the default path.
- **Capture cache *writes*, not just reads** — Anthropic bills cache writes at a premium; reads-only telemetry reports phantom savings. *Why:* avoids mispricing write-heavy runs.
- **Keep the no-plug guard intact while making verifier feedback directional** — we add diagnosis ("which side / likely sign error on row X"), we do **not** loosen the anti-plug rule (gotcha #17). *Why:* the goal is to give the agent a gradient, not permission to balance by plugging.
- **Provider-aware temperature, Gemini stays at 1.0** — only lower temperature off Gemini, and validate per model (some GPT-5 reasoning models ignore it). *Why:* gotcha — Gemini-3-through-proxy genuinely requires 1.0 ([CLAUDE.md:140](../CLAUDE.md), the "Temperature Constraint" rule).
- **De-hardcode coordinates rather than trust literals** — SOCIE/notes prompts should read row numbers from `read_template()`, matching the codebase's regeneration-survivable design (gotcha #3/#15). *Why:* literals drift when templates regenerate and there is no label-match safety net.
- **Every effectiveness change updates its pinning test in the same step** — per CLAUDE.md, "done" = the pinning test passes. *Why:* several targets (verifier wording, notes prompt, SOCIE) are guarded by tests that will fail loudly if we change behavior without updating them.

## Pre-Implementation Checklist
- [ ] 🟥 Scope confirmed: Everything (§6 + §9) — confirmed by user
- [ ] 🟥 Review report (Rev 2) is the source of truth; no separate PRD
- [ ] 🟥 No conflicting in-progress work (`git status` clean except this plan + the review HTML)
- [ ] 🟥 Capture a **baseline run** before any change (Phase 0) for before/after comparison

---

## Tasks

### Phase 0: Pre-flight — environment + baseline
- [x] 🟩 **Step 0.1: Reconcile pydantic-ai version** — DONE. venv = **1.77.0**; caching APIs confirmed (see header note). Run all tests with `venv/bin/python -m pytest`.
- [ ] 🟨 **Step 0.2: Capture a baseline run** — HANDED TO USER (needs live LLM + key). Run `venv/bin/python run.py data/FINCO-Audited-Financial-Statement-2021.pdf --statements SOFP SOPL SOCI SOCF SOCIE` and record per-run token totals for before/after.
  - **Verify:** a baseline `output/run_NNN/` exists with a filled workbook; token totals recorded.

---

### Phase 1: Cache telemetry (the gate for all of Track A)
- [x] 🟩 **Step 1.1: Read + write cache tokens through the usage path** — DONE. Added `_cache_read_tokens` / `_cache_write_tokens` helpers (`agent_runner.py`, mirroring `_in_tokens`), captured per-turn deltas in the `agent.iter()` loop, and summed them into the `AgentResult` rollup in `coordinator._finalize`.
  - **Verify:** ✅ `tests/test_db_schema_v15.py::test_cache_rollup_round_trips` + full backend suite (1881 passed).
- [x] 🟩 **Step 1.2: Persist + display cache metrics** — DONE.
  - [x] 🟩 Schema v14→v15: nullable `cache_read_tokens` / `cache_write_tokens` on `run_agents` + `run_agent_turns` (idempotent `ALTER`, same pattern as v8/v14) + `tests/test_db_schema_v15.py` pin.
  - [x] 🟩 Repository: `RunAgent` fields, `finish_run_agent` params, `insert_agent_turns` columns, `fetch_run_agents` / `fetch_agent_turns` readers.
  - [x] 🟩 Server: both `finish_run_agent` call sites pass the cache rollups; `api/runs.py` adds cache to `token_breakdown` + `telemetry_rollup`.
  - [x] 🟩 Frontend: `types.ts` (optional fields), `AgentTelemetryPanel.tsx` (rollup line + two per-turn columns). ✅ vitest 632 passed, tsc clean.
- [ ] 🟨 **Phase 1 Verify (gate):** HANDED TO USER — run the sample PDF (CLI or web UI) and read the Telemetry tab / `telemetry_rollup.cache_read_tokens`. On `openai.gpt-5.4` this answers the report's open question: *is auto-caching hitting today?* **This number decides Phase 3 scope.**

> **PAUSE after Phase 1** — report the measured hit rate before proceeding. Track A phases 3+ are gated on it.

---

### Phase 2: Provider-correct explicit caching (P0)
**Implemented via a single `build_model_settings(model, *, cache_key)` helper
(`model_settings.py`) wired into all 6 multi-turn / repeated-prefix agents
(extraction, notes, scout, reviewer, notes-validator, vision). One-shot
structured agents `scout/vision.py` + `scout/calibrator.py` deliberately skipped
— single request each (no repeated prefix to cache) and they carry no
temperature pin today, so converting them would change behaviour for ~0 benefit.**
- [x] 🟩 **Step 2.1: Anthropic breakpoint — direct mode** — DONE. `AnthropicModel` branch sets `anthropic_cache_instructions=True` + `anthropic_cache_tool_definitions=True` (caches the static system prompt + tool defs). Clean 1.77 API, no `CachePoint`/`extra_body` needed.
  - **Verify (USER, live):** a direct-Anthropic run (`--model bedrock.anthropic.claude-...` direct mode with `ANTHROPIC_API_KEY`) shows non-zero `cache_write_tokens` on turn 1 and `cache_read_tokens` on later turns in Telemetry.
- [ ] 🟨 **Step 2.2: Anthropic breakpoint — proxy/LiteLLM mode** — KNOWN GAP, documented not fixed. Claude through the proxy arrives as `OpenAIChatModel`, so it takes the OpenAI branch and the `anthropic_*` flags can't apply; caching it needs `cache_control` markers the OpenAI wire format can't carry from here. Flagged for a follow-up (proxy-side injection). The default model is OpenAI, so this isn't the common path.
- [x] 🟩 **Step 2.3: OpenAI explicit cache controls** — DONE. `OpenAIChatModel` branch sets `openai_prompt_cache_key` (stable per agent-type) + `openai_prompt_cache_retention="24h"` via the first-class 1.77 settings (no `extra_body` hack needed). Covers direct OpenAI **and** every proxy-routed OpenAI model.
  - **Verify (USER, live):** default-path run hit rate (Telemetry `cache_read_tokens`) ≥ Phase-1 baseline; note the delta.
- [x] 🟩 **Mocked verification:** `tests/test_model_settings.py` (6 tests — per-provider dispatch, no cross-provider flag leakage, temperature seam for Phase 9). Full backend suite green.
- [ ] 🟨 **Phase 2 Verify (USER, live):** measure per-provider cache reads/writes + cost-per-run delta vs. the Phase-0 baseline; record here.

> **PAUSE after Phase 2** — report measured savings per provider.

---

### Phase 3: Telemetry-gated caching follow-ups (only if Phase 1/2 data justifies)
- [ ] 🟥 **Step 3.1: `cache_template` decision** — using real numbers, either wire the template summary into the cacheable system prefix (saves a `read_template` turn) **or** delete the dead parameter + stale comments. One or the other; no half state.
  - **Verify:** if wired — turn count per face agent drops by 1 and the template block shows as cached; if deleted — `grep cache_template` returns only removal.
- [ ] 🟥 **Step 3.2: Sheet-12 inventory → prompt tail + warm-up** — move the per-batch `=== INVENTORY ===` (`notes/agent.py:561`) to the end, and add a deliberate warm-up (run one sub-agent to first-token before fanning out) since the 0.6s concurrent stagger (`notes/listofnotes_subcoordinator.py:82`) defeats a cold cache.
  - **Verify:** Sheet-12 burst shows cross-sub-agent cache reads in Telemetry (proves the warm-up works); coverage unchanged vs. baseline.
- [ ] 🟥 **Step 3.3: Non-determinism audit** — sweep prompt assembly for unsorted `dict`/`set` iteration or run-varying data in the static region that silently breaks cross-run cache keys.
  - **Verify:** two runs on the same PDF produce byte-identical static system-prefix bytes (hash compare).
- [ ] 🟥 **Phase 3 Verify:** each sub-step either landed with a measured win or was explicitly dropped with the number that justified dropping it.

---

### Phase 4: Effectiveness — reviewer cascade-trace pre-injection (highest ROI, lowest risk) ✅
- [x] 🟩 **Step 4.1: Pre-inject the trace into the review packet** — DONE. New `_trace_for_check` pre-computes `trace_cascade_source` for each failing check's `target_sheet`/`target_row` (and comparand coords, scope-aware, CY, deduped/capped); `render_reviewer_prompt` threads the rendered traces into `_format_review_packet`, which inlines them indented under each check.
  - [x] 🟩 `prompts/reviewer.md`: instructs tool-call batching ("budget counts round-trips") + tells the reviewer the named target's trace is already inlined (don't re-call the tool for it).
  - [x] 🟩 Tests: `test_packet_renders_precomputed_trace_under_check` (pure) + `test_prompt_inlines_cascade_trace_for_failing_target` (integration on the seeded fixture). Existing reviewer tests unaffected (substring assertions).
  - **Verify (USER, live):** on a known-failing run, the reviewer reaches its first `apply_fix` in fewer turns than baseline (compare `run_agent_turns`); fixes-per-budget improves.
  - **Mocked:** ✅ reviewer suite 58 passed; routes + lifecycle + e2e 43 passed.

---

### Phase 5: Effectiveness — notes prompt contradictions (§9 #1) ✅
- [x] 🟩 **Step 5.1: Reconcile "ONE NOTE, ONE CELL" vs multi-row** — DONE. `prompts/_notes_base.md` headline reworded from "exactly one CELL across the workbook" → **"exactly one SHEET"** (no cross-sheet duplication), explicitly blessing intra-sheet multi-row splits + sub-note grouping. Section renamed `INVARIANT: NO CROSS-SHEET DUPLICATION`.
- [x] 🟩 **Step 5.2: Reconcile "skip" vs "catch-all"** — DONE. Base "skip" disposition now carves out sheet-defined catch-all sinks. `prompts/notes_listofnotes.md` skip taxonomy tightened: a skip is valid **only** when the note belongs on another sheet (acc-policies / corporate-info / related-party); "no specific row fits" → catch-all, never a silent drop. Removed the contradictory "isn't important enough for the catch-all" escape and the misleading "handled by Notes-13" worked-example skip (now a catch-all `written` example).
- [x] 🟩 **Pinning test updated:** `test_notes_phase6_prompts.py::test_notes_base_prompt_contains_non_duplication_rule` rewritten to assert the corrected SHEET-level invariant (it had pinned the old contradictory "one cell" wording).
  - **Mocked:** ✅ notes prompt + e2e suites green (phase6, notes12_e2e, prompt_phase1, label_catalog, html_contract, no_mfrs_leak, filing_standard, mpers_notes — 86+ passed).
  - **Verify (USER, live):** a notes run on the sample PDF shows no cross-sheet duplicate; a real-but-unmatched note lands on the catch-all instead of being dropped.
- **Step 5.3 (sign-convention single source) MOVED to Phase 6** — it's face-statement sign conventions (`_sign_conventions.py` + SOCIE/SOCF prompts, ADR-002), the same subsystem as the SOCIE work; grouping it there keeps each commit coherent (notes vs face).

---

### Phase 6: Effectiveness — de-hardcode SOCIE rows (§9 #2) + sign-convention single source (§9 #1, moved from Phase 5)
- [ ] 🟥 **Step 6.0: Sign-convention single source** — state each dividend/OCI sign rule once; mark the dynamically-injected `_sign_conventions.py` block authoritative over the static prose; stop feeding SOCIE a SOCF-worded block. Respect ADR-002 + the dividend-sign pins.
- [ ] 🟥 **Step 6.1: Read movement rows from the template** — change `prompts/socie.md` to have the agent confirm movement-row numbers (profit, dividends, equity-at-end, share issue, OCI) from `read_template()` instead of trusting literal rows 6–25/30–49, which conflict with the group overlay (rows 3–25…).
  - [ ] 🟥 Add one worked `write_facts` example per movement type (not one bare example for a 24-column matrix)
  - [ ] 🟥 Re-check `tests/test_filing_level.py` (Company vs Group SOCIE routing) still passes
  - **Verify:** SOCIE extraction on the sample PDF lands values on the correct movement rows for **both** Company and Group filings (open the filled workbook; spot-check profit/dividend rows).

---

### Phase 7: Effectiveness — directional verifier feedback (§9 #3)
- [ ] 🟥 **Step 7.1: Add directional diagnosis without weakening no-plug** — extend SOPL/SOCI/SOCF feedback in `tools/verifier.py` to use the `computed_totals` already in hand ("gap matches row X — likely a sign error"), mirroring the SOFP `_sofp_imbalance_feedback`. Keep the `_NO_PLUG_FOOTER`.
- [ ] 🟥 **Step 7.2: Warn that verify is vacuous for non-SOFP** — in the relevant prompts, tell the agent value-accuracy is its own responsibility for SOPL/SOCI/SOCF/SOCIE; drop the impossible cross-statement cross-checks from face prompts (`soci.md:38`, `socf.md:48`).
  - [ ] 🟥 Update `tests/test_verifier_feedback_wording.py` (pins the wording; gotcha #17)
  - **Verify:** force a sign-error case; the feedback now names the suspect row/side; `pytest tests/test_verifier_feedback_wording.py` green; no-plug rule still present.

---

### Phase 8: Effectiveness — scout hint confidence-gating (§9 #5)
- [ ] 🟥 **Step 8.1: Gate scanned-PDF `face_line_refs`** — in `scout/agent.py`, instruct the scout to emit refs only at high confidence and null the `note_num` when the reference column is illegible (mirror the existing "do NOT guess" face-page rule).
- [ ] 🟥 **Step 8.2: `save_infopack` survival counts** — return surviving counts ("3 statements, 14 notes, 22 refs; 2 skipped — re-check") instead of a bare "saved successfully", so the agent can self-correct in-run.
  - [ ] 🟥 Update `tests/test_scout_*` assertions if the success string / ref schema is pinned
  - **Verify:** a scanned/low-text PDF run — scout no longer emits confident wrong note numbers; the tool result reports counts; relevant scout tests green.

---

### Phase 9: Effectiveness — provider-aware temperature (§9 #7)
- [ ] 🟥 **Step 9.1: Lower temperature off Gemini** — make temperature provider-aware (Gemini stays 1.0 per CLAUDE.md:140; OpenAI/Anthropic drop to ~0–0.2), validating per model since some GPT-5 reasoning models reject non-default temperature.
  - [ ] 🟥 Confirm no test pins `temperature=1.0` for non-Gemini; update if so
  - **Verify:** a Claude/OpenAI run still completes (no API rejection); spot-check that numeric extraction variance is not worse than baseline on a repeat run.

---

### Phase 10: Effectiveness — rounding tolerance (§9 #8)
- [ ] 🟥 **Step 10.1: Scale the SOFP balance tolerance** — change the absolute `abs(diff) > 0.01` check (`tools/verifier.py:482`) to scale with the statement's unit/magnitude so a legitimate ±RM1 rounding on an RM'000 statement doesn't manufacture an unresolvable imbalance.
  - [ ] 🟥 Update `tests/test_cross_checks.py` / verifier balance tests for the new tolerance
  - **Verify:** an RM'000 statement with a genuine ±1 source rounding no longer trips the imbalance → acknowledge loop; a real >tolerance imbalance still fails.

---

## Cross-cutting verification (run after each behavior-changing phase)
- [ ] 🟥 `python -m pytest tests/ -v` (backend; excludes live)
- [ ] 🟥 `cd web && npx vitest run` (only if a frontend file changed — Phase 1.2)
- [ ] 🟥 One real run on `data/FINCO-Audited-Financial-Statement-2021.pdf`, filled workbook opened in Excel so formulas evaluate (gotcha #4)

## Rollback Plan
If something goes badly wrong:
- **Per-phase git revert** — each phase is its own commit (on a feature branch, not `main`); revert the offending commit. Prompts and `litellm_config.yaml` are text — trivially revertible.
- **Templates are untouched** by this plan — if a workbook looks wrong, suspect the prompt/verifier change, not the template (do not hand-edit templates, gotcha #3).
- **Reviewer facts** — Phase 4 doesn't write facts itself, but if a reviewer change misbehaves, "Revert to original" restores the pre-reviewer extraction from `run_fact_snapshots` (gotcha #21).
- **Schema migration (Step 1.2)** — additive nullable columns only; a revert leaves them unused, not broken. Do not drop columns on rollback.
- **State to check after any revert:** `git status` clean, `pytest tests/` green, one sample run produces a valid `filled.xlsx`.

## Notes / deviations log
- **2026-06-02 — Phase 0:** the review's "pydantic-ai is 0.8.1" was a red herring (system python). The real runtime (`venv`) is **1.77.0** with first-class caching APIs, so Phase 2 needs no `extra_body` hacks: `AnthropicModelSettings.anthropic_cache_instructions` (direct) and `OpenAIChatModelSettings.openai_prompt_cache_key` / `openai_prompt_cache_retention` (OpenAI). `CURRENT_SCHEMA_VERSION` had already drifted to **14** (CLAUDE.md says 13) — v15 builds on 14.
- **2026-06-02 — Phase 1:** landed cache telemetry end-to-end (capture → schema v15 → repo → API payload → frontend). All tests I can run pass: backend 1881 passed / 2 skipped, frontend 632 passed, tsc clean. Live end-to-end verification (does a real run report non-zero cache reads) is handed to the user — it's the gate for Phase 3.
- **2026-06-02 — Phase 5:** fixed the notes prompt's flat contradictions (§9 #1). Base invariant reworded "one cell" → "one sheet" (the multi-row case is now explicitly legitimate); Sheet-12 skip taxonomy tightened so unmatched-but-real notes hit the catch-all instead of being dropped, and the misleading "handled by Notes-13" skip example was removed. Updated the one pinning test that encoded the old contradictory wording. Deferred Step 5.3 (sign conventions) to Phase 6 — same subsystem as SOCIE. Notes prompt + e2e suites green.
- **2026-06-02 — Phase 4:** pre-inject cascade traces into the reviewer packet (`_trace_for_check` + `render_reviewer_prompt` wiring), plus reviewer.md batching guidance. Highest-ROI reviewer change: the children-feeding-a-total data is computed server-side and was being rediscovered by the agent at 2-3 tool round-trips per check, against a tight [12,36] budget. Reviewer suite 58 + routes/lifecycle/e2e 43 green. Live before/after turn-count comparison handed to the user. (Did Phase 4 before Phase 3 because Phase 3 is gated on the live telemetry; Phase 4 is independent and mock-testable.)
- **2026-06-02 — Phase 2:** added `model_settings.py::build_model_settings` (provider-aware, dispatch by model type) and wired it into all 6 multi-turn agents. Direct-Anthropic caches instructions+tools; OpenAI (direct + proxy) sets cache_key+24h retention. 1.77's first-class settings meant **no `extra_body`/`CachePoint` hacks** — simpler than the report assumed. Removed the now-dead `ModelSettings` imports in the 6 files. Deferred: proxy-routed-Anthropic caching (Step 2.2 known gap) and the temperature seam (Phase 9 — helper already accepts a `temperature` override). New `tests/test_model_settings.py` (6) green.
