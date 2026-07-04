# Notes Coverage Checklist & Top-Line Routing — PRD

> Status: SHIPPED 2026-07-04 — all five decisions locked (Decision 5 acked)
> and implemented across all 8 plan phases (see the plan's DONE banner +
> CLAUDE.md gotcha #27).
> Implementation plan: docs/PLAN-notes-coverage-and-routing.md.
> Companion brainstorm: 2026-07-04 session. Builds on the existing notes
> reviewer (docs/PLAN.md), scout inventory (gotcha #13), and detectors
> (`notes/detectors.py`).

## Overview

- **Problem:** Two recurring quality failures in notes extraction:
  1. **Missed notes.** Notes that exist in the PDF never land in any template
     field — and today nobody can *see* that it happened. The pipeline has a
     notes list (scout's inventory) and gap detectors, but (a) the list itself
     is never validated, (b) an empty/failed list silently reports "no gaps",
     and (c) there is no human-visible checklist showing which notes were
     accounted for and where.
  2. **Over-eager splitting.** Agents fragment a single top-line note across
     multiple template fields (e.g. a right-of-use paragraph inside the PP&E
     note gets split out into the "Leases" field). Reviewer feedback: content
     under one top-line note belongs in that note's field, whole — with one
     precise exception for explicitly-labelled material/significant
     accounting policies.

- **Solution:** (1) A holistic, human-visible **coverage checklist** — every
  note the PDF contains, ticked off against every notes sheet, validated by
  the notes reviewer agent and shown in the run UI. (2) A **top-line routing
  rule** in the notes prompts + a reviewer check, so notes stay whole and only
  explicitly-labelled accounting-policy sections are carved out to the
  Accounting Policies sheet.

- **Target User:** PwC preparers/reviewers using the web UI to review a run's
  notes output before pasting into M-Tool; secondarily the notes reviewer
  agent itself, which consumes the same checklist.

- **Success Criteria:**
  1. For any completed run with notes, the UI shows the FINAL (post-reviewer)
     checklist of ALL top-level notes from the PDF with a per-note status
     (placed / missing / skipped-with-reason / suspected gap) and
     click-through to where each landed. Zero notes are "silently"
     unaccounted for — every inventory note has an explicit status a human
     can read.
  2. An empty or failed notes inventory on a notes-targeting run surfaces as
     a visible warning — never as a green "no gaps" result.
  3. A run that ends with unresolved `missing` checklist rows lands as
     `completed_with_errors`, never plain `completed`.
  4. On regression PDFs, top-line notes land in exactly one field (except
     labelled policy carve-outs), measured by the split-detector reporting
     zero unresolved split findings after the reviewer pass.

## The Two Rules (business logic, confirmed with examples)

### Rule 1 — Top-line placement (default)

Every piece of prose under a top-level note goes to **that note's field,
whole** — regardless of what topics its sub-sections mention or how they are
titled. A "right-of-use assets" paragraph inside Note 12 (PP&E) stays in the
PP&E field; it does NOT move to the "Leases" field. A section titled
"Policy on investment properties" inside Note 9 stays in Note 9's field.

### Rule 2 — Accounting-policy carve-out (the only exception)

A section **explicitly labelled** "material accounting policy/policies" or
"significant accounting policy/policies" routes to the **Accounting Policies
sheet** (MFRS sheet 11 / MPERS sheet 12), in the matching per-topic policy
field. This applies in both directions:

- **Direction 1 (embedded policy):** a sub-section inside a topical note that
  carries the explicit label (e.g. "Material accounting policy — Investment
  properties" inside Note 9) is carved out to the policies sheet. The rest of
  the note stays whole in its own field.
- **Direction 2 (policies top-line):** a top-line "Material/Significant
  Accounting Policies" note fans out per topic — each sub-policy lands in its
  matching per-topic field on the policies sheet. This is the one case where
  a top-line note legitimately spans multiple fields.

**Not a trigger:** "Policy on X", "Basis of measurement", or policy-sounding
prose without the explicit material/significant label. Those stay with their
top-line note.

Trigger phrases (closed set, case-insensitive, singular/plural):
`material accounting policy|policies`, `significant accounting
policy|policies` (covers post-MFRS-2023 "material" wording and older/MPERS
"significant" wording).

### Worked example (confirmed 2026-07-04)

```
Note 3 — Significant Accounting Policies      → Sheet 11, per-topic fields
  3.2 Investment properties                   → Sheet 11 "IP policy" field
  3.5 Financial instruments                   → Sheet 11 "FI policy" field

Note 9 — Investment Properties                → Sheet 12 "Investment properties"
  (a) fair-value movement table               → stays (disclosure)
  (b) "Policy on investment properties: ..."  → STAYS (no explicit label)
  (c) "Material accounting policy —
       Investment properties: ..."            → CARVED OUT to Sheet 11
  (d) rental income from operating leases     → STAYS (mentions leases, but
                                                 it's Note 9 disclosure)
```

## The Checklist (what the human sees)

One row per top-level note in the PDF, reconciled **holistically across all
notes sheets** (MFRS 10–14 / MPERS 11–15) — a note absent from Sheet 12 may
legitimately live on Sheet 11 (a policies note), Sheet 10, or the numeric
sheets 13/14.

```
NOTES COVERAGE CHECKLIST                                run #123
─────────────────────────────────────────────────────────────────────
 #   Note title (from PDF)          Status      Where it landed
─────────────────────────────────────────────────────────────────────
 1   Corporate information          ✓ placed    Sheet 10 · row 6
 2   Basis of preparation           ✓ placed    Sheet 11 · row 8
 3   Significant accounting
     policies                       ✓ placed    Sheet 11 · rows 10,14,22
                                                (fan-out: policy note)
 4   Property, plant & equipment    ✓ placed    Sheet 12 · row 31
                                                + Sheet 11 · row 14
                                                (policy carve-out)
 5   Investment properties          ✗ MISSING   — nowhere on any sheet
 6   Trade receivables              ✓ placed    Sheet 12 · row 48
 7   Share capital                  ✓ placed    Sheet 13 · rows 12-15
 ⋮
 13  (not in inventory)             ⚠ SUSPECTED PDF numbering jumps
                                      GAP        12 → 14; scout may have
                                                 missed Note 13
 ⋮
 18  Contingent liabilities         ⚠ skipped   agent reason: "no
                                                 matching template row"
─────────────────────────────────────────────────────────────────────
 Inventory: 24 notes (scout) · 1 suspected gap · 22 placed ·
 1 missing · 1 skipped                    [Re-run reviewer] [View PDF]
```

Status vocabulary:

| Status | Meaning | Source |
|---|---|---|
| `placed` | content citing this note exists on ≥1 notes sheet | provenance reconciliation |
| `missing` | in inventory, zero content anywhere | `inventory_coverage_gaps` |
| `skipped` | an agent explicitly skipped it with a reason | Sheet-12 coverage receipts (extended) |
| `suspected gap` | note number absent from the inventory sequence itself | new contiguity check |
| `inventory unavailable` | banner state: empty/failed inventory — the whole checklist is untrustworthy | new loudness rule |

## User Stories

1. **MUST HAVE** — As a reviewer, I want to see a checklist of every note in
   the PDF with where each landed, so I can verify nothing was dropped
   without re-reading the whole PDF myself.
2. **MUST HAVE** — As a reviewer, I want notes that are missing (or suspected
   missing from the list itself) to be automatically investigated and
   resolved by the notes reviewer agent — and any it cannot resolve to be
   visually loud and to mark the run `completed_with_errors` — so gaps get
   fixed or explained, not discovered in M-Tool later.
3. **MUST HAVE** — As a preparer, I want each top-line note's content kept
   whole in one field (with only labelled policy carve-outs moved), so the
   filled template mirrors how the financial statements are actually
   organised.
4. **NICE TO HAVE** — As a reviewer, I want to manually mark a checklist row
   as "verified OK" (e.g. a legitimately skipped note), so the checklist can
   reach a fully-green state I can sign off on.
5. **NICE TO HAVE** — As an operator, I want the notes agents (not just
   Sheet-12 sub-agents) to file coverage receipts, so skips on sheets
   10/11/13/14 carry reasons too.

## Detailed User Flows

### Flow A — Coverage checklist (story 1 + 2)

- **Trigger:** a run that targets any notes template completes extraction
  (checklist computed at the `reviewing_notes` stage; recomputed after the
  notes reviewer finishes and on any manual re-review).
- **Sequencing (decision 2026-07-04):** the checklist the human sees is the
  **post-reviewer** state. The draft (pre-reviewer) checklist is an internal
  input to the reviewer, not a UI surface — the human only gets the list
  after the reviewer has attempted to resolve every non-placed row. The one
  exception: if the reviewer pass itself fails or times out, the UI shows
  the draft checklist under an explicit "not yet reviewed" banner rather
  than nothing.
- **Steps:**
  1. Scout inventory is persisted per run (exists today:
     `run_notes_inventory`).
  2. **NEW — inventory self-check:** the system scans the inventory's note
     numbers for sequence gaps (1…N roughly contiguous). Each hole becomes a
     `suspected gap` row. An empty inventory sets the banner state instead.
  3. **NEW — holistic reconciliation (draft checklist):** for every inventory
     note, collect all placements across ALL notes sheets from
     `notes_cell_provenance` (`source_note_refs` → top-level note number,
     same coercion the detectors use today). Produces the checklist rows +
     statuses.
  4. The draft checklist is handed to the **notes reviewer agent** in its
     context packet (it already receives `coverage_gaps`; the checklist
     supersedes that with the full positive list). The reviewer must
     **auto-resolve** every non-placed row (decision 2026-07-04):
     - `missing` → locate the note in the PDF and author it (grounded,
       existing `author_note_cell` tool);
     - `suspected gap` → hunt the PDF around the numbering hole; either
       author the found note, or record "confirmed absent — PDF numbering
       skips this value" (which clears the suspicion);
     - only when genuinely unable → record a structured explanation
       (existing `raise_flag`, reason stored on the checklist row).
  5. The checklist is **recomputed after the reviewer finishes** — this
     final state is what gets persisted for the UI. Rows the reviewer
     resolved show `placed` (with a "reviewer-added" marker for audit);
     rows it could not resolve stay `missing` with the flag attached.
  6. **Run status (decision 2026-07-04):** any unresolved `missing` row tips
     the run to `completed_with_errors`. A `suspected gap` the reviewer
     confirmed absent does NOT (it is resolved-as-explained); an
     uninvestigated suspected gap (reviewer failed/exhausted) does.
  7. UI: a new **Coverage** section in the run's Notes tab renders the final
     checklist. Clicking a placement jumps to that sheet/row in the notes
     editor; clicking a missing row's "View PDF" opens the note's page range.
- **User input:** none required for the happy path; optional "Re-run
  reviewer" button; optional manual verify (nice-to-have).
- **System response:** checklist computation is deterministic (note-number
  reconciliation only — no content matching, respecting the pipeline's
  no-deterministic-label-matching rule / gotcha #14); the reviewer's
  investigation is LLM judgement.
- **Output:** the checklist table above, with a summary line (placed /
  missing / skipped / suspected).
- **Error states:**
  - Empty or unparseable inventory → banner: "Notes inventory unavailable —
    coverage could not be checked", run finishes `completed_with_errors`
    surfaced in the Notes tab (never a silent green).
  - Reviewer exhausts turns with rows still missing → rows stay `missing`
    with the reviewer's flag attached; run tips to `completed_with_errors`;
    checklist never fabricates a tick.
  - Reviewer pass fails outright → UI falls back to the draft checklist
    under a "not yet reviewed" banner; run tips to `completed_with_errors`.
  - Provenance table empty (legacy runs) → checklist renders inventory-only
    with "placements unknown (pre-feature run)".

### Flow B — Top-line routing + carve-out (story 3)

- **Trigger:** notes extraction agents run (rule applied at write time);
  notes reviewer runs (rule enforced after the fact).
- **Steps:**
  1. **Prompt tier:** `prompts/_notes_base.md` gains the two routing rules
     with the confirmed worked example (both directions + the two
     non-triggers). MPERS overlay keeps the "significant" wording note.
  2. **Detector tier (NEW, provenance-based):** a split-detector reports any
     top-level note whose content landed in ≥2 fields on the same topical
     sheet (fan-out on the policies sheet is exempt — that's Direction 2).
     Reported by note refs/coordinates only; the reviewer judges the content
     against the PDF (gotcha #14 pattern).
  3. **Reviewer tier:** for each split finding, the reviewer checks the PDF:
     if the separated section carries the explicit material/significant
     accounting-policy label → it belongs on the policies sheet (move there
     if needed); otherwise → merge it back into the top-line note's field
     (existing `edit_note_cell` + `clear_note_cell` / `move_note_cell`
     tools).
- **User input:** none; results appear as reviewer fixes/flags like today.
- **Output:** notes kept whole; carve-outs only where labelled; split
  findings visible in the reviewer findings list.
- **Error states:** reviewer unsure whether a label qualifies → raises the
  existing `needs_human` flag rather than guessing; no automatic merge
  happens without PDF grounding.

## Technical Approach

- **Stack:** existing pipeline only — Python backend (FastAPI + SQLite +
  pydantic-ai agents), React frontend with inline styles. No new services or
  dependencies.
- **Reuse (this is mostly wiring, not new machinery):**
  - The list: `run_notes_inventory` (schema v23) — already persisted.
  - Placements: `notes_cell_provenance` (v23) — already records
    sheet/row/source_note_refs per payload.
  - Gap detection: `notes/detectors.py::inventory_coverage_gaps` — already
    holistic across sheets; the checklist is its positive-form superset.
  - The acting agent: `notes/reviewer_agent.py` — already has author / move /
    clear / flag / verify tools and receives coverage gaps.
  - Receipts: `notes/coverage.py` CoverageReceipt — pattern proven on
    Sheet 12; extension to other sheets is the nice-to-have story 5.
- **New pieces:**
  - Checklist builder (pure function: inventory × provenance → rows) +
    contiguity check.
  - Durable checklist rows per run (new table, walk-forward migration per
    gotcha #11 conventions) so UI + re-review read one truth.
  - `GET /api/runs/{id}/notes-coverage` + Coverage UI section in the Notes
    tab.
  - Split-detector in `notes/detectors.py` + reviewer packet/prompt wiring.
  - Prompt additions to `prompts/_notes_base.md` (+ MPERS overlay), pinned by
    prompt tests like the existing routing/heading rules.
  - Loud-empty-inventory handling in `server.py` (replace the silent
    degrade-to-no-gaps path with a surfaced warning).
- **Data model (plain terms):** each run gets one checklist: a list of rows,
  each holding the note number, its title from the PDF, a status, the list of
  (sheet, row) placements, and — when missing/skipped — the reason recorded
  by the agent or reviewer.

## Scope Boundaries

- **In scope:**
  - Holistic checklist (build, persist, API, UI) for top-level notes, shown
    to the human in its post-reviewer (final) state.
  - Inventory contiguity check + loud empty-inventory handling.
  - Reviewer auto-resolves every non-placed row, including hunting the PDF
    for `suspected gap` numbering holes.
  - Unresolved `missing` rows (and uninvestigated suspected gaps) tip the
    run to `completed_with_errors`.
  - Per-sub-ref sub-note accounting on every note with inventory sub-notes
    (cited / verified / missing / not-verified states, reviewer content
    verification for coarsely-cited notes), rolled up on the parent row and
    expandable. Always-visible child rows for the policies fan-out and
    carve-outs (see Decisions — degrade to a single multi-placement row
    when the sub-note inventory is empty).
  - Routing rules in prompts + split detector + reviewer enforcement.
  - Both filing standards (MFRS/MPERS) and both levels (Company/Group).
- **Out of scope (yet):**
  - An AI "inventory auditor" second-opinion pass that re-reads the PDF to
    verify the scout's list (the contiguity check + reviewer gap-hunt is
    the v1 stand-in).
  - Extending coverage receipts to sheets 10/11/13/14 (story 5 —
    nice-to-have, only if cheap after the checklist lands).
  - Any change to Sheet-12 fan-out batching, retry budgets, or the xlsx
    download/overlay path.
- **Known limitations:**
  - The checklist is only as complete as scout's inventory plus the
    contiguity heuristic; a missed note whose absence leaves no numbering
    hole (e.g. the PDF's last note) can still go undetected in v1.
  - Contiguity assumes conventional 1…N numbering; unusual schemes (lettered
    notes, per-section restarts) may produce false "suspected gap" rows. The
    reviewer's PDF hunt clears these (records "confirmed absent"), at the
    cost of some reviewer turns spent on false suspicions.
  - The carve-out trigger is the explicit label; a policy section a human
    would recognise without the label will stay with its top-line note. This
    is deliberate (confirmed 2026-07-04) — predictability over cleverness.

## Decisions (2026-07-04)

1. **Human sees the post-reviewer list.** The checklist surfaced in the UI
   is computed AFTER the notes reviewer pass; the draft checklist is only a
   reviewer input (UI fallback with a "not yet reviewed" banner if the
   reviewer pass fails).
2. **Reviewer auto-resolves.** The reviewer investigates and fixes every
   `missing` row and hunts every `suspected gap` in the PDF automatically —
   no ask-the-human-first step.
3. **Unresolved gaps are errors.** Any unresolved `missing` row (or
   uninvestigated suspected gap) tips the run to `completed_with_errors`.
4. **Trigger phrases:** the closed material/significant accounting policy
   set only, no additional phrasings for now.
5. **Sub-note accounting is universal; child-row DISPLAY is not
   (recommended, pending final ack).** Accounting and display are separate
   concerns:

   **(a) Accounting — every note with inventory sub-notes is tracked
   per-sub-ref.** A top-level `placed` tick alone cannot prove the note's
   sub-sections all made it in. Each sub-ref carries a state:
   - `cited` — the writer's `source_note_refs` include it (deterministic,
     free — the existing citation reconciliation).
   - `verified` — the reviewer confirmed the content is present in the
     placed cell against the PDF (covers the combined-cell case where the
     agent cited only the bare note number — the citation-based detector's
     deliberate blind spot). A "folded-in / not applicable" verdict counts
     as verified, reason recorded.
   - `missing` — reviewer confirmed absent → it authors the fix; if it
     cannot, the row errors like a missing note.
   - `not verified` — reviewer budget exhausted before checking; shown
     honestly (warns, does NOT tip run status — only confirmed `missing`
     does). Fully-cited notes skip reviewer verification entirely.
   The parent row shows a roll-up indicator (`sub-notes 4/6 ⚠`) and expands
   to the per-sub-ref detail on click.
   **Cost note:** reviewer verification scales with the number of
   coarsely-cited notes; the deterministic cited-skip and the honest
   `not verified` state are the pressure valves.

   **(b) Always-visible child rows — only where a note legitimately spans
   more than one field.** By Rule 1/2 that happens in precisely two cases:
   - **Policies fan-out (Direction 2):** the policies note renders as one
     parent row with indented child rows, one per sub-policy from the
     inventory's `subnote_refs` (e.g. "3.2 Investment properties → Sheet 11
     row 14"). Rationale: the policies sheet has per-topic fields, so
     "Note 3 placed at rows 10, 14, 22" cannot show that ONE sub-policy was
     dropped — exactly the partial-coverage failure the sub-note gap
     detector already catches. Degrades gracefully: if the scout captured
     no sub-notes for the policies note, render the single multi-placement
     row.
   - **Carve-out from a topical note (Direction 1):** a topical note whose
     labelled policy sub-section was carved out to the policies sheet gains
     one child row per carve-out placement, tagged "policy carve-out"
     (e.g. Note 9 parent → Sheet 12 row 48; child "material accounting
     policy (carved out)" → Sheet 11 row 14).
   All other notes render single-row (with the expandable sub-note roll-up
   from (a)) — under Rule 1 they are placed whole, so always-visible
   sub-rows would be noise, and unexplained multi-placement is a split-
   detector finding, not a checklist state. Child rows derive from actual
   placements (provenance), never from pre-classifying inventory sub-note
   titles — a policy sub-section wrongly LEFT INSIDE its topical note is a
   Flow-B routing finding for the reviewer, not a coverage gap (the
   checklist answers "did every note land somewhere"; routing answers "did
   it land in the right field").

## Open Questions

1. **Manual sign-off (story 4):** should a human be able to tick a
   `missing`/`skipped` row as "verified OK" so the checklist can reach
   all-green? If yes: does that state need to survive a reviewer re-run?
   (Nice-to-have — not blocking v1.)
2. **Granularity ack:** confirm Decision 5 (per-sub-policy child rows under
   the policies note only).
