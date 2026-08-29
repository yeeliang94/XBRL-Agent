# XBRL Agent

Extracts financial data from Malaysian financial statement PDFs (MFRS or MPERS)
into SSM MBRS XBRL Excel templates for filing with the Companies Commission of
Malaysia (SSM).

Built on PydanticAI with multi-provider LLM support (Google Gemini, OpenAI,
Anthropic) via a LiteLLM proxy, and a Vite + React web UI for interactive
extraction.

## Architecture

```
PDF + scout (optional) → coordinator → N extraction agents (parallel) ─┐
                                    → M notes agents (parallel)       ─┤→ workbook_merger → filled.xlsx
                                                                       └→ cross_checks
```

Full module map in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick Start

Python 3.10 or newer is required. The startup scripts create `venv` with a
supported interpreter; macOS prefers Python 3.12 when it is installed.

### Mac/Linux

```bash
# 1. Populate data/ with test PDFs and templates (see Data Setup below)
# 2. Copy env template and add your API key
cp .env.example .env
# Edit .env, set GEMINI_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY)

# 3. Start everything (launches local LiteLLM proxy + server)
./start.sh
```

### Windows (enterprise proxy)

```powershell
.\start.bat
```

Web UI at http://localhost:8002

### CLI (bypass web UI)

Use the repository virtual environment explicitly. Activation is local to one
shell and does not carry into a new terminal or agent command.

#### macOS/Linux

```bash
# All 5 face statements (default MFRS, Company level)
venv/bin/python run.py data/FINCO-Audited-Financial-Statement-2021.pdf

# Specific statements + model
venv/bin/python run.py data/FINCO.pdf --model gpt-5.4 --statements SOFP SOPL

# Group filing (consolidated + company figures)
venv/bin/python run.py data/FINCO.pdf --level group --statements SOFP SOPL

# MPERS filing standard
venv/bin/python run.py data/FINCO.pdf --standard mpers --statements SOFP SOCIE

# With notes templates
venv/bin/python run.py data/FINCO.pdf --notes corporate_info list_of_notes
```

#### Windows PowerShell

```powershell
$python = ".\venv\Scripts\python.exe"

& $python run.py data\FINCO-Audited-Financial-Statement-2021.pdf
& $python run.py data\FINCO.pdf --model gpt-5.4 --statements SOFP SOPL
& $python run.py data\FINCO.pdf --level group --statements SOFP SOPL
& $python run.py data\FINCO.pdf --standard mpers --statements SOFP SOCIE
& $python run.py data\FINCO.pdf --notes corporate_info list_of_notes
```

## Data Setup

The `data/` folder is gitignored because it contains large PDFs and
confidential test files. Required files:

| File | Purpose |
|------|---------|
| `data/FINCO-Audited-Financial-Statement-2021.pdf` | Primary test PDF |
| `data/Oriental.pdf` | Secondary test PDF |
| `data/ground_truth_sofp_sopl.xlsx` | Evaluation reference |
| `data/MBRS_test.xlsx` | Full 18-sheet MBRS template |

Place your own Malaysian audited financial statement PDFs in `data/` to
extract them.

## Testing

macOS/Linux:

```bash
# Backend (excludes live LLM tests by default)
venv/bin/python -m pytest tests/ -v

# Live E2E (requires API key for TEST_MODEL)
venv/bin/python -m pytest -m live -v

# Frontend
cd web && npx vitest run
```

Windows PowerShell:

```powershell
.\venv\Scripts\python.exe -m pytest tests\ -v
.\venv\Scripts\python.exe -m pytest -m live -v

cd web
npx vitest run
```

## Configuration

See `.env.example` for all options. Key settings:

- `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — at least one
  required
- `LLM_PROXY_URL` — set to the enterprise proxy URL on Windows. **On Mac leave
  it empty** — `start.sh` exports `LLM_PROXY_URL=http://localhost:4000/v1`
  automatically so the local LiteLLM proxy is used. If you're not using
  `start.sh` (rare manual-run path) and leave it empty, the app runs in
  direct-mode (provider detected by model-name prefix).
- `GOOGLE_API_KEY` — also used as proxy auth key on Windows
- `TEST_MODEL` — default extraction model (e.g.
  `google-gla:gemini-3-flash-preview`)
- `SCOUT_MODEL` — legacy scout-model fallback; a role override saved under
  Settings takes precedence

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — AI-agent context pack: invariants, commands,
  and "how to work here" guidance
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — module map + data flow
- **[docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md)** — notes subsystem
  deep-dive
- **[docs/MPERS.md](docs/MPERS.md)** — MPERS filing-standard deep-dive
- **[docs/SYNC-MATRIX.md](docs/SYNC-MATRIX.md)** — cross-file impact for a
  given change
- **[docs/PORTING-WINDOWS.md](docs/PORTING-WINDOWS.md)** — Mac → Windows
  porting checklist
- `docs/workflows/*.md` — per-statement fill-workflow notes
- `docs/xbrl-field-descriptions.md` — XBRL taxonomy field reference
