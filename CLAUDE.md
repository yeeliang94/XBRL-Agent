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
GOOGLE_API_KEY=                # also used as proxy auth key on Windows

# Model defaults
TEST_MODEL=google-gla:gemini-3-flash-preview
SCOUT_MODEL=google-gla:gemini-3-flash-preview
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

### 5. LiteLLM SSL warning is safe to ignore

```
LiteLLM:WARNING: Failed to fetch remote model cost map... [SSL: CERTIFICATE_VERIFY_FAILED]
```

Enterprise firewall blocks GitHub; LiteLLM falls back to local pricing data.
Already suppressed via `litellm.suppress_debug_info = True` in `server.py`.

### 6. Per-turn token counts are approximate

`_track_turn()` in `extraction/agent.py` records zeros for per-turn tokens
because PydanticAI handles counting internally. After completion, `server.py`
backfills totals from `result.usage`. The token dashboard shows real numbers
only after the run finishes.

### 7. Frontend uses inline styles, not Tailwind

Tailwind CSS v4 didn't load reliably on Windows (the upload button was
unclickable). All components use inline `style={}` props. **Do not** convert
back to className-based Tailwind.

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

### 11. DB schema v2 — auto-migration on startup

`db/schema.py` carries `CURRENT_SCHEMA_VERSION = 2`. `init_db` detects v1
databases and runs `ALTER TABLE runs ADD COLUMN …` for the seven lifecycle
fields. Migration is idempotent and backfills `started_at` from `created_at`.

SQLite `ALTER TABLE` cannot add `NOT NULL` columns without defaults — every
entry in `_V2_MIGRATION_COLUMNS` is nullable or has a safe default. The
`status` column has no `CHECK` constraint on purpose: adding a new status
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
  differences (e.g. `_group_overlay.md`).

Full walkthrough: [docs/MPERS.md](docs/MPERS.md).

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
- `tests/test_db_schema_v2.py` — v1→v2 migration + fresh-init invariants.
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
