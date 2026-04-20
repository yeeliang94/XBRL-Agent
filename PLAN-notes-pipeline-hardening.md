# Implementation Plan: Notes Pipeline Hardening (PR A + PR B)

**Overall Progress:** `35%` (PR A complete locally, 6/6 correctness fixes landed with regression tests; awaiting push/open-PR approval)
**Context doc:** peer-review findings (2026-04-20). See the review message in the `vision-fallback` thread for source quotes. Items in this plan are the **pre-existing** findings from A.1 / A.2 / A.3 prior commits — none were introduced by the vision-fallback PR.
**Last Updated:** 2026-04-20

## Summary

Two small, focused PRs to clean up the notes pipeline:

- **PR A — Correctness** (~1 day): six real bugs / silent-failure modes in `notes/writer.py`, `notes/listofnotes_subcoordinator.py`, and `server.py`. Each fix lands with a regression test.
- **PR B — Cleanup** (~half day): mechanical refactors — constant deduplication, circular-import relocation, helper extraction, SSE prose trim. Zero behavioural change.

Security gaps (path traversal, no auth, etc.) are intentionally **not** in scope — they're a locally-bound dev tool today, and the right time to fix them is when the deployment model changes. Instead this plan **adds a `Known Security Gaps` section** to `CLAUDE.md` + `AGENTS.md` so the constraint is visible to every future contributor.

## Key Decisions

- **Two PRs, not one.** Correctness and cleanup land separately so each review is self-contained and the cleanup refactor doesn't obscure the actual bug fixes.
- **PR B blocked on PR A.** PR B's helper extractions touch the same `notes/coordinator.py` / `server.py` lines PR A edits — sequencing prevents merge-conflict noise.
- **Security documented, not fixed.** Every security item in the peer review assumes an internet-hosted surface. This is a localhost-only tool (`./start.sh` → `localhost:8002`). Documenting the known gaps in `CLAUDE.md` / `AGENTS.md` is more useful today than silent hardening — the day someone thinks about hosting this, the checklist is already there.
- **No behaviour change in PR B.** Every step in Phase 3 is mechanical. No logic moves, no renames that aren't pure re-exports, no test assertions change. If a test fails in PR B, it's a bug in the refactor, not the design.
- **Retry-count accounting uses `attempt`.** Sub-agent's `retry_count` field will count retries performed (0 = first try succeeded), matching the single-agent coordinator's convention.
- **Row-112 ordering key is `min(source_pages)`.** The first PDF page a sub-agent cited for a row is a stable ordering signal that survives re-runs.

## Pre-Implementation Checklist
- [x] 🟩 `main` branch clean and 609 backend + 346 frontend tests green on the current working tree (done 2026-04-20 after committing in-flight vision-fallback work as one prep commit)
- [ ] 🟥 Live test (`pytest -m live`) passes against FINCO fixture before starting (skipped — costs real API spend, not auto-run)
- [x] 🟩 Open one short-lived branch per PR (`hardening/pr-a-correctness` created; PR B branch to follow)

---

## PR A — Correctness fixes

Six bugs, each in its own commit on the `hardening/pr-a-correctness` branch so a reviewer can see the fix and its test side-by-side. Ordering is by file (single-file edits are easier to bisect).

### Phase 1: `notes/writer.py` — three writer correctness fixes

- [x] 🟩 **Step A1: Empty-payload no-op success flag** — `notes/writer.py:131-133`. Today `success = rows_written > 0 or not payloads` returns `True` on an untouched template; combined with Sheet-12's "no payloads = all sub-agents lost coverage", a silent green tick can ship.
  - [ ] 🟥 Change to `success = rows_written > 0`
  - [ ] 🟥 Update the docstring to note "callers must pre-check for empty payloads and skip the write if they want a no-op success"
  - [ ] 🟥 Audit call sites: `notes/coordinator.py:_write_template_workbook`, `notes/listofnotes_subcoordinator.py`. Confirm each one already has a short-circuit for `len(payloads) == 0`; add one if missing.
  - **Verify:** new `tests/test_notes_writer.py::test_empty_payloads_returns_failure` — `write_notes_workbook(template=X, payloads=[])` returns `success=False`, `rows_written=0`. Existing tests still pass.

- [x] 🟩 **Step A2: Evidence ghost-row fix** — `notes/writer.py:310-332`. If every value column is empty but `payload.evidence` is non-empty, the evidence cell still gets written → a row with citation text but no values.
  - [ ] 🟥 Gate the evidence-write block on `wrote_anything or payload.numeric_values`
  - [ ] 🟥 Add a comment explaining why a value-less row must not carry evidence
  - **Verify:** new `tests/test_notes_writer.py::test_evidence_not_written_without_values` — payload with `content=""`, `numeric_values=None`, `evidence="foo"` → evidence cell stays unchanged in the written workbook.

- [x] 🟩 **Step A3: Row-112 deterministic ordering** — `notes/writer.py:_combine_payloads` at :203-255. Concatenation iterates `payloads` in input order, which is batch-completion order from `asyncio.wait(ALL_COMPLETED)` — non-deterministic across runs.
  - [ ] 🟥 Sort `payloads` by `min(p.source_pages)` (fallback to 0 if empty) at the start of `_combine_payloads`
  - [ ] 🟥 Do the same to the `all_pages`, `evidence_parts`, and `sub_ids` accumulators if they aren't already ordered by that same key
  - [ ] 🟥 Update the "Prose: concatenate content with blank line separators" docstring to mention the PDF-page order
  - **Verify:** new `tests/test_notes_writer.py::test_combine_payloads_sorts_by_source_page` — feed two payloads in reverse page order, assert concatenated `content` has the earlier-page payload first. Existing `test_notes_writer.py` tests continue to pass.

### Phase 2: `notes/listofnotes_subcoordinator.py` — two sub-coordinator fixes

- [x] 🟩 **Step A4: Unify `retry_count` accounting** (test landed in `tests/test_notes12_subcoordinator.py` instead of `tests/test_notes_retry_budget.py` — the latter covers the single-agent path which has no `retry_count` field) — `:320-344`. Success path uses `retry_count=attempt`; failure path flips between `attempt+1` and `attempt`. Docstring at `:97-98` doesn't match either.
  - [ ] 🟥 Unify on `retry_count = attempt` in both the success and failure paths (number of retries performed; 0 = first-try success)
  - [ ] 🟥 Rewrite docstring at `:97-98` to describe this semantics in one sentence
  - [ ] 🟥 Delete the dead `retry_count = attempt + 1 if attempt < max_retries else attempt` branch
  - **Verify:** extend `tests/test_notes_retry_budget.py` with a case that confirms `retry_count` equals the number of retries performed (0 on first-try success, 1 on retried success, 1 on retried failure). Sweep existing assertions for the old convention.

- [x] 🟩 **Step A5: Sub-agent task-registry leak** (used existing `task_registry.unregister` instead of adding a new `remove` method — same semantics, no new API surface) — `:200-217`. `task_registry.register(session_id, sub_id, task)` is never paired with a `remove` call. On abort the outer `task_registry.remove_session(session_id)` catches most of it, but between abort and cleanup the refs linger, and standalone-cancellation paths leak.
  - [ ] 🟥 Wrap each `register` call in a `try/finally` that calls `task_registry.remove(session_id, sub_id)` once the sub-agent completes (success, failure, OR cancellation)
  - [ ] 🟥 If no `remove` method exists on `task_registry`, add one (lookup by `(session_id, task_id)`); verify with the existing `task_registry` unit tests
  - **Verify:** new `tests/test_notes12_subcoordinator.py::test_task_registry_cleared_on_completion` — register 5 sub-agents, run to completion, assert `task_registry._sessions[session_id]` is empty afterward. Mirror test for cancellation mid-run.

### Phase 3: `server.py` — one merge-scan fix

- [x] 🟩 **Step A6: Don't merge stale notes workbooks** (used preferred option — iterate `notes_result.workbook_paths` only) — `server.py:1119-1126`. The final merge scans the session directory for `NOTES_*.xlsx`. Re-running the same session (same UUID isn't possible via the UI, but CLI `--output-dir` can reuse) picks up prior-run artefacts.
  - [ ] 🟥 Replace the `for nt in NotesTemplateType: if wb_path.exists(): …` loop with one of:
    - (preferred) iterate `notes_result.workbook_paths` only — the coordinator has already tracked what this run wrote
    - (fallback) gate `wb_path.exists()` on `wb_path.stat().st_mtime > run_started_at`
  - [ ] 🟥 Add a comment explaining which was chosen and why
  - **Verify:** new `tests/test_server_run_lifecycle.py::test_merge_ignores_stale_notes_files` — pre-populate `session_dir/NOTES_CORP_INFO_filled.xlsx` with `mtime` 10 s before `started_at`, run a notes-free extraction, assert the merged workbook does NOT contain a CorpInfo sheet.

### Phase 4: PR A wrap-up

- [x] 🟩 **Step A7: Full regression** — `pytest tests/ -q` → **616 passed** (baseline 609 + **7** new tests; +1 over plan's +6 because A.5 naturally split into completion + cancellation coverage per the plan's own "Mirror test for cancellation mid-run" instruction). Frontend 346 passed, untouched. Live test skipped — not auto-run.
- [ ] 🟥 **Step A8: Open PR A** — branch `hardening/pr-a-correctness` exists locally with 6 commits; awaiting push/gh-pr-create approval.

---

## PR B — Cleanup refactors

Pure refactor — zero behavioural change, zero test edits except import adjustments. Depends on PR A merged.

### Phase 5: Constants / imports

- [ ] 🟥 **Step B1: Dedupe `_BORDERLINE_FUZZY` / `_BORDERLINE_FUZZY_SCORE`** — `notes/coordinator.py:789` duplicates `notes/writer.py:60` with a "known duplicate" comment. Constant drift risk.
  - [ ] 🟥 Export `BORDERLINE_FUZZY_SCORE` as a public constant from `notes/writer.py` (one name, drop the underscore on one of them)
  - [ ] 🟥 Import in `notes/coordinator.py`; delete the local copy + "known duplicate" comment
  - **Verify:** `pytest tests/test_notes_writer.py tests/test_notes_coordinator.py -q` green; grep for the old private names returns 0 matches.

- [ ] 🟥 **Step B2: Break `NOTES_PHASE_MAP` circular import** — `notes/listofnotes_subcoordinator.py:58` imports `NOTES_PHASE_MAP` from `notes.coordinator`, but `notes.coordinator.py:534` (inside a function) imports from `listofnotes_subcoordinator`. Today's workaround: function-scoped import. Cleaner: move the constant.
  - [ ] 🟥 Create `notes/constants.py` holding `NOTES_PHASE_MAP`
  - [ ] 🟥 Update both importers to read from there
  - [ ] 🟥 Drop the function-scoped import in `notes/coordinator.py`
  - **Verify:** `python3 -c "import notes.coordinator, notes.listofnotes_subcoordinator; print('ok')"` prints `ok`. `pytest tests/test_notes_coordinator.py tests/test_notes12_subcoordinator.py -q` green.

### Phase 6: Readability

- [ ] 🟥 **Step B3: Type `Infopack.notes_page_hints` properly** — `notes/coordinator.py:121-145` uses a three-layer `getattr` / `callable` / `try/except` defence for what is a typed method on `Infopack`.
  - [ ] 🟥 Confirm `Infopack.notes_page_hints()` is defined on all code paths (check `scout/infopack.py`)
  - [ ] 🟥 Replace the defensive block with a direct call
  - [ ] 🟥 Update the type hint on the calling function to reflect the non-optional return
  - **Verify:** `pytest tests/test_notes_coordinator.py -q` green. Static check: `python3 -c "from scout.infopack import Infopack; Infopack(toc_page=1, page_offset=0).notes_page_hints()"` returns `[]` without error.

- [ ] 🟥 **Step B4: Trim SSE error prose** — `notes/coordinator.py:578-595` emits a ~70-word error prose block straight into the SSE `run_complete` payload. UI renders it verbatim → wall of text in the toast.
  - [ ] 🟥 Keep UI-facing message to one sentence (≤ 120 chars); land the full diagnostic via `logger.error(...)` with the run's session_id
  - [ ] 🟥 Verify frontend rendering (`SuccessToast.tsx`, `RunDetailView.tsx`) wraps gracefully regardless — no change expected
  - **Verify:** read the SSE payload in a browser devtools trace on a forced-failure run; error field ≤ 120 chars. `grep -r "notes coordinator failed" logs/` shows the long form.

- [ ] 🟥 **Step B5: `_fail_run` helper in `server.py`** — the notes fail path in `run_multi_agent_stream` repeats the `error + run_complete(success=False) + mark_run_finished('failed') + return` quartet in three places (:739-755, :1057, and one more).
  - [ ] 🟥 Extract a `_fail_run(session_id, error_msg, …)` helper at module level
  - [ ] 🟥 Hoist the late `from notes.coordinator import NotesAgentResult` at `:1057` to a top-level lazy block
  - [ ] 🟥 Call sites collapse to one line each
  - **Verify:** existing `tests/test_server_run_lifecycle.py` green (this test covers the terminal-status invariant on every fail path). Optional: add a parametrised test that drives each fail-path through `_fail_run` and asserts the same terminal state.

### Phase 7: Test coverage gaps (identified by reviewer; ship in PR B to close the loop)

- [ ] 🟥 **Step B6: Notes-coordinator-crash E2E** — peer-review: no test covers `server.py:1071-1080`, the "notes coordinator raises → synthesized failed result" path.
  - [ ] 🟥 Add `tests/test_server_run_lifecycle.py::test_notes_coordinator_crash_synthesizes_failed_result` — patch `run_notes_extraction` to raise; assert `run_complete.success is False` AND every requested template in `run_complete.notes_failed`.
  - **Verify:** test passes; same test with the synthesis code deleted fails.

- [ ] 🟥 **Step B7: Sheet-12 fan-out cancel-after-raise** — mirror of `tests/test_notes_retry_budget.py:258-287` but for Sheet-12 sub-coordinator's `_safe_emit`.
  - [ ] 🟥 Add `tests/test_notes12_subcoordinator.py::test_safe_emit_swallows_queue_closed_on_cancel` — cancel mid-run after queue close; assert no exception bubbles past the sub-coordinator boundary.
  - **Verify:** test passes; reverting the `_safe_emit` safety catch fails it.

- [ ] 🟥 **Step B8: Verifier SOCF double-sign edge patterns** — peer-review suggestion.
  - [ ] 🟥 Add `tests/test_verifier_formula.py::test_resolves_double_prefix_coefficients` with inputs `=++1*B7` and `=--1*B7` → resolve to `+B7` and `+B7` respectively.
  - **Verify:** both cases green; removing the sign-normalising branch in `tools/verifier.py:279-296` fails them.

### Phase 8: PR B wrap-up

- [ ] 🟥 **Step B9: Full regression** — `pytest tests/ -q` green; `pytest -m live` green; frontend unchanged. Diff review: confirm no behavioural code is altered (only moves + helper extractions).
- [ ] 🟥 **Step B10: Open PR B** — branch `hardening/pr-b-cleanup`; title "notes pipeline: cleanup refactor (PR B)"; body lists the eight steps.

---

## PR C (doc-only) — Security gap disclosure

This is a doc-only change with a distinct blast radius (no code touched), shipping on its own branch so it can merge before PR A if desired.

### Phase 9: Document known security gaps

- [ ] 🟥 **Step C1: New `Known Security Gaps` section in `CLAUDE.md`** — positioned after the "Known Issues & Gotchas" list (after gotcha #14). Title: **`### 15. Known Security Gaps (local-dev tool only)`**. Body enumerates:
  1. **Path traversal on session-id path params** (`/api/scout`, `/api/run`, `/api/rerun`, `/api/download/{session_id}`) — only `/api/result/{session_id}/{filename}` validates. Accept: arbitrary strings. Risk on localhost: low; on a hosted surface: high. Fix: shared `_validate_session_id()` helper (reject `..`, `/`, `\\`; prefer UUID4 regex), call at every endpoint.
  2. **No auth on `/api/settings`** — writes `GOOGLE_API_KEY` into `.env` on any localhost request. Fix: shared-secret header on `settings`, `run`, `abort`, `delete`.
  3. **No CORS config** — implicit allow-all. Fix: explicit `CORSMiddleware` with an allowed-origins list.
  4. **`download_filled_endpoint` trusts DB-stored path** — reads `runs.merged_workbook_path` without re-validating containment under `OUTPUT_DIR`. Fix: `file_path.resolve().relative_to(OUTPUT_DIR.resolve())` check before serving.
  5. **`float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))` unhandled** — malformed env var crashes request handling. Fix: use the `_safe_float_env` helper already at `server.py:345-347`.
  - Close with one paragraph: **"These gaps assume an internet-facing deployment. The current app is bound to `localhost:8002` via `./start.sh` and is not intended for hosting. The day the deployment model changes, every item in this list becomes a release blocker."**
  - **Verify:** `grep "Known Security Gaps" CLAUDE.md` returns one match; section renders cleanly in GitHub markdown preview.

- [ ] 🟥 **Step C2: Mirror in `AGENTS.md`** — one-paragraph summary plus a link back to the `CLAUDE.md` section. Don't duplicate the full detail; the goal is that an agent reading `AGENTS.md` hits the constraint early and follows the link for specifics. Position near the top of the "Known Issues" equivalent in `AGENTS.md`.
  - **Verify:** `grep "Known Security Gaps" AGENTS.md` returns one match; link resolves.

- [ ] 🟥 **Step C3: Open PR C** — branch `hardening/pr-c-security-disclosure`; title "docs: record known security gaps (localhost-only constraint)"; one-line body.

---

## Rollback Plan

Each PR is independently revertable:

- **PR A rollback:** `git revert <commit>` removes the six fixes and their tests. Behaviour returns to today's silent-success / non-deterministic / leak state. No data migration needed.
- **PR B rollback:** pure refactor — revert restores the duplicated constants, circular-import workaround, reflective defence, verbose SSE prose. Test-coverage additions revert too but don't affect production behaviour.
- **PR C rollback:** doc revert only. No runtime impact.

**Data to check on rollback:**
- For PR A: any runs completed under the fixes may have different `run_agents` status rows (notably rows that were previously "success" for empty-payload templates will be "failed"). Decide whether to backfill or leave as-is.
- For PR B: none.
- For PR C: none.

**Watch window:**
- After PR A deploy: first 10 notes-enabled runs — inspect `run_agents.status` for empty-payload templates, confirm they flip from `succeeded` to `failed` as intended. Spot-check one row-112 concat to confirm ordering is now deterministic across runs.

## Rules

- PR A and PR B are separate PRs with separate branches and separate reviews.
- PR B does not merge until PR A is merged (shared-file conflict avoidance).
- Every correctness fix in PR A has a regression test that fails without the fix.
- PR B is a pure refactor — no behavioural changes, no renames that aren't one-for-one re-exports.
- Security items are **documented only** in this plan cycle. Do not attempt to fix them here; it's a deployment-model decision, not a code decision.
- No scope creep. Findings from the peer review that aren't listed above (e.g. the perf finding on `event_queue` backpressure, the `save_result` untrusted-JSON note) are explicitly parked as future work.
