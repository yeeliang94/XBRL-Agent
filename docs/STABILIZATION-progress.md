# Stabilization implementation progress

Controlling assessment: `docs/ASSESSMENT-stability-investigation.md`.

## 2026-09-02 — Stage 0 and P1 lifecycle containment complete

### Changes

- Verified the inherited P0-1 template re-import lifecycle implementation:
  obsolete current concept identities are retired, current resolution filters
  them, and historical UUID-pinned facts remain readable.
- Closed P0-2 at the live extraction tool seam. Canonical scalar-fact
  validation now runs after physical locator resolution and before workbook
  mutation. A rejected value does not change the scratch workbook and remains
  an unresolved write that blocks a clean save.
- Made canonical face-export and post-review notes-refresh degradation an input
  to final status. The retained artifact remains downloadable but the run
  finishes `completed_with_errors`; the no-canonical-facts scratch fallback
  remains benign.
- Made restart reconciliation settle the parent, every running child row, and
  one durable terminal `run_complete` event in the caller's SQLite
  transaction.
- Made the task registry loop-aware and thread-safe. Foreign-thread Stop
  requests dispatch cancellation through the task's owning loop.
- Serialized cancelled notes-review final writes behind the same SQLite gate
  as normal finalization. The face-review abort path releases that gate before
  cancelling and awaiting the notes task.

### Evidence

- Red/green characterization was added for canonical rejection before workbook
  mutation, artifact degradation status, restart reconciliation,
  cross-loop cancellation dispatch, and cancellation at the notes-review
  finalization gate.
- Follow-up review characterization distinguishes a wrong-column SOCIE write
  from an audit-only formula-row refusal, treats a failed fact-presence probe as
  unknown, and pins numeric-string page coercion at the typed reviewer-tool
  boundary.
- The standards pass also restored all-requested-pages-or-none PDF sidecar
  publication, filtered retired concepts from current cross-check/eval/UI
  consumers while retaining run-referenced historical facts, and made grounded
  flag-persistence failure a structured non-clean reviewer outcome.
- Focused backend gate: 339 passed.
- Full backend gate: 5,117 passed, 2 skipped.
- Prompt audit refresh reported the generated excerpts already matched;
  `tests/test_prompt_audit_matches_live.py` passed in the focused gate.
- Targeted frontend: 15 passed.
- Frontend production build passed.
- Documentation invariants: 6 passed.
- `git diff --check` passed.

### Performance and quality impact

- This continuation changed no model, prompt behavior, template, or dependency.
  It tightened scanned-PDF sidecar publication and current concept consumers;
  inherited prompt changes were preserved and only verified through the
  required audit.
- Managed canonical writes add one dedicated SQLite connection used only for
  reads per `write_facts` call and reuse it for every proposed cell in that
  call.
- Cancellation uses one `call_soon_threadsafe` dispatch per foreign-loop
  task. Same-loop cancellation remains direct.
- Honest `completed_with_errors` outcomes may reduce the apparent clean-run
  rate when the downloadable artifact is stale; that is the intended result.

### Checks not run

- Live and regression model suites were not run because they require
  credentials/fixtures and paid model calls were prohibited.
- The inherited notes-reviewer token budget and history processors are covered
  by non-live unit tests, but their real-provider behavior was not exercised
  because that would require paid model calls.
- Template generators and snapshot regeneration were not run because no
  template, taxonomy linkbase, or generator changed.
- Real mTool 2.2 Windows Validate/Generate was not run because it requires the
  operator's Windows tooling. The current-node change is in evaluation ingest
  mapping, not generated mTool output.

### Remaining structural stages

- Stage 1: single filing-resolution interface plus the full compatibility
  matrix before caller migration.
- Stage 2: authoritative run finalization and loop-aware run supervision.
- Stage 3: explicit pass completeness and finding-disposition interfaces.
- Stage 4: caller migration and removal of superseded paths/source-order pins.
- Stage 5: recurrence measurements across two release cycles.
