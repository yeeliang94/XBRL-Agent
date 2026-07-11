# Feature Implementation Plan

**Overall Progress:** `100%`

## TLDR

Refine the existing interface into a purpose-built financial review workspace. The work keeps the current PwC-inspired tokens, inline-style architecture, routes, and backend lifecycle intact. It improves action hierarchy, review readiness, document verification, visual restraint, and accessibility without a broad rebrand or workflow rewrite.

The end state should help an operator answer three questions immediately:

1. What needs my attention?
2. What evidence supports this figure?
3. Is this run ready to export, or am I downloading a draft for investigation?

## Critical Decisions

- **Refine, do not replatform** — retain `theme.ts`, `uiStyles.ts`, inline `style={}` usage, and the existing React component structure; Tailwind and a new component framework remain out of scope.
- **Improve the design specification first** — use `AUDIT-pwc-design-system.md` to correct governance, composition rules, domain patterns, and documentation drift before treating the HTML as the acceptance standard.
- **Design for financial review, not an AI dashboard** — make the document, figures, checks, evidence, and filing state primary; demote agents, models, tokens, and tool activity to advanced details.
- **No hard export block for review failures** — a run with failed checks remains downloadable as a clearly labelled draft after an explicit warning; a clean run gets the normal export action.
- **No new sign-off workflow or schema change** — readiness is derived from existing run status and checks. Persistent reviewer acknowledgement remains outside this plan.
- **Keep the current top-level navigation model** — admin-only pages are already gated. Use clearer labels and hierarchy without introducing new menus or route migrations.
- **Use task-appropriate page widths** — forms stay constrained, tables use available width, and Figures remains a multi-pane workspace.
- **Reduce generic SaaS styling selectively** — fewer ornamental cards, pills, shadows, and orange accents; do not reskin every component or discard the PwC visual identity.
- **Build on completed QA work** — do not repeat fixes already marked complete in `PLAN-design-qa-fixes.md` or `PLAN-ux-qa-fixes.md`.
- **Preserve review fallbacks** — existing Overview, Cross-checks, Notes, AI review, Activity, and Figures tabs remain available while the default review path becomes clearer.

## End-User Outcome

- The landing page prioritises starting work and returning to runs that need review.
- History supports quick scanning and filtering without ambiguous row actions.
- A run header states whether the workbook is clean, needs review, or is only safe to download as a draft.
- Failed checks navigate directly to the affected statement, figure, and source page where mappings exist.
- Figures defaults to review-relevant content instead of hundreds of blank template rows.
- Technical AI telemetry is available but does not compete with filing decisions.
- Settings and quality tools explain scope, saving behaviour, and next actions consistently.
- The visual language feels authored for accounting work rather than assembled from generic dashboard patterns.

## Tasks

- [x] 🟩 **Step 1: Confirm the baseline and protect existing behaviour**
  - [x] 🟩 Record desktop screenshots at 100% zoom for Extract, History, run Overview, Figures, Field labels, Benchmarks, Evaluation suites, and Settings.
  - [x] 🟩 Run the existing frontend suite and start the backend regression suite before changes.
  - [x] 🟩 Confirm that no active work overlaps `RunDetailView.tsx`, `ConceptsPage.tsx`, or the shared design primitives.
  - [x] 🟩 Treat completed items in the existing QA plans as regression requirements, not new work.
  - **Review gate:** baseline tests pass and the screenshots are available for before/after comparison.

- [x] 🟩 **Step 2: Improve the design-system specification and shared visual language**
  - [x] 🟩 Apply the approved findings from `AUDIT-pwc-design-system.md` to `pwc-design-system.html` before screen-level restyling.
  - [x] 🟩 Define the HTML as the behavioural specification, the TypeScript/CSS files as the production implementation, and tests as the parity contract.
  - [x] 🟩 Correct the inline-style/class-hook rule, stale navigation examples, typography drift, and unsupported accessibility claims.
  - [x] 🟩 Add guidance for application layouts, density, financial tables, content, responsive behaviour, motion, and component states.
  - [x] 🟩 Distinguish sections, bordered groups, static cards, and interactive cards; only interactive cards receive hover elevation.
  - [x] 🟩 Define when to use plain metadata, dot-and-text status, badges, and actionable alerts.
  - [x] 🟩 Add explicit shared styles for page titles, section titles, body copy, metadata, financial values, toolbars, sticky action bars, and empty states in `theme.ts` and `uiStyles.ts`.
  - [x] 🟩 Reserve orange for the primary action and active navigation; use semantic colours only for their matching status.
  - [x] 🟩 Apply card hover elevation only to interactive cards.
  - [x] 🟩 Replace ornamental nested cards with spacing or dividers where no separate object or action exists.
  - [x] 🟩 Reduce pill usage to statuses that require emphasis; render ordinary metadata as aligned text.
  - [x] 🟩 Use tabular numerals and accounting alignment for financial values.
  - [x] 🟩 Add focused HTML-to-code parity tests and update existing token/primitive tests alongside shared changes.
  - **Review gate:** the specification, production primitives, and tests agree; the system remains recognisably PwC-branded while no longer prescribing generic card-and-pill composition.

- [x] 🟩 **Step 3: Clarify navigation and terminology without changing routes**
  - [x] 🟩 Change visible labels to `New extraction`, `Runs`, and `Evaluation suites`; keep existing internal route and tab keys stable.
  - [x] 🟩 Keep Field labels, Benchmarks, and Evaluation suites admin-only as they are today.
  - [x] 🟩 Keep Settings and account actions in the existing header, but strengthen their labels and focus/hover states.
  - [x] 🟩 Centralise all changed text in the existing vocabulary seam where applicable.
  - [x] 🟩 Update navigation, routing, and vocabulary tests.
  - **Review gate:** existing deep links continue to work and normal operators still see only their relevant destinations.

- [x] 🟩 **Step 4: Make the landing page action-led**
  - [x] 🟩 Keep upload as the dominant action and make the full drop zone interactive with visible drag, focus, loading, success, and error states.
  - [x] 🟩 Show supported formats, file constraints, and a concise confidentiality note before upload.
  - [x] 🟩 Reorder the home summary around actionable states: runs needing review, active runs, recent results, and unstarted drafts.
  - [x] 🟩 Retain bulk draft clearing with its existing confirmation; label it explicitly as `Clear unstarted drafts`.
  - [x] 🟩 Standardise recent-run rows so document, filing profile, status, time, and primary action appear consistently.
  - [x] 🟩 Avoid adding new dashboard metrics that do not lead to an action.
  - **Review gate:** a first-time user can start an extraction, while a returning user can find the next run requiring work without scanning decorative metrics.

- [x] 🟩 **Step 5: Improve Runs scanning and filtering**
  - [x] 🟩 Keep the current runs-versus-drafts separation and load-more behaviour.
  - [x] 🟩 Add a visible clear-filter action and active-filter count using the existing filter state.
  - [x] 🟩 Make the filter bar sticky on long result sets.
  - [x] 🟩 Keep the document name as the primary link and place secondary row actions in a consistent action area.
  - [x] 🟩 Explain unavailable benchmark scores in the empty score state without adding a new scoring workflow.
  - [x] 🟩 Correct the duplicated table-header accessibility structure.
  - [x] 🟩 Preserve filter state when opening a run and returning to the list.
  - **Review gate:** users can isolate review-required runs and return from a run without rebuilding their search context.

- [x] 🟩 **Step 6: Make run readiness and export risk explicit**
  - [x] 🟩 Reorder the run header around document identity, filing profile, status, checks, and the next recommended action.
  - [x] 🟩 Keep model, token, cost, turn, tool-call, and agent details inside Activity or a collapsed advanced section.
  - [x] 🟩 For a clean run, show `Download filled Excel` as the primary action.
  - [x] 🟩 For a run with failed checks, show `Review issues` as the primary action and `Download draft` as a secondary action.
  - [x] 🟩 Before downloading a failed-check draft, show the existing confirmation pattern with the unresolved issue count and filing-risk warning.
  - [x] 🟩 Keep advisory-only findings distinct from blocking failures.
  - [x] 🟩 Rewrite failed-check summaries as discrepancies, including formatted values and differences, rather than failed versions of positive assertions.
  - [x] 🟩 Keep Delete run separated from export actions in the existing destructive-action treatment.
  - **Review gate:** the user can always tell whether they are exporting a clean workbook or an investigation draft, without losing expert access to the file.

- [x] 🟩 **Step 7: Make Figures an exception-led verification workspace**
  - [x] 🟩 Preserve the existing three-pane structure: document navigation, figures, and source PDF.
  - [x] 🟩 Default the centre table to rows with extracted values, edits, failed checks, missing evidence, or mandatory review relevance.
  - [x] 🟩 Add compact filters for `Needs attention`, `Extracted`, `Edited`, `Calculated`, `No source`, `Blank`, and `All` using existing row data.
  - [x] 🟩 Keep headers and review controls sticky while the table scrolls.
  - [x] 🟩 Make each actionable check select the correct statement and row, then update the PDF when a source page is available.
  - [x] 🟩 Add previous/next issue navigation over the existing attention list.
  - [x] 🟩 Highlight the active row and corresponding evidence together.
  - [x] 🟩 Move long source explanations and technical concept metadata into the existing field-details area rather than widening the grid.
  - [x] 🟩 Distinguish calculated, extracted, edited, zero, blank, and no-source states without relying on colour alone.
  - [x] 🟩 Clarify whether `Validate figures` saves edits, reruns checks, or both; change the label to match the real behaviour.
  - [x] 🟩 Preserve the active row, sheet, filters, panel visibility, and scroll position during normal tab/panel interaction.
  - **Review gate:** a reviewer can move from an issue to its value and evidence without manually searching the template or scanning irrelevant blank rows.

- [x] 🟩 **Step 8: Refine Field labels, Benchmarks, and Evaluation suites**
  - [x] 🟩 Field labels: show original and custom labels distinctly, retain explicit Save/Cancel behaviour, show edited state, and keep reset actions local to the row/template.
  - [x] 🟩 Field labels: ensure non-editable section rows never display an edit action.
  - [x] 🟩 Benchmarks: keep the existing source options and APIs, but rewrite `Gold source` and related helper text in plain language.
  - [x] 🟩 Benchmarks: make required fields and disabled-create reasons visible before submission.
  - [x] 🟩 Evaluation suites: add a visible field label and a useful empty state explaining what a suite measures and how to create one.
  - [x] 🟩 Keep existing confirmation and admin-gating behaviour unchanged.
  - **Review gate:** an administrator can understand and begin each quality workflow without knowing evaluation-system terminology.

- [x] 🟩 **Step 9: Make Settings scope and saving unambiguous**
  - [x] 🟩 Group the existing controls into service connection, extraction behaviour, review behaviour, prior-year assistance, notes appearance, and advanced settings.
  - [x] 🟩 Preserve the current split save model already implemented, but make the auto-saved notes-style section visually separate from explicitly saved settings.
  - [x] 🟩 Keep Test connection adjacent to the service settings and the main Save action.
  - [x] 🟩 Show saved, saving, error, and unsaved states next to the section they affect.
  - [x] 🟩 Disable dependent controls when their parent option is off.
  - [x] 🟩 State whether each setting affects everyone and whether it applies to future or existing runs.
  - [x] 🟩 Add a compact live preview for the existing notes-style controls without changing the formatting model.
  - **Review gate:** users can predict what Save affects and cannot mistake auto-saved appearance changes for unsaved service configuration.

- [x] 🟩 **Step 10: Complete accessibility and responsive verification**
  - [x] 🟩 Verify accessible names, roles, selected states, expanded states, and focus order on all changed controls.
  - [x] 🟩 Add live announcements for upload, validation, save, and filter-result changes.
  - [x] 🟩 Test the Figures grid and issue navigation by keyboard.
  - [x] 🟩 Verify text, status, border, and focus contrast at normal and high zoom.
  - [x] 🟩 Validate three intentional layouts: wide review workspace, standard laptop, and narrow monitoring/read-only view.
  - [x] 🟩 Do not attempt full mobile spreadsheet editing; keep status, monitoring, and simple actions usable on narrow screens.
  - [x] 🟩 Honour the existing reduced-motion behaviour.
  - **Review gate:** all core journeys are keyboard-operable and layout degradation is deliberate rather than accidental.

- [x] 🟩 **Step 11: Validate the end-to-end user journeys**
  - [x] 🟩 Run the full frontend and backend test suites after each modular change set.
  - [x] 🟩 Add focused tests for run readiness, draft-download confirmation, issue navigation, filters, table semantics, and Settings save scope.
  - [x] 🟩 Compare final screenshots with the Step 1 baseline at the same viewport and 100% zoom.
  - [x] 🟩 Test one clean completed run, one completed-with-errors run, one draft, one running run, and one failed run.
  - [x] 🟩 Have an operator verify starting/resuming work, a reviewer verify an issue against the PDF, and an administrator verify settings and quality tools.
  - [x] 🟩 Record any follow-up that requires a schema change, new backend workflow, or product decision in a separate plan rather than expanding this implementation.
  - **Review gate:** the redesigned workflow is faster to understand, preserves all current capabilities, and introduces no XBRL extraction or export regression.

## Implementation Boundaries

- No changes to XBRL templates, formulas, linkbases, or extraction prompts.
- No Tailwind conversion and no new design-system dependency.
- No persistent review sign-off, reviewer identity record, or database migration.
- No soft-delete/undo implementation.
- No replacement date-picker dependency.
- No new benchmark scoring or evaluation engine behaviour.
- No full mobile editing experience for the financial grid.
- No removal of existing run-detail tabs or expert telemetry.
- No adoption of unofficial PwC assets, fonts, or brand claims beyond the existing documented foundation.

## Suggested Change Sets

1. Shared visual primitives and terminology
2. Landing page and Runs
3. Run readiness and export treatment
4. Figures filters and attention navigation
5. Quality tools and Settings clarity
6. Accessibility, responsive polish, and final regression verification

Each change set should include its tests and remain independently revertible.
