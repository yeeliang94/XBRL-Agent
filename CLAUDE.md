# XBRL Agent — AI Agent Reference

This file is a **context pack for AI coding agents** (Claude Code, Codex, etc.).
It carries only load-bearing invariants and quick-reference commands. For the
full module map, feature walkthroughs, and the cross-file sync matrix, follow
the pointers in [Deeper References](#deeper-references).

## What This Is

A standalone XBRL extraction agent for Malaysian financial statement PDFs.
Extracts data into SSM MBRS XBRL Excel templates. Handles the five primary
statements (SOFP, SOPL, SOCI, SOCF, SOCIE) plus five supplementary notes
templates, across two filing standards (MFRS, MPERS) and two filing levels
(Company, Group). Agents run concurrently via a coordinator; results merge
into one workbook; cross-checks validate consistency.

## How to Behave Here

Before touching code in this repo (the project-specific specialization of the
session's general operating rules):

- **Surface assumptions.** This codebase has 21 load-bearing invariants below.
  If your change brushes one and the intent is ambiguous, present the
  interpretations — don't pick silently.
- **Stay surgical.** Every changed line must trace to the request. Don't
  "improve" adjacent template formulas, prompts, or inline styles as a side
  effect (see gotchas #3, #7).
- **Keep it minimum.** No speculative abstractions or config the task didn't
  ask for — the notes pipeline is deliberately all-LLM-judgement, not
  deterministic matching.
- **The bar for "done" is the pinning test, not "looks right."** Almost every
  invariant below names a `tests/…` file that guards it. A change near an
  invariant is complete only when its pinning test passes — run it and cite it.

## Quick Start

```bash
# Mac — Web UI (starts local LiteLLM proxy + server)
./start.sh
# Web UI at http://localhost:8002, LiteLLM proxy at http://localhost:4000

# Mac — CLI, all 5 statements (uses TEST_MODEL from .env)
python3 run.py data/FINCO-Audited-Financial-Statement-2021.pdf

# Mac — CLI, specific model + statements
python3 run.py data/FINCO.pdf --model gpt-5.4 --statements SOFP SOPL

# Mac — CLI, group filing (consolidated + company figures)
python3 run.py data/FINCO.pdf --level group --statements SOFP SOPL

# Mac — CLI, MPERS filing standard
python3 run.py data/FINCO.pdf --standard mpers --statements SOFP SOCIE

# Mac — CLI, with notes templates
python3 run.py data/FINCO.pdf --notes corporate_info list_of_notes

# Windows (enterprise proxy) — double-click start.bat
```

## Architecture at a Glance

```
PDF + scout (optional) → coordinator → N extraction agents (parallel) ─┐
                                    → M notes agents (parallel)       ─┤→ workbook_merger → filled.xlsx
                                                                       └→ cross_checks
```

Full module map, subsystems, and data flow in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
(OpenAI-compatible). Direct Google API calls are blocked (403). See
[docs/PORTING-WINDOWS.md](docs/PORTING-WINDOWS.md).

### .env

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
TEST_MODEL=openai.gpt-5.4
SCOUT_MODEL=openai.gpt-5.4     # falls back to TEST_MODEL when blank

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

# Canonical concept model is now MANDATORY (rewrite Phase 1.1): the legacy
# direct-xlsx pipeline and the XBRL_CANONICAL_MODE opt-out were removed.
# The flag is no longer read (see gotcha #21).
```

### PydanticAI Model Creation (v1.77+)

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
`start.bat` sets this; if running manually: `set PYTHONUTF8=1 && python server.py`.
`write_text(..., encoding="utf-8")` is used as a safety net throughout.

### 2. pydantic-ai pinned `>=1.77.0`

- `Agent._function_tools` does not exist — cannot monkey-patch tools.
- Use `OpenAIChatModel(name, provider=OpenAIProvider(...))`; `OpenAIModel`
  is a deprecated alias.
- Tool event streaming uses `agent.iter()` + `node.stream()` — no
  `event_callback` or monkey-patching.

### 3. XBRL templates derived from SSM linkbase

Templates in `XBRL-template-MFRS/` and `XBRL-template-MPERS/` are derived from
SSM MBRS linkbases under `SSMxT_2022v1.0/`. Formula cells must trace back to
the calculation linkbase.

**Do not hand-edit template formulas.** If a formula is wrong, regenerate
from the linkbase and capture the before/after in `backup-originals/`.
Historical incident (2026-04-07, +20-row SOFP offset bug) documented in
`docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md`.

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
`agent_run.usage()` after each node — there is no true per-turn split.
`TokenReport.add_turn()` in `token_tracker.py` is **only used in tests**, never
in the live path. The live coordinator loop derives a per-turn figure by
**subtracting the previous node's cumulative usage** and persists it to
`run_agent_turns` (schema v8) along with exact timing + tool activity; the
prompt/completion split is therefore best-effort, while duration and tool
calls are exact. The Telemetry tab labels this honestly. After completion,
`server.py` also backfills run-level totals from `result.usage`. Both the
**face coordinator** (`coordinator.py`) **and the single-agent notes path**
(`notes/coordinator.py`) capture this; the Sheet-12 fan-out leaves per-turn
rows empty (its sub-agents merge into one row) — rollups still populate.

**Verbatim content lives on disk, not the DB.** `save_agent_trace`
(`agent_tracing.py`) writes the full request/response transcript to
`{output_dir}/{stmt}_conversation_trace.json` — text kept verbatim (single
payloads capped at 100 KB; binary elided) — served on demand by
`GET /api/runs/{id}/agents/{stmt}/trace` (which verifies the resolved path
stays under the run's `output_dir`). Don't move that heavy content into
SQLite (hybrid-storage decision; see docs/PLAN-run-page-and-telemetry.md).
**Failed agents save a trace too:** the timeout / iteration-cap / cancel /
exception paths call a best-effort helper that falls back to
`agent_run.ctx.state.message_history` (a partial run has no `.result`), via
`save_messages_trace` — so the trace viewer is useful exactly when debugging
a failure. Pinned by `tests/test_agent_tracing.py`.

### 7. Frontend uses inline styles, not Tailwind

Tailwind CSS v4 didn't load reliably on Windows (the upload button was
unclickable). All components use inline `style={}` props. **Do not** convert
back to className-based Tailwind.

**Visual spec ([docs/pwc-design-system.html](docs/pwc-design-system.html)):**
the canonical PwC design-system reference. Tokens live in
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

### 11. DB schema — version-stepped auto-migration on startup

`db/schema.py` carries `CURRENT_SCHEMA_VERSION` (committed: **25**). `init_db`
reads the stored version and walks an old database up one version at a time
through per-version `ALTER TABLE` blocks, so any older DB lands on the current
schema without manual intervention. Each step is idempotent. Shipped steps:

- **v1 → v2:** adds the seven `runs` lifecycle fields; backfills `started_at`
  from `created_at` (`_V2_MIGRATION_COLUMNS`).
- **v2 → v3:** adds the `notes_cells` table — the canonical per-cell notes
  store (see gotcha #16).
- **v3 → v6:** canonical concept-model tables (v4), `concept_nodes.matrix_col`
  (v5), `notes_cells.concept_uuid` (v6) — see gotcha #21.
- **v6 → v7:** adds `cross_checks.target_sheet` / `target_row` (review-workspace
  click-to-cell).
- **v7 → v8:** adds the `run_agent_turns` per-turn telemetry metrics table +
  four rollup columns on `run_agents` (`prompt_tokens`, `completion_tokens`,
  `turn_count`, `tool_call_count`). Metrics only — verbatim per-iteration
  request/response content stays in `{stmt}_conversation_trace.json` on disk
  (hybrid storage; see docs/PLAN-run-page-and-telemetry.md and gotcha #6).
  Pinned by `tests/test_db_schema_v8.py`.
- **v8 → v9:** adds `concept_nodes.matrix_col_label` — hydrated from SOCIE
  row-2 headers at template-import time. Nullable; existing concepts read
  NULL until the startup bootstrap re-imports.
- **v9 → v10:** adds `runs.orchestration` (`TEXT DEFAULT 'split'`) — originally
  the monolith-experiment flag. The monolith experiment was deleted in the
  first-principles rewrite (Phase 1); the column is RETAINED (always `'split'`
  now) so the schema version and History read-back stay stable.
- **v10 → v11:** adds the `concept_render_aliases` table — secondary render
  coords for concepts that occupy more than one physical cell (face-sheet
  rows whose value cross-rolls-up from a sub-sheet total). See the
  cross-sheet rollup linkage note in gotcha #21. Pinned by
  `tests/test_db_schema_v11.py`.
- **v11 → v12:** adds two reviewer-agent tables (gotcha #21, reviewer pass):
  `run_fact_snapshots` (the ORIGINAL extraction facts, backed up before the
  reviewer writes — "Revert to original" restores from here) and
  `reviewer_flags` (the narrow `stuck` / `disputes_prior` user-facing list).
  Both are new tables → the step is a pure `CREATE TABLE IF NOT EXISTS`
  walk-forward with no `_V12_MIGRATION_COLUMNS`. Pinned by
  `tests/test_db_schema_v12.py`.
- **v12 → v13:** adds the `run_review_tasks` table (rewrite Phase 5.3) — the
  durable replacement for the in-process `_REVIEW_TASKS` dict in `server.py`
  (gotcha #21). One row per run (`run_id` PK; a relaunch overwrites the
  slot), so a finished manual re-review outcome survives a restart and a
  poll can still fetch it. New table → a pure `CREATE TABLE IF NOT EXISTS`
  walk-forward with no `_V13_MIGRATION_COLUMNS`. Startup
  (`server._lifespan`) calls `repo.reconcile_stale_review_tasks` to retire
  any row left `running` by a dead process into a terminal error. Pinned by
  `tests/test_db_schema_v13.py`.
- **v13 → v14:** adds `cross_checks.comparands_json` (reviewer holistic audit) —
  the JSON list of values a check compared, so the reviewer gets concrete entry
  points. Nullable `_V14_MIGRATION_COLUMNS`.
- **v14 → v15:** adds cache-telemetry columns (`cache_read_tokens` /
  `cache_write_tokens`) to `run_agents` + `run_agent_turns` (prompt-caching
  measurement). All `INTEGER DEFAULT 0` (`_V15_MIGRATION_COLUMNS`).
- **v15 → v16:** adds the four gold-standard-eval tables (`eval_benchmarks`,
  `eval_benchmark_templates`, `gold_concept_facts`, `eval_scores`) PLUS one
  nullable `runs.benchmark_id` column (`_V16_MIGRATION_COLUMNS`, FK →
  eval_benchmarks ON DELETE SET NULL). Three pure `CREATE TABLE IF NOT EXISTS` +
  one additive ALTER. `runs.benchmark_id` forward-references `eval_benchmarks`
  (created later in the same CREATE loop); SQLite resolves FK targets lazily so
  the forward ref is fine. Pinned by `tests/test_db_schema_v16.py`. See gotcha
  #23 + docs/PLAN-eval-benchmark.md.
- **v16 → v17:** adds the nullable failure-taxonomy column
  `run_agents.error_type TEXT` (`_V17_MIGRATION_COLUMNS`,
  PLAN-orchestration-hardening item 9). Vocabulary constants live next to
  `AgentResult` in `coordinator.py` (`turn_timeout · iteration_capped ·
  wallclock · token_budget_exceeded · projection_failed · save_gate_refused
  · tool_exception · cancelled · no_write · transient_exhausted`); no CHECK
  constraint on purpose
  (same rationale as `runs.status`). Server persistence guarantees every
  failed/cancelled agent row carries a non-null value
  (`server._agent_row_error_type` derives one when the coordinator didn't
  set it explicitly). Pinned by `tests/test_db_schema_v17.py`.
- **v17 → v18:** adds the two auth tables (PLAN-azure-auth-deployment Phase 1) —
  `auth_users` (the account list = the email allowlist; argon2id
  `password_hash`, nullable for future SSO-only users; `disabled` flag) and
  `auth_sessions` (server-side session store for the sliding 15-min idle
  timeout; `ON DELETE CASCADE` from `auth_users`). Both are pure
  `CREATE TABLE IF NOT EXISTS` walk-forward steps (new tables, no ALTER), so
  the migration block only bumps the version marker. The `auth/` package owns
  all access; accounts are provisioned with `python -m auth.manage`. Pinned by
  `tests/test_db_schema_v18.py`.
- **v18 → v19:** adds the `notes_nodes` table (prose notes registry, Track A of
  PLAN-notes-template-registry) — pure `CREATE TABLE IF NOT EXISTS` walk-forward.
  Pinned by `tests/test_db_schema_v19.py`.
- **v19 → v20:** adds `auth_users.is_admin` (`_V20_MIGRATION_COLUMNS`,
  `INTEGER NOT NULL DEFAULT 0`) — the admin role gating web user management
  (gotcha #24, Settings → Users tab + `/api/admin/*`). One additive ALTER;
  existing accounts walk forward as non-admins. Pinned by
  `tests/test_db_schema_v20.py`.
- **v20 → v21:** adds the `doc_conversions` table (formerly the scanned-PDF →
  readable-document feature, now REMOVED — see gotcha #26). The table is RETAINED
  as an inert artifact so the migration chain stays intact, but no code reads or
  writes it. Pure `CREATE TABLE IF NOT EXISTS` walk-forward (new table, no ALTER).
  Pinned by `tests/test_db_schema_v21.py`.
- **v21 → v22:** adds the nullable `runs.notes_table_style TEXT` column (per-run
  notes-table style override, gotcha #16 + docs/PLAN-notes-table-theme.md) —
  `_V22_MIGRATION_COLUMNS`, one additive ALTER. NULL = the run inherits the
  firm-default theme. Unlike `run_config_json` (draft-only editable), this is
  editable on ANY run status via `PATCH /api/runs/{id}/notes_table_style`
  because notes review happens after extraction. Pinned by
  `tests/test_db_schema_v22.py`.
- **v22 → v23:** adds the three notes-reviewer detector-input tables
  (`notes_cell_provenance`, `run_notes_inventory`, `run_notes_cell_snapshots`)
  plus the `run_notes_review_state` snapshot-taken marker (notes reviewer —
  docs/PLAN.md). Pure `CREATE TABLE IF NOT EXISTS` walk-forward. Pinned by
  `tests/test_db_schema_v23.py`.
- **v23 → v24:** adds `notes_review_flags` (reviewer flags) + `notes_review_tasks`
  (durable async re-review state, reconciled at startup). Pure
  `CREATE TABLE IF NOT EXISTS` walk-forward. Pinned by
  `tests/test_db_schema_v24.py`.
- **v24 → v25:** adds `notes_cell_tombstones` — the durable "this notes cell was
  emptied" record so the workbook overlay can BLANK a reviewer clear / move-out /
  authored-then-reverted coordinate (the overlay is additive and cannot otherwise
  represent a deletion). See gotcha #16 (overlay is authoritative for the notes
  region). Pure `CREATE TABLE IF NOT EXISTS` walk-forward. Pinned by
  `tests/test_db_schema_v25.py`.

SQLite `ALTER TABLE` cannot add `NOT NULL` columns without defaults — every
entry in each `_Vn_MIGRATION_COLUMNS` tuple is nullable or has a safe default.
The `status` column has no `CHECK` constraint on purpose: adding a new status
value should not require a full-table migration.

### 12. Filing level — Company vs Group

Each run has one `filing_level` (`"company"` or `"group"`, default
`"company"`) that flows end-to-end: `RunConfigRequest` → `RunConfig` →
`template_path()` → agent prompts → verifier → cross-checks → history.

- **Company templates:** 4 cols — A=label, B=CY, C=PY, D=source.
- **Group templates:** 6 cols — A=label, B=Group CY, C=Group PY, D=Company CY,
  E=Company PY, F=source.
- **Group SOCIE** uses 4 vertical row blocks (rows 3–25 Group CY, 27–49 Group
  PY, 51–73 Company CY, 75–97 Company PY).

On Group filings, verifier + cross-checks run twice (Group cols, then Company
cols) and report separately. Root-level template xlsx files no longer exist —
all templates live in `Company/` or `Group/`.

### 13. Scout page hints are soft guidance only

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
  every face and notes prompt. `scale_unit` carries especially loud
  "verify or 1000× error" wording because a wrong unit silently
  inflates every extracted value (gotcha #17's sibling failure mode).
  `scale_unit="unknown"` is the safe default and the prompt block
  upgrades from "verify" to "MUST read the header" in that case.
  Pinned by `tests/test_infopack_context_schema.py`,
  `tests/test_scout_populates_context.py`,
  `tests/test_prompts_render_context.py`.

All three additions degrade gracefully: empty `face_line_refs` /
`subnotes` / context fields fall through to today's bare hint blocks.
Plan: `docs/PLAN-scout-coverage-quality.md`.

### 14. Notes feature — five supplementary templates (parallel with face)

Notes agents fill MBRS templates 10–14 (MFRS) / 11–15 (MPERS) in parallel
with face statements. Discovery is PDF-first: scout extracts a
`notes_inventory` from the PDF, then per-template agents read those notes and
write content to matching rows. No deterministic matching, no OCR, no synonym
dictionary — pure LLM judgement.

Key invariants:

- **Sheet 12 (`LIST_OF_NOTES`) fans out** into `N` sub-agents; `N` is
  model-aware via `pricing.resolve_notes_parallel(model)`.
- **Retry budget:** every notes agent and Sheet-12 sub-agent retried at most
  once. Exhaustion writes `notes_<TEMPLATE>_failures.json` /
  `notes12_failures.json` / `notes12_unmatched.json` side-logs.
- **Cell cap:** 30,000 chars (`notes.writer.CELL_CHAR_LIMIT`). Longer content
  truncated with `[truncated -- see PDF pages N, M]` footer.
- **Column rules:** prose rows write col B only; numeric rows (13, 14) fill
  all four value columns on group filings. Evidence always col D (Company) /
  col F (Group).
- **Scanned-PDF fallback:** if the PyMuPDF-regex inventory pass returns empty,
  `scout.notes_discoverer_vision._vision_inventory` renders the notes section
  in 8-page batches and runs up to 5 vision batches in parallel.

Full walkthrough: [docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md).

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
  See [ADR-002](docs/ADR-002-socie-dividend-sign.md).

Full walkthrough: [docs/MPERS.md](docs/MPERS.md).

### 16. Notes cells are HTML; Excel download regenerates from the DB

Notes agents emit **HTML** (not plaintext) into cells on sheets 10–14
(MFRS) / 11–15 (MPERS). The payload flow is:

```
agent HTML → sanitiser → notes_cells (DB, canonical) → overlay → xlsx stream
                                     ↘ NotesReviewTab (TipTap editor)
```

Key invariants:

- `notes_cells` (schema v3) is the source of truth. The on-disk xlsx is
  a flattened snapshot; the download endpoint overlays the DB rows
  onto a temp workbook at stream time via
  `notes/persistence.overlay_notes_cells_into_workbook`.
- **The overlay is AUTHORITATIVE for the notes prose region, not additive
  (2026-06-24 fix).** It writes each surviving row's prose (col B) AND
  evidence (`evidence_col_for(filing_level)`, D=Company / F=Group), then
  BLANKS every coordinate the notes reviewer emptied — recorded in
  `notes_cell_tombstones` (schema v25). Additive-only overlay could not
  express a deletion, so a reviewer clear / move-out reintroduced the
  merge-time prose on download (duplicate / stale) and reviewer evidence
  never reached the export. `clear`/`move` add a tombstone, `author`/`edit`
  remove it, and `revert` reconciles them (clears cleared-row tombstones,
  re-tombstones authored rows). Callers MUST pass the run's `filing_level`
  so evidence lands in the right column. **Rerun-safety:** a notes-agent
  rerun (`persist_notes_cells`) drops the sheet's tombstones, and the overlay
  additionally NEVER blanks a coord that has live prose — so a regenerated row
  can't be wiped by a stale reviewer tombstone. Pinned by
  `tests/test_notes_reviewer_overlay_deletions.py`.
- Cap is 30,000 **rendered** characters (`notes.html_to_text
  .rendered_length`), not 30k of raw HTML. The sanitiser-and-writer
  both enforce this; the server's PATCH endpoint returns 413 when
  edited content exceeds it.
- Agent re-run **clobbers** edits: the coordinator calls
  `delete_notes_cells_for_run_sheet(run_id, sheet)` before writing a
  fresh batch. The UI gates this with a confirm dialog fed by
  `GET /api/runs/{run_id}/notes_cells/edited_count` (returns how many
  rows have `updated_at > run.ended_at`).
- HTML tag whitelist in `prompts/_notes_base.md` must match the
  backend sanitiser's `ALLOWED_TAGS` in `notes/html_sanitize.py`. A
  divergence silently strips legitimate markup the prompt invited.
- **Styles are now a VALIDATED whitelist on table tags, not "always
  stripped" (notes WYSIWYG, 2026-06-22, docs/PRD-notes-wysiwyg-formatting.md).**
  `sanitize_notes_html` keeps a value-checked set of inline `style=`
  declarations ONLY on `table/thead/tbody/tr/th/td`. The whitelist mirrors the
  editor's controls — `background-color` + per-side
  `border-top|right|bottom|left` — so no persisted style can be silently
  dropped on a later re-save (`color`, `text-align`, `font-weight` are
  intentionally NOT accepted on table cells; widen `_CSS_PROPERTY_VALIDATORS`
  only when the editor gains a matching control). **Browser border-collapse
  (2026-06-23 fix, real-Chrome incident):** the editor authors per-side
  `border-<side>` longhands, but `editor.getHTML()` runs through the browser's
  CSSOM serialiser which COLLAPSES four uniform per-side borders (a "Border
  all") into the all-sides `border:` shorthand — and partly-uniform borders
  into the `border-width`/`border-style`/`border-color` grouped longhands. So
  those forms reach the sanitiser even though the editor never authors them;
  `_CSS_PROPERTY_VALIDATORS` MUST accept them (`border` via
  `_is_border_shorthand`, the groups via `_is_border_group` — each value still
  shape-checked) or "Border all" is stripped on every save and the cell reverts
  to the default grid. jsdom does NOT collapse (it keeps the longhands), so unit
  tests miss this — pinned by `test_browser_collapsed_border_shorthand_survives_on_td`.
  The editor's `StyledTableCell` parseHTML expands a collapsed `border:` back to
  the four side attrs (`STYLE_PROPS` `fallback`, `cellFormatting.ts`) so a
  reloaded all-border cell still renders. The map shape-checks every value (rejects
  `url()`, `expression()`, malformed border values, disallowed props). Off the
  table, `style=` is still stripped wholesale, so this gotcha's "DB stays
  style-free" rule holds for prose. "No fill"/"no border" persist as explicit
  reset values (`background-color: transparent`, `border: none`), NOT
  attribute-absence (the editor CSS would otherwise repaint the default grid
  + header fill). **Agents still emit style-free HTML** (the prompt forbids
  it); styling is a human-only post-step in the editor. The decision to
  hand-roll the CSS validator (no `bleach` dependency) is recorded in
  docs/PLAN-notes-wysiwyg-formatting.md. On the style-bearing table tags,
  *attributes* are also an explicit allowlist (`_TABLE_STRUCTURE_ATTRS` =
  `colspan`/`rowspan`/`colwidth` + the validated `style=`), NOT the default
  keep-what-isn't-blacklisted — anything else on a table tag is dropped +
  surfaced. Off the table the default-keep branch still applies (e.g. `type`
  on `<ol>`). Pinned by `tests/test_notes_html_sanitize_css.py`.
- **Notes editor v2 (2026-06-23, docs/PRD-notes-editor-v2.md + PLAN-notes-editor-v2.md).**
  The above table-cell mechanism was generalised into a **full rich-text +
  table editor**. Key deltas to the v1 description above:
  - **Tag-aware style gate.** The single "table tags only" gate became
    `_STYLE_PROPS_BY_TAG`: each capability lands ONLY on the tag that produces
    it — `background-color`/`border-*`/`text-align` on table tags, `color` on
    `<span>` (TipTap Color), `background-color`+`color` on `<mark>` (Highlight),
    `text-align` on `<p>`/`<h3>/`<li>` (TextAlign). `ALLOWED_CSS_PROPERTIES`
    widened to add `color` + `text-align`; the value gate is unchanged (still
    rejects `url()`/`expression()`/loose values). `ALLOWED_TAGS` gained the
    human-only marks `u/s/sup/sub/mark/span` — a **superset** of the agent set
    (agents still emit style-free HTML; the prompt lock-step is now
    "agent-emittable ⊆ sanitiser-permitted", not equality).
  - **Constrained colour palette is enforced at the TOOLBAR**
    (`web/src/lib/notesPalette.ts`), NOT re-enforced in the sanitiser — the
    sanitiser validates *safe colour values* only. This is deliberate: a colour
    value isn't a security risk, and a cross-language palette list is the exact
    brittleness v2 set out to remove.
  - **Browser RGB border round-trip.** A browser may serialise a swatch as
    `rgb(255, 255, 255)` (with spaces). `_is_border_shorthand` must treat that
    complete function as one colour token before its width/style/colour
    validation; naive whitespace splitting strips the valid four per-side
    borders and exposes the editor's default grey grid. Pinned by
    `tests/test_notes_html_sanitize_css.py` and
    `tests/test_server_notes_cells_api.py`.
  - **The sanitiser-warning UI panel was REMOVED** (it was developer-facing
    noise; a paste from Excel/Word produced a wall of it). The backend still
    sanitises and still returns `sanitizer_warnings` for logs — the UI just no
    longer surfaces them. Dangerous markup is dropped silently + safely.
  - **One docked two-tier toolbar** (`EditorToolbar`) replaced v1's separate
    `FormatToolbar` + `TableFormatBar`: Tier 1 (Text · Colour · Paragraph)
    always in edit mode; Tier 2 (Table: fill/borders/structure + merge/split/
    header) only when the selection is in a table. The `table-format-bar`
    testid is retained on Tier 2.
  - **Drag-multi-select fixed at the root:** the `.selectedCell` highlight CSS
    was missing (ProseMirror selects on drag but ships no visible highlight),
    and the native `<input type=color>` fill blurred the editor and collapsed
    the selection — both fixed. Merge/split + `colspan` round-trip through the
    sanitiser (`_TABLE_STRUCTURE_ATTRS`) and the `html_to_excel_text` overlay
    (a merged cell flattens once, no duplication).
  - **Per-cell alignment, column width, indentation (2026-06-23 follow-up).**
    Cell alignment is a `textAlign` field on the cell style model
    (`cellFormatting.ts`), set across a `CellSelection` via `applyCellAlign` →
    `text-align` on the `<td>` (distinct from the paragraph TextAlign mark and
    the cosmetic `.is-numeric` runtime class). Column width = TipTap
    `resizable: true`: widths serialise as a standard
    `<colgroup><col style="width">` + `<table style="width">` + cell `colwidth`
    attrs (paste-faithful), so the sanitiser now allows `<colgroup>`/`<col>`
    and `width` **+ `min-width`** on `<table>`/`<col>` (length-validated, no
    `calc()`). `min-width` is load-bearing: TipTap's resizable table emits it on
    EVERY un-sized table/col, so omitting it would strip the editor's own output
    and churn `setContent()` on every table save. The clipboard decorator
    PRESERVES an explicit table `width` (a resized table) instead of forcing its
    `width: 100%` overflow guard over it. Indentation is a custom extension
    (`web/src/lib/notesIndent.ts`:
    `Indent` + `indentBlocks`/`outdentBlocks`) adding an integer level rendered
    as `margin-left` (em) on `<p>/<h3>`; the sanitiser allows `margin-left`
    (positive em/px) on `<p>/<h3>/<li>` only. The clipboard decorator must
    preserve that longhand: its paragraph/heading spacing uses a `margin:`
    shorthand, which would otherwise reset `margin-left` in Word, so indented
    blocks receive explicit top/right/bottom margins instead. **Still deferred:** native
    xlsx-download styling (download stays a text overlay). Pinned by
    `tests/test_notes_html_sanitize_css.py`, `tests/test_notes_html_to_text.py`,
    `web` `cellFormatting`/`notesIndent`/`NotesReviewTab` tests.
- Evidence column is **read-only** in the editor — it's the audit
  trail. `PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}` ignores
  any `evidence` key in the body.
- **Heading-injection scope (2026-04-27 fix):** the writer auto-injects
  `<h3>` lines from `parent_note` + optional `sub_note` structured
  fields. The "Heading markup is writer-owned" rule is scoped strictly
  to those two — in-prose `(a)/(b)/(i)/(ii)` sub-section labels (e.g.
  Note 2.14 Employee benefits → "(a) Short term benefits", "(b)
  Defined contribution plans") MUST be preserved verbatim by the agent
  as `<p><strong>...</strong></p>` paragraph headers in the body. A
  pre-fix prompt let agents over-generalise the writer-owned rule and
  strip these labels, leaving an undifferentiated wall of policy
  prose. Pinned by `tests/test_notes_prompt_phase1.py
  ::test_notes_base_prompt_requires_in_prose_subsection_label_preservation`
  and the worked-example test alongside it. Don't soften either rule
  without updating both tests.
- **Clipboard decoration (2026-04-27 fix):** `web/src/lib/clipboard.ts`
  exports `decorateHtmlForClipboard` which injects inline `style=`
  attributes (border, padding, right-align for numeric cells) into
  every `<table>`/`<th>`/`<td>` *immediately before* the HTML hits
  the clipboard. The DB / sanitiser remain style-free; only the
  clipboard variant carries inline styling because external CSS does
  not travel with paste targets like M-Tool, Word, or Outlook. Without
  it, pasted tables collapse to bare borderless boxes with column
  contents jammed together. Numeric-cell detection uses
  `_NUMERIC_CELL_RE` which matches `1,595` / `(95)` / `-` / `1.5`
  shapes — accountant-style. Spacing (margin, padding) mirrors
  `web/src/components/NotesReviewTab.css` for layout parity with the
  in-app editor preview. Border **colour** intentionally diverges —
  editor uses `#d1d5db` (modern soft grey on the app's white surface);
  clipboard uses `#999` because external editors render lighter
  borders against off-white surfaces and the grid disappears. Update
  both sides + this note if you change either colour. Pinned by
  tests in `web/src/__tests__/clipboard.test.ts`.
- **Configurable paste format (2026-06-22):** `decorateHtmlForClipboard`
  is now option-driven — it takes a `ClipboardFormatOptions`
  (`web/src/lib/clipboardFormat.ts`: `borderStyle` none/single/double,
  `fontSizePt`, `cellPaddingPx`, `paragraphSpacingPx`)
  defaulting to `DEFAULT_FORMAT_OPTIONS`. **Calling with the defaults
  reproduces the previous hard-coded output byte-for-byte** — the
  pinning tests above depend on that equivalence, so keep the defaults
  pinned when editing.
- **Notes-table style THEME (2026-06-23, docs/PLAN-notes-table-theme.md).** The
  `ClipboardFormatOptions` shape was promoted to a full table-style **theme** and
  is now the SHARED, SERVER-SIDE firm default (`XBRL_NOTES_TABLE_STYLE` JSON via
  `/api/settings`, surfaced on `/api/config`) — it **replaced** the old
  per-browser `localStorage` paste-format (so the firm shares one house style;
  `loadGlobalFormat`/`saveGlobalFormat` remain only as a legacy fallback). Two
  new optional fields — `borderColor`, `headerFill` (+ `headerBold`) — are
  **absent in `DEFAULT_FORMAT_OPTIONS` so the byte-compat equivalence above still
  holds**: only a customised theme changes output. The ONE resolved theme
  (`resolveTheme(runOverride, firmDefault)`) drives BOTH surfaces, so preview ==
  paste: the editor reads it as `--nt-*` CSS variables on the `.notes-review-tab`
  root (`themeToCssVars`, `NotesReviewTab.css`); the clipboard reads
  `opts.borderColor`/`headerFill` in `decorateHtmlForClipboard`. **This collapses
  the deliberate editor-`#C9C9C9`-vs-clipboard-`#999` divergence noted below** —
  intentional, since the firm value now unifies them (the historic per-surface
  defaults still apply ONLY when the field is unset). A **per-run override** lives
  on `runs.notes_table_style` (schema v22) and is editable post-run via the Notes
  tab "Table style" picker (PATCH `/api/runs/{id}/notes_table_style`). Per-cell
  manual styles still win over the theme; **"Reset cell to theme"**
  (`resetCellToTheme`) nulls a cell's style attrs so it re-inherits the theme.
  **Run-override is a SNAPSHOT, not a partial diff** (deliberate): the picker
  persists the whole resolved theme for that run, so a later firm-default change
  does NOT flow into an already-overridden run. This matches the "style THIS run"
  mental model and is more predictable than silent per-field inheritance (the
  numeric/style knobs have no "inherit" state anyway — only the colour swatches'
  "Default" does). Saves debounce + clamp via `parseThemeOptions` before hitting
  the server and revert to the last-confirmed value on failure, so a typed
  out-of-range value never 400s and a failed save never strands an unsaved theme.
  Validation (`parseThemeOptions` frontend / `_validate_notes_table_style`
  backend) mirrors the sanitiser's colour rules. Pinned by
  `tests/test_settings_api.py`, `test_run_notes_table_style.py`,
  `test_db_schema_v22.py`, and the `clipboardFormat`/`clipboard`/`cellFormatting`/
  `NotesReviewTab`/`SettingsModal` web tests.
- **Totals double underline + single Copy.** The Notes toolbar has ONE Edit
  surface; **Copy** reads the resolved theme at click time. A totals double
  underline is saved document formatting (`border-bottom: 3px double`) applied
  to the selected table row from that toolbar, not a transient copy override.
- **Numeric notes '000 separator (2026-06-22):** the numeric Notes review
  rows (`NumericCellRow`, sheets 13/14) display grouped (`1,595`) at rest
  and raw while focused, mirroring the face-statement value inputs. The
  formatter (`formatGroupedInput`) moved from `ConceptsPage` to the
  shared `web/src/lib/numberFormat.ts` to avoid a circular import
  (`ConceptsPage` imports `NotesReviewTab`); `ConceptsPage` re-exports it.
  Display-only — stored values stay raw.

Full walkthrough: [docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md).

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
  `prompts/correction.md`**: catch-all rows ("Other …",
  "Miscellaneous …", "Administrative expenses") are for genuinely coarse
  entity disclosures only. Agents must NEVER plug a residual into them
  to balance verify_totals or run_cross_checks. If the breakdown can't
  reconcile, leaf rows stay empty and the run finishes with a flagged
  imbalance — that is correct behaviour.
- **Verifier feedback is non-directive**: `tools/verifier.py` SOFP
  imbalance feedback carries the diagnostic ("equity+liabilities side is
  lower than assets") AND an explicit "do NOT plug a catch-all row".

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
2026-04-27 (Phase 0.3 of `docs/PLAN-stop-and-validation-visibility.md`).
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

The reviewer pass has its own dynamic 8-25 turn cap that's much
tighter (RUN-REVIEW P0-1) and is independent of MAX_AGENT_ITERATIONS;
it fires structured `correction_exhausted` outcomes via
`server._run_reviewer_pass`. (The legacy `_run_correction_pass` was
removed in rewrite Phase 1.1.)

**Wall-clock cap on correction (2026-04-27):**
`CORRECTION_WALLCLOCK_TIMEOUT = 300.0` in `server.py` is
defence-in-depth on top of the dynamic turn cap and the 180s per-turn
timeout. It catches the slow-LLM scenario where many quick-but-not-
quick-enough turns add up past 5 minutes total without either of the
finer-grained guards firing. Override via `XBRL_CORRECTION_WALLCLOCK_S`
(positive seconds; 0 disables). Same constant exists for
`NOTES_VALIDATOR_WALLCLOCK_TIMEOUT` but the validator pass hasn't
been wrapped with the in-loop check yet — that's a follow-up.

### 19. Pipeline-stage + cross-check progress events

Two new SSE event families surface the post-extraction silent dead
zones (added 2026-04-27, Phases 5 & 6 of the same plan):

- **`pipeline_stage`** — coordinator-level stage label, one of
  `extracting | merging | cross_checking | reviewing | re_checking |
  reviewing_notes | done`. Emitted at every phase boundary in
  `run_multi_agent_stream`. The frontend captures the latest stage
  and labels the corresponding silent gap ("Notes reviewer fixing…",
  "Re-running cross-checks…"). The notes pass emits `reviewing_notes`
  (the old `validating_notes` label is retained in the frontend
  `PipelineStage` union for older in-flight streams). Both must stay in
  sync — `web/src/lib/types.ts` + `web/src/pages/ExtractPage.tsx`. Pinned by
  `tests/test_pipeline_stage_events.py`.
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
agent** (`correction/reviewer_agent.py`, `prompts/reviewer.md`) is the
**only extraction → review → export pipeline**. As of the first-principles
rewrite (Phase 1.1) it is MANDATORY: the legacy direct-xlsx pipeline, the
`XBRL_CANONICAL_MODE` opt-out, the legacy `correction/agent.py`, and the
superseded `correction/canonical_agent.py` were all deleted. If the startup
concept-tree bootstrap fails, a run now **fails fast** (`_CANONICAL_BOOTSTRAP_OK
is False` → `_fail_run`) rather than degrading — there is no fallback. Every
code path is fully wired:

- **Extraction (Phase B):** `coordinator.py` threads `run_id` + `db_path`
  into the extraction tools so writes project into `run_concept_facts` as
  they happen.
- **Export (Phase C):** `_export_canonical_workbooks` (server.py) re-renders
  each succeeded statement from `run_concept_facts` via
  `concept_model/exporter.py::export_run_to_xlsx`, then merges. The download
  reflects the authoritative DB facts, not the scratch xlsx the agent wrote.
  Falls back to the agent workbook per-statement when an export applies
  zero facts (peer-review finding 1).
- **Review / correction (Phase D) — the REVIEWER pass:** the autonomous
  canonical correction pass (`_run_canonical_correction_pass`) was
  **replaced** by the reviewer (docs/Archive/PLAN-reviewer-agent.md,
  `correction/reviewer_agent.py`, `prompts/reviewer.md`,
  `server.py::_run_reviewer_pass`). The reviewer investigates the root cause
  of failing cross-checks + open conflicts down the face→sub→PDF chain, applies
  grounded fixes through the guarded `apply_fix` tool (a deterministic no-plug
  guard refuses ungrounded writes + plugs into catch-all/abstract rows,
  invariant #17), and raises only `stuck`/`disputes_prior` flags. Safety is
  **versioning, not write-gating**: `_run_reviewer_pass` calls
  `concept_model/versioning.py::snapshot_facts` ONCE (before any write) so
  "Revert to original" (`revert_to_original`) can restore the original
  extraction with one click. It then re-exports + re-merges so the download
  and Concepts UI stay in sync (no xlsx split-brain). The pass emits the
  `reviewing` pipeline stage. The legacy `correction.md` /
  `_run_correction_pass` / `correction/agent.py` path and
  `correction/canonical_agent.py` were deleted in rewrite Phase 1.1; the
  reviewer-owned `load_open_conflicts` helper (formerly in canonical_agent)
  now lives in `correction/reviewer_agent.py`.
  - **Group / MPERS scoping (reviewer layer).** `concept_nodes` holds EVERY
    imported standard×level (the bootstrap imports all four families), and
    uuids are minted per `(template_id, sheet, row, label)` — so the SAME
    `(sheet, row)` exists under MFRS/MPERS × Company/Group with different
    uuids. The reviewer's `(sheet,row)` resolution (`_resolve_concept` /
    `trace_cascade_source`) MUST therefore be scoped to the run's template
    family via a `template_prefix` (`"{standard}-{level}-"`), mirroring how
    `cell_resolver.resolve_cell` scopes by `template_id` — otherwise it
    resolves an arbitrary template's concept. The reviewer pass threads
    `filing_standard` (not just `filing_level`) into `ReviewerDeps` for this.
    On Group filings both `entity_scope`s exist; the tools default to Company,
    so the review packet surfaces each failing check's `[group]`/`[company]`
    tag as an explicit `entity_scope='…'` hint and `prompts/reviewer.md`
    requires the reviewer to honour it. The review diff resolves cells through
    `concept_targets` (falling back to `render_*`) so Group/SOCIE coordinates
    display correctly. Pinned by `tests/test_reviewer_tools.py`
    (`test_resolve_concept_is_template_scoped`, `…surfaces_group_scope`) and
    `tests/test_reviewer_versioning.py::test_diff_prefers_target_coord_over_render`.
  - **Auto-trigger toggle:** the automatic reviewer launch is gated on
    `XBRL_AUTO_REVIEW` (default on; Settings checkbox, surfaced via
    `/api/settings` + `/api/config`). When off, a run with failures/conflicts
    finishes without the reviewer and the user triggers it manually.
  - **Clean-run spot-check (issue 1, 2026-06-21):** a run with NO failing
    checks and NO open conflicts still gets a grounded sanity pass when
    `XBRL_SPOT_CHECK` is on (default on; **independent** of `XBRL_AUTO_REVIEW`,
    which only gates the failure path). It REUSES `_run_reviewer_pass` via a new
    `spot_check` arg (`"light"`/`"full"`) — so snapshot → fix → re-export →
    revert are unchanged — bypassing the `n_items == 0` early return.
    `XBRL_SPOT_CHECK_MODE` picks depth: `light` (default) swaps to the tight
    `prompts/spot_check.md` body + a 6/8-turn cap
    (`compute_spot_check_turn_cap`); `full` reuses the holistic `reviewer.md`
    body + the reviewer's base cap. Both render a SPOT-CHECK packet (no failing
    checks to inline) via `render_reviewer_prompt(spot_check_mode=…)`. Both
    settings round-trip through `/api/settings` + `/api/config` and the General
    settings tab. **Run-status semantics (peer-review HIGH):** the spot-check
    outcome carries a `spot_check` tag so a spot-check that merely EXHAUSTS its
    tight cap does NOT flag a clean run `correction_exhausted` (it's advisory),
    while a spot-check that genuinely FAILS to run (`reviewer_failed` — snapshot
    / model-build / no-facts / tool error, excluding the soft
    `reviewer_exhausted`) tips the run to `completed_with_errors` rather than
    hiding a failed CORRECTION row under a green badge. The suite defaults the
    toggle OFF (`tests/conftest.py`) so deterministic pipeline-count tests
    aren't perturbed; opt in with `monkeypatch.setenv`. Pinned by
    `tests/test_reviewer_pipeline.py`
    (`test_spot_check_runs_on_clean_run`),
    `tests/test_e2e.py::test_clean_run_fires_spot_check_when_enabled`,
    `tests/test_reviewer_agent.py` (turn cap + body/packet swap), and
    `tests/test_settings_api.py` (round-trip).
  - **Reviewer model:** user-selectable. `XBRL_DEFAULT_MODELS["reviewer"]`
    (Settings) sets the default for the automatic pass; the Review tab's model
    dropdown sends a per-request `model` override to `/re-review`. Both fall
    back to the run's extraction model when unset (`reviewer` is now a member
    of `_AGENT_ROLES`).
- **Frontend:** the **Review** tab (reviewer diff + flags + revert/re-review,
  `web/src/components/ReviewTab.tsx`) and the Values tab (`RunDetailView.tsx`)
  plus the `/concepts/{id}` alias are visible whenever `/api/config` reports
  `canonical_mode: true`. Reviewer API: `GET /api/runs/{id}/review`,
  `POST /api/runs/{id}/flags/{flag_id}/answer`, `POST /api/runs/{id}/re-review`,
  `GET /api/runs/{id}/re-review/status`, `POST /api/runs/{id}/revert-to-original`.
  - **Manual re-review is async (background thread + poll).** A pass can run
    for minutes, so `POST /re-review` only *launches* it — on a dedicated
    thread with its own event loop (`asyncio.run`), tracked in the **durable
    `run_review_tasks` table** (schema v13, gotcha #11) keyed by run_id — and
    returns `{ok, status:"running", model}` immediately. The Review tab polls
    `GET /re-review/status` (`idle` | `running` | `done` + the finished
    outcome) for the result. A dedicated thread (not a raw `asyncio.create_task`)
    is deliberate: a detached create_task is cancelled when the request scope
    tears down (and silently lost under TestClient), whereas the thread loop is
    isolated from request teardown and keeps the model's async HTTP client
    bound to the loop that uses it. A re-entrancy guard reports an in-flight
    pass instead of double-launching one over the same run's facts.
    - **Durable across restarts (rewrite Phase 5.3).** The pass state lives
      in `run_review_tasks` (one row per run; relaunch overwrites it), not an
      in-process dict, so a *finished* outcome survives a server restart and a
      poll can still fetch it. `server._save_review_task` /
      `repo.fetch_review_task` are the write/read helpers; the status endpoint
      and re-entrancy guard both read the table. Because the daemon thread
      dies with the process, `server._lifespan` calls
      `repo.reconcile_stale_review_tasks` at startup to flip any row left
      `running` by a crash into a terminal `done` error ("Server restarted
      while the re-review was running.") so the poll resolves and a relaunch
      isn't blocked. Pinned by `tests/test_db_schema_v13.py` (logic) +
      `tests/test_reviewer_routes.py`
      (`test_re_review_outcome_survives_simulated_restart`,
      `test_stale_running_task_reconciled_at_startup`).
      The launch's initial `running` write is **mandatory, not best-effort**
      (peer-review MEDIUM): the re-entrancy guard reads that row, so a
      swallowed launch-write would let a second POST start a duplicate pass
      over the same facts. `re_review` writes `running` directly and returns
      **503** if it fails (no thread started); only the terminal `done`
      write goes through the swallowing `_save_review_task`. Pinned by
      `test_re_review_launch_persist_failure_returns_503_and_no_thread`.

**Canonical mode cannot be disabled** (rewrite Phase 1.1). There is no
opt-out flag and no legacy pipeline to fall back to. If the concept-tree
bootstrap fails at startup, runs fail fast with a clear error instead of
degrading — fix the bootstrap (check logs, restart) rather than reaching for
a fallback that no longer exists. The Values tab, concept-tree review UI, and
reviewer pass are always present.

Plan / PRD docs (historical context, not API contracts):
[docs/PLAN-canonical-concept-model.md](docs/PLAN-canonical-concept-model.md),
[docs/PLAN-canonical-concept-model-phase1.md](docs/PLAN-canonical-concept-model-phase1.md),
[docs/PRD-canonical-concept-model.html](docs/PRD-canonical-concept-model.html).

**Cross-sheet rollup linkage (2026-05-28, schema v11, "render twice"):**
Face statement rows that pull their value from a sub-sheet total via
a cross-sheet formula (`='SOFP-Sub-CuNonCu'!Bn`) share ONE
`concept_uuid` with the sub-sheet's `*Total` row. The parser already
emits both nodes with that shared UUID; v11 adds
`concept_render_aliases` so the face render coord is preserved
alongside the sub coord, instead of being dropped during importer
dedup.

- **Importer (`concept_model/importer.py`):** demoted face entries
  land in `concept_render_aliases`. Edge resolution now builds a
  `coord→uuid` map from the FULL `concepts` list (not the dedup'd
  `seen`), so face-sheet COMPUTED rows like *Total non-current
  assets* still wire their child edges to PPE — pre-fix every
  cross-sheet rolled-up child was silently dropped and cascade
  understated every face total.
- **Cell resolver (`concept_model/cell_resolver.py`):** an agent write
  to a face coord falls back to the alias table on miss, preserving
  the canonical UUID instead of silently skipping the write.
- **Concepts endpoint (`concept_model/concepts_routes.py`
  `/api/runs/{id}/concepts`):** emits ONE extra view-row per alias,
  with `is_alias: true` and render coords swapped to the alias
  location. Sorted into its face-sheet section so the Review/Values
  page mirrors the workbook (one face row + one sub row, same
  concept).
- **Exporter (`concept_model/exporter.py`):** facts only route to the
  primary `concept_nodes.render_*` coord. Alias coords are **never**
  written — the workbook's cross-sheet formula stays live so Excel
  recomputes the value. (Pre-existing code; pinned now by
  `tests/test_canonical_cross_sheet_rollup.py::test_exporter_preserves_cross_sheet_formula_on_alias_coord`.)
- **Frontend (`web/src/pages/ConceptsPage.tsx`):** `ConceptRow` carries
  `is_alias?`. Alias rows render with an italic "(linked)" marker and
  are never editable (the backend already drops `editable`, frontend
  enforces it defensively). React keys are composite
  (`${concept_uuid}@${sheet}:${row}:${col}`) so primary + alias don't
  collide.

Pinned by `tests/test_db_schema_v11.py`,
`tests/test_canonical_cross_sheet_rollup.py` (5 tests),
`tests/test_concepts_routes.py::test_get_concepts_emits_alias_rows_with_face_coords`,
`web/src/__tests__/ConceptsPage.test.tsx::"alias view-rows render
with (linked) marker and stay read-only"`.

**PY columns in canonical download (2026-05-28):**
Linear Company filings in `concept_model/exporter.py` previously
dropped every fact that wasn't `(CY, Company)`, so the downloaded
canonical xlsx had empty col C (PY) on every face statement even
though `run_concept_facts` carried the PY data. Routing now mirrors
`cell_resolver.resolve_cell`: CY → `render_col` (default B), PY → C,
Group facts dropped (they only apply to Group filings, which use the
`concept_targets` branch). Pinned by
`tests/test_canonical_export.py::test_py_facts_export_to_col_c_on_linear_company`
and `::test_group_facts_dropped_on_company_filing`.

**Single-lookup routing (2026-05-30, rewrite Phase 6.1):**
The exporter no longer carries the three-way `render_col`/PY=C routing
fallback described above — it now does ONE `concept_targets` lookup per
fact for every shape. The importer precomputes a target row for every
dimension a filing renders: `import_company_targets` (Company B=CY/C=PY)
mirrors `import_group_targets` (B/C/D/E); matrix (SOCIE) targets stay
inline; `bootstrap._import_one` calls the company variant for non-group
linear templates. The CY=B/PY=C *result* is unchanged (the PY-columns
and Group-drop behaviour above still hold) — only the mechanism moved
from an inline fallback to a precomputed table. **Aliases are still NOT
targets** (both importers iterate `concept_nodes` primary coords only),
so cross-sheet formula cells stay live. An in-scope fact with no
precomputed target now RAISES (importer-bug signal) instead of silently
falling back. Tests that hand-roll a Company DB must call
`import_company_targets(db, template_id)` after `import_template` (as the
Group fixtures already call `import_group_targets`). Pinned by the same
`test_canonical_export.py` / `test_canonical_cross_sheet_rollup.py` /
`test_phase4_group.py` suites.

### 22. Agent workbook tools must serialise + atomic-save shared files

pydantic-ai (1.77+, default `parallel_execution_mode`) runs batched
`@agent.tool` calls as concurrent `asyncio` tasks; **sync** tools dispatch
onto separate anyio worker threads. openpyxl's `wb.save()` is a non-atomic
in-place zip rewrite — if a second tool's `load_workbook` hits the same path
mid-save it reads a truncated zip → `EOFError` (Windows incident,
2026-05-29). Any agent tool that loads + saves the **same** workbook path is
exposed.

The notes post-validator (`notes/validator_agent.py`) — whose `read_cell`
and `rewrite_cell` both target `merged_workbook_path` — is fixed with two
coupled defences: a per-run `NotesValidatorAgentDeps.io_lock`
(`threading.Lock`) wrapping every load/save, and `_atomic_save_workbook`
(tempfile + `os.replace`, atomic on Windows + POSIX) so even an un-locked
reader sees old-or-new, never partial. The validator's `EOFError` was
caught at `server.py` (`"Notes validator run failed"`), so it failed soft
(no dedup) rather than crashing the run. Pinned by
`tests/test_notes_validator_agent.py::TestWorkbookIoRaceSafety`.

**Closed everywhere (2026-06-12, PLAN-orchestration-hardening item 8):** the
helper was promoted to `utils/workbook_io.py::atomic_save_workbook` (the
validator keeps a `_atomic_save_workbook` re-export alias for its
import/test contract) and every live-path saver now routes through it —
`tools/fill_workbook.py`, `concept_model/exporter.py`, `notes/writer.py`,
`workbook_merger.py` (`tools/recalc.py` and `notes/persistence.py` already
used tmp+replace shapes). Pinned by `tests/test_workbook_io_atomic.py`.
If you add a NEW tool that writes a workbook another tool reads, use the
shared helper — never a bare in-place `wb.save(path)`.

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
  `python -m auth.manage add-user you@firm.com --name "Your Name"` (add `--admin`
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

The `docconvert/` package + "Readable Doc" frontend page (a standalone, offline
scanned-PDF → HTML/Word converter built on Docling) was **removed** — see
docs/PLAN-deprecate-docconvert.md. The `doc_conversions` table (schema v21)
remains as an inert artifact because the migration chain replays every step
(gotcha #11), but no code reads or writes it. The heavy deps it alone pulled in
(`docling`, `torch`, `onnxruntime`, `rapidocr`, `easyocr`, `pypandoc_binary`,
`python-docx`) and the `models/` weight bundle are gone.

## Testing

```bash
# Backend (from repo root) — excludes live LLM tests by default
python -m pytest tests/ -v

# Live E2E (uses TEST_MODEL from .env, needs matching API key)
python -m pytest -m live -v

# Frontend
cd web && npx vitest run

# Compare a filled workbook against a reference
python compare_results.py SOFP-Xbrl-reference-FINCO-filled.xlsx output/run_001/filled.xlsx
```

**High-value test files** (full catalog in `tests/`):

- `tests/test_e2e.py` — full 5-agent mocked pipeline.
- `tests/test_cross_checks.py` — cross-check framework + per-check unit tests.
- `tests/test_server_run_lifecycle.py` — runs-row pre-validation + terminal-status contract (see gotcha #10).
- `tests/test_db_schema_v2.py` / `test_db_schema_v3.py` — migration steps + fresh-init invariants (see gotcha #11).
- `tests/test_notes_retry_budget.py` — max-1-retry contract + failure side-logs.
- `tests/test_mpers_wiring.py` + `tests/test_mpers_generator.py` — MPERS phase-by-phase.
- `tests/test_filing_level.py` — Company vs Group routing end-to-end.
- `tests/test_page_hints.py` — scout hints are soft (gotcha #13).
- `web/src/__tests__/*.test.{ts,tsx}` — frontend reducers + components.

Some tests auto-skip when sample data is absent (e.g. `test_pdf_viewer.py`).

## How to Work Here (for AI agents)

- **Don't edit anything under `XBRL-template-*/backup-originals/`** —
  snapshot archives, used for drift diffing.
- **Don't run the MPERS generator without `--snapshot`** — you will destroy
  the previous snapshot.
- **Don't convert inline styles back to Tailwind** (gotcha #7).
- **Don't re-introduce `allowed_pages` filtering on scout hints** (gotcha #13).
- **Don't remove `_safe_mark_finished`'s try/except** (gotcha #10).
- **Don't add deterministic label-matching to the notes pipeline** — it's
  intentionally all LLM judgement.
- **Don't soften the abstract-row guard or the no-residual-plug prompts**
  (gotcha #17) — both encode the 2026-04-26 SOPL-Analysis incident where the
  agent wrote values onto section headers and used catch-all rows as a
  balancing plug.
- **`docs/Archive/` is read-only** — completed plans and fix reports kept for
  audit trail.
- **`docs/PLAN-*.md` are historical context**, not API contracts. Treat them
  as "why we did X" snapshots, not load-bearing specs.
- **For broad questions**, start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
  for cross-file impact, check [docs/SYNC-MATRIX.md](docs/SYNC-MATRIX.md).

## Deeper References

| Doc | When to read |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full module map + data flow |
| [docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md) | Notes subsystem deep-dive |
| [docs/MPERS.md](docs/MPERS.md) | MPERS filing-standard deep-dive |
| [docs/SYNC-MATRIX.md](docs/SYNC-MATRIX.md) | Cross-file impact for a given change |
| [docs/PORTING-WINDOWS.md](docs/PORTING-WINDOWS.md) | Mac → Windows porting checklist |
| [docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md](docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md) | SOFP formula-offset incident audit trail |
| `docs/workflows/*.md` | Per-statement fill-workflow notes |
| `docs/xbrl-field-descriptions.md` | Field reference for the XBRL taxonomy |
