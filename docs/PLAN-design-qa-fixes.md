# PLAN — Design & QA Review Fixes

**Source:** [docs/XBRL-Agent-Design-QA-Review.md](XBRL-Agent-Design-QA-Review.md) (9 July 2026)
**Status:** validated against code 9 July 2026; every finding below was traced to its
exact source before planning. Verdicts corrected where the review's diagnosis was wrong.

---

## Part 1 — Validation verdicts (what's real, what isn't)

### Confirmed, but the review's diagnosis was WRONG (fix differs from what was asked)

| # | Review said | What the code actually shows |
|---|---|---|
| V1 | "AI service address pre-filled with email — data-binding bug" | **Browser password autofill, not a code binding bug.** The proxy-URL input (`web/src/components/GeneralSettingsForm.tsx:338-365`) has no `name`/`id`/`autocomplete` attribute, has `autoFocus`, and sits directly above a `type="password"` field (API key, `:375-387`) — the classic username+password pair heuristic. The browser injects the saved login email. Binding and backend are clean (`proxyUrl` state ← `s.proxy_url` ← `LLM_PROXY_URL` only). Fix is input attributes, not state plumbing. |
| V2 | "Re-extract notes sends you to the Extract page to re-upload manually" | **The action already re-extracts in place.** `onRegenerate` → `POST /api/runs/{id}/rerun-notes` (`api/run_control.py:312-368`) reuses the on-disk PDF; the endpoint's docstring says it was built to replace the old Extract-page detour. What's stale is the **confirm-dialog copy** (`NotesReviewTab.tsx:640-646`), which still describes the old flow. Copy-only fix. |
| V3 | "A reload logs you out — session doesn't survive reloads" | **Session is a real httponly server cookie and an in-tab reload keeps you logged in; the deep-link path IS preserved through login.** The real gaps: (a) the cookie has **no `max_age`** (`auth/routes.py:74-81`) so it dies with the browser process; (b) the **15-min sliding idle timeout** (`auth/config.py`, default 900s); (c) any 401 anywhere hard-drops the shell to Sign-in (`api.ts:21-34`). Fix = cookie lifetime decision, not a persistence rewrite. |
| V4 | "'Check notes against this template' is a silent no-op" | **Not a no-op — the button is `disabled={!file || previewBusy}`** (`MtoolFillModal.tsx:611-619`). The gap is only that the disabled state has no "choose a template first" hint. |
| V5 | "No way to view or edit a benchmark from its card — only Delete" | **The whole card is clickable and opens the gold editor** (`BenchmarksPage.tsx:147-158` → `ConceptsPage source="benchmark"`). Real problem is discoverability: no visible Open/Edit affordance. |
| V6 | "denomination: units contradicts Denomination: RM" | **Same config value, three different renderings.** The mTool modal prints the raw enum (`units/thousands/millions`); Overview maps it to `RM / RM '000 / RM mil` (`RunDetailView.tsx:113-119`); `HistoryList.tsx:163-167` has a third variant. Labeling inconsistency, not a data contradiction. Centralize the mapping. |

### Not confirmed (drop or downgrade)

- **History rows "clicking does nothing"** — the click handler is on the whole `<tr>` (`HistoryList.tsx:128-143`), there are no nested interactive elements and no `stopPropagation`; no code path makes a filename click dead. Couldn't reproduce in code. The *legitimate* residue: rows are `role="button"`, not real `<a>` — no middle-click / open-in-new-tab. Downgraded to the routing work (R4 below).
- **"Esc happens to cancel" the Field-labels rename** — actually there is **no cancel path at all**; blur *commits* the draft and Escape does nothing (`TemplateSettingsPage.tsx:198-209`). The finding is worse than reported, and folded into F1.
- **"Benchmarks is a narrow page"** — Benchmarks actually renders full-width (`mainHistory`, no page cap). The width inconsistency itself is real (see D2).

### Confirmed as reported — everything else

All remaining findings confirmed with exact sources; they're embedded in the work items below.

---

## Part 2 — Fix plan

Ordered by (user impact × effort). Each item names its files and the pinning tests
that must be added/updated. Suite gate: `./venv/bin/python -m pytest tests/ -n auto`
+ `cd web && npx vitest run`.

### Sprint A — Bugs (small, high-impact, do first) — 🟩 DONE

*Implemented on branch `fix/design-qa-review`. Note on A2: the backend `error`
string is left intact (its content is pinned by `test_notes_format_patch.py`);
the frontend taxonomy mapping is the robust single control point and defends
against any dict-shaped string regardless of source.*


**A1. Stop browser autofill hijacking the AI service address** *(review's #1 priority)*
- `GeneralSettingsForm.tsx`: give the proxy-URL input `name="ai-service-address"`,
  `autoComplete="off"` (or `"url"`), and give the API-key password input
  `autoComplete="new-password"` + a non-credential `name`. Chrome ignores plain
  `autocomplete="off"` on password forms, so breaking the text-then-password
  adjacency heuristic via explicit non-credential semantics is the load-bearing part.
- Tests: none pin these attributes today — **add** an attribute-assertion test to
  `SettingsPage.test.tsx` so the fix can't silently regress.

**A2. Translate the leaked formatter error into plain language** *(review's #2 priority)*
- Backend origin: `notes/format_patch.py:169` raises
  `target matched no elements: {raw dict}`; `notes/formatting_agent.py` (`:403/:427/:461`)
  puts `str(exc)` into the API `error` field; `api/notes_formatter.py:243-246` spreads it
  verbatim; `NotesReviewTab.tsx:940` renders it raw.
- Fix both layers: (1) frontend — map `formatStatus.error_type` (already a clean taxonomy:
  `validation_failed`, `timeout`, `turn_budget`, `low_confidence`, `wrong_sheet`,
  `model_error`, `precondition_failed`, `reverted`) to plain-language copy at
  `NotesReviewTab.tsx:940`, e.g. *"The formatter tried to style a cell that no longer
  exists — your edits may have changed the table. No changes were saved."* Raw `error`
  becomes a collapsed "technical details" line at most. (2) backend — keep the target
  dict in the existing `logger.warning` calls, out of the user-facing `error` string.
- Tests: extend `web/src/__tests__/NotesReviewTab.test.tsx` (the `validation_failed`
  raw-dict case is currently unpinned) and `tests/test_notes_format_patch.py`.

**A3. Reconcile the stale "8/11 → 8/8" check counts** *(review's Validate-figures bug)*
- Root cause: on-load numbers come from persisted `cross_checks` rows, which include
  **advisory notes warnings** appended at pipeline time (`server.py:4697-4724`);
  the "Validate figures" recheck (`server._recheck_from_facts`, `server.py:574-724`)
  re-runs **only** the 9 numeric checks — so the denominator collapses 11 → 8 and the
  3 warnings silently vanish. `_refresh_persisted_cross_checks` (`server.py:727-766`)
  has the same blind spot: it deletes ALL rows and repopulates numeric-only, so a
  re-review/revert also drops advisory rows.
- Fix (two coordinated parts):
  1. `_recheck_from_facts` **preserves** advisory-warning rows: carry existing
     `status="warning"` advisory rows through the recheck result (they're
     notes-consistency findings the numeric recheck can't re-derive), or re-run the
     advisory generators (workbook already located at `server.py:597-599`). Same for
     `_refresh_persisted_cross_checks`.
  2. Align the headline semantics: in `ConceptsPage.tsx:690-701`, count the
     denominator as `passed + failed` only, and surface warnings as a separate
     "N advisory" note — so the Figures strip, Needs-attention panel, and the
     Cross-checks tab all agree before and after a recheck.
- Tests: `tests/test_recheck_endpoint.py`, `tests/test_cross_checks_persistence.py`,
  `web/src/__tests__/ConceptsPage.test.tsx` — add a case pinning
  "recheck does not shrink the check set".

**A4. Fix Field-labels Rename (prefill + Save/Cancel + Esc)**
- `TemplateSettingsPage.tsx:167-228` (`TemplateConceptRow`): seed the draft from the
  *displayed* label (`display_label || canonical_label`), add explicit Save/Cancel
  buttons, `Enter` commits, `Escape` cancels (today blur commits and there is no
  cancel path at all).
- Tests: add to `TemplateSettingsPage`'s coverage (via `ConceptsPage.test.tsx` or a
  new `TemplateSettingsPage.test.tsx`).

**A5. Fix the stale re-extract dialog copy** *(V2)*
- `NotesReviewTab.tsx:640-646`: rewrite the known-count branch to describe the real
  in-place behavior: *"This starts a fresh notes extraction on this PDF. When it
  finishes it will replace {N} edited cell(s); your edits stay in place until then."*
- Guard: disable the button when the optional `onRegenerate` prop is absent (today it
  silently no-ops).
- Tests: `NotesReviewTab.test.tsx`.

### Sprint B — Routing & session (one coordinated change) — 🟩 DONE

All three routing findings converge on `parseRouteFromPath` (`appReducer.ts:208-249`)
plus the pushState effect (`App.tsx:263-302`) — fix together.

**R1. `/run/{id}` becomes status-aware.**
- `ExtractPage.tsx:104-156` rehydration: after `fetchRunDetail`, branch on
  `detail.status` — non-`draft` runs redirect to run detail
  (`view:"history"`, `selectedRunId:id`) instead of dispatching `UPLOADED` into the
  run-config panel. Only genuine drafts keep the resume-config behavior.
- Tests: `AppRouting.test.tsx` only pins the draft case today — add the
  completed-run case.

**R2. Give Field labels a real URL.**
- The `concepts`-with-no-id branch (`App.tsx:269-271`) currently collapses to `/`.
  Map it to `/field-labels` (matches the nav label the operator sees), add the path to
  `parseRouteFromPath`, keep `/concepts` as an accepted alias. Note the admin guard
  (`App.tsx:179-188`) still bounces non-admins — that's correct, keep it.
- Tests: `AppRouting.test.tsx`, `TopNav.test.tsx`.

**R3. Make run-detail tabs addressable.**
- Lift `RunDetailView`'s `tab` state (`RunDetailView.tsx:332`) into the URL as
  `?tab=` (smallest change; avoids multiplying path routes), thread through the
  pushState effect + `parseRouteFromPath` + the existing `initialTab` prop.
- Tests: `AppRouting.test.tsx`, `RunDetailView.test.tsx`.

**R4. History rows become real links.**
- `HistoryList.tsx:128-143`: wrap rows (or the filename cell) in a real `<a href>`
  to the run's URL with `onClick` preventDefault + dispatch, enabling middle-click /
  open-in-new-tab. This also answers the unreproducible "click does nothing" report
  with a strictly better affordance.
- Tests: `HistoryList.test.tsx`.

**R5. Session lifetime — DECIDED: login lasts ≥ 1 hour.**
- Add `max_age` to `_set_session_cookie` (`auth/routes.py:74-81`) so the session
  survives a browser restart, and **raise the idle timeout to ≥ 1 hour**
  (`AUTH_IDLE_TIMEOUT_S`, currently 900s / 15 min in `auth/config.py`). Set the
  default to 3600s and let the cookie `max_age` match, so an idle-but-open tab and a
  closed-then-reopened browser both keep you signed in for at least an hour.
- The SPA already keeps the session warm via `/api/auth/refresh` while you're active,
  so the 1-hour window is a floor on *inactivity*, not a hard cap on a working session.
- Tests: `tests/test_auth_sessions.py`, `test_auth_middleware.py` (both pin the 900s
  default today — update deliberately).

### Sprint C — Design language (consistency sweep) — 🟩 DONE

**C1. Retire the blue buttons — DECIDED: make them NEUTRAL (secondary), not orange.**
- `ReviewTab.tsx:636-644,654-662` and `NotesReviewerPanel.tsx:547-555,565-573` hand-roll
  `background: pwc.info` (#3E84CC) fills. The design doc (rules 05/06) forbids status
  colours as fills and defines no blue button. Swap "Run AI review again", "Run notes
  review again", and the inline flag "Save" buttons to **`ui.buttonSecondary`** (the
  neutral white/grey outline variant) — this preserves the design-rule that orange
  marks the single most important action per screen (Download / Start), while these
  re-run actions read as clearly secondary. No test pins `pwc.info`, so the swap is safe.
- Tests: `ReviewTab.test.tsx`, `NotesReviewerPanel.test.tsx` (text-based queries — safe).

**C2. One denomination label helper.**
- Extract the `units → RM / thousands → RM '000 / millions → RM mil` mapping (today
  duplicated in `RunDetailView.tsx:113-119`, `HistoryList.tsx:163-167`, and absent in
  `MtoolFillModal.tsx:509` which prints the raw enum) into `web/src/lib/vocabulary.ts`;
  use it in all three places.
- Tests: `MtoolFillModal.test.tsx`, `HistoryList.test.tsx`.

**C3. Standardize page width.**
- No shared container primitive exists; width comes from three `<main>` styles
  (`App.tsx:84-116`) × ad-hoc page caps (Settings 640, Extract 1120, everything else
  full-width). Add a shared page-container in `uiStyles.ts` with two intentional
  widths (form pages ~760-940; workspace pages full) and route every page through it.
- Tests: light — page-level tests don't pin widths.

**C4. One save model on Settings.**
- Today only "Notes table style" auto-saves (debounced, its own POST —
  `GeneralSettingsForm.tsx:566-642`); everything else waits for Save. Keep both
  behaviors but make them legible: visually separate the auto-save section (its own
  card + "Saved ✓" inline confirmation) and scope the copy so the Save button clearly
  governs the rest. Move Test Connection next to Save in one sticky action row.
- Tests: `SettingsPage.test.tsx`, `SettingsModal.test.tsx`.

**C5. Money and negatives.**
- Cost: add `formatCost()` to `numberFormat.ts` (2 decimals, `<$0.01` for sub-cent);
  replace both `toFixed(4)` call sites (`RunDetailView.tsx:594,296`).
- Negatives: editable cells show `-20,667` at rest while computed rows show `(20,667)`.
  The minus is load-bearing for the save round-trip (`numberFormat.ts:25`), so display
  accounting parens **at rest only** and teach the input parser
  (`ConceptsPage.tsx:2278-2285`) to read `(1,234)` back as negative; raw digits still
  show while focused.
- Tests: `ConceptsPage.test.tsx:354-362` pins the current minus behavior — update
  deliberately in the same commit.

### Sprint D — Plain-language labels (the "PM can't read code" sweep) — 🟩 DONE

The mapping infrastructure mostly already exists and is simply bypassed:

**D1. Cross-check names.** No human label exists at any layer (`CrossCheck` protocol
has only `name`). Add a `label` (plain English) per check class in `cross_checks/*.py`
(or a central map), expose it through the results, render it in `ValidatorTab.tsx:103,149`
with the snake_case name demoted to a tooltip.
**D2. Run-config block.** `RunDetailView.tsx:87-104` prints raw `SOFP=OrderOfLiquidity`
and `SOFP=openai.gpt-5.4`. Route through the existing `variantLabel()`
(`vocabulary.ts:71-73`) and model `display_name` (from `available_models`).
**D3. Template picker.** `TemplateSettingsPage.tsx:136-140` shows raw
`mfrs-company-notes-issuedcapital-v1` IDs. Use the existing `templateDisplayName()`
(`sheetLabels.ts`) for option text + `<optgroup>` by Standard/Level; keep the ID as a
tooltip. (ConceptsPage's benchmark editor already does this — copy the pattern.)
**D4. Model picker in Settings.** `GeneralSettingsForm.tsx:397-420` is the only
free-text model field left; the run-config dropdowns already consume
`settings.available_models` (from `config/models.json`). Convert to the same
`<select>` with display names.
**D5. CY/PY years.** The years exist server-side (`reporting_period_cy/py` in
`runs.run_config_json.infopack`, see `server.py:148-163`) but no API exposes them.
Add them to the run-detail JSON (or concepts response), render "CY (FY2021)" style
headers in `ConceptsPage.tsx:1345-1346`.
- Tests: `ValidatorTab.test.tsx`, `RunDetailView.test.tsx`, `ConceptsPage.test.tsx`,
  `SettingsPage.test.tsx`, `tests/test_concepts_routes.py` (if the API shape grows).

### Sprint E — Structural UX (larger; schedule after A-D) — 🟩 DONE

*All items implemented across two passes:*
- *E1 — Overview leads with outcomes; telemetry demoted to a Performance section.*
- *E2 — Score column + empty-dash tooltips, a result count, a dedicated
  **Standard column** (standard/level moved out of the filename badges), and
  **drafts in their own collapsed section**.*
- *E3 — **bulk draft cleanup**: `DELETE /api/runs/drafts` (draft-only, skips a
  mid-start draft) + a confirm-gated "Clear" action on the Drafts KPI tile.*
- *E4 — RecentRunsList standard/level/RM chips; the 4th KPI tile (Last run
  status) sits on a neutral surface so it reads as a distinct panel, not a
  broken counter.*
- *E5 — benchmark cards get a visible Open button; gold-count de-monospaced;
  the "From a run" field is a **run picker** of finished runs, not free text.*
- *E6 — mTool modal corner ✕ + hint on the disabled check-notes button; the
  Extract dropzone extracted into a shared **`FileDropzone`** reused by the
  mTool template picker.*
- *E7 — collapse consecutive duplicate ABSTRACT headers; fall back to the
  Source column for a page number.*
- *E8 — Field-labels legend + "edited" chip + a **label search filter**.*

*Follow-on design fix (from a live screenshot): the Figures summary tiles
(`ReviewMetric`) moved off status-colour fills to the neutral-surface +
left-rule accent the app's alerts use.*


**E1. Overview leads with outcomes.** `RunDetailView.tsx:589-602` is hard-coded to
telemetry tiles. Add an outcomes strip first (checks passing / needs-attention / notes
placed — reuse the derivations that already exist in `ConceptsPage`'s summary strip),
demote tokens/cost to the Activity tab's "Performance details" disclosure.
**E2. History triage.** Default the list to a "Runs" segment with drafts in a separate
collapsed section (data + filter already exist server-side); add a Standard column
(badges already exist in the filename cell); add a persistent "N results" count;
explain the empty Score dash with a header tooltip ("scored only when a benchmark is
attached").
**E3. Draft cleanup.** New `DELETE /api/runs` bulk endpoint (drafts only, guarded) +
"Clear abandoned drafts" action near the KPI strip. This is the only item in the plan
that adds an API; keep it draft-status-scoped so it can't delete real runs.
**E4. Extract-page polish.** 4th KPI card → either a real metric or visually detached
status chip (`StatTiles.tsx:47-59`); RecentRunsList gains the same standard/level/RM
badges HistoryList already has (`RecentRunsList.tsx:86-98`).
**E5. Benchmarks polish.** Visible "Open" button on the card (editor is already wired);
run-number field → searchable run picker backed by the existing `GET /api/runs`;
drop `fontMono` from the gold-cell count (`BenchmarksPage.tsx:519-523`).
**E6. mTool modal polish.** Denomination label via C2; hint on the disabled
check-notes button; corner ✕ (Esc + scrim-click already work); extract UploadPanel's
dropzone into a shared component and reuse it for the template file.
**E7. Figures viewer polish.** Collapse consecutive duplicate ABSTRACT header rows in
`ConceptTree` (`ConceptsPage.tsx:1350-1366` — the code comments already describe the
symptom); when `evidence` has no parseable page, also try `row.source`
(`evidencePages.ts:19` regex today requires a literal "page"/"p." token).
**E8. Field-labels page aids.** Legend for grey (section-header) rows and the `*`
(mandatory) prefix; a text filter over labels; an "edited" dot on customised rows.

### Explicitly deferred / rejected

- **Brand mark**: the app's minimal identity is largely faithful to its own design doc;
  the only gap is the missing momentum-mark glyph next to the wordmark. Cheap to add,
  zero functional value — park it in E4 if desired.
- **Date-input restyling** (History filters): native controls already get the shared
  `ui.input` frame; replacing browser internals costs a datepicker dependency. Not worth it.
- **Deleting the review's "silent no-op" / "can't open benchmark" / "History clicks
  dead" items** — not confirmed (see Part 1); superseded by V4/V5/R4.

---

## Decisions (resolved 9 July 2026)

1. **Session lifetime (R5):** ✅ **Login lasts at least 1 hour.** Cookie gains a
   `max_age` and the idle timeout rises from 15 min to ≥ 1 hour; survives a browser
   restart. See R5.
2. **Blue buttons (C1):** ✅ **Neutral (secondary), not orange.** Keeps orange as the
   single primary action per screen. See C1.

## Decision — A3 recheck behavior (resolved 9 July 2026)

✅ **(a) Carry advisory notes-warnings through a recheck unchanged, labelled "as of
extraction".** When "Validate figures" runs, it re-runs the numeric balance checks live
but preserves the existing advisory notes-warning rows rather than dropping them, so the
denominator stays stable (no more "8/11 → 8/8" shrink). The warnings are about note text,
not the figures being edited, so re-deriving them on a figure-validation isn't worth the
latency. `_recheck_from_facts` and `_refresh_persisted_cross_checks` both preserve the
advisory rows; the headline denominator counts `passed + failed` with warnings shown as a
separate "N advisory" note.
