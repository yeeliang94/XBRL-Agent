# Implementation Plan: UX/QA Review Fixes (2026-07-09)

**Overall Progress:** `12%` — Phase 1 complete (stale-run reaper, force-abort, error banner).
**PRD Reference:** `docs/UX-QA-Review_VALIDATION_2026-07-09.md` (the validated findings) and
`docs/UX-QA-Product-Review_2026-07-09.md` (the original review + Section 12 backlog).
**Last Updated:** 2026-07-09

> **Note on filename:** the `/create-plan` template asks for `docs/PLAN.md`, but that path is
> the **active mTool plan (75%)**. This plan lives at `docs/PLAN-ux-qa-fixes.md` to avoid
> clobbering it.

## Summary
Resolve the issues confirmed by the code+data validation of the UX/QA review. We fix the
two dead-ends first (stuck "running" runs; "completed-with-errors" runs that offer a
download with no warning), then the trust-eroding honesty bugs (a single run finishing under
four different status labels; a cost meter that shows ~57% of the real bill), then a batch of
polish/consistency items. **Refuted findings are excluded**; **reframed findings are scoped to
the real, smaller defect underneath them.**

## Key Decisions
- **Build from the validation doc, not the raw review.** The review's four scariest items
  (notes checkboxes "disabled", no confirmations anywhere, settings ungated, History no
  pagination) were **refuted** in code and are **out of scope** here.
- **Notes are already selectable** — the fix is a contrast/affordance change + a post-scan
  nudge, not un-disabling a control. (Reframed backlog #24.)
- **Confirmations mostly already exist** — scope is *unifying* the confirm pattern and closing
  the few real gaps (reset-password, benchmark's raw `window.confirm`, self-targeted actions),
  not adding confirmations everywhere. (Reframed backlog #4.)
- **One status enum, one vocabulary.** The "Didn't finish" bug is a symptom of collapsing
  `completed_with_errors` into a binary `success`. We introduce a single status→display mapping
  and route every surface through it, rather than patching four call sites.
- **Stuck-run recovery = reaper + escape hatch.** A startup reaper (mirroring the existing
  `reconcile_stale_review_tasks`) plus a UI force-abort. The backend already has a TODO marking
  this gap (`server.py:4566-4567`).
- **Two DISPUTED items become investigations, not fixes** — the "10 statements" count and the
  "editable abstract row" couldn't be reproduced from code; we verify before touching anything.

## Pre-Implementation Checklist
- [ ] 🟥 Validation doc reviewed & this scoping (drop refuted, reframe others) approved
- [ ] 🟥 Confirm which phases are in scope now vs later (P0/P1 vs P2/P3 polish)
- [ ] 🟥 No conflicting in-progress work on `server.py` run-lifecycle or `RunDetailView.tsx`
- [ ] 🟥 Baseline green: `python -m pytest tests/ -n auto` and `cd web && npx vitest run`

---

## Tasks

### Phase 1 — Dead-ends (P0): no run should trap the user

- [x] 🟩 **Step 1: Startup stale-run reaper** — flip orphaned `running` rows to a terminal status on boot, so month-old wedged runs stop living forever. (Backlog #2a)
  - [ ] 🟥 Add `reconcile_stale_runs(db_path)` in `db/repository.py` mirroring `reconcile_stale_review_tasks`: any `running` run whose `started_at` is older than a threshold (e.g. 6h) → `aborted`, `ended_at` set. Honour the gotcha #10 terminal-status contract; no `CHECK` constraint issue (status is uncontrolled by design).
  - [ ] 🟥 Call it from `server.py` `_lifespan` alongside the other three reconcilers (`~server.py:2360-2390`).
  - [ ] 🟥 New pinning test `tests/test_stale_run_reaper.py`: seed a `running` row with an old `started_at`, run the reaper, assert it becomes `aborted`; assert a *fresh* `running` row is left untouched.
  - **Verify:** `pytest tests/test_stale_run_reaper.py -q` passes; then restart the server and confirm runs 135/138/140/142 (stuck since 2026-05-28) now show `aborted` in History.

- [x] 🟩 **Step 2: UI escape hatch for a stuck run** — let a user force-abort/delete a run that has clearly died. (Backlog #2b)
  - [ ] 🟥 Backend: relax `DELETE /api/runs/{id}` (`api/runs.py:454`) OR add `POST /api/runs/{id}/force-abort` that flips a `running` row to `aborted` without needing the in-memory session (the current abort only works on `active_runs`).
  - [ ] 🟥 Frontend `RunDetailView.tsx`: when `status==='running'` **and** no live stream is attached, show an "Abort run" control (replacing the disabled Delete) wired to the new endpoint.
  - [ ] 🟥 Test: `tests/test_server_run_lifecycle.py` — force-abort on a `running` row lands it `aborted`; web test asserts the Abort control renders for a stream-less running run.
  - **Verify:** open a stuck running run in the UI → click Abort → row becomes `aborted`, Delete becomes enabled.

- [x] 🟩 **Step 3: "Completed with errors" warning banner + de-emphasised Download** — never invite a download of failed-check data with no explanation. (Backlog #1)
  - [ ] 🟥 `RunDetailView.tsx` header (`~:527-603`): when `status ∈ {completed_with_errors, correction_exhausted}`, render a top banner naming the failing check(s) + a link that switches to the Cross-checks tab (reuse the existing tab-switch mechanism).
  - [ ] 🟥 Demote "Download filled Excel" from `btnPrimary` to secondary until the banner is acknowledged (a dismiss/ack click), keeping it reachable.
  - [ ] 🟥 Web test: a `completed_with_errors` detail renders the banner + names ≥1 failing check; a clean run renders no banner and keeps Download primary.
  - **Verify:** open run 168 or 216 (`completed_with_errors`) → banner names the failing cross-check and links to it; Download is visibly secondary.

### Phase 2 — Honesty bugs (P1): the tool must not lie about state or cost

- [ ] 🟥 **Step 4: One canonical run-status vocabulary** — kill the "Completed / Didn't finish / Completed with errors / Failed" four-way split for one run. (Backlog #22)
  - [ ] 🟥 Make `web/src/lib/runStatus.ts` the single source: one `overall_status` → `{label, tone}` map covering `completed`, `completed_with_errors`, `failed`, `aborted`, `correction_exhausted`, `running`, `draft`.
  - [ ] 🟥 Replace the binary `complete.success ? "Done" : "Didn't finish"` in `ResultsView.tsx:375-385` and the `AgentTimeline` terminal-row label with lookups against that map. Ensure `completed_with_errors` never renders as "Didn't finish".
  - [ ] 🟥 Backend: stop deriving `"success": overall_status == "completed"` as the *only* completion signal (`server.py:5867`) — pass the real `overall_status` through the `run_complete` event so the UI maps it directly.
  - [ ] 🟥 Fire a completion toast for `completed_with_errors` too (currently only `rc.success` triggers it — `appReducer.ts:646`).
  - [ ] 🟥 Web tests: given a `completed_with_errors` run, Summary card, activity log, and History badge all show the *same* label; assert "Didn't finish" never appears for that status.
  - **Verify:** re-run an extraction that lands `completed_with_errors` → all three surfaces read "Completed with errors" (or the agreed single label); no "Didn't finish".

- [ ] 🟥 **Step 5: Honest live cost meter** — the streaming meter must reflect the real bill, or say what it excludes. (Backlog #23)
  - [ ] 🟥 Preferred: include scout + reviewer tokens in the live meter. Scout runs on `/api/scout/{session}` (separate stream) and the reviewer emits no `token_update` (`server.py:1606`) — so either (a) emit token events from those passes, or (b) after each pass, push a one-shot rollup delta into the extraction stream's token state.
  - [ ] 🟥 Minimum-viable fallback if (a) is too big: relabel the meter "Extraction only (excludes scout & AI review)" so $2.03-vs-$3.55 isn't presented as the total.
  - [ ] 🟥 Test: a mocked run with scout + reviewer token rows → live-meter total equals the Overview `telemetry_rollup` total (or the label is present).
  - **Verify:** run an extraction with scout + auto-review on → the number the meter settles on matches the finished Overview cost (run 216 reference: should reach $3.55, not stop at $2.03).

- [ ] 🟥 **Step 6: One number-formatting convention everywhere** — grouped, fixed decimals, in columns *and* messages. (Backlog #9)
  - [ ] 🟥 Format figures in cross-check *messages* at the source (`cross_checks/*.py`, e.g. `sofp_balance.py:74`) using a shared grouping/`:,.2f` helper, so `assets (1,002,593)` not `(1002593.0)`.
  - [ ] 🟥 Give `ValidatorTab.tsx` Expected/Actual/Diff a fixed fraction-digit rule (currently bare `toLocaleString()`).
  - [ ] 🟥 Update the cross-check message pinning tests in `tests/test_cross_checks.py` to the new formatted strings.
  - **Verify:** `pytest tests/test_cross_checks.py -q` green; Cross-checks tab shows grouped figures in both the columns and the message.

- [ ] 🟥 **Step 7: Notes pre-run affordance + post-scan nudge** — notes look inert and nothing invites turning them on after a scan. (Reframed backlog #24)
  - [ ] 🟥 Fix the unchecked-note label contrast in `NotesRunConfig.tsx:41-49` (drop `grey300` for a normal-weight readable colour) so a togglable checkbox never reads as disabled.
  - [ ] 🟥 When the scout reports notes found (`PreRunPanel.tsx:1359-1382`), surface a one-line nudge near the notes block ("Scout found N notes — tick any you want extracted"). No auto-selection.
  - [ ] 🟥 Web test (`PreRunPanel`/`NotesRunConfig`): note checkboxes are enabled and toggle; the nudge appears after a mocked scout-notes result.
  - **Verify:** upload a doc, run scout → notes read as selectable, the nudge appears, ticking a note enables its model dropdown and includes it in the start payload.

- [ ] 🟥 **Step 8: Scan-first guard on "Start extraction"** — don't let a run start with no scan and zero chosen formats without warning. (Backlog #5)
  - [ ] 🟥 In `PreRunPanel.tsx` (`canRun`, `~:1002`), when scout hasn't run and all variants are unresolved, either soft-warn ("Formats not detected — run pre-scan or pick a format?") or gate Start behind a confirm.
  - [ ] 🟥 Web test: Start is guarded/soft-warned in the no-scan/no-format state; unaffected once ≥1 format resolves or scout ran.
  - **Verify:** fresh upload → Start prompts about missing scan/formats; after Auto-detect, Start is clean.

- [ ] 🟥 **Step 9: Flag "no source page" values in Figures** — the unverifiable rows are exactly the ones needing scrutiny. (Backlog #6)
  - [ ] 🟥 `ConceptsPage.tsx`: add a per-row "no source" badge and a filter/toggle ("Show only unverified") keyed on missing evidence page (`evidencePages.ts`).
  - [ ] 🟥 Web test: a row with no source page shows the badge and is included by the filter.
  - **Verify:** open a run's Figures tab → values lacking a source page carry a visible badge; the filter narrows to them.

- [ ] 🟥 **Step 10: Add-user form autofill fix** — stop the browser from injecting an email into Name and a saved password. (Backlog #8)
  - [ ] 🟥 `UsersTab.tsx:263-286`: set `autoComplete` correctly (`off`/`new-password`, mirroring the guard already in `GeneralSettingsForm.tsx:382,423`), add visible `<label>`s, add an inline 8-char hint; apply the same to the inline reset-password field (`:227-234`).
  - [ ] 🟥 Web test: add-user inputs carry the expected `autoComplete` values and visible labels.
  - **Verify:** open Users → Add user with a browser password manager active → Name field is empty, password not pre-filled.

### Phase 3 — Confirmation & undo consistency (P1, reframed #4)

- [ ] 🟥 **Step 11: Unify the confirm pattern + close the real gaps** — one dialog style; no self-lockout.
  - [ ] 🟥 Replace the raw `window.confirm` in `BenchmarksPage.tsx:134` with the shared `ConfirmDialog`.
  - [ ] 🟥 Add a confirm to **Reset password** in `UsersTab.tsx` (currently no dialog).
  - [ ] 🟥 Hide/disable **Disable** and **Revoke admin** on the signed-in admin's *own* row (`UsersTab.tsx:184-224`) — thread the current user's identity in; keep the server last-admin guard as backstop.
  - [ ] 🟥 Web tests: benchmark delete uses `ConfirmDialog`; reset-password confirms; self-row destructive actions are absent.
  - **Verify:** delete a benchmark → shared dialog; reset a password → confirm; your own row shows no Disable/Revoke-admin.

- [ ] 🟥 **Step 12 (optional, larger): Soft-delete / undo for Delete run** — a short undo window after deletion.
  - [ ] 🟥 Decide scope with product: tombstone + N-second undo toast vs. hard delete (current). If in scope, add a soft-delete flag + reaper.
  - **Verify:** delete a run → undo toast restores it within the window. *(Flagged as a separate decision — may defer.)*

### Phase 4 — Landing/History lifecycle hygiene (P1/P2, the real half of #10)

- [ ] 🟥 **Step 13: Recent-runs prioritises results; drafts metric reworded** — the landing page shouldn't be all empty drafts. (Backlog #10, landing half only — History pagination/sort were REFUTED)
  - [ ] 🟥 `api.ts` recent-runs fetch (`:282-287`): prioritise/split completed results from drafts, mirroring what HistoryPage already does.
  - [ ] 🟥 Reword the "Drafts in progress" tile (`StatTiles.tsx:52`) to "Unstarted drafts" (or similar) and include `completed_with_errors` in the "Completed this month" count (`api.ts:307`).
  - [ ] 🟥 Web tests: recent-runs surfaces results ahead of drafts; the month metric counts errored-completions.
  - **Verify:** landing page shows recent *results* prominently; the month count includes runs that finished with advisories.

- [ ] 🟥 **Step 14: Bulk-clear stale drafts from the UI** — the `DELETE /api/runs/drafts` endpoint exists; surface it. (Backlog #10)
  - [ ] 🟥 Add a "Clear unstarted drafts" action (with `ConfirmDialog`) on the landing/History drafts section, calling the existing bulk endpoint.
  - [ ] 🟥 Web test: the action calls the endpoint and refreshes the list.
  - **Verify:** click Clear drafts → confirm → the 79 drafts drop out of History and the tile updates.

### Phase 5 — Polish & consistency (P2/P3)

- [ ] 🟥 **Step 15: Statement-order + acronym gloss on Activity/Overview** — order SOFP→SOPL→SOCI→SOCF→SOCIE (client sort or backend `ORDER BY`), add the existing `templateSubtitle` gloss to agent rows (`RunDetailView.tsx:1070`). (Backlog #14, #12-legend)
  - **Verify:** Activity lists statements in preparer order; each shows its plain-English name.
- [ ] 🟥 **Step 16: Proportional font for accountant-facing meta** — swap `pwc.fontMono` on `agentMetaRow`/telemetry for the body font; keep raw ids in tooltips. (Backlog #12)
  - **Verify:** Activity/telemetry rows read as prose, not a debug log.
- [ ] 🟥 **Step 17: Calmer "needs attention" wording** — distinguish advisory (non-blocking) from failing so "8/8 passing" + "3 needs attention" don't look contradictory (`RunDetailView.tsx:490-504`). (Backlog #13a)
  - **Verify:** a warnings-only run reads as clean-with-advisories, not alarming.
- [ ] 🟥 **Step 18: "Open run report" deep-links to Figures** — thread `initialRunTab="values"` on that nav path (`App.tsx:694`) to match the copy. (Backlog #25)
  - **Verify:** click "Open run report" → lands on Figures.
- [ ] 🟥 **Step 19: Overall vs per-statement progress** — give the stepper an overall-run phase instead of the selected tab's (`ExtractPage.tsx:267-270`). (Backlog #26)
  - **Verify:** stepper stays pre-Complete until *all* statements finish.
- [ ] 🟥 **Step 20: Differentiate scout log rows** — add per-statement/page labels to `discover_notes`/`Read Face Structure` (`toolLabels.ts:177-185`); add a rough progress hint to the pre-scan spinner (`ScoutToggle.tsx:152`). (Backlog #27)
  - **Verify:** scout log rows are distinguishable; the spinner shows some progress signal.
- [ ] 🟥 **Step 21: Smaller polish batch** — model-name abstraction under Advanced (#15), date-range presets in History filters (#16), Field-labels default to SOFP (#19), Users table hierarchy pass (#20), source-PDF default width bump (Figures 7f-P2), notes-tab control grouping (#7c), louder confidence dots (#5-P2), AI-review empty-state collapse (#7e), mTool helper-line progressive disclosure (#7g).
  - **Verify:** each has a matching web test / visual check; group into 1–2 PRs.
- [ ] 🟥 **Step 22: Notes formatter error handling** — audit the `error_type` map so no error hits the reasonless fallback, and add a dismiss to the error strip (`vocabulary.ts:171-185`, `NotesReviewTab.tsx:938`). (Reframed backlog #3)
  - **Verify:** each backend formatter `error_type` maps to a specific reason+remedy; the strip is dismissable.

### Phase 6 — Investigations (DISPUTED — verify before touching code)

- [ ] 🟥 **Step 23: "10 STATEMENTS" reconciliation** — the tile counts face-agents (=5) in code; couldn't reproduce 10. Open the exact run the reviewer saw; if a real "10" surfaces, trace the count and fix; else close as not-a-bug. (Backlog #13b)
  - **Verify:** documented conclusion (reproduced+fixed, or closed with reason).
- [ ] 🟥 **Step 24: "Editable abstract row" import check** — ABSTRACT rows are never editable in the UI, so an editable "Statement of cash flows, indirect method" means that row imported as a LEAF. Check the concept import/classification for that template; fix upstream if mis-classified.
  - **Verify:** the row imports as ABSTRACT and renders non-editable, OR confirmed already correct.

---

## Rollback Plan
- **Per-step, small commits** on a branch (e.g. `fix/ux-qa-review-2026-07`), one logical fix per commit, each with its pinning test — so any single step reverts cleanly with `git revert`.
- **Highest-risk step is the reaper (Step 1)**: it mutates run status on boot. Guard behind a generous age threshold and log every flip; if it mis-fires, revert the `_lifespan` call and the `running` rows are untouched by the rest of the app.
- **Status-vocabulary change (Step 4)** touches shared display code — verify the full `web` vitest suite and the run-lifecycle pytest before merge; revert is isolated to `runStatus.ts` + the two call sites.
- **State/data to check after any lifecycle change:** query `output/xbrl_agent.db` for rows left in `running` and confirm terminal-status invariants (gotcha #10) still hold.
- No template/linkbase/formula files are touched by this plan, so the XBRL-correctness invariants (gotchas #3, #17) are not at risk.
