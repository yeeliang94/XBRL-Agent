# XBRL Agent Design System Audit

**Audited source:** `docs/pwc-design-system.html`  
**Compared with:** `web/src/lib/theme.ts`, `web/src/lib/uiStyles.ts`, `web/src/index.css`, `PageHeader.tsx`, `TopNav.tsx`, and the current UI/UX implementation plans  
**Date:** 11 July 2026  
**Status update:** 12 July 2026 — recommendations incorporated into the v3 specification

## Implementation Status (12 July 2026)

The v3 design system (`docs/pwc-design-system.html`, promoted from the v3
review draft) incorporates this audit's recommendations. The findings below
are retained as the audit trail; they no longer describe gaps in the
specification:

- **Governance / source-of-truth (P0)** — resolved. The spec header now
  states the three-layer model (specification → implementation → parity
  tests) and the lockstep rule.
- **Inline-style rule (P0)** — resolved. Rule 01 states the narrow
  distinction: inline values, `uiClass.*` hooks only for states inline
  styles cannot express.
- **Cards as default composition (P1)** — resolved. The Cards section and
  Layouts rule L5 document the grouping order; only interactive cards get
  the hover hook, and cards never lift.
- **Badge appropriateness (P1)** — resolved and superseded: routine status
  is now the monochrome symbol-plus-text language (Status section); outline
  badges are reserved for exceptional compact identification.
- **Application layout patterns (P1)** — resolved. Layouts & density section
  documents the canonical page modes and responsive/viewport contracts.
- **Financial-data patterns (P1)** — resolved. The Financial data section
  (rules F1–F7) is present.
- **Typography drift (P1)** — resolved. The compact semantic scale
  (28/22/20/15/13–14/11–12) is specified and implemented in `ui.*` roles.
- **Motion guidance (P1)** — resolved. Motion section (M1–M5) is present.
- **Navigation semantics (P1)** — resolved in the spec (destinations are
  links; tabs are alternate views of one resource); production adoption is
  tracked in the spec's Page adoption matrix.
- **Accessibility acceptance criteria (P1)** — resolved. Rules A–L cover
  focus, contrast roles, targets, announcements, zoom, reflow, and
  forced-colors.
- **Component/state coverage & content principles (P2)** — resolved. The
  Content & states section lists the required product patterns and the
  complete-component contract defines the 12-point standard.
- **Specification parity tests (P2)** — resolved.
  `designSystemParity.test.ts`, `uiStyles.test.ts`, `cssTokens.test.ts`, and
  `contrastMatrix.test.ts` pin tokens, primitives, state hooks, and contrast
  contracts.

Page-level adoption status lives in the specification's **Page adoption**
section and is updated per change set of
`docs/PLAN-app-wide-design-consistency.md`.

## Executive Summary

The design system has a strong foundation: a disciplined token set, clear PwC orange usage, restrained status treatments, shared inline-style primitives, and explicit Windows-safe implementation guidance. Those foundations should remain.

The document is not yet a complete product design system. It is primarily a token and component specimen. It lacks the layout, workflow, density, financial-data, content, responsive, and interaction guidance needed to stop screens from converging on generic cards, pills, KPI tiles, and helper paragraphs.

Three issues matter most:

1. **Governance is ambiguous:** the document calls itself the single visual reference while also calling live code the source of truth.
2. **Some rules create generic dashboard composition:** cards are described as the primary container, every demonstrated card lifts on hover, and all statuses are demonstrated as pills.
3. **The document has drifted from the application:** typography, navigation labels, motion, page layouts, accessibility behaviour, and several component examples no longer match the live system.

## What Should Stay

- PwC black, orange, white, and neutral-grey foundation.
- One intentional orange primary action per view.
- Status colour used as an accent rather than a large fill.
- Two main text weights: regular and semibold; medium for controls.
- Named spacing, radius, shadow, and motion scales.
- Inline `style={}` primitives with narrow global class hooks for states that inline styles cannot express.
- Neutral alerts with a semantic left rule and icon.
- Existing reduced-motion support and token drift tests.
- Clear distinction between application styling and the separate notes-table formatting subsystem.

## Findings

### P0 — Resolve the source-of-truth model

The HTML says both:

- it is the “single visual reference,” and
- `theme.ts` / `uiStyles.ts` are the live source of truth.

That makes conflicts impossible to resolve consistently.

**Recommendation:** define three explicit layers:

1. `pwc-design-system.html` — behavioural and visual specification.
2. `theme.ts` / `uiStyles.ts` / `index.css` — production implementation.
3. Tests — automated parity contract.

When a rule changes, specification, implementation, and tests move in one change set. Add a version, owner, last-updated date, and short changelog to the document.

### P0 — Correct the inline-style rule

Rule 01 says “no `className`-based styling,” while Rule 04 requires `uiClass.*` class hooks. The actual repository rule is narrower: layout and visual values remain inline; global classes are allowed for hover, focus, animation, editor styling, and other states inline styles cannot express.

**Recommendation:** rewrite Rule 01 to state that distinction. This prevents developers from either reintroducing utility CSS or incorrectly removing necessary state hooks.

### P1 — Stop making cards the default composition

The document calls cards “the primary container for grouped content and metrics,” demonstrates only KPI cards, and applies hover lift to every `.ui-card`. This encourages the card-grid appearance identified in the UI audit.

**Recommendation:** document four different grouping patterns:

- Section — spacing plus heading; no container.
- Bordered group — related controls or data; no elevation.
- Static card — a distinct object; no hover treatment.
- Interactive card — selectable/navigable object; hover and focus treatment.

Only interactive cards should receive `uiClass.card`. Metrics should use `ui.statTile` only when the metric supports a decision or action.

### P1 — Define when badges are appropriate

The system demonstrates every status as a pill, which encourages badge proliferation.

**Recommendation:** establish this hierarchy:

- Plain metadata for standard, filing level, denomination, date, and model.
- Dot plus text for ordinary inline status.
- Outline badge for compact, high-value state identification.
- Alert for a state requiring user action.

Do not use badges for ordinary metadata or as decorative labels.

### P1 — Add application layout patterns

The document defines component geometry but not application composition. The only page layout is the documentation page itself.

**Recommendation:** add canonical layouts:

- Form page: constrained readable column.
- List/table page: wide content with sticky filters.
- Run report: full-width outcome header and tabs.
- Review workspace: collapsible navigation, data grid, and source document panes.
- Modal/dialog: bounded overlay with one primary action.

Document maximum widths, gutters, responsive behaviour, sticky regions, and when full-width layouts are appropriate.

### P1 — Add financial-data patterns

The table example is an agent telemetry table, not the product's main accounting use case.

**Recommendation:** add a financial table section covering:

- Current-year and prior-year headings with explicit periods.
- Right-aligned tabular numerals.
- Accounting parentheses for negative display.
- Editable versus calculated values.
- Zero, blank, not applicable, and unavailable states.
- Totals, subtotals, section rows, and mandatory fields.
- Source/evidence indicators.
- Edited, failed-check, and missing-evidence states.
- Sticky headers and keyboard focus in dense grids.

These domain patterns will make the application feel purpose-built rather than generically themed.

### P1 — Reconcile typography drift

The HTML declares a five-size scale headed by a 28px title. `PageHeader.tsx` uses 32px and 22px titles, while components also use 12px, 14px, 16px, and 18px roles. The document's own chrome declares an even broader 11–36px scale.

**Recommendation:** define semantic roles instead of claiming only five literal sizes:

- Page title
- Compact page title
- Section heading
- Subsection heading
- Body
- Supporting text
- Metadata
- Control label
- Financial value
- Mono diagnostic/cell reference

Choose the production values, update the HTML specimens, then add a test that pins the shared roles rather than policing every legitimate component-specific size.

### P1 — Add motion guidance already present in code

`theme.ts` contains shared durations and easing, and `index.css` has reduced-motion handling, but the HTML contains no Motion section.

**Recommendation:** document:

- Motion communicates state or spatial continuity; it is not decoration.
- Only use the shared duration and easing tokens.
- Prefer opacity and transform.
- No motion on financial values while editing.
- Static cards do not lift.
- Reduced motion must produce an immediate equivalent state.

### P1 — Correct navigation semantics and documentation drift

The component examples use stale labels such as `Template`, `Readable Doc`, `Agents`, `Telemetry`, `Review`, and `Values`. The document also claims all tab bars implement roving arrow-key navigation; `TopNav.tsx` does not.

Top-level application destinations are conceptually navigation links, while run-detail sections are tabs within one view.

**Recommendation:**

- Update all examples to current labels.
- Document navigation links and in-page tabs separately.
- Keep the current production markup unchanged during the visual refinement unless a dedicated accessibility change is approved.
- Stop claiming roving keyboard behaviour where it is not implemented; either implement and test it or remove the claim.

### P1 — Expand accessibility from conventions to acceptance criteria

The existing accessibility section is a useful start but omits important application requirements.

**Add guidance for:**

- Error text associated with controls through `aria-describedby`.
- Live announcements for uploads, validation, saves, and run progress.
- Dialog focus trapping and focus return.
- Loading, disabled, and busy states.
- 200% zoom and narrow viewport behaviour.
- Table/grid keyboard operation.
- Sticky content not obscuring focus.
- Icon-button accessible names.
- Minimum target exceptions: default controls versus compact expert workspace controls.
- Reduced-motion verification.

The design-system HTML itself should retain usable navigation below 900px instead of hiding its sidebar without a replacement.

### P2 — Add missing component and state coverage

The current component catalogue omits frequently used patterns:

- Page shell and page header
- Toolbars and sticky action bars
- File drop zone
- Empty state
- Skeleton/loading state
- Toast
- Confirm dialog and modal
- Disclosure
- Icon button
- Overflow menu
- Search and filter bar
- Pagination/load-more
- Split pane and resizer
- Save state
- Issue/attention queue
- PDF/evidence inspector

Each component should document default, hover, focus, active, disabled, busy, error, and empty states where relevant.

### P2 — Add content and terminology principles

The system controls appearance but not voice. This allows verbose generic helper copy and technical AI language to return.

**Recommendation:** add concise rules:

- Lead with the accounting task or consequence.
- Describe discrepancies, not internal check names.
- Keep AI implementation detail in advanced views.
- Use verbs for actions and nouns for destinations.
- Explain destructive or filing-risk consequences directly.
- Avoid friendly filler and generic AI-product language.
- Use the existing vocabulary helpers for repeated terms.

### P2 — Correct examples that teach outdated behaviour

- The form example uses a free-text model field even though the app now uses a controlled model picker.
- The badge code example omits the status dot shown in the visual specification.
- The card example implies all cards are interactive.
- The token comments describe `grey100` as a card background while `ui.card` uses white.
- The code example encourages raw one-off card construction before defining when a container is warranted.

Update examples so copying them produces current, approved behaviour.

### P2 — Add automated specification parity

Current tests ensure duplicated CSS colours appear in the relevant files, but they do not validate the design-system HTML against production tokens or component metrics.

**Recommendation:** add a focused parity test that checks:

- Core colour values
- Spacing and radius values
- Shadow values
- Typography role values
- Button/input/badge geometry
- Motion durations and easing

The test should fail with a clear instruction to update the HTML, production token, and pinning assertion together.

## Recommended Design Principles v2

1. **Evidence first** — financial data and its source are the primary visual objects.
2. **Action with intent** — one primary action; secondary and destructive actions recede appropriately.
3. **Quiet structure** — use alignment, typography, spacing, and dividers before adding a card.
4. **Density follows the task** — setup flows are open; review tables are compact and precise.
5. **Status earns attention** — ordinary state stays quiet; actionable risk becomes unmistakable.
6. **AI stays backstage** — models, agents, tokens, and tool calls are diagnostics, not the product identity.
7. **Predictable interaction** — shared states, feedback, keyboard behaviour, and saving rules.
8. **Accessible by default** — focus, contrast, semantics, zoom, motion, and announcements are part of component completion.

These principles extend rather than replace the existing “Light & open,” “Restrained type,” “Orange with intent,” and “Quietly consistent” foundation.

## Recommended Update Order

1. Fix governance, contradictory rules, and stale examples.
2. Add layout, density, and financial-data guidance.
3. Correct card, badge, navigation, and typography rules.
4. Add motion, content, responsive, and expanded accessibility guidance.
5. Add missing component/state specimens.
6. Add HTML-to-code parity tests.
7. Apply the refined rules to the product screens in the UI/UX implementation plan.

## Relationship to the UI/UX Plan

The UI/UX plan already aligns with the durable parts of the current system: PwC tokens, inline styles, one orange primary action, neutral semantic surfaces, restrained motion, and shared primitives.

This audit adds an essential prerequisite: improve the design-system specification before using it as the acceptance standard for the application refinement. In particular, the product work must use the new grouping, badge, layout, financial-table, and content rules rather than copying the current card and pill examples unchanged.

