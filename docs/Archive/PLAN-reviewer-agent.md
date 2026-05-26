# Implementation Plan: Reviewer Agent (Phase 1 — Read-Only Findings)

**Overall Progress:** `0%`
**Exploration Reference:** `/explore` session on 2026-04-11 (architecture mapping, answer loop).
**Last Updated:** 2026-04-11
**Methodology:** Red–Green TDD. Every implementation step is preceded by a failing test (🔴 Red), then the minimum code to make it pass (🟢 Green). No production code without a test that required it.

## Summary

Add a post-extraction **Reviewer Agent** that runs after all extraction agents and cross-checks complete. It is a **global coordinator** that spawns **read-only sub-agents** (spot-checker or investigator, depending on cross-check outcome) using an orchestrator-worker pattern modelled on Anthropic's Claude Code `Task` tool. Sub-agents have their own fresh context, use read-only tools to examine work already done, and return single-message summaries to the coordinator. The coordinator records findings and (in Phase 2, later) corrects cells.

The feature is **opt-in via a pre-run toggle**. When the toggle is off, the pipeline behaves exactly as today — no reviewer code is entered, no reviewer row is persisted, no reviewer tab is rendered.

## Key Decisions

- **Orchestrator-worker pattern, not a single big agent.** Claude Code's `Task` tool is the reference: sub-agents have fresh context, return a single text summary, their intermediate tool calls never enter the parent's context. This is the context-compression mechanism.
- **Sub-agents are strictly read-only.** Five tools: `read_extracted_fields`, `read_agent_trace`, `read_filled_workbook`, `read_template`, `view_pdf_pages`. No `fill_workbook`, no `record_finding`. Phase 2 writes happen only at the coordinator level.
- **Two sub-agent flavours, same tool set, different system prompts.** `spotcheck` (trace-driven, "did the agent make sensible choices?") vs `investigate` ("this cross-check failed, dig in").
- **Coordinator decides mode per statement.** Reads cross-check results, dispatches one or more sub-agents, records findings, emits a final summary. No hard rules — agentic judgement within the system prompt.
- **Simple finding shape.** `{ severity, statement, message }`. No cell pointers — there's no result-preview UI to navigate to today. Phase 2 can extend if corrections need pointers.
- **Reviewer is a 6th `run_agents` row** (`statement_type="REVIEWER"`). SSE events flow through the existing `agent_events` pipeline, so the live tab renders "for free" using existing `ToolCallCard` primitives. Sub-agent dispatches show up as regular `tool_call` events on the coordinator's timeline.
- **Sub-agents do not get their own `run_agents` rows.** They're ephemeral. Their token usage rolls up into the coordinator's totals via PydanticAI's `result.usage`.
- **Pre-run toggle, default OFF.** Feature flag preserves existing behaviour when disabled.
- **Pre-run model dropdown for reviewer** (default to a stronger model) **and for scout** (consistency — addressed as a small sibling feature in this plan).
- **One reviewer run per session.** No re-review button in Phase 1.
- **Synchronous placement.** Reviewer blocks `run_complete` until its work is done. Simpler DB transaction, simpler UI contract. Latency hit (~30–60s) is accepted.

## Architecture Overview (Phase 1)

```
                                 ┌─────────────────────────────┐
                                 │  server.py                  │
                                 │  run_multi_agent_stream()   │
                                 └──────────────┬──────────────┘
                                                │
            ┌───────────────────────────────────┼────────────────────────────────┐
            ▼                                   ▼                                ▼
   ┌────────────────┐                ┌─────────────────────┐           ┌───────────────────┐
   │ coordinator    │                │ cross_checks        │           │ reviewer          │
   │ (extraction    │  workbooks     │ framework.run_all() │  results  │ (NEW — if toggle) │
   │  fan-out)      ├───────────────▶│                     ├──────────▶│                   │
   └────────────────┘                └─────────────────────┘           └─────────┬─────────┘
                                                                                  │
                                                              ┌───────────────────┼───────────────────┐
                                                              ▼                   ▼                   ▼
                                                    ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────┐
                                                    │ dispatch_spot   │ │ dispatch_invest  │ │ record_finding   │
                                                    │ checker (tool)  │ │ igator  (tool)   │ │ (tool)           │
                                                    └────────┬────────┘ └────────┬─────────┘ └──────────────────┘
                                                             │                   │
                                                             ▼                   ▼
                                                    ┌─────────────────┐ ┌──────────────────┐
                                                    │ spotcheck       │ │ investigator     │
                                                    │ sub-agent       │ │ sub-agent        │
                                                    │ (fresh context, │ │ (fresh context,  │
                                                    │  read-only)     │ │  read-only)      │
                                                    └─────────────────┘ └──────────────────┘
                                                             │                   │
                                                             └─────── returns single text summary ───────▶
```

## Pre-Implementation Checklist

- [ ] 🟥 All `/explore` questions resolved (confirmed in chat 2026-04-11)
- [ ] 🟥 This plan reviewed and approved by user
- [ ] 🟥 Working on a new branch `reviewer-agent` (cut from current branch after current frontend work lands)
- [ ] 🟥 Backup of `output/xbrl_agent.db` taken before Phase 2 (schema migration)
- [ ] 🟥 Baseline tests green (Python + frontend)

---

## Phase 0: Safety Net

- [ ] 🟥 **Step 0.1: Baseline green tests.**
  - [ ] 🟥 `python -m pytest tests/ -v` — record pass count as "Baseline: N python".
  - [ ] 🟥 `cd web && npx vitest run` — record as "M frontend".
  - **Verify:** both suites pass; numbers recorded at top of this doc.

- [ ] 🟥 **Step 0.2: Back up audit DB.**
  - [ ] 🟥 `cp output/xbrl_agent.db output/xbrl_agent.db.pre-reviewer-backup`
  - **Verify:** backup exists with matching byte count.

---

## Phase 1: Data Types & Config Schema

*Goal: define the Finding dataclass, extend `RunConfigRequest` with toggle + model fields, and make both wire through `server.py` untouched of behaviour.*

- [ ] 🟥 **Step 1.1 (🔴 Red): Test `Finding` dataclass.**
  - [ ] 🟥 Add `tests/test_reviewer_types.py`.
  - [ ] 🟥 Assert `Finding(severity="warn", statement="SOFP", message="...")` constructs.
  - [ ] 🟥 Assert `severity` restricted to `{"info","warn","error"}` (pydantic validation).
  - [ ] 🟥 Assert `statement` optional (can be `None`).
  - **Verify:** fails — module does not exist.

- [ ] 🟥 **Step 1.2 (🟢 Green): Create `reviewer/types.py`.**
  - [ ] 🟥 New package `reviewer/` with `__init__.py`.
  - [ ] 🟥 `Finding` pydantic model with `severity: Literal["info","warn","error"]`, `statement: str | None`, `message: str`.
  - **Verify:** Step 1.1 passes.

- [ ] 🟥 **Step 1.3 (🔴 Red): Test `RunConfigRequest` accepts new fields.**
  - [ ] 🟥 Extend `tests/test_server_run_lifecycle.py` (or add new test file).
  - [ ] 🟥 POST `/api/run/{session_id}` body with `enable_reviewer=true`, `reviewer_model="claude-sonnet-4-6"`, `scout_model="gemini-3-flash-preview"` → 200.
  - [ ] 🟥 POST same body without any of those fields → 200 (backward compatible).
  - [ ] 🟥 Assert `run_config_json` row persisted in DB contains the new fields when provided.
  - **Verify:** fails — `RunConfigRequest` rejects unknown fields.

- [ ] 🟥 **Step 1.4 (🟢 Green): Extend `RunConfigRequest` in `server.py`.**
  - [ ] 🟥 Add optional `enable_reviewer: bool = False`, `reviewer_model: str | None = None`, `scout_model: str | None = None`.
  - [ ] 🟥 Pass through into `RunConfig` in `coordinator.py` (new fields, default `None`/`False`).
  - [ ] 🟥 `run_config_json` serialises all three.
  - **Verify:** Step 1.3 passes. Existing lifecycle tests still pass.

- [ ] 🟥 **Step 1.5 (🔴 Red): Test reviewer disabled preserves current flow.**
  - [ ] 🟥 Add `tests/test_reviewer_disabled_preserves_flow.py`.
  - [ ] 🟥 Mock full extraction + cross-checks. POST with `enable_reviewer=false` (or omitted).
  - [ ] 🟥 Assert: no `run_agents` row with `statement_type="REVIEWER"`, no `run_reviewer_findings` entries, `agent_events` contains only extraction+scout events, SSE stream has no `agent_id="reviewer"` events.
  - **Verify:** passes trivially now (reviewer doesn't exist yet) — this test is a **regression guard** locked in early. It should remain green across ALL subsequent phases.

---

## Phase 2: Database Schema & Repository

*Goal: one new table for findings; repository helpers to insert and fetch them. Reuse `run_agents`/`agent_events` for everything else.*

- [ ] 🟥 **Step 2.1 (🔴 Red): Test schema migration creates `run_reviewer_findings`.**
  - [ ] 🟥 Add to `tests/test_db_schema_v2.py` (or new `test_db_schema_v3.py`).
  - [ ] 🟥 Open fresh in-memory SQLite via `db.schema.initialize()`.
  - [ ] 🟥 Assert table exists with columns `id, run_id, severity, statement, message, created_at`.
  - [ ] 🟥 Assert `run_id` has FK to `runs(id)` with `ON DELETE CASCADE`.
  - [ ] 🟥 Assert old schema (without the table) migrates cleanly via existing `_migrate()` path.
  - **Verify:** fails — table not defined.

- [ ] 🟥 **Step 2.2 (🟢 Green): Add `run_reviewer_findings` table to `db/schema.py`.**
  - [ ] 🟥 Schema version bump; migration SQL for existing DBs.
  - [ ] 🟥 Indexes: `(run_id)`.
  - **Verify:** Step 2.1 passes.

- [ ] 🟥 **Step 2.3 (🔴 Red): Repository insert test.**
  - [ ] 🟥 Add to `tests/test_db_repository.py`.
  - [ ] 🟥 `insert_reviewer_finding(conn, run_id, Finding(...))` → returns new id, row visible via raw SQL.
  - [ ] 🟥 `fetch_reviewer_findings(conn, run_id)` → returns list in insertion order.
  - **Verify:** fails — functions not defined.

- [ ] 🟥 **Step 2.4 (🟢 Green): Add `insert_reviewer_finding` + `fetch_reviewer_findings` to `db/repository.py`.**
  - **Verify:** Step 2.3 passes.

- [ ] 🟥 **Step 2.5 (🔴 Red): History API includes findings.**
  - [ ] 🟥 Extend `tests/test_history_api.py`.
  - [ ] 🟥 GET `/api/history/{run_id}` for a run with seeded findings → JSON response includes `reviewer_findings: [...]` with the right shape.
  - [ ] 🟥 Run with no findings → `reviewer_findings: []`.
  - **Verify:** fails — API doesn't return the field.

- [ ] 🟥 **Step 2.6 (🟢 Green): Extend `get_run_detail` handler.**
  - [ ] 🟥 Join `fetch_reviewer_findings` into the response.
  - **Verify:** Step 2.5 passes.

---

## Phase 3: Read-Only Sub-Agent Tools

*Goal: five read-only tools the sub-agents will use. Each is a pure function factory that takes `ReviewerSubDeps` (run_id, output_dir, pdf_path, db connection factory). No coupling to extraction deps.*

- [ ] 🟥 **Step 3.1 (🔴 Red): Test `read_extracted_fields(statement?)`.**
  - [ ] 🟥 Add `tests/test_reviewer_tools.py`.
  - [ ] 🟥 Seed `extracted_fields` with rows for SOFP + SOPL across two agents.
  - [ ] 🟥 `read_extracted_fields()` → returns all rows, each with `{statement, sheet, field_label, section, value, evidence}`.
  - [ ] 🟥 `read_extracted_fields(statement="SOFP")` → only SOFP rows.
  - [ ] 🟥 Empty DB → empty list.
  - **Verify:** fails.

- [ ] 🟥 **Step 3.2 (🟢 Green): Implement `reviewer/tools/read_extracted_fields.py`.**
  - [ ] 🟥 Thin wrapper around `db.repository.fetch_fields(run_id)` with statement filter.
  - **Verify:** Step 3.1 passes.

- [ ] 🟥 **Step 3.3 (🔴 Red): Test `read_agent_trace(statement)`.**
  - [ ] 🟥 Create fake `output/{sid}/SOFP_conversation_trace.json`.
  - [ ] 🟥 `read_agent_trace("SOFP")` → returns trace dict or terse text rendering.
  - [ ] 🟥 Missing file → raises with clear message.
  - [ ] 🟥 Decide: return raw JSON (LLM parses) or a structured summary (tool does the work)? **Return a structured summary**: list of `{role, tool_name?, args?, result_summary?, text?}` entries, stripped of image data and full tool result blobs. Assert the structure.
  - **Verify:** fails.

- [ ] 🟥 **Step 3.4 (🟢 Green): Implement `reviewer/tools/read_agent_trace.py`.**
  - [ ] 🟥 Read `{stmt}_conversation_trace.json`.
  - [ ] 🟥 Project each message into a terse shape. Preserve tool-call args and `result_summary` text, drop raw tool-result bodies over N chars.
  - [ ] 🟥 Ensure return is stable, deterministic, and small (target < 20 KB per statement).
  - **Verify:** Step 3.3 passes.

- [ ] 🟥 **Step 3.5 (🔴 Red): Test `read_filled_workbook(statement, sheet?)`.**
  - [ ] 🟥 Open a real test workbook (reuse any existing fixture).
  - [ ] 🟥 Tool returns list of `{sheet, coordinate, row, col, label_guess, value}` for non-empty, non-formula cells.
  - [ ] 🟥 `sheet=` filter restricts output to that sheet.
  - [ ] 🟥 Workbook path resolved from `run_agents.workbook_path` for that statement.
  - **Verify:** fails.

- [ ] 🟥 **Step 3.6 (🟢 Green): Implement `reviewer/tools/read_filled_workbook.py`.**
  - [ ] 🟥 Use openpyxl, iterate cells, emit only data-entry cells with values.
  - **Verify:** Step 3.5 passes.

- [ ] 🟥 **Step 3.7 (🟢 Green): Shim `read_template` and `view_pdf_pages` into reviewer namespace.**
  - [ ] 🟥 Re-export the existing tools from `tools/template_reader.py` and `tools/pdf_viewer.py`, bound to `ReviewerSubDeps` via tiny adapters.
  - [ ] 🟥 No new tests — the underlying functions are already covered. Just assert the adapters construct.

- [ ] 🟥 **Step 3.8 (🔴 Red): Test `read_cross_check_results()` tool.**
  - [ ] 🟥 Seed `cross_checks` table for a run.
  - [ ] 🟥 Tool returns list of `{name, status, expected, actual, diff, message}`.
  - **Verify:** fails.

- [ ] 🟥 **Step 3.9 (🟢 Green): Implement `reviewer/tools/read_cross_check_results.py`.**
  - **Verify:** Step 3.8 passes.

---

## Phase 4: Sub-Agent Factories

*Goal: two PydanticAI agents (spotcheck, investigate) sharing the same tool set but differing in system prompt. Each returns a single string summary to its caller.*

- [ ] 🟥 **Step 4.1 (🔴 Red): Test `create_spotcheck_agent()` shape.**
  - [ ] 🟥 Add to `tests/test_reviewer_subagents.py`.
  - [ ] 🟥 Returns `(Agent, ReviewerSubDeps)`.
  - [ ] 🟥 Agent has exactly: `read_extracted_fields`, `read_agent_trace`, `read_filled_workbook`, `read_template`, `view_pdf_pages`, `read_cross_check_results`.
  - [ ] 🟥 Agent has NO `fill_workbook`, NO `record_finding`, NO `dispatch_*`.
  - [ ] 🟥 System prompt contains the phrase "spot-check" / "trace-driven" / "sensible choices".
  - **Verify:** fails.

- [ ] 🟥 **Step 4.2 (🟢 Green): Create `reviewer/subagents.py` with `create_spotcheck_agent`.**
  - [ ] 🟥 Use same `_create_proxy_model` helper.
  - [ ] 🟥 System prompt in `prompts/reviewer_spotcheck.md`: "You examine extraction agent traces to judge whether tool-call choices were sensible. Return a terse finding summary."
  - [ ] 🟥 Output type: `str`.
  - **Verify:** Step 4.1 passes.

- [ ] 🟥 **Step 4.3 (🔴 Red): Test `create_investigator_agent()` shape.**
  - [ ] 🟥 Same shape assertions as 4.1.
  - [ ] 🟥 System prompt contains "investigate" / "cross-check failure".
  - **Verify:** fails.

- [ ] 🟥 **Step 4.4 (🟢 Green): Create `create_investigator_agent` alongside spotcheck.**
  - [ ] 🟥 System prompt in `prompts/reviewer_investigate.md`: "A cross-check failed. Agentically investigate — read the trace, inspect values, view PDF pages as needed. Return a terse finding."
  - **Verify:** Step 4.3 passes.

- [ ] 🟥 **Step 4.5 (🔴 Red): Test sub-agent end-to-end with `TestModel`.**
  - [ ] 🟥 Use pydantic-ai's `TestModel` to run a spotcheck sub-agent against a seeded DB + on-disk fake trace.
  - [ ] 🟥 Assert the agent completes within a small iteration cap and returns a non-empty string.
  - [ ] 🟥 Assert at least one tool call was made (TestModel records them).
  - **Verify:** fails (TestModel not plumbed).

- [ ] 🟥 **Step 4.6 (🟢 Green): Make factories accept an optional `model_override` for tests.**
  - **Verify:** Step 4.5 passes.

---

## Phase 5: Coordinator Agent

*Goal: the top-level reviewer agent. Has dispatch tools that instantiate and run sub-agents, a `record_finding` tool, and read-only peek tools for quick decisions. Emits SSE events via the same `event_callback` pattern used by extraction agents.*

- [ ] 🟥 **Step 5.1 (🔴 Red): Test `create_reviewer_coordinator()` shape.**
  - [ ] 🟥 Add to `tests/test_reviewer_coordinator.py`.
  - [ ] 🟥 Returns `(Agent, ReviewerCoordinatorDeps)`.
  - [ ] 🟥 Tools: `read_cross_check_results`, `dispatch_spotchecker`, `dispatch_investigator`, `record_finding`, `summarise_findings`.
  - [ ] 🟥 System prompt contains "spot-check each passing statement, investigate each failing cross-check, record findings".
  - **Verify:** fails.

- [ ] 🟥 **Step 5.2 (🟢 Green): Create `reviewer/coordinator.py`.**
  - [ ] 🟥 `ReviewerCoordinatorDeps` carries run_id, output_dir, pdf_path, db factory, findings list (mutable), event emitter, sub-agent model name.
  - [ ] 🟥 System prompt in `prompts/reviewer_coordinator.md`.
  - [ ] 🟥 Tools registered as stubs that raise `NotImplementedError` — wired in next steps.
  - **Verify:** Step 5.1 passes (shape only; tool calls would fail, but we don't call them here).

- [ ] 🟥 **Step 5.3 (🔴 Red): Test `record_finding` tool appends to deps.**
  - [ ] 🟥 Call the tool directly with `severity="warn"`, `statement="SOFP"`, `message="..."`.
  - [ ] 🟥 Assert `deps.findings` grows by one entry with matching fields.
  - [ ] 🟥 Assert an event is emitted through `deps.event_emitter` with type `finding_recorded`.
  - **Verify:** fails — stub raises.

- [ ] 🟥 **Step 5.4 (🟢 Green): Implement `record_finding`.**
  - [ ] 🟥 Append `Finding(...)` to deps, emit event.
  - **Verify:** Step 5.3 passes.

- [ ] 🟥 **Step 5.5 (🔴 Red): Test `dispatch_spotchecker` creates fresh sub-agent and returns string.**
  - [ ] 🟥 Mock `create_spotcheck_agent` to return a stub agent whose `.run()` returns `"finding: ok"`.
  - [ ] 🟥 Call `dispatch_spotchecker(statement="SOFP", task="verify trace")`.
  - [ ] 🟥 Assert: sub-agent constructed with fresh `ReviewerSubDeps`, `.run()` awaited with `task` as user prompt, returned string is exactly the sub-agent's `.output`.
  - [ ] 🟥 Assert: a `tool_call` SSE event with `tool_name="dispatch_spotchecker"` was emitted with args and the sub-agent's result summary.
  - **Verify:** fails — stub raises.

- [ ] 🟥 **Step 5.6 (🟢 Green): Implement `dispatch_spotchecker`.**
  - [ ] 🟥 Build `ReviewerSubDeps` from coordinator deps (inherit run_id, output_dir, model).
  - [ ] 🟥 `agent, deps = create_spotcheck_agent(...)`.
  - [ ] 🟥 `result = await agent.run(task, deps=deps, usage_limits=...)`.
  - [ ] 🟥 Roll up `result.usage` into coordinator usage tracker.
  - [ ] 🟥 Emit tool events before and after.
  - [ ] 🟥 Return `result.output` (a string) to the coordinator's tool-call resolution.
  - **Verify:** Step 5.5 passes.

- [ ] 🟥 **Step 5.7 (🔴 Red → 🟢 Green): Same pair for `dispatch_investigator`.**
  - [ ] 🟥 Mirror steps 5.5 + 5.6.

- [ ] 🟥 **Step 5.8 (🔴 Red): Test `summarise_findings` tool.**
  - [ ] 🟥 Seed deps with three findings, call the tool.
  - [ ] 🟥 Returns a short string "3 findings: 1 error, 1 warn, 1 info".
  - **Verify:** fails.

- [ ] 🟥 **Step 5.9 (🟢 Green): Implement `summarise_findings`.**
  - **Verify:** Step 5.8 passes.

- [ ] 🟥 **Step 5.10 (🔴 Red): End-to-end coordinator test with TestModel.**
  - [ ] 🟥 Use `FunctionModel` to script a coordinator run:
        1. call `read_cross_check_results` → receives one failed SOCF check
        2. call `dispatch_investigator("SOCF", "...")` → returns "missing cash reconciliation line"
        3. call `record_finding(severity="error", statement="SOCF", message="...")`
        4. call `summarise_findings` → "1 error"
        5. final message summarises the review
  - [ ] 🟥 Assert `deps.findings` has exactly one entry with severity="error".
  - [ ] 🟥 Assert the full SSE event stream was captured (tool_call events for all four calls plus the sub-agent dispatch).
  - **Verify:** fails initially; passes once all prior steps green.

---

## Phase 6: Pipeline Integration (`server.py` + `coordinator.py`)

*Goal: wire the reviewer into `run_multi_agent_stream` after cross-checks complete. Gated by `enable_reviewer`. Creates a `run_agents` row for the reviewer, streams its events, persists findings.*

- [ ] 🟥 **Step 6.1 (🔴 Red): Test reviewer only runs when toggle is on.**
  - [ ] 🟥 Extend `tests/test_reviewer_disabled_preserves_flow.py` with the positive case: same mock pipeline, `enable_reviewer=true`, reviewer coordinator mocked to append one finding.
  - [ ] 🟥 Assert: a `run_agents` row with `statement_type="REVIEWER"` exists, `run_reviewer_findings` has one row, `agent_events` has events with `run_agent_id` of the reviewer row.
  - [ ] 🟥 Assert SSE stream contains `agent_id="reviewer"` events.
  - **Verify:** fails — reviewer never runs.

- [ ] 🟥 **Step 6.2 (🟢 Green): Add `run_reviewer()` helper in `reviewer/runner.py`.**
  - [ ] 🟥 Signature: `async def run_reviewer(run_id, run_config, cross_check_results, workbook_paths, output_dir, pdf_path, event_callback) -> ReviewerResult`.
  - [ ] 🟥 `ReviewerResult(findings: list[Finding], summary: str, usage: Usage)`.
  - [ ] 🟥 Constructs coordinator deps, runs coordinator agent with a kick-off prompt that embeds cross-check summary.
  - [ ] 🟥 Emits lifecycle events: `reviewer_started`, tool events from the agent, `reviewer_complete`.
  - **Verify:** unit test for this helper in isolation passes.

- [ ] 🟥 **Step 6.3 (🟢 Green): Hook into `server.py:run_multi_agent_stream`.**
  - [ ] 🟥 Placement: after cross-checks complete (current ~line 823), before DB persist block (~line 825).
  - [ ] 🟥 Gate: `if run_config.enable_reviewer:`.
  - [ ] 🟥 Create `run_agents` row with `statement_type="REVIEWER"`, `model=run_config.reviewer_model`.
  - [ ] 🟥 Call `run_reviewer(...)` with an `event_callback` that pushes into the existing SSE event queue (same pattern as extraction agents).
  - [ ] 🟥 On completion: update row status, persist `agent_events`, persist findings via `insert_reviewer_finding`.
  - [ ] 🟥 On exception: row status `failed`, emit `reviewer_error` event, DO NOT fail the whole run (reviewer failure must not mask extraction success).
  - **Verify:** Step 6.1 passes.

- [ ] 🟥 **Step 6.4 (🔴 Red): Reviewer failure does not break run status.**
  - [ ] 🟥 Mock `run_reviewer` to raise.
  - [ ] 🟥 Assert run final status is `success` (extraction + cross-checks still green), reviewer row is `failed`, one `reviewer_error` SSE event.
  - **Verify:** fails — current code might bubble.

- [ ] 🟥 **Step 6.5 (🟢 Green): Wrap `run_reviewer` call in try/except inside `run_multi_agent_stream`.**
  - **Verify:** Step 6.4 passes.

- [ ] 🟥 **Step 6.6 (🟢 Green): Regression guard — Step 1.5 still green.**
  - [ ] 🟥 Re-run the `test_reviewer_disabled_preserves_flow.py` disabled case. Must still be green.

---

## Phase 7: Frontend Types & State

*Goal: new types, reducer routing, and an `AgentState` slot for the reviewer. Nothing visible yet.*

- [ ] 🟥 **Step 7.1 (🔴 Red): Test `ReviewerFinding` + `ReviewerState` types compile + reducer routing.**
  - [ ] 🟥 Add `web/src/__tests__/reviewerReducer.test.ts`.
  - [ ] 🟥 Dispatch a `reviewer_started` SSE event → state has `reviewer.status="running"`.
  - [ ] 🟥 Dispatch a `finding_recorded` event → `reviewer.findings` grows by one.
  - [ ] 🟥 Dispatch `reviewer_complete` with summary → `status="complete"`, summary persisted.
  - [ ] 🟥 Dispatch `reviewer_error` → `status="failed"`, error message set.
  - **Verify:** fails — types don't exist.

- [ ] 🟥 **Step 7.2 (🟢 Green): Extend `web/src/lib/types.ts`.**
  - [ ] 🟥 `ReviewerFinding { severity: "info"|"warn"|"error"; statement: string | null; message: string }`.
  - [ ] 🟥 `ReviewerState { status: "idle"|"running"|"complete"|"failed"; findings: ReviewerFinding[]; summary: string | null; error: string | null; events: SSEEvent[] }`.
  - [ ] 🟥 Extend `AppState` with `reviewer: ReviewerState`.
  - **Verify:** compiles.

- [ ] 🟥 **Step 7.3 (🟢 Green): Extend `appReducer` with `reviewer_*` event handling.**
  - [ ] 🟥 Branch on `event.agent_id === "reviewer"` before the per-statement branch.
  - [ ] 🟥 Append events to `reviewer.events`.
  - [ ] 🟥 Handle `finding_recorded`, `reviewer_started`, `reviewer_complete`, `reviewer_error`.
  - **Verify:** Step 7.1 passes.

- [ ] 🟥 **Step 7.4 (🟢 Green): `api.ts` types for `reviewer_findings` from history endpoint.**
  - [ ] 🟥 Extend `HistoryRunDetail` type with `reviewer_findings: ReviewerFinding[]`.
  - [ ] 🟥 Update `api.test.ts` fixtures.

---

## Phase 8: Frontend Pre-Run Toggle + Model Dropdowns

*Goal: the only user-visible opt-in. Default OFF. Scout model dropdown comes along as a sibling change.*

- [ ] 🟥 **Step 8.1 (🔴 Red): Test PreRunPanel renders reviewer toggle.**
  - [ ] 🟥 Add `web/src/__tests__/PreRunPanel.test.tsx` (or extend existing).
  - [ ] 🟥 Assert a labelled toggle "Enable reviewer agent" exists, default unchecked.
  - [ ] 🟥 Click → toggle becomes checked, `onConfigChange` called with `enable_reviewer=true`.
  - [ ] 🟥 When unchecked, reviewer model dropdown is hidden.
  - [ ] 🟥 When checked, reviewer model dropdown appears with default value (a stronger model — e.g., `claude-sonnet-4-6`).
  - **Verify:** fails.

- [ ] 🟥 **Step 8.2 (🟢 Green): Add reviewer toggle + model dropdown to PreRunPanel.**
  - [ ] 🟥 Default `reviewer_model` read from `config/models.json` as the first entry tagged `reviewer_default: true`, else fall back to a hardcoded stronger model.
  - [ ] 🟥 Wire both into `RunConfigRequest` body.
  - **Verify:** Step 8.1 passes.

- [ ] 🟥 **Step 8.3 (🔴 Red): Test scout model dropdown.**
  - [ ] 🟥 Scout toggle (existing) on → scout model dropdown renders with selectable options.
  - [ ] 🟥 Selecting a model calls `onConfigChange` with `scout_model=...`.
  - [ ] 🟥 Scout toggle off → scout model dropdown hidden.
  - **Verify:** fails.

- [ ] 🟥 **Step 8.4 (🟢 Green): Add scout model dropdown to PreRunPanel.**
  - [ ] 🟥 Reuse the same model-dropdown primitive as the reviewer.
  - [ ] 🟥 Default read from the existing `SCOUT_MODEL` env path via `/api/settings`.
  - **Verify:** Step 8.3 passes.

- [ ] 🟥 **Step 8.5 (🟢 Green): Extend `POST /api/run` body construction in `api.ts`.**
  - [ ] 🟥 Include `enable_reviewer`, `reviewer_model`, `scout_model` when set.
  - [ ] 🟥 Omit when null/false to keep bodies minimal.

---

## Phase 9: Frontend Reviewer Tab

*Goal: new tab in `AgentTabs` that appears only when reviewer was part of the run. Renders a live timeline (same `ToolCallCard` primitive used elsewhere) plus a findings list.*

- [ ] 🟥 **Step 9.1 (🔴 Red): Test AgentTabs shows reviewer tab when enabled.**
  - [ ] 🟥 Extend `web/src/__tests__/AgentTabs.test.tsx`.
  - [ ] 🟥 State with `enable_reviewer=true` → tab labelled "Reviewer" appears after the 5 statement tabs + Validator tab.
  - [ ] 🟥 State with `enable_reviewer=false` → no such tab.
  - [ ] 🟥 Tab badge mirrors reviewer status (running spinner, complete check, failed red).
  - **Verify:** fails.

- [ ] 🟥 **Step 9.2 (🟢 Green): Extend `AgentTabs` conditional rendering.**
  - [ ] 🟥 Read `state.reviewer.status` + `state.runConfig.enable_reviewer`.
  - **Verify:** Step 9.1 passes.

- [ ] 🟥 **Step 9.3 (🔴 Red): Test `ReviewerTab` renders timeline + findings.**
  - [ ] 🟥 Add `web/src/__tests__/ReviewerTab.test.tsx`.
  - [ ] 🟥 Pass props with 3 tool events + 2 findings + a summary string.
  - [ ] 🟥 Assert 3 `data-testid="tool-card"` rendered (timeline).
  - [ ] 🟥 Assert findings table has 2 rows, each with severity badge + statement + message.
  - [ ] 🟥 Assert summary text rendered in a header.
  - [ ] 🟥 Empty props → "Reviewer has not run yet" message.
  - **Verify:** fails — component does not exist.

- [ ] 🟥 **Step 9.4 (🟢 Green): Create `web/src/components/ReviewerTab.tsx`.**
  - [ ] 🟥 Reuse the tool timeline reducer from `buildToolTimeline`.
  - [ ] 🟥 Reuse `ToolCallCard` for rows.
  - [ ] 🟥 Findings rendered as a simple table: severity badge | statement | message.
  - **Verify:** Step 9.3 passes.

- [ ] 🟥 **Step 9.5 (🟢 Green): Wire ReviewerTab into `App.tsx` tab switch.**
  - [ ] 🟥 When active tab is "reviewer", render `ReviewerTab` with the reviewer slice of state.
  - **Verify:** existing App tests still pass; visual smoke test.

- [ ] 🟥 **Step 9.6 (🔴 Red): RunDetailView shows reviewer findings for historical runs.**
  - [ ] 🟥 Extend `web/src/__tests__/RunDetailView.test.tsx`.
  - [ ] 🟥 Fixture with `reviewer_findings` populated → history view renders them.
  - [ ] 🟥 Empty array → "No reviewer findings" message.
  - **Verify:** fails.

- [ ] 🟥 **Step 9.7 (🟢 Green): Render findings in RunDetailView.**
  - [ ] 🟥 Reuse the same findings table component from ReviewerTab (extract to a small shared component `ReviewerFindingsTable.tsx`).
  - **Verify:** Step 9.6 passes.

---

## Phase 10: Live E2E Smoke (Marked `@pytest.mark.live`)

*Goal: one real-model end-to-end run with reviewer enabled, gated behind the existing `live` marker so CI skips it.*

- [ ] 🟥 **Step 10.1 (🟢 Green): Add `tests/test_reviewer_live.py`.**
  - [ ] 🟥 `@pytest.mark.live` — uses real TEST_MODEL + real reviewer model.
  - [ ] 🟥 Runs a full extraction on a fixture PDF with `enable_reviewer=True`.
  - [ ] 🟥 Asserts: reviewer row exists, at least the reviewer completed (findings may be zero), no exceptions.
  - [ ] 🟥 Runs in < 3 minutes or fails.
  - **Verify:** manually run with `python -m pytest -m live -v tests/test_reviewer_live.py`.

- [ ] 🟥 **Step 10.2 (🟢 Green): Document live run in `CLAUDE.md`.**
  - [ ] 🟥 Add to the "Testing" section how to invoke the reviewer live test.

---

## Phase 11: Cleanup & Docs

- [ ] 🟥 **Step 11.1 (🟢 Green): Update `CLAUDE.md`.**
  - [ ] 🟥 New "Reviewer Agent" section: what it does, when it runs, toggle path, model selection, sub-agent pattern.
  - [ ] 🟥 Add `reviewer/` to the Architecture tree.
  - [ ] 🟥 Add "Files that must stay in sync" row for the reviewer.

- [ ] 🟥 **Step 11.2 (🟢 Green): Baseline regressions.**
  - [ ] 🟥 Full `pytest tests/ -v` — no regressions.
  - [ ] 🟥 Full `cd web && npx vitest run` — no regressions.
  - [ ] 🟥 Record "After Phase 11: N python / M frontend".

- [ ] 🟥 **Step 11.3 (🟢 Green): Manual smoke.**
  - [ ] 🟥 Run `./start.sh`, upload a real PDF, toggle reviewer ON.
  - [ ] 🟥 Verify live tab updates with coordinator tool calls + sub-agent dispatches.
  - [ ] 🟥 Verify findings table populates after the run completes.
  - [ ] 🟥 Toggle OFF — verify no Reviewer tab, no slowdown.

---

## Phase 2 (Deferred) — Reviewer Correction Mode

Not implemented in this plan. Recorded here so Phase 1 decisions align.

- Coordinator gains `fix_cell(statement, sheet, row, col, value, evidence)` tool.
- After writing, coordinator re-runs `workbook_merger.merge()` and cross-checks.
- If a correction breaks another check, coordinator rolls back (Phase 1 persistence scheme supports this — `run_reviewer_corrections` table with before/after values).
- Sub-agents remain strictly read-only forever.
- UI: findings table grows a "Fix proposed" / "Fix applied" column; user can accept/reject before merge persists.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Reviewer latency stretches perceived runtime 30–60s | Toggle is opt-in; UI shows clear "Reviewer running…" status so users understand the delay |
| Sub-agent dispatch causes recursive context explosion | Fresh context per sub-agent (not inherited); `usage_limits` on sub-agent `.run()` caps iteration count and tokens |
| Conversation trace on disk may be stale/missing | `read_agent_trace` raises with clear error; coordinator treats as soft failure and records an `info` finding |
| Reviewer failure masks extraction success | `run_reviewer` wrapped in try/except at server.py; final run status never depends on reviewer outcome |
| Model selection UI duplicates scout/reviewer rendering | Extract `ModelDropdown` primitive in Phase 8 and reuse |
| Findings table grows unbounded on repeated runs (future) | Phase 1 is one reviewer run per session; no accumulation |
| `TestModel` / `FunctionModel` API drift in pydantic-ai upgrades | Tests pin pydantic-ai version (>=1.77); bumping requires re-running the reviewer test suite |
| Sub-agent prompt injection via PDF content | Sub-agents only call read-only tools; worst case is a bad finding, not a bad write |

## Files Created

```
reviewer/
  __init__.py
  types.py                    # Finding model
  coordinator.py              # create_reviewer_coordinator + tools
  subagents.py                # create_spotcheck_agent, create_investigator_agent
  runner.py                   # run_reviewer() — pipeline entry point
  tools/
    __init__.py
    read_extracted_fields.py
    read_agent_trace.py
    read_filled_workbook.py
    read_cross_check_results.py
prompts/
  reviewer_coordinator.md
  reviewer_spotcheck.md
  reviewer_investigate.md
tests/
  test_reviewer_types.py
  test_reviewer_tools.py
  test_reviewer_subagents.py
  test_reviewer_coordinator.py
  test_reviewer_disabled_preserves_flow.py
  test_reviewer_live.py       # @pytest.mark.live
web/src/
  components/
    ReviewerTab.tsx
    ReviewerFindingsTable.tsx
  __tests__/
    reviewerReducer.test.ts
    ReviewerTab.test.tsx
    PreRunPanel.test.tsx      # may extend existing
```

## Files Modified

```
db/schema.py                  # run_reviewer_findings table + migration
db/repository.py              # insert_reviewer_finding, fetch_reviewer_findings
server.py                     # RunConfigRequest fields, run_multi_agent_stream hook
coordinator.py                # RunConfig fields
config/models.json            # optional reviewer_default flag
web/src/lib/types.ts          # ReviewerFinding, ReviewerState
web/src/App.tsx               # appReducer routing, tab wiring
web/src/lib/api.ts            # RunConfigRequest body, HistoryRunDetail
web/src/components/AgentTabs.tsx       # reviewer tab conditional
web/src/components/PreRunPanel.tsx     # toggle + model dropdowns
web/src/components/RunDetailView.tsx   # findings in history
CLAUDE.md                     # docs
```
