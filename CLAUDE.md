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
- **Talk like a product person, not an engineer.** The primary operator is a
  product manager who works with developers but does not read code fluently.
  This is a standing request — don't wait to be asked to "explain further."
  Default to plain language: lead with what something does or why it matters
  before the mechanism, put a few plain words next to any unavoidable jargon
  the first time it appears, spell out acronyms once, and keep code-level
  detail out of explanations unless it's asked for. When a thing is genuinely
  technical, give it a one-line plain-English gloss rather than assuming it
  lands. This governs how you *communicate*; it does not lower the technical
  precision of the code or of the invariants below.

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
documented in `docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md`.

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
a failure. The Sheet-12 fan-out saves one trace per sub-agent
(`NOTES_LIST_OF_NOTES_subN[_retryK]_conversation_trace.json`, run-63 fix).
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

`db/schema.py` carries `CURRENT_SCHEMA_VERSION` (committed: **29**). `init_db`
reads the stored version and walks an old DB up **one version at a time**
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

Recent tables/columns, each detailed in its linked gotcha: v11
`concept_render_aliases` (#21) · v12–v13 reviewer tables `run_fact_snapshots`
/ `reviewer_flags` / `run_review_tasks` (#21) · v16 gold-eval tables +
`runs.benchmark_id` (#23) · v17 `run_agents.error_type` · v18 auth tables (#24)
· v20 `auth_users.is_admin` (#24) · v22 `runs.notes_table_style` (#16) ·
v23–v25 notes-reviewer tables + `notes_cell_tombstones` (#16, #27) · v26–v27
notes-formatter `notes_format_tasks` / `notes_format_snapshots` (#16) · v28
`notes_coverage_rows` (#27) · v29 `notes_cells.style_source` (#16).

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

Notes agents emit **HTML** (not plaintext) into cells on sheets 10–14 (MFRS) /
11–15 (MPERS). Flow:

```
agent HTML → sanitiser → notes_cells (DB, canonical) → overlay → xlsx stream
                                     ↘ NotesReviewTab (TipTap editor)
```

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
- **Two AI styling paths (the `content` channel stays style-free either way):**
  - **Formatting sidecar (DEFAULT, write-time,
    docs/PLAN-notes-format-sidecar.md):** notes extraction agents emit an optional
    `format_ops` field per payload (same constrained op vocabulary as
    `notes/format_patch.py` — a structured channel, NOT inline styles in
    `content`). `notes/writer.py::_style_cell_html` applies it through one gate,
    `format_patch.apply_cell_operations` (ops → sanitiser → `verify_format_only`).
    Fallback: **agent ops → unstyled (plain).** The deterministic house-style
    floor (`notes/format_defaults.py`, kill switch `XBRL_NOTES_HOUSE_STYLE`) was
    **REMOVED 2026-07-07** — it *imposed* the accountant convention (notably a
    double-underline on any "total"-text row) rather than mirroring the source
    PDF, so it invented borders the statement didn't have. A cell without usable
    agent ops renders plain and the operator restyles on demand via the
    formatter agent; legacy DB rows may still carry `style_source='floor'`.
    Formatting NEVER blocks a content write — invalid ops degrade to plain.
    Multi-payload rows (`_combine_payloads`) re-offset each payload's table
    indices; a non-table op in a combined cell drops all ops for that cell.
    **Omission gets pushback (run-63 fix, 2026-07-07):** the `write_notes`
    return message appends a nudge when table cells land `unstyled`
    (`notes/agent.py::format_unstyled_table_nudge` — invite an observation,
    never invent), the tool docstring + a rebalanced `_notes_base.md`
    FORMATTING OBSERVATION block say a visible table's formatting is
    EXPECTED, and the Sheet-12 sink replaces (not concatenates) an
    identical-content re-send so the nudge's "re-send with format_ops" advice
    is safe. **Styling provenance is surfaced:** `_style_cell_html` tags each
    cell `ops`/`unstyled`, persisted to `notes_cells.style_source` (v29,
    preserve-on-omit like `concept_uuid`), returned by `GET /notes_cells`, and
    shown as a chip in the Notes tab (`StyleSourceChip` — only for `unstyled`/
    legacy `floor`, the cells that may want a formatter pass). Pinned by
    `tests/test_notes_format_sidecar.py`, `tests/test_db_schema_v29.py`.
  - **Notes formatter agent (manual REPAIR pass, `POST /api/runs/{id}/notes-format`,
    per prose sheet):** the only AI role that authors styling on demand; returns
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
  reproduce the old hard-coded output byte-for-byte** — keep that equivalence
  when editing. Pinned by `web/src/__tests__/clipboard.test.ts`.
- **Notes-table style THEME (docs/PLAN-notes-table-theme.md):**
  `ClipboardFormatOptions` was promoted to a full table theme that is the shared,
  server-side firm default (`XBRL_NOTES_TABLE_STYLE` via `/api/settings`). ONE
  resolved theme (`resolveTheme(runOverride, firmDefault)`) drives BOTH the editor
  (as `--nt-*` CSS vars) and the clipboard, so preview == paste. A per-run override
  lives on `runs.notes_table_style` (v22, editable post-run via the Notes-tab
  picker) and is a full SNAPSHOT, not a partial diff. Per-cell manual styles win
  over the theme; "Reset cell to theme" (`resetCellToTheme`) re-inherits it. A
  totals row's double underline (`border-bottom: 3px double`) is saved document
  formatting, and Copy reads the resolved theme at click time. Pinned by
  `tests/test_settings_api.py`, `test_run_notes_table_style.py`, and the
  `clipboardFormat`/`clipboard`/`cellFormatting`/`NotesReviewTab` web tests.
- **Numeric notes rows (sheets 13/14, `NumericCellRow`)** show grouped `1,595` at
  rest, raw while focused (`formatGroupedInput` in `web/src/lib/numberFormat.ts`);
  display-only, stored values stay raw.

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
  `prompts/reviewer.md`**: catch-all rows ("Other …",
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
(positive seconds; 0 disables). `NOTES_VALIDATOR_WALLCLOCK_TIMEOUT`
(legacy name) is the same defence for the notes-reviewer pass — the pass
inherited the old validator's constants and pseudo-agent id when it
replaced it (gotcha #22).

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
agent** (`correction/reviewer_agent.py`, `prompts/reviewer.md`) is the **only**
extraction → review → export pipeline. It is MANDATORY (first-principles rewrite
Phase 1.1): the legacy direct-xlsx pipeline, the `XBRL_CANONICAL_MODE` opt-out,
`correction/agent.py`, and `correction/canonical_agent.py` were all deleted, and
there is **no fallback** — if the startup concept-tree bootstrap fails, a run
fails fast (`_CANONICAL_BOOTSTRAP_OK is False` → `_fail_run`). Fix the bootstrap
(check logs, restart) rather than looking for an opt-out that no longer exists.

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

Plan/PRD docs (historical context): docs/PLAN-canonical-concept-model.md,
docs/PLAN-canonical-concept-model-phase1.md.

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
  the documented blind spot). `skipped` is sourced from the Sheet-12 skip
  receipts the coordinator persists to `{output_dir}/notes12_skips.json` at
  fan-out time (loaded by both the reviewer context and the server finalizer via
  `coverage_checklist.load_notes12_skips`) — an intentionally-skipped note is
  `skipped`, never `missing`, so it doesn't tip the run. An empty inventory yields
  `inventory_available=False` (loud, never empty-but-green). A note the reviewer
  resolves (`not_applicable`/`confirmed_absent`) or that was skipped is also
  dropped from the raw `coverage_gaps` detector family so `verify_findings`
  doesn't re-flag it as still-open.
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
  since they had the identical activation scenario. Pinned by
  `tests/test_notes_reviewer_coverage.py`. The pass
  recomputes + persists on EVERY exit path (`_finalize_coverage` in
  `server._run_notes_reviewer_pass`): success → `reviewed`; crash/construction
  failure → `not_reviewed` draft; empty inventory → `inventory_unavailable` +
  a structured warning event. Manual re-review re-persists for free (same pass).
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
- **Kill switch:** `XBRL_NOTES_COVERAGE` (default ON; `/api/settings` +
  `/api/config`; suite default OFF in `tests/conftest.py`, like spot-check).
  Rollback is a config flip — the table stays as an inert artifact.

Pinned by `tests/test_coverage_checklist.py`,
`tests/test_notes_reviewer_coverage.py`,
`tests/test_notes_coverage_run_status.py`, `tests/test_notes_coverage_api.py`,
`tests/test_notes_detectors_splits.py`, `tests/test_db_schema_v28.py`, and the
`NotesCoveragePanel` web tests.

### 28. mTool fill pipeline — offline zip surgery, one patcher, no DB schema

The `mtool/` package fills a run's figures into an SSM **mTool** MBRS template so
the operator can Validate/Generate the XBRL inside mTool without hand-copying
(docs/PLAN.md, docs/MTOOL-ZIP-RECON-BRIEF.md). Proven end-to-end. The whole path
is **Excel-free** (pure zip/XML surgery), so it runs server-side and in the cloud.

Load-bearing invariants:

- **`offline_fill.py` is a single stdlib-only file** (zipfile/re/ElementTree — no
  openpyxl, no repo imports) because it also travels to the enterprise Windows box
  as one script. Reading parses XML; **writing is targeted text edits** — openpyxl
  load/save corrupts the mTool package and full reserialization breaks namespaces.
  Prefixed sheet XML (`<x:sheetData>`) aborts loudly. Do NOT add a third-party dep
  or repo import (a test asserts this).
- **One patcher, no fork.** The server endpoint imports `offline_fill.fill_workbook`
  — the SAME function the CLI runs. Never reimplement patching in `api/`.
- **Exporter emits LEAF only** (`exporter.build_fill_doc`): ABSTRACT headers +
  COMPUTED totals excluded (mTool derives totals). SOCIE/MATRIX_CELL is deferred
  and **counted**, never silently dropped. Scoped to the run's `{standard}-{level}-`
  family, deduped by `concept_uuid`, reads `run_concept_facts` only.
- **Values emitted verbatim (scale=identity) by default.** The `scale` argument
  and per-row sign flips are **Windows-blocked** until the recon confirms whether
  mTool stores the unscaled or the thousands figure — a wrong scale silently
  1000×-inflates every figure. `denomination` is surfaced in the doc meta.
- **Semantic, not physical:** writes carry a `column_role` (CY/PY × company/group),
  NOT a column letter. mTool's real layout (observed: labels col D, values E/F) is
  resolved at fill time via `exporter.apply_column_map` (fails loudly on a missing
  role) or `column_detect.detect_column_map` (positional + confidence; the endpoint
  refuses low-confidence auto-detection and asks for an explicit map).
- **Machine docs are `strict`** (`build_fill_doc` sets `strict:true`): a non-exact
  label is a bug to surface, not a typo to forgive. Hand-authored operator runs
  stay lenient; fuzzy hits are still reported.
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
- **No DB schema change** — endpoints are stateless over existing tables; uploaded
  templates are request-scoped temp files under `OUTPUT_DIR/_mtool_tmp` (cleaned
  via `BackgroundTask`). Run gate is `completed`/`completed_with_errors` (409
  otherwise).
- **UI is a button + modal (`MtoolFillModal`), not a tab** — avoids a third
  `role="tab"` (gotcha #7).

Pinned by `tests/test_mtool_offline_fill.py`, `test_mtool_exporter.py`,
`test_mtool_routes.py`, `test_mtool_column_detect.py`, and the `MtoolFillModal`
web tests. Full plan: `docs/PLAN.md`; operator guide: `mtool/README.md`.

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
  mirrors the source's table structure and reflects styling through
  `format_ops` — **`content` stays style-free** (gotcha #16 preserved; the
  sidecar only changes what the agent mirrors, not how styling is applied).
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
  Resume re-launches only documents without a finished child run (identified by
  the deterministic `suite-{suite_run}-doc-{doc}` session id), and
  `repo.reconcile_stale_suite_runs` retires crash-orphaned `running` suite runs
  at startup (mirrors `reconcile_stale_review_tasks`). Child runs link via
  `runs.suite_run_id`, threaded through `run_multi_agent_stream` /
  `run_repeat_group_stream`.
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
  (greyed + excluded from the aggregate delta), and warns when gold was edited
  between the two runs (gold `updated_at` is timestamped).
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

## Testing

```bash
# Backend (from repo root) — excludes live LLM tests by default
python -m pytest tests/ -v

# FULL SUITE, ~4x faster — parallelise across cores (needs pytest-xdist).
# ~3100 tests: serial ~240s → ~60s. Use this for the whole-suite gate.
python -m pytest tests/ -n auto
# Focused / TDD runs stay SERIAL on purpose — for one file or one test,
# worker spawn + per-worker imports make `-n auto` SLOWER, so just:
#   python -m pytest tests/test_foo.py -q      (add `-n0` to force serial anywhere)

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
