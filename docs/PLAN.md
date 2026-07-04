# Implementation Plan: mTool Fill Pipeline — Facts → Filled MBRS Template

**Overall Progress:** `10%` (Phase 0 spike complete; Phases 1–6 to do)
**PRD Reference:** none — shaped in-session 2026-07-04/05. Context docs:
`docs/PLAN-mtool-offline-patch-spike.md` (the proven spike),
`docs/MTOOL-ZIP-RECON-BRIEF.md` (Windows recon questions), and the
`mtool_offline_patch_proven` memory.
**Last Updated:** 2026-07-05

> Replaces the previous (completed, 100%) PLAN.md for **Notes Editor —
> Per-Side Border Control + Selection Persistence** — that work is done and
> preserved in git history (same replace-in-place convention).

## Summary

Turn the proven single-sheet offline-patch spike into the product feature the
internal team asked for: the app takes a completed extraction run, generates
mTool fill instructions from the canonical facts DB, patches the user's
uploaded empty mTool template via the proven zip-surgery mechanism (no Excel,
so it runs server-side and in the cloud), and returns one filled workbook the
user opens in mTool to Validate/Generate. The bridge from `run_concept_facts`
to fill instructions — including sign and scale translation — is the core of
the work; the delivery UX builds on it.

## Key Decisions

- **Mechanism: offline zip surgery** (`mtool/offline_fill.py`) — proven
  end-to-end on the Windows box 2026-07-04 (mTool opened the patched file;
  Validate + Generate accepted the injected values). The live-Excel COM route
  is retired from this plan; do not re-litigate.
- **Source of values: `run_concept_facts` only** — the canonical, reviewed
  store. Never the scratch/merged xlsx (gotcha #21; sidesteps gotcha #4).
- **LEAF / MATRIX_CELL facts only** — COMPUTED totals are derived by the
  mTool template's own formulas. Mirrors the eval grader's rule (gotcha #23)
  and the spike's formula guard.
- **Variant knowledge lives in data, not code** — the fill tool stays
  variant-neutral; the exporter + per-template column map carry
  MFRS/MPERS × Company/Group. Validated by the real mTool layout differing
  from ours (labels col D, values E/F) and being absorbed by config alone.
- **Machine-generated instructions use exact label matching only** — fuzzy
  is a hand-authoring convenience; in the pipeline a non-exact match is a
  bug we want surfaced, not papered over (`--strict` at fill time).
- **The fill tool stays a single stdlib-only file** — it still travels to
  the Windows box for operator-driven runs; the server imports the same
  module so there is exactly one patcher (no fork/drift).
- **No DB schema change** — the exporter reads existing tables; delivery
  endpoints are stateless over the run's facts. Keeps rollback trivial.
- **SOCIE deferred** — matrix layout is its own problem on every axis;
  excluded until the linear sheets are shipped.

## Pre-Implementation Checklist

- [x] 🟩 Spike proven (mTool accepts patched workbook end-to-end)
- [ ] 🟥 Windows follow-up answers received (Phase 1 below) — **blocks
  Phase 3 sign/scale, not Phases 2/4**
- [ ] 🟥 No conflicting in-progress work: `main` is clean; the
  `feat/skill-first-workflow-references` branch is unrelated. Start branch
  `feat/mtool-fill-pipeline`.

## Tasks

### Phase 0: Spike (context — done)

- [x] 🟩 **Step 0: Offline-patch spike** — stdlib-only zip-surgery filler,
  label resolution + fuzzy reporting, formula guard, read-back verify, run
  report, BOM tolerance; 35 pinning tests; proven in mTool on Windows.
  - **Verify:** `./venv/bin/python -m pytest tests/test_mtool_offline_fill.py -q`
    → 35 passed. Windows operator confirmed Validate/Generate. ✅

### Phase 1: Close the spike's open questions (Windows side, parallel track)

- [ ] 🟥 **Step 1: Send the follow-up brief to the Windows agent** — append
  to `docs/MTOOL-ZIP-RECON-BRIEF.md` a short addendum asking, against the
  filing that just worked: (a) does the generated XBRL instance carry the
  full unscaled value or the on-sheet (thousands) figure — paste the fact
  element verbatim (recon Task 3.6); (b) did the negative test value survive
  to the instance with the right sign; (c) were derived/total cells correct
  without `--force-recalc`, and did the add-in behave with it on;
  (d) `inspect` output (sheet list + one sheet's column layout) for an MPERS
  and a Group template, to confirm sheet naming + the 4-column Group shape.
  - **Verify:** addendum committed; answers received and recorded in the
    addendum's answer block. Until then, Phase 3 steps stay blocked.

### Phase 2: The bridge — facts → fill instructions (Mac, pure Python)

- [ ] 🟥 **Step 2: Exporter core** — new `mtool/exporter.py`:
  `build_fill_doc(db, run_id) -> dict` (the fill-JSON shape the tool already
  consumes). Reads `run_concept_facts` joined to `concept_nodes` scoped to
  the run's template family (`{standard}-{level}-` prefix, gotcha #21);
  filters to LEAF/MATRIX_CELL; dedups by `concept_uuid` (aliases never
  emitted); maps `(period, entity_scope)` → `column_role`
  (company: `current_year`/`prior_year`; group adds
  `group_current_year`/`group_prior_year`); emits one write per fact with
  the concept's label. Sheets it can't handle (SOCIE) are excluded and
  **counted in the doc's metadata** — no silent truncation.
  - [ ] 🟥 Emit a `meta` block: run id, standard/level, excluded sheets +
    fact counts, generation timestamp — the operator's coverage receipt.
  - [ ] 🟥 Unit tests with a hand-rolled DB fixture (pattern from
    `tests/test_canonical_export.py`: `import_template` +
    `import_company_targets`): CY/PY routing, group scopes, COMPUTED
    excluded, alias deduped, SOCIE excluded-but-counted.
  - **Verify:** `./venv/bin/python -m pytest tests/test_mtool_exporter.py -q`
    green; fixture doc validates against `offline_fill.validate_input`.

- [ ] 🟥 **Step 3: End-to-end dry run on a real run's data** — small harness
  (test or script) that takes an existing completed run in the local DB,
  builds the fill doc, and runs `offline_fill` against our own
  `XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx` (whose labels are the
  same taxonomy vocabulary). Every label must resolve **exactly** —
  fuzzy hits or unresolved rows are exporter bugs (label drift) to fix here,
  where they're cheap.
  - **Verify:** report shows `fuzzy_matched: 0`, `unresolved: 0` for the
    sample run; values in the patched workbook match the run's Values tab.

- [ ] 🟥 **Step 4: Strict mode in the fill tool** — `--strict` flag (and
  `strict: true` accepted in the input doc) so any fuzzy match is refused
  and reported (run `degraded`), not written. Default stays lenient for
  hand-authored operator runs; pipeline-generated docs always set it.
  - **Verify:** new tests — same typo'd input passes lenient, degrades
    strict; exporter output carries `strict: true`.

### Phase 3: Sign & scale translation (blocked on Step 1 answers)

- [ ] 🟥 **Step 5: Pin OUR value conventions** — write down (in
  `mtool/exporter.py` docstring + tests) what unit and sign
  `run_concept_facts` values carry, derived from the extraction prompts +
  verifier (e.g. SOCIE dividends stored positive per ADR-002; SOCF signs per
  the 2026-07-03 regeneration). This is reading + pinning, no behaviour.
  - **Verify:** a test asserts the documented conventions against a fixture
    run seeded with known-sign facts (dividends, payments).

- [ ] 🟥 **Step 6: Translation layer** — `translate(value, concept, target)`
  applied in the exporter: scale (units ↔ thousands per the Step-1 answer +
  the mTool filing's rounding-level choice, carried as an exporter argument)
  and per-row-family sign flips (table driven by the Step-1/recon evidence;
  starts as identity where our conventions already match mTool's).
  - [ ] 🟥 Loud failure mode: a concept with no translation rule in a
    non-identity family → error in the doc's meta, never a guessed value.
  - **Verify:** unit tests per rule; regenerated fill doc for the Windows
    test filing reproduces the exact values the operator entered by hand.

- [ ] 🟥 **Step 7: Second Windows acceptance run, machine-generated** — send
  the Windows box a fill doc generated by Steps 2–6 from a real extraction
  run (FINCO sample); operator patches a fresh mTool template, runs
  Validate/Generate, and spot-checks the instance values against the PDF.
  - **Verify:** operator reports Validate/Generate pass and values (incl. a
    negative and a scaled figure) correct in the generated XBRL. **This is
    the phase gate for the delivery UX.**

### Phase 4: Delivery — server-side fill in the app

- [ ] 🟥 **Step 8: Decide the delivery shape** (user decision, on Step-7
  evidence): (a) server-side upload-template → download-filled (team's
  stated wish, cloud-ready) vs (b) app exports the fill JSON + operator runs
  the CLI locally. Steps 9–11 assume (a); if (b), Step 9 becomes a
  "Download fill instructions" button and Steps 10–11 drop.
  - **Verify:** decision recorded here with rationale.

- [ ] 🟥 **Step 9: Fill-doc endpoint** — `GET /api/runs/{run_id}/mtool-fill`
  (terminal runs only, 409 otherwise; auth middleware covers it
  automatically per gotcha #24). Returns the Step-2 doc as a download.
  Small "mTool" section on the run page (Overview tab; NOT a new
  `role="tab"` — gotcha #7) with the download button + excluded-sheet
  counts.
  - **Verify:** `tests/test_mtool_routes.py` (TestClient, AUTH_MODE=dev):
    200 + valid doc on a completed run, 409 on running, 401 unauthenticated
    (opt-out test). UI button renders and downloads in the web tests.

- [ ] 🟥 **Step 10: Server-side patch endpoint** —
  `POST /api/runs/{run_id}/mtool-fill/patch`: multipart upload of the empty
  mTool template → server builds the doc, auto-builds the column map by
  reading the template's header rows (generalising `inspect`; falls back to
  asking the user when ambiguous), patches via the SAME `offline_fill`
  functions (strict mode), streams back the filled workbook + the JSON run
  report. Reject non-zip/oversize uploads; never persist the upload beyond
  the request (request-scoped temp dir + cleanup).
  - [ ] 🟥 Column auto-detection unit tests against both observed layouts
    (ours A/B/C, real mTool D/E/F) + a Group 4-column fixture.
  - **Verify:** route tests: upload our own template fixture → 200, filled
    workbook opens (openpyxl), report clean; degraded report → still 200
    with `status: degraded` surfaced; running run → 409.

- [ ] 🟥 **Step 11: Report UI** — render the returned run report next to the
  download: written / fuzzy (should be none) / unresolved / skipped-formula
  counts with row detail, mirroring the CLI summary. The operator must see
  "clean" before taking the file to mTool.
  - **Verify:** web test renders a degraded report fixture; manual check in
    the running app with the FINCO run.

### Phase 5: Coverage — all linear sheets, all variants

- [ ] 🟥 **Step 12: All MFRS-Company linear sheets** — extend the exporter's
  sheet map to SOFP/SOPL/SOCI/SOCF faces + sub-sheets + numeric notes
  (13/14). Per-sheet Step-3-style exact-match dry runs against our
  templates.
  - **Verify:** dry-run harness reports 0 fuzzy / 0 unresolved across every
    covered sheet for the sample run.
- [ ] 🟥 **Step 13: Group + MPERS** — group column roles end-to-end (needs
  the Step-1(d) layout answer) and the MPERS sheet map (SoRE included,
  notes shifted 11–15 per gotcha #15).
  - **Verify:** exporter tests for both axes; one Windows acceptance run on
    a Group filing.
- [ ] 🟥 **Step 14: SOCIE (own step, may split into its own plan)** — matrix
  cells need `(row-label × column)` targeting; mTool's SOCIE shape is
  unknown until inspected. Scope only after Step 13 ships.
  - **Verify:** defined when scoped; until then the exporter keeps counting
    SOCIE facts as excluded in the meta block.

### Phase 6: Hardening

- [ ] 🟥 **Step 15: Failure-mode sweep** — corrupted/wrong-variant template
  upload (clear 422s), template whose sheets don't match the run's standard/
  level, duplicate labels on a real mTool sheet (ambiguous → report),
  oversized values, concurrent requests (patching is stateless/pure —
  confirm no shared temp collisions).
  - **Verify:** a test per failure mode; none crash the endpoint (gotcha #20
    spirit: structured errors, never silent).
- [ ] 🟥 **Step 16: CLAUDE.md + docs** — add the mTool pipeline gotcha
  (mechanism, strict-mode rule, "one patcher, no fork" invariant, pointer to
  this plan); update the spike plan's status header; refresh memory.
  - **Verify:** docs reference real file paths; pinning tests named.

## Rollback Plan

- The feature is **purely additive**: no DB migration, no existing-module
  edits beyond registering routes. Rollback = revert the feature commits (or
  simply don't expose the endpoints/UI section); extraction, review, and
  export paths are untouched.
- The fill tool file must stay importable-standalone — if a server-side
  change breaks its stdlib-only property, that's a regression (keep a test
  that `mtool/offline_fill.py` imports with no third-party deps).
- State to check after rollback: none — endpoints are stateless over
  existing tables; uploaded templates are request-scoped temp files.
