# Implementation Plan: Unified Run Page + Deep Telemetry

**Overall Progress:** `23%` (Phase 2 complete; Phases 1, 3–6 remaining)
**Design reference:** [docs/pwc-design-system.html](pwc-design-system.html) · memory `pwc-design-system`
**Last Updated:** 2026-05-27

## Summary

Collapse the two disjointed post-run surfaces — History run-detail
([RunDetailView.tsx](../web/src/components/RunDetailView.tsx)) and the canonical
Review workspace ([ConceptsPage.tsx](../web/src/pages/ConceptsPage.tsx)) — into
**one tabbed run page** with a single shared header, killing the navigation
seam. In parallel, persist the per-turn / per-tool telemetry that
[token_tracker.py](../token_tracker.py) already computes in memory but currently
throws away, so the page can show real depth (cost, timing, tool usage) instead
of a single floated token total. Restyle the page to the PwC design primitives
along the way.

Two largely independent tracks:

- **Track A (UI):** unified run-page shell → restyle → merge Values/Cross-checks
  tabs → telemetry UI.
- **Track B (data):** schema bump → flush the `TokenReport` ledger → expose via
  API.

Track B has **no dependency** on Track A and can be built concurrently; they
converge at Phase 4.

## Key Decisions

- **Merge, don't relink** (user choice): run detail and value-review become tabs
  of one page, not two pages with breadcrumbs. Verdict from brainstorm.
- **Default tab emphasis = Audit → Debug → Cost** (user said "all three,
  ranked"; I picked the order): landing tab is **Overview** (status, config,
  key actions, glanceable metric strip); **Agents** is the debug drill-down;
  **Telemetry** is the cost/perf deep-dive.
- **Persist the full per-turn ledger now** (user choice "persist everything"),
  but label token splits **approximate**. Honest ceiling from CLAUDE.md gotcha
  #6: pydantic-ai 1.77 counts tokens internally; per-turn prompt/completion
  splits are estimates and thinking-token separation is not reliably available.
  Per-turn **timing + tool calls** *are* exact (already in `agent_events`).
- **Keep `/history/<id>` as the canonical run-page URL.** `/concepts/<id>`
  becomes an alias that opens the run page on the **Values** tab. Routing stays
  on the existing reducer-based router ([appReducer.ts](../web/src/lib/appReducer.ts)),
  not React Router.
- **Values tab stays `canonicalEnabled`-gated** (CLAUDE.md gotcha #21 — the
  concept model is still uncommitted WIP). The merge must not *depend* on
  canonical mode being on; the tab is simply hidden when the flag is off.
- **`theme.ts` is the single styling cascade point.** Reconcile Concepts'
  `uiStyles.ts` usage toward the same `pwc` tokens rather than introducing a
  third system (gotcha #7 — inline styles only, no Tailwind).
- **Schema next version = v8** (committed schema is already at v7, not v3 as
  CLAUDE.md gotcha #11 still says — that note is stale). New telemetry tables
  follow the existing version-stepped, idempotent, nullable migration pattern.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm tab set + order: **Overview · Agents · Values · Cross-checks · Telemetry**
- [ ] 🟥 Confirm `/concepts/<id>` → `/history/<id>` (Values tab) alias is acceptable (no external bookmarks to break)
- [ ] 🟥 Confirm the stale CLAUDE.md gotcha #11 (says v3; real is v7) gets corrected in the same change set
- [ ] 🟥 No conflicting in-progress work — note: `web/src/App.tsx`, `HistoryList.tsx`, `api.ts`, `ExtractPage.tsx` already have uncommitted edits + untracked `HomeHero/StatTiles/RecentRunsList` (homepage redesign in flight). Coordinate so the run-page work doesn't clash with the homepage work.

## Tasks

### Phase 1: Unified run-page shell (Track A foundation)

- [ ] 🟥 **Step 1: Introduce a tabbed `RunPage` container** — wrap the existing
  detail body in a page that owns a `tab` state and renders one shared
  [PageHeader](../web/src/components/PageHeader.tsx) (filename, status badge,
  Run #id, action buttons). The existing `RunDetailView` body becomes the
  **Overview** + **Agents** content; no visual restyle yet — pure restructure.
  - [ ] 🟥 Add a `RunPage.tsx` that renders header + a tab bar + the active tab panel
  - [ ] 🟥 Move Download / Review / Delete / Regenerate actions into the shared header
  - [ ] 🟥 Tab bar: Overview, Agents, Cross-checks, Telemetry, (Values — gated)
  - **Verify:** open `/history/128`; the page shows one header with tabs;
    switching tabs does **not** remount the header or reload; existing content
    still renders under Overview/Agents.

- [ ] 🟥 **Step 2: Wire tabs into the router** — extend the reducer route model
  so a run page can carry a tab without losing deep-linking.
  - [ ] 🟥 Add tab to the route shape in [appReducer.ts](../web/src/lib/appReducer.ts) `parseRouteFromPath` (e.g. `/history/<id>` default tab, `/history/<id>/values`)
  - [ ] 🟥 Make `/concepts/<id>` parse to the run page on the **Values** tab (alias)
  - [ ] 🟥 Update the URL-sync effect + `popstate` handler in [App.tsx](../web/src/App.tsx) to round-trip the tab
  - **Verify:** navigate to `/concepts/128` → lands on run page, Values tab
    active; browser Back returns to prior view; refresh on `/history/128/values`
    stays on Values; copy/paste of the URL reopens the same tab.

- [ ] 🟥 **Step 3: Collapse the `concepts` view into `history`** — App renders
  the unified `RunPage` for both old entry points; remove the separate
  full-viewport `concepts` branch once Values is a tab.
  - [ ] 🟥 Point the "Review values" action at the Values tab (no full remount)
  - [ ] 🟥 Keep the full-width layout for the Values tab only (Concepts needs the 3-col width); other tabs use the standard capped width
  - **Verify:** from Overview, click "Review values" → Values tab opens in place,
    PDF + concept grid render, scroll position of the run page is preserved;
    no flash/full reload.

### Phase 2: Telemetry persistence (Track B — independent, can run in parallel with Phase 1)

> **Revised 2026-05-27 after code reading + user direction.** Two corrections
> to the original premise:
> 1. `TokenReport.add_turn()` is **never called in production** (tests only),
>    so there is no in-memory per-turn ledger to "flush." Per-turn token
>    *deltas* are instead derived from the cumulative `agent_run.usage()` the
>    coordinator already reads after every node.
> 2. The full per-iteration **content** (system prompt, every tool call + args,
>    every tool return, every model response) is **already persisted** to
>    `{output_dir}/{stmt}_conversation_trace.json` by `save_agent_trace`
>    ([agent_tracing.py:95](../agent_tracing.py)). It just isn't surfaced.
>
> **User requirement (expanded):** capture, per iteration, the tokens
> sent/returned, the exact request, the exact response, and tool-call activity
> — for debugging *and* cost. **Chosen storage = hybrid, full-verbatim:**
> per-turn *metrics* go in the DB (cheap, queryable, powers cost math); full
> *content* stays in the trace file (served on demand), with oversized single
> payloads capped.

- [x] 🟩 **Step 4: Schema v8 — per-turn metrics table + per-agent rollups** —
  done. `run_agent_turns` table + 4 rollup cols on `run_agents`, v7→v8
  migration block, `CURRENT_SCHEMA_VERSION=8`. Verified by
  `tests/test_db_schema_v8.py` (4 tests) + existing v2/v3 tests still green.
  - [ ] 🟥 `CREATE TABLE IF NOT EXISTS run_agent_turns (run_agent_id FK, turn_index, node_kind, tool_names, prompt_tokens, completion_tokens, total_tokens, cumulative_tokens, cost_estimate, duration_ms, ts)` + FK index. **No request/response content here** — that lives in the trace file (hybrid decision).
  - [ ] 🟥 `_V8_MIGRATION_COLUMNS` on `run_agents`: `prompt_tokens`, `completion_tokens`, `turn_count`, `tool_call_count` (`total_tokens`/`total_cost` already exist) — all default 0
  - [ ] 🟥 Add the `v7 → v8` `BEGIN IMMEDIATE` block (idempotent, duplicate-column-tolerant); bump `CURRENT_SCHEMA_VERSION = 8`
  - [ ] 🟥 Update the docstring version log; fix the stale v3 claim in CLAUDE.md gotcha #11
  - **Verify:** `python -m pytest tests/test_db_schema_v2.py tests/test_db_schema_v3.py -v` pass; add `tests/test_db_schema_v8.py` asserting (a) fresh init lands on v8 with the new table/columns, (b) a hand-built v7 DB walks up to v8 idempotently (run `init_db` twice).

- [x] 🟩 **Step 5: Capture per-turn metrics in the coordinator + persist** —
  done. `_turn_records` captured per node (delta vs prev cumulative, node kind,
  tool names, duration); attached to every `AgentResult` exit path via
  `_finalize`; persisted via `repo.insert_agent_turns` + extended
  `finish_run_agent` rollups; `save_agent_trace` now keeps text verbatim
  (100 KB cap) + carries the per-turn metrics. Verified by 3 new
  `tests/test_db_repository.py` tests + e2e green.
  - [ ] 🟥 In [coordinator.py](../coordinator.py) extraction loop, after each node compute the delta `usage()` and push a turn record (covers success, timeout-salvage, iteration-limit-salvage exits)
  - [ ] 🟥 Add `turns: list[...]` to `AgentResult`; persist via a new `insert_agent_turns` + extend `finish_run_agent` rollups in [db/repository.py](../db/repository.py)
  - [ ] 🟥 Enrich `save_agent_trace` so the trace file carries per-turn token deltas alongside the messages; cap any single payload > 100 KB with a truncation marker (full-verbatim decision)
  - [ ] 🟥 Guard the whole capture/flush in try/except (telemetry is advisory — never fault the run; mirror `_safe_usage_backfill`)
  - **Verify:** run `tests/test_e2e.py` (mocked pipeline); assert `run_agent_turns` rows exist per agent and `run_agents` rollups equal the summed turns; a forced flush exception does not fail the run.

- [x] 🟩 **Step 6: Expose telemetry + trace in the API** — done. Detail
  serializer now ships per-agent `token_breakdown` + `turns` and a run-level
  `telemetry_rollup`; new `GET /api/runs/{id}/agents/{statement}/trace`
  validates statement against the run's agents (no path traversal) and serves
  the trace JSON. TS types (`AgentTokenBreakdown`, `AgentTurnJson`,
  `TelemetryRollupJson`, `AgentTraceJson`) + `fetchAgentTrace` added; `tsc`
  clean, frontend api tests green.

  **Note:** CLAUDE.md gotcha #11 corrected (was "committed v3 / in-flight v6";
  now reflects committed v8 + the telemetry tables). Full suite: 1641 passed;
  the 2 failures (`test_silent_exception_surfacing` post-correction re-check,
  `test_sse_rejects_concurrent_run`) are **pre-existing** — both reproduce on
  pristine HEAD with my changes stashed.
  - [ ] 🟥 Add `turns[]` (metrics) + `token_breakdown` per agent and a run-level rollup (total tokens, est. cost, wall-clock) to the detail serializer in [server.py](../server.py)
  - [ ] 🟥 Add `GET /api/runs/{id}/agents/{statement}/trace` → reads `{output_dir}/{stmt}_conversation_trace.json` (404 if absent; never path-traverse — validate statement against known agents)
  - [ ] 🟥 Extend TS types in [web/src/lib/types.ts](../web/src/lib/types.ts) / `api.ts`
  - **Verify:** `curl /api/runs/128 | jq '.agents[0].turns | length'` > 0 and `.rollup.est_cost` present; the trace endpoint returns JSON for a real run and 404 for a bogus statement; `npx vitest run` type-checks.

### Phase 3: Restyle Overview + Agents to design primitives (Track A — the "ugly" fix)

- [ ] 🟥 **Step 7: Overview tab restyle** — replace the flat label/value config
  block and floated badges with design-system **cards**, a metric strip, and
  proper hierarchy (orange eyebrow + light title from `PageHeader`).
  - [ ] 🟥 Config → a card with a definition grid; status → a badge primitive; actions already in header (Step 1)
  - [ ] 🟥 Add a glanceable **metric strip** (total tokens · est. cost · wall-clock · #agents) reading the Phase 2 rollup, with a fallback for legacy runs lacking telemetry
  - [ ] 🟥 Honour the "chrome loose / data-dense tight" rule from the design memory — loosen the config card, keep agent rows dense
  - **Verify:** visual check against [pwc-design-system.html](pwc-design-system.html);
    update any RGB-asserting pinning tests in the same commit; `npx vitest run` green.

- [ ] 🟥 **Step 8: Agents tab restyle** — give each `AgentCard` real hierarchy
  (status, model, per-agent token/cost/duration), keep the timeline dense.
  - [ ] 🟥 Per-agent header row: statement · variant · status badge · model · tokens · est. cost · duration
  - [ ] 🟥 Collapsed-by-default preserved; expanded shows the `AgentTimeline`
  - **Verify:** expand an agent; timeline replays; numbers match the API; no
    layout regression on Sheet-12 multi-sub-agent (NotesSubTabBar) case.

### Phase 4: Telemetry tab + cost UI (convergence of both tracks)

- [ ] 🟥 **Step 9: Telemetry tab** — a per-agent table of turns (turn, tool,
  tokens, cumulative, duration) + a run-level summary, built on the Phase 2/3
  API.
  - [ ] 🟥 Per-agent turn table (dense, data-surface styling)
  - [ ] 🟥 Run rollup: total/est-cost/wall-clock + a "tokens by agent" and "tokens by tool" breakdown
  - [ ] 🟥 Visibly flag estimated columns (the `approx` flag) so numbers aren't over-trusted
  - **Verify:** Telemetry tab on `/history/128` shows turn rows whose totals
    reconcile to the agent rollup and the run total; legacy run with no turn
    data shows a clean "telemetry not captured for this run" empty state.

### Phase 5: Merge Values + Cross-checks tabs (Track A finish)

- [ ] 🟥 **Step 10: Values tab = Concepts workspace, reconciled styling** — mount
  `ConceptsPage`'s 3-column workspace inside the Values tab; converge its
  `uiStyles.ts` usage toward `theme.ts` tokens where they diverge.
  - [ ] 🟥 Mount the concept grid + PDF pane under the tab (lazy-load, like NotesReviewTab)
  - [ ] 🟥 Replace the orphaned `/concepts` chrome with the shared header
  - [ ] 🟥 Keep the tab hidden unless `canonicalEnabled`
  - **Verify:** with canonical mode ON, Values tab edits + cross-check re-run
    work exactly as before; with it OFF, the tab is absent and `/concepts/<id>`
    redirects to Overview.

- [ ] 🟥 **Step 11: Cross-checks tab** — surface the existing `ValidatorTab` +
  `PdfSourcePane` as its own tab (currently buried at the bottom of the detail).
  - **Verify:** failed checks still click-through to the target cell; passes
    render; matches current behaviour.

### Phase 6: Polish, accessibility, docs

- [ ] 🟥 **Step 12: Keyboard + a11y pass on the tab bar** — roving tabindex,
  `role="tablist"`/`tab`/`tabpanel`, focus-visible ring via
  [index.css](../web/src/index.css) (inline styles can't do `:focus-visible`).
  - **Verify:** Tab/Arrow navigation between tabs; focus ring visible; axe/manual screen-reader smoke check.

- [ ] 🟥 **Step 13: Full test sweep + docs** — backend + frontend suites, then
  update docs.
  - [ ] 🟥 `python -m pytest tests/ -v` and `cd web && npx vitest run` both green
  - [ ] 🟥 Update [CLAUDE.md](../CLAUDE.md): fix gotcha #11 schema version, add a telemetry-persistence invariant, note the unified run page
  - [ ] 🟥 Add/refresh a memory entry for the run-page consolidation decision
  - **Verify:** both suites green; CLAUDE.md reflects v8 + the merged surface.

## Rollback Plan

- **UI (Phases 1, 3, 5):** pure React/styling — revert the commits. The old
  `RunDetailView` / `ConceptsPage` entry points and `/concepts/<id>` route are
  the safe restore point; keep them until Phase 5 lands so a partial revert
  still routes.
- **Schema (Phase 2, Step 4):** v8 is **additive only** (new table + nullable
  columns). Rollback is a code revert; the orphaned `run_agent_turns` table and
  unused `run_agents` columns are harmless to existing readers (same property as
  the v3 `notes_cells` rollback note). Do **not** write a down-migration that
  drops columns — SQLite can't, and it isn't needed.
- **Telemetry flush (Step 5):** wrapped in try/except, so even a buggy flush
  cannot fail a run; disable by reverting the coordinator hook.
- **State to check on trouble:** `SELECT version FROM schema_version` (expect 8);
  `output/xbrl_agent.db` integrity; that `/history/<id>` and `/concepts/<id>`
  both resolve.

## Out of Scope (explicitly not doing)

- Tapping the provider SDK below pydantic-ai to recover true thinking-token
  counts (gotcha #6 ceiling) — we persist best-effort estimates and label them.
- Cross-run comparison / trend dashboards — the metric strip is per-run only.
- Reworking the homepage redesign already in flight (`HomeHero`, `StatTiles`,
  `RecentRunsList`) — coordinate, don't absorb.
- Any change to the extraction/notes pipelines themselves.
