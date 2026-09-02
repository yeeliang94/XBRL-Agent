# Live run, provider reasoning summaries, Responses API, and complete telemetry

Status: implemented locally on 2026-09-02. The checkout now defaults every
model-backed role to `openai.global.gpt-5.6-luna`, requests provider reasoning
summaries on OpenAI Responses, persists bounded summaries, records explicit
reasoning tokens in schema v44, and shows source preparation as a first-class
worker. A paid Windows provider round trip remains an operator-controlled
rollout check.

Prototype: `docs/prototype-live-run-reasoning.html`

## 1. Outcome

Refactor the live-run experience so an operator can answer four questions from
one screen:

1. What is the run doing now?
2. Which workers are queued, active, complete, or blocked?
3. What provider-supplied reasoning summary is available for each worker?
4. What usage and estimated cost has the entire run accumulated?

The production direction is prototype **A — Focused roster + evidence rail**,
with prototype C's small **All active summaries** section added to the bottom of
the rail. Prototype B remains a useful high-density reference but should not be
the default because its bordered console treatment is less consistent with the
current focused-workspace design system.

The phrase "final cost" in this plan means the estimated cost of every model
call attributable to the run, including helper passes, discarded attempts, and
retries. It must always carry a coverage label. The UI must never claim the
number is complete when a provider did not return usage.

## 2. Non-goals

- Do not expose or label anything as private chain-of-thought.
- Do not make all providers use the OpenAI Responses API.
- Do not migrate GPT-5.4 or other working Chat Completions routes merely for
  consistency.
- Do not remove full conversation traces or put their large contents in SQLite.
- Do not change extraction prompts, canonical facts, workbook writes, or the
  order of pipeline stages.
- Do not make a paid capability test run automatically at application startup.

## 3. Current-state findings

### 3.1 Live page

`web/src/pages/ExtractPage.tsx` currently renders four adjacent concepts:

- a run-level headline and progress message;
- `PipelineStages`;
- a collapsed `TokenDashboard`;
- `AgentTabs` beside `ActiveTabPanel` and `ActivityStream`.

The components are individually valid. The comprehension problem comes from
repeating the same run state in multiple places and requiring a worker tab to
exist before it can be inspected. Queued work, orchestration stages, and actual
agents therefore do not read as one system.

### 3.2 Provider reasoning

- `agent_runner.py` already maps PydanticAI `ThinkingPartDelta` objects to
  `thinking_delta` and `thinking_end` SSE events.
- The live reducer assembles those events into `ReasoningBlock` objects.
- `ActivityStream` labels the text "Provider reasoning".
- High-frequency reasoning events are intentionally not persisted by
  `db/recorder.py`, so live reasoning usually disappears from history.
- `model_settings.py` does not set `openai_reasoning_summary`. OpenAI Responses
  requests therefore do not opt in to summaries even when the transport is
  available.

Official OpenAI documentation says raw hosted-model reasoning tokens are not
visible via the API. A reasoning summary is returned only when explicitly
requested, while the usage object separately reports a reasoning-token count.
The product wording must remain **Reasoning summary**, never "chain of thought."

### 3.3 Transport

- Direct GPT-5.6 already selects PydanticAI `OpenAIResponsesModel`.
- A configured proxy defaults GPT-5.6 to `OpenAIChatModel` unless
  `XBRL_OPENAI_RESPONSES=1` is set.
- Chat Completions pins GPT-5.6 reasoning to `none` because this application is
  a multi-turn function-tool workflow.
- The installed local LiteLLM 1.83.0 package contains `/v1/responses`, reasoning
  summary streaming translation, and reasoning-token accounting.
- That local package is not proof that the Windows enterprise deployment uses
  the same version, exposes the endpoint, preserves summary events, or returns
  all usage details. Compatibility must be established against the configured
  gateway, not inferred from the client package.

### 3.4 Telemetry

The existing telemetry is substantial but cannot yet support an unconditional
"all calls included" claim:

- successful agent nodes emit cumulative usage and best-effort per-turn deltas;
- reasoning tokens are separated from visible output by `usage_metrics.py`;
- cache read/write tokens are captured;
- face-agent retry totals add failed-attempt tokens and cost to the final
  `run_agents` total;
- stored `total_cost` is a pre-cache estimate, while cache-adjusted pricing is
  calculated separately;
- some helper tasks have their own tables and rollups rather than one common
  call ledger;
- `run_agent_turns` does not store reasoning tokens explicitly and reconstructs
  them as `total - prompt - completion`;
- failed-attempt totals are added to `total_tokens` without adding the failed
  attempt's full prompt/output split. That can make the reconstructed reasoning
  number misleading for retried agents;
- a failed provider request can be billable even when the exception contains no
  usage. Such a run must report partial coverage rather than zero cost.

## 4. Product decisions

### 4.1 One narrative per layer

- The page header identifies the filing and exposes run actions.
- One run strip states the current pipeline activity, completion count, active
  worker count, and elapsed time.
- Active work uses a small rotating arc plus an explicit `Working` label and a
  live elapsed timer. Determinate work, such as scanned-page preparation, also
  gets a two-pixel progress bar. Reduced-motion mode keeps the arc static.
- One restrained stage line shows orchestration position. Stage-specific prose
  is not repeated underneath it.
- One complete worker roster contains planned, queued, active, terminal, and
  deterministic workers.
- One persistent right rail shows the selected worker's reasoning summary,
  activity, evidence, and technical metadata.
- Usage remains collapsed at the bottom, with the current estimated cost and a
  visible coverage statement in its summary row.

### 4.2 Worker identity

Seed all planned workers when the run starts rather than waiting for their
first SSE event. Each worker has a stable ID and one of:

- `planned`
- `queued`
- `running`
- `complete`
- `needs_attention`
- `failed`
- `cancelled`

Coordinator-owned paid work appears in the same roster as agents, but is
labelled as system work rather than being presented as an extraction agent.
The first required system worker is `source-preparation`: it starts after
Scout, reports scanned-page progress and elapsed time, retains its usage and
provider summary when available, and reaches a terminal state before extraction
workers leave `queued`. After source preparation completes, its row remains in
the roster as evidence of the handoff. The run strip and active-worker count
must include this work so the page never says `0 active` while transcription is
running.

The scout appears under Prepare. Face and notes workers appear under Extract.
Cross-checks and AI reviewers appear under Check and Review. Sheet-12 sub-agents
remain nested under the Disclosure notes worker rather than becoming unrelated
top-level tabs.

### 4.3 Reasoning provenance

Every displayed reasoning block must carry:

- provider;
- model;
- transport;
- visibility kind: `summary`, `provider_thinking`, `raw_open_weight`, or
  `unavailable`;
- attempt index;
- start/end timestamps;
- whether the content is complete or still streaming.

Hosted OpenAI Responses output is `summary`. Open-weight models that genuinely
return raw reasoning are `raw_open_weight`. Generic PydanticAI thinking from
another provider is `provider_thinking` unless its adapter can prove it is a
summary. The UI label comes from this field and must not guess from the text.

### 4.4 Graceful absence

The rail is always present. It shows one of:

- a streaming provider summary;
- the last completed summary;
- "This worker has not started";
- "This provider returned activity but no reasoning summary";
- "Reasoning summaries are disabled for this role";
- "The configured transport does not support summaries."

Blank rails and disappearing panels are not valid states.

## 5. Settings contract

Add an admin-only **AI models and reasoning** section. Preserve the current
server-side authorization boundary.

### 5.1 New shared settings

```text
openai_transport_mode:
  auto | responses | chat_completions

reasoning_summary_visibility_by_role:
  off | auto | concise | detailed

reasoning_summary_retention:
  live_only | trace_retention
```

The existing `thinking_levels` setting remains unchanged and independent.

### 5.2 Semantics

- **Reasoning effort** controls how much provider computation is requested.
- **Summary visibility** controls whether readable provider output is requested.
- **Transport** controls whether an OpenAI-compatible route uses Responses or
  Chat Completions.
- Changing visibility must not silently change effort.
- Changing effort must not silently enable summary retention.

For OpenAI Responses, map `auto`, `concise`, or `detailed` to PydanticAI
`openai_reasoning_summary`. Do not send that field to Chat Completions, native
Anthropic, or native Google transports. Their existing native settings and
adapter behavior remain provider-owned.

### 5.3 Capability result

Extend the existing explicit Test connection action with a capability test. It
must report separately:

- connection/authentication;
- `/v1/responses` availability;
- function-tool round trip;
- summary request accepted;
- summary event returned;
- reasoning-token usage returned;
- cached-token usage returned.

The capability test can incur a small provider charge and must remain an
explicit administrator action. Persist only the result, timestamp, model,
gateway fingerprint, and LiteLLM version header when supplied. Never persist
the API key.

`auto` may select Responses for a proxy only after a successful capability
result for the current gateway/model fingerprint. An absent, expired, or failed
result keeps the existing Chat Completions fallback. An explicit `responses`
selection fails configuration validation when the capability check is known to
be incompatible; it must not fail halfway through a filing run.

Keep `XBRL_OPENAI_RESPONSES` as the deployment-level emergency override. The
Settings page must show when the environment override wins over the saved UI
choice.

### 5.4 API changes

Update:

- `GET /api/settings`
- `POST /api/settings`
- `GET /api/config`
- `POST /api/test-connection` or add a narrowly scoped
  `POST /api/test-model-capabilities`

Return a per-role effective configuration so the UI can show requested versus
actual transport, effort, and summary visibility.

## 6. Backend implementation

### Phase A — model settings and capability selection

1. Add validated enums/helpers to `model_settings.py`.
2. Replace the environment-only proxy decision in `use_responses_api` with a
   resolved transport decision that accepts model, proxy, saved setting, and
   capability result.
3. Keep the current narrow model scope: GPT-5.6 first. Do not move GPT-5.4.
4. Set `openai_reasoning_summary` only on `OpenAIResponsesModel` when visibility
   is enabled.
5. Extend `describe_model_runtime` with requested/effective summary visibility
   and capability reason.
6. Ensure every live `Agent` retains `end_strategy="early"` and existing
   request limits.

### Phase B — versioned reasoning events

Introduce a semantic event contract while accepting the existing event names
during rollout:

```json
{
  "event": "reasoning_summary_delta",
  "data": {
    "reasoning_id": "SOFP_summary_2",
    "content": "...",
    "kind": "summary",
    "provider": "openai",
    "model": "gpt-5.6",
    "transport": "responses",
    "attempt_index": 0
  }
}
```

Complete each block with `reasoning_summary_end`. Bound and sanitize displayed
content. Never expose encrypted reasoning state. During transition, normalize
legacy `thinking_delta`/`thinking_end` in one frontend adapter rather than
branching every component.

Persist only one bounded completed-summary event per block when retention is
`trace_retention`. Continue excluding high-frequency deltas from SQLite. Full
conversation traces remain on disk under the existing retention and deletion
rules.

### Phase C — universal model-usage ledger (schema v44)

Add a durable `model_usage_calls` table rather than forcing every provider call
into an agent-turn abstraction. Suggested fields:

```text
id, run_id, run_agent_id nullable, role, worker_id, sub_agent_id nullable,
attempt_index, request_index, model, provider, transport,
started_at, ended_at, status, provider_request_id nullable,
input_tokens, cached_input_tokens, cache_write_tokens,
visible_output_tokens, reasoning_tokens, total_tokens,
estimated_cost_pre_cache, estimated_cost_adjusted,
pricing_status, usage_status, error_type nullable
```

Use a stable invocation ID and a uniqueness constraint across invocation,
attempt, and request index so retry-finalization cannot double insert.

Rules:

- Store reasoning tokens explicitly for new rows. Do not infer them from an
  aggregate that may include discarded attempts.
- Capture each successful model-request node as a call-level usage delta.
- On retry, persist the failed attempt's available full usage split before
  starting the next attempt.
- When a provider error supplies no usage, store `usage_status=unavailable` and
  keep the call in the denominator used by coverage.
- Route scout, face agents, note agents and sub-agents, reviewers, formatters,
  scanned-PDF sidecars, and any other paid helper through the same recorder.
- Telemetry failure remains non-fatal to extraction, but it changes run coverage
  to partial and emits a structured incident.
- Preserve `run_agents.total_cost` semantics for historical rows. Add new
  rollups rather than silently reinterpreting the old column.

### Phase D — rollups

Build one pure rollup function over `model_usage_calls`:

```text
usage coverage = complete | partial | unavailable
cost kind      = cache_adjusted_estimate | pre_cache_estimate | unavailable
```

Return:

- input tokens;
- cached input tokens;
- cache-write tokens;
- visible output tokens;
- reasoning tokens;
- total tokens;
- successful call count;
- retried/failed attempt count;
- pre-cache estimate;
- cache-adjusted estimate;
- calls with unavailable usage;
- calls with unconfirmed pricing.

The live SSE rollup and History API must call this same function. Do not keep a
separate browser-side cost sum as an authoritative total.

## 7. Frontend implementation

### Phase E — normalized worker view model

Create a pure selector that combines planned run configuration, agent state,
pipeline state, and deterministic stages into ordered worker rows. Use it from
both the live page and History replay so labels and lifecycle states cannot
drift.

Likely files:

- `web/src/lib/appReducer.ts`
- `web/src/lib/types.ts`
- `web/src/lib/agentTabKinds.ts`
- new focused selector under `web/src/lib/`

### Phase F — production Direction A

Refactor `ExtractPage.tsx` around:

- `LiveRunSummary`
- `WorkerRoster`
- `WorkerDetailRail`
- `RunUsageDisclosure`

Reuse existing logic from `AgentTabs`, `ActivityStream`, `AgentTimeline`, and
`TokenDashboard`; do not copy it into competing implementations. Once parity is
proved, remove the obsolete rendered path.

The rail contains:

- Reasoning summary tab;
- Activity tab;
- evidence/source references;
- provider, model, transport, effort and summary-visibility metadata;
- stop/rerun action for the selected controllable worker;
- compact summaries for all currently active workers.

Keep Stop all in the page header. Do not repeat it inside every selected-worker
pane.

### Phase G — settings UI

Split the current long General form into purpose-led sections without changing
the Settings page's ARIA tab contract:

- Service connection
- Models by role
- Transport and capabilities
- Reasoning effort and summary visibility
- Usage and retention
- Existing extraction/review defaults

All component styling remains inline. Shared tokens stay in `theme.ts`, layout
primitives in `uiStyles.ts`, and responsive/interaction rules in `index.css`.

## 8. Windows and LiteLLM rollout

Windows diagnostics received on 2026-09-02 confirm the expected client floor:
Python 3.13.12, LiteLLM 1.83.0, PydanticAI 2.9.0, and OpenAI 2.45.0 at git
revision `5f7c6b0`. The reported runtime has no LiteLLM proxy configured and no
`XBRL_OPENAI_RESPONSES` override. With this migration, GPT-5.6 therefore uses
direct OpenAI Responses automatically; it does not depend on an enterprise
gateway exposing `/v1/responses`. The local `.env` now uses Luna for the global
and Scout defaults, clears saved role overrides, and enables automatic
reasoning summaries. The admin Settings action performs the same role reset on
another checkout.

The client versions and mocked contracts prove application compatibility. They
do not prove the Windows host's credentials, firewall path, provider latency,
or live usage payload until an operator authorizes one paid connection test.

1. Deploy the updated checkout to Windows and restart the service so schema v44
   migrates the existing database one version at a time.
2. In Admin Settings, select **Use GPT-5.6 Luna for every role**, leave provider
   reasoning summaries on **Automatic**, and save.
3. Run one operator-approved low-cost tool round trip and confirm:
   - tool calls survive multiple turns;
   - reasoning summary deltas reach PydanticAI `ThinkingPartDelta`;
   - reasoning-token usage reaches `usage.details.reasoning_tokens`;
   - request IDs and cached-token details are retained;
   - Stop all still cancels during proxy initialization and streaming.
4. Compare completion rate, latency, total tokens, reasoning tokens, cache hit
   rate, and estimated cost with the Chat Completions baseline.
5. If a proxy is added later, verify its Responses endpoint before setting
   `XBRL_OPENAI_RESPONSES=1`; otherwise Chat Completions safely pins GPT-5.6
   reasoning to `none` rather than sending an incompatible tool request.

Windows startup keeps `PYTHONUTF8=1`, truststore injection, and the existing
proxy authentication behavior unchanged.

## 9. Verification

### Backend focused tests

- Extend `tests/test_gpt56_transport.py` for saved transport choice,
  capability-gated proxy selection, environment override precedence, and
  summary settings.
- Extend `tests/test_thinking_levels.py` to prove effort and visibility are
  independent.
- Add schema v44 fresh/migration/idempotency tests.
- Add a usage-ledger test for a success, a failed billable attempt, a retry, and
  a terminal success; assert no double count.
- Add a regression test proving failed-attempt totals cannot inflate reasoning
  tokens.
- Add coverage-state tests for missing usage and unconfirmed pricing.
- Add route authorization and validation tests for new admin settings.
- Add SSE contract tests for summary provenance and bounded persisted events.
- Run every pinning test named by invariants 2, 2a, 5, 6, 18, and 19.

### Frontend focused tests

- Every planned worker is visible before its first event.
- Selecting a worker changes one persistent rail without moving the roster.
- Summary, activity, unavailable, disabled, queued, failed, and retry states.
- All-active-summary links select the correct worker.
- One run headline; no duplicated stage prose.
- Stop all is available before the first agent event.
- Usage disclosure shows coverage and refuses a false complete claim.
- Settings distinguish effort, visibility, transport, and retention.
- Model-specific choices remain narrowed by server-published capabilities.
- ARIA tab/list semantics, keyboard navigation, live-region restraint, and
  reduced-motion behavior.

Run targeted Vitest suites, then `cd web && npm run build`. After focused
backend tests, run `venv/bin/python -m pytest tests/ -n auto` because the
transport and telemetry changes are cross-cutting.

### Manual checks

- Chrome/Edge at 1366×768, 1920×1080, and the current responsive breakpoint.
- Windows LiteLLM capability test and one controlled paid run.
- Direct OpenAI controlled run.
- A non-OpenAI model showing a truthful summary-unavailable fallback.
- Abort during proxy startup, during streamed summary, and during retry backoff.
- History replay with retention on and off.

## 10. Acceptance criteria

- The page shows every planned worker in one roster from run start to terminal
  state.
- The run state is narrated once; no incoherent duplicate progress messages.
- The Scout-to-extraction interval shows `source-preparation` in the worker
  roster with its rotating arc, elapsed timer, and bounded page progress.
- Queued extraction workers name source preparation as their dependency and do
  not appear active before that dependency completes.
- Selecting any worker changes the fixed rail and does not navigate away.
- Hosted OpenAI output is labelled reasoning summary, never chain-of-thought.
- GPT-5.6 Responses requests include the configured summary option.
- Proxy Responses is used only when explicitly selected or capability-verified.
- Chat Completions remains a functioning fallback.
- The displayed final estimate includes every recorded helper and retry call.
- Missing provider usage produces partial coverage, not a misleading zero.
- Reasoning tokens are explicit and cannot be inflated by discarded attempts.
- Live rendering remains responsive under concurrent summary and activity
  events.
- Existing extraction, cancellation, canonical fact, and workbook invariants
  remain unchanged.

## 11. Rollback

- A saved transport setting or `XBRL_OPENAI_RESPONSES=0` restores Chat
  Completions without removing telemetry rows.
- Summary visibility can be disabled independently of reasoning effort.
- The v44 ledger is additive and remains inert for older application code.
- The old event names remain readable for historical live traces during one
  compatibility release.
- Summary visibility and reasoning effort remain independently reversible from
  Admin Settings.
