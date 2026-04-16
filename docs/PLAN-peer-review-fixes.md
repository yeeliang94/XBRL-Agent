# Implementation Plan: Peer-Review Backend Fixes

**Overall Progress:** `100%` — all three phases complete, 0 regressions, +34 new tests
**PRD Reference:** Peer review session on 2026-04-16 (findings C1–C7, I1–I15). Invalid finding I10 rejected (SOCI-BeforeOfTax is the real sheet name, not a typo — confirmed by `tests/test_cross_checks_impl.py:495`). Findings I8 and I11 reclassified as design questions, not defects.
**Last Updated:** 2026-04-16
**Methodology:** Red–Green TDD per fix where a unit test is tractable. Every step has explicit Verify criteria. Land in order: security → data integrity → direct-mode routing → ops/hardening. One fix per commit so rollback is surgical.

## Summary

Address confirmed findings from the peer backend code review. Tier 1 (security + silent data corruption) lands first; Tier 2 (correctness, ops) second; Tier 3 (hardening, cleanups) last. Invalid / design-choice findings are not touched.

## Key Decisions

- **Skip I10** — "SOCI-BeforeOfTax" is the authentic sheet name inside `05-SOCI-BeforeTax.xlsx`. Removing it would break all SOCI cross-checks. *Why:* verified against `tests/test_cross_checks_impl.py:495` which comments on exactly this.
- **I8 (delete_run leaves disk)** — deferred as product decision, not a defect. Current behaviour is explicitly documented in the endpoint docstring (`server.py:1437-1440`). Needs a stakeholder call on retention policy.
- **I11 (tolerance split)** — keep two regimes. Verifier's 0.01 is formula precision within a single template; cross-checks' 1.0 is real-world financial noise across statements. Unification is optional cleanup, not correctness.
- **C6 — atomic merge+finish** — wrap the pair in one transaction to eliminate the partial-state window on clean exits. Hard-kill still produces `status='running'` and cannot be fully solved without startup recovery; we'll document that limit rather than ship startup recovery in this round.
- **C4 — prefix stripping** — add a single normalization function used by both `_detect_provider` and the direct-mode model constructors. All three prefix forms (`openai.`, `bedrock.anthropic.`, `vertex_ai.`) handled uniformly.
- **Thinking-token pricing (C5)** — bill at `output_price_per_mtok` by default. Optional override `thinking_price_per_mtok` per model entry if providers diverge later.
- **Tests land with fixes** — every Tier 1 fix ships with at least one regression test that would have caught the original bug.

## Pre-Implementation Checklist

- [x] 🟩 Snapshot current test status — baseline: 381 passed, 10 pre-existing failures in `test_section_headers.py` (template-header discovery, unrelated to this plan), 11 skipped
- [x] 🟩 Confirm no in-flight branch touches `server.py`, `cross_checks/`, `tools/verifier.py`, `pricing.py`, `db/` — clean
- [x] 🟩 Pin peer-review findings list to this plan for traceability

---

## Tasks

### Phase 1 — Tier 1: Security + Silent Data Corruption

Top priority. These ship before anything else. Independent fixes, but landed in order of blast radius.

- [x] 🟩 **Step 1.1: C1 — Path traversal in `/api/result/{session_id}/{filename}`**
  - [x] 🟩 Added tests in `tests/test_download_api.py` (existing file; two new tests covering filename-level dotdot and direct handler call with `/` + `\\` + absolute-ish paths)
  - [x] 🟩 Reject `..`, `/`, `\` tokens in `filename` at the top of the handler (`server.py:1535-1538`)
  - [x] 🟩 Added `resolve().relative_to()` guard as belt-and-braces (same pattern as `mount_spa`)
  - [x] 🟩 Returns 400 on `ValueError`/`OSError` from resolve/relative_to
  - **Verify:** `pytest tests/test_download_api.py -v` → 5 passed. Existing `filled.xlsx` / `SOFP_filled.xlsx` still return 200. Note: TestClient URL-decodes `%2F` before route matching, which Starlette turns into a 404 route-mismatch — that's naturally safe, so the handler-level tests use direct `download_result(...)` calls to simulate the permissive-proxy edge case.

- [x] 🟩 **Step 1.2: I1 — API-key leakage in `/api/test-connection`**
  - [x] 🟩 Added `tests/test_connection_endpoint.py` with a fake-bearer-token stub
  - [x] 🟩 Replaced `str(e)` in `server.py:1196` with a generic message
  - [x] 🟩 `logger.exception` preserved for server-side diagnostics
  - **Verify:** `pytest tests/test_connection_endpoint.py` → 1 passed; full trace visible in server logs under `ERROR server:server.py:1195 Connection test failed`.

- [x] 🟩 **Step 1.3: C2 — Group cross-checks silent-pass when Company totals missing**
  - [x] 🟩 Added `TestGroupMissingCompanyTotalsFail` class in `tests/test_group_checks.py` with 5 fixture tests (one per check)
  - [x] 🟩 Applied fix across all 5 cross-check files — `sofp_balance`, `sopl_to_socie_profit`, `soci_to_socie_tci`, `socie_to_sofp_equity`, `socf_to_sofp_cash`
  - [x] 🟩 Pattern: when `filing_level == "group"` and either Company value is None, flip `co_passed = False` and append a descriptive "Company: missing …" message
  - **Verify:** `pytest tests/test_group_checks.py -v` → 19 passed (14 existing + 5 new). Messages like `"Company: missing totals (assets=None, equity+liab=None)"` surface cleanly in the History UI.

- [x] 🟩 **Step 1.4: C4 — Provider detection + direct-mode model construction**
  - [x] 🟩 Added `_strip_provider_prefix` helper + `_PROVIDER_PREFIXES` tuple (longest match first)
  - [x] 🟩 `tests/test_provider_routing.py` covers bare names, registry IDs, and PydanticAI namespaced forms
  - [x] 🟩 `_detect_provider` normalizes before matching
  - [x] 🟩 OpenAI, Anthropic, Google direct-mode branches all pass `bare_name` to their constructors
  - **Verify:** `pytest tests/test_provider_routing.py -v` → 13 passed, 1 skipped (Anthropic SDK not importable in local env — not a defect). Proxy-mode path still passes raw `model_name` unchanged.

- [x] 🟩 **Step 1.5: C3 — Verifier formula evaluator broken for multi-term cross-sheet formulas**
  - [x] 🟩 Added `tests/test_verifier_formula.py` with 7 cases covering multi-term cross-sheet, weighted sum, SUM, unsupported funcs (`IFERROR`), error tokens (`#REF!`), and mixed refs
  - [x] 🟩 Replaced regex evaluator in `tools/verifier.py:_evaluate_formula` with a Tokenizer-driven walker, plus `_parse_range_operand` and `_sum_range_operand` helpers
  - [x] 🟩 Unsupported functions (non-SUM), unsupported operators, and error tokens all emit warnings and return 0.0 — no silent guessing
  - **Verify:** `pytest tests/test_verifier_formula.py` → 7 passed. Full suite (minus unrelated pre-existing `test_section_headers` failures) → 409 passed, 11 skipped, 0 regressions.

**End-of-phase gate:** Full `pytest tests/ -v` green. Manually exercise upload → extract → download for both Company and Group filings via Web UI. If any of the 5 new test files fails a pre-existing suite, stop and investigate before advancing to Phase 2.

---

### Phase 2 — Tier 2: Correctness + Ops

Confirmed bugs that don't bleed data but cause real operational pain.

- [x] 🟩 **Step 2.1: I3 — Exception in SSE queue drain falls through to merge** — cancel coordinator + mark failed + return. Verified: `pytest tests/test_server_run_lifecycle.py tests/test_multi_agent_integration.py` → 16 passed.

- [x] 🟩 **Step 2.2: C5 — Thinking tokens billed at output rate** — updated `pricing.py:estimate_cost`, added regression test. Verified: `pytest tests/test_token_tracker.py` → 5 passed (Claude Sonnet 1M thinking tokens now costs $15, not $3).

- [x] 🟩 **Step 2.3: I5 — `db_session` missing `WAL` + `busy_timeout`** — both pragmas set; matches `recorder.start`. Verified: `pytest tests/test_db_repository.py tests/test_db_schema_v2.py` → 20 passed.

- [x] 🟩 **Step 2.4: I15 — Token accounting inconsistency** — `grand_total` now includes thinking tokens; `add_turn` populates `cumulative_tokens` from running totals. Updated existing `test_cumulative_tokens` to reflect new contract.

- [x] 🟩 **Step 2.5: I4 — `active_runs.add` outside generator** — moved inside `event_stream()` so add + discard are symmetric under all abort paths. Verified: 16 server-lifecycle/SSE tests pass.

- [x] 🟩 **Step 2.6: C6 — `mark_run_merged` atomicity** — removed intermediate `db_conn.commit()`; the merged-path write now flushes with the per-agent persistence commit, eliminating the "merged_path durable but status=running" window on clean exits. Added explicit comment noting hard-kill between next commit and `mark_run_finished` still needs startup recovery (non-goal). Verified: 38 lifecycle/history tests pass.

**End-of-phase gate:** Pytest green. One real-PDF extraction end-to-end (Company + Group) to sanity-check cost totals look reasonable and no new warnings spam the logs.

---

### Phase 3 — Tier 3: Hardening + Cleanups

Lower urgency. Can land as one PR or broken across small commits.

- [x] 🟩 **Step 3.1: I6 — Cap SSE event recording** — 10 000 event cap + 16 KB payload truncation with `_truncated` marker; new `tests/test_recorder_caps.py` (3 tests).
- [x] 🟩 **Step 3.2: C7 — Migration race (v1→v2)** — `BEGIN IMMEDIATE` + re-check inside tx + `PRAGMA busy_timeout=5000` + idempotent duplicate-column handling. New multiprocessing test `test_concurrent_migration_from_v1`.
- [x] 🟩 **Step 3.3: I13 — `/api/upload` streams to temp file** — 1 MB chunked read + running byte counter; existing `test_upload_rejects_oversized_file` passes with streaming.
- [x] 🟩 **Step 3.4: I7 — Batch events fetch** — single `WHERE run_agent_id IN (…)` query in `get_run_detail`; 52 history/repo tests pass.
- [x] 🟩 **Step 3.5: I9 — Escape LIKE wildcards** — `_escape_like` helper + `ESCAPE '\'` clauses in `list_runs` / `count_runs`; new `test_list_runs_filter_escapes_like_wildcards`.
- [x] 🟩 **Step 3.6: I2 — Pin temperature** — `ModelSettings(temperature=1.0)` on both extraction and scout agents, with CLAUDE.md gotcha #5 reference.
- [x] 🟩 **Step 3.7: I14 — Build scripts idempotency** — moved to `scripts/` via `git mv`; `Path(__file__).resolve().parent` anchoring; already-built guards on both scripts.

---

## Rollback Plan

Each tier lands as its own PR or discrete commit cluster. If something goes wrong:

**Phase 1 rollback (security fixes):**
- Revert the specific commit with `git revert <sha>` (no schema / data implications).
- Watch the History page and any in-progress runs — none of these fixes touch DB schema, so rollback is clean.

**Phase 2 rollback (ops + pricing):**
- `pricing.py` rollback: cost totals recorded on historic runs will understate Claude/GPT thinking costs. Already-saved `total_cost` values in the `run_agents` table are unaffected by the revert (they're frozen at write time). Safe.
- `db_session` rollback: drops WAL / busy_timeout — readers may again hit `database is locked` under heavy load. Acceptable temporary state.
- Atomic merge+finish (C6) rollback: returns to the current split-commit behaviour. Known-OK baseline.

**Phase 3 rollback:**
- Recorder cap rollback: DB size may grow on runaway runs; monitor `agent_events` row count manually.
- Migration concurrency rollback: harmless unless two servers are started simultaneously against a v1 DB (rare).
- Other Phase 3 items are pure refactors — `git revert` is clean.

**Data integrity checks after any rollback:**
- `SELECT COUNT(*) FROM runs WHERE status = 'running';` — should be 0 when no extraction is active
- `SELECT COUNT(*) FROM runs WHERE merged_workbook_path IS NOT NULL AND status != 'completed' AND status != 'completed_with_errors';` — should be 0 (C6 contract)
- Verify `schema_version` row has `version = 2`
- Exercise Web UI: upload → scout → extract → download for both Company and Group filings
