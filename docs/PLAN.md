# Implementation Plan: Design-System Code Sweep (align live UI with `docs/pwc-design-system.html`)

**Overall Progress:** `0%`
**Design Reference:** `docs/pwc-design-system.html` (the spec — source of truth) · live tokens `web/src/lib/theme.ts` + `web/src/lib/uiStyles.ts` + `web/src/index.css` · CLAUDE.md gotcha #7 (inline-styles + rgb pinning tests).
**Last Updated:** 2026-06-24

> Replaces the previous (completed, 100%) PLAN.md for the **Notes Reviewer Agent** —
> that work is done; archived at `docs/Archive/PLAN-notes-reviewer-agent.md` and in
> git history (commits `34d4bc1`, `aef6b8f`).

## Summary
The 2026-06-24 design-doc redesign moved the status language from **filled chips/alerts**
to a calmer **outline-and-accent** language, but the code still ships the old style.
This sweep aligns `theme.ts` status tokens to the doc, rebuilds `ui.badge*` as
outline pills with a status dot, rebuilds `ui.alert*` to a neutral surface with a
coloured left-rule + icon, and removes Light-300 weight usage (→ regular 400).
Foundational tokens (orange, greys, spacing, radius, shadow, fonts, type sizes)
already match and are **out of scope**.

## Key Decisions
- **Scope = the 4 audited divergences only.** Status hues/text/tints, badges, alerts,
  type-weight. No restyling of components beyond what these primitives cascade into.
- **Tokens cascade; components rarely change.** Audit confirmed **zero** hardcoded
  status hexes in components — every status colour flows through `pwc.*`. So badge/alert
  *call sites* change only where they must now emit a `<dot>`/icon element.
- **`pwc.weight.light` key is retained, usage removed.** Deleting the key widens blast
  radius for no gain; gotcha #7 keeps token *names* stable. We stop using `light` in
  product UI and correct the stale `theme.ts` comment.
- **Notes-table subsystem is excluded** (gotcha #16): `NotesReviewTab.tsx` /
  `ClipboardFormatControls.tsx` raw hexes are the intentionally-non-tokenised theme
  subsystem — untouched here.
- **Lockstep tests (gotcha #7).** Every token hex maps to an `rgb()` some test asserts.
  Each phase updates its pinning tests in the same step; "Verify" = the vitest suite green.

## Pre-Implementation Checklist
- [x] 🟩 Audit complete — divergences enumerated against the spec
- [x] 🟩 Call sites + pinning tests mapped (badges: 7 components; alerts: LoginPage; direct rgb pins: PipelineStages, SettingsModal + badge/alert component tests)
- [ ] 🟥 Working tree clean on `theme.ts` / `uiStyles.ts` / `index.css` at start
- [ ] 🟥 Baseline: `cd web && npx vitest run` green before any change

## Tasks

### Phase 1: Status tokens (`theme.ts`)
The foundation — every later phase consumes these values. Land tokens first so badge/alert
rebuilds reference correct hues.

- [ ] 🟥 **Step 1: Align status base hues + add the two missing tokens** — bring `theme.ts` status values to the spec and fill the gaps the redesign introduced.
  - [ ] 🟥 Base hues: `success #059669→#1FAB76`, `error #DC2626→#E5484D`, `info #2F6FB0→#3E84CC`, `thinking #7C3AED→#8B5CF6`
  - [ ] 🟥 Add **base `warning: #EFA417`** (currently absent — required for the new dot/left-rule)
  - [ ] 🟥 Add **`infoText: #2C6299`** (only status family missing its `*Text`)
  - [ ] 🟥 Status text: `successText #166534→#157A53`, `errorText #991B1B→#C0303A`, `errorTextAlt #B91C1C→#D14A4E`, `warningText #92400E→#8A6111`
  - [ ] 🟥 Soft tints (8): `successBg #E6F4EF→#E8F6EF`, `successBorder #C8E6D2→#C8E9DA`, `errorBg #FBE9E9→#FCECEC`, `errorBorder #F4CFCA→#F6D5D6`, `infoBg #ECF3FA→#EAF2FB`, `infoBorder #CFE0F0→#D2E2F3`, `warningBg #FDF4E0→#FCF3DF`, `warningBorder #F4E2B0→#F3E2BB`
  - **Verify:** `cd web && npx vitest run __tests__/PipelineStages.test.tsx __tests__/SettingsModal.test.tsx` — update the success rgb in PipelineStages (`5,150,105→31,171,118`) and error rgb in SettingsModal (`220,38,38→229,72,77`) so both pass. Then full `npx vitest run`; fix any other status-colour assertion to the new rgb.

### Phase 2: Badges → outline pills with dot (`uiStyles.ts` + 7 call sites)
- [ ] 🟥 **Step 2: Rebuild `ui.badge*` primitives** — transparent fill, thin status border, neutral label; expose a shared dot.
  - [ ] 🟥 `badgeBase` → `background: transparent`, `color: pwc.grey800`, `border: 1px solid pwc.grey300`, keep pill radius + gap; padding to spec `3px 11px`
  - [ ] 🟥 Per-variant overrides **only `borderColor`** to the status hue (`success`/`warning`/`error`/`info`, `orange500` for brand); neutral keeps grey300
  - [ ] 🟥 Add exported `ui.badgeDot(status)` (or `Dot` helper) = 7px circle, `background` = status hue (grey500 for neutral)
  - **Verify:** `npx vitest run` — badge tests in `HistoryList`/`ValidatorTab`/`ResultsView`/`RunDetailView`/`ConceptsPage` flip from filled-bg to border assertions; update each to assert `borderColor` + transparent background.
- [ ] 🟥 **Step 3: Emit the dot at each badge call site** — 7 components render a badge.
  - [ ] 🟥 `HistoryList.tsx`, `RecentRunsList.tsx`, `ResultsView.tsx`, `RunDetailView.tsx`, `ValidatorTab.tsx`, `BenchmarksPage.tsx`, `ConceptsPage.tsx`: prepend `<span style={ui.badgeDot(<status>)} />` inside each badge
  - **Verify:** start the dev preview; open History + a run-detail page; confirm each status badge is a transparent pill with a coloured ring + matching dot and a dark-grey label. Screenshot before/after.

### Phase 3: Alerts → neutral surface + left-rule (`uiStyles.ts` + LoginPage)
- [ ] 🟥 **Step 4: Rebuild `ui.alert*` primitives** — neutral surface, hairline border, coloured 3px left-rule; icon carries the hue.
  - [ ] 🟥 `alertBase` → `background: pwc.white`, `border: 1px solid pwc.grey200`, `borderLeft: 3px solid` (per variant), `color: pwc.grey800`
  - [ ] 🟥 Per-variant `borderLeftColor` = status hue (`info`/`success`/`warning`/`error`); drop the soft-fill backgrounds
  - [ ] 🟥 Add `ui.alertIcon(status)` colour token so the icon (not the fill) carries status
  - **Verify:** `npx vitest run` — update any alert bg/border assertion (notably `LoginPage.test.tsx`) to the neutral-surface + left-rule values.
- [ ] 🟥 **Step 5: Apply icon colour at the one alert call site** — `LoginPage.tsx` error alert: colour the icon via `ui.alertIcon('error')`, body stays grey800.
  - **Verify:** preview the login error state (bad creds / force the error branch); confirm white surface, red left-rule, red icon, dark body. Screenshot.

### Phase 4: Typography weight (drop Light 300)
- [ ] 🟥 **Step 6: Replace `pwc.weight.light` usage with `regular`** across the 11 sites in 9 files.
  - [ ] 🟥 `App.tsx:49`, `PageHeader.tsx:66`, `RunDetailView.tsx:675,753`, `TokenDashboard.tsx:55,77`, `EvalTab.tsx:132`, `StatTiles.tsx:85`, `ResultsView.tsx:94`, `LoginPage.tsx:31`, `ReadableDocPage.tsx:210` → `pwc.weight.regular`
  - [ ] 🟥 Fix the stale `theme.ts` comment ("large headings sitting at light weight…") to the two-weight rule (regular 400 + semibold 600; medium 500 on controls)
  - [ ] 🟥 Leave the `weight.light` key in place (name stability); note it's unused in product UI
  - **Verify:** `npx vitest run` (fix any `fontWeight` assertions, e.g. `StatTiles.test.tsx`). Preview dashboard/stat tiles + page headers; confirm large numbers/headings render at regular 400 (no hairline look). Screenshot.

### Phase 5: Full verification + doc reconciliation
- [ ] 🟥 **Step 7: Whole-suite + visual sweep** — prove nothing regressed and the UI matches the doc.
  - [ ] 🟥 `cd web && npx vitest run` fully green
  - [ ] 🟥 Re-read `docs/pwc-design-system.html` Color/Badges/Alerts/Typography against the final `theme.ts`/`uiStyles.ts` — confirm 1:1
  - [ ] 🟥 Spot-check live: badges (History/run-detail), alerts (login), cards/stat numbers, status-text contrast
  - [ ] 🟥 Update memory note `project_pwc_design_system.md` — flip "code follow-through pending" to done
  - **Verify:** suite green + side-by-side screenshots (badges row, an alert, a stat tile) visually matching the doc's component specimens.

## Rollback Plan
If something goes badly wrong:
- All changes are confined to `web/src/lib/theme.ts`, `web/src/lib/uiStyles.ts`, `web/src/index.css`, ~9 component files, and their tests — **no DB/schema/backend impact**. Revert with `git restore` per file or `git revert` the phase commit.
- Each phase is an independent commit; a broken phase reverts without touching earlier green phases.
- If a token change cascades to an unexpected component, the "no hardcoded status hex" finding means the fix is always in `theme.ts` — check there first, not the component.
- Check after revert: `cd web && npx vitest run` returns to the Phase-0 baseline green.
