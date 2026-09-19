# Plan A — Automatic document preparation and content-faithfulness upgrade

Status: implementation in progress, 18 September 2026. The automatic preparation, source-backed notes, review safeguards and progress UI are implemented locally. The approved concurrency-ten refactor passed a fresh 37-page live preparation/Scout/manifest test in 213 seconds, retaining all 15 expected notes and five primary-statement entries. Page preparation took 152 seconds and the combined inventory/ownership map about 61 seconds. This is one measured document, not a universal timing or independent transcription-accuracy guarantee. Offline verification passes. The user authorized ignoring administrative stamp text and using AI best readings for unclear source wording without blocking preparation. Comparative concurrency trials, independent reference comparison and full live extraction-to-export acceptance remain outstanding. Published and revised at the user's explicit request as an exception to the default local-only working-plan policy. The user separately authorized implementation and paid evaluation runs.

## Outcome and scope

Uploading a document automatically starts preparation. The application detects the input type and each page's needs, corrects page orientation in derived views, captures and verifies source content, and then runs Scout to produce the inventory. Extraction and review use that prepared source without making the user enable transcription, source integrity, rotation or Scout settings.

Every source note, subsection, paragraph, list, table, caption and footnote must reach its required destination without changing readable wording or losing meaningful structure. Administrative stamp text is optional. For unclear wording, preparation uses the AI’s best contextual reading and records the uncertainty with original-page evidence; those readings are not claimed as verified transcription. Agents choose destinations and verified source blocks; ordinary code renders those blocks. Placement counts alone cannot establish completeness.

The user-authorized PDF simplification of 19 September excludes printed page numbers and routine running headers/footers from transcribed HTML and Scout blocks. Record their source regions as page furniture without reproducing their text. Preserve cover identity, statement titles, reporting dates, units, note/subnote headings, captions and substantive footnotes regardless of page position. Independent verification uses the same exclusion policy; printed folios need not match the unchanged 1-based PDF page indexes. Native Word body content remains protected.

Preparation covers the uploaded document so it can precede Scout without depending on Scout's notes inventory. Full-document capture provides reading evidence; it does not expand Plan A into numeric interpretation or certify primary-statement numeric accuracy. Plan A owns notes prose, including capital and related-party prose. Companion Plan B owns structured numeric extraction, category/period identities and native numeric/formula correctness.

Normal defects trigger targeted automatic repair. Keep the existing screen flow and one clear status surface. Do not add warning dashboards, routine acknowledgement dialogs, dismiss-to-pass controls or technical notices about optional internals. A genuine unresolved failure remains visible through the existing terminal-status mechanism; reducing clutter must not hide failure or certify incomplete work.

Preserve original local documents, templates, baseline runs and confidential findings. Never add them to Git. Use synthetic or de-identified tracked fixtures. Test-company identity/date corrections are outside scope. No new production dependency or OCR engine is proposed.

## Existing foundation and required changes

Reuse ingest/pdf_sidecar.py and the existing notes/source_models.py, source_repository.py, source_manifest.py, source_write.py, source_render.py, lineage and integrity modules. Do not create a second transcriber or extraction pipeline.

At revision time, transcribe_pages already supports one page per request, a concurrency parameter and a default of four concurrent requests. Results can be assembled in page order. The current server's _pdf_sidecar_enabled() returns False; the legacy orchestration also depends on Scout inventory and rotation hints. Current transcription strips meaningful presentation markup. Merely changing a setting or increasing concurrency will not provide the proposed workflow.

notes/pdf_layout.py is a separate gated digital-text reader, not a reader for image-only scans. Existing source blocks include continuation relationships and table groups; these do not establish reliable scanned-paragraph continuation handling. Audit existing ownership, repeated-text exclusions and table-linking heuristics against the new contract rather than assuming they prove source correctness.

The existing source-preparation UI can infer completion from inactivity. Replace that inference with persisted explicit outcomes. Preparation progress must not depend on model reasoning summaries being available.

Before implementation, read CLAUDE.md and the routed invariants. Update maintained contracts, code and pinning tests together. Preserve one canonical pipeline, atomic writes, revisions, cancellation and terminal-status ownership. Read CURRENT_SCHEMA_VERSION at implementation time; migrations remain sequential and idempotent.

## User journey and ordering

1. **Upload.** Persist the original and establish a durable preparation attempt before starting background work. Return the user to the existing document screen immediately; do not hold the upload request open for transcription.
2. **Document preparation.** Automatically inspect the whole document, prepare readable page views, correct orientation, capture content, resolve cross-page structure, independently verify and repair it, then activate a complete source generation.
3. **Scout and inventory.** Scout uses the prepared content and original page evidence to discover statement locations, notes, heading relationships and advisory filing context. Reconcile its inventory against captured content before extraction becomes ready.
4. **Extraction.** Use the existing extraction-start interaction and required filing choices; do not add a separate preparation confirmation. Starting extraction while preparation is active waits on the same attempt, rather than bypassing or duplicating it. Do not silently auto-submit filing choices merely because an upload occurred.
5. **Review and Checks.** Run the existing reviewer/check workflow with source-preserving corrections, completeness accounting and targeted repairs. Recheck after formatting and other automatic writes.
6. **Review results and fill MBRS.** Native workbook filling remains a later user action. Verify prose preservation and Plan B's numeric checks against the same final artifact.

Preparation and Scout start automatically after a valid upload using the application's configured model/provider. This is normal processing under the application's existing account configuration, with no per-upload feature-enable prompt. If credentials or a required configuration are genuinely unavailable, show one actionable blocked state in the preparation panel; never pretend the document is ready. Installing or configuring the application is distinct from enabling this workflow for each document.

Reuse completed preparation only when the document, capture-contract version and relevant configuration match. Reuse the upload-owned Scout result for that same document/preparation revision; do not repeat the old run-owned Scout pass solely because extraction starts. Define invalidation for changed inputs, material configuration and explicit inventory edits. Update invariant 13 and its tests when this lifecycle changes; page hints remain advisory and never restrict source access.

## Automatic document preparation

### Detection and page orientation

Inspect every page, rather than classifying the whole PDF from a small sample. Distinguish digital text, scans, mixed text/image regions and blank pages. A text layer does not prove that all visible content was captured. Retain original DOCX structure; use the existing conversion path for page views without treating conversion as the complete content reference.

Page rotation is part of preparation. Account for PDF rotation metadata, sideways and upside-down scans, legitimate landscape tables and pages with differently oriented regions. Apply a recorded transformation to derived page views; preserve original bytes, stable page numbers and original-to-derived coordinate mappings. Avoid applying metadata rotation twice or rotating an already readable landscape table solely because it is wide. Scout, verification and extraction must use consistent page views and source coordinates.

Uncertain orientation triggers focused source inspection. Recheck readability after correction. Small-angle deskew is a separate concern: assess existing capabilities and fixtures before promising it. Do not crop away marginal disclosures, reduce legibility or introduce a dependency as an implicit part of rotation support.

### Capture, reconciliation and activation

Capture all pages using an appropriate native or visual path. Native text and structure require completeness checks too; visual capture handles image-only and inadequately represented regions. Record ordered page regions, paragraphs, headings, lists, tables, captions, footnotes, orientation and processing outcomes. Distinguish blank pages and permitted furniture with evidence.

Use bounded page requests with explicit completion/continuation receipts. Dense pages may use bounded overlapping regions, preserving exact source ownership so overlap cannot duplicate or omit text. Response truncation must not publish a partial page or generation. Preserve meaningful formatting during capture; a later formatter cannot reconstruct missing content or structure.

A second visual verification pass inspects original page evidence, including margins and small footnotes, rather than only the first pass's block list. It checks capture correctness and boundary relationships. Repair disagreements before freezing. Verification may start as pages become available, but generation activation waits for all required pages and boundaries to be checked. Explicit best-effort readings may proceed with retained uncertainty evidence.

Source preparation cannot depend on a Scout inventory that does not yet exist. It records observed headings and candidate structural relationships without making MBRS routing decisions. A single Scout document-map pass subsequently supplies both the semantic inventory and exact block ownership. Mechanical coverage checks then reconcile that map without another page-by-page model pass. Scout receives a compact block index and can inspect full blocks or any valid page. Any new gap discovered by Scout returns to targeted preparation and verification; extraction stays blocked until the revised source and inventory agree. Preserve the prior valid generation if replacement preparation fails, but do not silently use it for a changed document.

Visual checking does not mathematically prove exactness on arbitrary scans. Acceptance requires independently reviewed references and measured detection of seeded defects. Persistent unreadability does not block processing. After bounded attempts, the AI supplies its best contextual reading, retaining page evidence and uncertainty metadata. Administrative stamp characters may be omitted. Uncertainty must survive source generation and placement without being misreported as independently verified text. Operational failures, cancellation and invalid structural receipts remain explicit failures.

## Parallelism and latency experiment

Use a bounded queue of page requests, not independent long-running agent conversations. A free worker immediately takes the next pending page. For 30 pages and concurrency ten, up to ten pages are in flight; do not wait for a fixed batch's slowest page before scheduling more work. Assemble results by source page/region order, never completion order.

The approved default is **10 concurrent requests**, within the shared process-wide limit of ten. Combine orientation detection with capture, reuse page renders, and start adjacent-page checks as soon as both pages are ready. Use compact edge context; skip a boundary request only when capture and independent verification both explicitly rule out continuation on the relevant edges. Persist valid boundary and document-map results for retries.

Measure the refactored path using GPT-5.6 Luna against the same independently reviewed approximately 30-page document. A comparative experiment at 4, 6 and 10 remains useful for later tuning; it is not a user-facing setting or a prerequisite for applying the approved default. Ten is not a guaranteed fastest setting. Provider limits and concurrent documents must be considered.

Capture, verification and retries share an aggregate request budget. Bound both per-document concurrency and total provider traffic across documents, with a documented fairness/backpressure policy. Do not create separate uncoordinated ten-worker pools. Resume successful page work after interruption and retry only the failed or invalidated units. Keep cancellation, deadlines and below-default agent request caps effective throughout preparation.

Measure rendering/detection time, queue delay, per-page capture/verification latency, first usable page, all pages captured, verified source ready, and inventory ready. Record slow-page distribution, rate-limit errors, retries, peak memory, token usage, cost and exact content/structure accuracy. Repeat the compared conditions enough to distinguish a slow request from a consistent concurrency effect. Choose the lowest concurrency that meets the measured latency goal without degrading correctness or reliability; record the release target and evidence before rollout.

For illustration only, 30 equal 20-second requests at concurrency ten take approximately 60 seconds for capture alone. This excludes preparation, verification, boundary work and repairs and is not a product estimate. Show no user ETA until measurements support one. Benchmark requests and costs must be included in the agreed live-evaluation budget, not silently added to the final nine-run acceptance proposal.

## Source structure, continuations and routing units

There is no single universal chunk size. Keep processing units distinct from source ownership and output placement.

| Purpose | Unit and rule |
| --- | --- |
| Parallel capture | A page, or bounded overlapping regions for dense pages |
| Physical evidence | Page/region fragments with stable source locations |
| Logical source block | Complete paragraph, heading, list item or linked table; preserve captions and footnote relationships |
| Cross-page assembly | Links joining fragments of the same paragraph/list/table across any number of pages |
| Policy routing | Complete subsection with its actual heading ancestors; whole paragraphs where appropriate |
| Ordinary disclosure routing | Complete top-level note, subject to explicitly permitted policy carve-outs |

A page boundary is never automatically a paragraph, subsection or note boundary. After capture, inspect adjacent page endings and beginnings together. Link continued paragraphs, lists and tables; distinguish a new heading from a running header or repeated table heading. Extend the context window when two pages cannot resolve the relationship. A logical paragraph may retain multiple physical fragments without losing their page evidence or altering meaningful whitespace or hyphenation.

Record candidate relationships during capture, verify them across page boundaries, and reconcile final note/subsection ownership with Scout. Missing repeated note numbers do not break ownership. Repeated numbers do not alone establish it. Unnumbered notes, nonconsecutive numbering, appendices and long continuations must be supported. Unresolved boundaries trigger inspection, not arbitrary ownership guesses.

Selecting a logical paragraph includes all its verified fragments. Selecting part of a linked table includes the complete table group and associated required content. Render in source order with the relevant heading ancestry. Do not join unrelated tables merely because they have the same column count or discard repeated substantive text as furniture.

### Accounting policies inside a larger note

Keep source ownership separate from destination. A policy subsection remains part of its original source note even when placed in an accounting-policy field. The mapping agent makes the semantic routing decision; code validates and accounts for the exact blocks and their persisted destinations. Do not introduce keyword routing or synonym dictionaries.

For example, a source note containing revenue policy, leases policy and other disclosure text may place complete policy subsections in their respective policy fields and the remaining disclosure in its permitted notes destination. All required blocks remain accounted for across those cells, with their source heading paths. An absent policy subsection in the List-of-Notes cell is not an omission when its permitted receiving placement actually exists and is source-exact.

A generic cross-sheet skip or a receipt alone is insufficient. Check live receiving cells. Preserve intentional full-note/numeric-note prose duplication with an explicit permitted purpose and identical source content. Numeric extraction must not consume or remove capital/related-party prose.

Default to subsection or whole-paragraph selection, never arbitrary token windows or a summarised replacement. If one paragraph mixes policy topics, preserve it intact in an appropriate supported destination where possible. If a split is necessary, use an explicit verified source-span operation with stable parent identity, exact boundaries and complete accounting for all spans. If no faithful valid placement can be established, repair or fail; do not invent bridging prose. A span-based split must not bypass the one-top-level-disclosure-per-List-of-Notes-field rule.

## Content, formatting and editing contract

1. Preserve wording, spelling, punctuation, capitalization, numbering, signs, dates, units, captions, list entries and every table cell. Do not repair grammar or substitute equivalent prose.
2. Preserve heading hierarchy, paragraph/list boundaries, table grouping and source order within each destination. Include actual heading ancestors without blindly repeating a heading already selected as a block.
3. Allow only documented destination presentation changes such as wrapping, supported typography, widths and representational escaping. Preserve meaningful bold, italics, underlining, list indentation, merged cells and footnotes. Do not promise pixel-identical reproduction.
4. Record ownership for page furniture exclusions. Repeated headings, disclosure footnotes and substantive paragraphs cannot be discarded simply because they repeat.
5. Check page, note, subsection and block completeness. A citation, word count, hash, bounding box or source link is evidence, not proof of faithful capture.
6. Do not summarise at token or workbook limits. Continue capture in bounded units. Use continuation storage only where the actual destination supports it; preserve full canonical content and fail an unsupported export instead of truncating or splitting a note into forbidden rows.
7. A missing block returns to capture/repair, never silently to free prose authoring. Source-backed reviewer corrections relink verified blocks. Capture corrections create a verified revision with history.
8. Preserve explicit human editing with revisions. Automatic extraction, repair and review cannot silently overwrite human edits or label them source-identical without comparison.
9. Formatting is validated style-only work. It cannot change text, numbers, geometry, structure or placement. Coordinate the sanitizer, editor schema, review display, mTool and clipboard decorators while preserving the Word data-source-styled contract.

## Completeness and automatic repair

Extend the existing coverage checklist and integrity runner. The verified source generation is the denominator. Track each page/note/subsection/block through capture, verification, required placement and actual persisted output. Keep one authoritative run status.

Normal routing decisions, including permitted policy carve-outs, satisfy the same placement ledger. A note is complete only when its required content and heading paths are rendered at the permitted destinations. Numbering gaps cause inspection, not invented notes. Unnumbered disclosures also need ownership and disposition.

Repairs identify exact locations, missing/different blocks and expected revisions. Re-read or relink only affected content, preserve unaffected blocks, and recompute integrity after each repair and all reviewer/formatter writes. Resume from durable state. Reject stale repairs after concurrent edits. Bound attempts and escalate only a genuine unresolved failure through the existing status mechanism.

An incomplete assessment, orphaned placement, overwritten destination, unresolved boundary or stale generation cannot produce success. Do not settle substantive omissions as not_applicable or a generic exclusion. Healthy documents finish without additional user interaction.

## Progress UI and status contract

Use the existing document preparation area and activity timeline. Show document preparation before Scout, followed by extraction and the existing review/check stages. Do not add a second competing progress dashboard or ten worker tabs.

Display the active operation and factual counters, for example:

- “Checking 30 pages”
- “Checking page orientation — 18 of 30”
- “Reading pages — 12 of 30 captured”
- “Checking source content — 9 of 30 checked”
- “Joining paragraphs and tables across pages”
- “Rechecking page 17”
- “Document prepared”
- “Building notes inventory”

These are illustrative labels, not fabricated counters or required extra screens. Capture and verification can overlap; show separate counts in the same panel so captured does not imply verified. Do not display a notes-total denominator before it is established. Avoid a percentage for work with an unknown denominator.

Persist explicit queued, working, retrying, succeeded, failed and cancelled outcomes using the existing status architecture, with substages as needed. Inactivity, a disconnected stream or a stage transition does not imply completion. Emit typed stage/progress/outcome events through the existing queue; restore the latest durable snapshot on refresh or reconnect and reject stale attempt events. Show elapsed time and useful progress without relying on hidden reasoning text.

Routine rotation corrections, native/visual path selection and successful repairs are ordinary progress, not warning banners or toasts. Keep technical traces available through existing diagnostics without confidential text in logs. Remove obsolete normal-path notices about disabled transcription, missing reasoning summaries or optional integrity settings where replaced by this workflow.

If bounded repair fails, show one concise failure in the same panel with affected page/location and the necessary next action; use existing retry/cancel controls where appropriate. Suppress duplicate banners/toasts for that same failure. A completed preparation can still be followed by a failed Scout stage; display those outcomes separately and do not show extraction as ready. Preserve keyboard access, accessible progress announcements and the repository's design system.

## Activation, configuration and lifecycle

The released normal workflow is automatic for supported uploads. No user-facing transcription, rotation, source-integrity enforcement or Scout enable toggle is required. Migrate legacy saved settings so off/shadow choices cannot silently disable required capture or completeness checks for new processing. Keep historical results readable; never relabel an old unverified run as verified.

Internal development/rollout controls may exist only while implementing and validating. They are not acceptance prerequisites for a user to discover, and an operational rollback must not silently present an unverified path as the new complete-preservation workflow.

Preparation has a durable identity, source revision, task registration, audit record and terminal outcome before extraction starts. Integrate with existing session/run persistence and task registry rather than an unrelated job subsystem. Keep Stop effective from the first paid request. Define ownership transfer/reuse when extraction starts, page-result checkpoints, restart recovery and cleanup under existing retention rules. Concurrent uploads, replaced files and reconnects cannot publish stale results into another document or attempt.

Run allocation and migrations follow the repository lifecycle rules. Local failed/retried preparation must preserve the original and prior valid artifacts. No shared mutable workbook or cross-loop state may bypass existing protections.

## Delivery work packages and exit criteria

### A0. References, lifecycle design and benchmark

Preserve baselines and independently check complete source references. Include the abridged standards list, eight missing policy headings, initial cross-sheet omissions and valid policy/disclosure splits. Specify upload-owned preparation/Scout identity, invalidation, model configuration and event/state transitions before wiring automatic execution. Design synthetic concurrency tests and the 4/6/10 live comparison; obtain execution scope before paid runs.

Exit: reviewed expected text/structure/placements, deterministic lifecycle scenarios and an agreed measurement protocol. Measured latency remains an open release criterion until live evidence exists.

### A1. Automatic preparation and verified source capture

Implement per-page detection, recorded orientation corrections, bounded capture and verification using existing modules. Add continuation resolution and stable physical/logical relationships. Remove dependence on an earlier Scout inventory. Integrate durable cancellation, checkpoints and aggregate concurrency limits. Build renderer/ledger fixtures alongside capture work.

Exit: all pages accounted for, source references match, no unresolved capture/boundary disagreement, and incomplete replacement generations never activate.

### A2. Source-backed mapping and deterministic rendering

Extend read_source_manifest/write_note_from_source to the verified model, policy subsections and heading ancestry. Include capital/related-party prose tools separately from numeric facts. Implement exact permitted placement and duplication contracts; support verified span splitting only where needed and tested.

Exit: legitimate policy carve-outs pass, missing or paraphrased content fails, and cross-page paragraphs/tables cannot be partially selected without detection.

### A3. Completeness ledger and repair

Extend actual-cell checks, routing-aware accounting and bounded repair. Recompute after writes and guard concurrent revisions and human edits.

Exit: seeded omissions, orphaned receipts, stale repairs and incomplete assessments cannot produce success; healthy documents complete automatically.

### A4. Source-preserving reviewer and formatting

Update notes base/list/reviewer prompts, tools and maintained formatting contracts together. Make content and heading verification required work. Relink verified blocks instead of rewriting prose, and preserve the complete standards list and policy hierarchy during repairs.

Exit: repairing one defect cannot remove adjacent content or change unaffected wording, numbers or table geometry.

### A5. Upload-to-Scout orchestration, UI and export acceptance

Wire automatic preparation on upload, Scout after verified preparation, inventory reconciliation and reuse by extraction. Deliver the explicit progress/status UI and retire conflicting switches/notices. Check actual content after formatting, editor/download overlays and native insertion against canonical source-backed notes. Coordinate final artifact tests with Plan B.

Exit: a fresh normally configured installation needs only upload to start preparation and Scout, shows truthful progress through failures/reconnects, and exports complete supported prose without extra preparation prompts. Publish only after capture, checks and this user journey all pass; do not relax completeness criteria to ship.

Delivery order: A0 → A1 → A2 → A3 → A4 → A5. UI event/state design and focused renderer/ledger tests begin in A0/A1, not after backend integration. Keep the published plan, maintained contracts, implementation and tests consistent as decisions become concrete.

## Verification and acceptance

Cover DOCX and digital/scanned/mixed PDFs, missing TOC, unnumbered/gapped notes, rotated/upside-down pages, legitimate landscape tables, metadata rotation, uncertain orientation and mixed-orientation regions. Cover multi-page paragraphs/lists/tables, small footnotes, repeated substantive paragraphs, misleading repeated headers, nested headings and missing first/final content.

Exercise policy subsections within larger notes, a mixed-topic paragraph, permitted and forbidden splits/duplication, receiving-cell deletion, stale receipts, oversized notes, truncated model responses and unreadable regions. Check wording, headings, table content, meaningful emphasis, placement and final rendered prose separately.

Test scheduling with synthetic controlled delays: concurrency is bounded, workers refill without batch barriers, output order is stable, capture/verification share the aggregate budget, retries do not duplicate blocks, and multiple documents remain isolated. These tests validate scheduling, not real provider latency.

Test upload-started processing, invalid credentials, cancellation before extraction, refresh/reconnect, restart recovery, replaced uploads, concurrent attempts, stale events and generation invalidation. Assert that Scout follows preparation, no duplicate paid pass runs on extraction start, required checks cannot be disabled by legacy settings, and incomplete source/inventory never enables extraction. Test actual progress labels and explicit outcomes, including one actionable failure without duplicate notices and no “complete” inferred from inactivity.

Run focused serial source manifest/render/write/lineage/integrity/false-green, coverage, reviewer, PDF-sidecar/layout, uploads/lifecycle, notes-export and formatting tests plus all invariant-routed pinning tests. Relevant existing suites include pipeline-stage events, PDF-sidecar wiring, settings API/source-integrity settings and frontend PipelineStages; update their contracts deliberately. After focused passes, run the full backend suite for this cross-cutting change. Regenerate the prompt audit and run its matching test when prompts change. Run targeted Vitest and the frontend build for shared types, stages, settings and formatting consumers.

Acceptance requires exact agreement with reviewed references for readable content, explicit provenance for inferred readings, complete permitted placement, automatic repair of seeded defects, supported formatting and equivalent final workbook prose. Omitted administrative stamps do not count as missing disclosure content. Record verified-preparation and inventory-ready latency, total time, cost and repairs. The performance target must be backed by the agreed benchmark; do not imply a universal timing or transcription guarantee.

## Plan B boundary and joint acceptance

Both plans share canonical data, source evidence, run IDs, revisions and atomic export. Source block IDs remain optional numeric evidence until Plan A supplies them; Plan B must not depend on this new transcription path to fix category storage or SOCIE mapping. Source-backed prose does not replace a numeric fact, and a numeric fact does not satisfy prose completeness.

Coordinate shared notes payload/writer contracts and migrations at integration time; do not reserve competing future schema versions. Verify numeric values/formulas and prose preservation after the final workbook write.

Use GPT-5.6 Luna for all roles in planned live evaluation. Each plan may be validated locally on its own. The proposed final joint acceptance is three independent runs per local document, nine total, not nine per plan. Include a separate explicitly budgeted concurrency experiment and broader synthetic/de-identified counterexamples. Live requests are proposed, not started by this document; agree execution scope when ready.

A numeric-only milestone is not verified complete notes, and successful notes preservation does not imply numeric exports are fixed. Completion requires both acceptance criteria in the same final artifacts.

## Publication scope

This tracked document contains the proposed Plan A revision only. Local financial statements, templates, extracted content, baseline artifacts and the companion working plan remain unpublished. Company references are anonymised. Updating this plan does not implement, validate or deploy the proposed behavior.
