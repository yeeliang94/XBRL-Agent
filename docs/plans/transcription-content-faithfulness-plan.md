# Plan A — Transcription and content-faithfulness backend upgrade

Status: proposed implementation plan, 18 September 2026. Published at the user's explicit request; this is an exception to the default local-only working-plan policy. This document plans future work; no implementation or additional live runs have been started.

Normal defects should trigger targeted automatic repair through the existing workflow. No new warning dashboard, acknowledgement dialogs or dismiss-to-pass mechanism is proposed. Genuine unresolved failures use the existing terminal status. Preserve the original local documents, templates and baseline runs; never add them or their confidential content to Git. Test-company identity/date corrections are outside scope.

Read CLAUDE.md and the applicable invariants before implementation. Update maintained contracts, code and pinning tests together. Keep one canonical pipeline and existing atomic writes, revision checks and cancellation behavior. Read CURRENT_SCHEMA_VERSION at implementation time; do not hardcode the planning-time value. Any migrations must be sequential and idempotent. No new production dependency is proposed.

Companion scope: Plan B — Numeric extraction and MBRS hardening (maintained separately). This plan owns prose content, including the share-capital and related-party prose disclosures. Plan B owns their structured numeric facts.

## Outcome and existing foundation

Every source note, subsection, paragraph, list, table, caption and footnote reaches the correct destination without rewriting its wording or losing its meaningful structure and formatting. Capture and verify the source first; downstream agents choose destinations and source blocks rather than composing replacement prose.

Scanned-page transcription already exists in ingest/pdf_sidecar.py, called by server.py::_maybe_build_pdf_sidecar. Reuse and harden it; do not create a duplicate transcriber. It currently produces a best-effort source.html reading aid, uses Scout page suggestions, and was disabled in the three baseline runs. The required upgrade is independent capture verification plus integration with the existing source-block generation, placement and rendering machinery.

Reuse notes/source_models.py, source_repository.py, source_manifest.py, source_write.py, source_render.py, lineage and integrity modules. notes/pdf_layout.py is a separate gated digital-text reader; enabling it does not read image-only scans. Keep note-to-template matching model-judged; do not add keyword routing or an OCR engine.

Observed failures: the final checklist placed 13/14/20 top-level notes, but the second test company recovered Note 1 was abridged and eight the third test company policy subheadings were missing as headings. Placement counts alone are insufficient.

## Required content contract

1. Preserve wording, spelling, punctuation, capitalization, numbering, signs, dates, units, captions, list entries and all table cells. Do not repair source grammar or substitute equivalent prose.
2. Preserve heading hierarchy, paragraph/list boundaries, table grouping and source order within each destination. Policy routing may place sections into different fields, but must retain each section and its source heading path.
3. Allow only documented presentation changes required by the destination: line wrapping, styling, and representational escaping. Do not normalize meaningful whitespace within numbers, join uncertain hyphenated words, or discard repeated substantive paragraphs.
4. Running page numbers/headers and continuation furniture may be treated separately only with recorded source ownership; headings and disclosure footnotes are not furniture merely because they repeat.
5. Completeness is checked at page, note, subsection and content-block levels. A note citation, word count, hash, bounding box or source link alone does not prove faithful capture.
6. No automatic summarisation at size/token limits. Continue reading/capturing, and use supported continuation storage. If native storage cannot hold a complete note, preserve full source/canonical content and fail that export instead of shortening it.
7. Unreadable source content cannot be guaranteed by code. Retry focused source views; if still unreadable, use the existing failure path. Do not fabricate text or add a blanket warning to an otherwise successful result.

## Formatting preservation contract

Current scanned transcription explicitly removes visual styles. Amend that contract deliberately: capture heading levels/numbering, meaningful bold/italics/underlining, list hierarchy/indentation, table headings/grouping/merged cells, units and disclosure footnotes alongside source text. Preserve reading order and multi-page table relationships. Source numbers inside prose/tables remain exact transcription; numeric interpretation belongs to Plan B.

The MBRS renderer may apply supported destination typography, widths and layout while preserving content and meaningful emphasis. Do not promise a pixel-identical page reproduction. A later formatter may apply validated styling only; it cannot reconstruct missing words by invention or change numbers, text, cell geometry or structure. Geometry originates in verified capture. Coordinate any expanded formatting contract across the sanitizer, editor schema, review display and mTool/clipboard decorators; preserve the existing Word data-source-styled contract.

## Work packages

### A0. Preserve the baseline and establish complete source references

Keep original PDFs, templates, run traces and extracted notes unchanged. Independently transcribe/check every source notes page to establish local expected wording, heading trees, tables and permitted placements. Do not use the current AI output as the reference answer. Include the second test company's standards list, the third test company's eight missing headings, initial cross-sheet omissions and valid policy/disclosure splits.

Use synthetic or de-identified examples in tracked tests. Detailed baseline findings are retained locally and are not required to read this plan. Exact text, heading structure, placement and formatting are separate acceptance dimensions.

### A1. Capture complete source content before notes mapping

Reuse notes/source_models.py, source_repository.py, source_manifest.py and the existing generation lifecycle. Retain the original Word structure for DOCX; use the existing digital-PDF path only where its evidence is adequate. Extend the existing ingest/pdf_sidecar.py visual transcription into the same verified block model, using the configured model (Luna for evaluation here), without adding an OCR engine.

Record every processed page and ordered region, note boundary, heading path, paragraph, list, table/caption/footnote and cross-page continuation. Use stable block identities within a source generation plus page/region evidence. Capture unnumbered notes, nonconsecutive numbering, appendices within the notes and mixed digital/scanned pages. Scout is advisory; it must not restrict which pages can be read or define completeness by itself.

Use bounded page batches with explicit continuation receipts so response truncation cannot activate a partial generation. Validate reading order and cross-page tables. A second visual verification pass inspects the original page, including margins and small footnotes, rather than only inspecting the first pass's block list. Repair omissions and transcription disagreements before freezing the source generation. Only a complete verified generation becomes active; failures preserve the prior generation.

Do not claim visual verification mathematically proves exactness on arbitrary scans. The deliverable is independent source checking plus elimination of subsequent rewriting, assessed on reviewed fixtures.

Exit: all source notes pages processed, no unresolved capture region or boundary disagreement; exact content/structure matches reviewed fixtures, including deliberately omitted first/last paragraphs and table footnotes.

### A2. Make notes agents select source content rather than rewrite it

Extend the existing read_source_manifest/write_note_from_source path in notes/agent.py, source_write.py and source_render.py. Agents decide which MBRS row receives which source blocks. Ordinary code renders the stored content in source order. Keep the canonical notes_cells store and existing transactional lineage/placement behavior.

Build each cell with its actual source heading ancestors. Require the relevant subheading when selecting a subsection; do not blindly prepend the same heading twice when it already exists as a source block. Support nested letters/roman numerals, several source subsections in one valid field, and tables split across pages.

Preserve current routing: one complete top-level disclosure in one List-of-Notes field, explicit policy carve-outs to policy fields, corporate information to its field, and intentional full-note/numeric-note prose duplication. Every allowed duplicate has the established purpose and remains source-identical. Related-party and capital numeric extraction must not consume/remove their prose disclosure. Extend source-backed prose writes on numeric-note sheets separately from numeric facts; the current source tools exclude those templates.

For this complete-preservation workflow, a missing block cannot silently switch to free prose authoring. It goes back to capture/repair, then renders from verified blocks. Preserve explicit user editing capability with existing revisions; automatic extraction/review must not overwrite user changes silently or certify them source-identical without comparison.

Exit: deleting a paragraph or subheading from an agent's selection is detected and repaired; adding or paraphrasing content is rejected; formatting cannot change text or table structure.

### A3. Finish the existing completeness ledger and automatic repair loop

Extend coverage_checklist.py and integrity.py/runner, retaining one authority for run status. The source generation is the denominator. For each page/note/subsection/block, track capture, verification, required destinations and actual persisted placements. Inspect live destination cells, not only receipts.

A note is complete only when all its required blocks and heading paths are correctly rendered. Cross-sheet skips resolve only after the receiving placement exists (the current checklist already checks provenance; extend that protection to exact blocks). Numbering gaps trigger source inspection, not invented notes. Unnumbered disclosures must also be accounted for.

Internal repair queue items contain exact source locations and missing/different blocks. Re-read or relink only affected content. Recompute integrity after each repair and after all reviewer/formatter writes. Resume from durable state after interruption; stale repairs cannot overwrite newer revisions. Use bounded attempts and existing below-default agent request caps, rather than one indefinitely growing conversation.

Do not settle substantive omissions as not_applicable or a generic exclusion. Preserve existing permitted furniture exclusions. Incomplete assessment is not completion. Stop after genuine unresolved failure through the existing terminal-state mechanism; do not add routine confirmation dialogs or dismiss-to-pass buttons.

Exit: deliberately dropped blocks, orphaned placements, stale revisions, overwritten cells and incomplete source manifests cannot produce successful completion. Healthy documents finish without user intervention.

### A4. Make reviewer behavior source-preserving

Update prompts/_notes_base.md, notes_listofnotes.md, notes_reviewer.md and reviewer tools/contracts together. Replace ambiguous 'verbatim where possible' with the exact content contract. Reviewer corrections on source-backed cells select/relink verified source blocks; they do not compose a summary. Capture corrections create a new verified source revision with history.

Subheading and content verification becomes required work, not spare-turn/advisory work. Limit repairs to the defective note, preserving unaffected blocks. Legitimate policy/disclosure routing should satisfy the deterministic placement rules, not generate recurring duplicate questions.

Exit: reviewer-restored the second test company Note 1 retains its full standards list; the third test company headings survive; repairing a missing paragraph cannot remove an adjacent table or replace untouched wording.

### A5. Integrate document preparation and verify prose export

Retain the normal coordinator, progress queue and terminal-status owner. Connect verified scanned source generations to notes extraction and reviewer tools. Simply enabling the present sidecar/integrity setting does not provide this guarantee. A preparation stage leaving the active state is not proof it completed successfully.

After formatter, editor/download overlay and native notes insertion, compare the actual rendered content with the verified source-backed canonical notes. Check text, heading hierarchy, lists, table structure and supported emphasis. No truncation or silent summarisation at workbook limits. Native numeric/formula checks belong to Plan B; prose equivalence is owned here.

Keep the existing screen flow: Document preparation contains Scout, then Source preparation. Scout finds locations and suggests an inventory. Source preparation transcribes, verifies, repairs and freezes source content. Show real page/note progress inside the existing panel. Notes agents then place verified content; existing Checks run completeness and repairs. Filling the native MBRS workbook remains a later user action. No additional routine user step is introduced.

Exit: complete verified notes survive capture, mapping, reviewer, formatting and native export; interrupted/retried runs preserve source generations and revisions.

## Delivery order

A0 reviewed reference → A1 capture and verification → A2 deterministic assembly and formatting → A3 completeness and repairs → A4 source-preserving reviewer → A5 integration and prose-export acceptance. Implement the ledger and renderer tests early enough to catch capture/integration mistakes. Release only when scanned capture and its checks work, not by relaxing completeness criteria.

## Verification and acceptance

Cover DOCX, digital/scanned/mixed PDFs, no TOC, unnumbered/gapped notes, nested headings, multi-page tables, small footnotes, repeated substantive paragraphs, policy carve-outs, intentional capital/related-party prose duplication, oversized notes, missing final pages, truncated model output and unreadable regions. Test dropped/substituted/reordered content and emphasis, incomplete generations, orphaned placements, stale revisions, reviewer repairs, cancellations and restart/concurrent isolation. A cited subsection must not count as verified text; a source hash must not count as proof of correct transcription.

Run focused serial source manifest/render/write/lineage/integrity/false-green, coverage, reviewer, PDF-sidecar/layout, notes-export and formatting tests and all pinning tests routed by the relevant invariants. For this cross-cutting implementation run the full backend suite after focused passes. Regenerate the prompt audit and run its matching test for prompt changes. Run affected Vitest tests and the frontend build if shared types/status/formatting consumers change.

Acceptance requires exact source-reference agreement for wording, headings and table content, supported formatting preservation, no missing disclosure block, automatic repair of seeded defects and equivalent final workbook prose. Visual transcription remains fallible; independent checks and reviewed test references are required, rather than claiming a universal guarantee from two model passes.

## Shared boundary and final acceptance

The two plans share the existing canonical pipeline, source evidence, run IDs, revision/snapshot contracts and atomic export. Source block identifiers remain optional evidence for numeric agents until Plan A supplies them; Plan B must not require the new transcription path to fix category storage or SOCIE mapping. A source-backed prose disclosure does not replace a structured numeric fact, and a numeric fact does not satisfy prose completeness.

Plan A owns the content of capital/related-party prose cells and all notes rendering. Plan B owns their numeric category/period identities and native numeric destinations. Changes to the shared notes payload/writer need coordinated contracts and combined tests. Coordinate schema migrations at integration time; do not assign competing future schema versions in advance. Verify numeric values/formulas and prose preservation together after the final workbook write.

Use only GPT-5.6 Luna for all model roles in the planned live evaluation. Each plan may be validated locally on its own. The proposed final joint acceptance is three independent runs per local document (nine total), not nine runs separately for each plan. Broader synthetic/de-identified counterexamples supplement these runs. Compare with independently checked references, and record time, cost and repair counts. These paid runs are proposed, not started by this document split; settle execution scope when implementation reaches acceptance.

A successful numeric-only milestone must not be described as verified complete notes, and successful notes preservation must not imply that numeric exports are fixed. The combined workflow is complete only when both plans pass their respective acceptance criteria in the same final artifacts.

## Publication scope

This published copy contains only the proposed Plan A work. Local financial statements, templates, extracted content, baseline artifacts and the companion working plan are not included. Company references are anonymised and machine-specific links removed. Publication does not implement or validate the proposed product changes.
