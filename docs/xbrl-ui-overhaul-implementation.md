# XBRL focused-workspace implementation matrix

Status: implemented. This matrix is maintained with the code,
canonical design specification, and pinning tests. Direction A (“Focused
workspace”) is the selected production direction. Prototype directions B/C and
the prototype switcher are reference-only.

## Governance and contracts

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Keep the XBRL authority in lockstep | Update `docs/xbrl-design-system.html`, Direction A behavior, `theme.ts`, `uiStyles.ts`, `index.css`, and the shared parity tests together. `docs/pwc-design-system.html` is compatibility-only. | Design-system, CSS-token, contrast, UI-style, and motion tests. |
| Preserve inline component styling | Keep stable geometry, type, color, and layout in inline `style={}` objects. Limit CSS classes to hover, focus, animation, responsive composition, and editor states. | Source/pinning tests and final diff audit. |
| Preserve product contracts | No change to authentication, authorization, canonical facts, run lifecycle, API routes, persistence, reviewer jobs, or workbook generation. | Existing frontend suite; backend pinning tests applicable to unchanged boundaries. |
| Preserve tab behavior | Keep `Run detail sections` and `Sheet-12 sub-agents` as separately labelled tablists. Keep heavy run panels conditionally mounted. | `RunDetailView`, `NotesSubTabBar`, routing tests. |
| Avoid invented source/dependency data | Show authoritative page-level source navigation only. Do not fabricate PDF regions, general parent agents, or `waiting_for` relationships absent from the API. | Existing source-pane and event/replay tests; documented limitation. |

## Foundation

| Requirement | Tokens/primitives | Component effect | Tests |
| --- | --- | --- | --- |
| Narrow palette | Orange ladder, black/white, cool Grey 50–500 in `theme.ts`. | Orange is limited to identity, current activity, and attention. | Exact token/spec parity. |
| White work surface | `tokens.surface.canvas/default`; `surface.navigation` is Grey 50. | White page and review canvas with one persistent grey rail. | CSS/token parity and shell tests. |
| Text hierarchy | Black primary/body; 64% secondary; 46% tertiary. | Three main levels: 28px page, 16px section, 14px body; small metadata remains valid. | UI-style and contrast tests. |
| Bundled variable Inter type | `@fontsource-variable/inter` is loaded once in `main.tsx`; shared heading/body stacks in `theme.ts` prefer `Inter Variable`. | Intermediate weights render consistently across platforms instead of collapsing to fallback font weights. | CSS/spec parity and production build. |
| Spacing/radius | Existing 4px spacing scale; 6/8/12px radii. | Compact controls and flat large surfaces. | UI-style parity. |
| Flat surfaces | `ui.flatList`, `ui.flatRow`, `ui.paneDivider`; shadows reserved for overlaps. | Divided rows and panes replace unnecessary nested cards. | UI-style parity and component tests. |
| Primary action hierarchy | Black fill, white text, restrained orange inset hover cue. | Orange is no longer a large button fill. | Button/contrast/CSS tests. |
| Status language | Working/attention orange, finished black, waiting grey; every state retains text/icon. | Agent rows, pipeline, history, toast, and review status remain understandable without color. | Agent/status/component tests. |
| Focus | 2px black focus ring with 2px offset. | Visible on white, grey, and orange-tint surfaces without adding another accent. | CSS/forced-colors pins. |

## Application shell and navigation

| State | Implementation | Responsive/accessibility behavior | Tests |
| --- | --- | --- | --- |
| Default workspace | 220px sticky Grey-50 sidebar, white workspace, 64px sticky context bar. | Top-level destinations remain real links with stable URLs and `aria-current`. Skip link still targets `main#main-content`. | `App`, routing, and `TopNav` tests. |
| Review-focused workspace | Collapse the rail to 72px only in Figures and Notes review while preserving each link’s accessible text. Overview and live-run monitoring keep the full rail. | Glyphs are decorative; labels remain in the accessibility tree when visually compacted. | Shell class/ARIA and routing tests. |
| Mobile | Fixed 64px bottom navigation with horizontally scrollable destinations; compact sticky top context. | No destination is removed; content receives bottom clearance; targets stay at least 44px. Hidden labels reappear as visible hover/focus tooltips. | CSS responsive pinning and browser QA. |
| Auth/admin utilities | Preserve existing signed-in visibility and server authorization; Settings remains directly reachable and Logout stays in the top context bar at mobile widths. | UI visibility is never treated as a security boundary. | Existing auth/nav/settings and shell-placement tests. |

`Work queue` and `New extraction` are separate Direction A destinations while
reusing the existing Extract route and stable upload tree. The selected mode
controls whether the queue or upload surface is shown; draft rehydration and
the canonical run path remain unchanged.

## Screens and states

| Surface | Concrete implementation | Interaction and state requirements | Tests |
| --- | --- | --- | --- |
| Work queue | `ExtractPage`, `HomeHero`, `StatTiles`, `RecentRunsList`. | Compact header, local upload action, flat metrics and divided recent rows, direct resume/review/open actions. Loading failure never blocks upload. | Home, stats, recent-runs, extract tests. |
| New extraction | Existing `UploadPanel`, `FileDropzone`, `PreRunPanel`, statement/notes configuration. | Large PDF/Word drop target, explicit filing standard/level/denomination, statements and notes, clear save/start hierarchy. | Upload/drop/config tests. |
| Runs | Existing `HistoryPage`, filters, list, unified detail. | Search/filter, honest status and issues, resumable drafts, stable native links. | History page/list/filter tests. |
| Live run | Existing reducer state rendered by `ExtractPage`, `PipelineStages`, `AgentTabs`, and focused detail. | Same roster moves through waiting/working/attention/finished; Working/All/Finished filters stay outside the ARIA tablist and move selection to a visible row; stop/rerun remains immediate; one stable status region announces the latest semantic update; raw tools are collapsed. Starting another extraction while streaming reveals the active run without replacing its URL. | Extract, agent tabs/panel/timeline, pipeline, and app routing tests. |
| Completed run | Existing `ResultsView` and unified `RunDetailView`, with a filtered workstream roster and one focused agent detail pane. | Readiness and issue counts lead; the selected agent exposes its latest semantic update and persisted page reference while raw tool events stay unmounted until Technical activity opens; downloads/mTool remain gated by authoritative terminal status. | Result/run-detail roster, ordering, disclosure, and mTool tests. |
| Figures | Existing `ConceptsPage`, reconciliation queue, and `PdfSourcePane`. | Preserve template order, leaf-only canonical edits, right-aligned tabular values, selection/filter/scroll state, and one-click page evidence when a page is recorded. Rows without page evidence render no source button and require manual verification. Never animate financial values. | Concepts/reconciliation/PDF tests. |
| Notes | Run-detail Notes opens the unified `ConceptsPage` review workspace on its Notes mode, with coverage nav, TipTap, and PDF source seam. | The note inventory is open and searchable; selection couples the note/editor/source panes; audit-only panels remain collapsed and unmounted until requested; affected-note warnings and saving remain available. Do not alter source-styled/sanitizer/clipboard behavior. | Notes review/coverage/focus/source and run-detail lazy-mounting tests. |
| Loading/empty/error/success | Shared Skeleton, EmptyState, alerts/dialogs/toast. | State text and next actions appear immediately; motion never determines truth. | Motion primitives and existing component tests. |

## Responsive composition

| Width | Required behavior |
| --- | --- |
| Wide desktop | Persistent rail, dominant work surface, source/agent detail visible beside it. |
| Laptop | Narrow secondary rails before reducing the core financial surface. |
| Tablet | Stack agent detail and PDF source below the primary surface; do not hide evidence. |
| Mobile | Bottom/horizontal app navigation, horizontally scrolling review rails, stacked note rows, and horizontally scrollable financial tables with sticky identifiers and period labels. Monitoring, evidence reading, and simple actions remain usable. |
| 320px / 200% zoom | Controls remain reachable and focused content is not obscured by sticky chrome. Full financial editing remains a desktop-oriented task. |

## Accessibility

- Top-level navigation uses links and `aria-current`; resource views retain
  their tab keyboard models.
- Agent filters are buttons with `aria-pressed`; the vertical roster retains
  roving focus with Arrow Up/Down, Home, and End. Filtering immediately selects
  the first visible workstream when the prior selection is no longer shown.
- Status is never color-only. The selected agent’s semantic latest update is
  `aria-live="polite"`; token/tool chatter is not announced.
- Icon-only actions keep accessible names and shared hover/focus tooltips.
  Focus uses a visible black ring, sticky regions leave scroll margin, and
  forced colors retain boundaries.
- Controls use the exact 40px default and 34px compact Direction A geometry.
  Mobile navigation remains at least 44px high.
- Reduced motion presents the same final state immediately.

## Motion conventions

| Purpose | Convention | State hook |
| --- | --- | --- |
| Immediate response | 100ms, no delayed controls | Press/focus feedback. |
| Hover/status | 140ms standard easing | Navigation, buttons, status arrival. |
| View/reveal | 180ms standard or emphasized easing | Page/tab entry, disclosure/editor/tool detail. |
| Larger causal change | 240ms emphasized easing | Progress fill or pane-state feedback only. |
| Active work | Restrained opacity loop while genuinely working/loading. | `.pwc-working-indicator`, `.pwc-skeleton`, `.pwc-spinner`. |

Motion uses transform and opacity. The prior `max-height` reveal is removed.
Navigation selection and pipeline text update immediately; animation never
reorders or delays pipeline truth. There is no continuous decorative motion,
no card lift, and no value tween in Figures or Notes. The global
`prefers-reduced-motion` rule disables entrances, loops, transitions, smooth
scroll, and count-up behavior while preserving clear static state.

## Verification checklist

- Targeted Vitest: shared design parity, shell/nav, Extract/agents/pipeline,
  run tabs/lazy mounting, Concepts/PDF, Notes review/coverage/focus.
- Full `npx vitest run` and `npm run build`.
- Applicable backend pinning checks: auth middleware, lifecycle/status events,
  notes integrity false-green protection, and mTool gates only if a touched
  frontend contract warrants them. No live/regression LLM suites.
- Browser QA at representative desktop, tablet, and mobile widths, including
  live/attention/complete, Figures selection/source, Notes selection/source,
  keyboard focus, and reduced motion when the local application is runnable.

## Contract-limited outcomes

- Current events do not provide authoritative general `parent_agent_id` or
  `waiting_for` fields. Existing role, status, semantic message, Notes-12 child
  metadata, timestamp, and page activity are shown without inventing general
  hierarchy or handoffs.
- Current source contracts support authoritative PDF page jumps, not universal
  bounding boxes. Exact region highlights appear only if future APIs provide
  coordinates.
- Uploaded mTool physical addresses are unavailable until column detection;
  the review surface mirrors the canonical template destination and period/
  entity structure without claiming an undetected physical address.
