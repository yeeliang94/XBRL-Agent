# XBRL Agent — Detailed Invariant Reference

This is the deep, task-specific reference reached from `CLAUDE.md` and
`AGENTS.md`. Do not load it end to end for every task. Read the numbered
sections that match the task and the pinning tests named there.

`AGENTS.md` is the single source of truth for universal working agreements,
action boundaries, verification, and review output. This file owns subsystem
invariants, failure history, and the reasons behind non-obvious constraints.
For values that change mechanically, such as the database schema version or a
runtime default, inspect the named code or configuration before editing.

## What This Is

A standalone XBRL extraction agent for Malaysian financial statement PDFs.
Extracts data into SSM MBRS XBRL Excel templates. Handles the five primary
statements (SOFP, SOPL, SOCI, SOCF, SOCIE) plus five supplementary notes
templates, across two filing standards (MFRS, MPERS) and two filing levels
(Company, Group). Agents run concurrently via a coordinator; results merge
into one workbook; cross-checks validate consistency.

## LLM Provider Setup

All LLM calls route through `_create_proxy_model()` in `server.py`. Both
`run.py` (CLI) and `server.py` (web UI) use this one function.

**Two modes:**

1. **Proxy mode** (`LLM_PROXY_URL` set): all models route through an
   OpenAI-compatible LiteLLM endpoint. Used on Windows (enterprise) and Mac
   (local dev via `start.sh`).
2. **Direct mode** (`LLM_PROXY_URL` empty): provider detected from model-name
   prefix:
   - `gpt-*`, `o1-*`, `o3-*`, `o4-*` → OpenAI (`OPENAI_API_KEY`)
   - `claude-*` → Anthropic (`ANTHROPIC_API_KEY`)
   - everything else → Google Gemini (`GEMINI_API_KEY` / `GOOGLE_API_KEY`)

**Mac:** `start.sh` launches LiteLLM on `:4000` and sets
`LLM_PROXY_URL=http://localhost:4000/v1`. Config in `litellm_config.yaml`,
master key `sk-local-dev-key`, logs in `litellm.log`. Falls back to direct
mode if the proxy fails to start.

**Windows:** all traffic goes through `https://genai-sharedservice-emea.pwc.com`
(OpenAI-compatible). Direct Google API calls are blocked (403).

### Runtime settings and deployment environment

Operator-managed settings are edited in the web Settings page and persisted
atomically to `output/settings.json` (or `XBRL_SETTINGS_FILE`). The web and CLI
reload that file before work starts. Environment variables and `.env` remain
fallbacks for deployment/bootstrap values. Administrators may also save the AI
service key through Settings; the local JSON is written owner-only and is
git-ignored. Saved local settings take precedence for keys the UI manages, and
the form can remove an override to return to the deployment value.

```env
# At least one provider API key
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Proxy (set by start.sh on Mac, manual on Windows)
LLM_PROXY_URL=                 # empty = direct mode
LLM_PROXY_API_KEY=             # proxy auth key; start.sh sets the local-dev master key here
GOOGLE_API_KEY=                # real Google key; also the proxy auth key on Windows (no LLM_PROXY_API_KEY there)

# Model defaults
TEST_MODEL=openai.global.gpt-5.6-luna
SCOUT_MODEL=openai.global.gpt-5.6-luna  # legacy fallback; Settings → Document scan wins

# Auth (gotcha #24). AUTH_MODE unset = real email+password login; AUTH_MODE=dev
# auto-sessions as dev@localhost (CI / offline only; refuses to boot on Azure).
AUTH_MODE=                     # leave blank for prod login; set "dev" for tests/CI
SESSION_SECRET=                # REQUIRED in prod (startup fails without it); dev falls back
# AUTH_IDLE_TIMEOUT_S=900      # sliding idle logout (default 15 min)
# AUTH_LOGIN_MAX_ATTEMPTS=5    # (email, IP) lockout threshold
# AUTH_LOGIN_LOCKOUT_S=900     # lockout window seconds

# Item-32 fact-based verification (gotcha #25) — both DEFAULT ON.
# XBRL_FACT_BASED_CHECKS=1     # cross-checks read run_concept_facts; 0 = xlsx path
# XBRL_FACT_BASED_VERIFY=1     # verifier reads facts; 0 = xlsx formula-eval path

# Extraction-harness efficiency flags (docs/PLAN-extraction-harness-efficiency.md)
# — all DEFAULT OFF / no-op during rollout; unset = today's behaviour.
# XBRL_TEMPLATE_SUMMARY_COMPACT=0  # read_template: one line per ROW (SOFP 80k→35k chars)
# XBRL_TEMPLATE_IN_PROMPT=0        # face agents: template in the system prompt; read_template returns a pointer
# XBRL_MAX_CONCURRENT_AGENTS=0     # cap on top-level agents running at once; 0 = unbounded
# XBRL_CACHE_PROBE=0               # lift the per-turn cache / history-rewrite probe lines to INFO
# XBRL_SCOUT_WALLCLOCK_S=300       # whole document-scan deadline; 0 disables
# XBRL_SCOUT_MAX_TURNS=20          # Scout model responses; Settings caps this at 40
# CLI: scout is ON by default (`--no-scout` to skip). Cost: run_agents.total_cost is
# still the PRE-CACHE estimate; `scripts/report_run_economics.py` prints the
# cache-adjusted figure beside it (pricing.estimate_cost_cache_adjusted).

# Canonical concept model is now MANDATORY (rewrite Phase 1.1): the legacy
# direct-xlsx pipeline and the XBRL_CANONICAL_MODE opt-out were removed.
# The flag is no longer read (see gotcha #21).
```

### PydanticAI model construction (V2-compatible API)

```python
# Proxy path (OpenAI-compatible)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
model = OpenAIChatModel(name, provider=OpenAIProvider(base_url=url, api_key=key))

# Direct Google
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
model = GoogleModel(name, provider=GoogleProvider(api_key=key))

# Direct Anthropic
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
model = AnthropicModel(name, provider=AnthropicProvider(api_key=key))
```

**Do not** pass `base_url=` or `openai_client=` as direct kwargs to
`OpenAIModel` — those were removed in pydantic-ai 1.x. Always use `provider=`.

### Temperature Constraint

For Gemini 3 models through the proxy, temperature **must** stay at `1.0`.
Lower values cause failures or infinite loops.

## Load-Bearing Invariants (Gotchas)

Each of these encodes a real failure mode. Touching the code around them
without reading the invariant is how regressions creep back.

### 1. `PYTHONUTF8=1` required on Windows

Windows defaults to `charmap` codec which crashes on Unicode text from PDFs.
`start.bat` sets this; if running manually:
`set PYTHONUTF8=1 && venv\Scripts\python.exe server.py`.
`write_text(..., encoding="utf-8")` is used as a safety net throughout.

### 2. pydantic-ai on the V2 line (floor `>=1.107.1`, pinned by `constraints.txt`)

Upgraded 2026-07-12 (docs/PLAN-pydantic-ai-v2.md; V1→V2 flip verified by
the full suite + a live SOFP run). `constraints.txt` is the reproducible
pin (`pip install -r requirements.txt -c constraints.txt`); the code runs
on 1.107.1 and 2.x — both expose the same post-deprecation API surface.

- `Agent._function_tools` does not exist — cannot monkey-patch tools.
- Use `OpenAIChatModel(name, provider=OpenAIProvider(...))`; `OpenAIModel`
  is a deprecated alias.
- Tool event streaming uses `agent.iter()` + `node.stream()` — no
  `event_callback` or monkey-patching. Tool-result events expose the part
  as `event.part` (`.result` was removed in V2).
- History processors register as `capabilities=[ProcessHistory(fn), ...]`
  (`Agent(history_processors=)` was removed in V2); ctx-taking processors
  still need the real `RunContext` annotation on the first param.
- `agent_run.usage` / `result.usage` are properties — no parentheses.
  Test fakes must expose usage as an attribute/property, NEVER a
  `MagicMock(return_value=...)` callable (property access turns that into
  `int(MagicMock()) == 1` and telemetry silently reads 1 token).
- Every live `Agent(...)` pins `end_strategy="early"` — V2's default is
  `'graceful'` (same-batch function tools run AFTER a successful terminal
  tool). Do not drop the pin without an eval-suite comparison (plan B.3.1).
- The Google model-string prefix is `google:` on V2 (`google-gla:` was
  removed); the server/pricing prefix tables carry both spellings.
- V2's silent `UsageLimits.request_limit` default is still 50 — gotcha #18
  unchanged, now asserted directly by
  `tests/test_max_agent_iterations_below_pydantic_cap.py`.

**Transport is per-model, not per-provider (2026-08-01 peer review).** Two
vendor rules make "one OpenAI-compatible client for everything" wrong:

- **GPT-5.6 + function tools + Chat Completions requires effective reasoning
  `none`**, and OMITTING the reasoning field is not neutral — 5.6 then
  defaults to `medium`, which is the incompatible case. Every agent here is a
  multi-turn tool caller. `model_settings.use_responses_api()` therefore
  builds an `OpenAIResponsesModel` for the 5.6 family on the DIRECT OpenAI
  path, and on Chat Completions `build_model_settings` pins
  `openai_reasoning_effort="none"` rather than letting the default through.
  The proxy path stays on Chat Completions by default because the enterprise
  proxy may not expose `/v1/responses` — flip with `XBRL_OPENAI_RESPONSES=1`
  once confirmed. `gpt-5.4` is deliberately untouched.
- **`none` is in `THINKING_LEVELS` but is NOT a pydantic-ai level.** It is the
  OpenAI wire value for "reasoning off"; pydantic-ai spells that `False`, and
  neither `ANTHROPIC_THINKING_BUDGET_MAP` nor the Google path has a key for
  the string. `_unified_thinking()` does that translation — passing `none`
  through raises at the same call site the old dict bug hit. 5.6 also dropped
  `minimal`, which `_openai_reasoning_effort()` folds to `none`.
- **Gemini 3 on a REMOTE OpenAI-compatible proxy is refused at construction**
  (`server._warn_if_gemini_loses_thought_signatures`). Google requires the
  `thought_signature` from each prior functionCall to be echoed back, even at
  minimal thinking levels, and a missing one is a 400 — so the run dies on the
  second tool call after paying for everything before it. The Mac local proxy
  is unaffected (it already bypasses to a native `GoogleModel`). Override with
  `XBRL_ALLOW_GEMINI_PROXY=1` only once the proxy is proven to preserve them.
- **The two cache shapes take DIFFERENT values — they are not
  interchangeable.** `prompt_cache_retention` (legacy) takes `in_memory` |
  `24h`; `prompt_cache_options.ttl` (5.6+) accepts **`30m` and nothing else**.
  Reusing one constant for both sends `ttl: "24h"`, which is a 400 on every
  request. Hence `CACHE_RETENTION` and `CACHE_OPTIONS_TTL` are separate. The
  new shape is **opt-in** via `XBRL_OPENAI_CACHE_OPTIONS=1` (through
  `extra_body`, since pydantic-ai 2.9.0 has no typed field for it).
- **An unsupported level is substituted loudly, and never inverted.**
  `supported_thinking_levels(model)` is the per-model vocabulary and is
  surfaced to the Settings picker as `thinking_level_choices_by_model`, so the
  UI cannot offer `minimal` for a 5.6 role. If one is configured anyway,
  `_LEVEL_FALLBACK` maps `minimal → low` — the least reasoning that still
  exists — and logs it. Mapping it to `none` disabled reasoning entirely,
  which is the opposite of what the operator asked for.

Pinned by `tests/test_gpt56_transport.py`, `tests/test_thinking_levels.py`,
`tests/test_provider_routing.py`.

### 2a. Cross-loop safety: agents on background threads

The reviewer, notes-reviewer, notes-formatter and suite-runner passes each run
on their own thread under `asyncio.run` (`api/*.py`), so process-global async
state is touched by MORE THAN ONE event loop. An `asyncio.Future` or
`asyncio.Semaphore` belongs to the loop that created it; awaiting one from
another loop raises `got Future attached to a different loop`.

Two structures in `notes/agent.py` carry this, by different means:

- `_render_semaphores` is keyed by `id(loop)` — one semaphore per loop.
- `_inflight` (render coalescing) holds **`concurrent.futures.Future`**, not
  `asyncio.Future`, under `_inflight_lock`, and awaiters join via
  `asyncio.wrap_future`. That keeps ONE render shared across loops instead of
  merely making it safe.

`task_registry` follows the same ownership rule for cancellation. Each
registered task is stored with its owning event loop under a thread lock.
Stop requests arriving from another thread dispatch
`task.cancel(USER_ABORT_REASON)` through `loop.call_soon_threadsafe`; they
never invoke cancellation directly on a foreign loop. Pinned by
`tests/test_task_registry_cross_loop.py`.

This became reachable when the notes formatter gained `zoom_pdf_region`
(2026-08-02): before that every `_inflight` caller was on the main server
loop. Reproduced as a hard failure with two threads. Pinned by
`tests/test_page_cache_single_flight.py::test_two_event_loops_can_share_one_inflight_render`.
**Any new global holding an asyncio primitive must state which of these two
shapes it uses.**

### 3. XBRL templates derived from SSM linkbase

Templates in `XBRL-template-MFRS/` and `XBRL-template-MPERS/` are derived from
SSM MBRS linkbases under `SSMxT_2022v1.0/`. Formula cells must trace back to
the calculation linkbase.

**Do not hand-edit template formulas.** If a formula is wrong, regenerate
from the linkbase and capture the before/after in `backup-originals/`.
`scripts/regenerate_mfrs_sofp_sopl_formulas.py` covers MFRS SOFP, SOPL,
SOCI (both variants) and SOCF-Direct (2026-07-03: the hand-built SOCI/
SOCF-Direct originals deviated from the calc linkbase — orphaned OCI
components, added-instead-of-subtracted reclassification adjustments,
inverted SOCF-Direct payment signs; pinned by
`tests/test_template_formulas.py`). SOCF-Indirect and SOCIE remain
hand-curated. Historical incident (2026-04-07, +20-row SOFP offset bug)
recorded in git history.

### 4. `compare_results.py` vs current templates — row numbering differs

The reference file (`SOFP-Xbrl-reference-FINCO-filled.xlsx`) has sub-sheet rows
shifted +1 from the current template. False "EXTRA" / "MISSING" diffs are that
mismatch, not a bug in `fill_workbook`. Validate by opening the filled
workbook in Excel so formulas evaluate — don't rely on the diff.

### 5. SSL: two distinct things — only one is harmless

**Harmless (suppressed):**

```
LiteLLM:WARNING: Failed to fetch remote model cost map... [SSL: CERTIFICATE_VERIFY_FAILED]
```

Enterprise firewall blocks GitHub; LiteLLM falls back to local pricing data.
Suppressed via `litellm.suppress_debug_info = True` in `server.py`.

**Real (fixed by truststore, 2026-04-27):** if the *actual* LLM call to the
proxy raises `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] ... unable
to get local issuer certificate`, that's the corporate MITM root CA missing
from `certifi`. `server.py` calls `truststore.inject_into_ssl()` at import so
Python's `ssl` module reads the OS certificate store (Windows store / macOS
Keychain). `truststore` is in `requirements.txt`; reinstall deps after a
pull if you see this error. Requires Python ≥ 3.10 — older interpreters
silently skip the inject and need `SSL_CERT_FILE` set manually.

### 6. Per-turn token counts are deltas of cumulative usage (approximate)

PydanticAI counts tokens internally and exposes only a **cumulative**
`agent_run.usage` property after each node — there is no true per-turn split.
The live coordinator loop derives a per-turn figure by **subtracting the
previous node's cumulative usage** and persists it to
`run_agent_turns` (schema v8) along with exact timing + tool activity; the
prompt/completion split is therefore best-effort, while duration and tool
calls are exact. After a face-agent loop completes, those same records rebuild
the text cost report that `save_result` initially creates mid-loop; otherwise
that file would permanently report zero because final usage is not observable
until the tool node finishes. The Telemetry tab labels the split honestly.
After completion, `server.py` also backfills run-level totals from
`result.usage`. Both the
**face coordinator** (`coordinator.py`) **and the single-agent notes path**
(`notes/coordinator.py`) capture this; the Sheet-12 fan-out leaves per-turn
rows empty (its sub-agents merge into one row) — rollups still populate.

**Verbatim content lives on disk, not the DB.** `save_agent_trace`
(`agent_tracing.py`) writes the full request/response transcript to
`{output_dir}/{stmt}_conversation_trace.json` — text kept verbatim (single
payloads capped at 100 KB; binary elided) — served on demand by
`GET /api/runs/{id}/agents/{stmt}/trace` (which verifies the resolved path
stays under the run's `output_dir`). Don't move that heavy content into
SQLite (the hybrid-storage decision).
**Failed agents save a trace too:** the timeout / iteration-cap / cancel /
exception paths call a best-effort helper that falls back to
`agent_run.ctx.state.message_history` (a partial run has no `.result`), via
`save_messages_trace` — so the trace viewer is useful exactly when debugging
a failure. The Sheet-12 fan-out saves one trace per sub-agent
(`NOTES_LIST_OF_NOTES_subN[_retryK]_conversation_trace.json`, run-63 fix).
The Activity viewer discovers those files through the trace-manifest endpoint
and lets the operator select each sub-agent/retry. Traces and notes failure
logs are sensitive diagnostics: run deletion removes them, and startup purges
files older than `XBRL_TRACE_RETENTION_DAYS` (default 90; `0` delegates
retention to an external system) while preserving workbooks/source files.
Face-agent provider response timeouts receive two fresh whole-attempt retries
with increasing backoff. The timeout classifier walks PydanticAI's exception
chain because direct OpenAI and Anthropic transports wrap their SDK timeout in
`ModelAPIError`; native Google uses PydanticAI's injected HTTPX client. Raw
`httpx.ConnectTimeout` remains in the separate one-retry connection lane.
Pinned by `tests/test_face_transient_retry.py`.
**Traces show the END-STATE history, not per-turn snapshots:** pydantic-ai
1.x persists each turn's processed history back onto the run state, so
token-saving compaction placeholders ("Page N was viewed earlier…") appear
where the model originally saw full content — every trace file carries a
`trace_note` saying so. Don't diagnose "the model wrote while blind" from
placeholders (the run-63 misdiagnosis). Pinned by
`tests/test_agent_tracing.py`.

### 7. Frontend uses inline styles, not Tailwind

Tailwind CSS v4 didn't load reliably on Windows (the upload button was
unclickable). All components use inline `style={}` props. **Do not** convert
back to className-based Tailwind.

**Visual spec ([docs/xbrl-design-system.html](docs/xbrl-design-system.html)):**
the canonical application design system. Follow Direction A in
[`docs/prototype-ui-overhaul.html`](docs/prototype-ui-overhaul.html) exactly
for product composition and screen states. `docs/pwc-design-system.html` is a
compatibility mirror for older pinning references, not a design authority.
Tokens live in
`web/src/lib/theme.ts` (the `pwc` object — imported by ~30 components, so it
is the single cascade point); shared component primitives in
`web/src/lib/uiStyles.ts`. Anything inline styles can't express (`:hover`,
`:focus-visible`, focus rings, table cell borders) lives in
`web/src/index.css` global classes or `NotesReviewTab.css`. Many frontend
tests assert exact RGB values derived from `theme.ts` tokens — change a token
and its pinning test in the same commit. Clipboard styling
(`web/src/lib/clipboard.ts`) is intentionally NOT tokenised (gotcha #16).

**Run-detail is one tabbed surface** (`RunDetailView.tsx`): Overview · Agents ·
Notes · Cross-checks · Telemetry · Review · Values (Review + Values gated on
canonical mode; Review is the reviewer-pass diff/flags tab — see gotcha #21).
The tab bar uses `role="tablist"`/`tab`/`tabpanel` with roving-tabindex arrow
nav, and **collides by role with the Notes-12 `NotesSubTabBar`** (also
`role="tab"`). Tests querying tabs must scope with `within(...)` by the
tablist's `aria-label` (`"Run detail sections"` vs `"Sheet-12 sub-agents"`),
never a bare `getAllByRole("tab")`. "Review values" switches to the Values tab
in-place — do **not** revert it to an `<a href>` page jump (that was the
disjointed-nav bug). `/concepts/{id}` is now an **alias**: App routes it to the
unified run page with `initialRunTab="values"` (threaded App → HistoryPage →
RunDetailPage → RunDetailView); the bare Template top-nav (no run id) still
renders the standalone `ConceptsPage`. Tab content is lazy: heavy sub-trees
(NotesReviewTab editor, ConceptsPage workspace, PdfSourcePane) mount only when
their tab is active.

### 8. Node.js may not be on PATH (Windows)

`start.bat` auto-discovers Node.js in `C:\Program Files\nodejs\`. If it's
elsewhere, set PATH manually before running.

### 9. Output directory structure

```
output/
  run_001/       # CLI runs (auto-numbered)
  run_002/
  {uuid}/        # Web UI runs (UUID per session)
```

`run.py` uses `Path(__file__).resolve().parent / "output"` as the base — works
regardless of caller's CWD.

### 10. Run lifecycle — `runs` row created before validation

`run_multi_agent_stream` in `server.py` creates the `runs` audit row
**before** parsing statement types, resolving variants, or building models.
If validation or proxy-model creation fails, the History page still captures
the failed run instead of silently dropping it.

The orchestration body is wrapped in try/except/finally so every exit path —
success, exception, `CancelledError`, client disconnect — leaves the row in a
terminal status (`completed`, `completed_with_errors`, `failed`, `aborted`)
and never `running`.

`mark_run_merged` is called immediately after a successful merge, **before**
the final status update, so `GET /api/runs/{id}/download/filled` has a durable
pointer to `filled.xlsx` even if later persistence crashes.

`_safe_mark_finished` in `server.py` swallows audit-write exceptions so error
handlers never double-fault. **Don't** "fix" this by removing the try/except.

**Terminal runs leave no child row `running` (run-83 hardening, 2026-08-05):**
face + notes agent rows are finalized from in-memory results right after the
merge (`_persist_face_and_notes_agent_rows` — BEFORE cross-checks, reviewer
and notes passes), and `_safe_mark_finished` additionally calls
`repo.reconcile_unfinished_run_agents` so any row still `running` under a
terminal run is closed as `cancelled`. The backstop never promotes a row to
`succeeded` from the presence of a workbook or trace — real statuses come
only from in-memory results. (Run 83: a Stop-All during the notes reviewer
left all five extraction rows + CORRECTION at `running` forever under an
`aborted` run.) Pinned by `tests/test_abort_reconciles_agent_rows.py`.

**Persistent-draft addition (2026-04-26):** `POST /api/upload` now also
inserts a draft `runs` row at upload time with `status='draft'` and an
empty `started_at`. This makes the upload immediately shareable as
`/run/{run_id}` and ensures abandoned uploads still appear in History.
The new `POST /api/runs/{id}/start` endpoint reuses the existing draft
(flipping `draft → running` via `repo.mark_draft_started`) instead of
creating a fresh row, so for that flow `run_multi_agent_stream` accepts
an `existing_run_id` kwarg. The legacy `POST /api/run/{session_id}`
keeps creating a new row from scratch — both paths converge on the same
terminal-status guarantee. `_safe_mark_finished` only fires once
extraction has actually started, so drafts that are never started simply
sit in History with status `draft` forever (no auto-cleanup is in scope).

**Stop-All partial-merge addition (2026-04-27):** the `CancelledError`
branch of the coordinator-await now calls `_attempt_partial_merge`
*before* `_safe_mark_finished("aborted")`. Any per-statement
`{stmt}_filled.xlsx` files already on disk get merged into a partial
`filled.xlsx`; `mark_run_merged` writes the DB pointer; a
`partial_merge` SSE event surfaces the included / missing statement
list. The helper is hardened to swallow every exception so the cancel
handler never double-faults (gotcha #10 invariant preserved). Users on
slow runs who hit Stop All now keep their work as a downloadable
artifact — pinned by `tests/test_stop_all_preserves_partial.py`.

**Restart reconciliation is one lifecycle transaction.** Startup
reconciliation sets each orphaned parent to `aborted`, closes every
still-running child, and appends a durable `run_complete` event with
`phase=restart_reconciliation` before the caller commits. A restarted
process must never expose a terminal parent with a running child or no
terminal event. Pinned by `tests/test_stale_run_reaper.py`.

**Artifact currentness participates in success.** A retained scratch or
merged workbook may remain downloadable after a canonical face export or
post-review notes refresh fails, but the run must finish
`completed_with_errors`, never `completed`. A run with no canonical facts
keeps the established benign scratch fallback. A failed fact-presence probe is
unknown, not proof of zero facts: the export is attempted so a real failure
emits degradation and tips status. Pinned by
`tests/test_silent_exception_surfacing.py::test_canonical_export_degradation_prevents_clean_run_status`.

### 11. DB schema — version-stepped auto-migration on startup

`db/schema.py::CURRENT_SCHEMA_VERSION` is authoritative; inspect it instead of
copying the current number from this document. `init_db` reads the stored
version and walks an old DB up **one version at a time**
through per-version, idempotent `ALTER TABLE` blocks, so any older DB reaches
the current schema automatically. `db/schema.py` is the authoritative
per-version detail; each step N is pinned by `tests/test_db_schema_vN.py`.

Two rules govern every step:

- SQLite can't add a `NOT NULL` column without a default, so every
  `_Vn_MIGRATION_COLUMNS` entry is nullable or has a safe default.
- The `runs.status` column has **no `CHECK` constraint on purpose** — a new
  status value must not require a full-table migration (same for the
  `error_type` columns).

Two things are **retained but inert — do NOT "clean them up":**

- **`runs.orchestration`** (v10, `TEXT DEFAULT 'split'`) — the monolith
  experiment was deleted, but the column stays (always `'split'`) so the schema
  version and History read-back stay stable.
- **`doc_conversions`** table (v21) — the scanned-PDF feature was removed
  (gotcha #26); the table stays as an inert artifact so the migration chain
  replays intact. No code reads it.

Migration history currently documented here through v46, with each feature
detailed in its linked gotcha: v11
`concept_render_aliases` (#21) · v12–v13 reviewer tables `run_fact_snapshots`
/ `reviewer_flags` / `run_review_tasks` (#21) · v16 gold-eval tables +
`runs.benchmark_id` (#23) · v17 `run_agents.error_type` · v18 auth tables (#24)
· v20 `auth_users.is_admin` (#24) · v22 `runs.notes_table_style` (#16) ·
v23–v25 notes-reviewer tables + `notes_cell_tombstones` (#16, #27) · v26–v27
notes-formatter `notes_format_tasks` / `notes_format_snapshots` (#16) · v28
`notes_coverage_rows` (#27) · v29 `notes_cells.style_source` (#16) · v30–v31
Evals workspace repeats/taxonomy/gold-prose + suites (#30) · v32
`eval_suite_run_docs` frozen-corpus snapshot (#30) · v33 gold fingerprint on
`eval_scores` + benchmark archive flag (#30) · v34 `run_lineage` (stage-level
resume) · v35 notes source-integrity tables (`notes_source_generations` /
`_notes` / `_blocks`, `notes_block_usages`, append-only
`notes_disposition_events`, `notes_integrity_runs`) + five nullable
content-provenance columns on `notes_cells` · v36 `runs.notes_integrity_mode`
· v37 `notes_block_placements` + `notes_integrity_tasks` +
`notes_cells.content_revision` / `source_render_version` — all inert unless
`XBRL_NOTES_SOURCE_INTEGRITY` is `shadow`/`enforce`
(#31, docs/PLAN-notes-source-integrity-build.md) · v38 `mtool_fill_receipts`
(#28, mTool fill audit trail — one row per fill) · v39
`mtool_fill_receipts.snapshot_notes_*` (#28, the PROSE revision — v38 recorded
only the numeric one) · v40 `concept_semantic_addresses` (#28, taxonomy identity
for filing targets) · v41 `taxonomy_concepts` / `template_slots`, notes/facts
quarantine state, and receipt filing-readiness evidence (#28) · v42
`run_incidents` / `run_events` (durable run-level failures and the
low-volume coordinator timeline; request/response traces remain on disk) · v43
`run_agents.error_message` (the exact terminal agent refusal/error alongside
the stable `error_type` classification) · v44 explicit reasoning-token and
usage-coverage columns plus the request-level `model_usage_calls` ledger · v45
notes-review flags carry the stable detector finding id and grounded source
pages/evidence that made the human disposition terminal · v46 current/retired
membership for canonical concept nodes across startup template re-imports.

### 12. Filing level — Company vs Group

Each run has one `filing_level` (`"company"` or `"group"`, default
`"company"`) that flows end-to-end: `RunConfigRequest` → `RunConfig` →
`template_path()` → agent prompts → verifier → cross-checks → history.

- **Company templates:** 4 cols — A=label, B=CY, C=PY, D=source.
- **Group templates:** 6 cols — A=label, B=Group CY, C=Group PY, D=Company CY,
  E=Company PY, F=source.
- **Group SOCIE** uses 4 vertical row blocks (rows 3–25 Group CY, 27–49 Group
  PY, 51–73 Company CY, 75–97 Company PY) — but the COLUMN count differs by
  standard, and SoRE has no blocks at all. Measured shapes:

  | Template | Cols | Rows | Group overlay applied |
  |---|---|---|---|
  | MFRS Group SOCIE | 24 | 97 | `_group_socie_overlay.md` (matrix) |
  | MPERS Group SOCIE | 4 | 97 | none — `socie_mpers.md` describes the blocks |
  | MPERS Group SoRE | 6 | 16 | `_group_overlay.md` (plain 6-col) |

  **`_group_socie_overlay.md` must never be applied outside MFRS Default.**
  It was applied to every Group SOCIE unconditionally, which contradicted
  `socie_mpers.md` in one rendered prompt and pointed SoRE at rows 27–97 of a
  16-row sheet (2026-08-01 peer review). The failure is SILENT:
  `cell_resolver.resolve_cell` returns `None` for a coordinate with no
  concept and the caller skips it, so the fact never reaches
  `run_concept_facts` and — because the export re-renders from facts (gotcha
  #21) — the statement lands empty while the agent reports success. Pinned by
  `tests/test_group_socie_overlay_routing.py`, which reads the LIVE templates
  and checks the fully rendered prompt for mutually exclusive layout claims
  (phrase-by-phrase assertions do not catch this — `test_socie_prompt_mpers.py`
  passed throughout).

On Group filings, verifier + cross-checks run twice (Group cols, then Company
cols) and report separately. Root-level template xlsx files no longer exist —
all templates live in `Company/` or `Group/`.

**The face persona is standard-neutral.** `_base.md` is loaded for every run,
so it may not name a standard; `prompts/__init__._render_standard_block()`
injects the run's actual framework instead. The persona used to declare the
agent an MFRS specialist "for Malaysian public listed companies", which every
MPERS (private-entity) run inherited. Pinned by
`tests/test_prompt_standard_neutrality.py`.

### 13. Scout page hints are soft guidance only

Normal web and CLI runs execute a fresh scout pass inside
`run_multi_agent_stream` before extraction. The pre-run page offers an optional
preview, but a preview is never required and never replaces the run-owned pass.
Specialized internal rerun paths may explicitly set `use_scout=False` when they
are reprocessing persisted material rather than starting a normal extraction.

The Settings page exposes the Scout's whole-run deadline and model-turn cap as
`XBRL_SCOUT_WALLCLOCK_S` (default 300 seconds; 0 disables) and
`XBRL_SCOUT_MAX_TURNS` (default 20; maximum 40). Both are resolved at the start
of every new Scout run, so a saved change applies without a server restart.
The maximum remains below PydanticAI's 50-request ceiling (gotcha #18).

Extraction agents receive `page_hints` (face_page + note_pages) as recommended
starting points. Agents can freely view **any** PDF page — there is no
`allowed_pages` enforcement. `view_pdf_pages` only validates 1 ≤ page ≤ N.

**Do not** re-introduce page-restriction logic (no `allowed_pages`, no
"disallowed" filtering). `tests/test_page_hints.py` asserts this with
negative assertions.

**Scout coverage push (2026-05-29) — soft contract still stands.** The
scout's `Infopack` was extended with structural hints downstream agents
read as advisory only:

- **Face-line refs** (`StatementPageRef.face_line_refs`): one
  `FaceLineRef(label, note_num, section)` per visible face-page line
  item. Populated by the deterministic `scout/face_structure.py` regex
  on text PDFs, or by the scout LLM emitting structured JSON on
  scanned PDFs (vision path). `face_read_in_detail` flags whether
  scout actually read the face page in detail. Rendered into the
  face-prompt navigation block with explicit `(scout-observed —
  VERIFY against the PDF)` framing. Pinned by
  `tests/test_scout_face_line_refs_schema.py`,
  `tests/test_scout_face_line_refs_wiring.py`,
  `tests/test_coordinator_forwards_face_line_refs.py`,
  `tests/test_prompts_render_scout_face_refs.py`.
- **Sub-note hierarchy** (`NoteInventoryEntry.subnotes`): nested
  `SubNoteInventoryEntry(subnote_ref, title, page_range)` capturing
  2.1, 2.14, (a), (b) sub-headings under each top-level note. Nested
  (not peer entries) precisely because Sheet-12 fan-out iterates
  `inventory` directly and validates coverage per int `note_num` —
  promoting "2.1" to a peer of "2" would double-bill the agent. The
  structural guarantee is pinned by
  `tests/test_sheet12_ignores_subnotes.py`. `note_num: int` stays
  unchanged; sub-notes carry their own `subnote_ref: str` precisely so
  the `int(item["note_num"])` coercions in `notes/coverage.py:256`
  and the `Field(ge=1, le=999)` validator in
  `scout/notes_discoverer_vision.py:58` keep working.
- **Entity / period / unit context** (top-level `Infopack`):
  `entity_name`, `reporting_period_cy`, `reporting_period_py`,
  `currency`, `scale_unit`, `consolidation_level`. Rendered into a
  `=== SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING) ===` block in
  every face and notes prompt. The run's user-declared `denomination` is
  threaded independently into both prompt families as the presentation-scale
  contract: figures are transcribed exactly as printed and are never multiplied
  or divided. The scout's `scale_unit` is retained only as an independent
  disagreement check. `scale_unit="unknown"` is the safe scout default.
  Pinned by `tests/test_infopack_context_schema.py`,
  `tests/test_scout_populates_context.py`,
  `tests/test_prompts_render_context.py`.

All three additions degrade gracefully: empty `face_line_refs` /
`subnotes` / context fields fall through to today's bare hint blocks.
Scanned-PDF sidecar transcription may use inventory ranges to choose which
pages to inspect, but those ranges never prove note completeness. If any
requested page fails, no partial sidecar is published; notes agents fall back
to direct PDF vision. The same Scout vision inventory emits sparse page
rotation corrections only when the primary financial content clearly needs a
90/180/270-degree clockwise turn; upright and uncertain pages are omitted and
there is no confidence field. The sidecar retries transport/provider failures
at the same orientation. A genuinely blank render is retained as an empty page
without a provider call so it cannot invalidate an otherwise complete sidecar.
Only an empty transcription from an ink-bearing page changes orientation: a
hinted page falls back to unrotated and an unhinted page tries 90 degrees.
Pinned by `tests/test_notes_discoverer_vision.py`, `tests/test_pdf_sidecar.py`, and
`tests/test_pdf_sidecar_wiring.py`.
### 14. Notes feature — five supplementary templates (parallel with face)

Notes agents fill MBRS templates 10–14 (MFRS) / 11–15 (MPERS) in parallel
with face statements. Discovery is PDF-first: scout extracts a
`notes_inventory` from the PDF, then per-template agents read those notes and
write content to matching rows. No deterministic matching, no OCR, no synonym
dictionary — pure LLM judgement.

Key invariants:

- **Sheet 12 (`LIST_OF_NOTES`) fans out** into `N` sub-agents; `N` is
  model-aware via `pricing.resolve_notes_parallel(model)`.
- **A later same-note/same-row Sheet-12 write is a revision.** Within one
  `write_notes` call, multiple attributed payloads may be deliberate chunks
  and remain combinable. Across calls, a later payload with the same
  `note_num` and resolved template row replaces every earlier-call chunk for
  that note. This is keyed on structured note identity plus row resolution,
  never prose matching. It prevents a sub-agent's corrected table from being
  concatenated after its incomplete first version. Pinned by
  `tests/test_notes_agent_label_prevalidation.py` and
  `tests/test_notes_source_prompt.py`.
- **Retry budget:** every notes agent and Sheet-12 sub-agent retried at most
  once. Exhaustion writes `notes_<TEMPLATE>_failures.json` /
  `notes12_failures.json` / `notes12_unmatched.json` side-logs.
- **Sheet-12 stall bounds:** every outer/model/tool stream step has a 180-second
  no-progress timeout (`XBRL_NOTES12_TURN_TIMEOUT_S`), which enters the normal
  retry lane. The parent fan-out has a 420-second hard deadline
  (`XBRL_NOTES12_FANOUT_TIMEOUT_S`); remaining workers are cancelled, emit a
  terminal audit event, and land as explicit failed batches so one worker can
  never hold the pipeline indefinitely.
- **Cell cap:** 30,000 chars (`notes.writer.CELL_CHAR_LIMIT`). Longer content
  truncated with `[truncated -- see PDF pages N, M]` footer.
- **Column rules:** prose rows write col B only; numeric rows (13, 14) fill
  all four value columns on group filings. Evidence always col D (Company) /
  col F (Group).
- **Numeric presentation scale:** Sheets 13/14 receive the same declared
  `denomination` as face statements. Values stay in the PDF's presentation
  scale (for example RM'000 `2,500` remains `2,500`, never `2,500,000`). A
  curated notes↔face contradiction is a failed cross-check that tips filing
  readiness and requires human review; it is excluded from the face-facts
  reviewer because that pass cannot re-check or safely repair note values. It
  is never silently auto-scaled.
- **Numeric notes also carry one HTML disclosure field.** Sheets 13/14 keep
  their numeric grid in `run_concept_facts`, but the taxonomy text-block row
  (currently row 4) is reproduced as rich HTML in `notes_cells` so it appears
  in the Review tab. That row is resolved through the shared v41
  `template_slots` field-semantics manifest, not `notes_nodes` (numeric notes
  deliberately live in `concept_nodes`). The Review projection exposes the
  slot even while blank, and the shared facts API uses the same resolver. The
  remaining rows stay numeric even if legacy/corrupt prose exists there.
  Pinned end-to-end by `tests/test_notes_cells_persistence.py`,
  `tests/test_notes_structured_table_reproduction.py`, and
  `tests/test_server_notes_cells_api.py`, plus the shared-write regression in
  `tests/test_phase7_notes_unified.py`.
- **Scanned-PDF fallback:** if the PyMuPDF-regex inventory pass returns empty,
  `scout.notes_discoverer_vision._vision_inventory` renders the notes section
  in 8-page batches and runs up to 5 vision batches in parallel.

Full walkthrough: [docs/Archive/NOTES-PIPELINE.md](docs/Archive/NOTES-PIPELINE.md).

**Formatter output is a DECLARED schema, not repaired prose (2026-08-03).**
`notes/format_schema.py` mirrors `notes/format_patch.py`'s closed vocabulary
exactly — wider only moves the failure, narrower silently rejects patches that
are valid today. `format_patch` stays the authority; the schema only removes
the parse-failure class that `_parse_json_patch`'s fence-stripping and
balanced-object hunt existed to absorb. `patch_to_dict` MUST keep
`exclude_none=True`: `_apply_style` iterates the keys it is handed, so a
serialised `"border_top": null` reaches `_border_value(None)` and raises.
Kill switch `XBRL_NOTES_FORMATTER_STRUCTURED=0` — unverified against a live
model, so a quality drop is a config flip, not a redeploy. The formatter's
`read_note_cell` tool was removed with it: the CURRENT CELLS payload already
carried those fields for every row, plus `table_geometry` the tool lacked.
Pinned by `tests/test_notes_format_schema.py`,
`tests/test_notes_formatter_zoom.py`.

### 15. MPERS — first-class filing standard

A `filing_standard: "mfrs" | "mpers"` axis threads through the whole pipeline
(registry → coordinator → agents → server API → cross-checks → scout →
frontend → history). MFRS is the default everywhere.

Key invariants:

- MPERS templates live in `XBRL-template-MPERS/{Company,Group}/` (15 per
  level), generated by `scripts/generate_mpers_templates.py` from
  `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers/`.
- Slot numbering shifts vs MFRS: `10-SoRE.xlsx` is **MPERS-only**; notes
  occupy 11–15 (vs 10–14 on MFRS).
- Cross-checks honour `applies_to_standard` per check.
  `sore_to_sofp_retained_earnings` is MPERS-only and fires only on
  `variant=SoRE`.
- Server rejects variant/standard mismatches (e.g. `SOCIE/SoRE` on MFRS)
  before launching any agent.
- **Always run the generator with `--snapshot`** so the previous version
  lands in `backup-originals/` for schema-drift diffing.
- **Template formatting parity with MFRS (2026-04-23):** the MPERS
  generator (`scripts/generate_mpers_templates.py`) now strips SSM
  ReportingLabel suffixes (`[text block]` / `[textblock]` /
  `[abstract]` / `[axis]` / `[member]` / `[table]` / `[line items]`)
  from rendered column-A labels via `_strip_display_suffix`, filters
  pure XBRL scaffolding rows (`[table]` / `[axis]` / `[member]` /
  `[line items]` nodes) via `_is_structural_label`, and wires
  face→sub cross-sheet rollup formulas via
  `_inject_face_to_sub_rollups` so face-sheet line items pull from
  sub-sheet `*Total X` rows the way MFRS does. Concept IDs on every
  row are preserved untouched — XBRL compliance lives in the
  calc/presentation linkbase, not label text. Templates no longer
  carry the suffixes; `notes.labels.normalize_label` still strips
  defensively in case agents quote taxonomy labels verbatim.
- **Notes-pipeline MPERS-awareness (2026-04-23 hardening):**
  `render_notes_prompt` takes a `filing_standard` kwarg; the sheet
  map and cross-sheet hints render per standard. An MPERS overlay
  block surfaces the `[text block]` suffix convention and narrower
  concept set. The writer + coverage-validator normalisers share
  `notes.labels.normalize_label` which strips trailing
  `[text block]` / `[textblock]` / `[abstract]` / `[axis]` /
  `[member]` / `[table]` / `[line items]` so agent-emitted labels
  that drift from template text still match the 0.85 fuzzy threshold.
  `create_notes_agent` seeds the template's col-A labels into the
  system prompt so agents pick from the live MPERS vocabulary, not
  their MFRS training prior. SOCIE cross-checks (`socie_to_sofp_equity`,
  `sopl_to_socie_profit`, `soci_to_socie_tci`) branch on
  `filing_standard`: MPERS reads col B (2), MFRS keeps col X (24) for
  equity/TCI and the NCI-aware col 24/3 for profit.
- **Prompt-file precedence (`prompts/__init__.py`):** variant-specific
  `{stmt}_{variant}.md` wins over filing-standard-specific
  `{stmt}_{standard}.md`, which wins over the generic `{stmt}.md`.
  MPERS-specific SOCIE Default lives in `prompts/socie_mpers.md` and is
  only loaded on MPERS filings — MFRS still falls through to the
  matrix-shaped `socie.md`. Use this tier (rather than an overlay
  suffix) whenever an entire statement prompt needs to differ by
  filing standard; the overlay mechanism remains for level-level
  differences (e.g. `_group_overlay.md`). A small MPERS-only *addendum*
  (not a full statement rewrite) may instead be code-injected in
  `render_prompt` gated on `std_key == "mpers"` — e.g. the MPERS SOPL
  revenue-bucket note (`_MPERS_SOPL_REVENUE_NOTE`) appended only on MPERS
  SOPL so `sopl.md` stays coarse and its pinning test is unaffected. Pinned
  by `tests/test_extraction_hardening_prompts.py`.
- **SOCIE / SoRE dividend sign (2026-04-25):** entered as POSITIVE
  magnitudes because every SOCIE/SoRE template's `*Total increase
  (decrease) in equity` formula subtracts the row. Pinned to live
  formulas by `tests/test_notes_prompt_phase1.py::test_live_templates_subtract_dividends_paid`
  (parametrised across all 6 templates including MPERS Group SOCIE,
  which was extended with per-block formulas in the same change).
  The pinning test above is the live contract for the historically documented
  dividend-sign decision.

Full walkthrough: [docs/MPERS.md](docs/MPERS.md).

### 16. Notes cells are HTML; Excel download regenerates from the DB

Notes agents emit **HTML** (not plaintext) into cells on sheets 10–14 (MFRS) /
11–15 (MPERS). Flow:

```
agent HTML → sanitiser → notes_cells (DB, canonical) → overlay → xlsx stream
                                     ↘ NotesReviewTab (TipTap editor)
```

**Verbatim table passthrough on Word uploads (2026-07-19) — the one exception
to "content stays style-free."** When a run carries a `source.html` sidecar
(gotcha #29), notes agents COPY the Word table's markup — inline `style=` and
all — straight into `content` rather than translating it into `format_ops`.
The sanitiser's table-tag whitelist already preserves those declarations
(verified end-to-end on the FINCO 2021 statement: padding, text-align and
per-side borders all survive), and `mtool/notes_decorate._merge_cell_style`
gives persisted per-cell declarations precedence over theme defaults, so
Word's own formatting reaches the review page, the clipboard and the mTool
filing without a model re-describing it. **PROSE stays style-free, enforced in
code** — `notes/writer.py::_strip_non_table_styles` removes inline `style=`
from every non-table tag on the AGENT path (`_sanitize_payload`). That strip is
load-bearing, not belt-and-braces: the sanitiser itself *does* permit
`text-align`/`margin-*` on `p`/`h3`/`li`, `color` on `span` and
`background-color` on `mark`, and `ingest/docx_html` deliberately writes
paragraph styles into `source.html` — so without it, "copy the table verbatim
but not the paragraph beside it" would rest on model judgement alone. The human
TipTap editor reaches the DB through the PATCH endpoint and keeps its paragraph
alignment. `_style_cell_html` tags such a
cell `style_source='source'` (vs `ops` / `unstyled`) so the Notes-tab chip can
tell "copied from the source" apart from "may want a formatter pass".

**Copying declarations is NOT copying the look — `data-source-styled` (run-75
fix, 2026-07-20).** A Word financial table defines its appearance largely by the
borders it does NOT state (measured on FINCO 2021: 515 of 662 cells state no
border at all; zero state a fill). Every renderer here paints a theme grid where
a cell is silent, so a perfectly-copied table came out boxed in lines the source
never had — verbatim in the DB, house-styled on screen. Absence can't be copied,
so it is DECLARED: `notes/writer.py::_mark_source_styled_tables` stamps
`data-source-styled="true"` on each table in verbatim content, and that marker
means **"this table's borders are the whole truth; add none."** It must stay in
lock-step across FOUR surfaces or preview ≠ paste ≠ filing:
`notes/html_sanitize.py` (`_TABLE_STRUCTURE_ATTRS`, value-checked to `"true"` —
without it a human edit strips the marker and the grid returns on first save),
`web/src/lib/cellFormatting.ts::StyledTable` (TipTap drops unknown attrs on
round-trip), `NotesReviewTab.css` (`border: 0`, so per-cell inline borders still
win), `mtool/notes_decorate.py` + `web/src/lib/clipboard.ts` (border-family
declarations dropped from the cell base). The house totals double-underline is
suppressed on these tables too — the source carries its own. Verified in real
Chrome (specificity vs `.is-totals-num` is not reproducible in jsdom). Pinned by
`tests/test_notes_format_sidecar.py`, `tests/test_mtool_notes_decorate.py`, and
the `clipboard` / `cellFormatting` web tests. Size is
handled by the existing mTool ladder (full → compact → lite → flat →
oversize), not by a new guard. Pinned by `tests/test_notes_source_prompt.py`,
`tests/test_notes_format_sidecar.py`. Plan:
docs/PLAN-notes-verbatim-and-scout-inventory.md.

Key invariants:

- **`notes_cells` (schema v3) is the source of truth.** The on-disk xlsx is a
  flattened snapshot; the download endpoint overlays the DB rows onto a temp
  workbook at stream time (`notes/persistence.overlay_notes_cells_into_workbook`).
- **The overlay is AUTHORITATIVE for the prose region, not additive.** It writes
  each surviving row's prose (col B) and evidence (`evidence_col_for(filing_level)`,
  D=Company / F=Group), then BLANKS every coordinate the reviewer emptied
  (recorded in `notes_cell_tombstones`, v25) — an additive-only overlay can't
  express a deletion. `clear`/`move` add a tombstone, `author`/`edit` remove it,
  `revert` reconciles. Callers MUST pass the run's `filing_level`. Rerun-safety:
  a notes-agent rerun drops the sheet's tombstones, and the overlay never blanks
  a coord that has live prose. Pinned by
  `tests/test_notes_reviewer_overlay_deletions.py`.
- **Cap is 30,000 RENDERED chars** (`notes.html_to_text.rendered_length`), not
  raw HTML; sanitiser + writer enforce it, and the PATCH endpoint returns 413
  over the limit.
- **Agent re-run CLOBBERS edits:** the coordinator calls
  `delete_notes_cells_for_run_sheet(run_id, sheet)` before writing a fresh batch.
  A confirm dialog gates this, fed by
  `GET /api/runs/{run_id}/notes_cells/edited_count`.
- **The HTML tag whitelist in `prompts/_notes_base.md` must match the sanitiser's
  `ALLOWED_TAGS`** (`notes/html_sanitize.py`) — a divergence silently strips
  markup the prompt invited.
- **Inline `style=` is a VALIDATED whitelist on TABLE tags only** (notes WYSIWYG,
  docs/PRD-notes-wysiwyg-formatting.md); off the table `style=` is stripped
  wholesale, so **prose in the DB stays style-free**. The gate is tag-aware
  (`_STYLE_PROPS_BY_TAG`): fill / per-side `border-*` / `text-align` on table
  tags, `color` on `<span>`, `background-color`+`color` on `<mark>`,
  `text-align`/`margin-left` (indent) on `<p>/<h3>/<li>`, `width`/`min-width` on
  `<table>`/`<col>`. Every value is shape-checked (rejects `url()`,
  `expression()`, malformed borders). Table-tag *attributes* are also an explicit
  allowlist (`_TABLE_STRUCTURE_ATTRS`: `colspan`/`rowspan`/`colwidth` + validated
  `style=`). Two browser-only traps the sanitiser MUST tolerate (jsdom doesn't
  reproduce them — verifiable only in real Chrome): (1) the browser CSSOM
  COLLAPSES four uniform per-side borders into the `border:` shorthand / grouped
  `border-width|style|color` longhands, so `_is_border_shorthand` /
  `_is_border_group` accept them and `resolveCellBorders` (`cellFormatting.ts`)
  expands them back; (2) a swatch may serialise as `rgb(255, 255, 255)` with
  spaces, so border parsing must treat the whole `rgb(...)` as one token. Erasing
  an edge uses an explicit `1px hidden #000000` triplet (`BORDER_HIDDEN`), NOT
  `none` — under `border-collapse` a neighbour's grid line out-prioritises `none`.
  "No fill" persists as `background-color: transparent`, not attribute-absence.
  Pinned by `tests/test_notes_html_sanitize_css.py` (incl.
  `test_browser_collapsed_border_shorthand_survives_on_td`) +
  `test_server_notes_cells_api.py`.
- **Editor v2** (docs/PRD-notes-editor-v2.md) is a full rich-text + table editor:
  `ALLOWED_TAGS` gained human-only marks `u/s/sup/sub/mark/span` — a **superset**
  of the agent set (agents still emit style-free HTML, so the rule is
  "agent-emittable ⊆ sanitiser-permitted"). The colour palette is enforced at the
  TOOLBAR (`notesPalette.ts`), not the sanitiser (which only validates safe colour
  values). The sanitiser-warning UI panel was removed (still logged in
  `sanitizer_warnings`). One two-tier `EditorToolbar` (Tier 2 = table controls,
  keeps the `table-format-bar` testid). Per-cell alignment (`applyCellAlign`),
  column width (TipTap `resizable`, serialised as `<colgroup>`), merge/split, and
  indentation (`notesIndent.ts`) all round-trip through the sanitiser + the
  `html_to_excel_text` overlay. Pinned by the `cellFormatting`/`notesIndent`/
  `NotesReviewTab` web tests + `tests/test_notes_html_sanitize_css.py`.
- **One AI styling role plus deterministic source passthrough:** notes extraction
  agents author content and table geometry only. Their typed tools do **not**
  expose `format_ops`, and prompts do not ask them to infer styling while they
  extract figures. PDF-origin tables land plain and may be styled later by the
  dedicated formatter. Word/source tables are the narrow exception above: code
  preserves the source's own table markup verbatim; that is passthrough, not
  model-authored styling. The writer retains the legacy internal
  `NotesPayload.format_ops` field for old stored payloads and compatibility
  tests, but it is absent from every live model tool schema. Invalid legacy ops
  degrade to plain and never block content. The deterministic house-style floor
  remains removed because it invented borders not present in the source.
  - **Source-copy replacement:** a source-styled resend replaces earlier drafts
    for the same top-level note and row. `read_source_note` returns the whole
    note slice, so `_top_level_note_key` matching ("9.1" → "9") makes this safe;
    a different note sharing the row survives and draws an advisory mixed-row
    message. With `source.html`, an unread source note gets
    `format_unconsulted_source_nudge`; a source note that was read but rebuilt
    without its table markup gets `format_uncopied_source_nudge`. Both paths
    direct the agent to copy source markup, never synthesize styling. Counts are
    based on cells actually written after label resolution, so rejected writes
    cannot produce a false formatting warning. Styling provenance remains
    persisted as `source` / legacy `ops` / `unstyled`; only `unstyled` and old
    `floor` cells are surfaced as formatter candidates. Pinned by
    `tests/test_notes_source_prompt.py`, `tests/test_notes_format_sidecar.py`,
    and `tests/test_db_schema_v29.py`.
  - **Notes formatter agent (manual REPAIR pass, `POST /api/runs/{id}/notes-format`,
    per prose sheet):** the only AI role that authors styling; returns
    JSON style patches applied to `notes_cells.html`, rejected unless rendered
    text, numeric tokens, and table geometry survive `sanitize_notes_html`.
    Production invariants: writes are compare-and-swap
    (`cas_update_notes_cell_html`, `WHERE html = launch-snapshot` under
    `BEGIN IMMEDIATE` — a row edited/deleted mid-pass is skipped, never
    clobbered); safety is versioning (`notes_format_snapshots` v27 +
    `/notes-format/revert`, content-guarded so it undoes styling not a newer
    edit); it atomically interlocks with the reviewer pass
    (`claim_*_task_guarded`); bounded by `XBRL_NOTES_FORMATTER_WALLCLOCK_S` (300)
    + `XBRL_NOTES_FORMATTER_MAX_REQUESTS` (16, ≤45 per gotcha #18); `error_type`
    taxonomy (`FORMATTER_ERROR_TYPES`) + token telemetry + a re-written trace;
    `notes_formatter` ∈ `_AGENT_ROLES` with `XBRL_NOTES_FORMATTER_MIN_CONFIDENCE`
    (0.70). Numeric sheets (13/14) are excluded (422). Pinned by
    `tests/test_notes_format_patch.py`, `test_notes_formatter_routes.py`,
    `test_db_schema_v26.py`/`_v27.py`.
  - **PDF structure-first formatting (2026-08-25).** Text PDFs already land
    style-free. Scanned-PDF `source.html` transcripts now preserve content and
    table geometry only: `ingest/pdf_sidecar.normalize_transcription` removes
    presentation attributes and unwraps presentation-only inline tags before
    publication. This stops the transcript model from becoming a second
    styling author and keeps scanned/text PDFs on one formatter path. When an
    individual page fails, publication is note-range atomic: only notes whose
    full scout page range transcribed are exposed through `read_source_note`;
    affected notes return no snippet and use direct PDF vision. A partial note
    is never stitched across a missing page. Pinned by `tests/test_pdf_sidecar.py`,
    `tests/test_pdf_sidecar_wiring.py`, and `tests/test_notes_source_snippets.py`.
    When the sidecar applies, it is built after the pipeline-owned scout and before
    extraction. That paid page transcription can run for up to the sidecar's
    600-second overall deadline, so the server drains SSE concurrently and
    emits `pipeline_stage=transcribing_source` before the first model call.
    The automatic formatter is unrelated to this pre-extraction interval: it
    runs later, after notes review. When
    `XBRL_PDF_NOTES_AUTO_FORMAT=true`, the run formats unstyled/floor prose
    cells after the notes reviewer, in parallel by sheet, through the same
    content/number/geometry verifier, CAS writes, snapshots, task rows, limits,
    and mTool-safe closed vocabulary as the manual pass. Every sheet uses the
    shared guarded claim, so a manual reviewer that starts first makes the
    automatic formatter record a skipped outcome; once any formatter sheet is
    claimed, the reviewer cannot start over it. The setting is
    admin-only and defaults OFF because it adds a paid pass per filled prose
    sheet. Stop All cancels the group. `uploaded.docx` is an explicit exclusion:
    Word's source-styled behavior above is unchanged. Pinned by
    `tests/test_pdf_sidecar.py`, `test_pdf_sidecar_prompts.py`,
    `test_notes_auto_format.py`, `test_pdf_notes_auto_format_wiring.py`,
    `test_notes_format_patch.py`, `test_settings_api.py`, and the Settings /
    reducer web tests.
  Styling reaches the Review panel + clipboard paste ONLY — the xlsx download
  stays a text overlay (native xlsx styling still deferred).
- **Evidence column is read-only in the editor** (audit trail); the PATCH
  endpoint ignores any `evidence` key.
- **Heading-injection scope:** the writer auto-injects `<h3>` from the
  `parent_note` + `sub_note` structured fields ONLY. In-prose `(a)/(b)/(i)/(ii)`
  sub-section labels MUST be preserved verbatim by the agent as
  `<p><strong>…</strong></p>` — don't let the writer-owned-heading rule
  over-generalise and flatten them. Pinned by `tests/test_notes_prompt_phase1.py`.
- **Clipboard decoration:** `web/src/lib/clipboard.ts::decorateHtmlForClipboard`
  injects inline `style=` (border, padding, right-align for numeric cells matched
  by `_NUMERIC_CELL_RE`) at copy time only — the DB stays style-free, because
  external CSS doesn't travel with a paste into M-Tool / Word / Outlook. It's
  option-driven (`ClipboardFormatOptions`); **the defaults (`DEFAULT_FORMAT_OPTIONS`)
  reproduce the old hard-coded STYLING byte-for-byte** — same border/padding/
  font/alignment declarations; keep that equivalence when editing. Two
  deliberate additions ride on top of it and are NOT part of the historic
  bytes: the run-76 TX-dialect pass (legacy `width` attrs on unsized numeric
  tables, white borders on source-styled / border-none tables — block below)
  and, on themed copies only, the theme's own knobs. Pinned by
  `web/src/__tests__/clipboard.test.ts`.
- **Notes-table style THEME (docs/PLAN-notes-table-theme.md):**
  `ClipboardFormatOptions` was promoted to a full table theme that is the shared,
  server-side firm default (`XBRL_NOTES_TABLE_STYLE` via `/api/settings`). ONE
  resolved theme (`resolveTheme(runOverride, firmDefault)`) drives BOTH the editor
  (as `--nt-*` CSS vars) and the clipboard, so preview == paste. A per-run override
  lives on `runs.notes_table_style` (v22, editable post-run via the Notes-tab
  picker) and is a full SNAPSHOT, not a partial diff. Per-cell manual styles win
  over the theme; "Reset cell to theme" (`resetCellToTheme`) re-inherits it. A
  totals row's double underline (`border-bottom: 3px double`) is saved document
  formatting, and Copy reads the resolved theme at click time.
  **The SHIPPED firm default is `notes/table_theme.py::HOUSE_NOTES_TABLE_STYLE`,
  resolved ONLY through `firm_theme()`** (2026-07-20, chosen by the product
  owner) — `server._notes_table_style` and
  `formatting_agent._resolve_notes_table_theme` both delegate there. They used
  to each parse the env var, and the formatter's copy still fell back to `{}`
  after the house default landed, so the agent reasoned about a boxed grey grid
  over a ruled display and would "correct" formatting that was already right.
  A new consumer must resolve through `firm_theme()`, never re-read the env var.
  The look: accountant *ruled*, not boxed — no cell grid
  (`borderStyle: "none"`), one rule under the header row (`headerRule`, the knob
  added for it), bold un-filled headers, historic Arial 10pt / 4×8px density,
  and totals underlines left MANUAL (the auto-detect matched the word "total" in
  row text and invented rules — the reason the house-style floor was removed,
  2026-07-07). Two DISTINCT layers, do not conflate them: `NotesTableStyle()` /
  `DEFAULT_FORMAT_OPTIONS` still mean "no theme configured at all" and keep the
  historic boxed STYLING (a dozen pinning tests rely on that; the run-76
  TX-dialect attrs below layer on top for every theme, so the full output is
  no longer literally byte-identical); `_notes_table_style()` returns the HOUSE style when the setting is
  unset. An explicit `{}` is the operator's escape hatch back to the historic
  look, so rollback is a Settings change, not a code revert. `headerRule` moves
  in lock-step across `mtool/notes_decorate.py`, `clipboard.ts`,
  `themeToCssVars`, `NotesReviewTab.css` and `api/config_routes.py` — and a
  source-styled table suppresses it (verbatim block above), since that table
  carries the source's own rules.
  **mTool/TX renders SILENCE differently from a browser (run-76, 2026-07-20):**
  in the TX editor an UNDECLARED cell boundary shows the default grey grid, and
  CSS widths are ignored — there is no "no line", only "visible line" or "line
  painted white", and no CSS page fit, only the legacy `width` ATTRIBUTE. So the
  two mTool-bound decorators (`mtool/notes_decorate.py` + `clipboard.ts`, twins
  — keep in step) must (a) spell out every intended-invisible edge as explicit
  white (`_fill_undeclared_borders_white`, the absent-edge twin of the proven
  hidden→white translation) for source-styled tables and `borderStyle: "none"`
  themes, and (b) fit unsized tables to the page via `width="100%"` +
  first-row percentage attrs (`_fit_table_width`; label column keeps the rest,
  amounts share a bounded slice; skipped on colspans / operator-sized tables —
  capture "operator-sized" BEFORE the decorator injects its own `width: 100%`
  CSS or the fit never fires; `<colgroup>`/`colwidth` column sizing counts as
  operator-sized too; 9+ columns bail to the page fit alone since the two
  percentage floors would sum past 100%; nested tables fit independently —
  row/cell scans are scoped to their OWN table). A side whose SHARED edge the
  neighbouring cell declares is NOT painted white (`_neighbor_declared_sides`;
  a same-width white tie wins by position under border-collapse and would
  erase a source underline / the header rule; spans disable the suppression).
  The white grid costs ~27 chars/cell against Excel's 32,767-char cell cap and
  lands on exactly the tables whose `compact` tier is inoperative, so the
  exporter ladder (gotcha #28's `_resolve_note_html`) retries full/compact/
  lite with `fill_white_grid=False` (the exact pre-run-76 payload) BEFORE
  falling to `flat`, and reports the drop (`white_grid_dropped` per-entry +
  meta count, surfaced in `MtoolFillModal`) — a note never lands on a worse
  tier than the pre-run-76 ladder gave it. `strip_inline_styles` (the destyle
  rescue rung) also drops the `data-source-styled` marker — with the styles
  gone its "borders are the whole truth" premise is false and it would force
  a pointless re-paint. The DB and the review page stay silent — this is
  strictly an mTool-dialect translation at decorate time. Pinned by
  `tests/test_settings_api.py`, `test_run_notes_table_style.py`,
  `test_mtool_notes_exporter.py` (ladder + no-regression pin), and the
  `clipboardFormat`/`clipboard`/`cellFormatting`/`NotesReviewTab` web tests.
- **Numeric notes rows (sheets 13/14, `NumericCellRow`)** show grouped `1,595` at
  rest, raw while focused (`formatGroupedInput` in `web/src/lib/numberFormat.ts`);
  display-only, stored values stay raw.

Full walkthrough: [docs/Archive/NOTES-PIPELINE.md](docs/Archive/NOTES-PIPELINE.md).

### 17. Abstract section-header rows are never writable; agents must not plug residuals

Two coupled defences live in the writer + prompts (added 2026-04-26 after a
Windows run polluted SOPL-Analysis-Function with header-row writes and
catch-all "balancing amount" plugs):

- **Header guard in `tools/fill_workbook.py`**: any row whose col-A cell has
  the dark-navy / mid-blue header fill (`_HEADER_FILL_RGB` in
  `tools/section_headers.py`) is XBRL-abstract. Writes to those rows are
  refused with an actionable error pointing at the leaves below. The
  template summary the agent sees from `read_template()` already labels
  them `[ABSTRACT (section header — do not write)]` (via
  `extraction/agent.py::_summarize_template`).
- **Leaf-preferred-over-header in `_find_row_by_label`**: when the same
  label appears at both a header and a leaf row in the same sheet (the
  "Other fee and commission income" case on SOPL-Analysis), the writer
  picks the leaf. Header detection is **row-based** — the legacy
  label-set form falsely marked any leaf with the same text as a header.
- **No-residual-plug rule in `prompts/_base.md`, `prompts/sopl.md`, and
  `prompts/reviewer.md`**: catch-all rows ("Other …",
  "Miscellaneous …", "Administrative expenses") are for genuinely coarse
  entity disclosures only. Agents must NEVER plug a residual into them
  to balance verify_totals or run_cross_checks. If the breakdown can't
  reconcile, leaf rows stay empty and the run finishes with a flagged
  imbalance — that is correct behaviour.
- **Verifier feedback is non-directive**: `tools/verifier.py` SOFP
  imbalance feedback carries the diagnostic ("equity+liabilities side is
  lower than assets") AND an explicit "do NOT plug a catch-all row".
- **Formula-row and wrong-column refusals are distinct.** A formula on an
  entirely non-writable row is audit-only because verification owns its inputs
  and result. A formula in a non-entry total column on an otherwise writable
  row (for example MFRS SOCIE column M) is an agent locator error and remains
  unresolved until corrected, so it continues to block a clean save.

Pinned by `tests/test_template_reader.py::test_abstract_rows_marked_in_sopl_analysis`,
`tests/test_fill_workbook_abstract_guard.py`,
`tests/test_prompt_residual_plug_rule.py`, and
`tests/test_verifier_feedback_wording.py`. Removing any of these defences
without updating the matching test will fail CI loudly.

**MPERS parity (2026-04-26):** the abstract-row guard works on MPERS too.
`scripts/generate_mpers_templates.py::_apply_abstract_row_styling` paints
the same dark-navy `1F3864` fill + white bold font that MFRS uses, on
every row whose underlying SSM concept ends in `Abstract`. Don't drop
this when editing the generator's `_apply_*_sheet_layout` helpers —
without it the guard silently no-ops on MPERS and the SOPL-Analysis
header-pollution failure mode returns. Pinned by
`tests/test_template_reader.py::test_mpers_templates_carry_header_fills_like_mfrs`
and the end-to-end
`test_writer_refuses_abstract_writes_on_mpers_sopl_analysis`.

### 18. Iteration caps must stay below pydantic-ai's silent 50-cap

`agent_tracing.MAX_AGENT_ITERATIONS` was lowered from 50 to **40** on
2026-04-27 during the stop-and-validation visibility work.
The 2026-04-26 user-reported incident — terminal traceback
`pydantic_ai.exceptions.UsageLimitExceeded: request_limit of 50` — was
caused by our cap racing pydantic-ai's silent default and losing.
Pydantic-ai 1.77's `UsageLimits.request_limit=50` fires from inside
its own `check_before_request`, bypassing the structured "Hit
iteration limit" path our coordinators emit.

The buffer (40 vs 50) absorbs pydantic-ai's per-iteration request
overhead. Operators can override via `XBRL_MAX_AGENT_ITERATIONS` but
**must not raise it to ≥50** — pinned by
`tests/test_max_agent_iterations_below_pydantic_cap.py`.

The reviewer pass has its own dynamic tool-turn cap (16-40 since the
2026-08-25 reviewer completion hardening; RUN-REVIEW P0-1). It is enforced
through `AgentLoopSpec.call_tools_cap`, stays below PydanticAI's 50-request
default, and fires structured `correction_exhausted` outcomes via
`server._run_reviewer_pass`. The notes reviewer uses the same enforced cap.
(The legacy `_run_correction_pass` was removed in rewrite Phase 1.1.)

**Wall-clock deadline behaviour (run-83 hardening, 2026-08-05):** the
cap in `agent_runner.run_agent_loop` stops NEW MODEL THINKING only — a
CALL-TOOLS node the model already issued executes past the deadline
(bounded by the per-turn timeout; writes still pass their deterministic
guards), and the END node is never discarded. Run 83's reviewer lost a
fully-formed 3-fix correction batch to the old raise-before-any-node
check. Companion soft deadline: `run_agent_loop` publishes
`deps._wallclock_started` / `_wallclock_cap`, and `limit_warner.py`
(now ALWAYS registered on the reviewer factory) injects a wrap-up
warning past 70% / CRITICAL past 90% of the cap. Pinned by
`tests/test_agent_loop_wallclock.py`, `tests/test_limit_warnings.py`,
`tests/test_reviewer_compact_context.py`.

For reviewer runs, the warning reports the same tool-turn unit shown in the
prompt. Graph-node counters remain the fallback for other agent roles. Do not
show the reviewer a graph-step budget that disagrees with its prompt.
An explicitly published token budget of `0` disables both the hard cap and its
warning; it must not fall back to a nonzero environment default.

**Wall-clock cap on correction (2026-04-27):**
`CORRECTION_WALLCLOCK_TIMEOUT = 300.0` in `server.py` is
defence-in-depth on top of the dynamic turn cap and the 180s per-turn
timeout. It catches the slow-LLM scenario where many quick-but-not-
quick-enough turns add up past 5 minutes total without either of the
finer-grained guards firing. Override via `XBRL_CORRECTION_WALLCLOCK_S`
(positive seconds; 0 disables). `NOTES_VALIDATOR_WALLCLOCK_TIMEOUT`
(legacy name) is the same defence for the notes-reviewer pass — the pass
inherited the old validator's constants and pseudo-agent id when it
replaced it (gotcha #22).

### 19. Pipeline-stage + cross-check progress events

Two new SSE event families surface the post-extraction silent dead
zones (added 2026-04-27, Phases 5 & 6 of the same plan):

- **`pipeline_stage`** — coordinator-level stage label, one of
  `scouting | reading_source | transcribing_source | extracting | merging |
  cross_checking | reviewing | re_checking | reviewing_notes |
  formatting_notes | done`. Emitted at every phase boundary in
  `run_multi_agent_stream`. The frontend captures the latest stage
  and labels the corresponding silent gap ("Notes reviewer fixing…",
  "Re-running cross-checks…"). The notes pass emits `reviewing_notes`
  (the old `validating_notes` label is retained in the frontend
  `PipelineStage` union for older in-flight streams). Both must stay in
  sync — `web/src/lib/types.ts` + `web/src/pages/ExtractPage.tsx`. Pinned by
  `tests/test_pipeline_stage_events.py`, `tests/test_pdf_sidecar_wiring.py`,
  and `web/src/__tests__/PipelineStages.test.tsx`.
- **`cross_check_start` / `cross_check_result` / `cross_check_complete`**
  — per-pass progress for each cross-check run. ValidatorTab fills
  rows incrementally instead of waiting for `run_complete`. Two
  passes labelled `phase: "initial"` and `phase: "post_correction"`;
  the post-correction events overwrite the initial-pass results in-
  place because the user cares about latest state, not history.
  Pinned by `tests/test_cross_check_progress_events.py`.

Both event families are pushed to the same `event_queue` the agents
use, then drained through the existing GeneratorExit-tolerant yield
path — never yield directly from the generator outside that pattern
(it breaks the disconnect-finalization contract; see the 2026-04-27
fix in `_emit_stage`).

**Two rules on the cross-check results list (peer review, 2026-08-05):**

- **The initial pass COMMITS.** Its rows are written before the reviewer so a
  Stop-All keeps the failing-check diagnosis, and that only works if the write
  is committed: the cancel path rolls back. Leaving it pending also deadlocks
  the reviewer — SQLite allows one writer, and the reviewer's very next act is
  `ensure_snapshot` with `BEGIN IMMEDIATE` on its OWN connection, which blocks
  for `busy_timeout` and raises `database is locked`, reported as
  `snapshot_failed`. Pinned by
  `tests/test_cross_checks_persist_before_reviewer.py`.
- **Every advisory goes through `server._run_notes_advisories`, never a direct
  call in a pipeline block.** The post-reviewer re-run REPLACES
  `cross_check_results` wholesale and the final persistence writes whatever
  that list holds, so an advisory computed in only one of the two blocks is
  deleted the moment the reviewer makes a fix (how the run-84 SOCF
  section-placement warning disappeared). The aggregator imports
  `check_notes_consistency` OUTSIDE its `try` — the except is there for a
  check that raises, and must not also absorb a missing symbol into an empty
  list that reads as "nothing to warn about". Pinned by
  `tests/test_socf_section_placement.py`.

### 20. Silent post-extraction failures are now structured SSE errors

Two paths used to swallow errors silently (2026-04-27 fix):

- **Merge failure** → `event: error` with `data.type =
  "merge_failed"` carrying the `MergeResult.errors` list. The success
  path already covers itself via `run_complete`; the failure path
  used to log + continue silently.
- **Cross-check exception** → wrapped in try/except. Emits
  `data.type = "cross_check_exception"` carrying the class name +
  message, falls back to empty results, and lets `run_complete` still
  fire. Run lands as `completed_with_errors` instead of crashing the
  whole pipeline. Pinned by
  `tests/test_silent_exception_surfacing.py`.

### 21. Canonical concept model — the MANDATORY pipeline

The `concept_model/` subsystem (parser, importer, exporter, cell resolver,
cascade recompute, group checks, facts API, versioning) plus the **reviewer
agent** (`correction/reviewer_agent.py`, `prompts/reviewer.md`) is the **only**
extraction → review → export pipeline. It is MANDATORY (first-principles rewrite
Phase 1.1): the legacy direct-xlsx pipeline, the `XBRL_CANONICAL_MODE` opt-out,
`correction/agent.py`, and `correction/canonical_agent.py` were all deleted, and
there is **no fallback** — if the startup concept-tree bootstrap fails, a run
fails fast (`_CANONICAL_BOOTSTRAP_OK is False` → `_fail_run`). Fix the bootstrap
(check logs, restart) rather than looking for an opt-out that no longer exists.

**Template re-import lifecycle (schema v46).** Template IDs remain stable
across startup imports, but row moves and label changes mint new concept UUIDs.
`concept_model.importer.import_template` therefore retires the prior
`concept_nodes` membership and reactivates only UUIDs present in the latest
parse, atomically in the import transaction. Retired nodes are never deleted:
existing `run_concept_facts` continue to join to the exact UUID stored by the
historical run. Current cell/label resolution and current target generation
must filter `is_current = 1`, as must current cross-check and eval catalogues.
Historical fact reads remain deliberately unfiltered; the run Concepts API
includes a retired node only when that exact run still references its UUID.
Pinned by `tests/test_concept_import_lifecycle.py` (rename, move, removal,
current-consumer filtering, and historical readability),
`tests/test_concepts_routes.py`, and `tests/test_db_schema_v46.py`
(fresh schema plus idempotent v45 migration).

- **Extraction:** `coordinator.py` threads `run_id` + `db_path` into the
  extraction tools so writes project into `run_concept_facts` live.
- **Export:** `_export_canonical_workbooks` (server.py) re-renders each succeeded
  statement from `run_concept_facts` via
  `concept_model/exporter.py::export_run_to_xlsx`, then merges — the download
  reflects DB facts, not the scratch xlsx. Falls back to the agent workbook
  per-statement when an export applies zero facts.
- **Review — the REVIEWER pass** (`server.py::_run_reviewer_pass`): investigates
  the root cause of failing cross-checks + open conflicts down the face→sub→PDF
  chain, applies grounded fixes through the guarded `apply_fixes` tool (a
  deterministic no-plug guard refuses ungrounded writes and plugs into
  catch-all/abstract rows — invariant #17), and raises only
  `stuck`/`disputes_prior` flags. Safety is **versioning, not write-gating**:
  `concept_model/versioning.py::snapshot_facts` runs ONCE before any write so
  "Revert to original" (`revert_to_original`) restores the extraction in one
  click; the pass then re-exports + re-merges (no xlsx split-brain) and emits the
  `reviewing` stage.
  - **Group / MPERS scoping.** `concept_nodes` holds every imported
    standard×level and uuids are minted per `(template_id, sheet, row, label)`,
    so the same `(sheet, row)` exists under each family with different uuids. The
    reviewer's `(sheet,row)` resolution (`_resolve_concept` /
    `trace_cascade_source`) MUST be scoped to the run's family via a
    `template_prefix` (`"{standard}-{level}-"`) — so `ReviewerDeps` threads
    `filing_standard`, not just `filing_level`. On Group filings both
    `entity_scope`s exist (tools default to Company), so the packet surfaces each
    check's `[group]`/`[company]` tag as an `entity_scope` hint the reviewer must
    honour. Pinned by `tests/test_reviewer_tools.py`,
    `tests/test_reviewer_versioning.py`.
  - **Auto-trigger toggle `XBRL_AUTO_REVIEW`** (default on) gates the automatic
    launch on the failure path; off = the user triggers it manually.
  - **Clean-run spot-check `XBRL_SPOT_CHECK`** (default on, independent of
    `XBRL_AUTO_REVIEW`): a run with no failing checks / open conflicts still gets
    a grounded sanity pass, reusing `_run_reviewer_pass` via a `spot_check` arg.
    `XBRL_SPOT_CHECK_MODE` picks depth — `light` (default, `prompts/spot_check.md`
    + a 6/8-turn cap) or `full` (holistic `reviewer.md`). A spot-check that merely
    exhausts its cap is advisory (doesn't flag a clean run), but one that FAILS to
    run (`reviewer_failed`) tips the run to `completed_with_errors`. Suite default
    OFF (`tests/conftest.py`). Pinned by `tests/test_reviewer_pipeline.py`,
    `test_e2e.py`, `test_reviewer_agent.py`, `test_settings_api.py`.
  - **Reviewer model** is user-selectable: `XBRL_DEFAULT_MODELS["reviewer"]`
    (Settings) for the auto pass, a per-request `model` override from the Review
    tab for `/re-review`; both fall back to the run's extraction model
    (`reviewer` ∈ `_AGENT_ROLES`).
- **Frontend:** the **Review** tab (`web/src/components/ReviewTab.tsx`) + Values
  tab + `/concepts/{id}` alias show whenever `/api/config` reports
  `canonical_mode: true`. Reviewer API: `GET /review`, `POST /flags/{id}/answer`,
  `POST /re-review`, `GET /re-review/status`, `POST /revert-to-original`.
  - **Manual re-review is async** (a pass runs minutes): `POST /re-review` only
    LAUNCHES it on a dedicated thread with its own event loop, tracked in the
    durable `run_review_tasks` table (v13) keyed by run_id, and returns
    immediately; the Review tab polls `GET /re-review/status`. A dedicated thread
    (not `asyncio.create_task`) survives request teardown. A re-entrancy guard
    prevents a double-launch over the same facts, so the initial `running` write
    is **mandatory** — `re_review` writes it directly and returns **503** if it
    fails (no thread started); only the terminal `done` write is best-effort.
    `server._lifespan` calls `repo.reconcile_stale_review_tasks` at startup to
    retire rows left `running` by a crash. Pinned by
    `tests/test_reviewer_routes.py`, `tests/test_db_schema_v13.py`.

**Cross-sheet rollup linkage (schema v11, "render twice"):** a face row that
pulls its value from a sub-sheet total via a cross-sheet formula
(`='SOFP-Sub-CuNonCu'!Bn`) shares ONE `concept_uuid` with the sub-sheet `*Total`
row. `concept_render_aliases` preserves the face render coord alongside the sub
coord instead of dropping it at importer dedup. Consequences: the importer builds
its `coord→uuid` edge map from the FULL concepts list (not the dedup'd set) so
face COMPUTED rows still wire child edges; `cell_resolver` falls back to the alias
table on a face-coord write; the concepts endpoint emits one extra `is_alias:true`
view-row (read-only "(linked)" in `ConceptsPage.tsx`); and the **exporter never
writes alias coords**, so the workbook's cross-sheet formula stays live and Excel
recomputes. Pinned by `tests/test_db_schema_v11.py`,
`tests/test_canonical_cross_sheet_rollup.py`, `tests/test_concepts_routes.py`.

**Fact → cell routing (exporter):** one `concept_targets` lookup per fact for
every filing shape (the importer precomputes a target row per rendered dimension:
`import_company_targets` = Company B=CY/C=PY, `import_group_targets` = Group
B/C/D/E, SOCIE matrix inline). Result: CY→B, PY→C, Group facts dropped on a
Company filing, and aliases are never targets (so formula cells stay live). An
in-scope fact with no precomputed target RAISES (importer-bug signal). Tests that
hand-roll a Company DB must call `import_company_targets(db, template_id)` after
`import_template`. Pinned by `tests/test_canonical_export.py`,
`tests/test_phase4_group.py`.

**Canonical validation precedes physical mutation.** The extraction writer
resolves the proposed physical slot, then runs the same scalar-fact validation
used by `apply_fact` before changing either the scratch cell or its evidence.
A rejected canonical value therefore cannot survive only in the workbook and
later pass `save_result`; it remains an unresolved write until corrected.
Pinned by
`tests/test_extraction_canonical_projection.py::test_write_facts_rejects_canonical_invalid_value_before_workbook_save`.

### 22. Agent workbook tools must serialise + atomic-save shared files

pydantic-ai (1.77+, default `parallel_execution_mode`) runs batched
`@agent.tool` calls as concurrent `asyncio` tasks; **sync** tools dispatch
onto separate anyio worker threads. openpyxl's `wb.save()` is a non-atomic
in-place zip rewrite — if a second tool's `load_workbook` hits the same path
mid-save it reads a truncated zip → `EOFError` (Windows incident,
2026-05-29). Any agent tool that loads + saves the **same** workbook path is
exposed.

The race was first hit + fixed on the notes post-validator (a
load+save-in-place agent, since **deleted** — its cross-sheet
reconciliation job moved to the notes reviewer, which writes only the DB,
never the xlsx, so it can't reproduce this). The fix pattern — a per-run
`threading.Lock` io_lock around every load/save plus a tempfile +
`os.replace` atomic save (atomic on Windows + POSIX) so even an un-locked
reader sees old-or-new, never partial — is the shape now shared everywhere.

**Closed everywhere (2026-06-12, PLAN-orchestration-hardening item 8):** the
helper was promoted to `utils/workbook_io.py::atomic_save_workbook` and
every live-path saver now routes through it — `tools/fill_workbook.py`,
`concept_model/exporter.py`, `notes/writer.py`, `workbook_merger.py`
(`tools/recalc.py` and `notes/persistence.py` already used tmp+replace
shapes). Pinned by `tests/test_workbook_io_atomic.py`. If you add a NEW
tool that writes a workbook another tool reads, use the shared helper —
never a bare in-place `wb.save(path)`.

### 23. Gold-standard eval — gold is facts, scoped by template SET

The `eval/` subsystem (schema v16) scores a run's extraction against a
benchmark's human-verified gold answers. Gold lives in `gold_concept_facts`,
the SAME shape as `run_concept_facts` (keyed by `concept_uuid + period +
entity_scope`); grading (`eval/grader.py::grade_run`) is a set join on that key,
so the score is exact, not a brittle cell-diff (sidesteps gotcha #4).

Load-bearing invariants:

- **Scope by the benchmark's explicit `template_id` SET, never a
  `{standard}-{level}-` prefix.** `template_id` encodes the variant
  (`...-sofp-cunoncu-v1` vs `...-sofp-orderofliquidity-v1`); uuids differ per
  variant (gotcha #21). `eval_benchmark_templates` holds the set;
  `eval/ingest.py` + `grade_run` both filter `template_id IN (set)`.
- **Grade LEAF / MATRIX_CELL only.** COMPUTED totals are Excel-formula-derived
  and excluded so they can't inflate the score. Grading keys on
  `concept_uuid`, so cross-sheet alias coords (one uuid, two render coords —
  schema v11) are counted once.
- **Score = `matched / gold_cells`** where `gold_cells = matched + missing +
  mismatch`. `extra_cells` (run filled a gold-blank leaf) + `scale_mismatch`
  (`run == gold·10^k`) are **flags, NOT in the denominator** (open question:
  whether extras should move the headline). `not_disclosed` gold is excluded
  from the denominator and a run value there is ignored; `explicit_zero` gold
  grades as numeric 0.
- **Ingestion reuses `cell_resolver.resolve_cell`** — no new mapping logic. A
  workbook matching no benchmark template is rejected loudly (`ValueError`); so
  is a workbook that matches sheets but yields **zero gold cells** (a useless
  0/0 benchmark — `eval/store.create_benchmark_from_workbook` raises → 422).
- **Two ways to author gold; prefer seeding from a run (2026-06-05).** Upload
  ingest reads `openpyxl(data_only=True)`, which returns `None` for any
  formula cell with **no cached value** — exactly the state of a freshly
  machine-exported workbook (the SOCIE matrix + cross-sheet face rollups are
  live formulas, computed only when Excel opens the file). So uploading an
  un-recalculated export silently drops most sub-sheet/matrix leaves (the
  2026-06-05 incident: gold seeded from `run_159_filled.xlsx` captured 64 of
  102 facts, SOCIE collapsing 42→6). `ingest_workbook` now COUNTS those lost
  gradeable cells (`IngestResult.skipped_formula_cells`) and surfaces a
  `warning` in the create response. The lossless path is
  `eval/store.create_benchmark_from_run` (`POST /api/benchmarks/from-run`):
  it copies `run_concept_facts` (LEAF/MATRIX_CELL, scoped to the templates the
  run wrote) straight into `gold_concept_facts`, bypassing the xlsx round-trip
  entirely. Only seedable from a **complete** terminal run (`completed` /
  `completed_with_errors`) — draft/running/failed/`aborted` (Stop-All partial
  merge) are refused. It also re-rejects the **0/0 gold** the workbook path
  guards (a run whose gradeable facts are all `not_disclosed`/blank copies rows
  but grades 0/0 — the reject uses grader-equivalent denominator semantics, not
  the raw copied-row count). Hand-correct values afterwards in the gold
  editor. Pinned by `tests/test_eval_from_run.py`,
  `test_eval_ingest.py::test_ingest_counts_uncached_formula_cells_as_warning`,
  and `test_eval_routes.py::test_create_benchmark_from_run_endpoint`.
- **Run-start validates the attached benchmark** (`_validate_and_build_run`):
  it must exist and its `filing_standard`/`filing_level` must match the run, or
  the run fails fast (config error, before extraction — not a soft skip). This
  only catches standard/level + existence; it **cannot** verify the uploaded
  PDF is the benchmark's document, because two same-`(standard, level)`
  benchmarks share `template_id`s/uuids — picking the wrong *document's*
  benchmark still grades against the wrong gold. That's inherent user
  responsibility (like uploading the wrong PDF), not a validatable condition.
  The extract-page picker filters to matching benchmarks and clears a stale
  selection on a standard/level switch to make the mismatch hard to hit.
- **Grading fires at run completion, after the reviewer + re-export/re-merge**
  (`server._grade_run_against_benchmark`), gated on `runs.benchmark_id`, wrapped
  in try/except (a grading failure never changes the run's terminal status —
  gotcha #20). Emits an `eval_score` SSE event.
- **Frontend reuses, never re-implements.** The gold editor is `ConceptsPage`
  with a `source='benchmark'` prop (NOT a component extraction); the Eval tab,
  Benchmarks page, extract-page toggle, and History score column are additive.
- **COMPUTED totals are derived on-read for DISPLAY, never persisted as gold.**
  Gold stores only leaves (ingest skips COMPUTED), so the gold editor's total
  rows would render blank. `eval/store.gold_display_totals` re-derives them from
  the gold leaves at query time (edge-sum + blank-child semantics mirroring the
  run cascade, minus the conflict machinery) and `benchmark_concepts` merges
  them into `value` + `scope_facts`. It writes nothing — grading stays
  leaf-only and unaffected; a coordinate already carrying a gold value (e.g. an
  ingested SOCIE MATRIX total) wins over the re-derivation. There is NO
  gold-side equivalent of `concept_model/cascade.py` (which is `run_id`-only).
  Pinned by `test_eval_ingest.py::test_benchmark_concepts_derives_computed_totals_from_gold_leaves`.

Pinned by `tests/test_db_schema_v16.py`, `test_eval_grader.py`,
`test_eval_ingest.py`, `test_eval_routes.py`, `test_eval_wiring.py`, and the
`BenchmarksPage` / `EvalTab` / `ConceptsPage` / `HistoryList` / `PreRunPanel`
frontend tests. Full plan: docs/PLAN-eval-benchmark.md.

### 24. Auth layer gates every `/api/*` route (schema v18)

The `auth/` package (`config`, `middleware`, `sessions`, `lockout`,
`passwords`, `routes`, `manage`) + `web/src/pages/LoginPage.tsx` add
email+password login (PLAN-azure-auth-deployment Phase 1). The DB side is
gotcha #11 (v18 `auth_users` / `auth_sessions`); the operational invariants:

- **`AUTH_MODE=dev` is required to run the test suite.** The middleware guards
  **every** `/api/*` route (exempt: prefix `/api/auth/*`, exact `/api/health`).
  `tests/conftest.py` defaults the whole suite into `AUTH_MODE=dev` (auto-session
  as `dev@localhost`, no login form) so pre-auth tests don't 401; auth-specific
  tests opt OUT with `monkeypatch.delenv("AUTH_MODE")`. **Running pytest with
  `AUTH_MODE` unset makes API-hitting tests 401.**
- **Production fails fast on misconfig.** `SESSION_SECRET` is mandatory in prod
  (startup refuses to boot without it; dev falls back to an insecure constant).
  A startup guard also **refuses to boot in `AUTH_MODE=dev` under production**
  (`WEBSITE_SITE_NAME` present) so dev-mode can never ship to Azure.
- **Sessions are server-side + revocable** (`auth_sessions` row, not a stateless
  JWT) with a **15-min sliding idle timeout** (`AUTH_IDLE_TIMEOUT_S`); the SPA
  keeps it alive via `/api/auth/refresh`. Brute-force lockout is per `(email, IP)`
  — 5 attempts → 15-min lock (`AUTH_LOGIN_MAX_ATTEMPTS` / `AUTH_LOGIN_LOCKOUT_S`).
- **Accounts = the email allowlist.** Provision with
  `venv/bin/python -m auth.manage add-user you@firm.com --name "Your Name"`
  (add `--admin`
  to mint an admin). There is no self-signup. Azure provisioning is still TODO.
- **Admin role + web user management (schema v20).** `auth_users.is_admin` is the
  privilege boundary. The CLI gained `--admin` / `make-admin` / `revoke-admin`
  (with a **last-admin guard** — refuses to demote/disable the only enabled
  admin); admin #1 is minted there since the admin UI is admin-gated. Web side:
  `/api/auth/me` reports `is_admin`; `/api/admin/users` (list/add/disable/enable/
  reset-password/promote) each independently enforce `is_admin` server-side via
  `_require_admin` (the hidden UI tab is NOT the boundary) and carry the same
  409 last-admin guard; `/api/auth/change-password` is self-service (re-auths
  with the current password). Frontend: the gear opens a consolidated **`/settings`
  page** (`SettingsPage.tsx`, `AppView "settings"`) with three tabs — **General**
  (the old model/proxy/run-defaults form, extracted into `GeneralSettingsForm`;
  `SettingsModal` is now a thin wrapper around it), **Account** (change password),
  **Users** (admin-only). Pinned by `tests/test_admin_routes.py`,
  `test_change_password.py`, `test_auth_me_reports_admin.py`,
  `test_db_schema_v20.py`, and `web` `SettingsPage`/`AccountTab`/`UsersTab` tests.

Pinned by `tests/test_auth_middleware.py`, `test_auth_password.py`,
`test_auth_sessions.py`, `test_auth_lockout.py`,
`test_auth_prod_requires_users.py`, `test_manage_users.py`,
`test_db_schema_v18.py`.

### 25. Fact-based verification (item 32) — both flags DEFAULT ON

The Excel-free verification path reads `run_concept_facts` (by `concept_uuid`)
instead of opening workbooks. Two independent flags, **both default ON**, read
at call time so tests can toggle them:

- **`XBRL_FACT_BASED_CHECKS`** (default on; `server._fact_based_checks_enabled`):
  cross-checks read facts via `run_all_facts` instead of `all_workbook_paths`.
  Scoping stays variant-precise via `_build_check_template_ids` (gotcha #21).
  Set `=0` to fall back to the xlsx path.
- **`XBRL_FACT_BASED_VERIFY`** (default on; `tools/verifier._fact_based_verify_enabled`,
  with `tools/verifier_facts.py`): the verifier reads facts instead of xlsx
  formula-eval. Set `=0` to fall back.

The xlsx formula-eval path **remains present and authoritative** as the fallback
until Phase 4 (xlsx retirement) lands — it is NOT removed yet. Export still
keeps live formulas (downloads recompute in Excel); item 32 is verification-only,
no static-value export. Plan: docs/PLAN-orchestration-hardening (item 32).

### 26. Scanned-PDF → readable-document — REMOVED

The `docconvert/` package + "Readable Doc" page (an offline Docling-based
scanned-PDF → HTML/Word converter) was removed (docs/PLAN-deprecate-docconvert.md),
along with its heavy deps (`docling`, `torch`, `onnxruntime`, `rapidocr`,
`easyocr`, `pypandoc_binary`, `python-docx`) and the `models/` weight bundle. The
`doc_conversions` table (v21) stays as an inert artifact (gotcha #11); no code
reads it.

### 27. Notes coverage checklist — post-reviewer visibility + status tipping

A holistic, human-visible **coverage checklist** reconciles every top-level
note in the scout inventory against WHERE its content landed across ALL notes
sheets (docs/PLAN-notes-coverage-and-routing.md). Two coupled hardenings: the
checklist, and a **top-line routing rule** (notes stay whole; only
explicitly-labelled material/significant accounting-policy sections carve out
to the policies sheet — enforced by prompt tiers + `detect_topline_splits`).

Load-bearing invariants:

- **Pure builder, gotcha-#14-safe.** `notes/coverage_checklist.py::
  build_draft_checklist(inventory_rows, provenance_entries, …)` keys ONLY on
  integer note numbers + sub-ref STRINGS from `source_note_refs` provenance —
  never content matching. Content judgement (is sub-section (b) really in the
  cell?) is the reviewer's job. Statuses: `placed` / `missing` / `skipped` /
  `suspected_gap` (INTERNAL numbering holes only — before-first / after-last is
  the documented blind spot). `skipped` remains in the persisted/API vocabulary
  for older runs. Current Sheet-12 skip receipts are routing CLAIMS stored in
  `{output_dir}/notes12_skips.json`, loaded by both the reviewer context and the
  server finalizer via `coverage_checklist.load_notes12_skips`. A claim clears
  coverage only when `source_note_refs` provenance shows the note placed on a
  notes sheet. With destination provenance the row is `placed`; without it the
  row is unresolved `missing`, retaining the claimed reason. This prevents a
  same-agent receipt from proving its own cross-sheet hand-off. An empty
  inventory yields `inventory_available=False` (loud, never empty-but-green).
  Only a grounded reviewer resolution (`not_applicable`/`confirmed_absent`) is
  removed from the raw `coverage_gaps` detector family; a bare skip claim is not.
- **The human sees the POST-reviewer checklist.** The draft is a reviewer
  INPUT only. The notes reviewer auto-resolves every non-placed row via two
  grounded tools (`resolve_coverage_notes` → `confirmed_absent`/`not_applicable`;
  `verify_subnotes` → `verified`/`missing`) accumulated on `NotesReviewerDeps`;
  the FINAL checklist merges those verdicts + reviewer-authored notes. **The
  coverage + clear tools are list-only (`resolve_coverage_notes` /
  `verify_subnotes` / `clear_note_cells`)** — each applies a list in ONE tool
  call (a single item is a one-element list) under the same grounding +
  once-per-pass snapshot latch, so the reviewer never burns one turn per row/ref
  (which was timing the pass out against the 300s wallclock —
  `notes_reviewer_wallclock_exceeded`). The 2026-07-07 change added the batch
  forms; the singular `resolve_coverage_note` / `verify_subnote` /
  `clear_note_cell` variants were removed 2026-07-07 (agent-tool consolidation)
  since they had the identical activation scenario. Coverage tools now carry
  per-item verdict, reason, and source pages: one `verify_subnotes` call can
  cover multiple note numbers and mixed verified/missing outcomes, and one
  `resolve_coverage_notes` call can cover mixed not-applicable/confirmed-absent
  outcomes without weakening grounding or failure isolation. Pinned by
  `tests/test_notes_reviewer_coverage.py`. The pass
  recomputes + persists on EVERY exit path (`_finalize_coverage` in
  `server._run_notes_reviewer_pass`): success → `reviewed`; crash/construction
  failure → `not_reviewed` draft; empty inventory → `inventory_unavailable` +
  a structured warning event. Manual re-review re-persists for free (same pass).
- **A grounded human flag is a terminal disposition, not detector deletion.**
  Every packet finding carries a stable id. `raise_flag` may settle that exact
  original finding only when it supplies the id plus PDF pages viewed in the
  current pass; the unchanged detector result then reads as sent to human
  review rather than STILL open. A flag without an id remains advisory, and a
  finding introduced by the reviewer's own edits can never be flag-cleared.
  The id, source pages, and evidence persist in `notes_review_flags` (schema
  v45). A persistence failure is a structured reviewer failure and cannot
  finish clean after the model was told the finding was handled. Pinned by
  `tests/test_notes_reviewer_self_verify.py`,
  `tests/test_notes_reviewer_pipeline.py`, and `tests/test_db_schema_v45.py`.
- **Automatic face and notes review overlap.** Once the initial cross-check
  rows and notes-review inputs are committed, both reviewer tasks launch on
  the same event loop and share the existing SSE queue. They write separate
  canonical stores (face facts versus `notes_cells`). Their paid model work
  overlaps, but the notes pass waits on a per-run event-loop gate before its
  final flags/checklist replacement; SQLite has one writer and the face
  reviewer's post-pass cascade can hold that lock beyond the ordinary busy
  timeout. The automatic notes
  reviewer receives no merged-workbook path while they overlap; its notes
  overlay is applied atomically only after both reviewers finish, so it cannot
  race a face re-export/re-merge. Manual notes review keeps its immediate
  refresh. Stop All settles both task rows and releases the durable notes-task
  interlock. Pinned by `tests/test_reviewer_parallel_wiring.py`.
- **Coverage tips run status.** An unresolved `missing` row / uninvestigated
  `suspected_gap` / unavailable inventory tips the run to
  `completed_with_errors` (`_notes_coverage_tips_status`, folded into the
  overall-status block per gotcha #10 — never a second writer). `not_verified`
  sub-refs warn only. The reviewer skip gate uses `count_open_items` (detector
  families + unresolved checklist rows) so a suspected-gap-only run still runs.
- **Persistence + API.** Durable in `notes_coverage_rows` (schema v28) — one
  top-level row per note + per-sub-ref child rows + a `note_num = -1` banner
  sentinel (distinguishes `inventory_unavailable` from `pre_feature`).
  `GET /api/runs/{id}/notes-coverage` nests children under parents + derives the
  summary. `web/src/components/NotesCoveragePanel.tsx` is a Notes-tab SECTION
  (not a `role="tab"` — gotcha #7), placement chips dispatch a
  `notes-coverage-focus` window event.
- **Reviewer clears preserve routing precision.** `clear_note_cells` refuses to
  remove the last provenance placement of a note. It also refuses to clear one
  List-of-Notes row while that note remains in a different row on the same
  sheet. Deterministic code cannot decide which MBRS disclosure label is more
  precise without violating the all-LLM-judgement rule, so the reviewer keeps
  both rows and raises `needs_human`. Cross-sheet duplicate clears remain
  available when another placement survives; a reviewer-authored cell may be
  cleared as an explicit same-pass undo. Pinned by
  `tests/test_notes_reviewer_coverage.py`.
- **Kill switch:** `XBRL_NOTES_COVERAGE` (default ON; `/api/settings` +
  `/api/config`; suite default OFF in `tests/conftest.py`, like spot-check).
  Rollback is a config flip — the table stays as an inert artifact.

Pinned by `tests/test_coverage_checklist.py`,
`tests/test_notes_reviewer_coverage.py`,
`tests/test_notes_coverage_run_status.py`, `tests/test_notes_coverage_api.py`,
`tests/test_notes_detectors_splits.py`, `tests/test_db_schema_v28.py`, and the
`NotesCoveragePanel` web tests.

### 28. mTool fill pipeline — semantic addressing, one patcher, receipts

The `mtool/` package fills a run's figures into an SSM **mTool** MBRS template so
the operator can Validate/Generate the XBRL inside mTool without hand-copying
(docs/PLAN.md, docs/PLAN-mtool-fill-pipeline.md, docs/MTOOL-ZIP-RECON-BRIEF.md).
Proven end-to-end. The whole path is **Excel-free** (pure zip/XML surgery), so it
runs server-side and in the cloud.

**History (2026-08-05 replay):** the v2 hardening (commit b04b178, 2026-07-28)
was reverted wholesale by 7140f59 on 2026-08-01 and re-applied on 2026-08-05 —
minus its `XBRL_MTOOL_FILL` exposure gate, dropped by product-owner decision.
There is NO exposure gate: the filing safety is the preflight + the
report-acknowledgment flow below, not hiding the feature.
`tests/test_mtool_preflight.py::test_no_exposure_gate_exists` pins the gate's
absence (routes live with no flag, no `mtool_fill` key in `/api/config`, no
`server._mtool_fill_enabled`).

Load-bearing invariants:

- **`offline_fill.py` is a single stdlib-only file** (zipfile/re/ElementTree — no
  openpyxl, no repo imports) because it also travels to the enterprise Windows box
  as one script. Reading parses XML; **writing is targeted text edits** — openpyxl
  load/save corrupts the mTool package and full reserialization breaks namespaces.
  Prefixed sheet XML (`<x:sheetData>`) aborts loudly. Do NOT add a third-party dep
  or repo import (a test asserts this).
- **One patcher, no fork.** The server endpoint imports `offline_fill.fill_workbook`
  — the SAME function the CLI runs. Never reimplement patching in `api/`.
- **Exporter emits data-entry LEAF and MATRIX_CELL facts**
  (`exporter.build_fill_doc`): ABSTRACT headers, COMPUTED totals and SOCIE cells
  with formula dependency edges are excluded because mTool owns totals. Each
  write carries the taxonomy primary concept, sorted dimensions, period, scope,
  and the canonical target hint. Scoped to the run's `{standard}-{level}-`
  family, deduped by `(concept_uuid, period, scope)`, reads
  `run_concept_facts` only.
- **One field-semantics contract.** `concept_model/filing_targets.py` is shared
  by extraction, review, persistence, and filing. Taxonomy capability
  (`taxonomy_concepts`) and physical workbook slot role (`template_slots`) are
  separate. Only reportable primary items on `INPUT` or `MATRIX_INPUT` slots
  are agent-writable. Headers, abstracts, tables, axes, members, line-item
  scaffolding, and formula-owned slots are never offered as writable fields.
  `scripts/audit_template_field_semantics.py --check` pins complete MFRS/MPERS,
  Company/Group, and statement-variant coverage against the committed snapshot.
- **Missing mappings fail closed at filing.** Preflight carries the
  `field_semantics` readiness block. A selected template with no manifest, an
  unresolved slot, or historical content quarantined on a presentation-only
  target blocks unless the operator records the existing audited override.
  Receipts persist taxonomy and manifest versions, readiness, and coverage.
  The named MFRS Issued Capital and Related Party wrapper omissions are the
  only reviewed semantic-alignment exceptions; do not replace them with a
  positional tolerance or weaken confirmation/degraded-report safeguards.
- **Unit-aware translation; the global `scale` multiplier is GONE.** A single
  multiplier had no unit dimension, so a thousands conversion would have
  multiplied share COUNTS (MFRS sheet 13 puts "Number of shares issued" three
  rows above "Amount of shares issued"). `scripts/generate_concept_units.py`
  extracts each concept's declared XBRL item type from the SSM taxonomy into a
  committed label-keyed index (`concept_model/concept_units_*.json`, 98% of
  LEAF rows); `mtool/units.py` resolves it and `mtool/translation.py` applies
  versioned manifests. The SHIPPED manifest is identity, and there is
  deliberately no way to pick another one over the wire; any non-identity
  manifest REFUSES an unclassified value rather than passing it through.
  Non-identity conversion stays **Windows-blocked** on recon Addendum A.
  `denomination` is surfaced in the doc meta.
- **Semantic, not physical — taxonomy identity before labels:**
  `concept_semantic_addresses` (schema v40) stores the primary taxonomy concept
  and dimensions derived from the same presentation roles that generate the
  canonical templates. `mtool/template_map.py` is the one forward/reverse
  adapter. It resolves semantic addresses to explicit cells in the uploaded
  workbook; SOCIE uses `ComponentsOfEquityAxis` members. Repository-generated
  templates use their verified exact target hints. Declared semantic identities
  that are missing or ambiguous fail closed. On period/entity sheets,
  address-less legacy writes may still use `column_role` (CY/PY ×
  company/group) plus exact labels, but an unverified template requires column
  confirmation and the resulting artifact is degraded until the operator
  acknowledges it. Category sheets never take that fallback: without one
  exact taxonomy-addressed target they return structured blocked coverage.

  `column_detect` reads mTool's own marker rows — `#PRIM#` (label column),
  `#ENDT#` (period end dates; current vs prior year comes from COMPARING
  DATES), `#UNITSCALE#` (declared unit), `#DOM#` (columns are dimension
  members). `needs_confirmation()` is the gate, not a confidence score: Group
  period/entity layouts and unknown non-dimensional template fingerprints
  (`mtool/known_templates.json`) need a human. Dimensional sheets do not show
  CY/PY inputs: their columns are taxonomy members (share classes or equity
  components), and `template_map.resolve_filing_doc` resolves one exact target
  from the semantic address or blocks. This prevents the sample template's
  Notes-Issuedcapital share-class columns from being mislabeled as current and
  prior years. `exporter.apply_column_map` still fails loudly on a missing
  legacy role.
- **Current compatibility target is mTool 2.2.** Generated-template adapters
  are verified in automated forward/reverse SOCIE round trips across
  MFRS/MPERS and Company/Group. A genuine mTool 2.2 workbook fingerprint and
  Windows Validate/Generate evidence are still required before describing an
  unknown uploaded layout as verified; inspection reports it as a candidate.
- **Preflight is the filing-readiness gate — run status never was one**
  (`mtool/preflight.py`). Blocks on conflicting figures that would REACH the
  workbook, open reviewer flags, and unresolved notes coverage; overriding
  needs a written reason that lands on the receipt. Conflicts on unfileable
  rows warn instead of blocking; `completed_with_errors` alone does not block.
- **Report before file.** `POST /patch` returns the COMPLETE report plus a
  short-lived artifact id; the workbook is a separate GET that a degraded fill
  won't release unacknowledged (enforced server-side — the acknowledgement is
  stamped on the receipt). The old 20-row / 6 KB `X-mTool-Report` header is
  gone.
- **Machine docs are `strict`** (`build_fill_doc` sets `strict:true`): a non-exact
  label is a bug to surface, not a typo to forgive. Hand-authored operator runs
  stay lenient; fuzzy hits are still reported.
- **Canonical note destinations scope mTool matching.**
  `notes_exporter.build_notes_fill_doc` carries each stored note's
  `source_sheet` into `offline_fill.fill_footnotes`. Both existing-`fn_*` and
  create-missing label resolution search only that sheet. A missing sheet or a
  same-sheet tie stays unresolved; it must never fall back to a matching label
  elsewhere in the workbook. Explicit operator key/cell choices still override
  label resolution. Pinned by the source-sheet scope tests in
  `tests/test_mtool_offline_fill.py`.
- **Created note slots REUSE the template's orphan `fn_` pool; the `+FootnoteTexts`
  column-A key is the join key and MUST stay unique** (2026-07-05 Amgen empty-popup
  incident). mTool joins visible cell → payload by that column-A string and reads
  the FIRST match, so a minted key that duplicates a pre-provisioned orphan `fn_N`
  row leaves the popup silently empty (and read-back misses it, because
  `read_footnote_rows` keeps the LAST match — the opposite of mTool).
  `_create_footnote_slot` drains `_build_orphan_pool` first and only appends past
  exhaustion; `_detect_duplicate_fn_keys` (a raw row scan) flags any duplicate into
  `report["errors"]`. Never `replace_shared_string` an EMPTY payload cell (it may
  share a `""` `<si>`); append+patch instead. Pinned by the orphan-pool tests in
  `tests/test_mtool_offline_fill.py`.
- **Every fill writes a receipt** (`mtool_fill_receipts`, schema **v38** —
  re-minted from the reverted build's v35 because source integrity took
  v35–v37): both file hashes, template fingerprint, column map, manifest
  version, preflight verdict + override, degraded-download acknowledgement,
  operator, full report — plus **TWO revision snapshots, because there are
  two reads**. The numeric one (`snapshot_*`, count/digest/max-updated) comes
  from `receipt.snapshot_facts` over `run_concept_facts`. The prose one
  (`snapshot_notes_*`, schema **v39**) comes from
  `notes_exporter.build_notes_fill_doc`, which opens its OWN connection to
  `notes_cells` later in the same request. v38 recorded only the numeric half
  while its docstring claimed to cover both, so a notes edit landing between
  the two reads produced a workbook whose prose no receipt described (peer
  review, 2026-08-05). **A new consumer must not fold them into one digest** —
  a numeric-only fill legitimately has `snapshot_notes_digest = NULL`, and
  that is a different fact from "the prose was empty". **The prose digest
  covers the notes `fill_footnotes` actually WROTE, never the candidate
  list** (`notes_exporter.build_notes_snapshot`, fed from
  `report["footnotes_written"]` indices): a candidate is only an OFFER, and a
  template with no `fn_*` slots resolves none of them while still returning a
  normal degraded report — so hashing the candidates attested to prose that
  was never in the workbook (peer review, 2026-08-05; the first fix keyed off
  `final = notes_out`, which that branch reaches even on a zero-write fill).
  Zero written ⇒ no snapshot. The digest is taken over each note's canonical
  DB form, so it tracks the DATA revision and not the `notes_styling` knob
  (the emitted file has its own `output_sha256`).
  Operator free text (`preflight_override`, `degraded_ack`)
  is clamped to `receipt.ACK_TEXT_LIMIT` before storage. The patcher itself
  stays stateless; the ROUTE writes the row. Uploaded templates are
  request-scoped temp files under `OUTPUT_DIR/_mtool_tmp`. Liveness gate is
  `completed`/`completed_with_errors` (409 otherwise).
- **UI is a button + modal (`MtoolFillModal`), not a tab** — avoids a third
  `role="tab"` (gotcha #7).

Pinned by `tests/test_mtool_offline_fill.py`, `test_mtool_exporter.py`,
`test_mtool_routes.py`, `test_mtool_column_detect.py`, `test_mtool_units.py`,
`test_mtool_value_conventions.py`, `test_mtool_preflight.py`,
`test_mtool_artifact_and_receipt.py`, `test_mtool_failure_modes.py`,
`test_mtool_coverage_dry_run.py`, `test_db_schema_v38.py`/`_v39.py`, and the
`MtoolFillModal` web tests. Full plan: `docs/PLAN.md` +
`docs/PLAN-mtool-fill-pipeline.md`; operator guide: `mtool/README.md`.

### 29. Word (.docx) input — convert at the door; PDF stays the spine

Uploads accept Microsoft Word (`.docx`) as well as PDF (docs/PLAN-word-input.md).
A `.docx` is converted to a **text PDF at upload time** and stored as the run's
`uploaded.pdf`, so the entire page-based pipeline (scout, page hints, evidence
citations "PDF page N", the PdfSourcePane viewer) runs UNCHANGED — it just sees
crisp real text instead of a scan. Excel input is deliberately out of scope
(a spreadsheet has no pages; it belongs as a future companion channel, not a
primary input).

- **Both files are kept in the session dir:** `uploaded.docx` (original,
  formatting source) + `uploaded.pdf` (canonical for extraction + viewer). The
  `uploaded.pdf` naming contract is preserved — nothing downstream learns a new
  path. PDF uploads are byte-for-byte unchanged (land straight as
  `uploaded.pdf`, no sidecar).
- **`ingest/word_convert.py` is the single converter seam** (`convert_docx_to_pdf`).
  Platform-native + lightweight, NOT the removed docling/torch stack (gotcha
  #26): **Word COM via `docx2pdf` on Windows** (Word is installed there),
  **LibreOffice `soffice --convert-to pdf` on Mac/Linux/cloud**. Override with
  `XBRL_DOCX_CONVERTER` (`soffice`|`docx2pdf`) / `XBRL_SOFFICE_PATH`.
  `_run_conversion` is the monkeypatch point in tests (no real converter in CI).
- **Conversion failure is a 422, never a crash.** The upload endpoint tears down
  the whole session dir and returns `WordConversionError.user_message` verbatim
  (plain-language, tells the operator to Save-As-PDF in Word and re-upload — the
  always-available fallback, since the pipeline can't tell a hand-saved PDF from
  a server-converted one). CLI (`run._stage_input_document`) lets it propagate.
- **Notes source-formatting side-channel (Phase 2).** `ingest/docx_html.py`
  extracts the Word body to `source.html` (via `mammoth`, small pure-Python) —
  **best-effort, never blocks the upload**. `notes/source_snippets.py` slices it
  per top-level note (navigation only, keyed on note-number headings like scout
  hints — gotcha #13; NO deterministic label-matching enters the notes
  pipeline). `create_notes_agent` registers the `read_source_note(note_num)`
  tool + a prompt block ONLY when `source.html` exists for the run (derived from
  the PDF's parent dir); PDF-only runs are byte-identical to before. The agent
  **COPIES the source table's markup — inline `style=` included — straight into
  `content`** (verbatim passthrough, 2026-07-19); it does NOT re-describe that
  styling as a second model-authored representation. **PROSE still stays style-free**, enforced in
  code by `notes/writer.py::_strip_non_table_styles` — the narrowing is TABLES
  ONLY. Such tables are stamped `data-source-styled` so no renderer adds its own
  grid. PDF tables with no source counterpart land plain; only the dedicated
  formatter agent may author styling later. See gotcha #16, which owns the full
  rule — this bullet must not drift from it again.
- **No DB schema change** — files live on disk (hybrid-storage, gotcha #6). The
  inert `doc_conversions` table (gotcha #11) is NOT reused.

Pinned by `tests/test_word_convert.py`, `test_docx_html.py`,
`test_notes_source_snippets.py`, `test_notes_source_prompt.py`,
`test_upload_docx.py`, `test_run_cli_docx.py`, and the `UploadPanel` web tests.
Phase 0 converter spike + real-run validation (Steps 6/10) and Windows
enablement (Step 11) are operator/hardware gates, still open. Plan:
docs/PLAN-word-input.md.

### 30. Evals workspace — repeats/consistency, mTool gold, suites, trends

The Evals workspace (docs/PLAN-evals-workspace.md, PRD docs/PRD-evals-workspace.md)
turns one-run-one-gold grading into a corpus-level quality system. Every eval
child run is a **completely normal extraction run** through the existing
pipeline; the workspace only launches, watches, grades, and aggregates — it
NEVER alters extraction behaviour. Schema v30 (repeats/taxonomy/gold-prose) +
v31 (suites). All additive/nullable (gotcha #11); on rollback the tables sit
inert.

Load-bearing invariants:

- **Scoring formulas are fixed and decompose (PRD Scoring Design).**
  `accuracy = matched ÷ gold slots` (unchanged headline; a value slot is
  concept_uuid × period × entity_scope, LEAF/MATRIX_CELL only — COMPUTED
  totals excluded so they can't inflate). The **failure taxonomy**
  (`eval/grader.classify_failures`: scale / sign / period-swap / scope-swap /
  misplaced / false-not-disclosed / unaddressed / plain-wrong) NEVER softens
  the score — it powers drill-down + trends. Beyond-gold is a trended watchdog,
  never a headline penalty. **Consistency = unanimous agreement over the union
  of slots any repeat filled** (`eval/consistency.py`), needs ≥2 finished
  repeats else "unavailable" (never a misleading 100%). **Suite aggregate =
  MEAN of per-document accuracy** (`eval/scorecards.aggregate_suite`), pooled
  figure secondary, worst document always surfaced, failed docs excluded +
  "N of M". These live in pure modules with hand-built fixtures — change a
  formula and its pinning test in the same commit.
- **Repeats ride one SSE stream** (`server.run_repeat_group_stream`, Step D1):
  N identically-configured runs back-to-back sharing ONE `session_id` (so
  Stop-All / disconnect reaches the live repeat) but isolated output subdirs;
  consistency is finalized on the generator's `finally` (abort mid-group →
  `partial`). Do NOT reintroduce a separate cancel channel.
- **Suite batch runner** (`api/suite_runner.py`, Step E3) is a background loop
  (reviewer-pass thread pattern), concurrency **fixed at 3** (decision #2),
  Resume re-launches only documents whose DISTINCT finished repeats are below
  the requested count (identified by the deterministic
  `suite-{suite_run}-doc-{doc}` session id; completion counts distinct repeats
  via `COALESCE(repeat_index, id)`, never raw rows), and
  `repo.reconcile_stale_suite_runs` retires crash-orphaned `running` suite runs
  at startup (mirrors `reconcile_stale_review_tasks`). Child runs link via
  `runs.suite_run_id`, threaded through `run_multi_agent_stream` /
  `run_repeat_group_stream`. **Repeat Resume fills the GAPS** — the missing
  repeat indices, computed from `repo.finished_repeat_indices` — never a blind
  append from a count (which duplicated a later index and left an earlier one
  unfilled when a middle repeat failed; consistency dedups per index via
  `repo.deduped_repeat_run_ids`). The v32 snapshot freezes each document's
  BYTES into a run-owned copy (`_copy_source_for_snapshot`, under
  `output/_suite_snapshots/run_{N}`), so deleting a live suite document can't
  strand an unfinished Resume. Snapshot copies are never auto-reclaimed —
  deliberate (Resume may need them indefinitely), same accumulation model as
  per-run output dirs; cleanup is future housekeeping, don't add it as a side
  effect. An empty statement list is a
  notes-only run (preserved, not expanded to all five); a both-empty selection
  is rejected 422.
- **History hides suite children by default** (Step E6): `GET /api/runs`
  filters `suite_run_id IS NULL` unless `include_suite_children=true`
  (decision #1). Repeat children are NOT hidden (they're normal History runs).
- **mTool gold ingest is strict + variant-precise** (already shipped C1–C3):
  `POST /api/benchmarks/from-mtool` requires a declared unit (no auto-guess —
  a wrong unit silently 1000×'s every value) AND an explicit `template_ids`
  set (gotcha #21 — uuids differ per variant). The C4 form's picker is fed by
  `GET /api/eval/templates`. Off-template labels surface as unmatched, never
  fuzzy-matched.
- **Trends + compare recompute on demand from durable facts** (`eval/compare.py`,
  F1/F2) — no heavyweight new storage. Compare unions differing document sets
  (greyed + excluded from the aggregate delta), and warns when gold changed
  between the two runs via a per-run gold FINGERPRINT (v33, `_gold_changed`);
  the `updated_at` timestamp window is the legacy fallback for pre-v33 scores.
  Pooled accuracy sums the EXACT repeat matched counts (`matched_for_pool`),
  never per-doc rounded ints (rounding a 0.5 repeat mean to 0 corrupted it).
  Suite "N of M" coverage is over the FROZEN corpus (`aggregate_suite(...,
  corpus_size=)`), so a failed-to-stage document counts toward M and its state
  + reason surface via the detail endpoint's `doc_states`.
- **Frontend:** the "Evals" nav surface (`/evals` → `web/src/pages/SuitesPage.tsx`)
  is admin-gated like Benchmarks (which it depends on for gold). Recharts is the
  ONE chart dep (SVG, coexists with the inline-style rule, gotcha #7). The
  ConsistencyPanel is a run-page SECTION, not a `role="tab"` (gotcha #7).

Pinned by `tests/test_db_schema_v30.py`/`_v31.py`, `test_eval_taxonomy.py`,
`test_eval_consistency.py`, `test_repeat_group_launch.py`,
`test_eval_mtool_ingest.py`/`test_mtool_gold_routes.py`, `test_suite_routes.py`,
`test_suite_runner.py`, `test_suite_scorecards.py`, `test_reviewer_lift.py`,
`test_suite_compare.py`, and the `ConsistencyPanel`/`BenchmarksPage`/
`SuitesPage`/`EvalTab` web tests.

### 31. Notes source integrity — a COUNT, not a claim; ships OFF

The `XBRL_NOTES_SOURCE_INTEGRITY` mode (`off` | `shadow` | `enforce`, **default
off**) makes notes extraction prove that every part of the source document was
handled, rather than only that each note landed somewhere. Word-first: the
uploaded `.docx` is read into numbered, hashed **blocks** before any agent sees
a template, agents return block ids instead of prose, and ordinary code builds
the cell and counts what was used. Plan:
docs/PLAN-notes-source-integrity-build.md. Schema is gotcha #11 (v35/v36).

**Operator-settable from Settings since 2026-08-04** (`notes_source_integrity`
on `/api/settings` + `/api/config`, "Word source handling" in the General tab).
All three modes are offered — `shadow` only earns its keep as a step towards
`enforce`. The POST validates against `IntegrityMode` and 400s an unknown value
**because `integrity_mode()` fails CLOSED to `off`**: an unvalidated write would
read as saved in the form and silently do nothing on the next run. The picker's
vocabulary is served (`notes_source_integrity_choices`) and the picker BUILDS
its options from it — a mode the frontend doesn't know must still render (as
its raw value) and, above all, survive a save. Hardcoding the three modes made
a future one display as `off` and then be written back as `off` on the next
save of any unrelated setting, silently downgrading the backend's real mode
(peer review, 2026-08-04). `SourceIntegrityMode` is therefore a plain string,
not a union. Pinned by `tests/test_settings_api.py` and
`web/src/__tests__/settingsSourceIntegrity.test.tsx`.

**Prompt activation (2026-08-06) — the tools are now TAUGHT.** Phases 1–10
built the block tools, renderer and checks, but no prompt ever mentioned them:
on the first live `enforce` run (IME 2024) every agent stayed on the
copy-into-content channel, `write_note_from_source` was never invoked, and the
integrity check then flagged the unused blocks — the mode was an accounting
layer, not a workflow. Now, when a generation exists AND its reading found
notes (`deps.source_block_notes`, loaded once at factory time),
`_render_source_blocks_block` REPLACES the copy-verbatim sidecar block —
rendering both would teach two incompatible workflows for the same notes, the
run-79 defect shape — and the write-time nudges route to
`write_note_from_source` (`format_block_write_nudge`) instead of the
copy-into-content pair. Agents are instructed, not forced: `write_notes` stays
correct for notes the source has no parts for, and for a PDF-vs-source
disagreement. Consequence for the modes: `shadow` now runs the SAME
source-first workflow as `enforce` — the two differ only in whether the
verdict tips run status and the reviewer relinks — so "changes nothing" means
run status, not extraction behaviour (the Settings copy says so). An
empty/failed reading degrades to the sidecar workflow.

Four peer-review findings (2026-08-06) harden the activation; each was
reproduced before fixing:

- **`write_note_from_source` payloads are `source_built=True`
  (CRITICAL).** The impl used to build a plain `NotesPayload` with no
  `parent_note` — the validator raised AFTER `write_cell_from_blocks` had
  committed, crashing the very tool the prompt teaches and leaving a DB cell
  with no workbook artifact. `source_built` waives the evidence + parent_note
  authoring contracts (the rendered text is the document's own, already
  persisted with lineage; a second injected `<h3>` would make the
  `persist_notes_cells` rewrite a clobber instead of a no-op). The
  `write_notes` JSON parser never passes the field, so an agent cannot set it
  to dodge the evidence contract. The payload also carries `note_num`
  (`_note_num_for_blocks` — None on a multi-note selection, never guessed) so
  the run-79 same-note supersede replaces a hand-written draft with the
  source-built version instead of concatenating.
- **`read_source_note` is HIDDEN on block-path runs.** Its description
  teaches copy-into-content — exposing it beside the block tools handed the
  agent two incompatible workflows again. Sidecar-only runs keep it.
- **Numeric templates (13/14, `entry.is_numeric`) are excluded from the
  block workflow.** `write_note_from_source` resolves prose `notes_nodes`
  only; teaching it to Issued Capital / Related Party taught a write that
  always rejects. The factory leaves `source_block_notes` empty for them —
  the ONE switch that gates the prompt, the nudges, the write-tool
  registration and the copy-tool hiding together — until a source-block →
  numeric-facts path exists.
- **The Settings copy states that `shadow` changes extraction behaviour**,
  not just measurement — only the verdict's effect on status differs from
  `enforce`.

Pinned by `tests/test_notes_source_prompt.py`,
`tests/test_notes_source_tools.py`.

Every invariant below exists because breaking it produces a **false green** — a
run that reports complete coverage of a document it only partly read. That is
the one failure this feature has no defence against, so each is pinned.

- **Disposition is not placement.** `notes_block_usages` records what was
  DECIDED about a block; `notes_block_placements` (v37) records where it
  currently LIVES, many-to-many, deactivated rather than deleted. They were
  one table, and that produced two reproduced false greens: relinking a cell
  from b1+b2 to b1 left b2 recorded as included AT that cell, and a clobbered
  sheet verified clean over zero cells. An `included` block resolves ONLY when
  it has an active placement pointing at a cell that still exists;
  `routed`/`structured_consumed` need a destination. `UNIQUE(generation_id,
  block_id)` on usages also made one block in two cells unrepresentable, so
  the duplicate check was structurally dead — it now reads the ledger.
- **A source write satisfies the ordinary coordinator contract.** It routes
  its rendered payload through the same writer an authored write uses, so
  `wrote_once` / `filled_path` / `cells_written` / the Sheet-12 sink are all
  set. Writing only to the database tripped the no-write guard and left the
  later `persist_notes_cells` free to clobber the row. That path now carries
  provenance across the rewrite and retires the placements of coordinates it
  drops.
- **The shared writer validates its target.** Template family, prose sheet and
  `LEAF`, inside `write_cell_from_blocks` — the one function all three callers
  use. Validating in each caller is how `Ghost` row 999 wrote successfully and
  verified clean. An extraction agent additionally may write only its OWN
  sheet.
- **One digest function for cell content.** `source_rendered_sha256` and
  `current_html_sha256` are both `lineage.content_sha256`; the render shape
  lives in `notes_cells.source_render_version`. Folding the version into the
  hash meant a human edit could never equal a source render, so editing a cell
  back to its exact source text never cleared the divergence mark.
- **The version token is `content_revision`, and the client sends it.**
  A monotonic counter, because `updated_at` is second-precision and two saves
  inside one second shared it. The check was built server-side and never wired
  to the editor, so the 409 could not fire in the product — the client now
  sends it on every save including the keepalive, and refreshes it from each
  response.
- **Failure to assess is never proof of no loss.** A preflight whose size
  check throws returns an explicit `unavailable` advisory and is NOT `clean`;
  a PDF page whose independent word measurement fails is unresolved, not fully
  covered; a detected table that cannot be extracted stops covering the words
  beneath it.
- **A short manifest is refused, never measured.**
  `notes/source_manifest.py::build_docx_manifest` reads the ORIGINAL `.docx`
  **uncapped** (`extract_docx_html` applies no cap; the 8 MB limit lives in
  `write_source_html`, which serves the agent sidecar — two consumers, two
  rules). Extraction failure, an empty body, or a truncation sentinel raises
  `ManifestError`; the run-level handler then continues on the current path
  with **no generation and no verdict**. Its splitter also MEASURES what it
  skips (`unaccounted_chars`) — `source_snippets`' splitter is a navigation aid
  and may ignore what falls between chunks, this one is a ledger. Gate 0.3 on
  the FINCO fixture: 246 blocks, 93,223/93,223 chars, 21/21 tables, 15/15
  boundaries, 0/20 contents-page lines misread.
- **Furniture is settled at freeze, not left for a human.** Page headers,
  page numbers, contents lines and pre-notes material are dispositioned with
  their approved reason code at `freeze_manifest` time. Leaving them unresolved
  buries the 186 blocks somebody must look at under 60 rows of page headers,
  which is how a review queue stops being used.
- **The reason list is closed, and one reason deliberately does NOT settle.**
  `EXCLUSION_REASONS` is fixed; `UNREADABLE_NEEDS_REVIEW` records the problem
  without resolving the block. `_SETTLING_REASONS` is DERIVED
  (`EXCLUSION_REASONS - UNRESOLVED_REASONS`) so a new code cannot be forgotten
  in a second place, and an unknown code fails CLOSED. There is **no generic
  dismiss** anywhere in the UI or the tools.
- **Lineage is recorded in the SAME transaction as the text.** `notes/lineage.py`
  is called inside the caller's `BEGIN IMMEDIATE` — a recompute afterwards
  leaves a window where a cell looks source-exact and is not. This applies to
  the PATCH endpoint, the reviewer, and `source_write`. Editing a cell back to
  the source text CLEARS the divergence; a permanent mark for an undone edit
  makes the flag useless. The PATCH gained an optimistic version check (409):
  last-write-wins was fine when an edit only lost text, not once it decides
  whether a cell counts as accounted for.
- **In `enforce` the reviewer relinks; it does not author over source.**
  Scoped to cells that ACTUALLY carry lineage, so a mixed run's uncovered notes
  keep the ordinary edit path. `off`/`shadow` are unchanged.
- **One function owns "build a cell from source parts"**
  (`notes/source_write.py`) — three callers need identical guarantees, and a
  second implementation is how two of them disagree about whether a block was
  used. Cell + lineage + one disposition per block land in ONE transaction
  (joining an ambient one rather than nesting; SQLite has no nested
  transactions). Naming half a split table pulls in the rest and says so.
- **Oversized notes are capped, not truncated** (Step 0.6 decision). A render
  over `CELL_CHAR_LIMIT` is refused with an instruction and left for the
  authoring path; `check_character_cap` reports it so it reaches a person.
- **Boundary disagreement BLOCKS, it is not merely measured.** A mis-assigned
  block otherwise shows 100% completeness and a wrong answer. Absent scout data
  reads as *unknown*, never as agreement.
- **Status goes through the ONE existing block in `server.py`** (gotcha #10) —
  `_run_notes_integrity_check` returns a verdict and never writes a status,
  pinned by a test that reads its source. Only `enforce` tips; `shadow`
  computes the IDENTICAL verdict and never touches run status. (Since prompt
  activation, 2026-08-06, `shadow` shares `enforce`'s source-first extraction
  workflow — the staged rollout comparison is now verdict-vs-status, not
  workflow-vs-workflow; see the activation block above.)
- **A stored verdict carries its `rule_version` and its `mode`**, and attempts
  append rather than replace. `legacy` (pre-feature), `off` (somebody decided)
  and a real verdict are three different facts and the API keeps them apart —
  an empty checklist would read as "nothing was missed".
- **PDF track (Phase 10) is BUILT and GATED OFF.** Step 0.4's zero-false-green
  criterion is unmet: no digital PDF exists in `data/`, so `notes/pdf_layout.py`
  has only ever run against generated fixtures. Its area accounting measures
  block boxes against word boxes from a SEPARATE `get_text("words")` call —
  comparing against the same block dict the blocks came from can only detect
  losses after segmentation, never the misses that happen. A page with no text
  layer emits an unresolved region covering the page, so a scan can never
  finish clean.
- **Step 9.1's preflight changes no decorator.** It reads what
  `mtool/notes_exporter` already produced and resolves its theme through
  `firm_theme()` (gotcha #16's rule for a new consumer). Advisory only —
  partial output stays downloadable.
- **DPI does not matter; area might.** Measured 2026-08-01 on FINCO page 31:
  input tokens were IDENTICAL at 150, 200 and 400 DPI (2884 each) because the
  provider downscales to a fixed budget, and 400 DPI answered slightly worse.
  Do not "improve" the render by raising DPI. The crop advantage is suggestive,
  not proven.

**Every one of the above was reproduced as a CLEAN verdict over missing
content before it was fixed** (peer review, 2026-08-01). The reproductions are
kept together in `tests/test_notes_integrity_false_greens.py` — read that file
before changing anything in this area.

Pinned by `tests/test_notes_integrity_false_greens.py`,
`tests/test_notes_integrity_retry.py`, `tests/test_notes_source_manifest.py`, `test_notes_source_render.py`,
`test_notes_source_write.py`, `test_notes_lineage.py`,
`test_notes_integrity_checks.py`, `test_notes_integrity_runner.py`,
`test_notes_integrity_wiring.py`, `test_notes_integrity_api.py`,
`test_notes_source_tools.py`, `test_notes_export_preflight.py`,
`test_notes_pdf_layout.py`, `test_db_schema_v35.py`/`_v36.py`, and the
`NotesIntegrityPanel` web tests.

## Deeper References

| Doc | When to read |
|---|---|
| [docs/Archive/NOTES-PIPELINE.md](docs/Archive/NOTES-PIPELINE.md) | Notes subsystem deep-dive |
| [docs/MPERS.md](docs/MPERS.md) | MPERS filing-standard deep-dive |
| [docs/agent-prompt-audit.html](docs/agent-prompt-audit.html) | Every agent's prompt, quoted verbatim. **Regenerate with `venv/bin/python scripts/refresh_prompt_audit.py` after editing a quoted prompt** — `tests/test_prompt_audit_matches_live.py` fails the build on drift, and also fails when a live agent role is missing from its matrix. |
| `docs/workflows/*.md` | Per-statement fill-workflow notes |
| `docs/xbrl-field-descriptions.md` | Field reference for the XBRL taxonomy |

Historically cited but absent files are listed once in `AGENTS.md`. The live
invariants and pinning tests above replace those missing documents.
