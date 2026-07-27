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

Variable names + defaults live in `.env.example` — copy it and fill in keys.
Non-derivable notes:

- `LLM_PROXY_URL` empty = direct mode. `LLM_PROXY_API_KEY` is the proxy auth
  key (`start.sh` sets the local-dev master key). On Windows there is no
  `LLM_PROXY_API_KEY` — `GOOGLE_API_KEY` doubles as the proxy auth key.
- `SCOUT_MODEL` falls back to `TEST_MODEL` when blank.
- Auth (gotcha #24): `AUTH_MODE` unset = real email+password login;
  `AUTH_MODE=dev` auto-sessions as dev@localhost (tests/CI only; refuses to
  boot on Azure). `SESSION_SECRET` is REQUIRED in prod (startup fails without
  it).
- Item-32 fact-based verification (gotcha #25): `XBRL_FACT_BASED_CHECKS` and
  `XBRL_FACT_BASED_VERIFY` both DEFAULT ON; set `=0` to fall back to the xlsx
  path.
- The canonical concept model is MANDATORY (rewrite Phase 1.1) — the legacy
  `XBRL_CANONICAL_MODE` opt-out was removed and the flag is no longer read
  (gotcha #21).

### PydanticAI Model Creation (v1.77+)

Copy the working construction pattern from `_create_proxy_model()` in
`server.py` (`OpenAIChatModel` / `GoogleModel` / `AnthropicModel`, each built
with its `provider=` object).

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

`db/schema.py` carries `CURRENT_SCHEMA_VERSION` (committed: **35**). `init_db`
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
`notes_coverage_rows` (#27) · v29 `notes_cells.style_source` (#16) · v30–v31
Evals workspace repeats/taxonomy/gold-prose + suites (#30) · v32
`eval_suite_run_docs` frozen-corpus snapshot (#30) · v33 gold fingerprint on
`eval_scores` + benchmark archive flag (#30) · v34 `run_lineage` stage-level
resume trail · v35 `mtool_fill_receipts` durable mTool fill record (#28).

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
11–15 (MPERS). The `notes_cells` DB table is the **source of truth**; the xlsx
download is regenerated from it at stream time (the overlay is authoritative,
including reviewer deletions via tombstones). Cap is 30,000 RENDERED chars. An
agent re-run **CLOBBERS human edits** (confirm-gated). Word-sourced tables are
copied verbatim — inline styles and all — and stamped `data-source-styled`
so no renderer adds a grid the source never had; PROSE stays style-free,
enforced in code.

**Full invariants** (sanitiser whitelist, the two AI styling paths, table
themes, clipboard/mTool dialect translation, editor rules):
`.claude/rules/notes-pipeline.md` — auto-loads when working under `notes/`,
`mtool/`, `web/src/lib/`, or the notes web components. Read it BEFORE changing
anything in those areas.

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

Gold lives in `gold_concept_facts` — the SAME shape as `run_concept_facts` —
and grading is an exact set join on `concept_uuid + period + entity_scope`,
scoped by the benchmark's explicit `template_id` SET (never a standard/level
prefix), LEAF/MATRIX_CELL only, `score = matched / gold_cells`. Prefer seeding
gold **from a run** (`POST /api/benchmarks/from-run`) — the workbook-upload
path silently drops formula cells with no cached value.

**Full invariants:** `.claude/rules/eval-benchmarks.md` — auto-loads when
working under `eval/` or the eval/suites web pages.

### 24. Auth layer gates every `/api/*` route (schema v18)

Every `/api/*` route is auth-gated (exempt: `/api/auth/*` prefix, exact
`/api/health`). **`AUTH_MODE=dev` is required to run the test suite** —
`tests/conftest.py` defaults it; running pytest with it unset 401s API-hitting
tests. `SESSION_SECRET` is mandatory in prod (startup refuses to boot without
it), and dev-mode refuses to boot on Azure. Sessions are server-side +
revocable with a 15-min sliding idle timeout; `auth_users.is_admin` is the
privilege boundary, enforced server-side per route.

**Full invariants:** `.claude/rules/auth.md` — auto-loads when working under
`auth/` or the login/settings pages.

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

A post-reviewer **coverage checklist** reconciles every top-level note in the
scout inventory against where its content landed across all notes sheets —
keyed on note numbers + provenance only, never content matching. An unresolved
`missing` row / uninvestigated `suspected_gap` / unavailable inventory tips the
run to `completed_with_errors`. Kill switch `XBRL_NOTES_COVERAGE` (default ON;
suite default OFF).

**Full invariants:** `.claude/rules/notes-pipeline.md` (shared with gotcha #16).

### 28. mTool fill pipeline — offline zip surgery, one patcher, gated OFF

`mtool/offline_fill.py` fills a run's figures into an SSM mTool template by
pure zip/XML **text surgery** — a single stdlib-only file (no openpyxl, no
repo imports; openpyxl load/save corrupts the mTool package). **One patcher:**
the server endpoint imports the same `fill_workbook` the CLI runs — never
reimplement patching in `api/`. Exporter emits LEAF only.

**The action is NOT exposed:** `XBRL_MTOOL_FILL` defaults **off** and every
workbook-producing route 404s without it. A filled MBRS workbook is a filing
artifact, so the default flips only after a machine-generated workbook passes
Validate/Generate on Windows (plan Step 7). Values are unit-aware and the
shipped translation is identity — scale/sign rules stay Windows-blocked.
Filing readiness is `mtool/preflight.py`, not run status. Every fill writes a
receipt (schema v35).

**Full invariants** (exposure gate, preflight policy, unit classes, semantic
column detection, patch/download split, receipts, the SOFP label-ambiguity
ceiling, footnote `fn_` orphan pool): `.claude/rules/mtool-fill.md` —
auto-loads when working under `mtool/`. Plan:
`docs/PLAN-mtool-fill-pipeline.md`.

### 29. Word (.docx) input — convert at the door; PDF stays the spine

Uploads accept `.docx`: converted to a text PDF **at upload time**
(`ingest/word_convert.py` — LibreOffice on Mac/cloud, Word COM on Windows) and
stored as the run's `uploaded.pdf`, so the whole page-based pipeline runs
unchanged. Conversion failure is a 422 with a plain-language Save-As-PDF
fallback, never a crash. A best-effort `source.html` sidecar (mammoth) feeds
the notes verbatim-table passthrough — gotcha #16 owns that rule.

**Full invariants:** `.claude/rules/word-input.md` — auto-loads when working
under `ingest/`.

### 30. Evals workspace — repeats/consistency, mTool gold, suites, trends

The Evals workspace (suites, repeats, consistency, mTool gold, trends) launches
completely normal extraction runs and only grades/aggregates — it **NEVER
alters extraction behaviour**. Scoring formulas are fixed and decompose:
accuracy = matched ÷ gold slots; consistency = unanimous agreement over the
union of filled slots (≥2 finished repeats, else "unavailable"); suite
aggregate = MEAN of per-document accuracy. History hides suite children by
default.

**Full invariants** (batch runner, gap-filling Resume, frozen-corpus snapshots,
gold fingerprints, frontend rules): `.claude/rules/eval-benchmarks.md`
(shared with gotcha #23).

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
