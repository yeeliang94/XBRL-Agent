# MPERS Filing Standard

Companion to `CLAUDE.md` gotcha #15. This is the full walkthrough; CLAUDE.md
only carries the load-bearing invariants.

## What It Is

MPERS (Malaysian Private Entities Reporting Standard) is a first-class filing
standard alongside MFRS. A second axis — `filing_standard: "mfrs" | "mpers"`
— threads through the whole pipeline: registry → coordinator → agent factories
→ server API → cross-checks → scout → frontend toggle → history persistence.

**MFRS is the default everywhere** so pre-MPERS callers / rows / payloads
keep working without change.

## Templates

`XBRL-template-MPERS/{Company,Group}/` contains 15 templates per filing level,
generated deterministically from the SSM MPERS linkbase
(`SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers/`) by
`scripts/generate_mpers_templates.py`.

### Numbering Convention

15 slots per level, shifted from MFRS's 14:

| Slot | File | Purpose |
|---|---|---|
| 01..09 | `01-SOFP-CuNonCu` .. `09-SOCIE` | Same face statements as MFRS |
| 10 | `10-SoRE.xlsx` | **MPERS-only** Statement of Retained Earnings (simplified SOCIE variant) |
| 11..15 | `11-Notes-CorporateInfo` .. `15-Notes-RelatedParty` | Notes (shifted from 10..14 in MFRS) |

### Column Layout

Identical to MFRS:

- **Company templates:** 4 cols — A=label, B=CY, C=PY, D=source.
- **Group templates:** 6 cols — A=label, B=Group CY, C=Group PY, D=Company CY, E=Company PY, F=source.
- **Group SOCIE** uses the 4-block vertical layout (same as MFRS).

## Pipeline Entry Points

### CLI

```bash
python3 run.py data/foo.pdf --standard mpers --level group --statements SOFP SOCIE
```

### API

`RunConfigRequest.filing_standard` (defaults `"mfrs"`). Rejects
variant/standard mismatches (e.g. `SOCIE/SoRE` on MFRS) **before** launching
any agent.

### UI

- `PreRunPanel` renders an MFRS/MPERS toggle above the Filing Level toggle.
- Scout's `detected_standard` preselects it; the user toggle always wins.
- `VariantSelector` exposes `SOCIE/SoRE` only on MPERS via
  `variantsFor(stmt, standard)` in `web/src/lib/types.ts`.

### History

`filing_standard` rides `run_config_json`, surfaces in `GET /api/runs` and
`/api/runs/{id}`, renders a teal `MPERS` badge in `HistoryList`, and feeds an
`All / MFRS / MPERS` filter in `HistoryFilters` (client-side filter at launch
volume).

## Cross-Checks

The framework honours `applies_to_standard` on each check (defaults to
`frozenset({"mfrs", "mpers"})`, narrows per check).

- `sore_to_sofp_retained_earnings` is **MPERS-only** and fires only when the
  SOCIE variant is `SoRE`.
- `sopl_to_socie_profit`, `soci_to_socie_tci`, and `socie_to_sofp_equity` gate
  themselves out on SoRE runs (SoRE has no per-component matrix).

## Scout

- `scout/standard_detector.py` — pure, presence-based keyword scorer — populates
  `Infopack.detected_standard` from TOC text during `_find_toc_impl` /
  `_parse_toc_text_impl`.
- `scout/variant_detector.py` takes an optional `standard=` filter so MPERS
  SOCIE pages can score `SoRE` over `Default`.

## Generator Usage

```bash
# Regenerate Company templates (15 files) + snapshot backup
python3 scripts/generate_mpers_templates.py --level company --snapshot

# Regenerate Group templates (15 files; SOCIE uses 4-block layout)
python3 scripts/generate_mpers_templates.py --level group --snapshot
```

**Always pass `--snapshot`** when regenerating — it writes the previous
version to `backup-originals/` so you can diff schema drift before accepting.

## Notes Pipeline MPERS-Awareness (2026-04-23 hardening)

Run #105 (FINCO MPERS, 2026-04-23) surfaced that the MPERS wiring was
only half-complete: templates / registry / cross-checks / server /
frontend all honoured `filing_standard`, but the **notes prompt layer
and the label fuzzy matcher did not**. MPERS agents were reading
MFRS-flavoured prompts (sheet map, cross-sheet hints, taxonomy
vocabulary) and the writer was silently rejecting short MPERS labels
because the `[text block]` suffix pushed the 0.85 fuzzy match below
threshold. Only 3 of 9 disclosure notes landed on the failing run.

The hardening lives across four code paths, all of which now branch
on `filing_standard`:

### 1. Prompt rendering (`notes/agent.py`)

- `render_notes_prompt(…, filing_standard=)` — required kwarg. MFRS
  is the default.
- `_render_sheet_map(standard)` emits Sheet-10..14 on MFRS,
  Sheet-11..15 on MPERS (with Sheet 10 called out as the MPERS-only
  SoRE face-statement slot).
- `{{CROSS_SHEET:<topic>}}` tokens in per-template prompts
  (`notes_listofnotes.md`, `notes_accounting_policies.md`) resolve
  to the right sheet number per standard via
  `_apply_cross_sheet_tokens`.
- `_render_mpers_overlay(standard)` appends a 3-point block on MPERS
  runs only: the `[text block]` suffix convention, the smaller
  concept set, and the SoRE slot location.
- `_render_label_catalog(labels)` embeds the template's actual col-A
  labels into the prompt so agents pick from the live vocabulary
  rather than their MFRS training prior. Soft-capped at 180 rows
  with a `read_template` fallback footer. Loaded at factory time by
  `_load_template_label_catalog` and cached on
  `NotesDeps.template_label_catalog`.

### 2. Label normalisation (`notes/labels.py`)

`notes.labels.normalize_label` is the shared comparator used by
`notes.writer._normalize` and `notes.coverage._normalize_label`. Both
strip trailing `[text block]`, `[abstract]`, `[axis]`, `[member]`,
`[table]` (case-insensitive) so the MPERS suffix stops moving labels
below the 0.85 fuzzy threshold. Extend `_TAXONOMY_SUFFIXES` in
`notes/labels.py` if future MPERS generator runs introduce a new
type bracket.

### 3. SOCIE cross-checks (`cross_checks/*_socie*.py`)

MPERS SOCIE is a flat 2-column layout (CY in col B, PY in col C) with
dimensional members on separate axis rows — the MFRS 24-column matrix
doesn't apply. Three checks (`socie_to_sofp_equity`,
`sopl_to_socie_profit`, `soci_to_socie_tci`) now accept
`filing_standard` via `run` and read via:

- `socie_total_column(standard)` — used by equity + TCI (pure totals);
  returns col 2 on MPERS, col 24 on MFRS.
- `socie_column(ws, filing_standard=)` — used by profit (which on
  MFRS still branches on NCI presence); returns col 2 on MPERS,
  col 24 / col 3 on MFRS depending on `has_nci_data`.

`cross_checks.framework.run_all` threads `filing_standard` into each
check via a try/except `TypeError` guard so pre-hardening check
signatures still run.

### Regression anchors

- `tests/test_notes_prompt_filing_standard.py` — 11 tests pinning
  the prompt-rendering contract.
- `tests/test_notes_writer_suffix_normalize.py` +
  `tests/test_coverage_validator_suffix_normalize.py` — 13 tests
  locking the suffix-stripping invariant across writer + validator.
- `tests/test_notes_prompt_label_catalog.py` + companion integration
  test — 8 tests covering the catalog seed.
- `tests/test_notes_prompts_no_mfrs_leak.py` — parametrised guard
  over all 7 notes-side prompt files; fails on any hardcoded `MFRS`
  literal.
- `tests/test_cross_checks_mpers_socie.py` — 6 tests reproducing the
  three run-#105 failures + their MFRS regression counterparts.
- `tests/test_e2e_mpers_notes.py` — golden E2E regression lock:
  simulates sub-agents emitting bare MFRS-style labels against an
  MPERS template, asserts ≥ 8 rows land (pre-fix baseline was 3).

## Taxonomy Updates

When SSM ships a new MPERS taxonomy (e.g. 2024 vs 2022):

1. Drop new files into `SSMxT_YYYYvN.N/rep/ssm/ca-2016/fs/mpers/`.
2. Re-run the generator for both levels with `--snapshot`.
3. Diff the re-emitted bundle against `XBRL-template-MPERS/backup-originals/`.
4. Only accept after reviewing schema drift.

## Tests

```bash
# All MPERS regression tests
python3 -m pytest tests/test_mpers_generator.py -v

# Per-phase
python3 -m pytest -m mpers_inventory -v        # Phase 1 (inventory + format pins)
python3 -m pytest -m mpers_generator_core -v   # Phase 2 (walker + emitter)
python3 -m pytest -m mpers_company -v          # Phase 3 (Company xlsx on disk)
python3 -m pytest -m mpers_formulas -v         # Phase 4 (calc linkbase + SUM)
python3 -m pytest -m mpers_group -v            # Phase 5 (Group layout + SOCIE blocks)
python3 -m pytest -m mpers_snapshot -v         # Phase 6 (backup-originals)

# Pipeline wiring
python3 -m pytest tests/test_mpers_wiring.py -v
```

## Key Files

- `statement_types.py` — `FilingStandard`, `TEMPLATE_DIRS`,
  `Variant.applies_to_standard`, `variants_for_standard`,
  `template_path(..., standard=)`
- `notes_types.py` — `notes_template_path(..., standard=)`
- `coordinator.py` — `RunConfig.filing_standard`
- `notes/coordinator.py` — `NotesRunConfig.filing_standard`
- `extraction/agent.py` — `ExtractionDeps.filing_standard`
- `notes/agent.py` — `NotesAgentDeps.filing_standard`
- `server.py` — `RunConfigRequest.filing_standard`, variant/standard early
  validation, `_build_default_cross_checks`
- `run.py` — `--standard` flag
- `cross_checks/framework.py` — `applies_to_standard` gate
- `cross_checks/sore_to_sofp_retained_earnings.py` — new MPERS-only check
- `cross_checks/{sopl_to_socie_profit,soci_to_socie_tci,socie_to_sofp_equity}.py`
  — SoRE variant gate
- `scout/infopack.py` — `Infopack.detected_standard`
- `scout/standard_detector.py`
- `scout/agent.py` — populates `deps.detected_standard`, plumbs standard into
  `check_variant_signals`
- `scout/variant_detector.py` — `standard=` filter
- `prompts/socie_sore.md` — MPERS-only SoRE prompt
- `db/repository.py` — `RunSummary.filing_standard`
- `web/src/lib/types.ts` — `FilingStandard`, `DetectedStandard`, `variantsFor`
- `web/src/components/PreRunPanel.tsx` — MFRS/MPERS toggle
- `web/src/components/VariantSelector.tsx` — `filingStandard` prop
- `web/src/components/HistoryList.tsx` — MPERS badge
- `web/src/components/HistoryFilters.tsx` — standard dropdown
- `web/src/pages/HistoryPage.tsx` — client-side filter
- `scripts/generate_mpers_templates.py` — generator CLI
- `XBRL-template-MPERS/{Company,Group}/*.xlsx` — 15 templates per level
- `XBRL-template-MPERS/backup-originals/` — generation-1 snapshot
- `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers/` — authoritative pre_/lab_/cal_
  linkbases
