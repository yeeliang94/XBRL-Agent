# Implementation Plan: Notes Source Integrity — Build (rev 2)

**Overall Progress:** `~30%` — Phases 1, 2, 3 and gate 0.2 built and committed (3 commits on `main`, full suite green). Phases 4–11 outstanding; 5–11 blocked on the decisions listed under Status below.
**Design reference:** [`docs/PLAN-notes-source-integrity.md`](PLAN-notes-source-integrity.md) (the proposal; this file is the build order)
**Findings register:** see [Appendix A](#appendix-a--findings-register) — 10 review findings + 15 peer-review findings, each mapped to a decision
**Last Updated:** 2026-08-01

## Status

| Phase | State |
|---|---|
| 0.2 Word extraction audit | 🟩 Passed — see Step 0.2 |
| 0.1 / 0.3 / 0.4 / 0.5 / 0.6 | 🟥 Not run — 0.1 needs live model spend, 0.4 needs digital-PDF fixtures that do not exist in `data/`, 0.6 is a product decision |
| 1 Page image pipeline | 🟩 Built (`0587251`) |
| 2 Table review surface | 🟩 Built (`85d86cb`) |
| 3 Source model foundation | 🟩 Built (`7a3f76a`) — schema v35, inert |
| 4 Word manifest | 🟥 Buildable next; fixtures exist |
| 5 Lineage-preserving reviewer/editor | 🟥 Blocked on Step 0.6 (oversized-note path) |
| 6 Link-only mapping | 🟥 Blocked on Step 0.6 |
| 7 Integrity engine | 🟥 Blocked on Phase 6 |
| 8 Integrity UI | 🟥 Blocked on Phase 7 |
| 9 Export preflight | 🟥 Blocked on Phase 7; 9.2 needs Windows hardware |
| 10 PDF track | 🟥 Blocked on Step 0.4 — no digital PDF in the repo |
| 11 Rollout | 🟥 Blocked on staging + operator sign-off |

**Open decisions blocking work** (all need a person, not more code):
1. Data-retention approval for storing source content (checklist blocker).
2. The oversized-note path (Step 0.6) — gates Phases 5 and 6.
3. Engineer-week estimate.
4. Digital (non-scanned) PDF fixtures — both files in `data/` are 150 DPI scans.

> **Revision 2 (2026-08-01).** Peer review raised 15 findings against rev 1. All
> 15 were verified against the code and accepted. Four were factual errors in
> rev 1: the manifest source, the crop verification physics, the rollback table,
> and a contradiction between Word locators and PDF navigation. Two findings
> were raised from HIGH to blocker. Appendix A records every finding and where
> it landed.

> **Why this file and not `docs/PLAN.md`:** `docs/PLAN.md` is the mTool Fill
> Pipeline plan at 75%, with Phases 1, 3 and 5 still open. CLAUDE.md gotcha #28
> points to it by name. To rotate `PLAN.md` to this work, move the mTool plan to
> `docs/PLAN-mtool-fill-pipeline.md` and update gotcha #28 in the same commit.

## Summary

Make notes extraction prove that every part of the source notes section was
handled, rather than only proving that each note was placed somewhere. The
source document is divided into numbered items and locked before any agent sees
the template; agents return item numbers instead of prose; ordinary code builds
the output and counts what was used.

Two pieces of work that stand on their own are pulled to the front: the page
image pipeline, and a table-review surface in the Notes tab. Both improve the
product immediately and neither depends on the rest.

## Key Decisions

- **The manifest is built from `uploaded.docx`, never from `source.html`.**
  The sidecar is best-effort, is hard-cut at 8 MB
  ([docx_html.py:230](../ingest/docx_html.py:230)), and the per-note reader
  hard-cuts a single oversized block at 60,000 characters
  ([source_snippets.py:201](../notes/source_snippets.py:201)). Building a
  completeness manifest from a capped source would let content go missing before
  the check runs — the exact false-green this feature exists to prevent. The
  sidecar keeps its current job unchanged: bounding what the agent reads per
  tool call. Two consumers, two rules.

- **Word first, PDF second.** Word documents carry real structure. Both sample
  filings in `data/` are 150 DPI scans with no text layer and no vector data, so
  the text-PDF layout engine has no fixture in this repo.

- **Do not raise the render DPI.** Measured on
  `data/FINCO-Audited-Financial-Statement-2021.pdf` page 31: the embedded scan is
  1648 × 2336 px on an 11 × 15.58 in page, so the source is **150 DPI**. We
  render at `dpi=200` (2200 × 3117), which upsamples past the source. Providers
  then downscale a full page to a fixed budget — roughly 768 px on the short side
  for OpenAI high detail, 1568 px on the long edge for Anthropic. Raising DPI
  costs bytes and returns nothing.

- **The lever is area, not DPI.** The pixel budget is per image, so covering less
  page with it leaves more detail after the provider's downscale. This is a
  hypothesis to be measured in Phase 0, not an assumption to be coded.

- **The DPI change is scoped to the notes vision path only.**
  `render_pages_to_png_bytes` is shared with scout, the reviewer and the
  formatter. "Native DPI" is undefined for a vector page carrying a small logo,
  so detection requires a dominant near-full-page raster by area coverage;
  anything else keeps the cap.

- **Formatting stays a separate channel.** The linker returns `format_ops`
  alongside its item numbers. Formatting carries no text, so the rule that the
  linker must not author content still holds, and
  [`notes/format_verify.py`](../notes/format_verify.py) already rejects anything
  in that channel that would alter text.

- **The flag is a mode, not a boolean.** `XBRL_NOTES_SOURCE_INTEGRITY` takes
  `off | shadow | enforce`. Shadow computes integrity and records it without
  changing run status. The effective mode is persisted on each run so a
  historical result stays explainable.

- **Terminal run status is immutable.** A run that finished
  `completed_with_errors` stays that way even after a user resolves every item.
  Current integrity state is exposed separately. Mutating a terminal status risks
  clearing an unrelated failure, which gotcha #10 forbids.

- **Free-form human editing stays allowed.** It is marked source-diverged at
  write time, atomically, not discovered later by a recompute.

- **No third ARIA tab.** The table and integrity surfaces are sections inside the
  Notes tab. A third `role="tab"` collides with `NotesSubTabBar` (gotcha #7).

- **Run tests with `./venv/bin/python -m pytest`.** The bare `python3` here is a
  stale 3.9 with pydantic-ai 0.8.1 and produces phantom import errors.

## Pre-Implementation Checklist

- [ ] 🟥 **Data retention and privacy approval for storing source content** —
      blocker. Phase 3 stores canonical source HTML per block and Phase 6 exposes
      it through agent tools. Decide: retention period, deletion rule, whether
      content is stored or only hashes plus locators.
- [ ] 🟥 **Engineer-week estimate per phase agreed** — rev 1 had none. Review
      finding 9.
- [ ] 🟥 **Oversized-note continuation mechanism decided** — see Phase 5. Without
      it, notes over 30,000 characters can never reach `complete`.
- [ ] 🟥 Review findings 1–10 and peer findings 1–15 accepted or overruled in
      writing (Appendix A)
- [ ] 🟥 ≥3 `.docx` filings sourced for the Word fixture set
- [ ] 🟥 ≥3 digital (non-scanned) PDFs sourced, ≥50 pages total, for the Phase 10
      fixture set — none exist in `data/` today
- [ ] 🟥 No conflicting work in flight on `notes/` — the mTool plan
      (`docs/PLAN.md`) touches `mtool/notes_decorate.py`, which Phase 9 reads
- [ ] 🟥 Agreement that unresolved source items produce `completed_with_errors`

---

## Phase 0: Evidence and gates

Nothing here changes production behaviour. Each step produces a number that
decides whether a later phase starts.

- [ ] 🟥 **Step 0.1: Measure what the model actually sees**
  - [ ] 🟥 Send one notes page at 150, 200 and 400 DPI to the configured model,
        asking it to describe the table's borders, alignment and totals rules.
  - [ ] 🟥 Repeat with the same table cropped, rendered at native DPI.
  - [ ] 🟥 Record tokens, latency and answer accuracy against the real page.
  - **Verify:** a result table. The hypothesis is that the three full-page DPI
    settings score the same and the crops score better. **If raising DPI does
    improve the answer, the Key Decision above is wrong — record that and change
    Phase 1 before building it.**

- [x] 🟩 **Step 0.2: Word extraction completeness audit** — peer finding 1. **PASSED 2026-08-01** on `data/FINCO-Audited-Financial-Statement-2021.docx`: 662/662 table cells, 21/21 tables, full body-text coverage. Merged cells, lists, footnotes, text boxes and headers/footers are ABSENT from this fixture, so they are untested rather than proven — a document using them still needs this gate re-run.
  - [ ] 🟥 On the `.docx` fixtures, compare what `mammoth` produces against the
        document: body paragraphs, table cells, **merged cells**, lists,
        **footnotes**, **text boxes**, **headers and footers**, and content after
        the last note.
  - [ ] 🟥 Record what is dropped. `ingest/docx_html.py` contains no handling for
        headers, footers or text boxes, so assume these are absent until proven
        otherwise.
  - [ ] 🟥 Decide per construct: extract it, or declare it out of scope and make
        its absence a recorded exclusion rather than a silent gap.
  - **Verify:** a construct-by-construct table with a decision against each.
    **Gate:** any construct that carries disclosure content and cannot be
    extracted blocks Word-first until it is either handled or explicitly excluded.

- [ ] 🟥 **Step 0.3: Word block splitting accuracy**
  - [ ] 🟥 Run the block splitting logic over the fixtures; count blocks, tables
        and note boundaries against a hand annotation.
  - **Verify:** every boundary correct, every table found, no contents-page line
    treated as a note heading (the run-74 failure). **Gate:** must pass before
    Phase 4.

- [ ] 🟥 **Step 0.4: PDF layout reading, gated on omissions not accuracy** —
      peer finding 12.
  - [ ] 🟥 On ≥50 annotated pages across ≥3 digital PDFs, run
        `page.get_text("dict")` and `page.find_tables()`.
  - [ ] 🟥 Score **block recall** and **table-cell retention** separately.
  - [ ] 🟥 For every miss, record whether the pipeline could detect its own
        uncertainty about that region.
  - **Verify:** **Gate is zero false-green omissions, not a detection
    percentage.** A detector may score below 100% only if every missed or
    uncertain region becomes visibly unresolved. If a miss is undetectable,
    Phase 10 does not start and PDF filings stay on the current path.

- [ ] 🟥 **Step 0.5: Baseline the current product**
  - [ ] 🟥 On three filings record: notes with missing content (hand-counted),
        tables with wrong formatting, tokens per run, wall-clock per run.
  - **Verify:** a baseline table committed here. Every later phase reports
    against it.

- [ ] 🟥 **Step 0.6: Decide the oversized-note path** — peer finding 13.
  - [ ] 🟥 A note over `CELL_CHAR_LIMIT` (30,000 rendered chars,
        [writer.py:52](../notes/writer.py:52)) cannot be rendered whole. Choose:
        validated continuation rows, an approved non-overlapping split, or
        gating link-only rendering to notes under the cap.
  - **Verify:** a written decision. **Gate:** Phase 6 does not start without it.

---

## Phase 1: Page image pipeline

Independent of the rest. Ships on its own.

- [x] 🟩 **Step 1.1: Native-resolution rendering, scoped to the notes vision path**
  - [x] 🟩 Detect a **dominant near-full-page raster** by area coverage. A page
        with no such image, or with several tiles, keeps the cap.
  - [x] 🟩 Render at `min(dominant_native_dpi, cap)`; cap defaults to 200.
  - [x] 🟩 Apply to the notes agent path only. Scout, reviewer and formatter keep
        current behaviour until this is proven.
  - [x] 🟩 Include the **render policy** in the page-cache and single-flight keys
        alongside dpi, so two policies cannot collide on one entry.
  - **Verify:** FINCO pages render 1648 px wide, not 2200; PNG size drops ~40%.
    A synthetic vector-page-with-logo fixture still renders at the cap.
    `./venv/bin/python -m pytest tests/test_pdf_viewer.py
    tests/test_page_cache_single_flight.py -q` passes.

- [x] 🟩 **Step 1.2: Cropped-region render**
  - [x] 🟩 Render a given rectangle at native DPI, returning PNG bytes.
  - [x] 🟩 Include the **normalised crop rectangle** in the cache key.
  - **Verify (mechanics only):** the returned image covers the requested region
    at the requested density, and two different rectangles do not share a cache
    entry. **Do not unit-test the effectiveness claim** — that is Step 0.1's
    experiment, and Phase 0 must be able to disprove it.

- [x] 🟩 **Step 1.3: Zoom tool for the notes agent**
  - [x] 🟩 Read-only tool taking a page and a region; returns the crop.
  - [x] 🟩 Prompt update: zoom into a table before recording its formatting.
  - [x] 🟩 Page hints stay soft — any page accepted, no allowed-page filtering
        (gotcha #13).
  - **Verify:** a FINCO run shows the tool called before `format_ops` are
    emitted. Compare `format_ops` quality against the Step 0.5 baseline.

- [x] 🟩 **Step 1.4: Formatting survives every downstream surface**
  - [x] 🟩 One note with a table through: editor preview, clipboard, workbook
        download, mTool fill. Compare borders, alignment, header rule and totals
        underline against the source page at each step.
  - **Verify:** the four agree. `./venv/bin/python -m pytest
    tests/test_notes_format_sidecar.py tests/test_mtool_notes_decorate.py -q` and
    `cd web && npx vitest run clipboard cellFormatting` pass.

- [x] 🟩 **Step 1.5: Report unstyled tables**
  - [x] 🟩 Count cells landing `style_source='unstyled'` per run; expose on the
        run record.
  - **Verify:** the count matches a hand count on a test run.

---

## Phase 2: Notes table review surface

Independent of the rest. Ships on its own.

- [x] 🟩 **Step 2.1: Tables index endpoint** — peer finding 7.
  - [x] 🟩 Return a **stable identifier** per table: `(sheet, row, table_index)`,
        plus nesting depth.
  - [x] 🟩 **Per-table** style facts derived from the cell HTML: does this table
        carry `data-source-styled`, does it carry inline borders, is it plain.
  - [x] 🟩 Row and column counts, character length.
  - [x] 🟩 Source pages labelled explicitly as **cell-level evidence**, not table
        provenance — `notes_cells.source_pages` and `style_source` are one per
        cell, not per table. True per-table provenance arrives with Phase 4.
  - **Verify:** a cell containing three tables returns three entries with
    distinct identifiers and independently derived style facts, and one shared
    page list marked cell-level.

- [x] 🟩 **Step 2.2: Tables list in the Notes tab**
  - [x] 🟩 A section inside the Notes tab, not a `role="tab"`.
  - [x] 🟩 One row per table: note, size, style state, page evidence.
  - [x] 🟩 Filter to "needs attention": plain, oversized, single-column, ragged.
  - [x] 🟩 Inline styles, tokens from `web/src/lib/theme.ts` (gotcha #7).
  - **Verify:** renders on a completed run; the plain filter matches Step 1.5.

- [x] 🟩 **Step 2.3: Side-by-side table and source**
  - [x] 🟩 Selecting a table shows it beside `PdfSourcePane` at the cited page.
  - [x] 🟩 Selecting focuses the cell; it does not replace the editor.
  - **Verify:** selection moves the PDF pane. Keyboard navigation works, focus
    is visible.

- [x] 🟩 **Step 2.4: Advisory sanity checks**
  - [x] 🟩 Ragged column counts, single-row tables, numeric columns with no
        numbers, tables over the cap. Display only. No blocking, no auto-fix.
  - **Verify:** each fires on a fixture and stays quiet on a clean run.

- [x] 🟩 **Step 2.5: States and responsive behaviour**
  - [x] 🟩 Loading, empty, error, run-in-progress, no-tables, narrow viewport,
        screen-reader labels, visible focus, reduced motion.
  - **Verify:** `cd web && npx vitest run` passes; manual check at three widths.

---

## Phase 3: Source model foundation

First flagged phase. No behaviour change in `off`.

- [x] 🟩 **Step 3.1: Schema**
  - [x] 🟩 Tables for source generations, source notes, source blocks, block
        usages and integrity results. Allocate version numbers at implementation
        time — committed version is 34 and may move.
  - [x] 🟩 An **append-only disposition event table** — peer finding 11. A
        mutable `created_by` column is not an audit trail.
  - [x] 🟩 Nullable provenance columns on `notes_cells`.
  - [x] 🟩 Every new column nullable or defaulted (gotcha #11).
  - **Verify:** fresh database initialises; a copy of a v34 database migrates
    forward; old code reads it unchanged. New `tests/test_db_schema_vN.py`.

- [x] 🟩 **Step 3.2: Typed models and repository helpers**
  - **Verify:** unit tests; invalid reason codes and dispositions rejected.

- [x] 🟩 **Step 3.3: Atomic activation**
  - [x] 🟩 One transaction activates the new generation and supersedes the old. A
        failed build leaves the previous one active.
  - **Verify:** a test that kills the build midway leaves exactly one active
    generation and no orphan blocks.

- [x] 🟩 **Step 3.4: Keep writing what the UI reads** — peer finding 8. Rev 1 was
      wrong here.
  - [x] 🟩 The Notes coverage endpoint reads **`notes_coverage_rows`**
        ([repository.py:1697](../db/repository.py:1697)), written from
        [server.py:1844](../server.py:1844) — not `run_notes_inventory` or
        `notes_cell_provenance`. The new path must keep populating
        `notes_coverage_rows`, plus the legacy provenance and inventory rows.
  - [x] 🟩 Write an explicit old/new coverage matrix: which writer owns which
        table in each mode.
  - **Verify:** a run in `enforce` produces both new and legacy rows. Switching
    to `off` shows a populated Notes tab for that run. Tests for all three modes
    and for rollback of a run created under the new path.

- [x] 🟩 **Step 3.5: Mode plumbing** — peer finding 9.
  - [x] 🟩 `off | shadow | enforce`; persist the effective mode on the run row.
  - **Verify:** a shadow-mode run records integrity and leaves status unchanged.

---

## Phase 4: Word manifest and boundaries

- [ ] 🟥 **Step 4.1: Build blocks from `uploaded.docx`** — peer finding 1.
  - [ ] 🟥 Read the **original .docx**, uncapped. `mammoth` is the only docx
        reader available (`python-docx` was removed, gotcha #26), so either drive
        mammoth directly or read the package XML with `zipfile`.
  - [ ] 🟥 Record the source checksum, the extractor version, and any construct
        skipped under a Step 0.2 decision.
  - [ ] 🟥 **Extraction failure or truncation fails the generation.** It must
        never produce a short manifest that then reports complete.
  - [ ] 🟥 Reuse the block-splitting *logic* from `notes/source_snippets.py`; do
        not source the manifest from `source.html`.
  - [ ] 🟥 Stable DOM-order locator, reading order, content hash per block. Link
        tables split across blocks into one table group.
  - **Verify:** on the Step 0.3 fixtures every block has a locator and an owner,
    and the block count matches the hand annotation. A fixture that trips the
    8 MB sidecar cap still produces a complete manifest. A fixture with a
    deliberately unreadable part fails the generation rather than shortening it.

- [ ] 🟥 **Step 4.2: Assign blocks to notes**
  - [ ] 🟥 Compare the block-structure reading against the scout inventory; flag
        disagreements rather than picking silently.
  - [ ] 🟥 Classify repeated headers, footers and contents-page lines as
        furniture.
  - [ ] 🟥 Detect missing leading and trailing notes, not only internal gaps.
  - **Verify:** every boundary matches the annotation; a contents page produces
    no false boundaries; a document missing its first note is flagged.

- [ ] 🟥 **Step 4.3: Freeze and report**
  - **Verify:** the run page shows the reading stage and the final counts.

- [ ] 🟥 **Step 4.4: Boundary accuracy is measured *and* gating** — peer
      finding 3. Rev 1 only measured it.
  - [ ] 🟥 Record boundary precision and recall separately from completeness.
  - [ ] 🟥 **Unresolved detector disagreement tips the run**, the same way an
        unresolved block does.
  - [ ] 🟥 Gate on *unresolved disagreement* first. Add a confidence threshold
        only after Phase 11 produces a distribution — a number picked now would
        likely tip most runs.
  - **Verify:** a fixture with a mis-assigned block shows 100% completeness, a
    boundary error, **and cannot finish `completed`**.

---

## Phase 5: Lineage-preserving reviewer and editor

New in rev 2 — peer finding 2. Must land before link-only rendering, or content
and lineage diverge the moment the reviewer runs.

- [ ] 🟥 **Step 5.1: Reviewer relinks instead of authoring**
  - [ ] 🟥 `edit_note_cells` today replaces cell bodies with authored prose
        ([reviewer_agent.py:1048](../notes/reviewer_agent.py:1048)). Add
        relink, route and disposition tools; retire body-replacement for prose
        cells in `enforce` mode.
  - **Verify:** in `enforce`, the reviewer cannot replace prose. Its fixes appear
    as changed block links. In `off`, current behaviour is unchanged.

- [ ] 🟥 **Step 5.2: Editor PATCH updates lineage atomically**
  - [ ] 🟥 In one transaction, `upsert_notes_cell`
        ([api/notes.py:419](../api/notes.py:419)) also writes the current hash,
        `human_modified` state, divergence timestamp and audit actor, and queues
        the integrity recompute.
  - [ ] 🟥 Optimistic version check so two editors cannot silently overwrite.
  - **Verify:** an edit marks the cell diverged in the same transaction. A stale
    version is rejected with a clear message. Source blocks are never modified.

- [ ] 🟥 **Step 5.3: Compare and restore**
  - [ ] 🟥 Show the human version against the source-rendered version; restore on
        confirmation, keeping the audit history.
  - **Verify:** restore returns the source render and leaves the event history
    intact.

- [ ] 🟥 **Step 5.4: Continuation mechanism for oversized notes** — peer
      finding 13, decided in Step 0.6.
  - [ ] 🟥 Implement the chosen path. Reporting permanent truncation is not a
        completion path.
  - **Verify:** a note above the cap reaches a defined terminal state that is
    either `complete` through continuation, or visibly unresolved — never
    silently short.

---

## Phase 6: Link-only mapping and deterministic rendering

- [ ] 🟥 **Step 6.1: Read-only source tools** — peer finding 10.
  - [ ] 🟥 List source notes, read a note manifest, view blocks, view any page.
  - [ ] 🟥 **Preserve the untrusted-content framing** already used by
        `read_source_note` ([agent.py:1582](../notes/agent.py:1582)):
        delimiters, and an explicit instruction to treat embedded text as data.
  - [ ] 🟥 Cap every tool response in bytes; a `view_source_blocks` returning
        unbounded content recreates the problem the 60,000-char cap solves.
  - **Verify:** each tool returns only blocks from this run's active generation;
    a foreign block ID is rejected; a document containing tool-like instructions
    does not change agent behaviour. Response caps enforced by test.

- [ ] 🟥 **Step 6.2: Write contract for prose notes**
  - [ ] 🟥 The agent returns destination row, block IDs and optional
        `format_ops`. Prose content is rejected.
  - [ ] 🟥 Validate every ID in code against the active generation and the
        template row catalogue.
  - **Verify:** prose payloads and fabricated IDs are refused with clear
    messages.

- [ ] 🟥 **Step 6.3: Deterministic renderer**
  - [ ] 🟥 Build the cell from blocks in reading order; rejoin table groups.
  - [ ] 🟥 Apply `format_ops` through the existing gate; invalid ops degrade to
        plain and never block the write.
  - [ ] 🟥 Preserve verbatim Word table markup and `data-source-styled`
        (gotcha #16).
  - **Verify:** the same block links render identically on two runs; rendered
    text matches the source exactly.

- [ ] 🟥 **Step 6.4: Structured sheets keep receipts**
  - [ ] 🟥 Corporate Information, Issued Capital and Related Party writes carry a
        block reference per value.
  - [ ] 🟥 Per review finding 7, do **not** require every unused block on those
        sheets to be individually dispositioned; one note-level statement is
        enough.
  - **Verify:** every written value traces to a block; the Corporate Information
    review queue does not grow.

---

## Phase 7: Integrity engine and run status

- [ ] 🟥 **Step 7.1: The checks, as pure versioned functions**
  - [ ] 🟥 Page receipts; one owner per block; valid disposition; full coverage
        for prose notes; whole table groups; continuity or an explained gap;
        approved duplicates; render matches the selected blocks; nothing lost to
        the character cap.
  - [ ] 🟥 **Boundary checks are part of this list** — unresolved disagreement,
        missing leading or trailing note.
  - **Verify:** each check has a failing fixture and a passing one.

- [ ] 🟥 **Step 7.2: One targeted retry**
  - [ ] 🟥 Return the exact missing block IDs; the retry fills only those. No
        second retry.
  - **Verify:** a two-block gap repairs in one retry; an unrepairable fixture
    ends needs-review, not a loop.

- [ ] 🟥 **Step 7.3: Run status**
  - [ ] 🟥 Unresolved items tip to `completed_with_errors` through the existing
        status block at [server.py:5849](../server.py:5849) — no second writer
        (gotcha #10).
  - [ ] 🟥 Terminal status is immutable after the run ends. Post-run resolution
        updates a separate current-integrity field — peer finding 14.
  - [ ] 🟥 Partial output stays downloadable.
  - **Verify:** one unresolved block prevents `completed` and the workbook still
    downloads. Resolving it post-run changes the integrity state and leaves the
    terminal status alone. `tests/test_server_run_lifecycle.py -q` passes.

- [ ] 🟥 **Step 7.4: Recompute after every change**
  - **Verify:** a manual disposition change updates the counts without a rerun.

---

## Phase 8: Integrity in the Notes tab

Extends the Phase 2 surface. Does not add a second one.

- [ ] 🟥 **Step 8.1: Coverage summary and per-note status**
  - [ ] 🟥 One status per note plus counts. Plain text alongside any colour.
  - [ ] 🟥 Per review finding 6, present a single status per note; the older
        placed/missing/skipped wording is retired after rollout, not shown
        alongside.
  - **Verify:** counts match the API. Component test.

- [ ] 🟥 **Step 8.2: Item list per note, navigating by input type** — peer
      finding 4. Rev 1 promised PDF navigation for Word blocks, which is not
      possible: `ingest/word_convert.py` produces a separate PDF with no
      DOM-to-page map.
  - [ ] 🟥 **Word runs:** items open a source-HTML preview at the DOM locator.
  - [ ] 🟥 **PDF runs:** items open `PdfSourcePane` at the page and highlight the
        region.
  - [ ] 🟥 For Word runs, PDF page citations remain secondary, cell-level
        evidence.
  - **Verify:** a Word run navigates in the HTML preview; a PDF run highlights
    the region. Neither shows a navigation control it cannot honour.

- [ ] 🟥 **Step 8.3: Manual fixes as a durable, guarded task** — peer finding 11.
  - [ ] 🟥 Attach an item, mark furniture with a reason from the fixed list,
        route, retry a note.
  - [ ] 🟥 A durable task row interlocking with the reviewer, formatter and
        rerun, following the existing pattern at
        [server.py:5381](../server.py:5381).
  - [ ] 🟥 Startup reconciliation for tasks left running by a crash, mirroring
        `reconcile_stale_review_tasks`.
  - [ ] 🟥 Optimistic version checks; every change written to the append-only
        event table.
  - [ ] 🟥 No generic dismiss. Each change previews its effect on the counts.
  - **Verify:** a remediation cannot start while a reviewer pass is running; a
    killed task is reconciled at startup; the event history shows every change.

- [ ] 🟥 **Step 8.4: Provenance and legacy states**
  - [ ] 🟥 Content provenance shown separately from style provenance.
  - [ ] 🟥 Pre-feature runs display as legacy without inventing item data.
  - **Verify:** an old run renders correctly in all three modes.

---

## Phase 9: Export and mTool preflight

- [ ] 🟥 **Step 9.1: Check before download and before mTool fill**
  - [ ] 🟥 Report loss from the size ladder rather than degrading silently.
  - [ ] 🟥 Do not change `mtool/notes_decorate.py` or `web/src/lib/clipboard.ts`
        unless required; if either changes, both change together (gotcha #16).
  - **Verify:** a note that cannot be retained reports the loss.
    `tests/test_mtool_notes_exporter.py -q` passes.

- [ ] 🟥 **Step 9.2: Windows operator gate**
  - **Verify:** one complete note through mTool Validate and Generate, operator
    confirms no text or table loss.

---

## Phase 10: PDF track — gated

**Does not start unless Step 0.4 met the zero-false-green criterion.** If it did
not, PDF filings stay on the current path and the feature ships Word-only.

- [ ] 🟥 **Step 10.1:** Digital PDF layout blocks — paragraphs, headings, tables,
      reading order, furniture, page receipts.
  - **Verify:** matches the Step 0.4 annotation.
- [ ] 🟥 **Step 10.2:** Independent region accounting — every area of every page
      is attributed or visibly unresolved.
  - **Verify:** a page with a deliberately undetected table reports an
    unaccounted region rather than passing.
- [ ] 🟥 **Step 10.3:** Multi-page table linking.
  - **Verify:** a three-page table produces one group.
- [ ] 🟥 **Step 10.4:** Scanned pages produce visible unresolved regions.
  - **Verify:** a low-quality page reaches review, never a clean finish.
- [ ] 🟥 **Step 10.5:** Confirm the scanned-filing outcome with operators —
      review finding 8. If scans will always need review, record it as a known
      limit.
  - **Verify:** written confirmation.

---

## Phase 11: Rollout

- [ ] 🟥 **Step 11.1:** Run `shadow` on staging; compare against current coverage
      and adjudicate every difference by hand.
  - **Verify:** a difference list with a decision against each.
- [ ] 🟥 **Step 11.2:** Calibrate the boundary-confidence threshold from the
      shadow distribution (deferred from Step 4.4).
  - **Verify:** a threshold with its false-positive rate recorded.
- [ ] 🟥 **Step 11.3:** `enforce` for internal users. If internal and external
      users share an environment, gate by user rather than by deployment.
  - **Verify:** review rate and run duration against the Step 0.5 baseline.
- [ ] 🟥 **Step 11.4:** Default on after the numbers hold for an agreed period.
  - **Verify:** sign-off recorded.
- [ ] 🟥 **Step 11.5:** Update CLAUDE.md invariants and `AGENTS.md` in the same
      commit as the default-on change. **Note:** rev 1 also named
      `docs/ARCHITECTURE.md` and `docs/SYNC-MATRIX.md`; neither exists in this
      repo. See Appendix B.

---

## Rollback Plan

**Phases 1 and 2** are unflagged. Revert the commits. The image change is scoped
to the notes path and the cache keys include the render policy, so stale entries
cannot leak. The tables surface is additive.

**Phases 3 to 9** are behind `XBRL_NOTES_SOURCE_INTEGRITY`:

1. Set the mode to `off`. New runs return to current behaviour.
2. Do not drop the new tables or columns. They are additive and nullable.
3. Keep source generations and audit records for runs created while enabled.
   Mark them inactive rather than deleting them.
4. Leave `notes_cells`, merged workbooks and existing downloads untouched.
5. The Notes tab reads **`notes_coverage_rows`**, which Step 3.4 has been
   populating throughout, so runs made while enabled still display. *(Rev 1
   named the wrong tables here.)*
6. If a phase changed agent contracts or prompts, revert that phase as one unit
   with its tests. Never run link-only prompts against a content-authoring
   writer.
7. After rollback run: schema migration, notes rerun, reviewer, formatter,
   download, clipboard and mTool smoke tests.

**Check after any rollback:** one completed run's Notes tab renders; one workbook
downloads; one mTool fill completes; `./venv/bin/python -m pytest tests/ -n auto`
is green.

---

## Appendix A — Findings register

**Review findings (2026-08-01, against the design proposal).**

| # | Finding | Landed |
|---|---|---|
| 1 | PDF layout is the largest task, shown as one line | Step 0.4 gate; Phase 10 gated; Word-first ordering |
| 2 | `format_ops` removed without mention | Key Decision: linker keeps `format_ops`; Step 6.3 |
| 3 | Completeness verified, attribution not | Step 4.4 — now gating, not only measured |
| 4 | Rollback leaves runs blank | Step 3.4 — corrected to `notes_coverage_rows` |
| 5 | One flag cannot cover the contract change | Key Decision: `off/shadow/enforce`; Step 3.5 |
| 6 | Two status vocabularies on one screen | Step 8.1 — one status per note, old wording retired |
| 7 | Corporate Information rule is busywork | Step 6.4 — receipts for written fields only |
| 8 | Scanned filings cannot meet the standard | Step 10.5 — operator confirmation required |
| 9 | No cost or effort estimate | Pre-implementation checklist — blocker |
| 10 | Storage grows with no clean-up | Pre-implementation checklist — retention approval, blocker |

**Peer-review findings (rev 1). All 15 verified against the code and accepted.**

| # | Finding | Severity | Landed |
|---|---|---|---|
| 1 | Word source not completeness-grade | Blocker | Key Decision; Steps 0.2, 4.1 |
| 2 | Reviewer and editor bypass lineage | Blocker | **New Phase 5** |
| 3 | Boundary error still finishes cleanly | Blocker | Steps 4.4, 7.1 |
| 4 | Word locators cannot drive PDF navigation | Blocker | Step 8.2 — split by input type |
| 5 | Crop verification physically wrong | High | Step 1.2 — mechanics only; benefit is Step 0.1 |
| 6 | Native-DPI detection can break other paths | High | Step 1.1 — dominant raster, notes path only, policy in cache key |
| 7 | Table API lacks table-level provenance | High | Step 2.1 — stable id, derived per-table style, pages marked cell-level |
| 8 | Coverage and rollback not integrated | High | Step 3.4; Rollback item 5 |
| 9 | One boolean cannot cover the rollout | High | Key Decision; Steps 3.5, 11.3 |
| 10 | Retention and injection controls missing | High | Checklist blocker; Step 6.1 |
| 11 | No durable task or audit design | High | Steps 3.1, 8.3 |
| 12 | PDF gate permits the target failure | **Raised to blocker** | Step 0.4 — zero false-green, ≥50 pages |
| 13 | Oversized notes have no completion path | **Raised to blocker** | Steps 0.6, 5.4 |
| 14 | Post-run status semantics unresolved | Medium | Key Decision; Step 7.3 — terminal status immutable |
| 15 | References not auditable | Medium | This appendix; Appendix B |

**Refinements applied to the peer recommendations, with reasons:**

- **#1** — the sidecar keeps its current job and its caps. Only the manifest gets
  an uncapped reader. Removing the caps would reintroduce the context problem
  they were built to solve.
- **#3** — gate on unresolved disagreement first. A confidence threshold chosen
  before calibration data exists would likely tip most runs; it arrives in
  Step 11.2.
- **#5** — the crop *mechanics* still get a unit test. Only the effectiveness
  claim is left to the experiment.
- **#7** — per-table style is derivable from the cell HTML today, so Phase 2
  still delivers real per-table information. Only page attribution waits for
  block provenance.
- **#12** — fixture size specified: ≥50 pages across ≥3 filings, scoring block
  recall and table-cell retention separately.

## Appendix B — Documentation references

Six of the thirteen `docs/` links in CLAUDE.md do not resolve. Five files do not
exist anywhere in the tree; one moved.

| Referenced | State |
|---|---|
| `docs/ARCHITECTURE.md` | Does not exist |
| `docs/SYNC-MATRIX.md` | Does not exist |
| `docs/PORTING-WINDOWS.md` | Does not exist |
| `docs/ADR-002-socie-dividend-sign.md` | Does not exist |
| `docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md` | Does not exist |
| `docs/NOTES-PIPELINE.md` | Moved to `docs/Archive/NOTES-PIPELINE.md` |

This misdirects context loading in every agent session and is unrelated to this
feature. Fixing the links is separate work and should not wait for this plan.

## Open Questions

1. Does Step 0.1 agree that DPI does not matter? If not, Phase 1 changes.
2. Do the digital PDFs meet the zero-false-green criterion? This decides whether
   Phase 10 happens at all.
3. Which oversized-note path is chosen in Step 0.6?
4. Is "scanned filings always need review" acceptable to operators?
5. What is the retention rule for superseded generations?
6. Which Word constructs are in scope after the Step 0.2 audit?
