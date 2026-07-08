# PLAN — UI/UX Plain-Language Overhaul

**Status:** Draft for review — vocabulary table (§2) needs product sign-off before Phase 1 starts.
**Date:** 2026-07-08
**Source:** Full-site UI/UX audit (five parallel sweeps: user journey, copy/errors, review-surface
density, design-language conformance, settings/AI transparency). Findings are summarized inline;
file:line references point at the audited code.

---

## 1. Product decisions (locked in with the operator, 2026-07-08)

These answers scope everything below:

1. **One UI for both audiences.** No mode toggle. Engineering detail (telemetry, JSON traces,
   raw args, token counts, tracebacks) stays available but moves **behind expand-to-view
   disclosures** — collapsed by default, one click away.
2. **Plain English only.** No Bahasa Malaysia / i18n framework this round. Strings can stay
   in-component; shared vocabulary moves to one module so terms stay consistent.
3. **AI plumbing becomes admin-only.** Proxy URL, API key, model name, default model roles,
   reviewer/spot-check toggles gate on `is_admin` (the boundary already exists, schema v20).
4. **Pre-run panel gets minimal.** Default view: Filing Standard, Filing Level, Denomination,
   plus user confirmation of which statements and notes sheets apply. Everything else
   (model pickers, eval testing, scanned-PDF toggle) folds into an "Advanced" disclosure.
   Variants stay visible (they're an accounting choice) but get plain-English labels.
5. **Internal codenames get renamed** to terms a Malaysian auditor understands (§2).
6. **Holistic delivery, no priority constraint** — phases below are ordered by dependency and
   risk, not by user preference.
7. **Eval/Benchmarks: minimal touch.** The feature gets a holistic revamp later. This plan only
   moves it out of the default experience (admin-only nav + out of the default pre-run view).
   No copy rework inside eval surfaces.

---

## 2. Vocabulary map (NEEDS SIGN-OFF)

One shared module (`web/src/lib/vocabulary.ts` — new) exports these terms so every surface uses
the same word. Backend status/SSE strings adopt the same vocabulary (server.py emits them).

| Current term (where) | Proposed plain-English term | Notes |
|---|---|---|
| Scout / "Auto-detect" (PreRunPanel, ScoutToggle) | **"Document pre-scan"**; button stays "Auto-detect"; described as "Reads your PDF first to detect which statements and notes it contains" | codename retired from UI |
| Agent / sub-agent / "Agent Activity" (ExtractPage) | **"Extraction progress"**; individual agents referred to by statement name (e.g. "Statement of Financial Position") | "agent" never shown |
| Correction agent / Reviewer (stage lines, ReviewTab) | **"AI review"** — stage line: "AI review: re-checking flagged figures against the PDF…" | one name for the reviewer pass |
| Notes reviewer / notes formatter | **"Notes review"** / **"Notes formatting"** | |
| Cross-checks (tab, ValidatorTab) | **Keep "Cross-checks"** — audit-adjacent and understood | check *names* get friendly labels (see Phase 1) |
| Telemetry (tab) | Tab removed; content becomes **"Performance details"** expander inside the activity view | expand-to-view |
| Tokens / Prompt / Completion / Turns / Est. Cost | **"AI usage"** summary behind an expander; raw table inside | never a headline metric |
| "turn budget N" (server status strings) | dropped from user-facing text | |
| Values (tab) + "Review values" | **"Figures"** — one tab, one name | |
| Review (tab) | **"AI review"** | |
| Agents (tab) | **"Activity"** | hosts per-statement timeline + Performance details expander |
| Eval (tab) / "Eval testing" (pre-run) | admin-only; unchanged inside | revamp later |
| "Template" (nav tab) | nav item removed; label editor moves to Settings → **"Field labels"** | fixes the dual-destination route |
| Benchmarks (nav tab) | admin-only nav item | revamp later |
| Variant codes (VariantSelector) | CuNonCu → "Current / Non-current", OrderOfLiquidity → "Order of liquidity", Function → "By function", Nature → "By nature", BeforeTax → "Before tax", NetOfTax → "Net of tax", Indirect → "Indirect method", Direct → "Direct method", NotPrepared → "Not prepared" | display-only; API values unchanged |
| Re-review (two buttons) | **"Run AI review again"** (figures) / **"Run notes review again"** (notes) | disambiguates the pair |
| Regenerate notes | **"Re-extract notes (replaces your edits)"** | consequence in the label |
| Revert to original / Revert formatting / Use firm default | **"Restore original extraction"** / **"Remove formatting changes"** / **"Reset style to firm default"** | three distinct undo verbs, each saying what it undoes |
| Download filled workbook / final Excel / Merged Excel / Fill & download | **"Download filled Excel"** everywhere (mTool modal keeps "Fill & download" — it's a different artifact) | one name |
| Flag kinds `stuck` / `disputes_prior` / `needs_human` (raw enums) | "Couldn't resolve — needs your decision" / "Disagrees with an earlier figure" / "Needs your review" | rendered labels, enum untouched |
| Coverage chip `fan-out` / `carve-out` | dropped from chip text (kept in tooltip for engineers) | |
| Sub-note states `cited` / `not_verified` / `verified` / `missing` | "Mentioned" / "Not checked" / "Checked" / "Missing" | |
| JSON / Trace download buttons | grouped under a **"Diagnostics"** expander, labelled "Raw data (JSON)" / "AI conversation log" | |

**Terms that stay as-is (correct domain language):** SOFP/SOPL/SOCI/SOCF/SOCIE/SoRE codes,
MFRS/MPERS, Company/Group, Denomination (RM / RM '000 / RM mil), mTool, note sheet names.

---

## 3. Phases

Ordering rationale: Phase 1 (error plumbing + vocabulary) is the foundation every later phase
renders through. Phases 2–4 are structural but independent. Phase 5 is the riskiest (touches
NotesReviewTab/ConceptsPage monoliths) and goes last among the big ones. Phases 6–8 are smaller and mostly independent; Phase 7's per-component animations
ship alongside whichever phase owns the component (noted inline).

### Phase 1 — Error rendering + vocabulary foundation

**Goal:** no user ever sees `[object Object]`, `HTTP 422`, a Python traceback, an env-var name,
or a raw enum. One shared vocabulary module.

- **Frontend error helper** (`web/src/lib/errors.ts` — new): every `catch` path routes through
  one `userMessage(err)` that (a) never stringifies objects, (b) maps HTTP status classes to
  plain sentences with a next step, (c) tucks the raw payload into an expandable "Technical
  details" block (the operator's expand-to-view preference). Replace the ~18 bare
  `e.message` banners (HomeHero, PreRunPanel, UsersTab, ReviewTab, UploadPanel,
  AgentTelemetryPanel, AccountTab, NotesCoveragePanel, ResultsView, NotesReviewerPanel,
  GeneralSettingsForm, HistoryPage, TemplateSettingsPage:68 `String(err)`) and every
  `throw new Error(\`HTTP ${r.status}\`)` (NotesCoveragePanel:106, NotesReviewerPanel:60-71,
  ReviewTab:87-110, MtoolFillModal:325-462, api.ts:24/69, sse.ts:167/234/289).
- **Extraction error box** (ExtractPage.tsx:391-399): plain headline + what-to-do line;
  traceback moves inside "Technical details" with a copy-for-support button.
- **Backend user-safe messages:** rewrite strings the SPA renders —
  server.py:4257/4294/4526/2889/2898 ("Stream error…", "Coordinator error…", merge failures),
  api/run_control.py:72/148-151/206-210/360/398 (env-var names, "draft", "legacy row"),
  api/run_control.py:161/165 (return flat strings, not pydantic error arrays),
  api/mtool.py:84-95/244/253-254/270-271/537-639 (JSON key paths → instructions),
  ingest/word_convert.py:189/233/247/313 (add the missing `user_message`s).
  Pattern to follow: word_convert's existing `user_message` and LoginPage copy.
- **Vocabulary module** (`web/src/lib/vocabulary.ts`): the §2 table as exported constants +
  label-map helpers (flag kinds, coverage states, sub-note states, variant display names,
  cross-check display names). Cross-check names (ValidatorTab.tsx:103 renders raw keys like
  `socie_to_sofp_equity`) get a display-name map with a safe fallback.
- **Status fallback guard** (runStatus.ts:71): unmapped status renders "In progress" /
  "Finished (see details)" style fallback, never the raw enum.

**Tests:** new pinning tests for `errors.ts` + vocabulary maps; update existing tests asserting
exact error strings (grep before/after). Backend: tests pinning HTTPException details
(test_mtool_routes, test_server_* files) updated in the same commits.

### Phase 2 — Navigation & information architecture

**Goal:** every destination is named for what the user gets; engineering destinations leave the
primary nav.

- **TopNav** (TopNav.tsx:21-28): `Extract · History` for everyone; `Benchmarks` renders only
  for `is_admin` (from `/api/auth/me`). "Template" nav item removed.
- **Template label editor** → Settings tab **"Field labels"** (SettingsPage gains a 4th tab;
  TemplateSettingsPage reused as-is; empty-state copy de-jargoned: drop "canonical mode" /
  "template registry").
- **Run-detail tabs** (RunDetailView.tsx:398-414) renamed per §2: Overview · Figures · Notes ·
  Cross-checks · AI review · Activity (+ Eval only for admins when a benchmark is attached).
  Telemetry tab removed; AgentTelemetryPanel mounts inside Activity as a collapsed
  "Performance details" section per agent (trace JSON stays inside it, one level deeper).
  **Gotcha #7 applies:** the tablist aria-labels and `within(...)`-scoped tests must be updated
  together; keep the roving-tabindex pattern.
- **One review door** (ResultsView.tsx:287-307 + RunDetailView.tsx:477-518): results screen
  keeps a single primary "Open run report" action + "Download filled Excel"; the redundant
  "Review values" header button on the run page is removed (the Figures tab is the door).
- **Page descriptions:** pass `description` to PageHeader on Extract ("Upload a financial
  statement and extract it into the SSM MBRS template"), History, Settings, Field labels.
- **Settings discoverability:** gear keeps working; add "Settings" to the user menu / label the
  gear with text on wide viewports.
- **Route aliases** (`/concepts/{id}` etc.) unchanged — cosmetic-only phase, no routing rework.

**Tests:** RunDetailView tab tests, TopNav tests, HistoryPage/ResultsView tests updated with new
labels; new test pinning admin-only Benchmarks nav.

### Phase 3 — Pre-run simplification

**Goal:** upload → confirm three accounting choices + which sheets apply → Run.

- **Default view** (PreRunPanel.tsx:993-1375): Filing Standard, Filing Level, Denomination,
  then "Statements to extract" (checkboxes + variant dropdowns with §2 plain labels — the
  user-confirmation requirement) and "Notes sheets to fill" (checkboxes). Run button visible
  without scrolling on a laptop viewport.
- **"Advanced" disclosure** (collapsed): per-statement model dropdowns
  (StatementRunConfig.tsx:107-119), per-note model dropdowns (NotesRunConfig.tsx:97-110),
  pre-scan model picker (ScoutToggle.tsx:129-143), "Scanned PDF" toggle (reworded: "My PDF is
  a scanned image (no selectable text)"), Eval testing block (admin-only, PreRunPanel.tsx:
  1097-1165 — also stops blocking Run for non-admins by construction).
- **Pre-scan framing:** section explains itself ("Document pre-scan — reads your PDF to
  suggest statements, formats and note locations. Results are suggestions; confirm below.").
  Scout-empty message (PreRunPanel.tsx:710) reworded to an actionable step ("No statements
  detected. Tick the statements you can see in the PDF, or turn on 'scanned image' under
  Advanced and try again.").
- **Confidence dots** (VariantSelector.tsx:129-151) get a visible legend line, not tooltip-only.

**Tests:** PreRunPanel/ScoutToggle/VariantSelector/StatementRunConfig web tests — labels and
visibility assertions change; add a test pinning "model dropdowns live under Advanced".

### Phase 4 — Live-run narrative

**Goal:** during a run the user always knows what's happening in plain words, whether it's
still working, and never needs the raw feed.

- **Stage sentences** (ExtractPage.tsx:259-272 + server.py `_emit` strings 1549-1567/1942-1967)
  rewritten per §2 ("AI review: re-checking flagged figures…"; drop "turn budget", "prose-notes
  findings", "Correction agent").
- **Tool feed:** keep friendly labels (toolLabels.ts is already good); fallback for unmapped
  tools becomes "Working…" + the raw name inside the expander (toolLabels.ts:36); ms timings
  and monospace arg previews move inside the card's expanded state (ToolCallCard.tsx:145/276);
  expanded raw JSON stays (it *is* the expand-to-view layer) but gains a one-line plain summary
  above it.
- **Liveness:** show elapsed time on the active step + a "Still working — large documents can
  take several minutes" line when a step exceeds a threshold; render a lightweight "thinking…"
  shimmer for the dropped `thinking_delta` gaps (buildToolTimeline.ts:181-183) so the screen
  is never silent.
- **TokenDashboard** (ExtractPage.tsx:275-277) collapses into an "AI usage" expander.
- **Results summary cards** (ResultsView.tsx:356-383) reorder: Status · what-was-extracted ·
  Elapsed headline; Tokens/Est. Cost move into the AI-usage expander. Downloads: one primary
  "Download filled Excel"; JSON/Trace under a "Diagnostics" expander; emoji icons (📊 📄 🔍,
  ResultsView.tsx:514-544) replaced with icons.tsx glyphs.
- **Honest-completion flag** (AgentTabs.tsx:205-210): chip label becomes "Needs your review";
  the model's free-text `agent.flag` moves into the expanded detail, not the tooltip headline.

**Tests:** test_pipeline_stage_events.py pins stage labels — update backend strings + frontend
`PipelineStage` union together (gotcha #19). ExtractPage/ToolCallCard/ResultsView web tests.

### Phase 5 — Run report & notes de-cluttering (highest structural risk) — 🟩 DONE (one item deferred)

**Status note (2026-07-08):** Shared `ConfirmDialog`/`Disclosure`/`Skeleton`
primitives + motion tokens (theme.ts `motion`, index.css reduced-motion) landed
as a foundation up front (Phase 7 material, pulled forward because 5/6 consume
them). Notes tab: coverage + reviewer panels collapse to summary bars (coverage
auto-opens when a gap needs attention); editor is the default surface. All four
`window.confirm` calls → shared `ConfirmDialog`; `ConfirmRegenerateModal` removed.
Verb unification applied (§2). Coverage chip fan-out/carve-out moved to tooltip.
ConceptsPage: duplicated notes-editor embed removed (hands off to the Notes tab),
"Download filled Excel" everywhere. **Deferred:** the editor-toolbar dropdown
reorg (Tier-1 overflow + Tier-2 Borders/Fill/Structure dropdowns) — the toolbar
already carries labelled `group(...)` captions addressing the discoverability
complaint, and converting the selection-guarded TipTap controls to popover menus
is the highest-risk change for marginal gain; flagged for a follow-up.

**Goal:** the Notes and Figures tabs stop stacking equal-weight panels; duplicate verbs merge.

- **Notes tab composition** (RunDetailView.tsx:582-588): coverage checklist and notes-review
  panels become collapsed summary bars ("Coverage: 41 of 43 notes placed — view details",
  "AI notes review: 3 changes, 1 flag — view details") that expand in place; the editor is the
  default-visible surface. Table-style panel (ClipboardFormatControls, 13 controls) opens only
  from the "Table style" button, framed as "how pasted tables look".
- **Editor toolbar** (NotesReviewTab.tsx:1558-1837): Tier 1 keeps the basics (bold/italic/
  underline, lists, alignment, insert table); colour/highlight/super-subscript/indent move into
  a "More formatting" overflow. Tier 2 table controls group under labelled dropdowns
  (Borders ▾ / Fill ▾ / Structure ▾) instead of 31 flat glyph buttons. Cryptic glyphs get
  text-labelled menu items inside the dropdowns.
- **Verb unification** per §2: the three redo verbs and three undo verbs get their new labels;
  each destructive one confirms via **one shared ConfirmDialog component** (replacing the four
  `window.confirm` calls at RunDetailView.tsx:390, NotesReviewTab.tsx:729, ReviewTab.tsx:245,
  NotesReviewerPanel.tsx:185 — wording standardized: what will be lost + Cancel/Confirm).
- **Figures tab (ConceptsPage):** no structural rebuild this round (monolith, 2646 lines —
  flagged as tech debt). Scope: rename per §2, remove the duplicated notes-editor embed
  (ConceptsPage.tsx:889-892 — the Notes tab is the single home; the Figures sheet list
  links across), single "Download filled Excel" label, keep one entity-scope toggle visible
  at a time.
- **AI review tab (ReviewTab) + notes review panel:** flag cards render §2 labels; raw
  `{f.kind}`/`{f.status}`/`{sub.state}` (ReviewTab.tsx:398-400, NotesReviewerPanel.tsx:352-354,
  NotesCoveragePanel.tsx:269) go through vocabulary maps.
- **Invariant watch:** notes overlay/tombstone contract (gotcha #16) — panel restructure must
  not touch the PATCH/persistence flow; NotesSubTabBar keeps its `role="tab"` +
  aria-label scoping (gotcha #7); reviewer interlocks untouched.

**Tests:** NotesReviewTab / NotesReviewerPanel / NotesCoveragePanel / ReviewTab / ConceptsPage
web tests — the biggest test-update surface of the plan; budget accordingly.

### Phase 6 — Trust, safety, and admin gating — 🟩 DONE

**Status note (2026-07-08):** AI plumbing (proxy URL / API key / model / auto-
review / spot-check / entity-memory) renders read-only for non-admins in
`GeneralSettingsForm` with a "Managed by your administrator" note; admins see
"These settings apply to everyone." Server-side, `/api/settings` refuses a
non-admin write touching any admin-only key via `auth_routes._require_admin`
(cosmetic `notes_table_style` stays open). Settings copy de-jargoned (no more
"Enterprise LiteLLM" / "Bruno"; entity-memory reworded). Per-agent Rerun and
the UsersTab disable/enable + make/revoke-admin actions now confirm via the
shared `ConfirmDialog`. One persistent AI disclaimer line on the results
surface and the run-report header. Inputs in GeneralSettingsForm / AccountTab /
UsersTab adopt `ui.input` (layout item #1). New `tests/test_settings_admin_gate.py`
pins the server gate; web suite 919 green.

- **Admin-only AI plumbing:** General settings' Proxy URL / API Key / Model Name / default
  model roles / auto-review / spot-check / entity-memory render read-only ("Managed by your
  administrator") for non-admins; server-side: the corresponding `/api/settings` keys reject
  non-admin writes via `_require_admin` (the UI is not the boundary — mirror the
  /api/admin/users pattern). Admin view adds one banner: "These settings apply to everyone."
- **Settings copy:** "From Bruno → Collection → Auth tab" (GeneralSettingsForm.tsx:361) and
  "Enterprise LiteLLM proxy endpoint" (:335) rewritten for the admin persona in plain terms;
  "Turn off if entity names collide" (:450) reworded.
- **Confirmations:** per-agent Rerun (ExtractPage.tsx:589-599) and UsersTab actions
  (disable/enable/revoke admin/reset password, UsersTab.tsx:191-229) get the shared
  ConfirmDialog from Phase 5.
- **AI disclaimer:** one persistent line on the results surface and run report header:
  "Figures were extracted by AI — verify against the source PDF before filing." Placement
  once, not per-panel.

**Tests:** test_settings_api.py gains admin-gate cases; UsersTab/GeneralSettingsForm web tests.

### Phase 7 — Motion & perceived speed — 🟩 CORE DONE (some micro-anims light-touch)

**Status note (2026-07-08):** Motion tokens (`theme.ts` `motion`), the global
`prefers-reduced-motion` block, and the `skeleton-shimmer`/`dialog-in` keyframes
shipped in the Phase 5 foundation commit, along with the shared `Disclosure`
(slide-open + chevron rotate) and `ConfirmDialog` (scale-in) primitives. This
phase added the `Skeleton`/`SkeletonText` primitives and used them for the
History list and the Review / Notes-review / Coverage panel loading states
(replacing "Loading…" text), plus a crossfade transition on the run-report tab
indicator. Live-feed tool-call cards already fade in (Phase 4). **Light-touch /
follow-up:** count-up stat numbers, per-cell save-flash, pipeline-strip fill,
status-badge crossfade, and an exhaustive universal-button-busy audit were left
as polish — most action buttons already carry busy labels ("Reviewing…",
"Restoring…", "Filling…"), and count-up/save-flash carry real test risk in the
value/notes monoliths for marginal gain.

**Goal:** the app feels snappy and deliberate. Two halves: perceived-speed mechanics (the bigger
payoff) and micro-animations. Both must stay inside the PwC design language — motion follows the
same restraint as the visual system ("depth felt, not seen"): purposeful, fast, never bouncy.

**Motion tokens first (one commit):** extend `theme.ts` with a `motion` object —
`duration.fast: 150ms` / `duration.base: 200ms` / `duration.slow: 250ms`, one easing curve
(`cubic-bezier(0.2, 0, 0, 1)` — decelerate, no overshoot), and add a global
`@media (prefers-reduced-motion: reduce)` block to `index.css` that zeroes animation/transition
durations (currently absent). All motion below consumes these tokens; no ad-hoc durations.
Existing keyframes (`pulse-subtle`, `spin`, `fade-in`, `slide-down`, `glyph-pulse` in
index.css:23-58) are reused/retimed, not duplicated.

**8a — Perceived speed (priority slice, independent of animations):**
- **Skeleton loading** for History list, run report tabs, and reviewer panels: grey placeholder
  bars (`pwc.grey100`, shimmering to `grey50`) in the shape of the coming content, replacing
  "Loading…" text. One shared `Skeleton` primitive in uiStyles.
- **Stale-while-refreshing:** polling panels (reviewer status, coverage, telemetry) keep prior
  content visible with a subtle updating shimmer instead of blanking to a spinner.
- **Universal button busy states:** every action button flips to spinner-in-button + progress
  label on press ("Filling…", "Reverting…"); no dead clicks. Audit each `disabled={busy}`
  button for a missing busy label.
- **Optimistic save feedback:** figure/note-cell saves show the saved state immediately and
  reconcile in the background; failures surface via the Phase-1 error helper.

**8b — Micro-animations (ride along with the phase that owns each component):**
- **Shared disclosure animation** (ships with Phase 2's expand-to-view pattern): one
  `Disclosure` primitive — content slides open at `duration.base`, chevron rotates 90°. Used by
  Technical details, Performance details, AI usage, Diagnostics, Advanced settings.
- **Live-feed entrances** (Phase 4): new tool-call cards fade in with a ~4px upward slide at
  `duration.fast`; only animate the newest card, never the whole list.
- **Status crossfades** (Phase 4): Running→Complete badge colors crossfade; checkmark draws
  with a quick stroke.
- **Pipeline strip fill** (Phase 4): connector line fills toward the next step.
- **Count-up numbers** (Phase 4): stat tiles count up over ~400ms on first render only.
- **Tab underline slide** (Phase 2): active-tab indicator slides between tabs.
- **Toast + dialog entrances** (Phases 5/6): toast slides in from edge; the shared
  ConfirmDialog scales 97%→100% with fade at `duration.fast`, backdrop fades.
- **Row hover lift** (Phase 8): History rows get background tint + `pwc.shadow.card` on hover.
- **Save-flash** (Phase 5): saved cells flash a soft `pwc.successBg` tint fading over ~1s.

**Hard rules:** animate transform/opacity only (no width/height/top animating on large tables —
enterprise Windows laptops are the floor hardware); nothing over 300ms except count-up and
save-flash decay; no motion on first paint of long lists (stagger the first ~8 rows max); the
notes editor never animates while typing; every animation must answer "what just changed?".

**Tests:** snapshot-level only — assert reduced-motion block exists, Disclosure/Skeleton
primitives render; do not pin durations in tests.

### Phase 8 — Design-language polish + layout normalization — 🟩 CORE DONE (broad sweeps light-touch)

**Status note (2026-07-08):** Fixed the load-bearing token drifts: index.css
danger-hover reds → the real `errorBg`/`error` values (added to the cssTokens
lockstep guard); EvalTab hero number 56px → 30px; ScoutToggle knob shadow →
`pwc.shadow.card`; `#fff` literals → `pwc.white` in the touched panels. Modal
scrims/shadows are unified (the off-spec `ConfirmRegenerateModal` was deleted in
Phase 5; MtoolFillModal already used the canonical scrim + `shadow.modal`).
AgentTabs already used `pwc.warningText`; emoji buttons became icons in Phase 4.
Added the four new `uiStyles` primitives the plan calls for — `ui.cardInset`,
`ui.statTile`, `ui.iconButton`, `ui.thDense`/`ui.tdDense` — each with a pinning
test (`uiStyles.test.ts`); `statTile` adopted in the run-report metric tiles.
**Light-touch / follow-up:** the broad off-grid sweep (NotesReviewTab fractional
paddings, straggler radii/`#fff`), full dense-table adoption across
telemetry/ValidatorTab/coverage, and the AgentTabs status-dot restyle are left
as mechanical cleanups — the primitives now exist for them to converge onto, and
PageHeader's 32px title stays as the documented in-app canon (its comment already
records the deliberate deviation from the reference's size).

Fix the 11 audited drifts: index.css:97-98 danger-hover reds → token values (lockstep rule);
AgentTabs.tsx:207 `#b54708` → `pwc.warningText`; emoji buttons → icons.tsx (done in Phase 4);
modal scrims/shadows unified on `pwc.shadow.modal` + one scrim value (SettingsModal.tsx:32,
MtoolFillModal.tsx:113, NotesReviewTab.tsx:2370-2382); AgentTabs status dots restyled toward
the outline-pill family (or documented as a deliberate compact exception); EvalTab.tsx:131
56px → 28/30px scale; `#fff` literals → `pwc.white`; hardcoded radii → `pwc.radius`;
PageHeader 32px title reconciled with the doc's 28px (decide which is canon, update the other);
ScoutToggle knob shadow → `pwc.shadow.card`; ad-hoc ⚠/✓/✗ unicode centralized in icons.tsx.

**Layout normalization (from the 2026-07-08 spacing audit).** The audit compared every page
wrapper, card, modal, input, button, and table against the design reference and the dominant
in-app values. Canonical metrics (dominant in-app, adopted as the standard):

| Surface | Canonical | Source |
|---|---|---|
| Page container | maxWidth 1120 · gutter 24 · section gap 32 | App.tsx `styles.main` |
| Card padding | 24 (`space.xl`); dense inset box 12 (`space.md`) — promote the 12px inset to a named `ui.cardInset` | uiStyles |
| Modal | padding 24 · radius `lg` (10) · zIndex 50 · backdrop `rgba(0,0,0,0.4)` · `pwc.shadow.modal` | SettingsModal/MtoolFillModal pair |
| Input/select | `ui.input`: 11/16 padding · min-height 44 · radius `lg` · grey300 border | uiStyles.ts:99 |
| Button | 10/24 · min-height 40 (`buttonSm` 8/16 · 36); add a shared icon-button primitive ≥32px hit area | uiStyles |
| Table cells | 16/24 default; add ONE shared dense variant (`ui.thDense`/`ui.tdDense`, 8/12) for data tables | uiStyles |

Ranked fixes (owner phase in parens — items land with the phase already touching the file):

1. **Inputs:** GeneralSettingsForm.tsx:78, AccountTab.tsx:27, UsersTab.tsx:38, PreRunPanel.tsx:116
   all reimplement a shorter off-spec input (8/12, ~34px, radius 6, grey200) — adopt `ui.input`
   (GeneralSettingsForm/AccountTab/UsersTab → Phase 6; PreRunPanel → Phase 3).
2. **ConfirmRegenerateModal** (NotesReviewTab.tsx:2364) deviates from the canonical modal on
   every axis (backdrop, zIndex 1000, radius 8, padding 20, raw shadow) — replaced outright by
   the shared ConfirmDialog (Phase 5). MtoolFillModal heading spacing (12) aligns to 24.
3. **RunDetailView page gutter:** routed through `mainFull` (16px gutter/gap) by accident of
   sharing ConceptsPage's route; move it to the standard 24/32 rhythm (Phase 2). `mainFull`
   stays for the genuinely full-bleed 3-column ConceptsPage only. `mainHistory`'s
   clamp(32–48) gutter converges on the standard 24 unless a run report needs the width.
4. **Stat/KPI tiles:** one tile object rendered at 16 / 24 / 32 padding across StatTiles.tsx:79,
   EvalTab.tsx:126/153, BenchmarksPage.tsx:479/491 — one `ui.statTile` primitive at 16 (Phase 8).
5. **Tables:** only HistoryList uses the canonical 16/24; telemetry (4/8), ValidatorTab (8/12),
   ReviewTab diff (mixed), coverage (0/6) each bespoke — dense tables converge on the shared
   dense variant (Phase 8; ReviewTab/coverage rows land with Phase 5's restructure).
6. **Section rhythm:** 32 (main pages) vs 16 (Notes tab) vs 12 (ExtractPage) — standardize
   inter-section gaps on 32 at page level, 16 within a card (ExtractPage → Phase 4; Notes tab
   → Phase 5).
7. **Off-grid sweep:** NotesReviewTab.tsx is the epicentre (11px/14px/7px/3px paddings,
   fractional 11.5 fontSize, radius 4/8/9 literals); normalize to the 4px grid + radius tokens
   during Phase 5's toolbar/panel rework; stragglers (AgentTimeline.tsx:65, ScoutToggle.tsx:68,
   PreRunPanel.tsx:1119/1238) in Phase 8.
8. **Sub-32px hit areas:** PdfSourcePane.tsx:302 (28px), NotesReviewTab.tsx:2265 (23px),
   ScoutToggle knob, SuccessToast close — raise to the icon-button primitive (Phase 8).

**Tests:** frontend tests assert exact RGB values from theme.ts — each token-affecting change
lands with its pinning test (CLAUDE.md gotcha #7 rule). New primitives (`ui.cardInset`,
`ui.statTile`, dense table variant, icon button) get uiStyles pinning tests.

---

## 4. Out of scope (explicit)

- Eval/Benchmarks internals — future holistic revamp; this plan only gates visibility.
- Bahasa Malaysia / i18n string extraction.
- ConceptsPage / NotesReviewTab monolith refactors beyond what Phase 5 lists (tracked as debt).
- Route-shape consolidation (`/history/{id}` vs `/concepts/{id}` vs `/run/{id}`).
- Native xlsx styling, mTool wizard restructure (copy there is already the house standard).

## 5. Risks

- **Pinned strings everywhere.** Stage labels (test_pipeline_stage_events.py), HTTPException
  details, and exact-label web tests mean every rename is a code+test pair. Grep for the old
  string before each commit.
- **Phase 5 brushes gotchas #7 (tab roles) and #16 (notes overlay).** Composition changes only;
  persistence and role semantics must not move.
- **Admin gating changes API behavior** — Windows/enterprise deployment uses the settings form
  for initial setup; confirm at least one admin exists there before shipping Phase 6 (the
  last-admin guard already prevents zero-admin states).
- **The two "Re-review" buttons are genuinely different backends** — renaming must keep them
  distinguishable, never merged.
