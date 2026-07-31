# Implementation Plan: Notes Source Integrity and Completeness

**Overall Progress:** `0%` — planning only; no feature code implemented

**Status:** Proposed for product and engineering review

**Last Updated:** 2026-07-31

**Related:** [Notes pipeline](Archive/NOTES-PIPELINE.md),
[coverage and routing plan](PLAN-notes-coverage-and-routing.md),
[verbatim/scout plan](PLAN-notes-verbatim-and-scout-inventory.md), and
[design system](pwc-design-system.html)

## 1. Outcome

Harden notes extraction so the product can prove that every relevant part of
the financial-statement notes was handled. A successful run must no longer mean
only that each discovered note was placed in an output row. It must also mean
that every source paragraph, heading, caption, and table in the notes section
has a traceable disposition.

The intended product promise is:

> The system may say that a source region is unreadable or needs review, but it
> must never silently call an incomplete note complete.

This plan changes the agent from an author of disclosure content into a linker
between immutable source blocks and XBRL/template destinations. Source capture,
rendering, and completeness checks become code-enforced responsibilities.

## 2. Problem statement

The current pipeline has useful note-level controls:

- Scout discovers a note inventory and page ranges.
- Notes agents write extracted HTML and report `source_note_refs`.
- The reviewer reconciles inventory entries with placements.
- The Notes Coverage UI shows placed, missing, skipped, and suspected-gap notes.

Those controls answer **“Was Note 27 placed somewhere?”** They do not reliably
answer **“Did Note 27 include all 13 paragraphs and both tables?”** An agent can
omit a paragraph, truncate a table, or select only the concept-relevant portion
and still produce a plausible row with a valid source-note reference.

The root cause is combined authority: an agent can decide what the source unit
contains and create the content that is mapped to the destination. The new
design separates those decisions and records physical-source coverage before a
mapping can be considered complete.

## 3. Scope

### In scope

- PDF and Word/DOCX notes-section source capture.
- Text-native PDFs, existing vision-based scanned-PDF handling, and ambiguous
  hybrid pages.
- All notes outputs:
  - Corporate Information;
  - Accounting Policies;
  - List of Notes / prose disclosures;
  - Issued Capital;
  - Related Party Transactions.
- Immutable source generations, source-note boundaries, source blocks, and
  block-to-output lineage.
- Mapping-agent contracts, deterministic rendering, controlled split requests,
  completeness validation, targeted retry, reviewer behavior, run status, API,
  Notes workspace UI, export preflight, telemetry, tests, rollout, and rollback.
- Backward-compatible display of pre-feature runs.

### Out of scope

- Changes to face-statement extraction.
- Taxonomy redesign or deterministic taxonomy-label matching.
- OCR engine introduction. Scanned documents continue to use the existing
  vision path; uncertain visual capture fails closed to review.
- Synonym dictionaries or rule-based semantic concept selection.
- Hand-editing SSM template formulas or backup originals.
- Replacing the canonical concept model.
- Moving mTool decoration styles upstream into the notes database.

## 4. Non-negotiable product invariants

1. **Freeze before mapping.** Source-note boundaries and blocks are fixed for a
   source generation before a taxonomy-aware agent receives the note.
2. **One physical owner.** Every source block has exactly one physical owner:
   a source note, page furniture, document metadata, or unresolved.
3. **Every owned block is reconciled.** A note-owned block must be included in
   a disclosure, consumed into structured fields, explicitly routed, excluded
   under a closed reason, or marked unresolved.
4. **Mapping does not rewrite source.** For full-fidelity prose flows, the
   mapper returns destination IDs and source-block IDs, not disclosure HTML.
5. **Tables are first-class source blocks.** A note cannot pass while an owned
   table or a continuation page is unaccounted for.
6. **Splitting is privileged.** One source note maps to one Sheet-12 disclosure
   block by default. A split requires structured evidence and deterministic
   validation.
7. **Exceptions remain explicit.** Accounting-policy fan-out/carve-outs,
   Corporate Information fields, Issued Capital, Related Party Transactions,
   and the existing share-capital exception use named routing semantics rather
   than weakening the default rule.
8. **Partial output remains recoverable but not green.** A user may review and
   download the available artifact, while unresolved source coverage tips the
   run to `completed_with_errors`.
9. **Human edits are allowed and visible.** Editing a rendered cell marks it as
   human-modified/source-diverged; it must not overwrite the immutable source.
10. **Formatting stays downstream.** The formatter remains style-only and must
    not alter text, numbers, row/column structure, placement, or source links.
11. **Page hints stay soft.** Agents and exception resolvers may inspect any
    source page; source coverage must not reintroduce `allowed_pages` filtering.
12. **No silent render loss.** Character-cap degradation, table flattening, or
    export truncation is an integrity result, not an invisible success.

## 5. Coverage semantics by notes flow

A single “copied percentage” is not correct for every output sheet. Store a
coverage mode for each extraction task and apply the corresponding completion
rule.

| Coverage mode | Applies to | Complete when |
|---|---|---|
| `PROSE_COMPLETE` | List of Notes and prose Accounting Policies | Every eligible source block is included in deterministic output or accounted for by an approved route/exclusion; all tables retained |
| `FIELD_SELECTIVE` | Corporate Information | Every source block in scope is tied to extracted fields, routed, explicitly out of scope under a closed reason, or unresolved |
| `STRUCTURED_SELECTIVE` | Issued Capital and Related Party Transactions | Every source table/region in scope has a structured-consumption receipt, approved route/exclusion, or unresolved status; cell/value tie-outs pass |
| `LEGACY_UNVERIFIED` | Runs created before this feature | Existing note-level coverage is shown, but block completeness is never implied |

“Not relevant” must not be a free-text escape hatch. The system will maintain a
closed reason-code list, initially:

- `PAGE_HEADER`
- `PAGE_FOOTER`
- `PAGE_NUMBER`
- `REPEATED_CONTINUATION_HEADING`
- `DUPLICATE_SOURCE_ARTIFACT`
- `DOCUMENT_METADATA`
- `OUTSIDE_SELECTED_FILING_SCOPE`
- `EXPLICIT_POLICY_ROUTE`
- `APPROVED_DUPLICATE_ROUTE`
- `UNREADABLE_NEEDS_REVIEW`

The last reason remains unresolved for status purposes. Product and accounting
owners must approve changes to the list because each code affects the meaning
of “complete.”

## 6. Target architecture

```text
Financial statement PDF / DOCX
               |
               v
   Source-generation builder
   - source checksum
   - page/DOM coverage receipts
               |
               v
   Layout and source-block capture
   - headings, paragraphs, tables, captions
   - PDF page + bbox or DOCX DOM locator
               |
               v
   Note boundary and ownership pass  <---- no taxonomy access
               |
               v
   Immutable source notes + content hashes
               |
        +------+------+
        |             |
        v             v
 Candidate retrieval  Structural summary
        |             |
        +------+------+
               v
   Mapping/linking agent
   - destination IDs
   - source block IDs
   - split/route evidence
   - no disclosure-content output for prose
               |
               v
   Deterministic renderer / structured writer
               |
               v
   Integrity engine
   - ownership, coverage, continuity, tables
   - render equivalence, duplication, truncation
               |
        +------+------+
        |             |
      pass      targeted retry / review
        |             |
        +------+------+
               v
   notes_cells + lineage + coverage status
               |
        +------+------+
        |             |
   Notes workspace   Export preflight
```

The scout inventory remains useful as a boundary hypothesis and a human-facing
table of contents. It no longer serves as proof that all physical source
content was captured.

## 7. Source model

### 7.1 Source generations

Every extraction or notes-only rerun creates a new source generation. A
generation becomes active only after source capture, note ownership, and basic
manifest validation complete transactionally. If generation building fails,
the previous active generation remains available.

Required generation metadata:

- run and monotonically increasing generation number;
- input checksum and extractor version;
- input kind (`pdf_text`, `pdf_vision`, `pdf_hybrid`, `docx_html`);
- lifecycle (`building`, `active`, `superseded`, `failed`);
- page/section coverage receipts;
- timestamps and failure code without logging source prose.

### 7.2 Source blocks

A source block represents content that can be owned and reconciled without an
agent rewriting it.

```json
{
  "block_id": "g2_p42_b017",
  "kind": "paragraph",
  "page": 42,
  "locator": {"bbox": [72, 118, 526, 183]},
  "reading_order": 317,
  "raw_text_hash": "sha256:...",
  "canonical_html": "<p>...</p>",
  "owner_kind": "note",
  "source_note_id": "g2_note_27",
  "capture_confidence": 0.98
}
```

For DOCX, `locator` uses a stable DOM/block index rather than a PDF bounding
box. For vision pages, it records the page region and the existing vision
transcription method. Source HTML is sanitized before persistence/display, but
its pre-sanitization hash is retained for change detection.

Tables store table identity, caption linkage, column signature, continuation
links, and canonical table markup. Multi-page table segments remain distinct
blocks linked through one `table_group_id`; integrity is evaluated at both
segment and table-group level.

### 7.3 Immutable source notes

A source note contains ordered block references, not generated disclosure text.

```json
{
  "source_note_id": "g2_note_27",
  "top_note_num": "27",
  "title": "Financial risk management objectives and policies",
  "block_ids": ["g2_p42_b017", "g2_p42_b018", "g2_p43_t004"],
  "boundary_confidence": 0.94,
  "content_hash": "sha256:...",
  "status": "frozen"
}
```

Subheadings remain logical children inside the frozen note. They may guide
candidate selection, policy routing, facts, and navigation without silently
turning into separate source disclosures.

### 7.4 Ownership and use

Physical ownership and downstream use are separate:

- A block has exactly one physical owner.
- A note-owned block has one primary disposition:
  `included`, `structured_consumed`, `routed`, `excluded`, or `unresolved`.
- A block may have zero or more reference usages for provenance or granular
  facts without changing ownership.
- Multiple disclosure placements require `APPROVED_DUPLICATE_ROUTE` and a
  route type; unreasoned duplication is an integrity failure.

## 8. Persistence design

Use forward-only schema migrations from the current schema version. Exact
version numbers must be allocated at implementation time if other migrations
land first.

### 8.1 New tables

`notes_source_generations`

- `id`, `run_id`, `generation_no`
- `source_sha256`, `extractor_version`, `input_kind`
- `status`, `pages_expected`, `pages_processed`
- `started_at`, `activated_at`, `failed_at`, `failure_code`
- unique `(run_id, generation_no)` and at most one active generation per run

`notes_source_notes`

- `id`, `generation_id`, `source_note_id`
- `top_note_num`, `title`, `page_lo`, `page_hi`
- `start_block_id`, `end_block_id`
- `boundary_confidence`, `content_sha256`, `status`
- unique `(generation_id, source_note_id)`

`notes_source_blocks`

- `id`, `generation_id`, `block_id`, `source_note_id`
- `page`, `reading_order`, `block_kind`, `locator_json`
- `canonical_html`, `content_sha256`, `capture_confidence`
- `owner_kind`, `table_group_id`, `continues_block_id`
- unique `(generation_id, block_id)`

`notes_block_usages`

- `id`, `run_id`, `generation_id`, `block_id`
- `sheet`, `row`, `concept_uuid`, `target_kind`
- `disposition`, `reason_code`, `route_type`
- `created_by` (`agent`, `reviewer`, `human`, `system`)
- `created_at`, `updated_at`
- indexes for run/note, target cell, unresolved disposition, and block

`notes_integrity_runs`

- one result per run/generation/check attempt;
- overall status and rule-version;
- source/page/block/table totals;
- unresolved, excluded, duplicated, and render-loss counts;
- retry number and review requirement;
- compact machine-readable reasons.

### 8.2 Existing table changes

Extend `notes_cells` with nullable, backward-compatible provenance fields:

- `source_generation_id`
- `source_rendered_sha256`
- `current_html_sha256`
- `content_origin`: `source_exact`, `source_normalized`,
  `vision_transcribed`, `structured_generated`, `human_modified`, or `legacy`
- `source_diverged_at`

Keep `style_source` independent; styling provenance and content provenance are
different facts.

Extend `notes_coverage_rows` or join its rows to `notes_integrity_runs` so the
API can return per-note metrics without duplicating truth:

- blocks total/included/routed/excluded/unresolved;
- tables total/included/unresolved;
- pages expected/processed;
- continuity, boundary confidence, render loss;
- integrity status and reason codes;
- source generation.

Retain `notes_cell_provenance.source_note_refs` and `run_notes_inventory` during
the migration. Existing detectors, legacy runs, and rollback depend on them.

### 8.3 Transaction and rerun rules

- Build generation rows in `building` state.
- Insert blocks, notes, and ownership within bounded transactions.
- Validate uniqueness, page receipts, ownership, and hashes.
- Atomically mark the new generation active and the previous one superseded.
- Replace derived block usages and coverage only after activation succeeds.
- Never delete the previous active generation before the new generation is
  valid.
- Respect current reviewer/formatter/rerun mutual-exclusion locks.
- Make retries idempotent by stable block IDs plus source/extractor hashes.

## 9. Source capture and boundary hardening

### 9.1 Text-native PDF

- Capture text spans, paragraphs, headings, captions, and tables with page,
  geometry, style features, and reading order.
- Classify repeated page furniture separately.
- Reconcile the notes-section page range against processed-page receipts.
- Use structural evidence—note-number shape, heading hierarchy, geometry,
  spacing, sequence, continuation markers, and table continuity—for boundary
  proposals. Do not perform deterministic taxonomy/label matching.
- Compare the existing scout/LLM inventory with the layout proposal.
- Send disagreements, low-confidence boundaries, same-page multiple headings,
  and missing leading/trailing notes to the existing stronger model path or
  human review.

### 9.2 DOCX

- Reuse `ingest/docx_html` output and `notes/source_snippets.py` rather than
  converting Word content back through PDF-like heuristics.
- Preserve source table markup through the existing sanitization trust model.
- Create stable DOM-order locators and continuation relationships.
- Verify that every notes-section DOM block has an owner/disposition.

### 9.3 Scanned and hybrid PDF

- Keep the existing vision-based path; do not introduce an OCR subsystem.
- Render every in-scope page and issue a page-processing receipt.
- Have a taxonomy-blind vision extraction pass create page regions, canonical
  transcriptions/tables, and confidence before mapping.
- Run an independent lightweight completeness check over the rendered page and
  the region manifest. It verifies region/page accounting, not taxonomy.
- Route unreadable regions, uncertain table reconstruction, and detector
  disagreement to `UNREADABLE_NEEDS_REVIEW`.
- Never infer completeness solely from an agent statement such as “all notes
  processed.” A scanned run is green only if the manifest and verifier agree
  above calibrated thresholds.

### 9.4 Boundary-specific failure cases

Pin handling for:

- multiple top-level notes beginning on one page;
- notes continuing without a repeated heading;
- repeated “continued” headings;
- subnote numbers such as `(a)` or `27.1`;
- a note number appearing inside narrative text;
- unnumbered notes;
- appendices or schedules that continue a disclosure;
- a table crossing pages with repeated column headings;
- leading/trailing note loss outside a naive sequential range;
- page headers that resemble note titles.

## 10. Agent and tool-contract changes

### 10.1 Separation of authority

Introduce three explicit roles, even where the first two share an underlying
model provider:

1. **Source assembler:** creates source blocks, note ownership, hierarchy, and
   confidence. No taxonomy or destination-row access.
2. **Mapping linker:** chooses destination concepts/rows and returns block IDs,
   routes, confidence, and reason codes. It cannot return prose HTML for
   `PROSE_COMPLETE` tasks.
3. **Exception resolver:** receives only disagreements, incomplete coverage,
   authorized split requests, or unreadable/ambiguous cases.

Candidate retrieval remains a small-list semantic task using the current LLM
judgment model. The full taxonomy should not be placed in the mapping context.

### 10.2 Proposed tools/contracts

Add read-only tools:

- `list_source_notes()`
- `read_source_note_manifest(source_note_id)`
- `view_source_blocks(block_ids)`
- `view_source_pages(pages)`; remains unrestricted by scout hints

Replace direct prose authoring for full-note flows with a structured linker:

```json
{
  "source_note_id": "g2_note_27",
  "placements": [
    {
      "sheet": "Notes",
      "row": 84,
      "concept_uuid": "...",
      "block_ids": ["g2_p42_b017", "g2_p42_b018", "g2_p43_t004"],
      "mapping_confidence": 0.91
    }
  ],
  "routes": [],
  "exclusions": [],
  "requires_review": false
}
```

For field/structured flows, require consumption receipts:

```json
{
  "target": {"sheet": "Notes-CI", "row": 4},
  "value": "...",
  "source_block_ids": ["g2_p3_b011"],
  "source_locator": {"page": 3, "bbox": [72, 210, 310, 229]}
}
```

Validate every returned ID against the active generation and target row/node
catalog in code. Reject foreign-run blocks, fabricated rows, partial table
segments, invalid dispositions, and blocks owned by another note.

### 10.3 Split budget

Default: one source note to one Sheet-12 disclosure cell.

A split request must provide:

- an approved reason type;
- a visible source heading and its boundary block;
- target row/concept per partition;
- a complete, non-overlapping block partition;
- explicit treatment of captions and tables;
- proof that no paragraph/table is orphaned;
- validator approval before rendering.

Semantic relevance to two concepts is not sufficient. Accounting-policy
carve-outs use a named route rather than pretending the physical note has two
owners. Existing legitimate multi-row flows are validated under their specific
coverage mode.

### 10.4 Targeted retry

Preserve the current bounded retry philosophy:

- First integrity failure returns exact missing block IDs, table groups, page
  receipts, or invalid routes to the responsible agent.
- The retry may repair only the cited gaps; it must not regenerate already
  accepted content or change source boundaries.
- Boundary corrections create a new manifest attempt/generation, not an
  in-place mutation.
- After the retry budget is exhausted, persist the partial artifact and mark
  the note/run `needs_review` / `completed_with_errors`.

## 11. Deterministic rendering and structured writing

### Prose rendering

- Retrieve canonical HTML from the active generation in reading order.
- Preserve heading/paragraph/table hierarchy and existing allowed Word table
  style passthrough.
- Assemble multi-page tables from linked segments without asking the mapper to
  reproduce them.
- Sanitize through the existing notes HTML trust model.
- Hash the rendered source version and store it on `notes_cells`.
- Compare visible text, ordered block IDs, table groups, and renderer output;
  do not rely on HTML-string equality alone.

### Structured flows

- Continue to write field/value or structured table outputs where exact source
  block copying is not the product behavior.
- Require source-block/region receipts per written field or table range.
- Reconcile unconsumed blocks at the source-note level.
- Preserve existing numeric concepts, validation, and canonical-fact paths.

### Human edits

- Keep the Notes editor available.
- On content-changing PATCH, update `current_html_sha256`, set
  `content_origin=human_modified`, and record source divergence.
- Never modify source blocks or source hashes.
- Offer “compare with captured source” and “restore source-rendered version.”
- A human edit can intentionally resolve an issue only when affected block
  usages are also updated with a reason and audit actor.

## 12. Integrity engine

Implement mostly as deterministic code in a new notes integrity module. Run it
after mapping, after reviewer changes, before terminal run status, and before
export/download materialization.

### Checks

1. Source generation exists and is active.
2. Every in-scope page/DOM segment has a processing receipt.
3. Every captured block has exactly one physical owner classification.
4. Every note-owned block has an allowed disposition.
5. `PROSE_COMPLETE` notes have full eligible-block coverage.
6. Every owned table group and continuation segment is included/routed.
7. Selected block sequences are continuous unless each gap is explained.
8. Split requests form complete non-overlapping partitions.
9. Duplicate placements have an approved route reason.
10. Source-note boundaries agree sufficiently or carry review status.
11. Rendered block IDs and hashes match the selected source version.
12. Sanitization/serialization did not remove visible text, rows, columns, or
    table groups.
13. Cell/export character limits did not truncate content.
14. Human-modified cells expose divergence and are not mislabelled source exact.
15. Existing top-line routing, carve-out, and cross-sheet checks still pass.

### Metrics

- source pages processed / expected;
- captured blocks and owner-classification rate;
- included, structured-consumed, routed, excluded, unresolved blocks;
- per-note eligible-block coverage;
- table segments and groups retained;
- continuity score;
- unexpected split, merge, duplicate, and orphan rates;
- boundary-detector agreement and confidence;
- render-loss characters/rows/columns;
- targeted-retry success;
- human-review and human-divergence rates.

### Status vocabulary

- `complete`: all required checks pass.
- `complete_with_exclusions`: all exclusions use approved closed reasons and no
  unresolved content remains.
- `needs_review`: unreadable, ambiguous, missing, orphaned, truncated, or
  otherwise unresolved content exists.
- `unavailable`: source manifest could not be built.
- `legacy_unverified`: predates source integrity.

Only the first two may support a green notes-integrity result. Any unresolved
block, missing table, unavailable inventory/manifest, failed page receipt, or
render loss tips a notes-targeting run to `completed_with_errors`. Do not
overwrite a more severe terminal failure or clear unrelated run errors.

## 13. Backend orchestration

Add explicit stages to the existing run lifecycle:

1. `building_notes_manifest`
2. `extracting_notes`
3. `checking_notes_integrity`
4. existing reviewer/formatter/finalization stages

Requirements:

- Create the audit run row before validation, as today.
- Emit progress through the existing event queue, not direct competing yields.
- Persist structured warning/error codes with safe metadata.
- Keep every success, failure, cancellation, and disconnect path terminal.
- Run integrity before the final status decision and again after reviewer
  remediation.
- Preserve partial notes cells and the merged/downloadable workbook when safe.
- Mark the run merged at the existing lifecycle point.
- Ensure manual reviewer and notes-only rerun paths rebuild/recompute integrity.
- Do not allow formatter, reviewer, rerun, or human block-remediation tasks to
  interleave writes for the same run.

The current coordinator behavior that treats partial per-sheet coverage as
sheet success can remain useful operationally, but the aggregate source
integrity gate must prevent that partial result from becoming green.

## 14. API design

### Extend existing endpoints

`GET /api/runs/{run_id}/notes-coverage`

- Preserve existing fields for frontend/backward compatibility.
- Add top-level `integrity`, `generation`, page/block/table counts, and result
  status.
- Add per-note block/table metrics, reason codes, retry state, confidence, and
  source origin.
- Return `legacy_unverified` for old runs instead of manufacturing block data.

`GET /api/runs/{run_id}/notes_cells`

- Add content-origin, source/current hashes, divergence state, generation ID,
  and integrity summary per cell.

`PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}`

- Keep the current edit contract.
- Mark content divergence atomically.
- Return refreshed cell-integrity metadata.

### New detail/remediation endpoints

`GET /api/runs/{run_id}/notes-source/{source_note_id}`

- Paginated/one-note detail with blocks, locations, dispositions, placements,
  safe preview HTML, confidence, tables, and hashes.

`POST /api/runs/{run_id}/notes-source/{source_note_id}/retry`

- Starts a durable, scoped retry task; rejects concurrent reviewer/formatter or
  rerun operations.

`PATCH /api/runs/{run_id}/notes-source/{source_note_id}/blocks/{block_id}`

- Human disposition correction with a closed reason and audit actor.
- Supports include/link, approved page-furniture classification, route, and
  unresolved confirmation; no arbitrary source-text mutation.

`POST /api/runs/{run_id}/notes-source/{source_note_id}/restore-render`

- Restores the deterministic source-rendered cell version after confirmation,
  while retaining audit history.

### API hardening

- Enforce run ownership/authentication through existing middleware.
- Validate generation/run/note/block relationships server-side.
- Sanitize source previews; never return raw unsafe DOCX/PDF-derived HTML.
- Bound page size, block count, payload bytes, and retry frequency.
- Use optimistic version checks for concurrent human edits.
- Do not log source prose, full tables, or model prompt payloads.
- Return stable machine error codes plus plain-language operator messages.

## 15. Frontend experience

Extend the existing Notes review workspace; do not add a disconnected screen.
Use inline styles, theme tokens, shared UI primitives, and the current design
system behavior.

### 15.1 Notes Coverage navigation

Enhance `NotesCoverageNav` and `NotesCoveragePanel`:

- Summary: “23 of 24 notes complete · 311 of 313 blocks · 18 of 19 tables.”
- Distinguish placement from source integrity.
- Status labels: Complete, Complete with exclusions, Needs review, Source
  unavailable, and Legacy—not color alone.
- Auto-expand attention items while preserving the current clean-state
  collapsing behavior.
- Show bounded retry progress and the last integrity check time.

### 15.2 Source Integrity detail

When a note is selected, show an integrity view alongside the existing editor
and PDF source pane:

- ordered block list grouped by subheading/page;
- each block marked Included in row X, Structured into fields, Routed,
  Excluded with reason, Unreadable, or Missing;
- table-group and continuation status;
- boundary confidence and disagreement warning;
- cell/source hashes and human-divergence state;
- safe source preview and destination link.

Clicking a PDF block moves `PdfSourcePane` to the page and highlights its
bounding box. DOCX blocks open the existing source preview at the DOM locator.
Clicking a placement keeps the current cell-focus behavior.

### 15.3 Remediation actions

For authorized users:

- Add/link this source block to the selected destination.
- Mark page furniture using a closed reason.
- Route an explicit policy section.
- Retry this note.
- Compare human-edited content with the captured source.
- Restore the source-rendered version.

No generic “dismiss” action. Any disposition change shows its effect on the
coverage counters before confirmation and triggers a centralized integrity
recompute afterward.

### 15.4 Cell provenance chips

Display content provenance separately from formatting provenance:

- Source exact
- Source normalized
- Vision transcribed
- Structured extraction
- Human modified
- Incomplete
- Legacy / not verified

Do not reuse `style_source` for these labels.

### 15.5 States and responsive behavior

Design and test:

- loading and retrying;
- active generation being built;
- complete and complete-with-exclusions;
- incomplete/unreadable/table-missing;
- API error and manifest unavailable;
- reviewer not completed;
- legacy/pre-feature run;
- no notes targeted;
- user lacks remediation permission;
- concurrent edit/task conflict.

Laptop/wide layouts show editor, integrity, and source panes together. Tablet
uses a simplified two-pane layout. Mobile is read/monitor-first with simple
actions and no precision bounding-box editing. Maintain keyboard navigation,
visible focus, semantic buttons/regions, screen-reader status announcements,
and reduced-motion behavior. Do not introduce competing ARIA tablists.

If a shared interaction or token changes, update
`docs/pwc-design-system.html`, `web/src/lib/theme.ts`,
`web/src/lib/uiStyles.ts`, `web/src/index.css`, and their pinning tests together.

## 16. Export and mTool hardening

- Keep `notes_cells` as the canonical editable derived output and source for
  workbook overlay.
- Run an integrity preflight before download materialization and mTool fill.
- Allow download of a partial artifact, but show a persistent warning and
  include the integrity status in run metadata.
- Convert the current rendered-character cap from a silent/degraded behavior
  into a recorded `render_loss`/`needs_review` result whenever visible content
  cannot be retained.
- Validate text, tables, row/column structure, and continuation segments before
  and after the existing compact/lite/flat size ladder.
- For a complete note too large for one target cell, require an approved
  continuation/split strategy; never truncate it silently.
- Preserve Word source-table styling and `data-source-styled` behavior.
- Keep `mtool/notes_decorate.py` and `web/src/lib/clipboard.ts` in lock-step if
  either must change. Prefer no decorator change for this feature.

## 17. Security, privacy, reliability, and performance

### Security/privacy

- Treat all PDF/DOCX text and HTML as untrusted input.
- Delimit source content in model contexts and prevent source text from being
  interpreted as tool instructions.
- Sanitize once at capture and again at render/API boundaries according to the
  current trust model.
- Validate locators and block IDs; prevent path traversal and cross-run access.
- Redact source prose from logs, traces, metrics labels, and error messages.
- Audit human disposition changes, retries, restores, and split approvals.

### Reliability

- Atomic generation activation and idempotent retries.
- Existing run-terminal-status and `_safe_mark_finished` protections retained.
- Explicit failure codes for capture, boundary, mapping, rendering, and export.
- No fallback from unavailable source integrity to a green note-level result.
- Previous active source generation remains recoverable after failed rerun.

### Performance/cost

- Build the manifest once and reuse it across parallel notes agents.
- Send compact note summaries/candidate lists, then fetch exact blocks through
  tools; do not place the entire taxonomy/document in each prompt.
- Paginate detail APIs and lazy-load block previews in the frontend.
- Cache page renders and canonical block HTML by source/extractor hash.
- Use deterministic integrity checks; reserve the strongest model for boundary
  disagreements and unresolved visual cases.
- Add per-document caps for pages, blocks, tables, context bytes, retries, and
  wall-clock time with an explicit needs-review outcome on exhaustion.

## 18. Implementation phases

No phase below has started. Each phase should land behind one master feature
flag and leave the repository testable.

### Phase 0 — Baseline, decisions, and evaluation corpus

- [ ] Record current missing-content, note-placement, table-retention,
      unexpected-split, run-status, latency, and cost baselines.
- [ ] Assemble anonymized/gold fixtures covering all cases in section 20.
- [ ] Manually annotate notes-section pages, source-note boundaries, blocks,
      tables, routes, exclusions, and expected destinations.
- [ ] Approve coverage modes, exclusion codes, split reasons, confidence gates,
      and who may remediate.
- [ ] Confirm whether source HTML retention complies with data-retention rules;
      if not, persist encrypted/minimized content plus hashes/locators.
- [ ] Produce a migration/feature-flag compatibility matrix.

**Gate:** Product, XBRL/domain, security, and engineering owners approve the
meaning of “complete” and the gold annotations.

### Phase 1 — Domain contracts and schema foundation

- [ ] Add typed models for generations, blocks, source notes, dispositions,
      coverage modes, integrity results, and reason codes.
- [ ] Implement forward-only migrations and repository helpers.
- [ ] Add atomic generation activation and failure recovery.
- [ ] Add nullable notes-cell provenance/divergence fields.
- [ ] Preserve current provenance/inventory contracts for legacy compatibility.

**Primary files:** `db/schema.py`, `db/repository.py`, `notes/payload.py`, new
`notes/source_models.py`, new `notes/source_repository.py`.

**Gate:** Fresh database, current-version migration, stepwise older-version
migration, failed activation, rerun, and rollback-compatibility tests pass.

### Phase 2 — Text PDF and DOCX manifest capture

- [ ] Implement PDF layout blocks and page receipts.
- [ ] Implement DOCX DOM blocks using current ingestion/source snippets.
- [ ] Capture and link table continuations.
- [ ] Classify page furniture and unresolved blocks.
- [ ] Freeze source notes with hashes and ownership.
- [ ] Compare manifest inventory with scout inventory; route disagreements.

**Primary files:** `scout/notes_discoverer.py`, `scout/infopack.py`,
`notes/source_snippets.py`, new `notes/source_manifest.py`, new
`notes/source_boundaries.py`, new `notes/source_tables.py`.

**Gate:** Gold text PDF/DOCX fixtures have full page/DOM receipts, no orphan
blocks, correct same-page boundaries, and complete table groups.

### Phase 3 — Vision/hybrid capture and uncertainty handling

- [ ] Adapt the existing vision path to emit regions/blocks before mapping.
- [ ] Add taxonomy-blind region-manifest verification.
- [ ] Calibrate confidence/disagreement gates on scanned fixtures.
- [ ] Persist unreadable regions as visible unresolved items.
- [ ] Enforce page/time/context limits with fail-closed status.

**Primary files:** `scout/notes_discoverer_vision.py`, `scout/vision.py`, new
`notes/source_vision.py`.

**Gate:** Scanned/hybrid fixtures never pass with an omitted annotated region or
table; low-quality pages reach review rather than green success.

### Phase 4 — Link-only prose mapping and deterministic renderer

- [ ] Add source-manifest read tools.
- [ ] Change Sheet-12 and prose-policy agent contracts to block-link outputs.
- [ ] Add candidate-list retrieval without deterministic semantic matching.
- [ ] Validate row/node and block IDs in code.
- [ ] Implement approved policy routing and split-request contracts.
- [ ] Render prose/table output deterministically into `notes_cells`.
- [ ] Store source-rendered hashes and block usages.

**Primary files:** `notes/agent.py`, `notes/coordinator.py`,
`notes/listofnotes_subcoordinator.py`, `notes/writer.py`,
`notes/persistence.py`, prompts under `prompts/`, new
`notes/source_renderer.py`.

**Gate:** The mapping model cannot submit prose content; complete source notes
render identically from accepted block links, with current routing exceptions
preserved.

### Phase 5 — Structured/field flow receipts

- [ ] Add block/region receipts to Corporate Information writes.
- [ ] Add table/region receipts and tie-outs to Issued Capital writes.
- [ ] Add table/region receipts and tie-outs to Related Party writes.
- [ ] Reconcile unconsumed source blocks under each coverage mode.
- [ ] Preserve canonical numeric facts and existing template registry behavior.

**Primary files:** relevant notes prompts/agent branches, `notes/payload.py`,
`notes/writer.py`, canonical-fact persistence and tests.

**Gate:** Every structured output value traces to source; every in-scope source
block is consumed, routed, excluded under a closed reason, or unresolved.

### Phase 6 — Integrity engine, targeted retry, and run status

- [ ] Implement all section-12 checks as pure, versioned rules.
- [ ] Add exact missing-block/table/page feedback for one bounded retry.
- [ ] Persist integrity attempts and metrics.
- [ ] Recompute after reviewer, manual remediation, and notes rerun.
- [ ] Integrate fail-closed terminal-status logic while preserving partial
      downloads and unrelated error severity.
- [ ] Emit pipeline/SSE events through the existing queue.

**Primary files:** new `notes/integrity.py`, `notes/coverage_checklist.py`,
`notes/coordinator.py`, `notes/reviewer_agent.py`, `server.py`,
`api/notes_reviewer.py`.

**Gate:** No fixture with an orphan block, missing table, unprocessed page, or
render loss can finish `completed`.

### Phase 7 — API and human-remediation backend

- [ ] Extend coverage/cell response types backward-compatibly.
- [ ] Add source-note detail, retry, block-disposition, and restore endpoints.
- [ ] Add authorization, optimistic versioning, concurrency locks, bounds,
      audit records, and safe error codes.
- [ ] Centralize integrity recomputation after mutations.
- [ ] Add legacy/unavailable/pre-feature response states.

**Primary files:** `api/notes.py`, `api/notes_reviewer.py`, `api/files.py`,
`server.py`, `db/repository.py`.

**Gate:** Endpoint, auth, cross-run isolation, conflict, sanitization, task
lifecycle, and backward-compatibility tests pass.

### Phase 8 — Frontend end-to-end Notes workspace

- [ ] Extend notes API types and data access.
- [ ] Upgrade `NotesCoverageNav`, `NotesCoveragePanel`, and
      `NeedsAttentionPanel` with integrity metrics/status.
- [ ] Add source block detail and remediation controls inside the existing
      review workspace.
- [ ] Add PDF bounding-box highlighting and DOCX locator navigation.
- [ ] Add cell provenance/divergence chips and compare/restore flow.
- [ ] Implement all error, empty, legacy, permission, retry, and conflict states.
- [ ] Complete responsive, keyboard, screen-reader, focus, and reduced-motion
      behavior using the canonical design system.

**Primary files:** `web/src/lib/notesCells.ts`, `web/src/lib/types.ts`,
`web/src/components/NotesCoverageNav.tsx`,
`web/src/components/NotesCoveragePanel.tsx`,
`web/src/components/NeedsAttentionPanel.tsx`,
`web/src/components/NotesReviewTab.tsx`,
`web/src/components/PdfSourcePane.tsx`, `web/src/pages/ConceptsPage.tsx`, and
possibly `docs/pwc-design-system.html` plus its pinned implementation files.

**Gate:** Component and browser tests prove note-to-block-to-PDF navigation,
remediation/recompute, human divergence, responsive layouts, and accessibility.

### Phase 9 — Export, download, and mTool preflight

- [ ] Run source integrity preflight before workbook overlay and mTool fill.
- [ ] Record and surface sanitizer/size-ladder/export loss.
- [ ] Preserve download access with incomplete-status warning.
- [ ] Verify source-styled Word tables and mTool decorator/clipboard twins.
- [ ] Perform a real Windows mTool Validate/Generate operator gate.

**Primary files:** `notes/persistence.py`, `api/files.py`,
`mtool/notes_exporter.py`, and—only if necessary—the decorator twins.

**Gate:** Complete notes survive editor, download, clipboard, mTool fill,
Validate, and Generate with no unreported text/table loss.

### Phase 10 — Full verification, observability, and performance

- [ ] Run unit, integration, lifecycle, API, frontend, E2E, and regression suites.
- [ ] Run live-provider evaluation separately from deterministic CI.
- [ ] Add dashboards/alerts for integrity, retry, review, cost, and latency.
- [ ] Load-test large notes, many tables, scanned filings, and concurrent runs.
- [ ] Conduct prompt-injection, unsafe-HTML, cross-run, and task-race testing.
- [ ] Update architecture, notes pipeline, sync matrix, operator guidance,
      CLAUDE.md, and AGENTS.md invariants together where required.

**Gate:** Section-21 acceptance criteria and rollout thresholds are met on the
gold corpus and staging workload.

### Phase 11 — Staged rollout

- [ ] Add one master `XBRL_NOTES_SOURCE_INTEGRITY` flag, initially off outside
      development/test.
- [ ] Shadow-compute integrity on staging without changing terminal status.
- [ ] Compare new results with current coverage and manually adjudicate deltas.
- [ ] Enable fail-closed status for internal users.
- [ ] Canary text PDF/DOCX, then scanned/hybrid, then all notes coverage modes.
- [ ] Make default-on only after accuracy, review-rate, latency, and support
      thresholds hold for the agreed window.
- [ ] Retain the flag for emergency rollback until at least one stable release.

**Gate:** No material silent omission in the canary corpus; operator sign-off on
Notes workspace and real mTool outputs.

## 19. Test strategy

### Backend unit tests

- stable block IDs and generation hashes;
- page/DOM receipts and ownership uniqueness;
- repeated furniture classification;
- note boundaries and continuation logic;
- multi-page tables and captions;
- coverage modes and closed exclusions;
- split partitions, approved duplicates, and routes;
- renderer ordering/equivalence and sanitizer loss;
- table/block continuity and orphan detection;
- source/current divergence;
- character-cap and export-loss detection;
- integrity status precedence and run-status tipping.

### Migration/repository tests

- fresh schema;
- current-to-new and stepwise migrations;
- nullable legacy rows;
- activation/supersession atomicity;
- failed generation recovery;
- rerun idempotency;
- integrity/usage replacement transaction rollback;
- indexes and uniqueness constraints.

### Agent-contract tests

- taxonomy-aware prose agent cannot return HTML/content;
- fabricated/foreign block IDs rejected;
- mapper receives a bounded candidate list;
- source assembler has no taxonomy/row tools;
- exact gap retry and retry-budget exhaustion;
- explicit policy route and valid/invalid split evidence;
- page hints remain soft;
- no deterministic label matching/OCR/synonym dictionary introduced.

### Reviewer/formatter tests

- reviewer relinks/routes blocks instead of silently rewriting source;
- human authoring marks divergence;
- restoring source render preserves audit trail;
- reviewer cannot close unreadable content without disposition evidence;
- formatter preserves source/current hashes, content, numbers, structure, and
  placement.

### API tests

- legacy and new response shapes;
- paginated note detail;
- disposition/retry/restore success and validation failures;
- authorization and cross-run block rejection;
- concurrent task/edit conflicts;
- HTML sanitization and safe errors;
- recompute after every mutation.

### Frontend tests

- summary block/table counts and plain-text statuses;
- clean collapse and attention expansion;
- block click to PDF page/bbox and placement click to editor cell;
- table-continuation display;
- remediation confirmation and refreshed counters;
- compare/restore and human-modified chips;
- loading, error, legacy, unavailable, permission, retry, and conflict states;
- keyboard, focus, screen-reader, reduced motion, and narrow viewports.

### End-to-end fixtures

- a multi-page prose note;
- multiple notes beginning on one page;
- multiple subheadings in one note;
- narrative dominated by tables;
- a multi-page table with repeated headers;
- unnumbered notes;
- ambiguous title/candidate concepts;
- a note number appearing inside narrative text;
- accounting policies followed by detailed disclosures;
- an explicit embedded policy carve-out;
- a legitimate multi-target/duplicate route;
- two semantically similar notes that remain separate;
- Corporate Information field extraction;
- Issued Capital and Related Party structured tables;
- text PDF, DOCX, scanned PDF, and hybrid pages;
- low-quality/unreadable scan;
- oversized disclosure hitting the cell/export cap;
- user edit followed by formatter, reviewer, rerun, download, and restore;
- cancellation/disconnect/failure during generation activation.

## 20. Observability and operational thresholds

Dashboard by input type, filing standard, sheet, model, extractor version, and
release:

- manifest build success and duration;
- page receipt rate;
- boundary agreement/accuracy;
- eligible-block and table retention;
- orphan/unresolved rate;
- unexpected split/merge/duplicate rates;
- sanitizer/render/export loss;
- retry invocation and success;
- human review and source-divergence rates;
- notes-integrity run-status tips;
- tokens, model calls, and wall-clock latency.

Alert on sudden increases in fragmentation, unresolved blocks, table loss,
vision disagreement, render loss, or legacy/unavailable responses. Metrics must
carry IDs/counts/reason codes only—never source disclosure text.

Rollout thresholds should be set in Phase 0. Recommended starting gates:

- 100% annotated eligible-block and table retention on text PDF/DOCX gold
  fixtures;
- 0 green runs with an annotated missing block/table across all fixtures;
- 0 silent truncations;
- 100% traceability from output cell/value to source block/locator;
- scanned uncertainty favors review over false completeness;
- no regression in approved policy fan-out/carve-outs, Corporate Information,
  Issued Capital, Related Party, or share-capital behavior;
- bounded latency/cost increase agreed before default-on.

## 21. Definition of done

The feature is complete only when all are true:

- Every notes-section page/DOM segment has a durable processing receipt.
- Every source block has one physical owner classification.
- Every note-owned block/table has a valid disposition and traceable destination
  or is visibly unresolved.
- No unresolved block, missing table, boundary disagreement above threshold, or
  render/export loss can coexist with a green notes result.
- Full-fidelity prose agents link source blocks and cannot author disclosure
  HTML.
- Structured/field outputs carry block-level consumption receipts.
- Legitimate routes/splits are explicit, validated, and non-orphaning.
- Targeted retry repairs cited gaps without rewriting accepted content.
- The Notes workspace shows integrity, source location, destination, and safe
  remediation in one end-to-end flow.
- Human modifications are visibly different from source-exact content.
- Partial artifacts remain reviewable/downloadable with a persistent warning.
- Old runs display as legacy/unverified without migration breakage.
- Workbook and mTool paths report any loss and pass the Windows operator gate.
- Backend, frontend, E2E, accessibility, security, performance, and lifecycle
  suites pass.
- Architecture, pipeline, operator, schema, sync-matrix, and invariant docs are
  updated before rollout.

## 22. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Layout extraction itself misses content | Page/DOM receipts, geometry/region accounting, independent manifest comparison, and fail-closed uncertainty |
| Scans cannot provide reliable exact text | Keep taxonomy-blind vision capture, require confidence/agreement, expose unreadable regions to review |
| Block-level storage grows the database | Hash/canonicalize once, index metadata, lazy-load previews, define retention policy in Phase 0 |
| Link-only mapping is a large agent/writer refactor | Stage prose first, keep legacy fields during migration, use one feature flag and golden contract tests |
| Strict coverage creates excessive review | Calibrate only on annotated corpus; keep exclusion reasons closed; use targeted retry and exception-only stronger model |
| Human editing conflicts with source fidelity | Preserve immutable source, mark divergence, provide compare/restore, recompute integrity |
| Tables exceed Excel/mTool constraints | Detect before export, validate the size ladder, require approved continuation strategy, never silently truncate |
| Rerun/reviewer/formatter races corrupt lineage | Existing run locks plus atomic generation activation and optimistic edit versions |
| Current note-level UI becomes confusing | Extend its existing mental model; clearly separate Placed from Complete and use plain-language status/counters |
| Rollback leaves new metadata values | New columns/tables are additive and nullable; old code ignores them; preserve legacy provenance/inventory |

## 23. Rollback plan

1. Disable `XBRL_NOTES_SOURCE_INTEGRITY` to return new runs to the current
   extraction and note-level coverage behavior.
2. Do not drop new tables/columns during emergency rollback. They are additive,
   nullable, and ignored by the old path.
3. Keep source generations and usage audit data for runs created while enabled;
   mark them inactive/feature-disabled rather than deleting them.
4. Preserve `notes_cells`, current merged workbooks, and existing downloads.
5. Recompute API responses using current `run_notes_inventory`,
   `notes_cell_provenance`, and `notes_coverage_rows`; new runs show the old
   banner/coverage semantics.
6. If a rollout phase changes prompts/contracts, revert that phase as one unit
   with its tests; do not mix link-only prompts with a content-authoring writer.
7. If decorator behavior changed, revert both mTool/clipboard twins together.
8. After rollback, run schema compatibility, notes rerun, reviewer, formatter,
   download, clipboard, and mTool smoke tests.

## 24. Recommended delivery slices

For reviewable pull requests without claiming premature end-to-end completion:

1. Models, schema, and atomic source generations.
2. Text PDF/DOCX manifests and source-note ownership.
3. Vision/hybrid manifests and fail-closed uncertainty.
4. Link-only Sheet-12/prose-policy mapping plus renderer.
5. Corporate Information/numeric structured receipts.
6. Integrity engine, targeted retry, and terminal status.
7. API and durable remediation tasks.
8. Notes workspace UI and PDF/DOCX source navigation.
9. Export/mTool preflight and loss reporting.
10. Observability, evaluation, documentation, and staged rollout.

Each slice must include migrations/contracts/tests relevant to that slice and
must keep the feature flag off until its end-to-end gates are satisfied.
