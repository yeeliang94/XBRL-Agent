# PLAN — Whole-product UI/UX QA Follow-up

**Status:** Complete  
**Progress:** `100%`  
**Source:** Browser-based whole-product QA performed 11 July 2026 against the latest working tree  
**Predecessor:** [PLAN-ui-ux-qa-refinement.md](PLAN-ui-ux-qa-refinement.md)

## Purpose

Close the remaining UI, copy, responsive, accessibility, and workflow gaps found after the completed refinement plan. This is a follow-up plan, not a rewrite of the completed plan. Existing routes, backend contracts, inline-style architecture, XBRL templates, extraction behaviour, and run lifecycle remain unchanged unless a task below explicitly calls for a small presentation-layer correction.

The finished product must let an operator answer, on every run state:

1. What happened?
2. What should I do next?
3. Is any available workbook safe to file, or only suitable for investigation?

## Confirmed findings

### P0 — Release blockers

- The narrow layout expands from a 390 px viewport to approximately 812 px, forcing horizontal scrolling and collapsing introductory copy into a very narrow column.
- A failed run can offer `Download filled Excel` without a filing-risk warning, despite the extraction having failed.
- Failed and aborted run pages do not explain what failed, what output survived, or the recommended recovery action.
- Draft run detail pages have no `Resume setup` action and incorrectly say that figures were extracted by AI.

### P1 — High-impact workflow and comprehension issues

- `Completed with errors` is clipped in the Runs status column at a normal desktop width.
- Header navigation can retain its narrow-screen horizontal scroll position after the viewport widens, hiding earlier destinations and the brand.
- Cross-checks expose raw equations, internal identifiers, `None`, decimal-heavy values, and ambiguous `Expected` / `Actual` headings.
- Failed cross-check rows use positive rule names such as `Equity total agrees...` next to a failed status.
- AI review flags expose internal check IDs and implementation language as the primary reviewer explanation.
- Benchmark financial values are editable without a visible save model, saved state, or unsaved state.
- Benchmark headings claim values are `verified` while the same dataset contains missing values and missing evidence.
- Extraction setup warns that formats are missing but still makes `Start extraction` available without explicit acknowledgement.
- Field-label template choices repeat without filing standard and filing level, making variants indistinguishable.

### P2 — Consistency, copy, and accessibility issues

- `Back to history` conflicts with the renamed `Runs` destination.
- `Scout`, `Scout model`, raw model IDs, and raw variant codes leak through otherwise plain-language screens.
- Advanced model and pre-scan details remain in run Overview instead of Activity / Performance details.
- Settings Account and Users tabs lack short scope explanations.
- User status and role values use lowercase internal language and the signed-in account is not identified as `You`.
- Account validation errors are not announced as alerts and focus does not move to, or reference, the invalid field.
- Reset-password UI uses an ambiguous `Set` action, has no inline Cancel action, and provides weak target context.
- Evaluation-suite empty-state text runs together visually.
- Evaluation-suite detail, running, results, comparison, and error states still require a live end-to-end QA pass.
- Some empty-state copy uses internal terms such as `notes pipeline`.
- Settings validation, save, and test-connection states need a complete keyboard and screen-reader verification pass.

## Non-negotiable boundaries

- Do not convert inline styles to Tailwind or introduce a new component framework.
- Do not modify XBRL templates, formulas, linkbases, prompts, canonical concepts, or extraction logic.
- Do not change the mandatory canonical pipeline or run lifecycle invariants.
- Do not introduce a new persistent review/sign-off workflow or database migration.
- Do not hide useful technical diagnostics; place them behind explicit advanced or technical-detail disclosure.
- Do not hard-block investigation downloads. Clearly distinguish them from filing-ready exports and require acknowledgement where risk exists.
- Preserve admin gating and existing server-side authorization.

## Work plan

### 🟩 Step 1 — Pin the audit baseline

- [ ] Capture screenshots at 1440×900, 1024×768, 720×900, and 390×844 for New extraction, Runs, each run state, Figures, Field labels, Benchmarks, Evaluation suites, and all Settings tabs.
- [ ] Add an audit fixture or deterministic test data covering clean, completed-with-errors, failed, aborted, draft, running, and legacy runs.
- [ ] Record current browser-console output and document-width measurements at every viewport.
- [ ] Run and record the frontend and backend suites before changes.
- [ ] Confirm the current dirty working tree before editing and preserve unrelated changes.

**Gate:** every confirmed finding has a reproducible screenshot, DOM assertion, or test fixture.

### 🟩 Step 2 — Repair global responsive behaviour

- [ ] Remove document-level horizontal overflow at 390 px and 720 px.
- [ ] Make the top navigation intentionally responsive: preserve access to primary destinations without shrinking body content or pushing the Settings action off-screen.
- [ ] Reset or eliminate retained horizontal navigation scroll when widening the viewport or changing routes.
- [ ] Ensure page titles and introductory copy retain a readable minimum measure on narrow screens.
- [ ] Stack summary tiles, filters, upload content, actions, and forms without fixed widths that exceed the viewport.
- [ ] Keep narrow mode focused on monitoring and simple actions; do not attempt full mobile financial-grid editing.
- [ ] Add viewport assertions for `scrollWidth <= clientWidth` on non-grid pages.

**Gate:** no global horizontal scrolling at 390 px; all primary navigation and monitoring actions remain reachable.

### 🟩 Step 3 — Fix Runs table scanning

- [ ] Give status content enough width so `Completed with errors` is never clipped.
- [ ] Rebalance filename, date, status, standard, score, and duration widths at laptop and desktop sizes.
- [ ] Preserve full status text and accessible name at every supported width.
- [ ] Confirm long filenames truncate with a discoverable full label and do not displace status or actions.
- [ ] Recheck sticky filters, active-filter count, clear filters, load more, drafts, and return-state preservation.

**Gate:** status, filename, filing profile, and next action can be scanned without clipped decision-critical content.

### 🟩 Step 4 — Make every run state truthful and actionable

- [ ] Create one shared presentation mapping for `draft`, `running`, `completed`, `completed_with_errors`, `failed`, `aborted`, and legacy states.
- [ ] Draft: show `Resume setup` as primary, remove the false extracted-figures notice, and demote unavailable review tabs/actions.
- [ ] Running: show current stage, elapsed state, safe Stop treatment, and what remains available while processing.
- [ ] Clean completed: retain `Download filled Excel` as primary and distinguish advisory notes from failures.
- [ ] Completed with errors: retain `Review issues` primary and confirmed `Download draft` secondary.
- [ ] Failed: explain the failure in plain language, state whether a partial workbook exists, and label any download `Download partial workbook` or `Download investigation draft` with confirmation.
- [ ] Aborted: explain that the run was stopped, state what was preserved, and offer the correct retry/resume path.
- [ ] Legacy: replace schema terminology with a short user-facing limitation; keep schema detail in a tooltip or technical disclosure.
- [ ] Replace `Back to history` with `Back to runs` everywhere.
- [ ] Keep Delete visually and spatially separate from recovery and export actions.

**Gate:** each state answers what happened, what survived, and what to do next without contradicting the available actions.

### 🟩 Step 5 — Rewrite Cross-checks for financial review

- [ ] Use discrepancy labels for failed rows and positive labels only for passed rows.
- [ ] Replace ambiguous `Expected` / `Actual` with statement-specific headings where possible, or place both named values in a clear explanation.
- [ ] Rename `Message` to `Explanation`.
- [ ] Format values using the run denomination and accounting number formatting.
- [ ] Translate `N/A` to `Not applicable` and explain why in plain language.
- [ ] Remove `None`, raw enum names, raw check identifiers, and Python-like equations from the primary table.
- [ ] Move the original raw diagnostic into collapsed `Technical details` for expert support use.
- [ ] Make a failed row navigate to the affected statement, line item, and source page where mappings exist.

**Gate:** an accountant can understand every result without knowing statement codes or backend check names.

### 🟩 Step 6 — Rewrite AI review around decisions

- [ ] Convert reviewer flags into a short structure: issue, PDF evidence, system limitation, and requested user decision.
- [ ] Humanize raw check IDs, concept IDs, matrix mechanics, and model terminology.
- [ ] Put the original reviewer diagnostic behind `Show technical details`.
- [ ] Replace generic `Open` with a meaningful state such as `Needs your decision`.
- [ ] Clarify what answering a flag does and whether the answer is saved immediately.
- [ ] Clarify what `Run AI review again` will recheck and whether it can change saved figures.
- [ ] Keep direct navigation from a flag to the relevant figure and PDF evidence.

**Gate:** the primary flag copy is concise, evidence-led, and tells the reviewer what decision is needed.

### 🟩 Step 7 — Clarify extraction setup and terminology

- [ ] Replace visible `Scout` terminology with `Document pre-scan`, `Use document pre-scan`, and `Pre-scan model`.
- [ ] Replace `Auto-detect` with `Scan document`; use `Scanning document…` while active.
- [ ] Use sentence case for `Run configuration`, `Filing standard`, and `Filing level`.
- [ ] Explain confidence indicators next to the affected statement formats, not only as a detached legend.
- [ ] Require a format for every selected statement before enabling `Start extraction`, or require explicit acknowledgement before default-format fallback.
- [ ] Make the relationship between `Statement format` and `Statements to extract` clear and keep disabled states understandable.
- [ ] Verify setup error, retry, pre-scan warning, unsupported file, and upload-limit copy.

**Gate:** the primary action always matches the next safe step and internal agent terminology is absent.

### 🟩 Step 8 — Make benchmark editing trustworthy

- [ ] State whether benchmark edits auto-save or require Save.
- [ ] Show saved, saving, unsaved, and error state adjacent to the edited values.
- [ ] Replace overstated `verified values` with `reference values` unless verification is genuinely recorded.
- [ ] Show separate counts for reference values, missing values, and values without source evidence.
- [ ] Replace visible or accessible `gold`, `canonical`, `observed`, and `seeded` terminology with plain labels where they are not essential.
- [ ] Explain calculated rows versus editable reference rows.
- [ ] Keep mandatory and missing-evidence warnings, but ensure they do not imply the benchmark is complete.
- [ ] Verify keyboard editing, blur behaviour, navigation with unsaved changes, and error recovery.

**Gate:** users always know whether a financial edit was saved and how complete the reference set is.

### 🟩 Step 9 — Disambiguate Field labels

- [ ] Label every template with filing standard, filing level, statement, and variant.
- [ ] Group options by standard and level where supported by the current select implementation.
- [ ] Ensure section rows never expose Rename.
- [ ] Retain explicit Save and Cancel for editable rows and make Escape cancel rather than commit.
- [ ] Confirm custom/original labels, edited state, row-local reset, filter behaviour, and long-label wrapping.

**Gate:** no two selectable templates have indistinguishable visible labels.

### 🟩 Step 10 — Finish Settings UX

- [ ] Account: add a short scope statement explaining that the change affects only the signed-in account and requires the current password.
- [ ] Account: add `role="alert"`, `aria-describedby`, and appropriate focus/error association for validation and server errors.
- [ ] Account: distinguish `Changing…` from generic `Saving…` and announce success.
- [ ] Users: add a scope statement explaining sign-in and administrator permissions.
- [ ] Users: use `Active`, `Disabled`, `Administrator`, and `Standard user`; mark the signed-in row as `You`.
- [ ] Users: make Reset password reveal `Set new password` and `Cancel`, identify the target account, validate before submission, and announce success.
- [ ] Users: retain confirmation for enable/disable and promote/demote actions and verify last-administrator protection copy.
- [ ] General: verify dependent controls, local unsaved state, auto-saved notes appearance, Test connection, Save, error, and success announcements.
- [ ] Verify admin and non-admin variants of Settings and direct-route guards.

**Gate:** every setting states its scope, save model, and consequence; validation is announced and recoverable by keyboard.

### 🟩 Step 11 — Refine Notes and technical Activity copy

- [ ] Replace `Face-statement-only runs skip the notes pipeline` with `This run did not include note extraction.`
- [ ] Hide rerun and formatting controls when there is no notes content unless they provide a valid recovery action.
- [ ] Humanize Activity variants (`Order of liquidity`, `Before tax`, `Indirect method`) through the shared vocabulary mapper.
- [ ] Keep tokens, cost, tool calls, and models in Activity / Performance details only.
- [ ] Move model overrides and pre-scan implementation detail out of Overview.
- [ ] Verify notes review, formatter, re-extraction, copy-to-mTool, unsupported-format, and revert confirmations with real note content.

**Gate:** Overview remains filing-focused; technical telemetry remains available without leaking into primary decisions.

### 🟩 Step 12 — Complete Evaluation suites and remaining admin journeys

- [ ] Fix empty-state spacing and treat the heading and explanation as separate blocks.
- [ ] Seed a disposable QA suite and exercise suite creation, document add/remove, optional benchmark association, run configuration, running state, Stop, results, score trend, comparison, retry, and empty/error states.
- [ ] Clarify `Repeats per doc`, `Use scout`, `Run label`, `Scores`, `Consistency`, and `Health` in operator language.
- [ ] Confirm all consequential actions have appropriate confirmation and success feedback.
- [ ] Remove the disposable QA data after verification using a documented safe cleanup path.

**Gate:** an administrator can create, run, interpret, and recover a suite without evaluation-system knowledge.

### 🟩 Step 13 — Accessibility, zoom, motion, and keyboard verification

- [ ] Test all primary journeys with keyboard only, including tabs, filters, dialogs, Figures issue navigation, benchmark editing, and Settings.
- [ ] Verify tab patterns support Arrow keys, Home, End, selected state, and focus placement.
- [ ] Verify dialogs trap focus, label their title/body, restore focus on close, and close with Escape where safe.
- [ ] Verify live announcements for upload, pre-scan, extraction, validation, saving, filtering, password errors, and run-state changes.
- [ ] Test 100%, 200%, and 400% browser zoom for reflow, clipping, and readable focus indicators.
- [ ] Verify normal and high-contrast focus, status, border, and text contrast.
- [ ] Verify reduced-motion behaviour manually in addition to existing unit tests.
- [ ] Add automated accessibility checks for the changed pages and components.

**Gate:** no critical keyboard, accessible-name, focus-order, announcement, reflow, or contrast failures.

### 🟩 Step 14 — End-to-end regression and sign-off

- [ ] Run the complete frontend suite: `cd web && npx vitest run`.
- [ ] Run the complete backend suite: `./venv/bin/python -m pytest tests/ -v`.
- [ ] Add focused tests for responsive overflow, run-state actions/copy, failed-download confirmation, cross-check humanization, AI-review summary, benchmark save state, Field-label option uniqueness, and Settings announcements.
- [ ] Repeat the full screenshot matrix at matching viewport, zoom, and data state.
- [ ] Confirm no browser console warnings or errors.
- [ ] Conduct three role-based acceptance passes: operator, reviewer, and administrator.
- [ ] Record anything requiring schema or workflow expansion in a separate plan.

**Gate:** all automated suites pass, every P0/P1 finding is closed, and role-based acceptance confirms that the interface is truthful, understandable, and safe.

## Suggested implementation sequence

1. Responsive shell and Runs table
2. Run-state truthfulness and export risk
3. Cross-checks and AI review copy
4. Extraction setup and shared vocabulary
5. Benchmarks and Field labels
6. Settings Account and Users
7. Notes, Activity, and Evaluation suites
8. Accessibility and final end-to-end verification

Each change set should include its own pinning tests and remain independently reviewable and revertible.

## Definition of done

- No P0 or P1 finding remains open.
- Non-grid pages have no global horizontal overflow at supported widths.
- Every run state has truthful status copy, a clear next action, and correctly labelled output risk.
- No raw backend identifiers or equations appear as primary operator copy.
- Editable financial-reference values have an unambiguous save model.
- All Settings tabs explain scope and announce validation/save outcomes accessibly.
- The running-run and evaluation-suite journeys have been tested with real disposable QA data.
- Frontend and backend test suites pass and final screenshots are approved.
