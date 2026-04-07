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

# Windows (enterprise proxy) — just double-click start.bat
# Or: start.bat
# Web UI at http://localhost:8002
```

## Architecture

```
run.py              CLI entry point — runs coordinator for 1-5 statements
server.py           FastAPI + SSE web server (POST /api/run/{session_id})
coordinator.py      Fans out N extraction agents concurrently via asyncio.gather
extraction/
  agent.py          Generic extraction agent factory (one per statement type)
statement_types.py  StatementType enum, variant registry, template path resolver
prompts/            Per-statement system prompt templates (sofp.md, sopl.md, etc.)
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
workbook_merger.py  Merges per-statement workbooks into single output file
db/                 SQLite audit trail (runs, agents, events, cross-checks)
scout/              PDF page-range detection for each statement
web/                Vite + React frontend (inline styles, tab-based multi-agent UI)
config/
  models.json       Available models registry (id, provider, display name)
litellm_config.yaml LiteLLM proxy config — routes models to correct provider APIs
start.bat           Windows startup script (finds Python/Node, sets UTF-8)
start.sh            Mac/Linux startup script (launches LiteLLM proxy + server)
XBRL-template-MFRS/ Template Excel files (01-SOFP-CuNonCu.xlsx through 09-SOCIE.xlsx)
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

See `TEMPLATE-FORMULA-FIX-GUIDE.md` for full details, broken formula table, and XBRL
linkbase verification methodology.

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
- `web/src/__tests__/appReducer.test.ts` — per-agent event routing + state management
- `web/src/__tests__/AgentTabs.test.tsx` — tab bar with status badges
- `web/src/__tests__/ValidatorTab.test.tsx` — cross-check results display

Note: `test_pdf_viewer.py` and `test_template_reader.py` fail without sample PDF data present. All server/API tests pass independently.

## Files That Must Stay in Sync

| Change | Also update |
|--------|-------------|
| pydantic-ai API | `server.py` (`_create_proxy_model`), `extraction/agent.py` (imports/agent creation) |
| .env variable names | `server.py` (settings endpoints), `run.py` (loads .env), `.env.example`, `start.bat`, `start.sh`, `litellm_config.yaml` (references env vars) |
| Agent tool names | `server.py` (`PHASE_MAP`), `extraction/agent.py` (tool definitions) |
| Excel template structure | `tools/fill_workbook.py` (section headers), `tools/verifier.py`, `cross_checks/util.py` (label lookups), `TEMPLATE-FORMULA-FIX-GUIDE.md` (formula audit trail) |
| XBRL template formulas | `XBRL-template-MFRS/*.xlsx` (sub-sheet formulas), `SSMxT_2022v1.0/` (authoritative XBRL calc linkbase), `XBRL-template-MFRS/backup-originals/` (pre-fix backups) |
| Statement types / variants | `statement_types.py`, `coordinator.py`, `server.py` (`RunConfigRequest`), `prompts/` (per-variant files), `web/src/lib/types.ts` (`STATEMENT_TYPES`, `VARIANTS`) |
| Cross-check implementations | `cross_checks/*.py`, `server.py` (`run_multi_agent_stream` check list), `cross_checks/util.py` |
| Model wiring / proxy setup | `server.py` (`_create_proxy_model`, `_detect_provider`), `run.py` (also calls `_create_proxy_model`), `coordinator.py` (`RunConfig.model/models`), `litellm_config.yaml` (proxy model routing), `config/models.json` (UI model list) |
| Frontend agent state types | `web/src/lib/types.ts` (`AgentState`, `CrossCheckResult`), `web/src/App.tsx` (`appReducer`, `agentReducer`) |
| Tab/Validator UI | `web/src/components/AgentTabs.tsx`, `web/src/components/ValidatorTab.tsx`, `web/src/App.tsx` (tab wiring) |

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
