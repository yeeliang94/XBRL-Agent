# Implementation Plan: mTool Fill Pipeline — Facts → Filled MBRS Template

> **2026-08-05 replay note.** This plan describes the v2 build (b04b178,
> 2026-07-28), which was reverted wholesale on 2026-08-01 (7140f59) and
> re-applied on 2026-08-05 **without the `XBRL_MTOOL_FILL` exposure gate** —
> the product owner chose to keep the fill exposed. Wherever this document
> says the action is "gated OFF" or names that flag, read: the gate does not
> exist; the shipped safety is the preflight (Step 8A's readiness half), the
> report-before-file flow (Step 11A) and the fill receipts (Step 19), now on
> schema **v38** (v35–v37 were taken by notes source integrity in the
> interim). The gate tests became `tests/test_mtool_preflight.py`, which also
> pins the gate's absence. The two Windows-blocked questions (Steps 1 and 7)
> remain open and still block any non-identity value translation.

**Overall Progress:** `95% implemented / 0% released` — every peer-review
finding is now closed in code and pinned by tests. **Nothing here is
filing-ready yet** while the two Windows-blocked questions (Steps 1 and 7)
remain open.

Two things stand between "implemented" and "released", and neither can be done
from a Mac:

1. **Step 1's answers** — does mTool's generated XBRL carry the full value or
   the on-sheet thousands figure, and how does a Group template mark its
   columns. Addendum A of `docs/MTOOL-ZIP-RECON-BRIEF.md` is written and
   waiting.
2. **Step 7** — one machine-generated workbook through Validate/Generate on the
   Windows box. Only then may the flag default on.

**Status vocabulary** (every task below carries one):
`designed` · `implemented` · `Mac-tested` · `Windows-accepted` · `released`.
🟩 means *Mac-tested at most* unless the line says otherwise.

**PRD Reference:** none — shaped in-session 2026-07-04/05. Context docs:
`docs/PLAN-mtool-offline-patch-spike.md` (the proven spike),
`docs/MTOOL-ZIP-RECON-BRIEF.md` (Windows recon questions + Addendum A), and the
`mtool_offline_patch_proven` memory.
**Last Updated:** 2026-07-28

> Moved here from `docs/PLAN.md` on 2026-07-27 (verbatim copy) when that slot
> was taken by the extraction-efficiency plan. This work is **not cancelled**.
> It in turn replaced the completed PLAN.md for **Notes Editor — Per-Side
> Border Control + Selection Persistence**, preserved in git history.

## Peer Review 2026-07-27 — Verified Findings

A different team lead reviewed the plan + implementation. Every claim was
checked against the code. Verdicts:

| # | Finding | Verdict | Evidence |
|---|---|---|---|
| 1 | Release gates contradict each other | **CONFIRMED** | Checklist says Windows answers don't block Phase 4; Step 7 says it **is** the delivery gate; Steps 9–11 are done while 7–8 are open |
| 2 | Scale model has no unit dimension | **CONFIRMED** | `mtool/exporter.py` — `scale: float = 1.0` is one global multiplier applied by `_scaled()` to every fact; no unit or data type on the fact row. A thousands conversion would multiply share counts |
| 3 | Column "confidence" doesn't validate meaning | **CONFIRMED** | `mtool/column_detect.py` picks the densest text column, then assigns roles **positionally** (`start + offset`). Confidence measures text density, not header semantics — a "high" Group result can swap Group/Company or CY/PY |
| 4 | "Terminal" is not a filing-readiness gate | **CONFIRMED** | `api/mtool.py` `_FILLABLE_STATUSES` includes `completed_with_errors`; the exporter deliberately writes `conflict` facts (counted in `conflict_writes`, but not blocking) |
| 5 | UX can't satisfy "see clean before taking the file" | **CONFIRMED** | `MtoolFillModal.tsx` triggers the download **before** parsing the report — the code comment states this intent. Report rides an HTTP header capped at `_HEADER_LIST_CAP = 20` / `_HEADER_MAX_BYTES = 6000` |
| 6 | No audit receipt / no consistent snapshot | **CONFIRMED as fact** | "No DB schema change … endpoints are stateless" is a stated Key Decision. Whether it's a defect is a judgement call — for a regulatory filing artifact, the reviewer's call is fair |
| 7 | Acceptance coverage too narrow | **PARTIALLY CONFIRMED** | 8 mTool test files exist (more than implied), but only one real fixture (`data/MBRS_test.xlsx`). Real-mTool coverage across MFRS/MPERS × Company/Group is genuinely thin |
| 8 | Progress reporting unreliable | **PARTIALLY CONFIRMED** | Status drift is real. Generation timestamp genuinely **missing** from the exporter's `meta` block. But excluded-sheet metadata **is** emitted (`sheets_covered` + `excluded_*` counts) — that half of the claim is wrong |

**Accepted in full.** Findings 1, 4 and 5 are addressed by the new Step 8A
(exposure gate) below; 2 and 3 become hard prerequisites inside Phase 3; 6 and 7
become new Phase 6 steps.

**All eight closed in code, 2026-07-28** — see each step for the implementation
and its pinning test. Summary of what actually changed:

| # | How it was closed |
|---|---|
| 1 | `XBRL_MTOOL_FILL` (default off) gates every workbook-producing route AND the run-page action. `tests/test_mtool_exposure_gate.py` |
| 2 | The global `scale` argument is **gone**. Facts now resolve a unit class from the SSM taxonomy (`mtool/units.py` + a committed index); `mtool/translation.py` holds versioned manifests, ships identity, and REFUSES an unclassified value under any non-identity rule. `tests/test_mtool_units.py` |
| 3 | `column_detect` reads mTool's own marker rows (`#PRIM#`, `#ENDT#`, `#UNITSCALE#`, `#DOM#`), so periods are matched by DATE and a dimensional sheet is refused. `needs_confirmation()` replaces confidence as the gate; Group layouts and unknown template fingerprints always need a human. `tests/test_mtool_column_detect.py` |
| 4 | `mtool/preflight.py` — blocks on conflicting figures that would reach the workbook, open reviewer flags, unresolved notes coverage. Override needs a written reason, stored on the receipt. |
| 5 | Patch returns the COMPLETE report + an artifact id; the workbook is a second request that a degraded fill won't release unacknowledged. `tests/test_mtool_artifact_and_receipt.py` |
| 6 | Schema v35 `mtool_fill_receipts` + a single consistent fact snapshot per fill. `tests/test_db_schema_v35.py` |
| 7 | Template fingerprint registry with provenance (`mtool/known_templates.json`) + a full-sweep dry run over every linear sheet × standard × level. `tests/test_mtool_coverage_dry_run.py` |
| 8 | `generated_at` + `translation_version` + the snapshot identity now ride in the doc's `meta`. `tests/test_mtool_exporter.py` |

## Discovered limitation (2026-07-28) — label addressing can't reach all of SOFP

Not in the peer review; found by the Step-12 sweep, and larger than most of what
was. A write is addressed by `(sheet, label)`. On `SOFP-Sub-CuNonCu` the same
label legitimately repeats — "Cost" and "Accumulated depreciation" appear once
per asset class — so **185 of 327 SOFP writes (56%) resolve to several rows and
are refused**. Refusing is correct: guessing would file a figure against the
wrong line. But it caps how much of SOFP a label-addressed fill can place, and
no amount of better matching fixes it.

Measured per sheet and pinned by `tests/test_mtool_coverage_dry_run.py`
(0 fuzzy / 0 unresolved everywhere; ambiguity confined to the SOFP and SOPL
sub-sheets — the numeric notes are clean).

The fix needs a stable per-row key. A real mTool template already carries one:
column A of every data row holds the taxonomy concept
(`full_ifrs-cor_2022-03-24.xsd#ifrs-full_PropertyPlantAndEquipment`). Our own
templates don't, and the concept-model parser mints UUIDs from
`(template_id, sheet, row, label)` and discards the concept id — so wiring this
through is a change to the parser and importer, not to the fill tool. Question
A.5 of the recon addendum asks whether that column is present on every real
template; scope the work only after it comes back.

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
  **REVISED 2026-07-27 (finding 6):** this holds for the *patching mechanism*,
  but a filing artifact needs a durable receipt. Step 19 adds one additive
  table; the patcher itself stays stateless.
- **Exposure is separate from implementation (new, finding 1).** Code being
  written and Mac-tested does not make the patch/download action safe to show.
  The action stays behind an off-by-default flag until a machine-generated
  workbook has passed Validate/Generate **and** instance-value inspection on
  Windows (Step 7).
- **SOCIE deferred** — matrix layout is its own problem on every axis;
  excluded until the linear sheets are shipped.

## Pre-Implementation Checklist

- [x] 🟩 Spike proven (mTool accepts patched workbook end-to-end)
- [ ] 🟥 Windows follow-up answers received (Phase 1 below) — **blocks the
  DEFAULT-ON flip of any Phase 4 patch/download action.** Addendum A is written
  and committed (Step 1); the answers are outstanding. Phase 3 code landed
  around the gap: the translation layer is unit-aware and versioned, and ships
  the identity manifest, so the recon answer changes a config value rather than
  a design.
  *(Corrected 2026-07-27, finding 1. The old wording — "blocks Phase 3, not
  Phases 2/4" — contradicted Step 7's own "this is the phase gate for the
  delivery UX" and is how Steps 9–11 came to ship ahead of their gate.
  Phase 4 code may be **written**; it may not be **exposed**.)*
- [x] 🟩 No conflicting in-progress work. Work landed on branch
  `feat/mtool-fill-pipeline-v2` (the original `feat/mtool-fill-pipeline` was
  already merged to `main`, so a fresh branch avoids rewriting its history).

## Tasks

### Phase 0: Spike (context — done)

- [x] 🟩 **Step 0: Offline-patch spike** — stdlib-only zip-surgery filler,
  label resolution + fuzzy reporting, formula guard, read-back verify, run
  report, BOM tolerance; 35 pinning tests; proven in mTool on Windows.
  - **Verify:** `./venv/bin/python -m pytest tests/test_mtool_offline_fill.py -q`
    → 35 passed. Windows operator confirmed Validate/Generate. ✅

### Phase 1: Close the spike's open questions (Windows side, parallel track)

- [x] 🟩 **Step 1: Send the follow-up brief to the Windows agent** *(addendum
  written 2026-07-28; ANSWERS STILL OUTSTANDING — this is the long pole)* — append
  to `docs/MTOOL-ZIP-RECON-BRIEF.md` a short addendum asking, against the
  filing that just worked: (a) does the generated XBRL instance carry the
  full unscaled value or the on-sheet (thousands) figure — paste the fact
  element verbatim (recon Task 3.6); (b) did the negative test value survive
  to the instance with the right sign; (c) were derived/total cells correct
  without `--force-recalc`, and did the add-in behave with it on;
  (d) `inspect` output (sheet list + one sheet's column layout) for an MPERS
  and a Group template, to confirm sheet naming + the 4-column Group shape.
  - **Verify:** addendum committed ✅ — `docs/MTOOL-ZIP-RECON-BRIEF.md`
    "Addendum A", questions A.1–A.5 with an empty answer block. A.5 is new:
    whether column A carries the taxonomy concept on every real template (see
    *Discovered limitation* above). Answers received and recorded: **pending**.
    Until then `XBRL_MTOOL_FILL` stays off by default.

### Phase 2: The bridge — facts → fill instructions (Mac, pure Python)

- [x] 🟩 **Step 2: Exporter core** — new `mtool/exporter.py`:
  `build_fill_doc(db, run_id) -> dict` (the fill-JSON shape the tool already
  consumes). Reads `run_concept_facts` joined to `concept_nodes` scoped to
  the run's template family (`{standard}-{level}-` prefix, gotcha #21);
  filters to LEAF/MATRIX_CELL; dedups by `concept_uuid` (aliases never
  emitted); maps `(period, entity_scope)` → `column_role`
  (company: `current_year`/`prior_year`; group adds
  `group_current_year`/`group_prior_year`); emits one write per fact with
  the concept's label. Sheets it can't handle (SOCIE) are excluded and
  **counted in the doc's metadata** — no silent truncation.
  - [x] 🟩 *(implemented, Mac-tested — partial)* Emit a `meta` block: run id,
    standard/level, excluded sheets + fact counts. **Generation timestamp is
    NOT emitted** (finding 8) — `meta` carries `run_id`, `filing_standard`,
    `filing_level`, `denomination`, `scale`, `sheets_covered`, `counts`,
    `columns_unresolved` and nothing else. Excluded-sheet metadata *is* present,
    contrary to the review's second half.
    - [x] 🟩 *(2026-07-28)* `meta` now carries `generated_at` (UTC ISO-8601),
      `translation_version`, and `snapshot` (fact count + digest + newest
      `updated_at`) — so a doc says when it was built, under which rules, and
      from which revision of the run's data. Pinned by
      `tests/test_mtool_exporter.py::test_meta_is_self_describing` and
      `::test_snapshot_digest_changes_when_a_fact_changes`.
  - [x] 🟩 Unit tests with a hand-rolled DB fixture (pattern from
    `tests/test_canonical_export.py`: `import_template` +
    `import_company_targets`): CY/PY routing, group scopes, COMPUTED
    excluded, alias deduped, SOCIE excluded-but-counted.
  - **Verify:** `./venv/bin/python -m pytest tests/test_mtool_exporter.py -q`
    green; fixture doc validates against `offline_fill.validate_input`.

- [x] 🟩 **Step 3: End-to-end dry run on a real run's data** — small harness
  (test or script) that takes an existing completed run in the local DB,
  builds the fill doc, and runs `offline_fill` against our own
  `XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx` (whose labels are the
  same taxonomy vocabulary). Every label must resolve **exactly** —
  fuzzy hits or unresolved rows are exporter bugs (label drift) to fix here,
  where they're cheap.
  - **Verify:** report shows `fuzzy_matched: 0`, `unresolved: 0` for the
    sample run; values in the patched workbook match the run's Values tab.

- [x] 🟩 **Step 4: Strict mode in the fill tool** — `--strict` flag (and
  `strict: true` accepted in the input doc) so any fuzzy match is refused
  and reported (run `degraded`), not written. Default stays lenient for
  hand-authored operator runs; pipeline-generated docs always set it.
  - **Verify:** new tests — same typo'd input passes lenient, degrades
    strict; exporter output carries `strict: true`.

### Phase 3: Sign & scale translation (blocked on Step 1 answers)

- [x] 🟩 **Step 5: Pin OUR value conventions** *(2026-07-28)* — write down (in
  `mtool/exporter.py` docstring + tests) what unit and sign
  `run_concept_facts` values carry, derived from the extraction prompts +
  verifier (e.g. SOCIE dividends stored positive per ADR-002; SOCF signs per
  the 2026-07-03 regeneration). This is reading + pinning, no behaviour.
  - **Verify:** ✅ `tests/test_mtool_value_conventions.py` — the SOCIE/SoRE
    subtotal is asserted to SUBTRACT the dividends row (so positive storage is
    right, and this is the tripwire if a regenerated template ever adds it), a
    negative SOCF outflow is asserted to reach the doc unchanged, the identity
    manifest is asserted to flip no signs, and a "thousands" run is asserted to
    emit the thousands figure with the denomination REPORTED, not applied. The
    conventions themselves are written up in `mtool/exporter.py`'s docstring.

- [x] 🟩 **Step 6: Translation layer — unit-aware** *(implemented + Mac-tested
  2026-07-28; the shipped manifest is IDENTITY until Step 1 answers)*. The original design carried a single
  filing-wide `scale` multiplier. That is wrong the moment linear coverage
  reaches the numeric notes (sheets 13/14), which mix monetary amounts with
  share counts: a blanket thousands conversion would multiply "number of
  shares" by 1,000 and file it.
  - [x] 🟩 **Prerequisite — give facts a unit class.** `run_concept_facts` /
    `concept_nodes` carry no unit or data type today. Derive a `unit_class`
    (`monetary` · `shares` · `per_share` · `pure` — the last covering
    percentages and ratios) per concept, sourced from the SSM taxonomy under
    `SSMxT_2022v1.0/`, not guessed from the label.
    **Done:** `scripts/generate_concept_units.py` reads every XBRL element's
    declared item type out of the taxonomy schemas and joins it to the
    taxonomy's own English labels, emitting
    `concept_model/concept_units_{mfrs,mpers}.json`. `mtool/units.py` is the
    runtime lookup. Coverage measured at **1,232 of 1,257 MFRS-Company LEAF
    rows (98%)** and 902 of 903 for MPERS; the residue is sheet-title rows the
    parser types as LEAF plus ~9 genuine label drifts, all reported in the fill
    doc's `unit_class_unknown` rather than hidden. One label mapping to two
    NUMERIC classes is recorded `ambiguous` and READS AS UNKNOWN — never
    resolved by picking one. (A label shared with a text/domain concept is
    narrowed to the numeric reading: a text block can't be the source of a
    number, so that is the only interpretation, not a guess.)
  - [x] 🟩 Replace the global `scale` argument with an **evidence-backed
    translation manifest** keyed by `(template_id, concept, unit_class)`
    carrying scale and sign. Version the manifest; stamp its version into
    `meta` (see Step 2).
    **Done:** `mtool/translation.py`. `build_fill_doc` takes a
    `TranslationManifest`, not a float; the shipped `IDENTITY` changes nothing
    and its version rides in `meta` and on every receipt. The per-concept
    override slot exists and ships EMPTY — an override without evidence is the
    guess this layer was built to prevent. `thousands_manifest()` is a worked,
    tested non-identity example so unit-awareness is provable BEFORE the recon
    answer arrives; if the answer says mTool wants full units it becomes the
    shipped manifest, and if not it is deleted and nothing else moves.
  - [x] 🟩 Loud failure mode, strengthened: a concept with **no unit class** or
    no translation rule in a non-identity family → hard error
    (`UnknownUnitClass`, naming the row and sheet in plain words), never a
    guessed value and never a silent identity pass-through. Under the identity
    manifest an unknown unit is harmless by construction and passes through —
    but is still counted and listed, because those are exactly the rows a
    future manifest would refuse.
  - **Verify:** ✅ `tests/test_mtool_units.py` — each unit class is exercised
    independently, and the headline regression runs end to end on the real MFRS
    sheet 13 (`Notes-IssuedCapital`), which carries share counts and money
    amounts a few rows apart: under a thousands manifest RM 5,000 thousand
    becomes 5,000,000 while 10,000,000 shares stay 10,000,000. The old blanket
    multiplier would have filed ten billion shares.
    🟥 **Outstanding:** regenerating the Windows test filing's doc and matching
    it to the operator's hand-entered values — needs Step 1 + Step 7.

- [ ] 🟥 **Step 7: Second Windows acceptance run, machine-generated** — send
  the Windows box a fill doc generated by Steps 2–6 from a real extraction
  run (FINCO sample); operator patches a fresh mTool template, runs
  Validate/Generate, and spot-checks the instance values against the PDF.
  - **Verify:** operator reports Validate/Generate pass and values (incl. a
    negative and a scaled figure) correct in the generated XBRL. **This is
    the phase gate for the delivery UX.**

### Phase 4: Delivery — server-side fill in the app

- [x] 🟩 **Step 8: Decide the delivery shape** — **(a) server-side
  upload-template → download-filled**, recorded 2026-07-28.
  - **Rationale.** (a) was the team's stated wish and is the only cloud-capable
    option: the whole patch path is Excel-free, so it runs on the server the
    same way it runs on a laptop, and the operator never installs or updates a
    script. (b) — exporting the fill JSON and having the operator run the CLI —
    is strictly worse on every axis that matters here except one: it needs no
    upload. But it puts a hand-run tool between the reviewed data and the
    filing, which is precisely where the audit trail (Step 19) would vanish.
  - **What (b) still buys us, and is kept:** `GET …/mtool-fill` remains, so an
    operator on the Windows box can take the fill doc and run
    `offline_fill.py` by hand — the same one patcher, no fork. That is the
    fallback if the server route is ever unavailable, and it is how the Step-7
    acceptance run will be performed.
  - **Verify:** ✅ decision + rationale recorded here; both paths exercised by
    `tests/test_mtool_routes.py` (server) and
    `tests/test_mtool_coverage_dry_run.py` (CLI-shaped `fill_workbook` calls).

- [x] 🟩 **Step 8A: Exposure gate — flag the action off NOW** *(implemented +
  Mac-tested 2026-07-28; findings 1 + 4 + 5)*. Build this first; it makes the rest of
  the plan safe to work on incrementally instead of leaving a filing-capable
  action exposed while its correctness work is open.
  - [x] 🟩 `XBRL_MTOOL_FILL` (default **off**) gates the patch/download UI
    action and every route that can produce a workbook — `…/patch`,
    `…/artifact/{id}`, `…/detect-columns`, `…/notes-preview`. Off ⇒ 404, and the
    run page renders with no mTool button at all. The read-only
    `GET …/mtool-fill`, `…/mtool-fill/preflight` and `…/mtool-fill/receipts`
    stay available — they produce no filing artifact, and an audit trail you
    can't read once the feature is switched off isn't an audit trail.
  - [x] 🟩 **Preflight policy** (finding 4) — `mtool/preflight.py`, enforced
    server-side before any patch: block on open fact conflicts (`run_concept_facts` status
    `conflict`), unresolved reviewer flags, and notes-coverage errors. Blocking
    is the default; an operator override must be an explicit acknowledgement,
    recorded in the receipt (Step 19). Run status alone (`completed` vs
    `completed_with_errors`) is **not** a sufficient gate — and the exporter
    deliberately writes conflict facts, so the two decisions compound.
  - [x] 🟩 Flag-off identity: with the flag off the workbook-producing routes
    404 and the run page renders exactly as before — no disabled button, no
    hint that something is being withheld. The frontend defaults the flag to
    FALSE (the opposite posture to canonical mode) so a raced or failed
    `/api/config` fetch hides the action rather than offering it.
  - **Verify:** ✅ `tests/test_mtool_exposure_gate.py` (26 tests) — flag off ⇒
    404 on every writing route and 200 on the read-only ones; flag on + an open
    conflict ⇒ 409 naming the row in plain words; `completed_with_errors` alone
    ⇒ proceeds (finding 4's point); open reviewer flag / unresolved notes
    coverage / unavailable inventory ⇒ blocked; a written acknowledgement ⇒
    proceeds AND is recorded on the receipt; a blank one ⇒ still blocked.
    Web: `MtoolFillModal.test.tsx` — the action is absent when the flag is off,
    and the Fill button stays disabled until a reason is typed.

- [x] 🟩 **Step 9: Fill-doc endpoint** — `GET /api/runs/{run_id}/mtool-fill`
  (terminal runs only, 409 otherwise; auth middleware covers it
  automatically per gotcha #24). Returns the Step-2 doc as a download.
  Small "mTool" section on the run page (Overview tab; NOT a new
  `role="tab"` — gotcha #7) with the download button + excluded-sheet
  counts.
  - **Verify:** `tests/test_mtool_routes.py` (TestClient, AUTH_MODE=dev):
    200 + valid doc on a completed run, 409 on running, 401 unauthenticated
    (opt-out test). UI button renders and downloads in the web tests.

- [x] 🟩 **Step 10: Server-side patch endpoint** —
  `POST /api/runs/{run_id}/mtool-fill/patch`: multipart upload of the empty
  mTool template → server builds the doc, auto-builds the column map by
  reading the template's header rows (generalising `inspect`; falls back to
  asking the user when ambiguous), patches via the SAME `offline_fill`
  functions (strict mode), streams back the filled workbook + the JSON run
  report. Reject non-zip/oversize uploads; never persist the upload beyond
  the request (request-scoped temp dir + cleanup).
  - [x] 🟩 *(implemented, Mac-tested — NOT released; gated by Step 8A)*
    Column auto-detection unit tests against both observed layouts
    (ours A/B/C, real mTool D/E/F) + a Group 4-column fixture.
  - [x] 🟩 **Semantic column validation** *(implemented + Mac-tested
    2026-07-28; finding 3)*. A real mTool sheet labels its own structure and we
    now read those markers instead of guessing: `#PRIM#` designates the label
    column, `#ENDT#` gives each value column's period end date, `#UNITSCALE#`
    its declared unit, `#DOM#` says the columns are dimension MEMBERS rather
    than periods.
    - [x] 🟩 Current vs prior year now comes from comparing dates, so the answer
      survives a reordered template. Marker-less workbooks (our own generated
      ones) fall back to the positional guess but must declare
      `basis: "positional"`.
    - [x] 🟩 **`confidence` is no longer the gate — `needs_confirmation()` is.**
      A Group layout ALWAYS requires confirmation (mTool's Group column shape
      has never been observed, so there is nothing to corroborate against), as
      does any template whose fingerprint isn't in `mtool/known_templates.json`.
      The endpoint refuses unless BOTH are satisfied.
    - [x] 🟩 **The failure mode is real and is now caught.** In the one real
      mTool template we hold, `Notes-Issuedcapital` lays its value columns out
      as share CLASSES (Ordinary / Redeemable preference / …), all for the same
      period. Positional assignment would have written the current year into
      "Ordinary shares" and the prior year into "Redeemable preference shares"
      — a plausible, wrong filing that nothing downstream would catch. It is
      now recognised as dimensional and refused.
    - [x] 🟩 The synthetic Group test was replaced with an assertion about
      BEHAVIOUR rather than layout: a Group map is never auto-accepted, even
      for our own known templates. A real-mTool **Group** fixture is still
      absent (Step 18) — recon question A.4 asks for one.
    - [x] 🟩 Bonus, and directly relevant to finding 2: the sheet's declared
      `#UNITSCALE#` (observed: `MYR'000`) is read and compared against the
      run's denomination. A disagreement is surfaced as a warning in the fill
      report — never a silent conversion.
  - **Verify:** ✅ `tests/test_mtool_routes.py` (43 tests) +
    `tests/test_mtool_column_detect.py` (18) — our own template fills to a
    clean report and a workbook openpyxl can open; a degraded fill returns 200
    with `status: degraded`; a running run 409s; the real mTool template's
    periods resolve from its dates, its dimensional sheet is refused with an
    empty column map, and a Group shape is never mapped by position.

- [x] 🟩 **Step 11: Report UI** — *(criterion now MET via Step 11A, 2026-07-28;
  still NOT released — gated by Step 8A)* render the returned run
  report next to the download: written / fuzzy (should be none) / unresolved /
  skipped-formula counts with row detail, mirroring the CLI summary. The
  operator must see "clean" before taking the file to mTool.
  - **Verify:** web test renders a degraded report fixture; manual check in
    the running app with the FINCO run.

- [x] 🟩 **Step 11A: Split patch from download** *(implemented + Mac-tested
  2026-07-28; finding 5 — the fix for Step 11's unmet criterion)*. Today
  `MtoolFillModal.tsx` triggers the file download *first* and parses the report
  *after*, deliberately: the comment explains that a proxy-clipped header
  shouldn't turn a successful fill into a failure. That reasoning is sound and
  the resulting behaviour is still wrong — the operator has the file before
  they can see whether it's clean, and the report itself is capped at 20 detail
  rows / 6 KB in an HTTP header while the UI renders counts, not the promised
  row detail.
  - [x] 🟩 Two-step contract: `POST …/mtool-fill/patch` returns a **complete
    JSON report** (no cap) plus a short-lived artifact ID; a second
    `GET …/mtool-fill/artifact/{id}` streams the workbook. The
    `X-mTool-Report` header is gone entirely.
  - [x] 🟩 Download enabled only after a clean result, or after an explicit
    "I've read the problems above" acknowledgement — enforced on the SERVER
    (the artifact route 409s without it), not just in the UI, and stamped on
    the receipt together with when the file was actually taken.
  - [x] 🟩 Render full row detail, not counts — six collapsible groups
    (couldn't be placed / formula cell / failed read-back / matched more than
    one row / near-miss / errors), each listing every row.
  - [x] 🟩 Artifact lifetime bounded: 15 minutes, at most 32 live, swept on
    every access — including directories orphaned by a process restart, which
    the in-process map would otherwise forget about.
  - **Verify:** ✅ web tests — a degraded report leaves the download disabled
    until acknowledged, a clean one enables it, filling alone fetches no file,
    and individual problem rows render by name. Route tests — the artifact
    expires and 404s, is scoped to its run, and the unresolved list is returned
    in full (`len(unresolved) == counts.unresolved`, no `truncated` flag).

### Phase 5: Coverage — all linear sheets, all variants

- [x] 🟩 **Step 12: All MFRS-Company linear sheets** *(2026-07-28)*. No sheet
  map was needed after all — the exporter emits by the concept's own
  `render_sheet` and excludes only matrix shapes, so coverage was already
  structural. What was missing was the PROOF, and it now exists.
  - **Verify:** ✅ `tests/test_mtool_coverage_dry_run.py` seeds a fact on every
    LEAF of every linear sheet and fills our own template: **0 fuzzy,
    0 unresolved, 0 formula-cell writes** on all 13 MFRS-Company templates.
  - 🟥 **But see *Discovered limitation* above:** 56% of a full SOFP fill is
    refused as ambiguous because the sub-sheet repeats labels. Coverage of the
    sheet is complete; coverage of its ROWS is not, and can't be until rows
    have a stable key.
- [x] 🟩 **Step 13: Group + MPERS** *(Mac side done 2026-07-28)* — group column
  roles end-to-end and MPERS including SoRE (notes shifted 11–15 per
  gotcha #15).
  - **Verify:** ✅ the same sweep runs across MFRS×{Company,Group} and
    MPERS×{Company,Group} — 58 parametrised cases, all 0 fuzzy / 0 unresolved.
    A Group fill is asserted to write all four value columns, and MPERS's
    MPERS-only SoRE is asserted to fill like any linear sheet.
  - 🟥 **Outstanding:** the Windows acceptance run on a Group filing, and the
    Step-1(d)/A.4 answer about how a real Group template marks its columns —
    until it lands, a Group layout always asks the operator to confirm.
- [ ] 🟥 **Step 14: SOCIE (own step, may split into its own plan)** — matrix
  cells need `(row-label × column)` targeting; mTool's SOCIE shape is
  unknown until inspected. Scope only after Step 13 ships.
  - **Verify:** defined when scoped; until then the exporter keeps counting
    SOCIE facts as excluded in the meta block.

### Phase 6: Hardening

- [x] 🟩 **Step 15: Failure-mode sweep** *(2026-07-28)* — corrupted /
  wrong-variant template upload, a valid zip that isn't a workbook, an empty
  upload, a template whose sheets don't match the run, a run with no facts,
  duplicate labels, an absurd figure, a negative figure, four concurrent fills,
  and a missing run.
  - **Verify:** ✅ `tests/test_mtool_failure_modes.py` — a test per mode; every
    one returns a structured 404/409/413/422 or a report that names the
    problem, and none reaches a 500. Concurrency: four simultaneous fills of
    the same run produce four distinct artifacts and four receipts, with no
    temp-dir collision. Also re-asserts the rollback invariant that
    `offline_fill.py` imports nothing third-party or repo-local.

- [x] 🟩 **Step 16: CLAUDE.md + docs** — add the mTool pipeline gotcha
  (mechanism, strict-mode rule, "one patcher, no fork" invariant, pointer to
  this plan); update the spike plan's status header; refresh memory.
  - **Verify:** docs reference real file paths; pinning tests named.
  - [x] 🟩 *Re-closed 2026-07-28:* `.claude/rules/mtool-fill.md` now leads with
    the exposure rule, and carries the preflight policy, the unit-class rule,
    the column-corroboration rule, the patch/download split, the receipt, and
    the SOFP label-ambiguity ceiling. The CLAUDE.md stub is retitled
    "gated OFF" so the posture is visible without opening the rules file, and
    the schema-version line reads 35.

- [~] 🟨 **Step 18: Real-mTool fixture corpus** *(machinery + our own corpus
  done 2026-07-28; real SSM templates still one file — finding 7)*.
  - [x] 🟩 **Fingerprint + provenance registry.**
    `scripts/generate_mtool_fingerprints.py` walks the corpus and writes
    `mtool/known_templates.json`: 32 distinct layouts across 59 files, each
    recording where the file came from (`generated` = ours, `ssm-mtool` = a
    real SSM workbook) and who vouched for it. The fingerprint is layout-only
    — it deliberately ignores content, because an mTool export stamps a fresh
    GUID into row 1 of every sheet and a content-sensitive hash would make
    every export a stranger. A template not in the registry is treated as
    unknown and needs operator confirmation (Step 10).
  - [x] 🟩 **Coverage the corpus CAN prove today.** All 58 linear
    sheet × standard × level combinations fill green
    (`tests/test_mtool_coverage_dry_run.py`), duplicate labels on one sheet are
    exercised (`tests/test_mtool_failure_modes.py`), and both non-monetary unit
    classes are exercised against a REAL sheet — MFRS 13 carries share counts
    and money amounts a few rows apart (`tests/test_mtool_units.py`).
  - [x] 🟩 **The real template we do have is used properly.**
    `data/MBRS_test.xlsx` now drives the semantic-detection tests: its marker
    rows, its date-based period columns, its declared `MYR'000` unit, and its
    dimensional share-class sheet.
  - [ ] 🟥 **Still genuinely thin, and can't be fixed from here:** we hold ONE
    real SSM-issued mTool workbook (MFRS, Company). MPERS and Group real
    templates are absent, so the semantic detector has never been run against
    a real Group layout — which is exactly why it refuses to auto-map one.
    Recon question A.4 asks the Windows operator for both.
  - **Verify:** ✅ the dry-run harness is green across our whole corpus and each
    finding-2 unit class has a real-sheet test. 🟥 Each finding-3 LAYOUT does
    not yet have a real fixture — Company does, Group does not.

- [x] 🟩 **Step 19: Fill receipt** *(implemented + Mac-tested 2026-07-28;
  finding 6)*.
  A filing artifact leaves no durable record today, and numeric facts and notes
  are read at different moments while a completed run remains editable — so two
  fills of "the same" run can differ with nothing to show it.
  - [x] 🟩 Read all facts for one fill from a single consistent snapshot
    (`mtool/receipt.snapshot_facts`, one transaction), so the workbook
    corresponds to one source revision — and that revision has an IDENTITY
    (fact count + a digest over the ordered fact tuples + the newest
    `updated_at`), so two fills of the same still-editable run are
    distinguishable even when the counts match.
  - [x] 🟩 One additive table — schema **v35** `mtool_fill_receipts` (nullable
    columns, no CHECK on `status`) recording exactly that list, plus when the
    workbook was actually downloaded and any degraded-fill acknowledgement
    given at that moment. Exposed read-only at
    `GET /api/runs/{id}/mtool-fill/receipts`.
  - [x] 🟩 The patcher stays stateless and stdlib-only; the receipt is written
    by the route. `mtool/receipt.py` also swallows its own write errors — losing
    the audit trail is bad, failing an otherwise-good fill because the audit
    write hiccuped is worse.
  - **Verify:** ✅ `tests/test_db_schema_v35.py` (fresh init, v34→v35
    walk-forward, no CHECK on status, cascade with the run) and
    `tests/test_mtool_artifact_and_receipt.py` (exactly one receipt per fill;
    re-filing after an edit yields two receipts with distinct output hashes AND
    distinct snapshot digests; the download stamp and acknowledgement land).

## Release Order — status 2026-07-28

Items 1 and 3–6 are DONE. What remains needs the Windows box.

1. ~~**Step 8A** — exposure gate + preflight policy.~~ ✅ done first, exactly as
   planned, which is what made the rest safe to build.
2. **Step 1** — Windows recon answers. **STILL THE LONG POLE.** Addendum A is
   written; nothing else can start until an operator answers it. Everything
   downstream was built to absorb the answer as a config change rather than a
   redesign: the translation manifest is versioned and ships identity, so
   "mTool wants full units" becomes `manifest = thousands_manifest()`.
3. ~~**Steps 5, 6** — unit-aware translation.~~ ✅ done. Finding 2's prerequisite
   turned out to be tractable: the unit class was already in the SSM taxonomy,
   it just had to be extracted and joined by label (98% coverage), rather than
   requiring a new column on `run_concept_facts`.
4. ~~**Step 10's semantic column validation** + **Step 18** fixture corpus.~~
   ✅ done, except that the corpus still holds only one real SSM workbook — so
   Group layouts are refused rather than validated.
5. ~~**Step 11A** — split patch from download.~~ ✅ done.
6. ~~**Step 19** — receipt.~~ ✅ done.
7. **Step 7** — machine-generated Windows acceptance run. **Only now** may
   `XBRL_MTOOL_FILL` default on. Blocked on 2.

**Not in scope, decide separately:** the SOFP label-ambiguity ceiling
(*Discovered limitation*, above). It is a bigger constraint on how useful this
feature is than anything remaining on this list, and its fix touches the
concept-model parser rather than the mTool package.

## Rollback Plan

- The feature is **purely additive**: no DB migration, no existing-module
  edits beyond registering routes. Rollback = revert the feature commits (or
  simply don't expose the endpoints/UI section); extraction, review, and
  export paths are untouched.
  **REVISED 2026-07-28:** Step 19 landed, so there is now one additive table
  (v35 `mtool_fill_receipts`). It follows gotcha #11 rules and sits inert after
  a rollback (the `doc_conversions` precedent). The fastest rollback at any
  point remains leaving `XBRL_MTOOL_FILL` unset — which is also the shipped
  default, so "rollback" is the current state.
- The fill tool file must stay importable-standalone — if a server-side
  change breaks its stdlib-only property, that's a regression (keep a test
  that `mtool/offline_fill.py` imports with no third-party deps).
- State to check after rollback: none — endpoints are stateless over
  existing tables; uploaded templates are request-scoped temp files.
