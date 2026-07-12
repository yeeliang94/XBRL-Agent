# Plan — App-Wide Design Consistency

**Status:** Implemented — v3 promoted to production; all seven change sets landed (commits on `main`, 12 July 2026)  
**Progress:** `100%` (code + tests; see Implementation Notes for the operator verification that remains open)  
**Created:** 12 July 2026  
**Updated:** 12 July 2026  
**Current production specification:** [`docs/pwc-design-system.html`](pwc-design-system.html) (v3, promoted)  
**Review history:** [`docs/pwc-design-system-v3-review.html`](pwc-design-system-v3-review.html) (retained until the user confirms removal)

## Implementation Notes (12 July 2026)

Implemented as the seven independently reviewable change sets below, one
commit each (`CS1`–`CS7` in the commit subjects). 75 frontend test files /
1139 tests green after every change set; no backend, API, route, or
lifecycle code was touched.

What shipped, by change set:

1. **CS1** — v3 spec promoted into `pwc-design-system.html` (with a page-
   adoption matrix); three-layer token model (`pwc` globals → `tokens`
   semantic roles → `component` roles) in `theme.ts`; accessible action
   colours (#C63D00 / #A83A00) on the primary button; four button roles
   (Subtle+Ghost → Quiet); flat cards with quiet border/surface hover (no
   lift); monochrome status symbols (○ ✓ ! × – ◇) in `runStatus.ts`; shared
   StatusLabel/EmptyState primitives; shared tab, dialog/scrim, bordered
   group, table-density, page-mode primitives; two-part focus + forced-
   colors + stat-tile breakpoints in `index.css`; 28px page-title scale;
   WCAG contrast-matrix test.
2. **CS2** — TopNav destinations became links with stable URLs and
   `aria-current`; wordmark demoted from `h1`; dark active text + orange
   indicator.
3. **CS3** — New extraction (upload-first, quiet flat stat tiles with ! / –
   priorities, divided Recent-runs work queue, `Clear drafts` /
   `Continue setup` renames, compact drop zone) and Runs (1440 list,
   compact labelled filter toolbar, `Showing N of M runs`, Standard table
   density, monochrome statuses, visible row actions, conditional Score
   column, concise dates).
4. **CS4** — Field labels, Benchmarks (collapsible Add-benchmark setup
   group), Evaluation suites (shared tabs/status/metrics/tables; separated
   empty state).
5. **CS5** — Settings at 840px Form mode with PageHeader + shared tabs;
   44px controls with 3:1 boundaries; readable helper/status text; Login
   Sign-in on the accessible action colour.
6. **CS6** — Run report: shared tab geometry (lazy mounting untouched),
   monochrome run/agent/check statuses, readable metric tones + tabular
   numerals, coverage/review status sweep. Reviewer flags and timeline
   alert rules deliberately keep functional colour (attention surfaces).
7. **CS7** — All modals on the shared dialog/scrim primitives (no raw
   backdrops); toast on the elevated-overlap shadow; final monochrome
   sweep (ResultsView, ConceptsPage value states, coverage nav, mTool
   modal); adoption matrix flipped to Adopted; this note.

Deliberate scope notes / deviations:

- **Phase 0 screenshot baselines and the Phase 9 manual browser matrix**
  (zoom, forced-colors, 320px reflow, keyboard walkthrough on live data)
  were not captured in this automated pass — the regression net is the
  test suite (1139 tests, including new contrast, status, layout-mode,
  and behaviour pins). A human visual QA pass over the running app is the
  remaining acceptance step.
- ToolCallCard keeps its status hues: it lives in the Technical-details /
  AI-activity surface where functional colour is in scope by design.
- Notes cell-formatting palettes, charts (eval sparkline/trend), alerts,
  and attention rows keep functional colour per the non-goals.
- `ui.buttonSubtle` / `ui.buttonGhost` remain as deprecated aliases of
  `ui.buttonQuiet` so unmigrated call sites keep compiling; new code must
  use the Quiet role.

## Purpose

Bring every XBRL Agent web surface into one coherent PwC-inspired product
language. The immediate screenshots that triggered this work were New
extraction and Runs, but the implementation must also cover the application
shell, Login, Field labels, Benchmarks, Evaluation suites, Settings, run
reports, review workspaces, dialogs, loading states, and empty states.

This is a visual-system adoption pass, not a rebrand or workflow rewrite. It
preserves the existing routes, APIs, extraction lifecycle, review behavior,
admin gates, draft handling, and XBRL functionality.

The proposed target is deliberately compact rather than consumer-SaaS-like:
15px body copy, 13–14px data/supporting text, 11–12px short labels, flat
surfaces, restrained radii, and dense financial tables. Accessibility comes
from contrast, line height, focus, semantics, and target sizing—not oversized
type or decorative color.

## Why a New Plan Is Required

[`PLAN-ui-ux-qa-refinement.md`](PLAN-ui-ux-qa-refinement.md) is marked complete
and contains many correct product and accessibility decisions. Those completed
items remain regression requirements. However, the current screenshots and
component code still show adoption drift:

- top-level pages use three different heading systems;
- page widths change without a consistently documented task-based reason;
- tabs, tables, status chips, metrics, and empty states are repeatedly rebuilt;
- raw palette values are consumed where semantic action/text/surface roles
  should decide the appearance;
- static cards and shadows are used where spacing or dividers would be clearer;
- routine statuses compete through colored dots, borders, and pills instead of
  the approved monochrome symbol-plus-text language;
- Evaluation suites and Benchmarks do not look like the same quality workspace;
- Settings uses a narrower and quieter visual system than the other pages;
- the HTML design specification and its audit no longer describe the same
  implementation status.

This plan does not reopen completed functional work. It finishes the visual
adoption and documentation parity that remain visible in the live product.

## Source-of-Truth Contract

Changes must preserve the four-part contract already documented in
`CLAUDE.md` and `AGENTS.md`:

1. `docs/pwc-design-system.html` remains the production contract until the v3
   review draft is approved and promoted.
2. `docs/pwc-design-system-v3-review.html` is the proposed target for this plan.
3. `web/src/lib/theme.ts`, `web/src/lib/uiStyles.ts`, and
   `web/src/index.css` implement those rules.
4. Frontend tests protect token meaning, component behavior, accessibility,
   responsive composition, and page adoption.

Phase 1 must either approve/promote the v3 draft or revise it before production
files change. After approval, update every affected layer in the same change
set. Do not make a page look correct with local values while leaving semantic
tokens, shared primitives, or the specification behind.

## Non-Negotiable Decisions

- Keep inline `style={}` values as the primary styling mechanism. Do not add
  Tailwind, CSS modules, or a component framework.
- Use narrow global class hooks only for hover, focus, responsive composition,
  animation, and other states inline styles cannot express.
- Introduce a three-layer token model: global values → semantic roles →
  component roles. Page components consume meaning, not palette names.
- Keep signature orange (`#FD5108`) for identity, rules, progress, and active
  indicators. Accessible darker action roles carry button fills and small
  interactive text.
- Allow one dominant action per decision region, not an arbitrary one-per-page
  quota. Locally competing primary actions remain prohibited.
- Use the approved monochrome status language: neutral symbol plus explicit
  text. Routine status never relies on a colored dot, border, pill, or fill.
- Reserve success, warning, error, info, and thinking hues for exceptional
  alerts, attention rules, charts, or technical-detail AI activity. They are
  optional and never the default status treatment.
- Prefer typography, alignment, spacing, and dividers before adding a card.
- Static surfaces remain flat. Interactive cards use a quiet border/surface
  response; shadows are reserved for genuine overlap such as sticky regions,
  popovers, and dialogs.
- Treat ordinary filing metadata as text, not pills.
- Use sentence case by default. Uppercase 11px is limited to short eyebrows,
  never table headers, long labels, or instructions.
- Keep financial evidence, values, checks, and filing state ahead of models,
  tokens, tool calls, and agent internals.
- Preserve the expert density of the Figures and Notes workspaces. Do not force
  a spacious marketing-page layout onto financial editing tools.
- Treat Notes cell-formatting palettes as document-formatting data, not app
  chrome. Their literal workbook colors are outside the app-token cleanup.
- Do not introduce unofficial PwC logos, fonts, or brand assets.

## Canonical Layouts

Document and implement these task-based page modes:

| Mode | Target width | Intended surfaces |
|---|---:|---|
| Authentication | 380px | Login |
| Form | 840px | Settings, account and focused configuration forms |
| Standard | 1120px | New extraction, Benchmarks, Evaluation suites |
| Wide list | 1440px | Runs, Field labels |
| Workspace | Full available width | Run report, Figures, PDF/source review |

The outer app shell owns the route-level mode. A page may use a narrower inner
reading measure for prose, but it must not add another arbitrary page width.
Run-detail list and workspace modes must remain distinct: constraining the Runs
table must not constrain the selected run review workspace.

Use four responsive workflow modes rather than treating responsiveness as a
single mobile breakpoint:

| Viewport mode | Product expectation |
|---|---|
| Mobile | Monitor status, approve, read evidence, and take simple actions |
| Tablet | Simplified grid with one contextual pane at a time |
| Laptop | Primary review workspace with collapsible context |
| Wide desktop | Full financial grid with navigation and evidence panes |

## Canonical Typography

Reconcile the current 28px HTML specification with the 32/24/22px production
drift and use semantic roles:

| Role | Size / weight | Use |
|---|---|---|
| Page title | 28px / 600 | One top-level page heading |
| Compact workspace title | 22px / 600 | Selected run/file or dense workspace heading |
| Section title | 20px / 600 | Major section within a page |
| Subsection title | 15–16px / 600 | Local content group |
| Body | 15px / 400 | Main instructions and content |
| Data / supporting text | 13–14px / 400 | Tables, help, secondary explanation |
| Metadata | 12–13px / 400 | Dates, filing profile, auxiliary facts |
| Micro-label | 11–12px / 600 | Short eyebrows and compact keys only |

Medium 500 remains valid for interactive controls. Financial and diagnostic
monospace use remains deliberate; ordinary counts and prose use the body font.
Small text uses `grey700` or darker and a readable line height. Table headers
use sentence case rather than tracked uppercase.

## Canonical Semantic Tokens

Phase 1 must define names and ownership before page adoption:

| Layer | Examples | Consumers |
|---|---|---|
| Global | `orange500`, `grey700`, spacing/radius steps | Semantic aliases only |
| Semantic | `color.action.primary`, `color.text.secondary`, `surface.canvas`, `space.section` | Shared primitives |
| Component | `button.primary.background.hover`, `table.header.surface`, `dialog.scrim` | Components and state hooks |

Minimum contrast contracts:

- normal text: 4.5:1;
- essential control boundaries and focus: 3:1;
- primary action text: 4.5:1 in every state;
- disabled/decorative content is the only intentional low-contrast exception.

Proposed reviewed action values are `#C63D00` for primary and `#A83A00` for
hover; keep these semantic and independently changeable from signature orange.

## Canonical Geometry and Elevation

| Role | Radius / elevation |
|---|---|
| Dense cells and compact references | 3px, flat |
| Buttons, inputs, and alerts | 6px, flat |
| Cards and panels | 8px, flat |
| Large feature surfaces | 10px only when justified |
| Sticky / popover | Restrained elevated shadow |
| Dialog | Modal shadow and semantic scrim |

The four button variants are Primary, Secondary, Quiet, and Destructive.
`Subtle` and `Ghost` must converge into one Quiet role rather than surviving as
overlapping fifth and sixth choices.

## Shared Foundations to Add or Finish

### Page and grouping primitives

- Add the Standard and Workspace layout roles alongside `ui.pageForm` and
  `ui.pageWide`, or rename the roles coherently in one migration.
- Make `PageHeader` the canonical top-level page heading with standard and
  compact variants.
- Add a static bordered-group primitive with no shadow.
- Keep `ui.card` for distinct objects; require the interactive card class only
  when the whole object navigates or selects.
- Replace interactive card lift/translation with a quiet border or surface
  change. Cards never move vertically on hover.
- Add a shared empty-state component or strict primitive supporting a title,
  explanation, and optional action.
- Add a standard section-header composition.

### Interaction primitives

- Add shared tab bar, tab, and active-tab styles. Keep each page's existing
  keyboard and selection state logic.
- Add one monochrome status component that accepts a semantic state, explicit
  human label, neutral symbol, and optional supporting description. Symbols are
  `aria-hidden`; the text remains the accessible name.
- Centralize the canonical symbol families: `○` in progress; `✓` successful,
  verified, completed, or extracted; `!` action required, needs review, or no
  source; `×` failed or aborted; `–` draft, not started, skipped, unavailable,
  or not applicable; `◇` calculated or derived. The explicit label carries the
  precise state. Add a symbol only for a genuinely different user-facing
  concept, not every backend enum.
- Add a compact filter-toolbar composition.
- Complete shared table-density adoption: Compact 28–32px, Standard 40px, and
  Comfortable 48px. Headers and rows use the same density.
- Add explicit table contracts for sticky headers, frozen identifiers where
  context requires them, horizontal overflow, numeric alignment, and
  keyboard-visible row/cell focus.
- Consolidate buttons to Primary, Secondary, Quiet, and Destructive. Important
  actions and inputs remain 44px; compact desktop controls may be 40px when
  separated; nothing falls below the WCAG 24px minimum.
- Add shared dialog, scrim, and action-bar primitives instead of repeating
  modal geometry and raw backdrop colors.

### Global state hooks

Add only the necessary hooks in `index.css` for:

- inactive navigation hover;
- interactive row/card hover;
- responsive filter and form composition;
- forced-colors / high-contrast preservation for boundaries and status;
- stat-tile breakpoints;
- narrow recent-run rows;
- sticky-region behavior that does not obscure focused controls.

Continue honoring `prefers-reduced-motion`.

### Complete-component contract

Every primitive added in Phase 1 must document and test:

1. purpose and user outcome;
2. when to use and when not to use;
3. anatomy and required parts;
4. variants and density;
5. default, hover, focus, active, selected, disabled, busy, success, error,
   and empty states where relevant;
6. keyboard and pointer behavior;
7. responsive and overflow behavior;
8. accessibility requirements;
9. content/vocabulary guidance;
10. semantic and component token mapping;
11. examples and anti-patterns;
12. maturity: draft, beta, stable, or deprecated.

## Implementation Phases

### Phase 0 — Baseline and regression inventory

- [ ] Run `cd web && npx vitest run` before changes.
- [ ] Capture comparable 100%-zoom screenshots for Login, New extraction,
  Runs, a clean run report, a flagged run report, Figures, Field labels,
  Benchmarks, Evaluation suites, and all Settings tabs.
- [ ] Capture at 1440px and 1024px. Record focused 720px, 390px, and 320px
  states for monitoring/simple-action surfaces.
- [ ] Record 200% zoom checks for the main navigation, filters, tables, and
  Settings form.
- [ ] Inventory current user-visible states: empty, loading, error, disabled,
  busy, success, failed, warning, and destructive confirmation.
- [ ] Inventory every user-visible status enum and map it to an explicit human
  label plus one canonical neutral symbol family before changing components.
- [ ] Record current contrast failures for primary actions, small secondary
  text, placeholders, control boundaries, and focus indicators.
- [ ] Confirm no unrelated active work overlaps shared styling files.

**Gate:** baseline suite is green and each target page has a before-state.

### Phase 1 — Specification and shared implementation parity

Files:

- `docs/pwc-design-system.html`
- `docs/pwc-design-system-v3-review.html`
- `docs/AUDIT-pwc-design-system.md`
- `docs/PLAN-ui-ux-qa-refinement.md`
- `web/src/lib/theme.ts`
- `web/src/lib/uiStyles.ts`
- `web/src/index.css`
- `web/src/components/PageHeader.tsx`
- design-system and primitive tests

Tasks:

- [ ] Review and approve the v3 draft, including compact typography, accessible
  action roles, monochrome status symbols, flat surfaces, radii, density, and
  component-completeness contract.
- [ ] Promote the approved decisions into `docs/pwc-design-system.html`; retain
  the review copy until the user confirms whether it should remain as review
  history or be removed.
- [ ] Reconcile the title scale and canonical layouts across specification,
  implementation, and tests.
- [ ] Add global, semantic, and component token layers without renaming stable
  raw `pwc` tokens until all consumers have migrated.
- [ ] Add a contrast matrix test for supported foreground/background pairs and
  state transitions.
- [ ] Mark audit recommendations already incorporated into the HTML as
  implemented; stop describing layouts, motion, financial patterns, and
  content guidance as absent.
- [ ] Add a page-adoption status matrix to the audit or specification.
- [ ] Add shared tab, bordered-group, empty-state, monochrome status, dialog,
  scrim, and section primitives without speculative abstractions.
- [ ] Define static versus interactive card behavior in both documentation and
  code.
- [ ] Define semantic radius/elevation roles and consolidate Subtle/Ghost into
  the Quiet button role.
- [ ] Extend `designSystemParity.test.ts` and `uiStyles.test.ts` to pin semantic
  typography, page modes, tab geometry, dense tables, empty states, and card
  behavior.
- [ ] Make parity tests assert the correct semantic token at the correct
  property/state; a hex appearing anywhere in a file is not sufficient parity.
- [ ] Do not globally change a token until exact-RGB tests and all affected
  components are accounted for.

**Gate:** the v3 direction is approved, promoted, and represented by semantic
tokens, complete primitives, and meaningful parity tests before page-level
restyling begins.

### Phase 2 — Application shell and navigation

Files:

- `web/src/App.tsx`
- `web/src/components/TopNav.tsx`
- `web/src/index.css`
- shell and navigation tests

Tasks:

- [ ] Change the product wordmark from an `h1` to non-page-heading brand text.
- [ ] Guarantee one page-level `h1` per destination.
- [ ] Normalize header height, alignment, spacing, and action hit areas.
- [ ] Apply a quiet hover state to inactive destinations.
- [ ] Represent the active destination with dark readable text plus the
  signature-orange indicator; do not use orange as small navigation text.
- [ ] Render top-level destinations as links with stable URLs and
  `aria-current`, while preserving SPA navigation, deep links, admin gates, and
  modified-click/new-tab behavior. Reserve the ARIA tab pattern for alternate
  views of one resource.
- [ ] Preserve admin-only destination gates and mobile horizontal scrolling.
- [ ] Keep email, logout, and Settings secondary to main navigation.

**Gate:** moving among destinations no longer changes the apparent product
scale, and keyboard focus remains visible throughout the shell.

### Phase 3 — New extraction

Files:

- `web/src/pages/ExtractPage.tsx`
- `web/src/components/HomeHero.tsx`
- `web/src/components/UploadPanel.tsx`
- `web/src/components/FileDropzone.tsx`
- `web/src/components/StatTiles.tsx`
- `web/src/components/RecentRunsList.tsx`
- their focused tests

Tasks:

- [ ] Use the Standard layout and canonical `PageHeader`.
- [ ] Place upload before workload metrics and recent activity.
- [ ] Keep `UploadPanel` mounted in a stable tree position across state changes.
- [ ] Compact the extraction drop zone and strengthen its upload/document
  affordance without changing validation.
- [ ] Explain accepted files, 100MB limit, and the next configuration step once.
- [ ] Preserve drag depth, focus, loading, error, PDF, and DOCX behavior.
- [ ] Use quiet `ui.statTile` metrics rather than four equally elevated cards.
- [ ] Prioritize Needs review and Not started drafts through ordering, explicit
  copy, and the neutral `!` / `–` symbols—not decorative color.
- [ ] Rename `Clear` to `Clear drafts`; preserve the current confirmation and
  draft-only deletion behavior.
- [ ] Convert Recent runs from nested cards to a divided work queue with
  filename, filing profile, monochrome symbol-plus-text status, concise date,
  and visible action.
- [ ] Rename draft action `Resume` to `Continue setup`.
- [ ] Add intentional four/two/one-column stat breakpoints.

**Gate:** upload is unmistakably the primary purpose, while returning users can
still locate review work without reading a dashboard wall.

### Phase 4 — Runs

Files:

- `web/src/pages/HistoryPage.tsx`
- `web/src/components/HistoryFilters.tsx`
- `web/src/components/HistoryList.tsx`
- history tests

Tasks:

- [ ] Use a 1440px list container without constraining selected run detail.
- [ ] Replace one uniform 32px page gap with grouped header, filter, count,
  table, drafts, pagination, and note spacing.
- [ ] Rebuild filters as a compact responsive toolbar.
- [ ] Add a visible Filename label to search.
- [ ] Keep filename search debounce and stale-response guards unchanged.
- [ ] Remove the visible `No filters applied` message while retaining live
  announcements.
- [ ] Show active filter count and `Clear filters` only when useful.
- [ ] Disable sticky filtering where it consumes excessive narrow-screen space.
- [ ] Replace `218 runs · 50 loaded` with `Showing 50 of 218 runs`.
- [ ] Reduce row padding from 24px to the default compact list rhythm.
- [ ] Use sentence-case headers and the Compact or Standard density role rather
  than page-specific cell padding.
- [ ] Replace colored run-status chips with the canonical neutral symbol and
  explicit label.
- [ ] Apply the existing table-row hover hook.
- [ ] Keep filenames as native links and the whole row as pointer convenience.
- [ ] Add visible `Review`, `Continue setup`, or `Open` row actions without
  firing callbacks twice.
- [ ] Render the Score column and trend only when loaded rows contain scores.
- [ ] Use concise dates with exact timestamps available as supplementary text.
- [ ] Preserve drafts disclosure, standard filtering, load-more behavior, and
  modified-click/new-tab support.

**Gate:** users can scan and act on 50 rows without excessive eye travel or
ambiguous controls.

### Phase 5 — Quality administration surfaces

#### Field labels

Files: `TemplateSettingsPage.tsx` and tests.

- [ ] Use Wide list mode rather than custom clamp-based page padding.
- [ ] Use `PageHeader`, shared toolbar, visible Template and Search labels, and
  shared table container styles.
- [ ] Replace local 2px/3px radii with tokens.
- [ ] Use a standard neutral edited symbol and explicit label.
- [ ] Use shared alert treatment for load and rename failures.
- [ ] Preserve Save, Cancel, Enter, Escape, reset, and non-editable sections.

#### Benchmarks

Files: `BenchmarksPage.tsx` and tests.

- [ ] Use Standard mode and `PageHeader`.
- [ ] Present Add benchmark as a bordered setup group, not another equal card.
- [ ] Consider collapsing Add benchmark when benchmarks already exist; keep it
  open and primary in the empty state.
- [ ] Retain interactive benchmark cards, but replace hover elevation/lift with
  the canonical border/surface response.
- [ ] Use shared empty, warning, form, button, and metadata patterns.
- [ ] Preserve editor routing, create modes, import reports, and delete dialog.

#### Evaluation suites

Files: `SuitesPage.tsx` and tests.

- [ ] Use Standard mode and `PageHeader`.
- [ ] Fix the missing separation in `No evaluation suites yetCreate one...`.
- [ ] Replace the oversized creation card with a compact bordered form group.
- [ ] Give the empty state a separate title, explanation, and next action.
- [ ] Replace custom `TabBtn`, `StatusChip`, `Metric`, and table geometry with
  shared primitives while preserving behavior.
- [ ] Translate raw statuses into human labels with the canonical monochrome
  symbol-plus-text treatment; remove colored status dots and pills.
- [ ] Use body type for ordinary metrics and monospace only where alignment or
  diagnostics require it.
- [ ] Normalize suite documents, run history, trends, and compare layouts.
- [ ] Make creation, launch, and comparison forms stack deliberately.

**Gate:** Field labels, Benchmarks, and Evaluation suites read as one related
administration workspace, not three separately styled tools.

### Phase 6 — Settings and authentication

Files:

- `SettingsPage.tsx`
- `GeneralSettingsForm.tsx`
- `AccountTab.tsx`
- `UsersTab.tsx`
- `LoginPage.tsx`
- focused tests

Tasks:

- [ ] Move Settings from a custom 640px container to Form mode at 840px.
- [ ] Use canonical `PageHeader` and shared tab styles.
- [ ] Preserve Settings arrow-key tab behavior and admin gating.
- [ ] Keep the split save model but make explicitly saved and auto-saved
  sections visually unmistakable.
- [ ] Standardize section headings, helper text, inline state feedback, and
  dependent-control disabled states.
- [ ] Keep helper/metadata text compact but use `color.text.secondary`
  (`grey700` or darker); do not recreate hierarchy with faint text.
- [ ] Keep Test connection and Save near the settings they affect.
- [ ] Adopt dense-table and shared action patterns in Users.
- [ ] Remove fallback app-chrome color literals when an existing token is
  guaranteed.
- [ ] Keep Login at the Authentication width; normalize title/supporting roles
  without adding decoration or extra actions.
- [ ] Use the semantic accessible action color for Sign in rather than white
  text on signature orange.
- [ ] Preserve login autocomplete, lockout, errors, and submission behavior.

**Gate:** users can predict scope and save behavior, and Settings looks like the
same product without becoming unnecessarily card-heavy.

### Phase 7 — Run report and financial review workspace

Files include:

- `RunDetailView.tsx`
- `ConceptsPage.tsx`
- `ValidatorTab.tsx`
- `ReviewTab.tsx`
- `NotesReviewTab.tsx` and its supporting panels
- `AgentTelemetryPanel.tsx`, `AgentTimeline.tsx`, and `ToolCallCard.tsx`
- `PdfSourcePane.tsx`
- related tests

Tasks:

- [ ] Keep Workspace mode and the current tab structure.
- [ ] Adopt shared tab geometry without changing lazy mounting or tab routing.
- [ ] Normalize run header, monochrome symbol-plus-text status, filing metadata,
  actions, and warning banner with shared roles.
- [ ] Continue leading with outcomes and evidence; keep performance details
  collapsed or in Activity/Telemetry. If labels are revisited, prefer the
  human-facing umbrella “Technical details”; do not change routes or lazy
  mounting in this visual pass.
- [ ] Normalize metric tiles, section headings, alerts, and static card borders.
- [ ] Remove hover elevation from static report objects.
- [ ] Use financial-value typography and tabular numerals consistently.
- [ ] Keep currency, scale, entity scope, and comparative periods visible while
  values remain on screen.
- [ ] Use provenance, review state, and validation evidence rather than generic
  model-confidence decoration.
- [ ] Replace arbitrary app-chrome values only where a shared token/primitive
  fits without harming dense-workspace usability.
- [ ] Preserve Figures panes, evidence navigation, selection, editing, checks,
  notes formatting, and responsive monitoring behavior.
- [ ] Exclude user-authored Notes formatting colors from token enforcement.

**Gate:** run review feels connected to the rest of the app while remaining a
purpose-built financial workspace rather than a stretched form page.

### Phase 8 — Dialogs, modals, feedback, and terminal states

Files include `ConfirmDialog.tsx`, `MtoolFillModal.tsx`, `SuccessToast.tsx`,
shared disclosures, skeletons, and page-specific error/loading states.

- [ ] Normalize modal title hierarchy, close targets, dividers, and action bars.
- [ ] Migrate every modal to the shared dialog/scrim primitive; remove repeated
  raw backdrop colors, local shadows, and local radii.
- [ ] Keep one dominant action in the dialog's decision region, neutral
  secondary actions, and clearly separated destructive actions.
- [ ] Verify focus entry, focus trap, Escape behavior, and focus return.
- [ ] Verify the two-part focus treatment remains visible on the scrim, dialog,
  controls, and forced-colors/high-contrast modes.
- [ ] Standardize empty, loading, error, busy, saved, and retry states.
- [ ] Ensure progress and save updates are announced without excessive chatter.
- [ ] Keep compact expert controls where the design specification explicitly
  permits them.

**Gate:** equivalent states look and behave equivalent regardless of the page
that produced them.

### Phase 9 — Final validation and documentation closure

- [ ] Run focused tests after each phase and the complete frontend suite at the
  end of every change set.
- [ ] Run backend tests only when a supposedly visual change crosses an API or
  lifecycle boundary; avoid those boundaries by default.
- [ ] Compare after-screenshots with the exact baseline viewport and zoom.
- [ ] Test clean, completed-with-errors, failed, running, aborted, and draft runs.
- [ ] Test empty and populated Benchmarks and Evaluation suites.
- [ ] Test Settings as admin and non-admin.
- [ ] Test keyboard-only navigation across shell, upload, filters, lists, tabs,
  forms, dialogs, and review controls.
- [ ] Verify 320 CSS px reflow for monitoring/simple actions, 200% zoom,
  reduced motion, forced-colors/high contrast, long filenames, long error
  messages, zero metrics, 50+ runs, and mixed scored/unscored rows.
- [ ] Verify all documented text/control/focus contrast pairs in default, hover,
  focus, active, disabled, error, and destructive states.
- [ ] Verify Compact, Standard, and Comfortable tables preserve headers,
  alignment, keyboard focus, and horizontal context.
- [ ] Search for and remove legacy colored status chips/dots from app chrome;
  exclude charts, alerts, and Notes document-formatting palettes.
- [ ] Update the design-system adoption matrix and this plan's progress.
- [ ] Record any workflow/schema request discovered during visual work in a
  separate plan rather than expanding scope.

**Gate:** all core tasks remain functional, visual rules are consistent, and
the specification, implementation, tests, and screenshots agree.

## Required Test Updates

At minimum, extend or add coverage for:

- `designSystemParity.test.ts`: semantic token ownership, compact typography,
  layout modes, tabs, monochrome status, empty states, card behavior, motion,
  and component-contract coverage.
- `cssTokens.test.ts`: correct semantic value at the correct selector/property;
  mere source-string presence is not sufficient.
- `uiStyles.test.ts`: new shared primitives, four button roles, radii/elevation,
  dialog/scrim, status symbols, table densities, and canonical geometry.
- Add contrast tests for supported text, action, control-boundary, and focus
  foreground/background pairs.
- `TopNav.test.tsx`: active destination, semantics, admin gates, callbacks.
- `HomeHero`, `UploadPanel`, `FileDropzone`, `StatTiles`, and
  `RecentRunsList` tests: order, copy, stable mounting, states, callbacks.
- `HistoryFilters`, `HistoryList`, and `HistoryPage` tests: labels, debounce,
  counts, conditional Score, native links, actions, drafts, pagination.
- `TemplateSettingsPage`, `BenchmarksPage`, and `SuitesPage` tests: canonical
  headers, empty states, monochrome status symbols/labels, and preserved actions.
- `SettingsPage`, `GeneralSettingsForm`, `AccountTab`, and `UsersTab` tests:
  tabs, save scope, state feedback, admin behavior.
- Run-detail and review tests: tab behavior, warnings, outcomes, static card
  treatment where testable, and no regression in lazy mounting.
- Dialog, modal, and toast tests: accessible names, focus, busy, cancel, and
  destructive confirmation.

Avoid brittle full-markup snapshots. Pin semantic roles, meaningful text,
callbacks, shared primitive values, and state-dependent visibility.

## Visual Acceptance Matrix

Every page must satisfy these checks:

| Area | Acceptance criterion |
|---|---|
| Page identity | One page `h1`; title and description align with canonical roles |
| Width | Page uses its documented task-based mode; no arbitrary inner page cap |
| Typography | Compact semantic scale; 15px body; 13–14px data/support; small text remains high contrast |
| Primary action | One dominant accessible dark-orange action per decision region |
| Brand color | Signature orange is identity/indicator, not small text or default button fill |
| Grouping | Sections/dividers used before cards; static surfaces are flat; cards do not lift |
| Status | Canonical neutral symbol plus explicit label; no routine colored dot, pill, border, or fill |
| Forms | Visible labels, associated help/errors, consistent 44px controls |
| Tables | Sentence-case stable headers, named density, aligned numeric values, overflow and keyboard focus |
| Empty state | Clear title, explanation, and next action when one exists |
| Loading/error | Layout remains stable and recovery is clear |
| Responsive | Monitoring/simple actions reflow at 320px; workspace composition follows mobile/tablet/laptop/wide modes |
| Keyboard | Focus order, active state, dialogs, and tabs work without a pointer |
| Contrast | Text, action, boundary, and focus roles meet their documented ratios in every state |
| Zoom/motion | Usable at 200% zoom, forced colors, high contrast, and reduced motion |

## Explicit Non-Goals

- No backend API, database, extraction, reviewer, prompt, or run-lifecycle
  changes unless a separately approved defect blocks the visual implementation.
- No XBRL template, formula, linkbase, exporter, or workbook-formatting changes.
- No route migration or new information architecture.
- No dark-theme launch in this pass. Semantic tokens must not block future
  theming, and forced-colors/high-contrast support remains in scope.
- No removal of functional color from alerts, charts, attention rules, or
  user-authored Notes content; the monochrome rule applies to routine app
  status displays.
- No new benchmark scoring or evaluation behavior.
- No replacement date-picker dependency.
- No full mobile financial spreadsheet editing experience.
- No removal of expert telemetry, activity, review, notes, or Figures features.
- No new official-brand claims or unlicensed assets.
- No broad rewrite of the Notes editor's workbook-formatting subsystem.

## Change-Set and Rollback Strategy

Implement as independently reviewable changes:

1. Specification and shared primitives
2. App shell and navigation
3. New extraction and Runs
4. Field labels, Benchmarks, and Evaluation suites
5. Settings and Login
6. Run report and review workspace
7. Dialogs, accessibility, responsive polish, and documentation closure

Each change set must include its tests. Do not mix backend cleanup or unrelated
component refactors into these commits. If a shared primitive causes broad
regression, revert that change set rather than adding local overrides to every
page.
