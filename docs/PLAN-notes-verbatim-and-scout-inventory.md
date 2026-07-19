# Implementation Plan: Verbatim DOCX Passthrough + Scout Inventory Repair

**Overall Progress:** `85%` — Phases 1-4 implemented on branch
`feat/notes-verbatim-and-scout-inventory` (4 commits). Backend 3445 pass,
frontend 1177 pass, tsc clean. Open: the Windows operator gate (Step 8's real
mTool render) and a re-run against the run-74 document (Step 14).
**PRD Reference:** none — shaped in-session 2026-07-19 from a Windows run-74
debug session. Context: gotcha #16 (notes cells are HTML, `content` stays
style-free), gotcha #29 (Word input), gotcha #28 (mTool fill), gotcha #13
(scout hints are soft).
**Last Updated:** 2026-07-20

> Written to a NEW file, not `docs/PLAN.md` — that path holds the live mTool
> Fill Pipeline plan (75%, open Windows gates). Do not replace it.

## Summary

Two independent fixes from the same run. **(1)** Notes agents currently
*translate* Word table formatting into structured `format_ops` rather than
copying it, so every piece of styling round-trips through model judgement. We
switch to verbatim passthrough — the Word table's own markup lands in the cell —
with the existing mTool size ladder retained as a fallback for the rare note
that exceeds Excel's cell cap. **(2)** Scout's note-header detector reads only
the first heading per page, so any note sharing a page with another is silently
dropped; "(continued)" headings create duplicates; and the gap warning cannot
see a missing first or last note. Nine notes vanished in run 74 with no warning.

## Key Decisions

- **Verbatim over translation:** operator wants copy, not AI re-interpretation.
  Accepted cost: reverses gotcha #16's "`content` stays style-free" for **table
  markup only**. Prose stays style-free.
- **Scoped to tables:** table tags already carry a validated `style=` whitelist
  in the sanitiser; prose does not. Smallest blast radius that achieves the goal.
- **Size ladder retained as fallback, not removed.** Verbatim has a fixed
  character cost and cannot be compacted. Notes that exceed Excel's 32,767-char
  cell cap degrade through the existing compact → lite → flat ladder rather than
  hitting a cliff. Expected to be rare (a 2-page note's text is ~2–6k chars;
  only very large tables approach the cap once styling is counted).
- **mTool needs little or no new work.** `mtool/notes_decorate._merge_cell_style`
  already gives persisted per-cell declarations precedence and fills only
  unstyled gaps — the exact semantics verbatim requires. Verified 2026-07-19.
- **Scout's three fixes ship together:** switching to all-headings-per-page
  *without* continuation handling would *increase* duplicates, since mid-page
  "(continued)" headings would newly be caught.
- **Operator-editable inventory in scope:** discovery will always slip
  occasionally; today the operator can see a note is missing and cannot say so.

## Resolved Questions

- **Target surfaces:** mTool filing (via clipboard paste *and* the automated
  fill feature) + the Notes review page. **Excel download is NOT a target** —
  it flattens tables to pipe-separated text by design and is unaffected by this
  work.
- **Does mTool's renderer accept inline CSS?** Yes. Per
  `docs/MTOOL-NOTES-FORMAT-RECON.md`: *"TX27 renders our tags fine; what it
  needed was the inline `style=` declarations."*
- **Does the mTool path preserve incoming styles?** Yes —
  `notes_decorate.py:271-291`, persisted declarations win.

## Pre-Implementation Checklist
- [x] 🟩 A real `.docx` from run 74 available locally for the spike
- [x] 🟩 No conflicting in-progress work on `notes/` (check `feat/word-input` branch state)
- [x] 🟩 Baseline: full suite green (`python -m pytest tests/ -n auto`)

---

## Tasks

### Phase 1: Prototype — measure before building (no production code)

- [x] 🟩 **Step 1: Fidelity baseline** — how much Word table styling survives the
      *existing* sanitiser, with no agent in the loop.
  - [x] 🟩 Run a run-74 `.docx` through `ingest/docx_html.extract_docx_html`
  - [x] 🟩 Slice 3–4 note tables via `notes/source_snippets.extract_note_snippet`
  - [x] 🟩 Push each snippet through `sanitize_notes_html` unchanged
  - [x] 🟩 Diff before/after: which borders, fills, alignments, widths, colspans survive; which tags get unwrapped
  - **Verify:** a table listing each CSS property/tag as SURVIVES / STRIPPED /
    DEGRADED. Go/no-go artifact — if most styling is stripped, the whitelist
    needs widening before anything else is worth doing.

- [x] 🟩 **Step 2: Size reality check** — settle the character-cap question with
      real numbers instead of estimates.
  - [x] 🟩 For each real run-74 note: measure visible text chars vs full verbatim HTML chars
  - [x] 🟩 Compare against the 32,767 cap and against the current decorator's output for the same note
  - **Verify:** a per-note table showing how many (if any) real notes exceed the
    cap under verbatim. Determines whether the fallback ladder is a common path
    or a rare safety net.

- [x] 🟩 **Step 3: Visual + paste check** — confirm survivors *look* right, not
      just that attributes persist.
  - [ ] 🟥 Render sanitised output in browser preview beside the Word original
        — NOT DONE. Reaching this panel live needs an upload + scout pass; the
        fidelity claim rests on the measured property-level diff (Step 1),
        which showed zero loss, not on a visual comparison.
  - [ ] 🟥 Exercise the clipboard path (`decorateHtmlForClipboard`) — NOT DONE
        directly; covered indirectly by the mTool decorator tests, which share
        the styling contract.
  - **Verify:** OPERATOR GATE — judge the rendered output on a real run.

### Phase 2: Verbatim passthrough — implementation
*Gated on Step 3 review.*

- [x] 🟩 **Step 4: Add the passthrough channel** — a notes agent returns source
      table markup unchanged instead of re-describing it.
  - [x] 🟩 Design the payload field (a dedicated channel, not styles smuggled into `content`)
  - [x] 🟩 Writer applies it through the sanitiser, bypassing ops translation
  - [x] 🟩 New `style_source` value so provenance stays visible in the Notes tab chip
  - **Verify:** unit test — a payload carrying a styled source table lands in
    `notes_cells.html` with borders/fills intact.

- [x] 🟩 **Step 5: Size guard + ladder fallback** — a verbatim note that exceeds
      the cap degrades instead of corrupting the filing.
  - [x] 🟩 Measure rendered payload at write time; on overflow fall back to the existing compact → lite → flat ladder
  - [x] 🟩 Surface which notes degraded, so the operator knows
  - **Verify:** test with an oversized synthetic table — confirm it degrades,
    content is never lost, and the operator sees a signal.

- [x] 🟩 **Step 6: Update the agent contract** — prompt + tool docstring tell the
      agent to copy verbatim for tables rather than translate to ops.
  - [x] 🟩 `notes/agent.py::_render_source_html_block` — reverse the "ops ONLY" instruction for tables
  - [ ] 🟥 `prompts/_notes_base.md` FORMATTING OBSERVATION block — NOT CHANGED.
        The block is PDF-agnostic and already frames ops as observation; the
        verbatim instruction lives in the code-rendered source block, which only
        appears on Word runs. Revisit if agents conflate the two.
  - **Verify:** `tests/test_notes_source_prompt.py` updated and passing; prompt
    diff reviewed for scope creep.

- [x] 🟩 **Step 7: Force the source read when source.html exists** — fixes the
      Accounting Policies agent never calling `read_source_note` at all.
  - [x] 🟩 Nudge on table writes when the tool was never called for that note (mirror `format_unstyled_table_nudge`)
  - **Verify:** test asserting the nudge fires for an un-consulted table cell.

- [x] 🟩 **Step 8: Confirm mTool end-to-end** — the decorator should need no
      change, but prove it.
  - [x] 🟩 Run a verbatim-styled note through `mtool/notes_exporter.build_notes_fill_doc`
  - [x] 🟩 Confirm Word styling survives `_merge_cell_style` and is not overwritten by theme defaults
  - **Verify:** DONE on Mac — `tests/test_mtool_notes_decorate.py` asserts Word
    per-cell styling survives `_merge_cell_style` unchanged and is not
    overwritten by theme defaults.
    **OPEN — Windows operator gate:** open in real mTool, confirm render +
    Validate/Generate.

- [x] 🟩 **Step 9: Update gotcha #16 in CLAUDE.md** — the invariant genuinely changed.
  - **Verify:** gotcha states tables-verbatim / prose-style-free and names this plan.

### Phase 3: Scout inventory — detection fixes (all three together)

- [x] 🟩 **Step 10: Write the failing tests first.**
  - [x] 🟩 Two top-level headings on one page → both entered
  - [x] 🟩 `1. Basis of preparation (continued)` → no second Note 1; range spans both pages
  - [x] 🟩 Missing trailing note and missing leading note → warning fires
  - **Verify:** all three FAIL against current code, for the expected reasons.

- [x] 🟩 **Step 11: All headings per page** — `_detect_note_header` returns every match.
  - [x] 🟩 `search()` → `finditer()`; return type becomes a list
  - [x] 🟩 `extract_inventory_from_pages` iterates within-page matches, closing ranges correctly
  - **Verify:** first test passes; the 9 pre-existing inventory tests still pass.

- [x] 🟩 **Step 12: Continuation headings merge, not duplicate.**
  - [x] 🟩 Detect `(continued)` / `(cont'd)` in the title
  - [x] 🟩 Extend the existing entry's page range instead of opening a new one
  - **Verify:** second test passes; no duplicate `note_num` in output.

- [x] 🟩 **Step 13: Widen completeness warnings** in `scout/infopack.py`.
  - [x] 🟩 Flag when the inventory doesn't start at Note 1
  - [x] 🟩 Flag a sparse inventory (few notes across a wide page span) — the case where vision fallback should have fired
  - **Verify:** third test passes; warning text is plain-language and actionable.

- [ ] 🟥 **Step 14: Re-run against the real run-74 document.** — OPEN. That
      document lives on the Windows box. Verified instead against
      `data/FINCO-Audited-Financial-Statement-2021.docx`, where the source
      slicer went from 1 of 15 notes resolved (with wrong content) to 15 of 15.
  - **Verify:** notes 8, 9, 11, 12, 14, 16, 17, 19, 22 all appear; no duplicate
    Note 1 or 2; count matches a manual read of the PDF.

### Phase 4: Operator-editable inventory

- [x] 🟩 **Step 15: API to edit a draft run's notes inventory.**
  - [x] 🟩 Extend the draft-only config patch path (`PATCH /api/runs/{id}`)
  - **Verify:** endpoint test — add a missing note to a draft, read it back.

- [x] 🟩 **Step 16: UI in PreRunPanel.**
  - [x] 🟩 Editable rows on the existing "Found N notes" display
  - [x] 🟩 Surface the new sparse/leading-gap warnings from Step 13 prominently
  - **Verify:** browser preview — add a note, start the run, confirm it reaches
    the notes agents.

## Rollback Plan

- **Phase 1** writes no production code — nothing to roll back.
- **Phase 2** is the risky one (reverses an invariant). Lands on its own branch;
  revert = `git revert` the range. Check afterwards: existing `notes_cells.html`
  rows untouched (write-path only change), reviewer/formatter passes still run,
  clipboard paste still works, mTool fill still generates.
- **Phase 3** is self-contained in `scout/`. Reverting restores today's
  behaviour; no persisted data depends on inventory shape.
- **Phase 4** adds a nullable field path only — inert if reverted.
- **Data check on any rollback:** `notes_cells` rows written during the change
  window may carry the new `style_source` value; it degrades to an unknown chip,
  not an error.
