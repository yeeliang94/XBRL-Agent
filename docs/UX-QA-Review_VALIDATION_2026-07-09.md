# Validation of the UX/QA Product Review — 2026-07-09

**What this is:** a point-by-point fact-check of every finding in
`docs/UX-QA-Product-Review_2026-07-09.md`, done by reading the actual frontend
and backend code and by querying the live run database
(`output/xbrl_agent.db`, 216 runs). Each finding gets one of four verdicts:

- **CONFIRMED** — the code (or the data) matches the claim.
- **REFUTED** — the code contradicts the claim; the reviewer was mistaken.
- **PARTIAL** — partly true; the headline is right but a detail is wrong, or vice versa.
- **DISPUTED** — I could not reproduce the observation from code or data; needs a visual recheck.

## Headline

**The review is largely accurate and high-quality — but four of its scariest-sounding
items don't hold up, and correcting them materially changes the backlog.** The
biggest one: the flagship claim that *"you cannot include notes at all"* is **false**
— the note checkboxes are fully clickable; they just look greyed-out. And the broad
*"destructive actions have no confirmation"* charge is mostly wrong — Delete-run and
the user-management actions already sit behind confirm dialogs.

Everything the reviewer found in the **live run** (Section 11A) checks out, some of it
to the penny.

---

## The corrections that change the backlog (read these first)

### ❌ REFUTED — "Notes to include checkboxes are disabled / notes are unusable" (backlog #24, #5)
This is the review's single most alarming finding, and it's **wrong at the mechanism level**.
The note checkboxes have **no `disabled` attribute** and are fully clickable
(`web/src/components/NotesRunConfig.tsx:91-96`). Selection flows end-to-end into the
run payload (`PreRunPanel.tsx:920`) and the backend accepts it (`server.py:2508`).

What the reviewer actually saw: an **unchecked** note renders its label in light grey
(`grey300`, `NotesRunConfig.tsx:41-49`), which *reads* as disabled but still toggles on
click. The only genuinely-disabled control is the per-note **model dropdown**, which
correctly enables once you tick the note.

**But there is a real, smaller defect hiding underneath:** the scout's "Found 14 notes"
result is completely **decoupled** from these checkboxes — finding notes does nothing to
enable, pre-tick, or even nudge them. So the *feeling* that "the tool found notes but
won't let me use them" is legitimate; the fix is a contrast/affordance change plus a
post-scan nudge, **not** un-disabling a dead control. → **Downgrade from "feature
unusable" to "P2 affordance bug."**

### ❌ REFUTED (mostly) — "Destructive actions have no confirmation or undo" (backlog #4, #8, #17)
The sweeping claim is contradicted by a purpose-built shared `ConfirmDialog.tsx` that's
already wired to the important actions:
- **Delete run** → confirm dialog (`RunDetailView.tsx:588-623`).
- **Users: Disable / Enable / Make-admin / Revoke-admin** → confirm dialog (`UsersTab.tsx:195-224, 306-318`).
- **Re-extract notes (replaces your edits)** → confirm dialog fed by edited-count (`NotesReviewTab.tsx:637-657`).
- **Delete benchmark** → confirmed, but via native `window.confirm` (`BenchmarksPage.tsx:134`).

**The genuine residue** (much smaller than stated): (a) confirmation UX is *inconsistent* —
one shared dialog vs. a raw `window.confirm` on benchmarks; (b) **Reset password** has no
dialog (it's a two-step inline reveal, not one-click); (c) there is **no undo/soft-delete**
anywhere. → **Reframe #4 from "add confirmations" to "unify the confirm pattern + add undo."**

### ❌ REFUTED — "Global/infra settings editable without an admin gate" (backlog #7)
Gated on **both** layers. UI: every AI-config field is `readOnly` for non-admins with a
"managed by your administrator" banner (`GeneralSettingsForm.tsx:202, 358-362`). Server:
writes to admin-only keys return **403** (`api/config_routes.py:198-202`). Same story for
**Benchmarks** (backlog #17) — it's `adminOnly` in the nav *and* every endpoint has
`Depends(require_admin_dep)` (`TopNav.tsx:33`, `api/eval.py`). → **Drop #7; downgrade #17
to a nav-tidiness nice-to-have.** (One caveat: the *legacy* `SettingsModal` doesn't pass
`isAdmin` so it looks editable — but the server still blocks the save.)

### ❌ REFUTED — History "no load-more control" + "default sort buries completed under drafts" (backlog #10, in part)
History **does** have a "Load more (N remaining)" button (`HistoryPage.tsx:451-463`) and
**does** split drafts into a separate collapsed "Drafts — not started" section so the main
table doesn't lead with them (`HistoryPage.tsx:346-352, 428-440`). The *landing page*
"Recent runs" list is the one that's genuinely drafts-dominated — so the underlying
complaint is real, but it's misattributed to History.

### ⚠️ DISPUTED — "10 STATEMENTS when 5 were configured" (backlog #13, second half)
The Overview tile counts **face-agent rows**, which is 5 for every real 5-statement run in
the database (`RunDetailView.tsx:494-497`; verified runs 159/168/216 all = 5). I **could not
reproduce "10"** from code or data. Likely the reviewer conflated a sheet/sub-sheet count
seen elsewhere. → **Needs a 30-second visual recheck before actioning.** (The "8/8 passing
vs 3 needs attention" half of #13 is **CONFIRMED** — see below.)

### ⚠️ PARTIAL — "Abstract header row renders empty editable input boxes" (backlog, Figures 7f)
At the code level this **can't happen**: ABSTRACT rows never render value inputs; only
`kind === "LEAF"` cells are editable (`ConceptsPage.tsx:1726, 1839, 1599-1615`). If the
reviewer truly saw editable boxes on "Statement of cash flows, indirect method", that row
was **mis-classified upstream** (imported as a LEAF, not an ABSTRACT header) — a data/import
bug, not a UI bug. Worth investigating, but the fix is in the concept import, not the table.

### ⚠️ PARTIAL — "Notes formatting failure gives no reason/diagnostic" (backlog #3)
There **is** a 7-entry diagnostic taxonomy mapping error types to plain-language
reasons + remedies (`vocabulary.ts:149-165`). The reasonless "Formatting couldn't be
applied…" string is a **fallback** that only fires for an *unmapped* error type. So the P0
framing overstates it — but the real bugs are still there: an unmapped error leaked through,
**and the error strip has no dismiss** so it lingers on a completed run. → **Keep on the
backlog, but as "audit the error-type map + add a dismiss," not "errors have no diagnostics."**

---

## Confirmed findings (act on these) — with evidence

### Verified against the live database (strongest evidence)
| Finding | Verdict | Proof |
|---|---|---|
| **P0** — 4 runs stuck "running" with no recovery | **CONFIRMED** | Runs 135/138/140/142, `uploaded.pdf`, status=`running` since **2026-05-28 = 42 days**. Delete disabled when running (`RunDetailView.tsx:447`), API returns 409 (`api/runs.py:454`), **no startup stale-run reaper** (only review/notes tasks are reaped — `server.py:2360-2390`). |
| **P0** — "Completed with errors" leads with Download, no error banner | **CONFIRMED** | Runs 168 & 216 are `completed_with_errors` **with a merged workbook on disk** → Download button is live. Header shows only a small amber badge; no banner (`RunDetailView.tsx:527-603`). |
| **P1** — 79 stale drafts counted as "progress" | **CONFIRMED** | Exactly **79** `draft` rows in the DB, spanning 2026-04-26 → 2026-07-09. Label "Drafts in progress" at `StatTiles.tsx:52`. |
| **P1** — Live cost meter under-reports (~$2.03 vs $3.55) | **CONFIRMED to the penny** | Run 216: extraction-only agents (SOFP+SOPL+SOCI+SOCF+SOCIE) = **exactly $2.03 / 765,440 tok**; full run incl. SCOUT ($0.42) + CORRECTION ($1.10) = **$3.55 / 1,345,923 tok / 7 agents**. Live meter excludes scout (separate endpoint) + reviewer (emits no token events — `server.py:1606`). Matches the review's numbers exactly. |

### Verified against the code
| # | Sev | Finding | Verdict | Key evidence |
|---|---|---|---|---|
| 11A.5 | P1 | Same run shows contradictory statuses ("Completed" + "Didn't finish") | **CONFIRMED** | `completed_with_errors` → `success=false` → **"Didn't finish"** in Summary card (`ResultsView.tsx:375-385`, `server.py:5867`), while a succeeded per-agent event shows green "Completed" in the activity log. Found **four** different vocabularies for this one state (Summary "Didn't finish", log "Completed", History "Completed with errors", global timeline "Failed"). |
| 3.1 / 7d.10 | P1 | Number formatting: grouped columns vs raw `diff=0.00`/`(1002593.0)` in messages | **CONFIRMED** | Columns use `.toLocaleString()` (`ValidatorTab.tsx:115-121`); message rendered verbatim from backend (`cross_checks/sofp_balance.py:74`). |
| 3.5 / 7a.4 | P2 | "8/8 passing" beside amber "3 needs attention" looks contradictory | **CONFIRMED** | `graded` excludes advisories; `needsAttention = failed + advisories` (`RunDetailView.tsx:490-504`). |
| 5.2 | P1 | No "scan first" prompt; Start not guarded | **CONFIRMED** | `canRun` only checks ≥1 statement/note (`PreRunPanel.tsx:1002`); statements default all-on, so Start is active with zero formats + no scan. |
| 5.3 | P2 | ~12 raw model names in scout dropdown | **CONFIRMED** | 12 entries in `config/models.json`, rendered raw (`ScoutToggle.tsx:137`). *Nuance: scout picker is admin-only.* |
| 5.4 | P2 | Confidence dots tiny/low-contrast; "Please check" not louder | **CONFIRMED** | Fixed 10×10px dots, color-only severity, no size/weight emphasis (`VariantSelector.tsx:85-90`). |
| 7.1 | P0 | (see DB table above) | **CONFIRMED** | — |
| 7.2 | P2 | Disclaimer static grey, doesn't escalate on error runs | **CONFIRMED** | Unconditional render, no status branch (`RunDetailView.tsx:546`). |
| 7b.6 | P2 | Agents listed alphabetically, not statement order | **CONFIRMED** | Backend inserts `sorted(...key=s.value)` (`server.py:3970`); no client re-sort. *Fix needs backend ORDER BY or a client sort.* |
| 7b.7 / 3.2 | P2 | Monospace/debug styling on agent rows | **PARTIAL→CONFIRMED** | Activity/telemetry rows are mono (`RunDetailView.tsx:1071,1093`); *the cross-check message text itself is body font, not mono — so that half is overstated.* |
| 7b.8 | P3 | SCOUT "0 tokens · $0.00 · 1m 51s" | **CONFIRMED** | 0-token rows render literally "0 tokens · $0.00" (`RunDetailView.tsx:291-297`). |
| 7f.2 | P1 | Values with no source page aren't flagged/filtered | **CONFIRMED** | "No source page recorded…" exists (`PdfSourcePane.tsx:209`); only filters are label/sub-sheet — no unverified badge/filter (`ConceptsPage.tsx:186,217`). |
| 7f.3 | P2 | Source-PDF pane too small | **PARTIAL** | Default 440px is cramped, but it's resizable to 720px + zoom to 3× (`ConceptsPage.tsx:263,1153`). |
| 7f.5 | P3 | "Validate figures" outcome unclear | **CONFIRMED** | Label only, no tooltip/help (`ConceptsPage.tsx:993`). |
| 7c.7 | P2 | Dense notes controls / 3 model selectors / 2 re-run verbs | **CONFIRMED** | Global reviewer model + per-sheet formatter model + re-extract + review-again all stack (`RunDetailView.tsx:727-729`). |
| 8.2 | P2 | Field-labels picker defaults to "Notes — Issued Capital" | **CONFIRMED** | Defaults to first template alphabetically = `mfrs-company-notes-issuedcapital-v1` (`TemplateSettingsPage.tsx:79`). |
| 10.9 | P2 | Weak password policy (8 chars) | **CONFIRMED (by design)** | `MIN_PASSWORD_LEN = 8` with an explicit comment that argon2id + lockout are the real defences (`auth/passwords.py:19`). |
| 10.11 | P1 | Add-user form autofill trap | **CONFIRMED** | Email/name/password inputs have **no `autoComplete` attribute**, email directly above password — the exact autofill heuristic. Notably `GeneralSettingsForm` already guards this; the add-user form doesn't. |
| 10.13 | P3 | Self-destructive actions shown on admin's own row | **CONFIRMED** | Disable/Revoke-admin rendered for every row; component never learns the current user (`UsersTab.tsx:184-224`). |
| 11.2 | P1 | Running run from History = static zero-state | **CONFIRMED** | Single fetched JSON, no SSE/poll; badge never updates (`RunDetailPage`/`RunDetailView.tsx:532`). |
| 11.3 | P2 | Can't rejoin a live run after navigating away | **CONFIRMED** | SSE only *initiates* runs; no GET reconnect-by-run-id (`sse.ts`). |
| 11A.7 | P2 | "Open run report" lands on Overview, not the promised Figures | **CONFIRMED** | Nav dispatches History branch without `initialRunTab`, defaults to overview (`App.tsx:694`, `RunDetailView.tsx:352`). |
| 11A.8 | P2 | Stepper hits "Complete" while other statements still run | **CONFIRMED** | Stepper is fed the *selected tab's* phase (`ExtractPage.tsx:267-270`). |
| 11A.9 | P2 | Scout log repeated undifferentiated rows | **CONFIRMED** | `discover_notes` preview is a constant; "Read Face Structure" has no arg preview (`toolLabels.ts:177-185`). *Exception: page-view rows do carry page numbers.* |
| 11A.10 | P2 | Pre-scan spinner, no progress hint | **CONFIRMED** | Just a spinner + static "Detecting..." (`ScoutToggle.tsx:152`). |
| 4.7 / 4.8 / 4.9 | P2/P3 | Recent-runs sorts by upload time; whole row is one button; "0 completed this month" | **CONFIRMED** | Recent = unfiltered `created_at DESC` (`api.ts:282`); row is a single `role="button"` with a non-focusable "Resume" span (`RecentRunsList.tsx:74-91`); month metric excludes `completed_with_errors` (`api.ts:307`) — *actually worse than the reviewer noted.* |
| 6.12 / 6.13 | P2/P3 | Native date inputs, no presets; score/duration dashes | **CONFIRMED** | `HistoryFilters.tsx:133-155`; `HistoryList.tsx:202-219`. |
| 7e.13 | P3 | Three stacked repetitive empty sections in AI-review | **CONFIRMED** | Three near-synonymous empties (`ReviewTab.tsx:314,347,399`). |
| 7g | Strength | mTool modal: counts, exclusions, disabled-until-template | **CONFIRMED** | `MtoolFillModal.tsx:535-542, 961`. |

### Strengths — all confirmed
The reviewer's praise is accurate: URL-addressable human-renamed tabs (`?tab=values`),
the Figures three-pane source-cited verification surface, the Cross-checks readability +
blocking/advisory split, the mTool modal, per-run cost transparency, and the persistent-draft
Resume flow all check out in code.

---

## Net effect on the prioritised backlog (Section 12)

**Drop or sharply downgrade:**
- **#24** (notes unusable) → **P2 affordance bug** (grey label + no post-scan nudge). Notes DO work.
- **#7** (settings admin gate) → **drop**; already gated UI + server.
- **#4** (no confirmations) → **reframe**: confirmations mostly exist; the real gaps are *inconsistent* confirm UX + *no undo* + reset-password/benchmark-delete edges.
- **#10 (History half)** → **drop the "no load-more / drafts-on-top" claim**; keep the *landing-page* Recent-runs version.
- **#17** (benchmarks in preparer nav) → **drop**; already admin-only.

**Needs a visual recheck before actioning:**
- **#13 "10 statements"** — code says 5; couldn't reproduce 10.
- **Figures editable abstract row** — code says impossible; likely an import mis-classification to chase separately.
- **#3 notes formatter** — reframe to "audit error-type map + add dismiss."

**Rock-solid, prioritise as written (several with database-grade proof):**
- **#2** stuck running runs (4 of them, 42 days) + startup reaper — the code even has a
  TODO acknowledging this gap (`server.py:4566-4567`).
- **#1** completed-with-errors download/banner.
- **#22** the "Didn't finish" status bug (worse than reported — *four* conflicting labels).
- **#23** cost meter (exact match: $2.03 vs $3.55).
- **#9** number formatting, **#5** scan-first guard, **#8** add-user autofill.

---

*Method: 6 parallel code investigations across `web/src` + backend, plus direct queries
against `output/xbrl_agent.db` (216 runs). Verdicts cite `file:line`. The one surface neither
the reviewer nor this validation exercised is the unauthenticated login/lockout screen.*
