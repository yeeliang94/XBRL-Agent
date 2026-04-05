# XBRL Agent

Extracts financial data from Malaysian financial statement PDFs (MFRS format) into SSM MBRS XBRL Excel templates for filing with the Companies Commission of Malaysia (SSM).

Built on PydanticAI + Gemini, with a Vite + React web UI for interactive extraction.

## Architecture

```
run.py          CLI entry point (direct API)
server.py       FastAPI + SSE web server
agent.py        PydanticAI agent with 5 tools
tools/
  template_reader.py   Read Excel template structure
  pdf_viewer.py        Render PDF pages to images
  fill_workbook.py     Write values to Excel (label matching)
  verifier.py          Check balance sheet balances (formula evaluator)
web/            Vite + React frontend
data/           Test PDFs + Excel templates (gitignored, see below)
tests/          Pytest suite
```

## Quick Start

### Mac/Linux

```bash
# 1. Populate data/ with test PDFs and templates (see Data Setup below)
# 2. Copy env template and add your API key
cp .env.example .env
# Edit .env, set GEMINI_API_KEY

# 3. Start everything
./start.sh
```

### Windows (enterprise proxy)

```powershell
# Just double-click start.bat
.\start.bat
```

Web UI at http://localhost:8002

### CLI (bypass web UI)

```bash
python run.py data/FINCO-Audited-Financial-Statement-2021.pdf SOFP-Xbrl-template.xlsx
```

## Data Setup

The `data/` folder is gitignored because it contains large PDFs and confidential test files. Required files:

| File | Purpose |
|------|---------|
| `data/FINCO-Audited-Financial-Statement-2021.pdf` | Primary test PDF |
| `data/Oriental.pdf` | Secondary test PDF |
| `data/ground_truth_sofp_sopl.xlsx` | Evaluation reference |
| `data/MBRS_test.xlsx` | Full 18-sheet MBRS template |

Place your own Malaysian audited financial statement PDFs in `data/` to extract them.

## Testing

```bash
# Backend
python -m pytest tests/ -v

# Frontend
cd web && npx vitest run
```

## Configuration

See `.env.example` for all options. Key settings:

- `GEMINI_API_KEY` — Google Gemini API key (Mac direct mode)
- `LLM_PROXY_URL` — Enterprise proxy URL (Windows mode)
- `GOOGLE_API_KEY` — Proxy API key (Windows mode)
- `TEST_MODEL` — Model name (e.g. `vertex_ai.gemini-3-flash-preview`)

For AI agent context and detailed architecture notes, see `CLAUDE.md`.
