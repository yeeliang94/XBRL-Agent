# Stability Investigation: Recurring Failure Themes and Stabilization Seams

**Assessment date:** 2026-09-02

**History window:** `c7f1d2d49f893e2669293ce3e033e51adce3e413..32e0555fec1620f4f3fb994089cb81eca3d6af43` (2026-08-26 through 2026-09-02)

**Conclusion:** The recurring bugs are concentrated in two architectural themes: semantic identity is reconstructed after resolution, and run completion is assembled from distributed mutable state. A third recurring failure mode—treating incomplete or unavailable evidence as clean—amplifies both. These themes warrant stabilization work before more filing-path features are added.

**Immediate priority:** Fix P0-1, stale concept identities on template re-import. Canonical mode is mandatory (`server.py:110-121`), and startup imports every registered template into the persistent audit database on every server boot (`server.py:92-94`, `server.py:2926-2947`). For face and numeric-notes templates routed through the concept-model importer, a row rename, move, or removal followed by an ordinary restart can therefore create or preserve a stale writable identity. This is an active lifecycle path, not a hypothetical maintenance operation; prose notes use a separate importer and are outside the reproduced defect.

This is an investigation of recurring technical themes, not personnel or team ownership. Git records one author throughout the window, so it cannot support conclusions about which human team caused defects.

## Scope and method

Primary sources were the repository contract (`AGENTS.md`), the task router (`CLAUDE.md`), the relevant invariants in `CLAUDE-REFERENCE.md`, implementation code, pinning tests, commit diffs, and the current uncommitted worktree. Historical `docs/PLAN-*.md` files were used only to identify prior intent; they were not treated as current contracts.

The review covered all 26 non-merge commits after `c7f1d2d`. Eleven commit subjects contain “Fix,” “Harden,” “Correct,” or “Preserve”; that title-based count is only a signal, but the diffs confirm that most of those commits repair production-facing correctness or completion behavior. The range changes 348 files with 34,026 insertions and 10,234 deletions. The raw highest-frequency paths include `CLAUDE.md` (11 commits), `web/src/pages/ExtractPage.tsx` (9), `server.py` (9), `web/src/components/RunDetailView.tsx` (8), and `web/src/components/NotesReviewTab.tsx` (8). Among the recurring production-backend touch points, `concept_model/filing_targets.py` and `db/schema.py` appear in 6 commits, `db/repository.py` in 5, and `mtool/template_map.py` in 4.

The current worktree was preserved. At completion it contains 28 paths: 26 tracked modifications and two untracked files, this assessment and one test. Of the 26 modified tracked paths, 22 were also changed in the history window. That overlap is useful evidence: the current fixes remain concentrated in the same recently changed contracts rather than in unrelated areas.

No paid or live model calls were made.

## Ranked risk themes

### 1. Critical: semantic identity is not yet carried as one immutable value

The product has several identities for what users experience as “the same field”: taxonomy concept, canonical concept UUID, template family, statement variant, sheet, row, column, period, entity scope, dimensions, label, section hint, and legacy workbook role. The repository correctly documents that taxonomy capability and physical workbook slot are different (`CLAUDE-REFERENCE.md:1610-1617`) and already has a rich `FilingTarget` structure (`concept_model/filing_targets.py:47-69`). The remaining problem is that downstream paths still receive partial representations and reconstruct equivalence.

The sequence of fixes is unusually concentrated:

- `b510095` introduced semantic mTool mapping and SOCIE support.
- `43a3283` added unverified-template confirmation and degraded reporting.
- `3056a1e` established the canonical template-field semantics registry.
- `68282fb` restored legacy behavior at boundaries where no authoritative manifest exists.
- `8a6a09a` corrected filing-field semantics across facts, notes, persistence, API, and UI.
- `dd97105` added physical period/scope slots, coordinate guards, duplicate-label disambiguation, and conflicting-write detection.
- `5f7c6b0` kept those guards active across incremental writes, preserved prior-year slots, and added an audited recovery path for rejected writes.
- `ba58eb9` required exact taxonomy dimensions for category columns and scoped note targets to their canonical sheets.
- `32e0555` expanded taxonomy coverage diagnostics after those mapping changes.

Nine commits in seven days touching one semantic-routing path is the strongest recurrence signal in the window.

The current implementation shows where identity is still recomposed:

- `FilingTarget.dimensions` carries period and entity scope as dictionary entries, while physical identity is separately assembled into `target_id` from concept, sheet, row, and column (`concept_model/filing_targets.py:186-213`).
- `tools/fill_workbook.py` accepts either a label plus section or an explicit row, resolves the label again, then separately checks row and coordinate writability (`tools/fill_workbook.py:384-443`, `tools/fill_workbook.py:475-543`).
- `mtool/template_map.py` independently narrows taxonomy occurrences by sheet, period block, dimensions, or legacy column role (`mtool/template_map.py:331-416`) before producing a physical cell (`mtool/template_map.py:419-578`). This is a legitimate adapter for uploaded mTool files, but its input should already be a complete semantic identity.
- The current WIP has to recognize that an ambiguous-label failure and a later explicit-row success are the same logical retry by comparing candidate rows, sheet, and column (`extraction/agent.py:492-539`). That equivalence belongs at the resolution seam, not in mutable error cleanup after the write.
- Formula targets are now classified as audit-only refusals so they do not masquerade as missing output (`extraction/agent.py:548-570`, `tools/fill_workbook.py:525-555`). This is another example of target role being rediscovered after resolution.

Impact is high because failures can be silent or plausibly successful: a value can land in the wrong period, entity, category, or repeated-label row; a valid retry can remain poisoned by an earlier error; or a protected formula can be treated as missing data. Invariant 12 records an earlier variant of exactly this outcome: an invalid Group SOCIE coordinate returned no concept, the caller skipped it, and the statement could be empty while the agent reported success (`CLAUDE-REFERENCE.md:493-523`).

### 2. Critical: completion and publication have distributed ownership

One run has several kinds of completion: agent result saved, canonical facts committed, workbook merged, reviewer passes finalized, child rows terminal, cross-checks persisted, notes coverage known, and a terminal run status emitted. These are currently coordinated across the 3,235-line `run_multi_agent_stream` function (`server.py:4948-8182`), helper functions, mutable result objects, separate SQLite connections, the filesystem, and the SSE queue.

Recent history repeatedly repaired this area:

- `62d5e85` hardened extraction/review orchestration, reviewer cancellation, and overlapping reviewer execution.
- `b1402bd` fixed notes review finalization and canonical note projection.
- `beb4dd2` added durable incidents, run events, child-row reconciliation, and broader run observability.
- `1747da3` added further notes-review completion safeguards.
- `5f7c6b0` persisted exact terminal errors and surfaced flagged success as attention.

The implementation contains the correct rules, but they are encoded as ordering knowledge:

- `_safe_mark_finished` updates the parent, reconciles unfinished children, and commits, while swallowing all failures (`server.py:3874-3917`).
- cancellation has a separate filesystem-derived partial-publication path with its own merge, pointer write, and commit (`server.py:3920-4042`).
- normal publication writes the merged pointer before later status work (`server.py:6460-6477`), then finalizes face and notes child rows in a nested closure (`server.py:6509-6545`).
- overlapping notes and face reviewers require a gate plus an explicit commit before releasing SQLite’s single writer (`server.py:7003-7020`, `server.py:7318-7341`, `server.py:7515-7523`).
- final status is derived from a long conjunction of independent booleans and optional outcomes (`server.py:7935-8006`), with a final `finally` backstop that chooses `aborted` if no earlier path established a terminal status (`server.py:8142-8151`).

The tests record concrete prior failures: `tests/test_save_result_contract.py:1-17` describes a workbook write being counted as success even though `save_result` was refused; `tests/test_reviewer_parallel_wiring.py:119-151` reproduces the run-97 SQLite lock at the finalization seam; `tests/test_abort_reconciles_agent_rows.py:1-8` records child rows left running under an aborted run; and `tests/test_stop_all_preserves_partial.py:1-20` records cancellation discarding completed statement files.

The risk is false success, stale downloadable workbooks, lost partial work, non-terminal history rows, or a correct underlying result presented with the wrong status. The failures are not primarily caused by asynchronous execution itself. They occur because no deep module owns the complete publication transaction and returns an authoritative receipt.

### 3. High: incomplete, unavailable, and failed states can collapse into “clean”

Several systems produce a collection plus a separate signal explaining whether the collection is complete. If consumers inspect only the collection, empty can mean either “nothing found” or “the assessment failed.”

The final status code explicitly guards a prior false-clean failure: a crashed cross-check returned an empty results list, so `any_check_failed` was false until `cross_check_crashed` was added (`server.py:7962-7972`). Notes coverage similarly treats an unavailable inventory as unresolved, not empty-and-clean (`CLAUDE-REFERENCE.md:1486-1502`). Source-integrity invariant 31 imposes the same rule. The current WIP adds explicit `partial`, `failed_pages`, and `notes_available` metadata to PDF sidecars, and adds stable grounded disposition IDs so an unchanged detector finding can be distinguished from an open finding (`notes/reviewer_agent.py:918-1028`, `notes/reviewer_agent.py:1473-1529`; `db/schema.py:220-227`).

This is a shared semantic problem, not a reason to create one generic result class for every subsystem. Each pass needs an explicit completeness state in its own interface: complete, partial, unavailable, failed, or cancelled, with “clean” legal only for a complete assessment.

### 4. High: contracts are pinned reactively and sometimes through code shape

The repository has strong incident-specific tests, but many were added after a narrow production failure. The current WIP adds tests for ambiguous-label-to-row retry equivalence, formula refusals, grounded notes dispositions, partial sidecars, and terminal attention reasons. Those are valuable regressions, but they expose missing state-transition and compatibility-matrix tests.

Some orchestration ordering is pinned by parsing function source rather than crossing a behavioral seam. For example, the first two tests in `tests/test_reviewer_parallel_wiring.py` search the source text to prove task launch and workbook-refresh ordering (`tests/test_reviewer_parallel_wiring.py:21-47`). This makes safe refactoring harder and verifies syntax rather than outcome. The same file’s live orchestrator test is stronger because it exercises concurrency and the real SQLite writer seam (`tests/test_reviewer_parallel_wiring.py:50-196`).

The issue is not lack of test volume. It is that the most important interfaces—resolved filing identity and durable finalization—do not yet exist as compact test surfaces.

### 5. Medium: very large modules increase change blast radius

The most active modules are also large: `server.py` is 9,008 lines, `db/repository.py` 3,943, `db/schema.py` 3,435, `notes/reviewer_agent.py` 1,985, and `extraction/agent.py` 1,438. Size alone is not a defect. The problem is interface shallowness: callers and tests must understand many ordering constraints and mutable flags.

There are already successful deeper seams that should be preserved and used as models:

- agent event construction and retries are centralized in `agent_runner.build_agent_event`, `make_emitter`, `RetryPolicy`, and `run_agent_with_retries` (`agent_runner.py:110-153`, `agent_runner.py:681-765`);
- cross-check backend selection is centralized in `CrossCheckPlan`, `select_cross_check_backend`, and matching async/sync runners (`server.py:441-526`).

These reduce duplicated policy without erasing real adapter differences. Stabilization should extend that approach, not repeat already completed orchestration-seam work.

## Current WIP implications

The uncommitted work is directionally aligned with this assessment. It makes previously implicit states explicit:

- typed successful and failed write identities, candidate rows, and refusal kinds in `tools/fill_workbook.py:75-117` and `tools/fill_workbook.py:343-363`;
- retry equivalence and audit-only formula handling in `extraction/agent.py:492-570`;
- exact finding IDs, grounded pages, and evidence for notes-review dispositions in `notes/reviewer_agent.py:918-1028` and `notes/reviewer_agent.py:1473-1529`;
- schema v45 persistence for that identity in `db/schema.py:1938-1947` and `db/repository.py:1651-1675`;
- preservation of an attention reason when an otherwise successful agent is persisted as `completed_with_errors` (`server.py:1270-1312`, `server.py:6535-6542`);
- explicit sidecar failure metadata while partial requested-page results stay
  unpublished because scout ranges cannot prove note completeness.

The WIP should be finished as one contract-aware stabilization batch before structural extraction begins. It crosses a schema migration, repository methods, agent tools, prompts, server status, frontend types, and tests. Splitting only by file layer would risk merging an interface producer without all consumers. Before merge, run every pin named by the changed invariants, refresh the prompt audit because `prompts/notes_reviewer.md` changed, and run the required frontend build for shared TypeScript changes.

The WIP is mitigation, not the final seam. In particular, `_unresolved_fill_error_metadata` is a second mutable dictionary beside `_unresolved_fill_error_state` (`extraction/agent.py:153-154`, `extraction/agent.py:500-507`). That is acceptable for an immediate bug fix, but it should disappear behind a typed write-attempt ledger during stabilization.

## Open risks found during this investigation

The history analysis also exposed current risks that are not covered by the
focused green baseline. They should receive characterization tests and narrow
containment before the structural stages below.

| Priority | Exposure | Evidence and required check |
| --- | --- | --- |
| P0 — first fix | Automatic startup template re-import retains obsolete identities and makes them targetable. | Canonical mode is mandatory (`server.py:110-121`), and the server imports every registered template into the persistent audit database on every boot (`server.py:92-94`, `server.py:2926-2947`). Every face template uses the concept-model import path (`concept_model/bootstrap.py:45-66`); numeric-notes templates use the same path, while prose notes use a separate importer and are not covered by this reproduction (`concept_model/bootstrap.py:69-105`). A template ID is path-derived and permanently suffixed `-v1`, while a concept UUID includes row and label (`concept_model/parser.py:429-448`, `concept_model/parser.py:761-764`). `import_template` upserts current nodes but does not retire nodes removed by a later parse (`concept_model/importer.py:85-152`), and the linear-target import then rebuilds targets for every node still attached to that template ID (`concept_model/importer.py:412-510`). A temporary-database reproduction renamed one LEAF row and re-imported the same template: the node count grew by one, the old and new UUIDs both remained, and each received two Company targets. The filing-coverage query only rejects a UUID when a manifest row exists but is non-writable (`concept_model/filing_targets.py:705-720`); a stale UUID with no current manifest row is not caught by that condition. Because an ordinary qualifying template edit plus restart exercises this path, add row-move, rename, and removal tests first, then either retire obsolete current nodes atomically or pin runs to immutable template revisions. |
| P0 | A physical workbook write can succeed before canonical fact validation rejects the same cell. | The extraction tool accepts numeric values or strings, resolves a cell, and atomically saves the scratch workbook (`tools/fill_workbook.py:23-59`, `tools/fill_workbook.py:621-667`). Canonical projection happens afterwards; an individual unresolved or non-numeric cell is skipped/rejected while sibling facts commit (`concept_model/cell_resolver.py:137-198`). `_project_facts_if_canonical` treats that `has_gaps` result as advisory, not a save blocker (`extraction/agent.py:240-304`). `tests/test_cell_resolver.py:250-280` intentionally pins per-cell rejection, but no test proves the statement cannot then finalize with the rejected cell absent from the canonical export. Add that end-to-end test first; the target design is resolve-and-validate before either representation is written. |
| P1 | Artifact degradation can coexist with a clean parent status. | Initial canonical export keeps the agent scratch workbook on export failure and emits a recoverable SYSTEM error (`server.py:159-258`, `server.py:6386-6405`), but that failure is not an input to the final decision at `server.py:7935-8006`. The SYSTEM row is later reconciled as failed while the parent can remain completed (`db/repository.py:866-899`). Parallel notes-refresh failure is only logged (`server.py:7534-7549`). Add an artifact-currentness input to finalization and require that a stale/degraded artifact can never be `ready`. |
| P1 | Restart reconciliation can leave a terminal parent with running children. | Startup calls `reconcile_stale_runs(max_age_hours=0)` (`server.py:2880-2902`), but that repository function updates only `runs.status` and `ended_at` (`db/repository.py:2468-2493`). Child reconciliation currently lives in `_safe_mark_finished`, which startup does not call (`server.py:3874-3917`). Add a restart test asserting that the parent, every child, and the durable terminal event settle together. |
| P1 | Cancellation ownership is not safe across all loops and finalization schedules. | `task_registry.cancel_all` directly invokes `Task.cancel()` on globally stored tasks (`task_registry.py:54-62`). Suite children run under `asyncio.run` in a worker thread while the HTTP stop path calls the same registry from the main loop (`api/suite_runner.py:388-412`, `api/suite_runner.py:702-724`). In the automatic notes reviewer, normal exits wait on the SQLite finalization gate, but `CancelledError` persists without waiting; making it wait naively would deadlock because face-review cancellation awaits notes before releasing the gate (`server.py:2397-2422`, `server.py:7242-7254`, `server.py:7515-7517`). Reproduce both schedules with real loops and SQLite, then move cancellation and final writes behind `RunSupervisor` and `RunFinalization`. |

## Proposed deep modules and seams

### A. Filing resolution module

Create one deep module whose interface accepts a complete filing context and one semantic fact identity, then returns either a resolved target or a typed refusal.

Suggested interface shape:

```text
resolve_target(
  FilingContext(standard, level, statement, variant, template_family),
  FactIdentity(concept_uuid, taxonomy_concept, dimensions, period, entity_scope),
  LocatorHint(label?, section?, row?)
) -> ResolvedTarget | TargetRefusal
```

`ResolvedTarget` should carry the canonical identity and the exact managed-template slot. A successful resolution should also carry one stable `attempt_id`; retries using a different locator can then settle the same attempt without candidate-row inference. `TargetRefusal` should distinguish ambiguous, missing, non-writable, formula-owned, wrong-family, and legacy-unverified outcomes.

The external seam belongs in `concept_model`, near the existing `FilingTarget` contract. Managed-template resolution is one adapter. Uploaded mTool resolution is a second real adapter because its taxonomy markers and physical XML vary. Legacy label resolution remains an explicit degraded adapter. `tools/fill_workbook.py`, extraction projection, reviewer writes, persistence validation, and `mtool/template_map.py` should consume the resolved result rather than reinterpreting labels.

Deletion test: if the module were removed, target-role, retry-equivalence, family, period/scope, and refusal logic would reappear in at least extraction, workbook filling, notes editing, and mTool filing. That complexity demonstrates useful depth.

### B. Run finalization module

Create one deep module that owns the transition from accumulated pass outcomes to a durable run result. Its interface should accept immutable outcomes and artifact receipts, commit parent/child status and artifact pointers in the required order, and return a `FinalizationReceipt` containing the persisted terminal status and events to publish.

It should own:

- the legal run-state transition table;
- child-row reconciliation;
- merged and partial artifact publication receipts;
- status derivation from extraction, merge, cross-check, reviewer, notes coverage, and source-integrity outcomes;
- SQLite transaction/commit/rollback policy;
- idempotency for cancellation and `finally` backstops.

`server.run_multi_agent_stream` should remain the composition shell that launches work and drains events. It should not compute terminal status or know which commit releases another pass. Event emission should occur from the returned receipt after durable persistence, avoiding “event says complete but commit failed” ambiguity.

This module needs an internal persistence seam for tests, with the real SQLite repository and an in-memory adapter. There are two adapters, so the seam is concrete rather than hypothetical.

Pair it with a loop-aware `RunSupervisor` that owns run-level and child tasks as
`(task, owning_loop)` records. Cancellation from another thread or event loop
must dispatch with `owning_loop.call_soon_threadsafe`; the supervisor should
also keep the run-level task cancellable while merge, cross-check, and
formatting stages have no registered agent child. Execution supervision and
durable finalization are separate modules: the supervisor stops work, while
`RunFinalization` records the resulting truth.

### C. Finding disposition module

Move detector-finding identity and transitions into a small deep module used by notes review first and available to face review when the contracts converge. Its interface should expose `open`, `resolve`, `escalate_with_evidence`, and `introduce`, keyed by a stable finding ID. Only original findings may be escalated; introduced findings remain open until resolved. The current WIP behavior in `notes/reviewer_agent.py:996-1028` and `notes/reviewer_agent.py:1473-1529` is the starting contract.

The interface should return a final ledger snapshot from which “clean,” “handled with human review,” and “still open” are derived. Prompt wording, DB columns, and UI labels then consume one disposition state rather than reconstructing it.

### D. Explicit pass outcomes

Define narrow, subsystem-specific result types for cross-checking, notes coverage, source preparation, source integrity, face review, and notes review. Each must include a completeness state and structured reason. This is not a universal framework. The value is preventing `[]`, `None`, and `False` from carrying both “clean” and “did not run.” `RunFinalization` consumes these outcomes without inspecting subsystem internals.

## Test gaps to close

1. **End-to-end filing identity matrix.** Cover MFRS/MPERS × Company/Group × statement variant × current/prior period × entity scope × scalar/dimensional × managed/legacy/mTool. Verify the same semantic identity from extraction through canonical facts, workbook render, mTool resolution, and reverse ingest. Assert typed refusals where a combination is unsupported.
2. **Write-attempt state transitions.** Drive unresolved → retried with alternate locator → resolved; unresolved → formula-owned/audit-only; unrelated success leaves the original open; and two ambiguous failures cannot be cleared by one success. Test through the filing-resolution interface rather than directly mutating `ExtractionDeps`.
3. **Finalization schedule tests.** Exercise face-first, notes-first, cancellation during backoff, cancellation during each reviewer, database lock, merge failure, canonical re-export failure, event disconnect, and process-restart reconciliation. For every schedule, assert the parent is terminal, every child is terminal, artifact pointers describe what exists, and the final event matches the committed status.
4. **No-false-clean contract tests.** For every pass, assert unavailable, partial, crashed, timed out, and skipped-without-evidence cannot produce `completed`. Include empty-result collections explicitly.
5. **Replace source-order pins after seams exist.** Keep behavioral tests like `test_live_pipeline_reviewers_are_in_flight_together`; remove source-string ordering checks once `RunFinalization` and reviewer scheduling have testable interfaces.
6. **Migration compatibility.** For every new finalization or finding column, migrate from representative older schemas one version at a time and rerun initialization to prove idempotence, following `db/schema.py:3390-3412`.
7. **Real mTool evidence remains an operator gate.** Automated tests can validate the adapter, but an unknown uploaded layout must remain candidate/degraded until a genuine mTool 2.2 workbook passes Windows Validate/Generate, as required by `CLAUDE-REFERENCE.md:1664-1668`.

## Staged stabilization plan

### Stage 0 — Contain automatic stale identities, then finish the current WIP

Scope: first characterize and contain P0-1 in the automatic server-startup import path. Then complete the current fixes without adding new filing behavior, characterize P0-2, and add narrow containment if it reproduces end to end. Refresh the prompt audit. Run focused pinning tests, then the required broad backend and frontend checks.

Acceptance criteria:

- every current WIP test passes, including schema v45, write retry identity, formula classification, grounded disposition, sidecar partiality, and status/error persistence;
- a renamed, moved, or removed template row cannot remain a target in the current template revision, while historical facts remain readable through their pinned revision;
- a cell rejected by canonical projection cannot be finalized as successfully filed merely because the scratch workbook contains it;
- `venv/bin/python scripts/refresh_prompt_audit.py` leaves `tests/test_prompt_audit_matches_live.py` green;
- focused invariant pins pass before `venv/bin/python -m pytest tests/ -n auto`;
- targeted Vitest passes and `cd web && npm run build` succeeds;
- the final diff contains no unrelated edits and no live-model call is required.

### Stage 1 — Establish filing identity as the single resolution interface

Scope: introduce `FactIdentity`, `ResolvedTarget`, `TargetRefusal`, and stable write-attempt identity. Adapt the existing managed-template, mTool, and legacy paths without changing policy.

Acceptance criteria:

- all downstream writers accept resolved targets or typed refusals; no downstream path re-resolves a managed target from label text;
- alternate locators for one fact settle one write attempt without candidate inference in `extraction.agent`;
- the full filing identity matrix passes;
- `scripts/audit_template_field_semantics.py --check` and all invariant 12, 15, 17, 21, 22, and 28 pins pass;
- mTool category sheets still fail closed and legacy behavior remains explicitly degraded.

### Stage 2 — Establish authoritative finalization

Scope: introduce immutable pass outcomes, `RunFinalization`, and the loop-aware
`RunSupervisor`; move status derivation, child reconciliation, artifact
publication, cancellation ownership, and commit ordering out of
`run_multi_agent_stream`.

Acceptance criteria:

- one module owns legal terminal transitions and status derivation;
- every successful call returns a durable receipt whose terminal status equals the DB row and final event;
- cancellation preserves every completed artifact and cannot leave a child row running;
- cancellation from the HTTP loop reaches suite-child tasks on their owning loop, including between-agent merge/check stages;
- reviewer overlap cannot expose the merged workbook to concurrent writers or hold SQLite’s writer lock across the notes gate;
- all schedule tests pass without inspecting source text;
- `run_multi_agent_stream` contains orchestration, not persistence/status policy.

### Stage 3 — Unify finding disposition and completeness semantics

Scope: move stable finding transitions behind `FindingDisposition`; make each pass report explicit completeness.

Acceptance criteria:

- unchanged detector findings can be terminal only through grounded escalation tied to an exact original ID;
- introduced findings cannot be escalated away;
- empty, partial, unavailable, failed, and cancelled pass outcomes cannot be interpreted as clean;
- notes coverage, source integrity, PDF sidecar, and reviewer UI use the same persisted disposition/completeness vocabulary for their own result types;
- false-green pinning suites pass, especially `tests/test_notes_integrity_false_greens.py`.

### Stage 4 — Remove obsolete compatibility logic and structural pins

Scope: after the new seams have shipped and are stable, remove superseded dictionaries, duplicated target resolution, boolean status assembly, and source-order tests. Preserve documented legacy adapters and inert migration history.

Acceptance criteria:

- deleting the old paths does not change observed behavior;
- each policy appears in one implementation and is tested through its interface;
- no documented inert schema field/table or mTool legacy safeguard is removed;
- full backend, frontend, template audit, and snapshot checks pass;
- a final history review shows new fixes landing in the new modules rather than being propagated across server, DB, agent, and UI layers.

### Stage 5 — Measure whether recurrence actually fell

Scope: tag defects for two release cycles using the themes in this report: filing identity, finalization, completeness, findings, and unrelated.

Acceptance criteria:

- no wrong-period, wrong-scope, wrong-category, or wrong-sheet write escapes the filing matrix;
- no run reports clean when a required pass is partial, unavailable, or failed;
- no terminal run has a running child or a stale/missing artifact pointer;
- any recurrence is fixed once behind the owning interface, with no cross-layer patch chain comparable to the nine-commit semantic-routing sequence above.

## Recommended order and stop conditions

Finish Stage 0 first. Stage 1 and Stage 2 are the highest-value structural work; implement them as separate reviewable changes because they affect different invariants. Stage 3 follows once both consumers have stable interfaces. Do not combine this with UI redesign, model changes, template regeneration, or new filing features.

Stop and reassess if Stage 1 requires weakening exact taxonomy resolution, if Stage 2 cannot preserve partial artifacts under cancellation, or if a proposed abstraction has only one adapter and merely passes parameters through. Those outcomes would reduce safety rather than deepen the modules.

The expected result is not fewer lines by itself. It is locality: a future identity bug changes the filing-resolution implementation and matrix tests; a future completion bug changes finalization and schedule tests. It should no longer require synchronized repair across `server.py`, mutable agent state, repository methods, schema, prompts, and frontend interpretation.

## Verification performed

- Combined focused baseline: 137 passed, with five existing warnings.
- Focused critical-regression baseline: `venv/bin/python -m pytest tests/test_mtool_template_map.py tests/test_mtool_column_detect.py tests/test_save_result_contract.py tests/test_notes_integrity_false_greens.py tests/test_reviewer_parallel_wiring.py tests/test_notes_reviewer_self_verify.py -q` — 131 passed, with five existing warnings.
- Documentation invariant: `venv/bin/python -m pytest tests/test_docs_invariants.py -q` — passed.
- Template re-import exposure: reproduced against a copied MFRS Company SOFP workbook and a temporary SQLite database; the temporary directory was removed afterward.
- Evidence validation: every cited repository path and line exists; all 13 cited commits inside the history window and the base anchor resolve; whitespace checks pass.

Not run: the full backend suite, frontend Vitest/build, prompt audit, live-model suites, and template-generation snapshots. This investigation changes no product code, prompt, frontend, template, or taxonomy artifact. Those checks are Stage 0 and implementation acceptance gates, not evidence needed to produce this report.
