# Architecture

Companion to `CLAUDE.md`. This file is the *map* — what each module does and how
data flows through the pipeline. For load-bearing invariants and gotchas, see
`CLAUDE.md`. For subsystem-specific deep dives, see `docs/NOTES-PIPELINE.md` and
`docs/MPERS.md`.

## Mental Model

```
PDF + scout (optional) → coordinator → N extraction agents (parallel) ─┐
                                    → M notes agents (parallel)       ─┤→ workbook_merger → filled.xlsx
                                                                       └→ cross_checks
```

- **Scout** (optional, runs first) walks the PDF's TOC, detects variants,
  detects MFRS vs MPERS, and builds a `notes_inventory`.
- **Extraction coordinator** (`coordinator.py`) fans face-statement agents out
  via `asyncio.gather`. One agent per `StatementType`.
- **Notes coordinator** (`notes/coordinator.py`) fans notes-template agents out
  the same way. Sheet-12 (`LIST_OF_NOTES`) uses a sub-coordinator for a further
  N-way fan-out.
- **Workbook merger** (`workbook_merger.py`) stitches per-statement workbooks
  into a single `filled.xlsx` (face sheets first, notes after).
- **Cross-checks** (`cross_checks/`) validate values across statements once
  everything is merged.

Every agent talks to the same LLM backend through `_create_proxy_model()` in
`server.py` (also called by `run.py`). See CLAUDE.md §"LLM Provider Setup".

## File Map

### Root

| Path | Role |
|---|---|
| `run.py` | CLI entry — runs coordinator for 1–5 statements + any notes templates. |
| `server.py` | FastAPI + SSE web server. `POST /api/run/{session_id}`, `/api/runs` history, `/api/settings`, SPA fallback. Single source of truth for `_create_proxy_model` and `_PROVIDER_PREFIXES`. |
| `coordinator.py` | `RunConfig` + fan-out for face statements. |
| `agent_tracing.py` | Shared trace writer + `MAX_AGENT_ITERATIONS = 50` cap (used by face / notes coordinators and scout). |
| `statement_types.py` | `StatementType` enum, `FilingStandard`, variant registry, `template_path(..., standard=)` resolver. |
| `notes_types.py` | `NotesTemplateType` enum, `NOTES_REGISTRY`, `notes_template_path(..., standard=)`. |
| `workbook_merger.py` | Merges per-statement workbooks into a single `filled.xlsx`. |
| `pricing.py` | Per-model $/token costs + `resolve_notes_parallel(model)` (Sheet-12 fan-out width). `_normalize` must strip the same provider prefixes as `server._PROVIDER_PREFIXES`. |
| `token_tracker.py` | Accumulates token usage per agent for the dashboard. |
| `task_registry.py` | In-memory task registry for SSE event routing. |
| `compare_results.py` | Manual diff script for validating a filled workbook against a reference file. See CLAUDE.md gotcha #4. |
| `validate_ordofliq_fixes.py` | One-off validator for SOFP OrderOfLiquidity formula fixes. |
| `litellm_config.yaml` | LiteLLM proxy config (Mac-local dev proxy). |
| `start.sh` | Mac/Linux launch — starts local LiteLLM (`:4000`) + server (`:8002`). |
| `start.bat` | Windows launch — uses enterprise proxy directly (no local LiteLLM). |
| `setup_data.sh` | Populate `data/` with test PDFs + templates. |
| `requirements.txt` | Python deps. Pydantic AI pinned `>=1.77.0`. |
| `pytest.ini` | Test config, custom markers (`live`, `mpers_*`). |

### `extraction/`

| Path | Role |
|---|---|
| `agent.py` | Generic PydanticAI extraction-agent factory (one per `StatementType`). `ExtractionDeps.filing_standard` threads MFRS vs MPERS. |

### `prompts/`

| Path | Role |
|---|---|
| `_base.md` | Shared persona + output contract for face agents. |
| `_group_overlay.md` | Group-filing instructions for SOFP/SOPL/SOCI/SOCF (6-column layout). |
| `_group_socie_overlay.md` | Group SOCIE overlay (4 vertical row blocks). |
| `_notes_base.md` | Shared notes persona: output contract, 30K char limit, multi-page continuation. |
| `sofp.md`, `sofp_orderofliquidity.md`, `sopl.md`, `soci.md`, `socf.md`, `socie.md`, `socie_sore.md` | Per-variant face prompts. `socie_sore.md` is MPERS-only. |
| `notes_corporate_info.md`, `notes_accounting_policies.md`, `notes_listofnotes.md`, `notes_issued_capital.md`, `notes_related_party.md` | Per-template notes prompts. |

### `tools/`

| Path | Role |
|---|---|
| `template_reader.py` | Read XBRL template structure (rows, sheets, labels). |
| `pdf_viewer.py` | Render PDF pages to PNG for vision input. |
| `fill_workbook.py` | Write extracted values to Excel by label match. |
| `verifier.py` | Evaluate balance/totals via formula evaluator. Runs twice on Group filings (Group cols, then Company cols). |
| `page_cache.py` | LRU cache for rendered PDF pages across agent iterations. |
| `section_headers.py` | Per-template section-header constants used by `fill_workbook`. |

### `cross_checks/`

| Path | Role |
|---|---|
| `framework.py` | `run_all` runner + result protocol. Honours `applies_to_standard` per check. |
| `util.py` | Shared label/value lookups across workbooks. |
| `sofp_balance.py` | Total assets = Total equity + liabilities. |
| `sopl_to_socie_profit.py` | SOPL profit = SOCIE profit row. |
| `soci_to_socie_tci.py` | SOCI TCI = SOCIE TCI row. |
| `socie_to_sofp_equity.py` | SOCIE closing equity = SOFP total equity. |
| `socf_to_sofp_cash.py` | SOCF cash = SOFP cash movement. |
| `sore_to_sofp_retained_earnings.py` | **MPERS-only.** SoRE closing retained earnings = SOFP retained-earnings row. Gated by `variant=SoRE` and `standard=mpers`. |
| `notes_consistency.py` | Note-to-face consistency spot-checks. |

### `notes/`

| Path | Role |
|---|---|
| `agent.py` | Per-template notes agent factory with `_notes_base.md` prompt. `NotesAgentDeps.filing_standard` threads MFRS vs MPERS. |
| `coordinator.py` | Fan-out for notes agents. `SINGLE_AGENT_MAX_RETRIES = 1` retry budget + per-template failure side-log. |
| `listofnotes_subcoordinator.py` | Sheet-12 only: `parallel`-way sub-agents (model-aware), row-112 unmatched concatenation. |
| `payload.py` | `NotesPayload` dataclass. |
| `writer.py` | Writes payloads to xlsx. 30K char cap, evidence col D/F, Group vs Company column rules. |
| `constants.py` | Shared constants (cell limits, retry counts). |
| `coverage.py` | Sheet-12 coverage reporting. |
| `_rate_limit.py` | 429 retry helper shared across notes agents. |

### `scout/`

| Path | Role |
|---|---|
| `agent.py` | PydanticAI scout agent + tools (TOC, variant, notes inventory, standard detector). `ScoutDeps.vision_model` plumbs the same LLM model into the vision fallback. |
| `runner.py` | Back-compat re-export of `agent.py`. |
| `toc_locator.py` | Deterministic TOC page finder. |
| `toc_parser.py` | TOC text parser (English + Malay combined titles). |
| `variant_detector.py` | Deterministic variant signal scorer. Takes `standard=` filter. |
| `standard_detector.py` | Presence-based MFRS/MPERS detector from TOC text. |
| `notes_discoverer.py` | Fast PyMuPDF-regex notes inventory (+ async entry). Falls back to vision when regex yields nothing. |
| `notes_discoverer_vision.py` | Batched parallel vision pass for scanned PDFs. See docs/NOTES-PIPELINE.md. |
| `vision.py` | LLM vision helpers (TOC extraction for scanned PDFs). |
| `infopack.py` | Typed scout output — page refs, variants, confidence, `detected_standard`. |
| `calibrator.py` | Legacy page calibrator, retained for reference. |

### `db/`

| Path | Role |
|---|---|
| `schema.py` | SQLite DDL + v1→v2 migration for the runs lifecycle columns. `CURRENT_SCHEMA_VERSION = 2`. |
| `repository.py` | CRUD + history queries. `create_run`, `mark_run_merged`, `mark_run_finished`, list/filter/detail/delete. |
| `recorder.py` | `SSEEventRecorder` — persists live SSE events to the run. |

### `web/`

Vite + React frontend. Inline styles (not Tailwind — see CLAUDE.md gotcha #7).

**Pages:**

| Path | Role |
|---|---|
| `src/pages/ExtractPage.tsx` | Main extraction view — upload, PreRunPanel, agent tabs, results. |
| `src/pages/HistoryPage.tsx` | Past-runs browser (filters + list + detail modal). |

**Components (`src/components/`):**

| Path | Role |
|---|---|
| `TopNav.tsx` | Extract / History nav bar. |
| `UploadPanel.tsx` | PDF upload + metadata. |
| `PreRunPanel.tsx` | Model picker, filing standard/level toggles, scout toggle, face + notes selectors. |
| `StatementRunConfig.tsx`, `NotesRunConfig.tsx` | Per-pipeline config panels. |
| `VariantSelector.tsx` | Variant picker. Standard-aware (`variantsFor(stmt, standard)`). |
| `ScoutToggle.tsx` | Scout on/off + inline model dropdown. |
| `SettingsModal.tsx` | Global model defaults. |
| `AgentTabs.tsx` | Per-agent tab bar with status badges. |
| `AgentTimeline.tsx` | Terminal-style tool timeline (live + history replay). |
| `ToolCallCard.tsx` | Single tool-row primitive shared by live + history. |
| `NotesSubTabBar.tsx` | Sub-agent chip bar inside Notes-12. |
| `PipelineStages.tsx` | Stage indicator (scout → extract → notes → merge). |
| `ElapsedTimer.tsx` | Live elapsed-time counter. |
| `TokenDashboard.tsx` | Token-usage summary (populated on completion). |
| `ValidatorTab.tsx` | Cross-check results display. |
| `ResultsView.tsx` | Results summary after merge. |
| `HistoryList.tsx`, `HistoryFilters.tsx` | History browser chrome (filters + list). |
| `RunDetailView.tsx`, `RunDetailModal.tsx` | Past-run replay (agents, timeline, cross-checks, download). |
| `SuccessToast.tsx` | Ephemeral post-merge toast. |
| `icons.tsx` | Shared icon primitives. |

**Lib (`src/lib/`):**

| Path | Role |
|---|---|
| `types.ts` | `StatementType`, `FilingStandard`, `NotesTemplateType`, payload + response types, `variantsFor`. |
| `api.ts` | Fetch helpers (run, history, settings, scout). |
| `sse.ts` | SSE client. |
| `appReducer.ts` | `appReducer` / `agentReducer` + notes tab labels. |
| `buildToolTimeline.ts` | SSE → timeline reducer. Includes sub-agent helpers for Notes-12. |
| `toolLabels.ts` | `humanToolName` / `argsPreview` / `resultSummary`. |
| `runStatus.ts` | Status badge/color helpers. |
| `modelId.ts` | Strip PydanticAI `repr()` wrappers from persisted model ids. |
| `time.ts` | `formatMMSS` / `formatElapsedMs`. |
| `notes.ts` | Notes-template metadata for the UI. |
| `theme.ts` | Shared inline-style tokens. |

### `XBRL-template-MFRS/`

```
Company/           Company-level templates (4 cols: label, CY, PY, source)
Group/             Group-level templates (6 cols: label, Group CY, Group PY, Company CY, Company PY, source)
backup-originals/  Pre-formula-fix snapshots (see CLAUDE.md gotcha #3) — do not edit
backup/            Earlier pre-format-variant snapshots — do not edit
```

### `XBRL-template-MPERS/`

```
Company/           15 MPERS templates per level (01..09 face, 10-SoRE MPERS-only, 11..15 notes)
Group/             15 MPERS templates per level (09-SOCIE uses 4-block layout)
backup-originals/  Generation-1 snapshot — diff here after re-running scripts/generate_mpers_templates.py
```

See `docs/MPERS.md` for the filing-standard axis and generator details.

### `scripts/`

| Path | Role |
|---|---|
| `generate_mpers_templates.py` | Emits all 30 MPERS xlsx files from the SSM linkbase. Run with `--level {company,group} --snapshot`. |

### `config/`

| Path | Role |
|---|---|
| `models.json` | Available models registry (id, provider, display name, per-model `notes_parallel`). |

### `docs/`

| Path | Role |
|---|---|
| `ARCHITECTURE.md` | This file. |
| `NOTES-PIPELINE.md` | Notes subsystem deep-dive. |
| `MPERS.md` | MPERS filing-standard deep-dive. |
| `SYNC-MATRIX.md` | "Files That Must Stay in Sync" cross-reference table. |
| `PORTING-WINDOWS.md` | Mac → Windows porting checklist. |
| `PLAN-*.md` | Active implementation plans — historical context, not API. |
| `workflows/*.md` | Per-statement fill-workflow notes. |
| `Archive/` | Completed plan/fix documents retained for audit trail (e.g. `TEMPLATE-FORMULA-FIX-GUIDE.md`). Read-only. |
| `xbrl-field-descriptions.md` | Field reference for the XBRL taxonomy. |

## Key Data Flow

**CLI run (`python3 run.py ...`):**

1. `run.py` parses args, loads `.env`, calls `_create_proxy_model()`.
2. If `--scout-enabled`, scout runs first, producing an `Infopack` (variants,
   page hints, notes inventory, detected standard).
3. `coordinator.run_multi_agent` fans out extraction agents; each writes its
   own per-statement workbook under `output/run_NNN/`.
4. If `--notes ...` specified, `notes.coordinator.run_notes_pipeline` fans
   notes agents out in parallel.
5. `workbook_merger.merge()` stitches everything into `filled.xlsx`.
6. `cross_checks.framework.run_all()` validates across statements.
7. Results print to stdout + persist under `output/run_NNN/`.

**Web run (`POST /api/run/{session_id}`):**

Same pipeline, wrapped in SSE streaming. `runs` row created **before**
validation (see CLAUDE.md gotcha #10). Every SSE event is persisted via
`SSEEventRecorder` so the History page can replay the run.

## Output Directory

```
output/
  run_001/       # CLI runs (auto-numbered)
  run_002/
  {uuid}/        # Web UI runs (UUID per session)
```

`run.py` uses `Path(__file__).resolve().parent / "output"` as the base — works
regardless of caller's CWD.
