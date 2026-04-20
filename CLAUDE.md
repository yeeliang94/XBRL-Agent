# XBRL Agent — AI Agent Reference

## What This Is

A standalone XBRL extraction agent for Malaysian financial statement PDFs. Extracts data
into SSM MBRS XBRL Excel templates. Uses PydanticAI with multi-provider LLM support
(Google Gemini, OpenAI, Anthropic) via a LiteLLM proxy on all platforms. Handles all 5
primary financial statements: SOFP, SOPL, SOCI, SOCF, and SOCIE — each with variant
support (e.g. CuNonCu vs OrderOfLiquidity for SOFP). Multiple agents run concurrently
via the coordinator, results are merged into a single workbook, and cross-statement
checks validate consistency.

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

# Windows (enterprise proxy) — just double-click start.bat
# Or: start.bat
# Web UI at http://localhost:8002
```

## Architecture

```
run.py              CLI entry point — runs coordinator for 1-5 statements
server.py           FastAPI + SSE web server (POST /api/run/{session_id}, /api/runs history endpoints, SPA fallback)
coordinator.py      Fans out N extraction agents concurrently via asyncio.gather
agent_tracing.py    Shared trace-writing + `MAX_AGENT_ITERATIONS=50` cap used by face / notes coordinators and scout
extraction/
  agent.py          Generic extraction agent factory (one per statement type)
statement_types.py  StatementType enum, variant registry, template path resolver (routes to Company/ or Group/ by filing level)
prompts/            Per-statement system prompt templates (sofp.md, sopl.md, etc.)
  _group_overlay.md   Group extraction instructions for SOFP/SOPL/SOCI/SOCF (6-column layout)
  _group_socie_overlay.md  Group SOCIE instructions (4 vertical row blocks)
  _notes_base.md      Shared notes persona: output contract, 30K char limit, multi-page continuation
  notes_*.md          Per-template notes prompts (corporate_info / accounting_policies / listofnotes / issued_capital / related_party)
tools/
  template_reader.py   Read template structure
  pdf_viewer.py        Render PDF pages to images
  fill_workbook.py     Write values to Excel (label matching)
  verifier.py          Check statement balance/totals (formula evaluator)
cross_checks/
  framework.py      Cross-check runner (run_all) + result protocol
  sofp_balance.py    Total assets = Total equity + liabilities
  sopl_to_socie_profit.py   SOPL profit = SOCIE profit row
  soci_to_socie_tci.py      SOCI TCI = SOCIE TCI row
  socie_to_sofp_equity.py   SOCIE closing equity = SOFP total equity
  socf_to_sofp_cash.py      SOCF cash = SOFP cash movement
notes_types.py      NotesTemplateType enum + registry + notes_template_path() routing Company/ vs Group/
notes/
  agent.py          Notes agent factory (one per notes template) with shared _notes_base.md prompt
  coordinator.py    Fans out notes agents in parallel; max-1 retry per sheet + failure side-log
  listofnotes_subcoordinator.py  Sheet-12 only: 5 parallel sub-agents, row-112 unmatched concatenation
  payload.py        NotesPayload dataclass (chosen_row_label, content, evidence, numeric_values, …)
  writer.py         Writes NotesPayloads to xlsx; 30K-char guard + evidence col D/F + Group/Company rules
workbook_merger.py  Merges per-statement workbooks into single output file (face sheets first, notes after)
db/
  schema.py         SQLite DDL + v1→v2 migration (runs lifecycle columns)
  repository.py     CRUD + history queries (list/filter/detail/delete, mark_run_finished/merged)
  recorder.py       SSEEventRecorder — persists live events during a run
scout/
  agent.py          PydanticAI scout agent (single agent with 6 tools)
  runner.py         Backward-compatible entry point (re-exports from agent.py)
  toc_locator.py    Deterministic TOC page finder
  toc_parser.py     TOC text parser (English + Malay, combined titles)
  variant_detector.py  Deterministic variant signal scorer (cross-check tool)
  notes_discoverer.py  Note page discovery from face-page references
  calibrator.py     Legacy page calibrator (kept for reference, not used by agent)
  vision.py         LLM vision helpers (TOC extraction for scanned PDFs)
  infopack.py       Typed output: page refs, variants, confidence per statement
web/                Vite + React frontend (inline styles, tab-based multi-agent UI)
  src/App.tsx         Router shell — /extract and /history views share TopNav and app state
  src/pages/
    ExtractPage.tsx     Main extraction view — upload, PreRunPanel, agent tabs, results
    HistoryPage.tsx     Past-runs browser (filters + list + detail modal)
  src/components/
    TopNav.tsx          Extract / History nav bar
    AgentTimeline.tsx   Terminal-style tool timeline (live + history replay)
    ToolCallCard.tsx    Single tool-row primitive shared by live + history
    HistoryList.tsx     Run table with status badges
    HistoryFilters.tsx  Search / status / model / date filters
    RunDetailModal.tsx  Wraps RunDetailView in a modal for the history list
    RunDetailView.tsx   Replay a past run — agents, timeline, cross-checks, download
    SuccessToast.tsx    Ephemeral success notification after merge
    icons.tsx           Shared icon primitives (CloseIcon, RerunIcon, settings gear) — replaces inline HTML entities
  src/lib/
    toolLabels.ts       humanToolName / argsPreview / resultSummary — shared label logic
    buildToolTimeline.ts SSEEvent[] → ToolTimelineEntry[] reducer (live + persisted)
    runStatus.ts        Run-status badge/color helpers
    modelId.ts          `displayModelId` — strip PydanticAI repr() wrappers from persisted model ids for history UI
    time.ts             `formatMMSS` / `formatElapsedMs` — shared elapsed-time formatters
    appReducer.ts       `appReducer` / `agentReducer` / notes tab labels (extracted from App.tsx)
config/
  models.json       Available models registry (id, provider, display name)
litellm_config.yaml LiteLLM proxy config — routes models to correct provider APIs
start.bat           Windows startup script (finds Python/Node, sets UTF-8)
start.sh            Mac/Linux startup script (launches LiteLLM proxy + server)
XBRL-template-MFRS/ Template Excel files organized by filing level
  Company/           Company-level templates (4 cols: label, CY, PY, source)
  Group/             Group-level templates (6 cols: label, Group CY, Group PY, Company CY, Company PY, source)
```

## LLM Provider Setup

All LLM calls are routed through `_create_proxy_model()` in `server.py`. Both `run.py`
(CLI) and `server.py` (web UI) use this single function so behaviour is consistent.

### Multi-Provider Routing

`_create_proxy_model()` has two modes:

1. **Proxy mode** (`LLM_PROXY_URL` is set): All models route through an OpenAI-compatible
   LiteLLM proxy endpoint. Used on Windows (enterprise) and Mac (local dev via `start.sh`).
2. **Direct mode** (`LLM_PROXY_URL` is empty): Provider detected from model name prefix:
   - `gpt-*`, `o1-*`, `o3-*`, `o4-*` → OpenAI API (uses `OPENAI_API_KEY`)
   - `claude-*` → Anthropic API (uses `ANTHROPIC_API_KEY`)
   - Everything else → Google Gemini API (uses `GEMINI_API_KEY` / `GOOGLE_API_KEY`)

### Local LiteLLM Proxy (Mac — simulates enterprise)

`start.sh` launches a local LiteLLM proxy on port 4000 to simulate the Windows enterprise
proxy. This ensures the same OpenAI-compatible code path is exercised on Mac.

```
Browser/CLI → server.py → LiteLLM proxy (:4000) → Gemini / OpenAI / Anthropic APIs
```

- Config: `litellm_config.yaml` (model routing, API keys via env vars)
- Master key: `sk-local-dev-key` (set in litellm_config.yaml `general_settings`)
- `start.sh` auto-sets `LLM_PROXY_URL=http://localhost:4000/v1` at runtime
- Logs: `litellm.log`
- Falls back to direct mode if proxy fails to start

### Enterprise Proxy (Windows)

All LLM traffic goes through `https://genai-sharedservice-emea.pwc.com` using
OpenAI-compatible protocol. Direct Google API calls are blocked (403).

```env
# .env on Windows
LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com
GOOGLE_API_KEY=sk-xxxx   # from Bruno -> Collection -> Auth tab
TEST_MODEL=vertex_ai.gemini-3-flash-preview
```

### .env Configuration

```env
# Required: At least one provider API key
GEMINI_API_KEY=            # Google Gemini (direct or via proxy)
OPENAI_API_KEY=            # OpenAI models (gpt-5.4, etc.)
ANTHROPIC_API_KEY=         # Anthropic models (claude-sonnet-4-6, etc.)

# Proxy (set by start.sh on Mac, manual on Windows)
LLM_PROXY_URL=             # Empty = direct mode, set = proxy mode
GOOGLE_API_KEY=            # Also used as proxy auth key on Windows

# Model defaults
TEST_MODEL=google-gla:gemini-3-flash-preview   # Default extraction model
SCOUT_MODEL=google-gla:gemini-3-flash-preview  # Default scout model
```

### PydanticAI Model Creation (v1.77+)

```python
# Proxy path (OpenAI-compatible)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
model = OpenAIChatModel(name, provider=OpenAIProvider(base_url=url, api_key=key))

# Direct Google path
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
model = GoogleModel(name, provider=GoogleProvider(api_key=key))

# Direct Anthropic path
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
model = AnthropicModel(name, provider=AnthropicProvider(api_key=key))
```

**DO NOT use** `base_url=` or `openai_client=` as direct kwargs to `OpenAIModel` — those were removed in pydantic-ai 1.x. Always use the `provider=` pattern above.

### Temperature Constraint

For Gemini 3 models through the proxy, temperature MUST stay at 1.0. Lower values cause failures or infinite loops.

## Known Issues & Gotchas

### 1. Windows Encoding: PYTHONUTF8=1 is Required

Windows defaults to `charmap` codec which crashes on Unicode text from PDFs. The `start.bat` sets `PYTHONUTF8=1` before running Python. If running manually:

```cmd
set PYTHONUTF8=1
python server.py
```

All `write_text()` calls also have `encoding="utf-8"` as a safety net.

### 2. pydantic-ai Version: Pinned to >= 1.77.0

The project uses pydantic-ai 1.77+ API. Key differences from older versions:
- `Agent._function_tools` **does not exist** — cannot monkey-patch tools
- Model creation uses `OpenAIModel(name, provider=OpenAIProvider(...))` 
- `OpenAIModel` is deprecated in favor of `OpenAIChatModel` (but still works)
- Tool event streaming uses `agent.iter()` + `node.stream()` — no `event_callback` or monkey-patching

### 3. Template Formula Fixes (2026-04-07) — SOFP Sub-Sheets

The XBRL templates in `XBRL-template-MFRS/` were derived from SSM MBRS v2.0 originals
(`SSMxT_2022v1.0/`). The originals had 20 extra header rows and different column layout
(E/F vs our B/C). A prior conversion correctly adjusted column refs and cross-sheet refs
but **missed same-sheet cross-section subtotal references**, leaving them +20 rows off.

**Fixed (2026-04-07):**

- `01-SOFP-CuNonCu.xlsx` / `SOFP-Sub-CuNonCu`: 30 formula cells across 15 rows had
  cross-section subtotal refs pointing +20 rows into wrong accounting sections (e.g.,
  "Total cash" summed equity rows instead of cash rows). All fixed by subtracting 20
  from the broken refs. Within-section and cross-sheet refs were already correct.

- `02-SOFP-OrderOfLiquidity.xlsx` / `SOFP-Sub-OrdOfLiq`: 4 formulas had different bugs
  (not a clean +20 offset) — wrong children from XBRL hierarchy parsing errors. Fixed by
  regenerating formulas from the XBRL calculation linkbase (`cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml`).
  Rows 148 (Total cash), 168 (Total issued capital), 241 (Total borrowings), 295 (Total payables).

**Originals backed up to:** `XBRL-template-MFRS/backup-originals/`

**All other templates verified clean:** 03-SOPL-Function, 04-SOPL-Nature, 05-SOCI-BeforeTax,
06-SOCI-NetOfTax, 07-SOCF-Indirect, 08-SOCF-Direct, 09-SOCIE, 10-14 Notes.

See `docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md` for full details, broken formula table,
and XBRL linkbase verification methodology.

### 4. fill_workbook Row Matching — Off-by-One with Reference File

The reference file (`SOFP-Xbrl-reference-FINCO-filled.xlsx`) has a different row layout than the template (`SOFP-Xbrl-template.xlsx`) — an extra blank row near the top shifts all sub-sheet rows down by 1. The agent writes to correct rows **per the template**, but comparisons against the reference show everything off by 1.

This is NOT a bug in fill_workbook — it's a template/reference mismatch. When comparing results, always compare against the template row layout, not the reference.

**Verified (2026-04-03):** Label matching in fill_workbook.py is correct. "Retained earnings" matches to row 40 (*Retained earnings) properly. The compare_results.py script shows false "EXTRA"/"MISSING" because it compares row-by-row against a reference with different row numbering. To properly validate results, open the filled Excel in Excel (not openpyxl) so formulas evaluate, then check totals balance.

**Actual extraction quality gaps:**
- Agent extracts ~24 fields but FINCO SOFP has ~35+ data-entry cells
- "Deferred income" on sub-sheet not filled (agent puts it in "Contract liabilities" on main sheet only)
- Coverage could be improved by enhancing the system prompt to emphasize sub-sheet breakdowns

### 5. LiteLLM SSL Warning is Safe to Ignore

```
LiteLLM:WARNING: Failed to fetch remote model cost map... [SSL: CERTIFICATE_VERIFY_FAILED]
```

Enterprise firewall blocks GitHub. LiteLLM falls back to local pricing data. Suppressed via `litellm.suppress_debug_info = True` in server.py. On Mac, the local LiteLLM proxy may also show this warning — it is harmless.

### 6. Token Counts are Approximate

`_track_turn()` in agent.py records zeros for per-turn tokens because PydanticAI handles counting internally. After the run completes, `server.py` backfills totals from `result.usage`. The token dashboard shows real numbers only after completion.

### 7. Frontend Uses Inline Styles, Not Tailwind

Tailwind CSS v4 didn't load reliably on Windows (the upload button was unclickable). All components now use inline `style={}` props. This is intentional — don't convert back to className-based Tailwind.

### 8. Node.js May Not Be on PATH (Windows)

`start.bat` auto-discovers Node.js in `C:\Program Files\nodejs\`. If it's elsewhere, set PATH manually before running.

### 9. Output Directory Structure

```
output/
  run_001/     # CLI runs (auto-numbered)
  run_002/
  {uuid}/      # Web UI runs (UUID per session)
```

`run.py` uses `Path(__file__).resolve().parent / "output"` as the base — works regardless of working directory.

### 10. Run Lifecycle Contract — `runs` Row Created Before Validation

`run_multi_agent_stream` in `server.py` creates the `runs` audit row **before**
parsing statement types, resolving variants, or building models. This is
deliberate: if validation or proxy-model creation fails, the History page still
captures the failed run instead of silently dropping it. The orchestration body
is wrapped in try/except/finally so every exit path — success, exception,
`CancelledError`, or client disconnect — leaves the row in a terminal status
(`completed`, `completed_with_errors`, `failed`, `aborted`) and never `running`.

`mark_run_merged` is called immediately after a successful merge, **before** the
final status update, so `GET /api/runs/{id}/download/filled` has a durable
pointer to `filled.xlsx` even if later persistence work crashes.

`_safe_mark_finished` in `server.py` swallows audit-write exceptions so error
handlers never double-fault — a DB write failure during an already-failing run
gets logged, not re-raised. Don't "fix" this by removing the try/except.

### 11. DB Schema Version 2 — Auto-Migration on Startup

`db/schema.py` carries `CURRENT_SCHEMA_VERSION = 2`. `init_db` detects v1
databases and runs `ALTER TABLE runs ADD COLUMN …` for the seven lifecycle
fields (`session_id`, `output_dir`, `merged_workbook_path`, `run_config_json`,
`scout_enabled`, `started_at`, `ended_at`). The migration is idempotent and
backfills `started_at` from `created_at` for legacy rows so duration math
doesn't explode.

SQLite `ALTER TABLE` cannot add `NOT NULL` columns without defaults — every
entry in `_V2_MIGRATION_COLUMNS` is either nullable or carries a safe default.
The `status` column has no CHECK constraint on purpose: adding a new status
enum value should not require a full-table migration.

### 12. Filing Level — Company vs Group Templates

Each run has a single `filing_level` (`"company"` or `"group"`, default `"company"`)
that flows from the frontend toggle (or `--level` CLI flag) through the entire pipeline:
`RunConfigRequest` → `RunConfig` → `template_path()` → agent prompts → verifier → cross-checks → history.

`template_path()` in `statement_types.py` routes to `XBRL-template-MFRS/Company/` or
`XBRL-template-MFRS/Group/` based on the level. Both directories contain identically
named files — the column structure inside the Excel differs:

- **Company templates:** 4 columns (A=label, B=CY, C=PY, D=source)
- **Group templates:** 6 columns (A=label, B=Group CY, C=Group PY, D=Company CY, E=Company PY, F=source)
- **Group SOCIE is special:** same 24 equity-component columns but 4 row blocks
  (rows 3-25 Group CY, 27-49 Group PY, 51-73 Company CY, 75-97 Company PY)

For Group filings, the agent extracts both consolidated and standalone figures.
Cross-checks and the verifier run twice — once for Group columns, once for Company
columns — and report results separately.

Root-level template xlsx files no longer exist. All templates live in `Company/` or `Group/`.

### 13. Scout Page Hints are Soft Guidance Only

When scout is ON, extraction agents receive `page_hints` (face_page + note_pages) in their
system prompt as recommended starting points. Agents can freely view **any** PDF page —
there is no `allowed_pages` enforcement or page filtering. The `view_pdf_pages` tool only
validates that requested pages are within the document's 1-N range.

Do NOT re-introduce page restriction logic (no `allowed_pages`, no "disallowed" filtering).
Tests in `test_page_hints.py` assert this contract with negative assertions.

### 14. Notes Feature — Five Supplementary Templates (Sheets 10-14)

The notes pipeline fills MBRS templates 10-14 in parallel with face statements.
Discovery is PDF-first: scout extracts a `notes_inventory: list[NoteInventoryEntry]`
from the PDF, then per-template agents read those notes and write content into the
matching template rows. There is no deterministic matching, no OCR, no synonym
dictionary — every matching decision is pure LLM judgement on the rendered PDF pages.

The 5 templates and their runtime shape:

| Template | Sheet | Runner |
|---|---|---|
| `10-Notes-CorporateInfo.xlsx` | `Notes-CI` | single agent |
| `11-Notes-AccountingPolicies.xlsx` | `Notes-SummaryofAccPol` | single agent |
| `12-Notes-ListOfNotes.xlsx` | `Notes-Listofnotes` | **5 parallel sub-agents** |
| `13-Notes-IssuedCapital.xlsx` | `Notes-Issuedcapital` | single agent (numeric) |
| `14-Notes-RelatedParty.xlsx` | `Notes-RelatedPartytran` | single agent (numeric) |

Sheet 12 fans out because it has 138 target rows — one agent choosing among
138 labels would be slow and error-prone. The sub-coordinator splits scout's
inventory into 5 page-contiguous batches, runs 5 agents in parallel, then
aggregates payloads for one final workbook write.

**Retry budget (PLAN §4 E.1):** every single notes agent is retried at most
once on non-cancellation errors. Sub-agents for Sheet 12 have the same
max-1-retry budget. Exhausted budgets emit a side-log:

- `notes_<TEMPLATE>_failures.json` — single sheet retry exhaustion
- `notes12_failures.json` — Sheet 12 sub-agents that lost coverage
- `notes12_unmatched.json` — notes funnelled into row 112 ("Disclosure of
  other notes to accounts"); only written when non-empty

**Cell format:** plain text, `\n\n` for paragraph breaks (Excel renders as
Alt+Enter line breaks), ASCII-aligned tables. Cap is 30,000 chars
(`notes.writer.CELL_CHAR_LIMIT`); longer content is truncated with a
`[truncated -- see PDF pages N, M]` footer.

**Group/Company rules:** prose rows write content to col B only (Company
CY on company filings, Group CY on group filings) and leave the other
value columns empty. Numeric rows (sheets 13, 14) fill all four value
columns on group filings (B=Group-CY, C=Group-PY, D=Company-CY,
E=Company-PY). Evidence always lands in col D (company) or col F (group).

**Invocation:** `python3 run.py data/FINCO.pdf --notes corporate_info list_of_notes`
or via the web UI (5 checkboxes in PreRunPanel, default OFF).

**Scanned-PDF fallback for `notes_inventory`:** `scout.notes_discoverer.build_notes_inventory`
runs a fast PyMuPDF-regex pass by default. On image-only (scanned) PDFs PyMuPDF
returns empty text and the regex finds nothing — in that case, if the caller
passed a `vision_model` (the scout always does; it's the same PydanticAI
`Model` driving the scout run), the function falls back to
`scout.notes_discoverer_vision._vision_inventory`. That path renders the notes
section to PNG in 8-page batches with a 1-page overlap, runs up to 5 batches in
parallel through a dedicated one-shot `_VisionBatch`-schemad agent, and stitches
the batches back together: non-terminal notes get `last_page = next_note.first_page - 1`
(LLM's end is ignored), while the terminal note uses `min(LLM-last_page, notes_end)`
so it can't silently absorb Directors' Statement / auditor's report pages (peer-
review MEDIUM fix, 2026-04-20). Callers who know the true notes-section end
(e.g. scout walking the TOC for "Statement by Directors" / "Independent Auditors' Report")
can pass `notes_end_page=N` to tighten the vision scan range and the terminal
clamp. Scout mis-offsets that push `notes_start_page` past `pdf_length` short-
circuit to `[]` with a warning rather than raising. Per-batch failures log and
skip; all-batch failure returns `[]`, preserving the existing loud-fail contract
in `notes/coordinator.py` for Sheet 12. Temperature is pinned at 1.0 per
gotcha #5. Look for `vision inventory tokens: input=X output=Y across N/M batches`
in the logs to see what the fallback cost on a given run.

### 15. Known Security Gaps (local-dev tool only)

This app is bound to `localhost:8002` via `./start.sh` and is not
intended for hosting. The items below are safe to ignore today but
become release blockers the moment the deployment model changes. Any
agent or human contributor should treat this section as the gate —
before exposing the server beyond localhost, every gap here must be
closed.

1. **Path traversal on session-id path params.** `/api/scout/{session_id}`,
   `/api/run/{session_id}`, `/api/rerun/{session_id}`,
   `/api/abort/{session_id}`, and `/api/abort/{session_id}/{agent_id}`
   accept arbitrary strings as `session_id` and join them into
   filesystem paths. Only `/api/result/{session_id}/{filename}` applies
   a validator. Risk on localhost: low; on an internet-hosted surface:
   high (read / overwrite / delete anywhere the server process has
   permissions). **Fix when hosted:** shared `_validate_session_id()`
   helper (reject `..`, `/`, `\\`; prefer UUID4 regex), called at
   every endpoint.
2. **No auth on `/api/settings`.** Any localhost request can write
   `GOOGLE_API_KEY` (and siblings) into `.env`. **Fix when hosted:**
   shared-secret header on the `settings`, `run`, `abort`, and
   `delete` endpoints at minimum.
3. **No CORS middleware configured.** Without `CORSMiddleware`, the
   browser same-origin policy is what keeps cross-origin requests
   out — but simple requests (GET, form-encoded POST) still reach the
   server and there's no auth layer to reject them; attackers can also
   hit the server directly from non-browser clients (curl, scripts).
   **Fix when hosted:** add `CORSMiddleware` with an explicit
   allowed-origins list **and** require auth on state-changing
   endpoints — CORS alone is not a security boundary.
4. **`/api/runs/{run_id}/download/filled` trusts the DB-stored path.**
   Reads `runs.merged_workbook_path` and serves the file without
   re-validating that the path resolves under `OUTPUT_DIR`. `run_id`
   is a numeric DB primary key so path traversal via the URL param
   isn't the concern here — the concern is a compromised or
   mis-written `runs` row pointing anywhere. **Fix when hosted:**
   `file_path.resolve().relative_to(OUTPUT_DIR.resolve())` check
   before serving.
5. **`float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))` is unhandled
   at one of two call sites.** Two call sites today:
   - `server.py:345-347` — guarded by an inline `try/except ValueError`
     that falls back to `1.0`.
   - `server.py:1166` — **not guarded**. A malformed env var crashes
     cross-check setup mid-run.
   **Fix when hosted:** extract the guarded read into a
   `_safe_float_env(name, default)` helper and replace both call sites
   with it.

These gaps assume an internet-facing deployment. The current app is
bound to `localhost:8002` via `./start.sh` and is not intended for
hosting. The day the deployment model changes, every item in this
list becomes a release blocker.

## Testing

```bash
# Backend (from repo root) — excludes expensive LLM tests by default
python -m pytest tests/ -v

# Run live E2E tests (requires API key for the model being tested)
python -m pytest -m live -v   # uses TEST_MODEL from .env

# Frontend (from web/ directory)
cd web && npx vitest run

# CLI with specific model
python3 run.py data/FINCO.pdf --model gpt-5.4 --statements SOFP
python3 run.py data/FINCO.pdf --model claude-sonnet-4-6 --statements SOPL

# Compare extraction results against reference
python compare_results.py SOFP-Xbrl-reference-FINCO-filled.xlsx output/run_001/filled.xlsx
```

Key test files:
- `tests/test_e2e.py` — full 5-agent mocked pipeline (coordinator → merger → cross-checks → DB)
- `tests/test_multi_agent_integration.py` — multi-agent SSE event format + DB persistence
- `tests/test_cross_checks.py` — cross-check framework unit tests
- `tests/test_db_schema_v2.py` — v1→v2 migration + fresh-init schema invariants
- `tests/test_db_repository.py` — repository CRUD + history list/filter/detail
- `tests/test_history_repository.py` — repository helpers for the history endpoints
- `tests/test_history_api.py` — `GET /api/runs`, `/api/runs/{id}`, delete, download/filled
- `tests/test_server_run_lifecycle.py` — runs-row created before validation + terminal-status contract
- `tests/test_spa_fallback.py` — `/history` and unknown paths served from the SPA
- `web/src/__tests__/appReducer.test.ts` — per-agent event routing + state management
- `web/src/__tests__/AgentTabs.test.tsx` — tab bar with status badges
- `web/src/__tests__/ValidatorTab.test.tsx` — cross-check results display
- `web/src/__tests__/AgentTimeline.test.tsx` — per-agent terminal-style tool timeline
- `web/src/__tests__/buildToolTimeline.test.ts` — SSEEvent[] → ToolTimelineEntry[] reducer
- `web/src/__tests__/toolLabels.test.ts` — shared human-readable tool labels
- `web/src/__tests__/ToolCallCard.test.tsx` — single tool-call row (live + history shared)
- `web/src/__tests__/App.test.tsx` + `AppRouting.test.tsx` — /extract ↔ /history routing + popstate
- `web/src/__tests__/HistoryPage.test.tsx` + `HistoryList.test.tsx` + `HistoryFilters.test.tsx` — history browser
- `web/src/__tests__/RunDetailView.test.tsx` + `RunDetailModal.test.tsx` — past-run replay UI
- `web/src/__tests__/TopNav.test.tsx` + `SuccessToast.test.tsx` — chrome components

- `tests/test_filing_level.py` — template path routing by level, RunConfig/API field, verifier + cross-checks with Group fixtures
- `tests/test_notes_*.py` — notes pipeline unit + integration tests (coordinator, agent factory, writer, payload, types)
- `tests/test_notes_e2e_*.py` — per-sheet (CORP_INFO / ACC_POLICIES / ISSUED_CAPITAL / RELATED_PARTY) + Sheet-12 (`test_notes12_e2e.py`) E2E with mocked agents
- `tests/test_notes_e2e_full_pipeline.py` — full cross-sheet run covering all 5 notes templates in one coordinator call
- `tests/test_notes_retry_budget.py` — PLAN §4 E.1 retry-once contract + per-sheet `notes_<TEMPLATE>_failures.json` side-log
- `tests/test_notes_continuation.py` / `test_notes_char_limit.py` — multi-page continuation prompt pin + 30K char-limit truncation
- `tests/test_server_notes_api.py` — `notes_to_run` request plumbing + SSE `run_complete.notes_completed` shape
- `web/src/__tests__/PreRunPanel.test.tsx` — notes checkboxes default OFF + notes-only run enables Run button

Note: `test_pdf_viewer.py` and `test_template_reader.py` auto-skip when sample data is absent (via `pytestmark`). All server/API tests pass independently.

## Files That Must Stay in Sync

| Change | Also update |
|--------|-------------|
| pydantic-ai API | `server.py` (`_create_proxy_model`), `extraction/agent.py` (imports/agent creation) |
| .env variable names | `server.py` (settings endpoints), `run.py` (loads .env), `.env.example`, `start.bat`, `start.sh`, `litellm_config.yaml` (references env vars) |
| Agent tool names | `server.py` (`PHASE_MAP`), `extraction/agent.py` (tool definitions) |
| Excel template structure | `tools/fill_workbook.py` (section headers), `tools/verifier.py`, `cross_checks/util.py` (label lookups), `docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md` (formula audit trail) |
| XBRL template formulas | `XBRL-template-MFRS/Company/*.xlsx` + `XBRL-template-MFRS/Group/*.xlsx` (sub-sheet formulas), `SSMxT_2022v1.0/` (authoritative XBRL calc linkbase), `XBRL-template-MFRS/backup-originals/` (pre-fix backups) |
| Statement types / variants | `statement_types.py`, `coordinator.py`, `server.py` (`RunConfigRequest`), `prompts/` (per-variant files), `web/src/lib/types.ts` (`STATEMENT_TYPES`, `VARIANTS`) |
| Cross-check implementations | `cross_checks/*.py`, `server.py` (`run_multi_agent_stream` check list), `cross_checks/util.py` |
| Model wiring / proxy setup | `server.py` (`_create_proxy_model`, `_detect_provider`), `run.py` (also calls `_create_proxy_model`), `coordinator.py` (`RunConfig.model/models`), `litellm_config.yaml` (proxy model routing), `config/models.json` (UI model list) |
| Frontend agent state types | `web/src/lib/types.ts` (`AgentState`, `CrossCheckResult`), `web/src/App.tsx` (`appReducer`, `agentReducer`) |
| Tab/Validator UI | `web/src/components/AgentTabs.tsx`, `web/src/components/ValidatorTab.tsx`, `web/src/App.tsx` (tab wiring) |
| Agent timeline / tool-row rendering | `web/src/lib/toolLabels.ts` (humanToolName/argsPreview/resultSummary), `web/src/lib/buildToolTimeline.ts` (SSE → timeline reducer), `web/src/components/ToolCallCard.tsx` (single row primitive), `web/src/components/AgentTimeline.tsx` (live + history + scout feed), `web/src/components/PreRunPanel.tsx` (scout auto-detect rendering), `web/src/components/RunDetailView.tsx` (history replay) |
| DB schema (runs lifecycle) | `db/schema.py` (`CURRENT_SCHEMA_VERSION`, `_CREATE_STATEMENTS`, `_V2_MIGRATION_COLUMNS`), `db/repository.py` (`create_run`, `mark_run_merged`, `mark_run_finished`, list/detail queries), `tests/test_db_schema_v2.py`, `tests/test_db_repository.py` |
| History API endpoints | `server.py` (`GET /api/runs`, `GET /api/runs/{id}`, `DELETE /api/runs/{id}`, `GET /api/runs/{id}/download/filled`, SPA fallback), `web/src/lib/api.ts` (fetch helpers), `web/src/lib/types.ts` (`RunSummaryJson`, `RunDetailJson`, `RunAgentJson`, `RunCrossCheckJson`, `RunsFilterParams`), `tests/test_history_api.py` |
| Run lifecycle / terminal status | `server.py` (`run_multi_agent_stream` try/except/finally, `_safe_mark_finished`, pre-validation `create_run`), `db/repository.py` (`mark_run_finished` status enum), `tests/test_server_run_lifecycle.py` |
| History UI / routing | `web/src/App.tsx` (`AppView`, popstate handling, `/history` hydration), `web/src/pages/HistoryPage.tsx`, `web/src/components/{HistoryList,HistoryFilters,RunDetailModal,RunDetailView,TopNav,SuccessToast}.tsx`, `web/src/lib/runStatus.ts` |
| Filing level / templates | `statement_types.py` (`template_path` level param), `coordinator.py` (`RunConfig.filing_level`), `server.py` (`RunConfigRequest.filing_level`), `run.py` (`--level` flag), `extraction/agent.py` (passes to `render_prompt`), `prompts/__init__.py` (overlay injection), `prompts/_group_overlay.md`, `prompts/_group_socie_overlay.md`, `tools/verifier.py` (dual-column check), `cross_checks/framework.py` + all `cross_checks/*.py` (dual Group/Company validation), `web/src/lib/types.ts` (`RunConfigPayload.filing_level`), `web/src/components/PreRunPanel.tsx` (toggle), `web/src/components/HistoryList.tsx` (badge), `web/src/components/HistoryFilters.tsx` (filter) |
| Notes template registry | `notes_types.py` (`NotesTemplateType` enum + `NOTES_REGISTRY` + `notes_template_path`), `server.py` (`_PUBLIC_NOTES_TEMPLATES` allowlist + `RunConfigRequest.notes_to_run`), `run.py` (`--notes` CLI flag + `_NOTES_CLI_MAP`), `web/src/lib/types.ts` (`NotesTemplateType`, `NOTES_TEMPLATE_TYPES`, `NOTES_TEMPLATE_LABELS`), `web/src/components/PreRunPanel.tsx` (5 checkboxes), `tests/test_server_notes_api.py` (allowlist drift check) |
| Notes inventory discovery | `scout/notes_discoverer.py` (`build_notes_inventory` + `build_notes_inventory_async` — fast PyMuPDF pass + optional vision fallback via `vision_model` kwarg), `scout/notes_discoverer_vision.py` (`_chunk`, `_merge_and_stitch`, `_build_vision_agent`, `_scan_batch`, `_vision_inventory` — batched parallel vision pass with stitched trailing page ranges), `scout/agent.py` (`ScoutDeps.vision_model`, `discover_notes_inventory` async tool passing it through), `tests/test_scout_notes_inventory.py` (text-PDF regressions + scanned-PDF wiring), `tests/test_notes_discoverer_vision.py` (unit tests for chunker/stitcher/scan/orchestrator), `tests/test_scout_notes_inventory_vision_live.py` (live integration test — `pytest -m live`) |
| Notes agent prompts | `prompts/_notes_base.md` (persona + output contract + 30K-char cap + multi-page continuation rule), `prompts/notes_{corporate_info,accounting_policies,listofnotes,issued_capital,related_party}.md`, `notes/agent.py` (`_TEMPLATE_PROMPT_FILES` map + `render_notes_prompt`), `tests/test_notes_continuation.py` (prompt-contract pin) |
| Notes writer / column rules | `notes/writer.py` (`CELL_CHAR_LIMIT`, `evidence_col_letter`, `_EVIDENCE_COL`, Group/Company column rules, `_combine_payloads` for row concatenation), `prompts/_notes_base.md` (mirror of same rules for the LLM), `notes/agent.py` (`_render_column_rules` in system prompt), `tests/test_notes_writer.py`, `tests/test_notes_char_limit.py` |
| Notes retry budget | `notes/coordinator.py` (`SINGLE_AGENT_MAX_RETRIES`, `_run_single_notes_agent`, `_invoke_single_notes_agent_once`, `_write_single_sheet_failure_log`), `notes/listofnotes_subcoordinator.py` (sub-agent `max_retries` + `_write_failures_side_log`), `tests/test_notes_retry_budget.py` |
| Notes UI / tabs | `web/src/lib/appReducer.ts` (`notesInRun`, `deriveAgentLabel`, `NOTES_TAB_LABELS`), `web/src/App.tsx` (`RUN_STARTED` payload includes notes), `web/src/components/AgentTabs.tsx` (notes bucket between statements and scout/validator, `notesInRun` + `notesSkeletons` props), `web/src/pages/ExtractPage.tsx` (builds notes skeleton labels), `web/src/components/PreRunPanel.tsx` (5 checkboxes), `web/src/components/RunDetailView.tsx` (renders NOTES_* agents alongside face agents) |

## Porting Checklist (Mac -> Windows)

1. `git push` from Mac, `git pull` on Windows
2. Run `start.bat` — handles everything automatically
3. First run: Notepad opens `.env` — fill in `GOOGLE_API_KEY` from Bruno
4. Set `LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com` (enterprise proxy)
5. Verify proxy is reachable (must be on corporate network/VPN)
6. Check `PYTHONUTF8=1` is set (start.bat does this)
7. If pydantic-ai version differs: check `_create_proxy_model()` in server.py
8. Note: On Windows, `start.bat` does NOT launch a local LiteLLM proxy — it uses the
   enterprise proxy directly. Only `start.sh` (Mac) runs a local LiteLLM instance.
