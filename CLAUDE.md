# XBRL Agent — AI Agent Reference

## What This Is

A standalone XBRL extraction agent for Malaysian financial statement PDFs. Extracts data
into SSM MBRS XBRL Excel templates. Uses PydanticAI with Gemini (via enterprise LiteLLM
proxy on Windows, or direct API on Mac). Currently handles SOFP (Statement of Financial
Position); being extended to all 18 MBRS reporting sheets.

## Quick Start

```bash
# Mac (direct Gemini API)
GEMINI_API_KEY=your-key python3 run.py data/FINCO-Audited-Financial-Statement-2021.pdf SOFP-Xbrl-template.xlsx

# Windows (enterprise proxy) — just double-click start.bat
# Or: start.bat
# Web UI at http://localhost:8002
```

## Architecture

```
run.py          CLI entry point (Mac/direct API)
server.py       FastAPI + SSE web server (Windows/proxy)
agent.py        PydanticAI agent with 5 tools
tools/
  template_reader.py   Read template structure
  pdf_viewer.py        Render PDF pages to images
  fill_workbook.py     Write values to Excel (label matching)
  verifier.py          Check balance sheet balances (formula evaluator)
web/               Vite + React frontend (inline styles, no Tailwind dependency)
start.bat          Windows startup script (finds Python/Node, sets UTF-8)
start.sh           Mac/Linux startup script
```

## Enterprise Proxy Setup (Windows)

All LLM traffic goes through `https://genai-sharedservice-emea.pwc.com` using OpenAI-compatible protocol. Direct Google API calls are blocked (403).

### Key Configuration

```env
# .env file
LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com
GOOGLE_API_KEY=sk-xxxx   # from Bruno -> Collection -> Auth tab
TEST_MODEL=vertex_ai.gemini-3-flash-preview
```

### PydanticAI Model Creation (v1.77+)

```python
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(base_url=proxy_url, api_key=api_key)
model = OpenAIModel(model_name, provider=provider)
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
- Tool event emission uses `event_callback` on `AgentDeps` instead of wrapping internals

### 3. fill_workbook Row Matching — Off-by-One with Reference File

The reference file (`SOFP-Xbrl-reference-FINCO-filled.xlsx`) has a different row layout than the template (`SOFP-Xbrl-template.xlsx`) — an extra blank row near the top shifts all sub-sheet rows down by 1. The agent writes to correct rows **per the template**, but comparisons against the reference show everything off by 1.

This is NOT a bug in fill_workbook — it's a template/reference mismatch. When comparing results, always compare against the template row layout, not the reference.

**Verified (2026-04-03):** Label matching in fill_workbook.py is correct. "Retained earnings" matches to row 40 (*Retained earnings) properly. The compare_results.py script shows false "EXTRA"/"MISSING" because it compares row-by-row against a reference with different row numbering. To properly validate results, open the filled Excel in Excel (not openpyxl) so formulas evaluate, then check totals balance.

**Actual extraction quality gaps:**
- Agent extracts ~24 fields but FINCO SOFP has ~35+ data-entry cells
- "Deferred income" on sub-sheet not filled (agent puts it in "Contract liabilities" on main sheet only)
- Coverage could be improved by enhancing the system prompt to emphasize sub-sheet breakdowns

### 4. LiteLLM SSL Warning is Safe to Ignore

```
LiteLLM:WARNING: Failed to fetch remote model cost map... [SSL: CERTIFICATE_VERIFY_FAILED]
```

Enterprise firewall blocks GitHub. LiteLLM falls back to local pricing data. Suppressed via `litellm.suppress_debug_info = True` in server.py.

### 5. Token Counts are Approximate

`_track_turn()` in agent.py records zeros for per-turn tokens because PydanticAI handles counting internally. After the run completes, `server.py` backfills totals from `result.usage`. The token dashboard shows real numbers only after completion.

### 6. Frontend Uses Inline Styles, Not Tailwind

Tailwind CSS v4 didn't load reliably on Windows (the upload button was unclickable). All components now use inline `style={}` props. This is intentional — don't convert back to className-based Tailwind.

### 7. Node.js May Not Be on PATH (Windows)

`start.bat` auto-discovers Node.js in `C:\Program Files\nodejs\`. If it's elsewhere, set PATH manually before running.

### 8. Output Directory Structure

```
output/
  run_001/     # CLI runs (auto-numbered)
  run_002/
  {uuid}/      # Web UI runs (UUID per session)
```

`run.py` uses `Path(__file__).resolve().parent / "output"` as the base — works regardless of working directory.

## Testing

```bash
# Backend (from repo root)
python -m pytest tests/ -v

# Frontend
cd web && npx vitest run

# Compare extraction results against reference
python compare_results.py SOFP-Xbrl-reference-FINCO-filled.xlsx output/run_001/filled.xlsx
```

Note: `test_pdf_viewer.py` and `test_template_reader.py` fail without sample PDF data present. All server/API tests pass independently.

## Files That Must Stay in Sync

| Change | Also update |
|--------|-------------|
| pydantic-ai API | `server.py` (`_create_proxy_model`), `agent.py` (imports) |
| .env variable names | `server.py` (settings endpoints), `.env.example`, `start.bat` |
| Agent tool names | `server.py` (`phase_map` in `run_agent_in_thread`) |
| Excel template structure | `tools/fill_workbook.py` (section headers), `tools/verifier.py` |

## Porting Checklist (Mac -> Windows)

1. `git push` from Mac, `git pull` on Windows
2. Run `start.bat` — handles everything automatically
3. First run: Notepad opens `.env` — fill in `GOOGLE_API_KEY` from Bruno
4. Verify proxy is reachable (must be on corporate network/VPN)
5. Check `PYTHONUTF8=1` is set (start.bat does this)
6. If pydantic-ai version differs: check `_create_proxy_model()` in server.py
