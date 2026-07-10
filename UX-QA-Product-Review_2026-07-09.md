# XBRL Agent — Product / Design / QA Review

**Reviewer role:** Product Designer + Product Owner + QA
**Date:** 9 July 2026
**Build reviewed:** `http://localhost:8002` (authenticated as `admin@test.com`)
**Method:** Manual walkthrough of every authenticated surface — Extract landing, pre-run configuration, History + filters, one clean completed run and one "completed with errors" run across all six run-detail tabs (Overview, Activity, Notes, Cross-checks, AI review, Figures), the Fill mTool modal, Field labels, Benchmarks, Settings (General / Account / Users), and a stuck "running" run.

**Update (same day):** I subsequently ran a full live extraction end-to-end (uploaded `Oriental.pdf`, ran the scout pre-scan, and extracted all 5 face statements with the auto AI-review). Findings from that run are in the new **Section 11A**, and several items below were corrected as a result. The only surface still not reviewed is the unauthenticated login screen (`/login` redirects to home when a session exists; I did not log out to avoid lockout).

---

## 1. Executive summary

This is a genuinely strong, purpose-built product. The information architecture is clean, the plain-language copy is well-judged for an accountant audience, and the core review loop — *upload → confirm layout → extract → verify figures against the source PDF → download / fill mTool* — is coherent and mostly friction-free. Standout strengths: the three-pane Figures verification screen with inline source-page evidence, accountant conventions (negatives in parentheses, `*` for mandatory MBRS fields), consistent status badges, and the honest "AI extracted this — verify before filing" framing that appears throughout.

The gaps are mostly at the edges of the happy path: **lifecycle hygiene** (79 abandoned drafts and 4 permanently-stuck "running" runs with no recovery UI), **destructive actions without confirmation**, an **error state that is visible but unexplained** (a notes-formatting failure and a "completed with errors" run that never tells the user what to do next), and a set of **polish/consistency issues** (inconsistent number formatting, developer-flavoured monospace text in accountant-facing places, a very narrow content column that wastes wide screens, and an under-styled Users admin screen).

None of these are architectural. Most are 1–2 day fixes. The three that matter most for a filing tool where correctness is the product: **(1)** don't let "completed with errors" quietly offer a Download with no explanation of the error; **(2)** add confirmation + undo to destructive actions; **(3)** give stuck/abandoned runs a lifecycle.

A live end-to-end run (Section 11A) confirmed the extraction engine is strong — real source-cited figures, a genuine cross-check failure correctly caught, and an excellent streaming progress UI — but surfaced three more issues worth prioritising: a **completion screen that labels a finished `completed_with_errors` run as "Didn't finish"** (contradicting the "Completed" badge inches above it), a **live cost meter that under-reports the final bill by ~40%** ($2.03 shown vs $3.55 actual), and **confirmation that notes cannot be included at all** even after the scout detects them.

---

## 2. How to read this

Severity levels used throughout:

- **P0 – Critical:** risks a wrong filing, data loss, or a dead-end the user can't escape.
- **P1 – High:** meaningful usability or trust problem; hit on a normal path.
- **P2 – Medium:** friction, inconsistency, or polish that a professional user will notice.
- **P3 – Low:** nice-to-have refinement.

A consolidated, prioritised backlog is in **Section 12**.

---

## 3. Cross-cutting findings (design system & consistency)

**[P1] Inconsistent number formatting between columns and messages.** On the Cross-checks tab the Expected/Actual columns are correctly grouped (`1,002,593`), but the adjacent Message column prints the same figures raw and unrounded (`assets (1002593.0) vs equity+liab (1002593.0), diff=0.00`). For a numbers-first audience this reads as unfinished. Pick one convention (grouped, fixed decimals) and apply it everywhere a figure is shown.

**[P2] Developer-flavoured monospace text in accountant-facing areas.** The Activity tab (`gpt-5.4  16 turns · 12 tool calls  1m 29s`) and the Cross-check messages render in monospace with technical tokens (`diff=0.00`, `(-20678.0)`, `sore_to_sofp_retained_earnings`). It's precise but feels like a debug log rather than a professional tool. Consider a proportional font and humanised phrasing ("difference: 0.00"), keeping the raw check-id in a tooltip or "details" affordance.

**[~~P2~~ — CORRECTED, mostly withdrawn] Content width.** My first pass flagged a very narrow content column with lots of wasted whitespace. On re-testing with browser DevTools closed, this was largely a capture artifact: the app actually uses the full window width, and the Figures three-pane layout (sheets nav / figures / source PDF) is comfortable at full width. **Withdrawn** as a real defect. The one residual, low-priority observation: the *text-form* pages (Field labels, Settings, History filters) still cap at a fairly narrow measure, which is fine for readability but leaves large side margins on wide monitors.

**[P1] Destructive actions have no confirmation or undo.** "Delete run" is a prominent outlined button on every run page; the Users tab exposes "Disable", "Revoke admin" and "Reset password" as bare one-click buttons. None appear to prompt for confirmation. For an audit tool this is risky — add a confirm dialog (ideally typing the run name for Delete) and, where possible, a soft-delete/undo window.

**[P2] Acronym-heavy without a persistent legend.** SOFP / SOPL / SOCI / SOCF / SOCIE appear everywhere. The pre-run screen expands them (good), but the run-detail Overview, Activity and Figures sheet-nav mostly show bare acronyms. A hover tooltip or a small persistent legend would help newer staff.

**[P2] Status-badge color language is good but "needs attention" overstates non-blocking items.** The Overview card labels advisory (explicitly non-blocking) warnings as "NEEDS ATTENTION" in amber, sitting next to "8/8 CHECKS PASSING". At a glance the two cards look contradictory. Consider "Advisory (non-blocking)" wording or a calmer treatment so a clean run reads as clean.

**[Strength] Plain-language help text is excellent.** "Ask your IT team if you're unsure", "This doesn't change the XBRL — only the display text", the mTool modal's explanation of what will/won't be filled — this is exactly right for the audience and should be the model for the rest of the app.

---

## 4. Extract (landing page)

**Observed:** Header stat cards (215 total runs · 79 drafts in progress · 0 completed this month · last-run status), a drag-and-drop upload zone with a "Choose file" primary button, and a "Recent runs" list where each row shows filename + status + timestamp + a "Resume" action.

- **[P1] 79 "drafts in progress" is lifecycle debt, not progress.** The Recent-runs list is entirely "Not started" drafts (`x.pdf`, `test.pdf`, `Accounts.docx`…), and the stat card proudly counts 79 of them. These are abandoned uploads. They clutter Recent runs and History, and the "Drafts in progress" label misrepresents them as active work. Add a way to auto-expire or bulk-clear stale drafts, and reword the metric.
- **[P2] "Recent runs" surfaces only drafts, burying real work.** Because drafts sort by upload time, the five most-recent-runs shown are all empty drafts — a first-time viewer sees no evidence the tool has ever produced a result. Consider prioritising completed runs, or splitting "Continue a draft" from "Recent results".
- **[P2] The whole recent-run row is a single button with no distinct "Resume" target.** Accessibility tree shows each row as one unlabeled button containing all the text; the visible "Resume" link isn't separately focusable. Fine visually, weaker for keyboard/screen-reader users.
- **[P3] "0 completed this month" reads as a failure signal** on an otherwise healthy tool (81 lifetime completed runs exist). If the metric stays, pair it with a friendlier zero-state.
- **[Strength]** Clear single primary CTA, honest one-line description of what the tool does, drag-or-click upload — the entry point is clean.

---

## 5. Pre-run configuration (`/run/{id}` draft)

**Observed:** File chip → Run Configuration with Filing standard (MFRS/MPERS), Filing level (Company/Group), Denomination (RM / RM '000 / RM mil), a collapsible "Advanced settings", a Document Pre-scan block (Scout toggle + model dropdown + Auto-detect), a Statement Format section with a confidence legend (Confident / Fairly sure / Please check / Not detected) and a format dropdown per statement, Statements-to-extract checkboxes, Notes-to-include checkboxes, and a "Start extraction" button.

- **[P1 → confirmed] "Notes to include" checkboxes are disabled and *stay* disabled even after a successful pre-scan.** I first assumed they were gated on running Auto-detect. They are not — on my live run the scout explicitly reported **"Found 14 notes in the document"** and the five note checkboxes (Corporate Information, Accounting Policies, List of Notes, Issued Capital, Related Party Transactions) *remained greyed out and unselectable*. So a user cannot include any notes through this flow at all, despite the tool having detected 14. Either this is a real regression, or there's an enabling toggle I couldn't find — either way it presents as broken and blocks a headline feature. Needs an explicit enabled state (and, if intentionally off, a reason).
- **[P1] Nothing tells the user the pre-scan hasn't run yet.** The Statement Format dropdowns all read "Select format…" with grey (Not detected) dots, but there's no prompt that the document still needs scanning, and "Start extraction" appears active. A user can plausibly start a run with zero formats chosen and no scan. Add a state machine: *upload → (prompt) Auto-detect → confirm → Start*, and guard/soft-warn the Start button until formats resolve.
- **[P2] The Scout model dropdown exposes ~12 raw model names** (GPT-5.5, GPT-5.4 Mini/Nano, Gemini 3.5 Flash, Claude Opus 4.7…). This is powerful for an admin but overwhelming/irrelevant for the day-to-day preparer, and leaks vendor/model churn into the operator UI. Consider a "Recommended / Fast / Thorough" abstraction with the raw list behind Advanced.
- **[P2] The confidence legend is good but the dots are tiny and low-contrast.** "Please check" (the one that matters) needs to be visually louder than "Confident".
- **[Strength]** The whole "we pre-scan and you confirm everything before we spend money extracting" model is well-conceived, and the format-by-statement choice with confidence signalling is exactly the right pattern.

---

## 6. History (`/history`)

**Observed:** Filter bar (filename search, Status select, Standard select, From/To date pickers), a "215 runs · 50 loaded" counter, and a table (Filename / When / Status / Score / Duration).

- **[P2] "215 runs · 50 loaded" implies pagination but the mechanism isn't visible.** There's no obvious "Load more" / page control in view; a user with 215 runs can't tell how to reach run #51+. Make the load-more affordance explicit, or add real pagination.
- **[P2] Default sort buries completed work under drafts again.** Same issue as Recent runs — the top of the table is all "Not started". Defaulting the Status filter to "all except draft", or a quick "Results only" toggle, would make History useful on open.
- **[P2] Date inputs are raw native `dd/mm/yyyy` fields** with no range presets (This week / This month / Last 30 days). Minor, but presets are the 90% case.
- **[P3] Score and Duration columns are `—` for most rows** (drafts) — expected, but combined with the draft-heavy sort it makes the table look empty of data.
- **[Strength]** The filter set (status, standard, date range, filename) is well chosen, and the columns are the right ones.

---

## 7. Run detail — shared shell

**Observed:** Back link, `RUN {n}` eyebrow, filename title, status badge + timestamp, the disclaimer "Figures were extracted by AI — verify against the source PDF before filing", primary actions (Download filled Excel / Fill mTool template / Delete run), and a tab bar: Overview · Activity · Notes · Cross-checks · AI review · Figures.

- **[P0] "Completed with errors" runs still lead with a prominent "Download filled Excel" and no explanation of the error.** On run 168 (status: *Completed with errors*, 4/5 checks passing) the page looks almost identical to a clean run — the only signal is a small amber badge. There's no banner saying *what* failed, *which* statement, or *what to do next*; the biggest, most colourful button on the page invites the user to download and (potentially) file data that failed a consistency check. For a compliance tool this is the highest-risk issue in the app. Add a top-of-page error/warning banner that names the failing check(s), links straight to them, and visually de-emphasises Download until the user has acknowledged them.
- **[P2] The disclaimer is well-worded but static and easy to miss** in small grey text under the title. On error/needs-review runs it should escalate in prominence.
- **[Strength]** Renaming the underlying tabs for humans — "Activity" (not "Agents"), "AI review" (not "Reviewer/Correction"), "Figures" (not "Values") — is a genuinely nice product decision. Tab state is URL-addressable (`?tab=values`), which is great for sharing.

### 7a. Overview tab
- **[P2] "8/8 checks passing" + "3 needs attention" side by side is confusing** (see cross-cutting note). Reconcile the phrasing so passing + advisory don't look like a contradiction.
- **[P2] "10 STATEMENTS" when 5 were configured.** The count appears to include sub-sheets, but to the user who selected 5 statements it reads as wrong. Label it precisely ("10 sheets") or count what they chose.
- **[Strength]** The Run Configuration + Performance (tokens / est. cost / turns / tool calls / agents) summary is a great transparency touch — cost per run especially.

### 7b. Activity tab
- **[P2] Agents are listed alphabetically (SCOUT, SOCF, SOCI, SOCIE, SOFP, SOPL, Notes…), not in statement or execution order.** Preparers think in statement order (SOFP → SOPL → SOCI → SOCF → SOCIE). Reorder to match.
- **[P2] Monospace debug styling** (see cross-cutting).
- **[P3] SCOUT shows "0 tokens · $0.00 · 1m 51s"** — a minute of wall-clock but zero tokens/cost reads oddly; clarify what SCOUT's time represents.

### 7c. Notes tab
- **[P0/P1] A live, unexplained failure is on screen:** the *List of Notes* section shows the red message *"Formatting couldn't be applied and nothing was saved. Try again, or format one section at a time."* This is a real error state persisting in a completed run. It gives no reason and no diagnostic. At minimum: explain why (timeout? invalid content?), and don't leave a completed run displaying a raw failure with only "try again".
- **[P2] Dense controls with unclear hierarchy:** each note section has a row count, a per-section model dropdown, and a "Format" link, while the top of the tab has a *global* model dropdown + "Run notes review again", plus "Table style" and "Re-extract notes (replaces your edits)". Three different model selectors and two "re-run" verbs on one screen is a lot to parse. Group and label the global vs per-section actions.
- **[P2] "Re-extract notes (replaces your edits)"** is a genuinely destructive action styled as a quiet link. It should read as destructive and confirm.

### 7d. Cross-checks tab
- **[Strength]** This is the best screen in the app: human-readable check names ("Balance sheet balances (assets = equity + liabilities)"), clear Passed/N/A status, Expected/Actual/Diff columns, and an "Advisory Warnings" section correctly separated as non-blocking. The N/A row even explains itself ("only applies to MPERS filings; this run is MFRS").
- **[P1] Number formatting inconsistency in the Message column** (see cross-cutting) — most visible here.
- **[P3]** Advisory-warning bodies are long single-paragraph strings; a little structure (which sheets, which pages) as fielded data would scan better.

### 7e. AI review tab
- **[Strength]** Clean empty state ("No reviewer changes — this run is the original extraction"), clear Changes(0)/Flags(0), and a "Run AI review again" box with optional free-text guidance + model select. Good pattern.
- **[P3]** When there are zero changes/flags, the three stacked empty sections feel repetitive; could collapse to one line with the re-run control.

### 7f. Figures tab (the core review surface)
**Observed:** Three panes — left: sheet nav (SOFP/SOPL/SOCI/SOCIE/SOCF/Notes) + a "Needs attention (3)" panel; centre: "Review extracted results" with checks-passing + "your edits" cards, a search box, and an editable table (Line item / CY / PY / State / Source); right: the source PDF with page navigation.
- **[Strength]** This is the heart of the product and it's well designed: inline-editable CY/PY cells, an "observed" state chip, a Source column citing the exact page and line ("Page 15, 'Loss before tax…'"), negatives in parentheses, and a live PDF alongside for verification. This is exactly what an accountant needs.
- **[P1] "No source page recorded for this value — jump to a page manually."** When a figure has no evidence link, the PDF pane can't help and the burden falls entirely on the user. These unlinked values are precisely the ones most needing verification — flag them (e.g. an "unverified / no source" filter or badge) rather than treating them like any other row.
- **[P2] The source-PDF pane is too small** to actually read at this layout width (cramped by the narrow shell — see cross-cutting). Let this view go full-width and give the PDF real estate.
- **[P2] An abstract header row ("Statement of cash flows, indirect method") renders with empty editable input boxes.** Section headers shouldn't look editable — it invites accidental entry and confuses the hierarchy.
- **[P3] "Validate figures" button's outcome isn't obvious** from the label alone; a one-line hint of what it checks would help.

### 7g. Fill mTool template (modal)
- **[Strength]** Exemplary modal: explains exactly what it does, states the counts up front ("73 values across 8 sheets · MFRS company · denomination RM", "25 written notes", "Excluded: 56 SOCIE/matrix"), notes the known limitation (SOCIE not filled, totals left to mTool's formulas), and correctly **disables "Fill & download" until a template is chosen**. This is the model the rest of the app should follow.
- **[P3]** "Add missing note spots (off by default — run 'Check notes' first…)" packs a lot into one helper line; could be progressive.

---

## 8. Field labels (`/field-labels`)
**Observed:** "Rename how individual template line items are labelled on screen. This doesn't change the XBRL — only the display text." Template picker, a legend ("Greyed rows are section headers — can't be renamed; leading `*` marks a mandatory MBRS field"), and a long list of rows each with a "Rename" button.
- **[Strength]** The reassurance that renaming is cosmetic and doesn't touch the XBRL is exactly the right thing to say to a nervous compliance user.
- **[P2] Template picker defaults to "Notes — Issued Capital"** — an arbitrary, minor template. Default to a primary statement (SOFP) or the most-edited one.
- **[P3] Rename-per-row is tedious** for long templates; no search/filter within the template, no inline edit (each opens…?), no bulk view of which labels have been customised.

---

## 9. Benchmarks (`/benchmarks`)
**Observed:** "A library of financial statements with human-verified gold answers…" Add-benchmark form (From a run / From an uploaded workbook), Name, Run number, Create; plus a list card ("Oriental 1936 Berhad · seeded from run 159 · MFRS · company · 102 gold cells · SOCF·SOCI·SOCIE·SOFP·SOPL" with Open/Delete).
- **[Strength]** Clear purpose, sensible "from a run (recommended — captures sub-sheets)" default that steers users away from the lossy path.
- **[P2] This is a QA/admin surface sitting in the top-level nav next to preparer tasks.** For most accountant users it's noise. Consider gating it behind Settings/admin, or a role-based nav.
- **[P3] "Delete" on a benchmark** — again no confirmation (see cross-cutting).

---

## 10. Settings (`/settings`)
**Observed:** Tabs General / Account / Users.

**General** — global banner ("These settings apply to everyone using this tool"), AI service address, API key (masked as "current: Alza…0Y"), model select, and toggles: auto-run reviewer, spot-check even when checks pass (+ Light/Full depth), reuse prior-year hints, notes table style.
- **[P1] Global, tool-wide settings appear editable on this screen without a clear admin gate.** Changing the model or AI service address affects *everyone*, and the API-key/endpoint controls are effectively infrastructure config living in the same panel a normal user might open via the gear. Confirm these are admin-only server-side, and visually separate "firm-wide (admin)" from personal settings.
- **[Strength]** Every toggle has a plain-English explanation of the trade-off (e.g. spot-check "catching errors the checks can't — wrong value vs the PDF, scale slip, double-count"). Excellent.

**Account** — change password (current / new / confirm, "at least 8 characters").
- **[P2] Weak stated password policy (8 chars, no complexity/length guidance)** for a tool holding financial data. Consider a stronger minimum and a strength meter.

**Users** (admin) — table (Email / Name / Status / Role / Actions) with Disable / Revoke admin / Reset password (and Enable / Make admin for others), plus an Add-user form.
- **[P1] Destructive user actions have no confirmation** (Disable, Revoke admin, Reset password) — one stray click disables a colleague or resets a password.
- **[P1] Add-user form has a browser-autofill trap.** The second field (intended for Name) shows an autofilled `admin@test.com`, and the password field is pre-populated by the browser's password manager. A distracted admin could create a user with the wrong name/credentials. Set correct `autocomplete` attributes, label the fields visibly, and clear autofilled values.
- **[P2] The Users screen is visibly under-styled** relative to the rest of the app — default-looking buttons, weak table hierarchy, cramped action stack. It reads as unfinished.
- **[P3] "Disable"/"Revoke admin" appear on the current admin's own row** — even with a server-side last-admin guard, the UI shouldn't offer an admin the option to lock themselves out; hide or disable self-destructive actions with an explanation.

---

## 11. Lifecycle, running state & journey issues

- **[P0] Stuck "running" runs have no recovery path.** Four runs (e.g. run 142, "uploaded.pdf") have been in *Running* status since **28 May 2026** — over a month — with test model names like `stale-bad-model-1`. On their detail page: Download and Delete are both **disabled** (because "running"), performance is all zeros, and the Overview shows `—` checks with no live indicator. The user cannot abort, delete, or clear them; they sit in History forever. There needs to be a client-visible way to cancel/reap a run that has clearly died, and ideally a server-side stale-run reaper (the app already reconciles stale *review* tasks at startup — extend the same idea to runs).
- **[P1] Opening a running run from History shows an ambiguous static zero-state.** No spinner, no "extraction in progress — this page will update", no stage label. A user who opens an in-flight run from History can't tell whether it's working, stalled, or broken. Either live-poll this view or show an explicit "in progress" state with the current pipeline stage.
- **[P2] The live extraction progress experience wasn't observable outside the original streaming session.** (Flagged as a follow-up to review by starting a fresh run.) Worth confirming that a user who navigates away and back during a run can rejoin the progress stream rather than landing on the zero-state above.
- **[Strength]** The persistent-draft / Resume flow is well done — an interrupted upload survives and is one click from resuming.

---

## 11A. Live extraction run — observed end-to-end

I uploaded `Oriental.pdf` (a real Malaysian FS), ran the scout pre-scan, and extracted all five face statements with the default auto AI-review. The run finished in ~2 minutes and landed as **`completed_with_errors`** (6/7 cross-checks passing; one genuine failure it correctly caught: *"SOCIE equity 984,720 vs SOFP equity 963,391, diff 21,329"*). This section is the most valuable part of the review — several issues are only visible in the live flow.

**The live experience is genuinely excellent — much richer than the historical run view.**
- **[Strength] A clear 5-step stepper** (Reading template → Viewing PDF → Filling workbook → Verifying → Complete) with green/active states.
- **[Strength] A live token + cost meter** (Prompt / Completion / Cumulative / Est. cost) that updates as the run proceeds.
- **[Strength] Per-statement tabs with status dots** (SOFP/SOPL/SOCI/SOCF/SOCIE), plus AI review and Cross-checks sub-tabs appearing as those phases start.
- **[Strength] A streaming "Agent Activity" log** with human-readable steps and timings ("Filling workbook  18 fields → SOFP-OrdOfLiq  · 18 values", "Verifying totals · balanced", "Checking PDF pages 12, 21 and 22"), plus per-agent **Stop** and a global **Stop all**.
- **[Strength] Plain-language pipeline-stage banners** between phases: *"AI review: tracing flagged figures back to the PDF…"* then *"Re-running the cross-checks after the AI review…"*, each with the reassuring *"This can take a few minutes — you can leave this page open."*
- **[Strength] The scout pre-scan has its own live step log** (with document-specific content, e.g. it read "Oriental 1936 Berhad" from the table of contents) and a Stop button.
- **[Strength] A high-value scout warning on completion:** *"scout reported scale_unit='units' but the run's declared denomination is 'thousands'. Keeping scout's value; verify the presentation unit against the PDF header."* Catching a potential 1000× scale error and surfacing it in plain English is exactly right.

**New defects found only in the live flow:**

- **[P1 — status bug] The completion screen shows the same run as three contradictory statuses at once.** When the run finished, the Agent Activity log showed **"Run finished · Completed"** (green) while the Summary card immediately below showed **"Didn't finish"** (red). The true backend status was **`completed_with_errors`**. So one screen displays "Completed", "Didn't finish", and (actually) "completed with errors" for the same run. "Didn't finish" is both wrong and alarming for a filing tool — `completed_with_errors` must not render as "Didn't finish". Reconcile all three to one honest status.
- **[P1 — cost accuracy] The live cost meter materially under-reports the final cost.** The streaming meter settled at **$2.03**, but the finished run's Overview reports **$3.55** (and 1,345,923 tokens / 101 turns / 7 agents). The live meter appears to exclude the scout and the AI-review/re-check passes. A user watching the meter to decide whether to Stop is being shown ~55% of the eventual spend. Either include all agents in the live meter or label it "extraction only (excludes review)".
- **[P2] "Open run report" doesn't open where it says.** The completion copy states *"The run report opens on the Figures tab, with the notes and cross-checks alongside."* Clicking it actually lands on the **Overview** tab. Either deep-link to Figures as promised, or fix the copy.
- **[P2] The global stepper reaches "Complete" while other statements are still running.** Because the stepper reflects the *selected* statement tab, it can show "Complete" (for e.g. SOFP) while SOCF still has an in-progress dot — reads as if the whole run is done when it isn't. Consider a separate overall-progress indicator vs. per-statement.
- **[P2] Scout log has repeated, undifferentiated entries.** Five identical "Read Face Structure" rows appear with no indication of which statement each corresponds to; likewise duplicate "Discovering notes from face page". Label them (per statement/page) so the log is scannable.
- **[P2] Pre-scan latency with a long spinner.** The scout took ~60s on a 26-page document, most of it under a single "Detecting…/Checking PDF pages…" spinner. The step log helps, but a rough progress hint ("scanning page 12 of 26") or expected-time note would reduce "is it stuck?" anxiety. (The Stop button is good.)
- **[Confirms P1] Notes could not be included** despite the scout reporting 14 notes (see Section 5).

**Positive confirmations from the live run:** extraction produced real, individually **source-cited** figures (each value links to a PDF page + quoted line, e.g. "Page 15, 'Loss before tax…'"); the cross-check engine caught a real equity mismatch rather than rubber-stamping; negatives render in parentheses; and the completion view correctly foregrounds the "verify before filing" disclaimer and the scout scale warning.

---

## 12. Prioritised backlog

| # | Sev | Area | Issue | Suggested fix |
|---|-----|------|-------|---------------|
| 1 | P0 | Run detail | "Completed with errors" leads with Download, no error explanation | Top banner naming failing checks + link; de-emphasise Download until acknowledged |
| 2 | P0 | Lifecycle | 4 runs stuck "Running" for a month; no abort/delete/recover | Client cancel + server-side stale-run reaper; enable Delete on dead runs |
| 3 | P0/P1 | Notes | Raw "Formatting couldn't be applied…" failure persists on a completed run | Explain cause; provide real recovery; don't leave raw errors on-screen |
| 4 | P1 | Global | No confirmation/undo on destructive actions (Delete run, user Disable/Revoke/Reset, Re-extract notes, Delete benchmark) | Confirm dialogs (+ type-to-confirm for Delete run); soft-delete/undo where feasible |
| 5 | P1 | Pre-run | Notes checkboxes disabled with no reason; no "scan first" prompt; Start not guarded | Inline explanation; guided upload→scan→confirm→start flow |
| 6 | P1 | Figures | Figures with "no source page recorded" aren't flagged for extra scrutiny | Badge/filter unverified values |
| 7 | P1 | Settings | Global/infra settings editable without a clear admin boundary | Server-side admin gate + visual separation of firm-wide vs personal |
| 8 | P1 | Users | Autofill trap in Add-user; destructive actions unconfirmed | Fix `autocomplete`, labels; confirm dialogs; hide self-destructive actions |
| 9 | P1 | Cross-checks | Inconsistent number formatting (grouped columns vs raw messages) | Single formatting convention everywhere |
| 10 | P1 | Landing/History | Drafts dominate Recent runs & History; 79 stale drafts counted as "progress" | Prioritise results; draft expiry/bulk-clear; reword metric |
| 11 | P2 | Global | Narrow content column wastes wide screens; Figures/PDF cramped | Wider max-width; full-width for Figures & History |
| 12 | P2 | Global | Monospace/debug text in accountant-facing areas | Proportional font, humanised phrasing, raw ids in tooltips |
| 13 | P2 | Overview | "8/8 passing" vs "3 needs attention"; "10 statements" vs 5 chosen | Reconcile phrasing; precise counts |
| 14 | P2 | Activity | Agents alphabetised, not in statement order | Reorder to statement sequence |
| 15 | P2 | Pre-run | Raw model list overwhelming for preparers | Recommended/Fast/Thorough abstraction; raw list under Advanced |
| 16 | P2 | History | Pagination ("50 loaded") mechanism not visible; no date presets | Explicit load-more/pagination; range presets |
| 17 | P2 | Benchmarks | Admin/QA surface in top-level preparer nav | Role-based nav / move under admin |
| 18 | P2 | Account | Weak stated password policy (8 chars) | Stronger minimum + strength meter |
| 19 | P2 | Field labels | Arbitrary default template; tedious rename UX | Default to SOFP; add search + "customised" view |
| 20 | P2 | Users | Under-styled admin screen | Bring up to app's visual standard |
| 21 | P3 | Various | Acronym legend, "0 completed this month" tone, SCOUT 0-token time, Validate-figures hint, empty-state repetition | Small copy/affordance refinements |
| 22 | P1 | Live run | Completion shows "Completed" + "Didn't finish" for the same `completed_with_errors` run | Reconcile to one honest status; never render completed-with-errors as "Didn't finish" |
| 23 | P1 | Live run | Live cost meter ($2.03) under-reports final cost ($3.55) — excludes scout/review | Include all agents, or label "extraction only" |
| 24 | P1 | Pre-run | Notes stay disabled even after scout finds 14 notes — feature unusable in this flow | Enable note selection post-scan (or explain why off) |
| 25 | P2 | Live run | "Open run report" opens Overview, not the promised Figures tab | Deep-link to Figures, or fix the copy |
| 26 | P2 | Live run | Global stepper hits "Complete" while other statements still run | Separate overall vs per-statement progress |
| 27 | P2 | Live run | Scout log has repeated, undifferentiated "Read Face Structure" rows; ~60s spinner | Label log rows per statement/page; add progress hint/expected time |

---

## 13. What's working well (keep / amplify)

- Coherent core journey and URL-addressable, human-renamed tabs.
- The **Figures** verification screen with per-value source-page citations and a live PDF — the product's crown jewel.
- The **Cross-checks** screen: readable check names, blocking vs advisory separation, self-explaining N/A rows.
- The **Fill mTool** modal: states scope/exclusions/limitations up front and disables the action until valid — the gold standard for the rest of the app.
- Consistent, restrained visual language (neutral greys + a single orange accent, color-coded status badges) and accountant conventions (parentheses for negatives, `*` for mandatory fields).
- Plain-language, trade-off-aware help text throughout, and honest "verify before filing" framing.
- Per-run cost/token transparency.
- Persistent drafts with a clean Resume flow.

---

*Follow-up completed after the first pass: the live streaming extraction experience was run end-to-end (Section 11A). Still outstanding: (a) the unauthenticated login screen and error/lockout states — `/login` redirects when a session exists, so this needs a deliberate logout to review; (b) rejoin-after-navigation behaviour during a live run (does leaving and returning to a genuinely in-flight run reconnect to the stream, or land on the static History zero-state noted in Section 11?).*
