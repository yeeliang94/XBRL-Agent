# AGENTS.md

Context pack for AI coding agents (Codex, Claude Code, etc.) working in this
repository. The **full reference is [CLAUDE.md](CLAUDE.md)** — read it if your
agent runtime follows links. The block below carries the non-negotiables so
agents that treat `AGENTS.md` as the bootstrap payload still get them on the
first turn.

## What This Is

XBRL extraction agent for Malaysian financial statement PDFs. Extracts data
into SSM MBRS XBRL Excel templates. Two filing standards (MFRS, MPERS) × two
levels (Company, Group). Multi-provider LLM (Google Gemini, OpenAI, Anthropic)
via LiteLLM proxy. PydanticAI `>=1.77.0`.

## Quick Start

```bash
# Mac — Web UI (launches local LiteLLM proxy + server)
./start.sh                                                # http://localhost:8002

# Mac — CLI
python3 run.py data/FINCO.pdf                             # all 5 statements, MFRS, Company
python3 run.py data/FINCO.pdf --level group               # Group filing
python3 run.py data/FINCO.pdf --standard mpers            # MPERS filing standard
python3 run.py data/FINCO.pdf --notes corporate_info list_of_notes

# Windows (enterprise proxy)
.\start.bat
```

Tests: `python -m pytest tests/ -v` (backend), `cd web && npx vitest run` (frontend).

## Non-Negotiables (Do Not Do)

These are load-bearing invariants. Violating any of them will regress tests
or reintroduce documented bugs.

- **Don't edit anything under `XBRL-template-*/backup-originals/`** —
  snapshot archives for schema-drift diffing.
- **Don't hand-edit template formulas.** They trace back to the SSM
  calculation linkbase. Regenerate from linkbase if wrong.
- **Don't run `scripts/generate_mpers_templates.py` without `--snapshot`** —
  you will destroy the previous generation snapshot.
- **Don't convert frontend inline styles back to Tailwind/className.**
  Tailwind v4 doesn't load reliably on Windows; all components use inline
  `style={}` props by design.
- **Don't re-introduce `allowed_pages` or page-filter logic in scout.** Scout
  page hints are soft guidance only; agents can view any page in the PDF.
  `tests/test_page_hints.py` pins this with negative assertions.
- **Don't remove the `_safe_mark_finished` try/except in `server.py`.** It
  swallows audit-write exceptions so error handlers never double-fault.
- **Don't add deterministic label-matching, OCR, or synonym dictionaries to
  the notes pipeline.** Matching is intentionally pure LLM judgement.
- **Don't pass `base_url=` or `openai_client=` directly to `OpenAIModel`.**
  Removed in pydantic-ai 1.x — use the `provider=OpenAIProvider(...)` pattern.
- **Don't use temperature `< 1.0` for Gemini 3 through the proxy** — causes
  failures and infinite loops.
- **Don't assume `Agent._function_tools` exists** — it was removed in
  pydantic-ai 1.77+. Tool event streaming uses `agent.iter()` + `node.stream()`.

## Run Lifecycle Invariant (server.py)

`run_multi_agent_stream` creates the `runs` audit row **before** validation
so failed runs still appear in history. Every exit path (success, exception,
`CancelledError`, disconnect) leaves the row in a terminal status
(`completed`, `completed_with_errors`, `failed`, `aborted`) and never
`running`. `mark_run_merged` is called immediately after merge, before the
final status update, so downloads work even if later persistence crashes.

## Provider Setup (one function)

All LLM calls route through `_create_proxy_model()` in `server.py` (used by
both `run.py` and the web server). Two modes:

1. **Proxy mode** (`LLM_PROXY_URL` set) — OpenAI-compatible LiteLLM endpoint.
2. **Direct mode** (`LLM_PROXY_URL` empty) — provider detected by prefix:
   `gpt-*`/`o*-*` → OpenAI, `claude-*` → Anthropic, else → Gemini.

On Mac, `start.sh` auto-exports `LLM_PROXY_URL=http://localhost:4000/v1`. On
Windows, the enterprise proxy is set manually in `.env`. Windows also requires
`PYTHONUTF8=1` (set by `start.bat`).

## Deeper References

- **[CLAUDE.md](CLAUDE.md)** — full agent context pack (this file is a subset)
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — module map + data flow
- **[docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md)** — notes subsystem
- **[docs/MPERS.md](docs/MPERS.md)** — MPERS filing-standard deep-dive
- **[docs/SYNC-MATRIX.md](docs/SYNC-MATRIX.md)** — cross-file impact table
- **[docs/PORTING-WINDOWS.md](docs/PORTING-WINDOWS.md)** — Mac → Windows porting

Please keep `CLAUDE.md` as the full source of truth and this file as its
must-read subset. If you add a new non-negotiable, add it in both places.
