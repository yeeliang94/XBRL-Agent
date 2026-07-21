# Implementation Plan: CodeMode Spike (Monty sandbox) — SOFP only, behind a toggle

**Overall Progress:** `60%` — Phases 1–3 built + tested (2026-07-21); Phase 0/4 live
runs pending
**PRD Reference:** none — decision instrument is docs/PLAN-pydantic-ai-v2.md §D.5 item 3
(this plan is that spike, made concrete). Exploration evidence: 2026-07-21 session
(telemetry query of `output/xbrl_agent.db` + three-agent codebase sweep).
**Last Updated:** 2026-07-21

## Summary

Prototype pydantic-ai Harness **CodeMode** on the SOFP face agent: the model writes one
sandboxed Python script per turn (Monty mini-interpreter) that calls a *text-only subset*
of our existing tools as host-side functions, collapsing the sequential
`write_facts → verify_totals → (fix) → save_result` chain from 3–7 model round trips into
1–2. Telemetry shows every model round trip costs ~27k re-billed prompt tokens and ~12s
wall time while tool execution is free (~0.2s), so round trips are the lever for both
cost and speed. The spike is gated behind a default-off env toggle, measured against
interleaved flag-off baseline runs with accuracy judged by the operator by hand, and
designed to be deletable in one commit. Success thresholds are pre-registered below —
the adopt/keep/delete decision is made against numbers written down before any CodeMode
run, not after seeing the results.

## Key Decisions

- **Toggle, not a separate fork/script**: the value question is "does the *real* pipeline
  get cheaper/faster without losing accuracy" — only answerable inside the real
  coordinator, guards, and telemetry. A standalone prototype would
  measure a different system. The toggle is a single seam (one branch point at agent
  creation) so failure = flip off; abandonment = delete the seam.
- **`XBRL_CODE_MODE` env, default OFF, plus `XBRL_CODE_MODE_STATEMENTS` allowlist
  (default `SOFP`)**: mirrors the repo's kill-switch convention
  (`XBRL_FACT_BASED_CHECKS`, `XBRL_SPOT_CHECK`, `XBRL_NOTES_COVERAGE`). Flag read at
  call time so tests can toggle. NOT surfaced in `/api/settings` for the spike —
  operator-set env only, to keep UI scope at zero.
- **The harness is an optional, exactly-pinned dependency**: lazy-imported only when the
  flag is on. Flag off ⇒ the package is never imported ⇒ runs are byte-identical to today
  (pinned by test). It does NOT enter `requirements.txt`; it lives in a new
  `requirements-codemode.txt` pinning the **exact tested artifact including the CodeMode
  extra** (expected shape `pydantic-ai-harness[codemode]==<tested-version>` — Monty ships
  behind an extra, and the base package alone won't install it; exact extra name
  confirmed in Step 2) plus its transitive pins, validated with `pip check`. The harness
  is 0.x and allows breaking minor releases, so `==`, never `>=`. Installed manually on
  the Mac dev box only; Windows enablement is explicitly out of scope.
- **Text-only tool subset inside the sandbox**: `calculator`, `lookup_definitions`,
  `search_pdf_text`, `write_facts`, `verify_totals`, `save_result`
  (+ `submit_face_coverage` when registered). **`view_pdf_pages` and `read_template`
  stay ordinary tools** — page images must reach the model's own eyes (a script cannot
  look at pictures), and the template summary's value is in provider prompt-cache reuse.
- **All write guards are untouched by construction**: abstract-row guard, no-plug rule,
  save gate (`_check_save_gate` reads `deps.last_verify_result`, which `write_facts`
  nulls and `verify_totals` repopulates — deps state, not conversation state), atomic
  saves. All live inside the tool bodies, host-side, verified in exploration. The spike
  adds NO new write path.
- **Stateful tools must run sequentially inside the sandbox; partial execution is a
  first-class test case**: CodeMode scripts can run inner calls concurrently
  (`asyncio.gather`), but `write_facts` / `verify_totals` / `save_result` mutate shared
  state (workbook file, `deps.last_verify_result`, the save gate) and must be declared
  sequential / non-overlapping — the harness's mechanism for this is confirmed in
  Step 2 and its absence is a no-go. Separately, a script that fails AFTER a successful
  `write_facts` leaves the host-side write in place while the model gets a retry; the
  existing defences (coordinate-keyed overwrites, atomic save, verify-must-follow-last-
  write deps ordering) should make that safe, but Step 4 tests must PROVE no duplicate,
  corrupt, or late writes under exactly these two scenarios.
- **Debug ledger replaces per-tool telemetry** (the agreed trade): every sandbox-callable
  tool is wrapped to append `{script_turn_index, run_code_call_id, seq, tool,
  args_summary, duration_ms, outcome (ok|error), error, result_summary}` to a per-run
  ledger on deps — recorded in a `finally` so FAILED calls land too, and keyed to the
  outer `run_code` call so retries are distinguishable from first attempts. Flushed into
  the existing conversation-trace JSON (new `code_mode_calls` key) and echoed (truncated)
  in the `run_code` tool_result SSE summary. Before building any of this, Step 2
  evaluates the harness's OWN nested-call observability (`ToolReturnPart.metadata`
  tool_calls/tool_returns) — custom wrapping fills only what it lacks (durations,
  failure records), never duplicates what it provides. `run_agent_turns` rows still land
  (one `run_code` row per script turn) — degraded but honest; the Telemetry tab needs no
  changes for the spike.
- **Accuracy is judged manually by the operator** (2026-07-21 decision): no gold
  benchmark for this spike — the operator compares the filled workbooks by eye/Excel.
  The spike's automated measurement is therefore cost, wall time, and model round trips.
  **Metric definition:** "model round trips" = `run_agent_turns` rows
  `WHERE node_kind = 'model_request'` — NOT the Telemetry tab's raw turn total, which
  counts every graph node (model AND tool nodes) and would overstate the baseline.
  Outer `run_code` tool nodes and nested sandbox calls (from the ledger) are reported
  as separate columns, never mixed into the round-trip count.
- **Branch from `main`**: `feat/notes-verbatim-and-scout-inventory` (6 unmerged commits)
  touches notes/scout files only — no expected overlap with `extraction/agent.py` /
  `agent_runner.py`. Do not stack on it.

## Success Criteria (pre-registered — frozen before the first CodeMode run)

Written down NOW so the Step 7 decision cannot be rationalised after seeing results.
The operator may adjust these numbers any time BEFORE Step 6 starts; once the first
flag-on run launches they are frozen.

All comparisons use the Step 6 **interleaved pairs** (off/on alternation, same sitting,
same PDF + model) so prompt-cache warming and provider drift cannot favour one group.

- **ADOPT (widen beyond opt-in)** requires ALL of:
  - mean cost reduction ≥ 15% AND mean wall-time reduction ≥ 15%
  - operator rejects **no more** CodeMode workbooks than flag-off workbooks
  - one deliberately-failed flag-on run is successfully debugged from ledger + trace
    alone (the debuggability bar, tested — not assumed)
- **DELETE the seam** if ANY of:
  - more rejected workbooks than baseline (accuracy regression)
  - savings < 10% on either cost or wall time
  - the ledger proves insufficient to debug the deliberate failure
- **KEEP as off-by-default opt-in** (the middle): savings in the 10–15% band with no
  accuracy regression — record numbers, revisit alongside the face-reviewer candidate.

## Pre-Implementation Checklist

- [x] 🟩 All questions from /explore resolved (pain = cost+speed; telemetry tradeable
  with replacement debug logs; target = most value; accuracy judged manually by the
  operator — no gold benchmark)
- [ ] 🟥 PRD approved — n/a; this plan + PLAN-pydantic-ai-v2.md §D.5 are the reference.
  Product owner sign-off on THIS plan stands in for it.
- [ ] 🟥 No conflicting in-progress work — confirm the notes branch stays unmerged or
  merges cleanly first; spike branches from `main` either way.

## Tasks

### Phase 0: Measuring stick (no CodeMode code)

- [ ] 🟥 **Step 1: Baseline runs** — an early "before" reference and the operator's
  first accuracy calibration. NOTE: the decision numbers come from Step 6's interleaved
  pairs, not from this phase alone — these runs establish the workflow and the
  operator's acceptance standard.
  - [ ] 🟥 3 SOFP-only repeats (same PDF, same model) via the existing repeats feature
  - [ ] 🟥 Record per run: total cost, wall time, and model round trips
    (`run_agent_turns` rows `WHERE node_kind='model_request'` — see metric definition
    in Key Decisions); note cache-read tokens so the pre-cache cost-estimate caveat
    is quantified, not hand-waved
  - [ ] 🟥 Operator reviews the 3 filled workbooks and marks each acceptable / not —
    calibrating the acceptance standard reused verbatim in Step 6
  - **Verify:** a filled baseline table in this doc with the operator's verdict column.

### Phase 1: Feasibility gate (read-only investigation; explicit go/no-go)

- [x] 🟩 **Step 2: Harness compatibility check** — CodeMode's docs assume `agent.run`;
  our loop is hand-rolled `agent.iter()` + node streaming (`agent_runner.run_agent_loop`).
  This is the spike's biggest unknown — resolve it before writing product code.
  - [x] 🟩 Determine the exact install artifact (CodeMode/Monty ships behind an extra —
    expected `pydantic-ai-harness[codemode]`; confirm the real extra name), install it
    in a scratch venv, confirm it tolerates our pinned `pydantic-ai==2.9.0`
    (constraints.txt), and run `pip check`; record the exact `==` pin + transitive pins
    for `requirements-codemode.txt`
  - [x] 🟩 Source-read CodeMode (`code_mode/_toolset.py` + README): how tools are
    selected/wrapped, whether it works under `agent.iter()`, how a `run_code` call
    surfaces in `FunctionToolCallEvent` (our SSE + telemetry hooks), error shape when a
    script raises
  - [x] 🟩 Confirm the mechanism for marking tools SEQUENTIAL (non-overlapping) inside
    the sandbox, and the partial-failure semantics: what exactly does the model see on
    retry when a script raises AFTER a successful host-side tool call?
  - [x] 🟩 Evaluate the harness's built-in nested-call observability
    (`ToolReturnPart.metadata` tool_calls/tool_returns): what it already records for
    successful calls, so the Step 5 ledger only fills the gaps (durations, failures)
  - [x] 🟩 Minimal throwaway script (scratchpad, not repo): a 2-tool fake agent running
    write→verify inside one `run_code` turn — including one `asyncio.gather` attempt
    over both tools to observe the concurrency behavior first-hand
  - [x] 🟩 Write findings + **go/no-go** into this doc. No-go criteria: incompatible
    with `agent.iter()`, or requires replacing `run_agent_loop`, or can't restrict the
    tool subset, or can't enforce sequential execution of stateful tools (any of the
    four blows the "one seam" isolation decision or the write-safety invariants)
  - **Verify:** the throwaway script runs; findings section filled in; explicit GO
    recorded before Phase 2 starts.

  **Findings (2026-07-21) — GO.** Probed live in a scratch venv with a fake model
  (zero API cost), under `agent.iter()`:
  - **Install artifact:** `pydantic-ai-harness[code-mode]==0.8.0` (PyPI publishes both
    `code-mode` and `codemode` extra aliases; we pin the hyphenated one). Monty arrives
    as `pydantic-monty 0.0.18`. `pip check` clean alongside `pydantic-ai==2.9.0` AND
    with pydantic forced down to our constraints pin `2.12.5`. Requires Python ≥3.10
    (the stale system 3.9 can't even see the wheel — venv interpreter only).
  - **Integration shape:** CodeMode is a pydantic-ai V2 *capability* —
    `Agent(capabilities=[CodeMode(tools=[...])])` — the same list our agents already
    use for history processors. `tools=[...]` sandboxes exactly the named subset; the
    rest stay ordinary tool calls. Works under `agent.iter()` unmodified; `run_code`
    surfaces as a normal tool call (SSE + telemetry hooks see it like any tool).
  - **Sequential mechanism:** `@agent.tool(sequential=True)` exists on our pinned 2.9;
    the harness renders sequential tools as SYNC functions in the sandbox — a script
    cannot even attempt to `gather` them. Confirmed live: gather over async tools ran;
    the sequential write→verify→save chain executed strictly in order; the save-gate
    deps ordering held inside a single script.
  - **Partial-failure semantics:** a script that raised AFTER a successful sequential
    write left the host-side write in place (as expected) and the model's retry
    message contained `[stdout before error] write ok: ...` — the model is explicitly
    told what succeeded before the crash. Monty also TYPE-CHECKS scripts before
    execution (missing/positional args are rejected with line-pointer retries;
    sandbox functions are keyword-only — the Step 4 prompt addendum must say so).
  - **Observability:** the `run_code` ToolReturnPart carries
    `metadata={'code_mode': True, 'tool_calls': [...], 'tool_returns': [...]}` with
    per-call IDs — nested structure for SUCCESSFUL scripts is free; the Step 5 ledger
    adds durations + failure records, as planned.
  - **No-go criteria:** none triggered.

### Phase 2: The seam (toggle + wiring; flag off = byte-identical)

- [x] 🟩 **Step 3: Toggle + lazy import + agent-creation branch**
  - [x] 🟩 `XBRL_CODE_MODE` (default off) + `XBRL_CODE_MODE_STATEMENTS` (default
    `SOFP`), read at call time; one branch point in `create_extraction_agent` (or its
    caller in `coordinator.py`) — no second agent factory
  - [x] 🟩 Lazy import of the harness inside the branch; friendly startup error if the
    flag is on but the package missing
  - [x] 🟩 Pin test — run in a FRESH SUBPROCESS (a `sys.modules` check inside the suite
    is test-order-dependent once any flag-on test imports the harness): flag off ⇒
    `pydantic_ai_harness` absent from `sys.modules` after agent creation; flag on for a
    non-allowlisted statement ⇒ same
  - [x] 🟩 Factory-snapshot test: with the flag off, the built agent's instructions,
    registered tool names/schemas, capabilities list, and model settings are identical
    to today's factory output — asserted structurally, not "suite is green"
  - [x] 🟩 Both tests run in two environments: harness NOT installed, and harness
    installed but flag off (guards the lazy-import boundary from both sides)
  - **Verify:** full suite green with flag off; subprocess pin test + factory-snapshot
    test pass in both environments.
- [x] 🟩 **Step 4: Expose the text-only tool subset to CodeMode**
  - [x] 🟩 Sandbox-callable: calculator, lookup_definitions, search_pdf_text,
    write_facts, verify_totals, save_result (+ submit_face_coverage if registered);
    ordinary tools: view_pdf_pages, read_template
  - [x] 🟩 Prompt addendum (code-injected, gated on the flag — the
    `_MPERS_SOPL_REVENUE_NOTE` pattern, so `sofp.md` and its pinning tests are
    untouched): how to use `run_code`, incl. "write, then verify, then save only if
    balanced — in one script"
  - [x] 🟩 Declare `write_facts` / `verify_totals` / `save_result` sequential via the
    Step 2-confirmed mechanism
  - [x] 🟩 Unit test with a fake model: a scripted write→verify→save turn exercises the
    save gate (refuses when imbalanced, passes when balanced) — proving guard parity
  - [x] 🟩 Concurrency test: a script attempting `asyncio.gather(write_facts, verify_totals)`
    is serialized (or rejected) — never overlapped
  - [x] 🟩 Partial-failure test: script performs a successful `write_facts` then raises;
    the retry turn re-writes — assert no duplicate, corrupt, or late writes (workbook
    state + `run_concept_facts` both checked) and the save gate still demands a fresh
    verify after the surviving write
  - **Verify:** all unit tests green; flag-off suite still fully green.

### Phase 3: Debug ledger (the telemetry replacement)

- [x] 🟩 **Step 5: Host-side call ledger** — builds on the Step 2 findings: reuse the
  harness's own nested-call metadata where it exists; custom wrapping fills only the
  gaps (durations, failure records).
  - [x] 🟩 Wrapper on each sandbox-callable, recording in a `finally` so failed calls
    land: `{script_turn_index, run_code_call_id, seq, tool, args_summary (truncated),
    duration_ms, outcome (ok|error), error, result_summary (truncated)}` appended to
    `deps.code_mode_ledger` — keyed to the outer `run_code` call so retries are
    distinguishable from first attempts
  - [x] 🟩 Flush into the conversation trace JSON as `code_mode_calls` (grouped per
    script turn), alongside the existing `turns` block; include a truncated ledger echo
    in the `run_code` tool_result SSE `result_summary`
  - [x] 🟩 Test: ledger records N entries for a script calling N tools; a script that
    raises mid-way still ledgers the successful AND the failing call with outcomes;
    entries group correctly under their `run_code_call_id` across a retry; trace file
    carries them; `save_agent_trace` failure path still best-effort (gotcha #6 shape)
  - **Verify:** open a trace JSON from a test run with a deliberate mid-script failure
    and reconstruct the exact call sequence, including the failure — the debugging
    story demonstrably preserved for the case that matters (things going wrong).

### Phase 4: Live spike + decision

- [ ] 🟥 **Step 6: Interleaved A/B runs** — the decision data. Freeze the Success
  Criteria before the first flag-on run.
  - [ ] 🟥 6 SOFP-only runs in ONE sitting, alternating flag off/on/off/on/off/on —
    same PDF, same model — so prompt-cache warming and provider drift cannot favour
    either group (Step 1's runs remain an early reference, not decision data)
  - [ ] 🟥 Comparison table in this doc: per run — cost, wall time, model round trips
    (`node_kind='model_request'` count), outer `run_code` calls, nested sandbox calls
    (from the ledger) as separate columns
  - [ ] 🟥 Operator reviews all 6 workbooks blind to which group each came from where
    practicable — same acceptable/not judgement, same person, same Step 1 standard
  - [ ] 🟥 The debuggability bar: force one flag-on failure (e.g. a script error) and
    debug it from ledger + trace alone; record the experience
  - **Verify:** table filled; any rejected workbook investigated via ledger + trace
    before drawing conclusions.
- [ ] 🟥 **Step 7: Decision record** — apply the pre-registered Success Criteria to the
  Step 6 table; the criteria pick the outcome, not post-hoc judgement.
  - [ ] 🟥 Update PLAN-pydantic-ai-v2.md §D.5 item 3 with the outcome
  - [ ] 🟥 If kept: CLAUDE.md gotcha for the toggle + ledger invariants; candidate
    next target = face-reviewer investigation chains (best collapse ratio, all-text).
    If deleted: revert the seam commit(s)
  - **Verify:** decision written down with the numbers next to it.

## Rollback Plan

- **Instant:** unset `XBRL_CODE_MODE` (default is already off) — behavior returns to
  today's pipeline with zero code changes.
- **Full:** revert the Phase 2–3 commits (the seam is deliberately one branch point +
  one wrapper module); uninstall `pydantic-ai-harness`. No DB schema was touched, so
  nothing to migrate down.
- **State to check after rollback:** flag-off pin test green; full suite green;
  a fresh SOFP run's Telemetry tab shows normal per-tool turns again.
- **Keep regardless:** the Phase 0 baseline numbers (cost/time/turns + the operator's
  accuracy verdicts) — a reusable "before" reference for future changes.

## Out of Scope (explicitly)

- Windows/enterprise enablement (Mac dev box only)
- Any statement other than SOFP; notes agents; reviewer/formatter passes
- Settings UI / `/api/settings` exposure of the flag
- Telemetry-tab or trace-viewer UI changes
- Collapsing `view_pdf_pages` (vision turns are floor, not fat)
