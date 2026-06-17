# PLAN — Notes Template Registry (full-template notes review + XBRL-ID anchor)

**Status:** proposed
**Author:** planning session, 2026-06-17
**Scope:** make the Notes Review tab show the *full* notes template (every fillable
row, in M-tool order, blanks included and editable), built on persistent
per-row identity that mirrors the face-statement `concept_model` pattern and
anchors downstream full-XBRL-filing generation.

> "Why + how" plan, not an API contract (repo convention for `docs/PLAN-*.md`).
> Load-bearing invariants are called out against their CLAUDE.md gotcha numbers.

---

## 1. Problem

The **Notes Review** tab ([web/src/components/NotesReviewTab.tsx](../web/src/components/NotesReviewTab.tsx))
renders **only the rows the agent filled**. `GET /api/runs/{id}/notes_cells`
([api/notes.py](../api/notes.py)) returns `notes_cells` verbatim — a sparse,
run-local store. A reviewer can't see *where* a filled note sits in the complete
M-tool template, so mapping extracted content back to the right Excel row when
copying into the SSM M-tool is slow and error-prone.

The face statements already solve this: the **Values** tab
([web/src/pages/ConceptsPage.tsx](../web/src/pages/ConceptsPage.tsx)) projects
the full `concept_nodes` registry and overlays facts, so every template row
appears — blanks included — in order.

## 2. Two shapes of notes (this is the spine of the design)

The five notes templates are **not** uniform. Research confirmed two genuinely
different shapes:

| Notes | Sheet | Shape | Today's storage |
|---|---|---|---|
| Corporate Info (10) | `Notes-CI` | prose HTML, col B only | `notes_cells.html` |
| Accounting Policies (11) | `Notes-SummaryofAccPol` | prose HTML, col B only | `notes_cells.html` |
| List of Notes (12) | `Notes-Listofnotes` | prose HTML, col B only | `notes_cells.html` |
| **Issued Capital (13)** | `Notes-Issuedcapital` | **numeric, multi-column** (B=CY,C=PY company; B/C/D/E group) | **xlsx only — NOT in DB** |
| **Related Party (14)** | `Notes-RelatedPartytran` | **numeric, multi-column** | **xlsx only — NOT in DB** |

(MPERS shifts these to slots 11–15, gotcha #15.)

Two findings that reshaped the plan:

1. **Numeric notes (13/14) are structurally identical to face statements** —
   `A=label, B/C(/D/E)=values, evidence col`. They are written **directly to the
   xlsx** by `notes/writer.py::_write_row` and **explicitly excluded from
   `notes_cells`** (the writer skips any row with `numeric_values`). So they are
   *already invisible* in the Notes Review tab today, and the one-HTML-per-row
   model cannot represent their multiple numeric columns.

2. **An HTML table *inside* a prose cell is already handled** (sanitiser
   whitelist + TipTap table extensions + clipboard decoration, gotcha #16). That
   case needs nothing new.

**Therefore the design is two-track:**
- **Prose notes (10/11/12):** new parallel `notes_nodes` registry + `notes_cells`
  (HTML) — registry gives the full-template projection + blanks.
- **Numeric notes (13/14):** **reuse the existing `concept_model` pipeline** —
  they *are* numeric multi-column tables, exactly what `concept_targets` /
  `run_concept_facts` / `cell_resolver` / exporter already do. This gets
  full-template projection, multi-column editing, export, and the XBRL-ID anchor
  by reuse instead of reinvention.

Both tracks surface in **one** Notes Review tab (user decision): prose rows as
TipTap editors, numeric rows as value-input cells (the `ConceptsPage` pattern).

## 3. Goal

1. Notes Review shows the **full template** for every notes sheet in the run, in
   M-tool order, blanks included — prose **and** numeric.
2. **Blank rows are editable in place** (prose → upsert `notes_cells`; numeric →
   write/edit `run_concept_facts` via the existing fact-edit API).
3. **Fillable rows only** — abstract/section-header rows excluded; row order
   still matches the M-tool for shown rows.
4. **Forward-proof for XBRL generation** — every notes row has a stable
   template-scoped identity with a reserved slot for the real SSM XBRL concept ID.

### Non-goals
- Emitting the full XBRL instance document (separate future work; this lays the
  identity anchor only).
- Populating the *real* SSM element id now (column reserved; see §8).
- Numeric cascade/cross-checks for prose notes (prose stays HTML, face-only
  machinery).

## 4. Current state (from research)

Key references:
- UUID minting: [concept_model/parser.py](../concept_model/parser.py) `_mint_uuid` (template-scoped), `mint_notes_concept_uuid` (sheet/row/label — collides across families), `_CONCEPT_NS`.
- Importer/targets: [concept_model/importer.py](../concept_model/importer.py) `import_template`, `import_company_targets`, `import_group_targets`.
- Bootstrap (face only): [concept_model/bootstrap.py](../concept_model/bootstrap.py) `import_all_face_templates`; called in `server._lifespan` under `_CANONICAL_BOOTSTRAP_OK` fail-fast (gotcha #21).
- Notes file registry: [notes_types.py](../notes_types.py) `NOTES_REGISTRY`, `_NOTES_FILENAMES_BY_STANDARD`, `is_numeric` flag.
- Numeric write path: [notes/writer.py](../notes/writer.py) `_write_row` (numeric → cols B/C/D/E), and the `if not combined.numeric_values:` guard that **omits numeric rows from `cells_written`**.
- Prose persistence: [notes/persistence.py](../notes/persistence.py) `persist_notes_cells`, `overlay_notes_cells_into_workbook` (col B only).
- Cell store: [db/schema.py](../db/schema.py) `notes_cells` (nullable `concept_uuid`, v6); repo `upsert_notes_cell` / `list_notes_cells_for_run` / `delete_notes_cells_for_run_sheet`.
- Facts ingest pattern (xlsx → facts via `cell_resolver.resolve_cell`): [eval/ingest.py](../eval/ingest.py) (gotcha #23) — reusable for numeric-notes capture.
- Endpoint + PATCH (update-only, 404 on missing): [api/notes.py](../api/notes.py).
- Template reader (`is_abstract`): [tools/template_reader.py](../tools/template_reader.py).

## 5. Design — Track A: prose notes (10/11/12)

### 5.1 Parallel `notes_nodes` registry (not extending `concept_nodes`)
`concept_nodes` feeds the numeric pipeline (cascade, `run_concept_facts`,
cross-checks, exporter, eval). **Prose** notes are HTML text-blocks with none of
that; dropping them into `concept_nodes` would force every numeric consumer to
filter a "prose kind" (broad, invariant-guarded edits) and they still wouldn't
surface in Values (its scoping joins through `run_concept_facts`, which prose
never writes). A parallel registry mirrors the *architecture* — registry +
project + overlay — keeping the two pipelines separate.

### 5.2 Identity (the crux)
`notes_nodes` mints **template-scoped** ids so the same prose row in MFRS-Company
vs MFRS-Group (identical sheet name in `NOTES_REGISTRY`) doesn't collide:
```
node_uuid = uuid5(_CONCEPT_NS, f"{template_id}::{sheet}::{row}::{label}")
```
This diverges from legacy `notes_cells.concept_uuid` (sheet/row/label only). We
reconcile by **joining on `(sheet, row)` within run scope, not on uuid**: a run
has one `(standard, level)` → known prose `template_id`s → known nodes; within
that set `(sheet,row)` is unique and matches `notes_cells`' `UNIQUE(run_id,
sheet,row)`. Display never depends on a uuid migration. For XBRL linkage we
additionally stamp the template-scoped `node_uuid` onto `notes_cells.concept_uuid`
on new writes (the writer/PATCH can resolve `template_id` from run standard+level
+ sheet); legacy rows still display and can be backfilled in v19.

### 5.3 `xbrl_concept_id` (reserved, nullable)
`notes_nodes` carries nullable `xbrl_concept_id TEXT` — the real SSM element id,
distinct from the geometric `node_uuid`. Sourcing is deferred (§8); the column
exists now so no later migration is needed.

### 5.4 Fillable rows
`read_template` yields ordered col-A fields with `is_abstract`. Fillable =
`col==1 and value and not is_abstract`. Store abstract rows too (kind=`ABSTRACT`)
for a faithful registry; the endpoint returns only non-abstract.

## 6. Design — Track B: numeric notes (13/14) via `concept_model`

Numeric notes are face-shaped, so they ride the **existing** machinery:

- **Registry:** parse sheets 13/14 with `parse_template` (they're linear numeric;
  no special-casing needed) into the **existing `concept_nodes`** table under
  notes `template_id`s (e.g. `mfrs-company-notes-issuedcapital-v1`). `concept_uuid`
  is already template-scoped via `_mint_uuid` → no collisions, lives alongside
  face concepts. Run `import_company_targets`/`import_group_targets` so CY/PY/
  Group/Company column routing is precomputed (gotcha #21 single-lookup routing).
- **Bootstrap:** extend `import_all_face_templates` (or add a sibling called in
  the same `_lifespan` block, same fail-fast guard) to also import the numeric
  notes templates across `{mfrs,mpers}×{company,group}`.
- **Capture (extraction) — LIVE write (decided):** the numeric notes agent
  currently writes B/C/D/E to the xlsx and skips the DB. Thread `run_id`/`db_path`
  into the notes numeric write path so it projects into `run_concept_facts` as the
  agent writes — mirroring face extraction Phase B (gotcha #21). Resolve each
  written `(sheet,row,col)` → `concept_uuid` via `cell_resolver.resolve_cell`
  before upserting the fact, so identity matches the imported `concept_nodes`.
  (The agent xlsx write can remain as today; the live projection is added
  alongside it.) Any shared-workbook save still routes through
  `utils/workbook_io.atomic_save_workbook` (gotcha #22).
- **Edit:** numeric-note cells are edited through the **existing fact-edit API**
  (the same one the Values tab uses) — no new write path.
- **Export:** with facts in `run_concept_facts` and notes templates imported, the
  canonical exporter re-renders sheets 13/14 from facts like any statement
  (export keeps live formulas — [[export_keeps_live_formulas]]). Confirm the
  merger keeps the notes sheets.

### 6.1 Interactions to handle (numeric track)
- **Values tab duplication:** because numeric notes now live in `concept_nodes` +
  `run_concept_facts`, the `/concepts` projection (scoped by templates the run
  touched) would surface them in the **Values** tab too. Decide: filter notes
  `template_id`s out of the Values projection (keep notes in the Notes tab only),
  or allow them in both. (Lean: filter out of Values to avoid a confusing split.)
- **Cross-check scoping** (gotcha #25 `_build_check_template_ids`) must keep
  excluding notes template_ids unless a check explicitly targets a note — adding
  notes to `concept_nodes` must not silently widen existing checks.
- **Eval grader** (gotcha #23) grades `run_concept_facts` LEAF/MATRIX scoped by
  the benchmark's template set — numeric notes become gradeable *only if* a
  benchmark includes their template_ids. Additive, no change needed, but note it.
- **Evidence column** (D company / F group) — confirm how/whether per-fact
  provenance is stored; numeric notes carry evidence today in the xlsx.

## 7. Phased implementation

Each phase lands with its pinning test ("done = pinning test passes").

### Phase 1 — schema v19 (Track A registry)
- Add `notes_nodes` (prose registry):
  ```sql
  CREATE TABLE IF NOT EXISTS notes_nodes (
      node_uuid       TEXT PRIMARY KEY,
      template_id     TEXT NOT NULL,
      sheet           TEXT NOT NULL,
      row             INTEGER NOT NULL,
      label           TEXT NOT NULL,
      kind            TEXT NOT NULL,          -- 'ABSTRACT' | 'LEAF'
      xbrl_concept_id TEXT,                   -- reserved; NULL until §8
      UNIQUE(template_id, sheet, row)
  );
  ```
  (Numeric notes need **no** new table — they use `concept_nodes`.)
- Bump `CURRENT_SCHEMA_VERSION` 18→19; `_V19` walk-forward (no NOT-NULL without
  default, gotcha #11). Optional `notes_cells.concept_uuid` backfill.
- **Pinning:** `tests/test_db_schema_v19.py`.

### Phase 2 — registries + bootstrap
- **Track A:** `concept_model/notes_parser.py::parse_notes_template` (prose →
  ABSTRACT/LEAF, template-scoped uuid) + `notes_importer.py::import_notes_template`
  + `import_all_notes_templates`.
- **Track B:** extend bootstrap to import numeric notes (13/14) via the existing
  `parse_template` + `import_template` + `import_*_targets`.
- Wire both into `server._lifespan` after `import_all_face_templates`, same
  fail-fast guard (gotcha #21). MPERS slot numbering (gotcha #15).
- **Pinning:** `tests/test_notes_registry_import.py` — prose nodes in
  `notes_nodes` (template-scoped, idempotent); numeric notes in `concept_nodes`
  with targets; counts/order per `(standard, level)`.

### Phase 3 — endpoint projection (merge both tracks)
- Rework the notes endpoint to return, per sheet in M-tool order:
  - prose sheets: `notes_nodes` (LEAF) LEFT-JOIN `notes_cells` on `(sheet,row)`;
  - numeric sheets: `concept_nodes` (LEAF) for the run's notes template_ids
    LEFT-JOIN `run_concept_facts`, shaped with the value columns the level uses.
  - each row tagged `kind: "prose" | "numeric"` so the frontend picks an editor;
    include `node_uuid`/`concept_uuid` + `xbrl_concept_id`.
- **Sheet inclusion:** only project sheets the run actually targeted (open Q §9).
- **Pinning:** endpoint test — blank prose + blank numeric rows appear in order;
  filled rows carry html / values.

### Phase 4 — capture + editable blanks
- **Prose:** make `PATCH /notes_cells/{sheet}/{row}` an **upsert** — insert when
  absent using the `notes_nodes` label + template-scoped `node_uuid`; evidence
  stays null/read-only (gotcha #16); reject non-registry `(sheet,row)` (400);
  keep html-only body + 30k cap (413). Regenerate clobber + `edited_count`
  unchanged (gotcha #16).
- **Numeric:** implement the **live** capture path (§6) — notes numeric write
  threads `run_id`/`db_path` and projects into `run_concept_facts`; edits go
  through the existing fact-edit API.
- **Pinning:** prose upsert inserts with registry label + uuid; numeric capture
  populates `run_concept_facts` for sheets 13/14; fact-edit round-trips.

### Phase 5 — frontend: one tab, two editors
- `lib/notesCells.ts`: extend types with `kind`, `node_uuid`, optional
  `xbrl_concept_id`; treat empty html/null value as blank.
- `NotesReviewTab`/`CellRow`: render every row; **prose** rows → TipTap (blank =
  empty editor, editable, debounced upsert-PATCH); **numeric** rows → value-input
  cells reusing the `ConceptsPage` `EditableValueCell` pattern, saving via the
  fact-edit API. Evidence read-only, shown when present. Inline styles + theme
  tokens only (gotcha #7).
- If §6.1 chooses to hide notes from Values, filter there.
- **Pinning:** update `web/src/__tests__/NotesReviewTab.test.tsx` — mock includes
  blank prose + numeric rows; assert ordering, both editor types render, edits
  fire the right endpoint. (Existing `rows.length===3` assertion changes
  deliberately.)

### Phase 6 — (future, out of scope) real XBRL concept-ID + filing hook
See §8; reserved column makes it additive.

## 8. Sourcing the real XBRL concept ID (follow-up)
Deterministic path: have the template **generators** persist the SSM concept id
per row at generation time (they already extract it — `generate_mpers_templates.py
::_concept_id_from_href`) into a hidden column / comment, then importers read it
into `notes_nodes.xbrl_concept_id` (Track A) and a new `concept_nodes.xbrl_concept_id`
(Track B + face). Done under the regenerate-with-`--snapshot` rule (gotcha #3).
Out of scope here; reserved columns mean no further migration.

## 9. Decisions (resolved 2026-06-17)
1. **Sheet inclusion (Phase 3):** project only the notes sheets the run actually
   targeted — not every sheet of the family.
2. **Numeric capture (Phase 4):** **LIVE write** into `run_concept_facts` (thread
   `run_id`/`db_path` into the notes numeric write path, like face extraction
   Phase B) — *not* ingest-at-merge.
3. **Values-tab duplication (§6.1):** filter numeric notes' template_ids out of
   `/concepts` so notes stay in the Notes tab only.
4. **`xbrl_concept_id` on `concept_nodes`:** defer to §8 (the actual XBRL-gen
   work). Only `notes_nodes` gets the reserved column now, since that table is new.
5. **Legacy `notes_cells.concept_uuid` backfill:** only stamp new writes; leave
   legacy rows (display works via the `(sheet,row)` join regardless).
6. **Numeric-note evidence (verify, not a choice):** confirm during Phase 4 that
   per-fact provenance is stored so the D/F evidence column survives the
   round-trip through `run_concept_facts`.

## 10. Invariants touched (CLAUDE.md gotchas)
- **#11** schema v19 walk-forward (no NOT-NULL-without-default).
- **#14** notes pipeline (numeric col routing B/C/D/E; prose col B).
- **#15** MPERS slot numbering (notes 11–15).
- **#16** notes_cells canonical, evidence read-only, regenerate clobber + edited_count.
- **#21** canonical bootstrap fail-fast; numeric notes join `concept_nodes`/targets/single-lookup routing.
- **#23** reuse `cell_resolver`/eval-ingest for numeric capture; eval grader may now grade notes.
- **#25** cross-check scoping must not silently widen to notes template_ids.
- **#7** frontend inline styles + theme tokens.
- **#3** templates regenerated, never hand-edited (relevant to §8).
- **#22** any new workbook writer uses `utils/workbook_io.atomic_save_workbook` (imports are read-only — likely N/A).

## 11. Rollout / sequencing
Phases 1→5 ordered (each depends on prior). 1–2 are backend-only and inert until
the endpoint reads the registries. Phase 3 changes the response shape — land 3+5
together or tolerate both shapes during the gap. No new runtime flag; behavior is
strictly additive to the review UI.
