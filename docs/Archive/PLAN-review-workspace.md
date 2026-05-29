# Implementation Plan: Review Workspace — Evidence-Synced Verification

**Overall Progress:** `~95%` (12 of 13 steps done; Step 10 animation pass is the only item left, and it needs a live eyeball with you.)

**Decisions locked (2026-05-26):** O1 → cross-check click-through jumps to the *exact cell* (backend target field on `CrossCheckResult`). Navigator → *dedicated left column*. Because the cross-check table (`ValidatorTab`) lives on `RunDetailView`, Step 8's clickable payoff is bundled with Phase 5 (putting the PDF pane + selection model on `RunDetailView`).
**PRD Reference:** [docs/PRD-review-workspace.html](PRD-review-workspace.html)
**Last Updated:** 2026-05-26

> Saved as `PLAN-review-workspace.md` (not `PLAN.md`) to match this repo's
> `docs/PLAN-<topic>.md` convention and sit beside its PRD sibling. The
> archived generic plans under `docs/Archive/` are why we keep plans topic-named.

## Summary
We're making the post-run review screens take you *to* the thing they talk about, anchored on seeing the source PDF page beside an extracted value. We add a small server route that renders a PDF page to an image, parse each value's existing `evidence` string into page numbers, and show a PDF pane that follows the already-existing concept selection on `ConceptsPage`. Then we make reconciliation conflicts clickable, replace the template dropdown with a navigator, and tidy the visuals — building the PDF pane + selection wiring as reusable parts we later drop onto the History detail and run-complete screens.

## Key Decisions
- **Server-rendered page images, not browser PDF.js** — reuse `tools/pdf_viewer.py::render_pages_to_png_bytes()`, show as `<img>`. Why: PDF.js is a heavier bundle with a separate worker and has a history of Windows/enterprise trouble (same family as gotcha #7); the render code is already proven in scout/correction/extraction. **Correction (peer-review):** `render_pages_to_png_bytes` does NOT use `tools/page_cache.py` — an earlier note claiming page-cache reuse was wrong. The endpoint opens the PDF once to count pages and once to render; cheap for a single reviewer, documented as LOW tech-debt.
- **Start on `ConceptsPage` only** — it already has the value grid, a selection model (`selectedConceptUuid`), the `ConceptEvidencePanel` side rail, the `ReconciliationQueue`, and the unfriendly dropdown. All five reported pains overlap there. History detail (`RunDetailView`) and run-complete (`ResultsView`) come later by reuse.
- **Selection model already exists** — `ConceptsPage` tracks `selectedConceptUuid` and renders a `selectedConcept`. We do NOT build a new store; the PDF pane reads `selectedConcept.evidence`, and clickable findings just call the existing `setSelectedConceptUuid` (plus switch `activeTemplate`).
- **Conflicts are clickable; cross-checks are not (yet)** — `ConflictRow` carries `concept_uuid` + `render_sheet`/`render_row`, so a conflict maps cleanly to a concept. `CrossCheckResult` carries **no target cell** (only name/expected/actual/diff/message), and `ValidatorTab` lives on `RunDetailView`, not `ConceptsPage`. So M2 wires conflict click-through; cross-check click-through is a deliberate later decision (see Open Question O1), not silently assumed.
- **PDF resolved from the run's session dir** — `Path(run.merged_workbook_path).parent / "uploaded.pdf"` (the pattern already used at server.py:235/343). Runs with no stored PDF (legacy / CLI) degrade to an empty pane, not an error.
- **Page hints stay out** — scout `page_hints` are in-memory only; persisting them is explicitly out of scope (PRD §9, Open Question O2).

## Pre-Implementation Checklist
- [ ] 🟥 PRD approved (two open questions from the brainstorm still pending: surface scope, "right page" vs "right number")
- [ ] 🟥 Confirm O1 (cross-check click-through approach) before M2 step 6
- [ ] 🟥 No conflicting in-progress work — the canonical-concept-model work (gotcha #21) is uncommitted and touches `ConceptsPage`/`concept_model/`; coordinate so we branch off the same tree
- [ ] 🟥 Decide DPI default for served pages (start ~150)

## Tasks

### Phase 1 (M1): See a value's source page — ✅ COMPLETE
Goal demo: *click a value on ConceptsPage → its source PDF page appears beside it.*

> **Phase 1 result:** all 5 steps done, all tests green (`tests/test_pdf_page_endpoint.py` 8 pass; `evidencePages.test.ts` 8 pass; `PdfSourcePane.test.tsx` 6 pass; full frontend suite 561 pass; `tsc` clean). Manual live-run eyeball still owed by reviewer.
> **Minor deviations (per command):** (1) Step 4 adds clickable cited-page *chips* alongside prev/next — strictly additive, prev/next still step the cited set. (2) Step 1 resolves the PDF from `session_id` first (canonical output-dir name), falling back to `merged_workbook_path.parent` — both point at the same session folder; session_id is more robust for runs that failed before merge.

- [x] 🟩 **Step 1: PDF page-serving endpoint** — let the browser fetch a rendered page image for a run.
  - [ ] 🟥 Add `GET /api/runs/{run_id}/pdf/page/{page}.png` (optional `?dpi=`) in `server.py`, near the other `/api/runs/{run_id}/…` routes.
  - [ ] 🟥 Resolve the PDF via the run repo → `Path(run.merged_workbook_path).parent / "uploaded.pdf"`; return 404 if the run or file is absent.
  - [ ] 🟥 Validate `1 ≤ page ≤ count_pdf_pages`; clamp DPI to a safe range (e.g. 72–250); render with `render_pages_to_png_bytes(path, page, page, dpi)[0]`.
  - [ ] 🟥 Return an image response with a cache header; scope this route to page images only (do NOT widen `ALLOWED_DOWNLOADS` — this is a separate, explicit read path).
  - **Verify:** `curl -s -o /tmp/p.png 'http://localhost:8002/api/runs/<id>/pdf/page/3.png' && file /tmp/p.png` reports a PNG; an out-of-range page returns 404/400; a run with no `uploaded.pdf` returns 404. Add `tests/test_pdf_page_endpoint.py` covering valid page, out-of-range, and missing-PDF.

- [x] 🟩 **Step 2: Add a total-pages field to the page count** — the pane needs to know how many pages exist for prev/next bounds.
  - [ ] 🟥 Either extend the endpoint response headers with `X-PDF-Page-Count`, or add a tiny `GET /api/runs/{run_id}/pdf/info` returning `{ "pages": N }` (reuses `count_pdf_pages`).
  - **Verify:** the chosen surface returns the correct page count for a known PDF; 404 when no PDF. Covered in `tests/test_pdf_page_endpoint.py`.

- [x] 🟩 **Step 3: Evidence → page parser (frontend util)** — turn an evidence string into page numbers.
  - [ ] 🟥 Add `web/src/lib/evidencePages.ts` exporting `parseEvidencePages(evidence: string | null): number[]` — regex for `Page N`, `p.N`, `Pages N-M` (range expand), `Page A; Page B` (split). Dedup + sort. Empty input → `[]`.
  - **Verify:** `web/src/__tests__/evidencePages.test.ts` asserts the worked examples from the feasibility check (`"Page 14, Note 1"`→[14], `"Pages 19-20, Note 2(g)"`→[19,20], `"Page 3; Page 4"`→[3,4], `"p.42"`→[42], `null`→[]). `npx vitest run` green.

- [x] 🟩 **Step 4: PDF pane component** — reusable image viewer.
  - [ ] 🟥 Add `web/src/components/PdfSourcePane.tsx`: props `{ runId, pages: number[], totalPages?: number }`. Renders `<img src={/api/runs/{runId}/pdf/page/{current}.png}>`, prev/next within `pages` (or free paging when `pages` empty), a manual page jumper, and zoom-to-fit / zoom-in. Inline styles only (gotcha #7), `pwc` theme tokens.
  - [ ] 🟥 Quiet loading/error states: a failed image shows a retry, never a crash; empty `pages` + no manual nav shows "No source page recorded — jump to a page manually."
  - **Verify:** mount in isolation (vitest + a stubbed `<img>`), assert it requests the right URL for `pages=[14]` and that next/prev move within `[19,20]`. Manual: load a real run, eyeball a rendered page.

- [x] 🟩 **Step 5: Wire the pane into ConceptsPage** — drive it from the existing selection.
  - [ ] 🟥 In `ConceptsPage`, render `<PdfSourcePane runId pages={parseEvidencePages(selectedConcept?.evidence)} />` in the `sideRail` (above or below `ConceptEvidencePanel`), or promote to a third column on wide screens.
  - [ ] 🟥 No new state — it reads the existing `selectedConcept`. Selecting any grid/matrix row already updates `selectedConceptUuid`.
  - **Verify:** open a completed web run on the Concepts tab → click rows in the tree and the matrix grid; the pane flips to each value's cited page. A value with no evidence shows the empty state, not an error. `npx vitest run` + `python -m pytest tests/ -v` stay green.

### Phase 2 (M2): Reconciliation that takes you to the issue
Goal demo: *click an open conflict → the grid selects the offending value and the PDF pane shows its page.*

- [x] 🟩 **Step 6: Make ReconciliationQueue rows clickable** — conflict → concept selection. Added optional `onSelectConcept` prop; row click calls it with `concept_uuid`; Resolve/Dismiss `stopPropagation`. `ConceptsPage.handleSelectConcept` looks the concept up, switches `activeTemplate`, clears search, sets selection. Verified by 2 new tests in `ReconciliationQueue.test.tsx`. ✅
  - [ ] 🟥 Add an optional `onSelectConcept?: (conceptUuid: string) => void` prop to `ReconciliationQueue`; clicking a conflict row (not the Resolve/Dismiss buttons — stop propagation on those) calls it with `c.concept_uuid`.
  - [ ] 🟥 In `ConceptsPage`, pass a handler that (a) finds the concept in `concepts` by `concept_uuid`, (b) sets `activeTemplate` to that concept's `template_id` (so the selection isn't filtered out by the active-template view), then (c) sets `selectedConceptUuid`. Clear `searchQuery` if set so the row is visible.
  - **Verify:** seed a run with an open conflict on a non-active template; click it → active template switches, the row highlights, the PDF pane updates. Resolve/Dismiss still work and do NOT trigger selection. Add `web/src/__tests__/reconciliationQueue.test.tsx` for the click→callback + button-propagation-stop.

- [x] 🟩 **Step 7: Selection scroll-into-view** — `scrollIntoView({ block: "nearest" })` on `selected` change in both `ConceptRowView` (tree) and `ConceptMatrixGrid` (SOCIE, via a render_row→element ref map). Guarded with `?.` for jsdom. Full frontend suite 563 pass; `tsc` clean. ✅
  - Implemented in both `ConceptRowView` (tree) and `ConceptMatrixGrid` (ref map), guarded for jsdom. ✅

- [x] 🟩 **Step 8: Cross-check click-through (exact cell, per O1)** — DONE.
  - Backend: added nullable `target_sheet`/`target_row` to `CrossCheckResult` (`cross_checks/framework.py`) and the DB-row `CrossCheck` (`db/repository.py`); schema **v6→v7** migration adds the columns (`db/schema.py`, version-stepped per gotcha #11). `save_cross_check`/`fetch_cross_checks` round-trip them; all 6 server serialization sites (recheck dict, 2 SSE events, persist call, run_complete `checks_data`, run-detail endpoint) carry them. Added `find_label_row` helper; `SOFPBalanceCheck` populates the equity+liabilities anchor.
  - Frontend: `CrossCheckResult` + `CrossCheckResultEventData` types gained the fields; `ValidatorTab` rows are clickable when a target is present (`onSelectTarget`).
  - **Scope note:** only `sofp_balance` populates a target so far — the cross-statement checks (SOPL↔SOCIE profit, SOCI↔SOCIE TCI, etc.) have *ambiguous* anchors, so they're left null (frontend renders them non-clickable). Adding their anchors is a clean follow-up if wanted.
  - **Verify:** `tests/test_cross_check_target.py` (4 — migration, round-trip, check populates target) + 2 ValidatorTab click tests. ✅

### Phase 3 (M3): Navigator replaces the dropdown
Goal demo: *switch sheets from an always-visible list with per-sheet finding counts.*

- [x] 🟩 **Step 9: Sheet navigator component** — `SheetNavigator` left-column rail replaces the dropdown; per-template open-conflict count badges (new lightweight `/conflicts` fetch in ConceptsPage, keyed on `conflictReloadKey`). Removed the `<select>` + dead `controlGroupWide` style; updated 4 ConceptsPage tests from select→nav clicks, added a badge-count test. Full suite 565 pass. ✅
  - [ ] 🟥 Add a left-column navigator listing each `template_id` (order-preserving, as `templates` already is) plus the `Notes` entry, driven by `activeTemplate`/`setActiveTemplate`. Reuse the notes slot-sort if applicable.
  - [ ] 🟥 Per-item finding count badge: number of open conflicts whose concept maps to that template (derive from `concepts` + the conflicts list). Red dot when > 0.
  - [ ] 🟥 Remove the `<select id="template-selector">`; keep `data-testid="template-selector"` semantics or update dependent tests.
  - **Verify:** grep tests for `template-selector` and update; navigator switches templates; counts match the conflict list. Responsive: collapses sensibly on narrow widths. `npx vitest run` green.

### Phase 4 (M4): Visual calm + responsive
Goal demo: *the review surface is quieter, content doesn't jump, and the PDF pane behaves on small screens.*

- [~] 🟨 **Step 10: Animation + clutter audit** — PARTIAL. Done the one safe, concrete item: the PDF viewport reserves `minHeight` so a loading page image doesn't shift the layout. The animation/clutter softening genuinely needs a *live eyeball* (I can't run the browser here) and risks touching shared keyframes used by the agent timeline — best done with you watching in a review session. Flagged, not silently skipped.
  - **Verify (owed):** load the review page, throttle the network, confirm no layout jump; eyeball animations together and decide what to soften.

- [x] 🟩 **Step 11: Responsive PDF pane** — `PdfSourcePane` has a Show/Hide collapse toggle defaulting collapsed under `max-width: 900px` (matchMedia, guarded for jsdom). Test added; the three-column flex layout already wraps the side rail below on narrow widths. ✅

### Phase 5 (Later): Reuse on the other surfaces
Goal demo: *the same verify-against-source pattern on History detail and the run-complete summary.*

- [x] 🟩 **Step 12: Reuse on RunDetailView** — `PdfSourcePane` rendered under the Cross-checks section; `ValidatorTab` wired with `onSelectTarget`. Clicking a check with a target resolves `(target_sheet, target_row)` → the concept's evidence (per-run `/concepts` fetch on mount) → page numbers, driving the pane. Runs without canonical facts yield an empty map → pane's quiet "no source" state. ✅

## Peer-review fixes (2026-05-26)
A second team-lead review caught real gaps; fixed and pinned:
- **HIGH — targets dropped on History detail.** `crossChecksForValidator` and the `RunCrossCheckJson` type omitted `target_sheet`/`target_row`, so cross-check rows were never clickable on `RunDetailView` despite backend support. Added the fields to both; new integration test `RunDetailView.test.tsx::"clicking a targeted cross-check drives the source-PDF pane"` (would have failed before the fix — my original ValidatorTab unit test bypassed the mapper).
- **MED — stale concept map on run switch.** `RunDetailView` isn't keyed by run id, and the fetch guard blocked refetch when `detail.id` changed. Reworked: the `/concepts` effect keys on `detail.id` and resets state; the pane's pages are now `useMemo`-derived from a stored `selectedTarget`, so an early click before the map loads resolves correctly once it arrives.
- **MED — PDF pane reset on unrelated renders.** `PdfSourcePane`'s reset effect depended on the `pages` array identity; callers pass `parseEvidencePages(...)` (fresh each render), so any parent re-render reset current page + zoom. Now keyed on a stable `pages.join(",")`, plus memoised the parse in `ConceptsPage`.
- **MED — render errors → 500.** Wrapped `render_pages_to_png_bytes` in try/except → 422.
- **LOW-MED — PDF path hardening (downgraded from HIGH).** Inputs are DB- not URL-controlled, so not a remote-traversal vector — but added `OUTPUT_DIR.resolve()` + `relative_to` constraint anyway (defense-in-depth, matches `/api/result/`). New test `test_traversing_session_id_cannot_escape_output_dir`. *This surfaced that `OUTPUT_DIR` is a module global, not env-driven — the endpoint test fixture now sets `srv.OUTPUT_DIR` to mirror production layout.*
- **Acknowledged, not fixed:** duplicate `/conflicts` fetch (accepted tradeoff for a local tool); `find_label_row` reverse-substring (mirrors existing `find_value_by_label`, exact match wins for the SOFP total).
- **OPEN (important):** the SOFP-balance target is the equity+liabilities *computed subtotal*, which usually carries **no evidence string** — so a correct cross-check click can still land on an empty pane. Live verification owed; if the empty-pane UX is poor, re-anchor cross-check targets to a leaf row with evidence, or show "right row, no source page" explicitly.
- [x] 🟩 **Step 13: Reuse on ResultsView** — satisfied by the *existing* "Review extracted values" action (`onViewConcepts`) that routes to the canonical workspace (ConceptsPage), which now carries the full pane + navigator + clickable reconciliation. **Deliberate scope decision:** NOT duplicating a standalone `PdfSourcePane` on ResultsView — it has no concept-selection driver, so a pane there would be dead UI. The run-complete screen's job is to *get you into* the workspace, which it does. ✅

## Open Questions (resolve at the marked gates)
- **O1 (gate before Step 8):** Cross-check click-through — jump to the *statement/sheet* (no backend change, coarse) or to a *specific cell* (add a target field to `CrossCheckResult`, more work)? Also decide whether it lands on `ConceptsPage` or only on `RunDetailView` where `ValidatorTab` lives.
- **O2 (after M2):** Persist scout `page_hints` as a smarter fallback page for values with no parseable evidence? One-column migration on `runs` (schema is version-stepped, gotcha #11) if yes.
- **O3 (from brainstorm):** "Right page" vs "right number" — is showing the correct page enough, or do reviewers need the exact figure highlighted on the page? Highlighting is currently out of scope.
- **O4:** DPI/legibility vs payload size — confirm the default against a real statement page.

## Rollback Plan
If something goes badly wrong:
- **Frontend-only steps (3–11):** revert the touched files under `web/src/`; the review page returns to the current dropdown + evidence-text panel. No data migration involved.
- **Backend endpoint (Steps 1–2):** the PDF route is purely additive and read-only — remove the route; nothing else depends on it. No schema change, so no migration to undo.
- **Step 8 "jump to cell" variant only:** if a `CrossCheckResult` target field was added and causes persistence/SSE issues, revert the field — it's optional/nullable and read-only, so older runs and the rest of the pipeline are unaffected.
- **State to check after any revert:** completed runs still load on the Concepts tab (`/api/runs/{id}/concepts`), conflicts still list (`/api/runs/{id}/conflicts`), and `filled.xlsx` download still works (`downloadFilledUrl`). Run `python -m pytest tests/ -v` and `cd web && npx vitest run`.
