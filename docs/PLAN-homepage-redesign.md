# Implementation Plan: Homepage Split-Hero Redesign

**Overall Progress:** `100%`
**PRD Reference:** No PRD — shaped in /brainstorm session (2026-05-27). See [Summary](#summary).
**Last Updated:** 2026-05-27

## Summary
Redesign the **empty state** of the Extract homepage (`web/src/pages/ExtractPage.tsx`)
from a single full-width upload card into a **two-column split hero**: left keeps the
existing upload card (the hero — "start fast" path untouched), right becomes a "home
base" with four stat tiles (Total runs · Drafts in progress · Completed this month ·
Last run status) and a list of the last 3–5 recent runs (clickable, status badge,
Resume/View). Only the empty/landing state changes — the post-upload flow (config,
agent activity, results) is completely untouched.

## Key Decisions
- **Approach B (split hero), always two columns** — chosen over a vertical stack. Most
  demo-worthy and puts both "start fast" and "resume" above the fold. Audience is
  mixed/demo on desktop laptops, so no mobile stack; instead use sensible min-widths so
  it doesn't crush on a 13" screen.
- **No new backend endpoint** — `GET /api/runs` (server.py:4693) already returns a
  `total` field and supports `status` + `date_from` filters. All four tiles derive from
  the existing API:
  - *Total runs* → `GET /api/runs?limit=1` → read `total`
  - *Drafts in progress* → `GET /api/runs?status=draft&limit=1` → read `total`
  - *Completed this month* → `GET /api/runs?status=completed&date_from=<month-start>&limit=1` → read `total`
  - *Recent list + Last run status* → `GET /api/runs?limit=5` → cards + `runs[0].status`
  This keeps the change frontend-only — no `server.py` / DB repo edits.
- **Reuse existing primitives, don't reinvent** — status colours/labels via
  `runStatusDisplay` (`web/src/lib/runStatus.ts`), card/badge/button styles via
  `web/src/lib/uiStyles.ts` (`ui.card`, `badge*`, `ui.buttonPrimary`), spacing/colour
  tokens via the `pwc` object in `web/src/lib/theme.ts`. Resume navigation reuses the
  existing `onResumeDraft` dispatch pattern from `App.tsx` (lines 494–502).
- **Empty-state gate** — render the split hero only when `state.sessionId == null &&
  !state.isRunning` (the true empty state). The upload card still renders inside the
  left column; once `sessionId` is set the hero unmounts and the existing
  PreRunPanel/activity/results flow takes over unchanged.
- **Inline styles only** — no Tailwind (CLAUDE.md gotcha #7); hover/focus via `uiClass`
  classNames already wired in `web/src/index.css`.

## Pre-Implementation Checklist
- [x] 🟩 All questions from brainstorm resolved (stat tiles, recents depth, responsive — all answered ✅)
- [x] 🟩 Confirmed `?status=draft&limit=1` and `?status=completed&from=YYYY-MM-01&limit=1` return accurate `total`s — verified against the live API (total 130 = full pull; drafts 14 = the 14 draft rows; completed-this-month 5). **Note:** the wire param is `from` (middleware remaps to `date_from`), not `date_from` directly.
- [x] 🟩 Working tree clean; no conflicting work on `ExtractPage.tsx`

## Tasks

> **Implementation note (2026-05-27):** one deliberate deviation from the
> original component shape — `HomeHero` does NOT contain `UploadPanel` as an
> internal element; it takes the upload card as `children` and renders it in a
> stable tree slot that persists across the empty→non-empty transition. This
> avoids remounting `UploadPanel` (which holds internal `uploading`/`error`
> state) mid-upload. `lastStatus` is derived from the recent-runs list's first
> row rather than a 4th fetch. Both keep the change minimal and were verified
> in the browser.

### Phase 1: Data layer (API helpers) — 🟩 DONE
- [x] 🟩 **Step 1: Add `fetchRecentRuns` + `fetchHomeStats` helpers** — Wrap the four
  `GET /api/runs` calls (Key Decisions) into typed functions alongside the existing
  `fetchRuns` in the API module.
  - [ ] 🟥 `fetchRecentRuns(limit = 5)` → `RunSummaryJson[]` (reuse the existing type from `web/src/lib/types.ts`).
  - [ ] 🟥 `fetchHomeStats()` → `{ total, drafts, completedThisMonth, lastStatus }`, computing month-start client-side and issuing the three count calls in parallel (`Promise.all`).
  - **Verify:** ✅ `api.test.ts` gained `fetchRecentRuns` + `fetchHomeStats` cases asserting URLs/params + derived shape. All pass.

### Phase 2: Presentational components — 🟩 DONE
- [x] 🟩 **Step 2: Build `StatTiles` component** — A four-tile grid using `ui.card` +
  `pwc` tokens. Pure props in (`{ total, drafts, completedThisMonth, lastStatus }`), no
  fetching.
  - [ ] 🟥 Last-run-status tile uses `runStatusDisplay(lastStatus)` for label + colour (consistent with History).
  - [ ] 🟥 Dash/skeleton placeholder when a value is `undefined` (loading / fetch failure).
  - **Verify:** ✅ `StatTiles.test.tsx` (4 tests) — labels/counts, status-label mapping, dash fallbacks. Pass.
- [x] 🟩 **Step 3: Build `RecentRunsList` component** — Renders up to 5 run cards from
  `RunSummaryJson[]`: filename, relative date, status badge, Resume (drafts) / View
  (others) action, plus a "View all →" link.
  - [ ] 🟥 Reuse `runStatusDisplay` for badge colour/label (consistent with `HistoryList`).
  - [ ] 🟥 Props only: `runs`, `onResumeDraft(id)`, `onOpenRun(id)`, `onViewAll()` — no internal navigation.
  - [ ] 🟥 Empty state: "No runs yet — upload a PDF to get started" (covers zero-runs so the column never looks broken).
  - **Verify:** ✅ `RecentRunsList.test.tsx` (6 tests) — draft→onResumeDraft, run→onOpenRun, View-all, empty + error states. Pass.

### Phase 3: Compose the split hero into ExtractPage — 🟩 DONE
- [x] 🟩 **Step 4: Build `HomeHero` container** — Two-column wrapper: left slot for the
  existing `UploadPanel`, right slot for `StatTiles` + `RecentRunsList`. Owns the data
  fetch (`fetchHomeStats` + `fetchRecentRuns` on mount).
  - [ ] 🟥 `display: flex`, `gap: pwc.space.xl`; left `flex: 1` min-width ~420px, right min ~360px; no `flexWrap` (always side-by-side per decision) with min-widths chosen to fit a 1280px laptop.
  - [ ] 🟥 Right-column callbacks delegate to props passed from ExtractPage (same dispatch actions `App.tsx` uses for `onResumeDraft` and run navigation).
  - **Verify:** ✅ `HomeHero.test.tsx` (3 tests) — active renders both columns + recent row; inactive renders no column and fires no fetch; failed fetch degrades to placeholders. Pass.
- [x] 🟩 **Step 5: Mount `HomeHero` in ExtractPage empty state** — Replace the bare
  `UploadPanel` render in the empty state (ExtractPage.tsx ~line 196) with `HomeHero`
  (which contains the upload panel on its left). Gate strictly on
  `state.sessionId == null && !state.isRunning`.
  - [ ] 🟥 UploadPanel renders identically inside HomeHero's left slot (same props/handlers); once a file sets `sessionId`, HomeHero unmounts and PreRunPanel/results take over unchanged.
  - [ ] 🟥 Update only the empty-state assertions in the existing `ExtractPage.test.tsx`; leave post-upload-path tests untouched.
  - **Verify:** ✅ Browser-verified on the live server (rebuilt `dist/`): empty state shows two columns; the four tiles read 130 / 14 / 5 / "Not started" (matching the API); clicking a draft's **Resume** routed to `/run/130`, collapsed the hero to a full-width upload card, and rehydrated the config panel; **View all** routed to `/history`. Existing `ExtractPage.test.tsx` render-gate tests still pass; full suite 605/605; `tsc -b` clean.

### Phase 4: Polish + edge cases — 🟩 DONE
- [x] 🟩 **Step 6: Edge states + visual pass** — Zero-runs, failed/slow stat fetch, long
  filenames, narrow-laptop layout.
  - [ ] 🟥 If the stat/recent fetches fail, the right column degrades gracefully (tiles "—", list shows empty message) and never blocks the upload card.
  - [ ] 🟥 Long filenames truncate with ellipsis; status badges wrap cleanly.
  - [ ] 🟥 Eyeball against `docs/pwc-design-system.html` — spacing rhythm, Helvetica weights, orange accent only where intended.
  - **Verify:** ✅ Graceful degradation covered by `HomeHero.test.tsx` (500 response → placeholders, upload card unaffected) and the `RecentRunsList`/`StatTiles` empty+error tests. Long filenames truncate with ellipsis (`title` carries the full name). Rendered cleanly at the default 1500px viewport; min-widths (left 420, right 360) keep both columns side-by-side down to ~1280px.

## Rollback Plan
If something goes badly wrong:
- This is a **frontend-only, additive** change. Revert by restoring the original
  empty-state render in `ExtractPage.tsx` (single `UploadPanel`) and deleting the new
  components (`HomeHero`, `StatTiles`, `RecentRunsList`) + their tests and the two API
  helpers — `git checkout -- web/src/pages/ExtractPage.tsx` plus removing new files.
- No backend, DB, or template changes are made — nothing to migrate; `/api/runs` is a
  read-only consumer here.
- If only the layout is off but logic works, the upload card is self-contained in the
  left slot, so the "start fast" path keeps working even with a broken right column.

## Open Questions / Risks
- **Draft/completed count accuracy** depends on the `status` filter on `GET /api/runs`
  returning a correct `total` — verify with curl before Phase 1 (checklist item).
- **"This month" boundary** is computed in the browser's local timezone; if runs are
  stored UTC there may be edge-of-month off-by-one. Acceptable for a dashboard tile;
  note it rather than over-engineer.
- **Always-side-by-side** means very narrow windows (<~820px) could clip the right
  column. Per the brainstorm decision we are not adding a mobile stack; min-widths keep
  it safe on real laptops.
