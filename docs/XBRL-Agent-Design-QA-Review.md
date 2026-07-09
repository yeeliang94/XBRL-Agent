# XBRL Agent — Design & QA Review

**Reviewer role:** Product Designer + QA specialist
**Scope:** Walkthrough of the running app at `http://localhost:8002/` — Extract (home), History, Run detail (Overview / Figures / Cross-checks / AI review / Notes), Field labels, Benchmarks, Settings.
**Date:** 9 July 2026

---

## TL;DR

The product is functionally rich and the plain-language *help text* is genuinely good in places (Settings toggles, page subtitles). But it reads as an engineering tool wearing a thin product skin: technical identifiers leak into the operator-facing UI, the design language is inconsistent (two different "primary" button colours, mixed save models, inconsistent page widths), and there are a handful of real bugs — including one that would block the core setup task.

Findings are tagged **[Bug]**, **[UX]**, **[Design]**, or **[Content]** and ordered by priority within each section.

---

## Priority bugs (fix first)

1. **[Bug] Settings › "AI service address" is pre-filled with the user's email (`admin@test.com`).**
   The field requires an `https://` URL (per its own helper text) and is showing a validation-error border. A user landing here can't tell what's wrong or what the correct value is — and this is the field that makes the whole tool work. Looks like a data-binding error (email bound to the wrong field). *Highest priority — blocks first-run setup.*

2. **[Bug] Raw internal error string leaks into the Notes tab.**
   Under **List of Notes** the UI renders `target matched no elements: {'table': 0, 'cell': {'r': 5, 'c': 2}}`. A raw Python-dict error is shown to the operator with no plain-language explanation or recovery action.

3. **[Bug] Deep-linking a run by URL is inconsistent.**
   - The real run view lives at `/history/{id}`.
   - Typing `/run/159` (a *completed* run) instead renders a blank **Run Configuration** panel with a "Start extraction" button — i.e. it looks like you're about to re-run a finished job. Deep links should resolve to the run's actual state or 404, never silently drop you into a re-run form.
   - `/field-labels` typed directly redirects to the Extract home page instead of the Field labels page (route only works via the nav click).

4. **[UX] History rows are hard to actually open.**
   Rows are marked up as `role="button"` and the page subtitle says "open one to review…", but clicking the filename text often does nothing (the click only registers programmatically / on part of the row). Users will click and feel the app is broken. Make the entire row a reliable click target (ideally a real `<a>` so middle-click / open-in-new-tab work).

---

## Cross-cutting design-language issues

1. **[Design] Two competing "primary" colours.** The app's primary action is orange (`Choose file`, `Start extraction`, `Download filled Excel`, `Create benchmark`, `Save`). But **"Run AI review again"** and **"Run notes review again"** are **blue**. Pick one primary colour; if blue is meant to signal "secondary/re-run", make that a deliberate, documented variant rather than a one-off.

2. **[Design] Inconsistent page content width.** Extract, Benchmarks and Settings render in a narrow centred column (~740–940px); History, Field labels and the Run detail run near-full-width (~1270px). Moving between pages the content visibly jumps. Standardise a max-width (or a small set of intentional layouts).

3. **[Design] Mixed save models.** Settings › Notes table style says *"changes save automatically (no Save button)"* — yet the same page has a prominent orange **Save** button at the bottom. Users can't tell which settings persist immediately vs. require Save. Unify: either auto-save everything (with a "Saved" toast) or gate everything behind Save.

4. **[Content] Technical identifiers exposed to a non-technical operator.** Per the project's own note, the primary operator is a PM who doesn't read code. Yet the UI surfaces:
   - Cross-check names as snake_case (`sofp_balance`, `sopl_to_socie_profit`, `socf_articulation`).
   - Template picker values like `mfrs-company-notes-issuedcapital-v1`.
   - Run config as `SOFP=OrderOfLiquidity, …` and `Model overrides: SOFP=openai.gpt-5.4, …`.
   - Model name as a free-text field (`openai.gpt-5.4`) — typo-prone; should be a picker.
   Add human-readable labels (keep the technical ID as secondary/tooltip).

5. **[Design] Number formatting for negatives is inconsistent.** In the Figures table, editable cells show `-20,667` (minus sign) while calculated rows show `(20,667)` (accounting parentheses) in the same column. Accountants expect one convention — parentheses are standard for financial statements.

6. **[Design] Sparse / generic visual identity.** The interface is clean but utilitarian: wordmark-only ("XBRL Agent"), system font, minimal use of colour or hierarchy. If PwC branding is intended (referenced in the design system), it isn't showing up strongly here.

---

## Page-by-page

### Extract (home) — `/`
- **[UX] The KPI strip signals a funnel problem the UI ignores.** 214 total runs, **78 drafts in progress**, **0 completed this month**. 36% of all runs are abandoned drafts and there's no way to clean them up (no bulk delete / archive). Consider a "resume or discard" nudge and draft cleanup.
- **[Design] The 4th KPI card breaks the pattern.** Three cards show a big number + label; the fourth shows a status pill ("Not started") + "Last run status". Different shape/among a stat row reads as unfinished. Either make it a number metric or visually separate "last run status" from the counters.
- **[UX] Recent runs are noisy and ambiguous.** Multiple identical `x.pdf` / `Accounts.docx` entries distinguishable only by timestamp. Show a bit more context (standard/level, or a thumbnail) and de-duplicate visually.
- **[Good] Clear one-line value prop, obvious drag-and-drop dropzone, prominent CTA.**

### History — `/history`
- **[UX] Default sort buries the useful runs.** Drafts ("Not started") are newest, so the default view is a wall of empty drafts; all 82 completed runs are pushed down. Default to meaningful runs, or separate drafts into their own view/section.
- **[UX] Columns don't match filters.** There's a **Standard** filter but no Standard column; there's a **Score** column that's always "—" (only populated when a benchmark is attached — not explained). Add the columns the filters imply, and explain empty Score.
- **[Design] Native date inputs are unstyled** (`dd/mm/yyyy`), clashing with the custom selects next to them.
- **[UX] No result count, no pagination controls, no bulk actions.** With 200+ runs this list is unmanageable.
- **[Design] The small "RM" denomination chip appears under only some filenames** — inconsistent, and its meaning isn't labelled.

### Run detail — `/history/{id}`
- **[UX] Overview leads with engineering telemetry, not outcomes.** The first thing shown is Total tokens (1,303,285), Est. cost ($2.8692), Turns, Tool calls, Agents. An operator's first question is "did it extract correctly / anything to fix?" Lead with a results summary (figures extracted, checks passing, flags) and demote cost/tokens to a Telemetry/Activity area.
- **[Content] `Est. cost $2.8692` shows 4 decimal places** — odd precision for money; round to cents.
- **[Figures tab — strong overall]** Side-by-side source PDF + editable values, state chips (`observed` / `Calculated`), inline source citations, search, and a "Needs attention" panel is a genuinely good reviewer workflow. Issues:
  - **[Bug/UX] Repeated empty header rows** — "STATEMENT OF CASH FLOWS" appears three times in a row, looking like duplicate/empty rows.
  - **[UX] Column headers `CY` / `PY` carry no years** — user must infer which period each is.
  - **[UX] "No source page recorded for this value — jump to a page manually"** defeats the point of the linked PDF for those rows; the source column has a citation but the viewer doesn't jump to it.
  - **[Content] "Needs attention" copy is dense and technical** ("Sheet 11 … cites pages [19]; Sheet 12 … cites pages [21]. No overlap —…").
- **[Cross-checks tab]** Good expected/actual/diff table. But check names are snake_case and messages are technical; the headline "8/11 checks passing" (amber) is hard to reconcile with the rows shown (8 passed + 1 N/A visible) — clarify what the denominator counts.
- **[AI review tab]** Clean empty state ("No reviewer changes — this run is the original extraction"). The "Run AI review again" button is the off-brand blue (see cross-cutting #1).
- **[Notes tab]** Per-section model dropdown + "Format" link + "Run notes review again" (blue) + "Re-extract notes (replaces your edits)" is a lot of controls competing for attention; the destructive "replaces your edits" action is a plain text link with no obvious guard. Plus the leaked error string (priority bug #2).

### Field labels — (nav only; no working URL route)
- **[UX] Template picker is unusable without XBRL fluency.** The only way to choose a template is a dropdown of IDs like `mfrs-company-notes-issuedcapital-v1`. Break it into human-friendly facets (Standard / Level / Statement / Variant) or add labels + search.
- **[UX] No explanation of row types.** Some rows have a **Rename** button, some (grey) don't; some labels start with `*`. The meaning of the asterisk and of the non-renamable rows is never explained.
- **[UX] No search/filter within a template's labels** and **no indicator of which labels are already customised** — painful on large templates.
- **[Good] Clear purpose line:** "This doesn't change the XBRL — only the display text."

### Benchmarks — `/benchmarks`
- **[UX] "Run number" is a free-text field ("e.g. 159").** Operators won't remember run numbers. Replace with a searchable run picker (filename + date + status).
- **[UX] No way to view or edit a benchmark from its card** — only **Delete**. The gold-value editor exists but isn't discoverable from here; add "Open / Edit".
- **[Design] "102 gold cells" renders in monospace** while the rest is proportional — typographic inconsistency.
- **[Good] Clear explanation, sensible "From a run vs. from a workbook" choice, destructive Delete styled as a red outline.**

### Settings — `/settings`
- **[Bug]** AI service address = email (priority bug #1).
- **[UX] Mixed save model** (cross-cutting #3): auto-save copy + a Save button on the same page.
- **[Design] "Test Connection" and "Save" are small and bottom-left/bottom-right** with no sticky footer; on a long form they're easy to miss.
- **[Content] Model Name is free text** ("openai.gpt-5.4") — should be a validated picker.
- **[Good] The toggle explanations (reviewer, spot-check, prior-year hints) are excellent plain-language microcopy** — this is the standard the rest of the app should match.

---

## What's working well (keep)
- Plain-language subtitles and toggle explanations in Settings and page headers.
- The Figures reviewer: PDF-beside-values, editable cells, provenance chips, source citations, "needs attention" surfacing.
- Confidence indicators in the pre-run scan ("Confident / Fairly sure / Please check / Not detected").
- Sensible destructive-action styling (red) and a clear primary CTA per screen (when it's the right colour).

## Interaction & modal testing (buttons, popups, confirms)

I exercised the interactive elements — modals, confirms, inline edits, and stateful buttons — not just the static pages.

### Session / deep-link (found while opening the run)
- **[Bug] A full page load / deep link logs you out.** Hard-navigating to `/history/191` dropped to a **Sign in** screen; the SPA session doesn't survive a reload. This matters because the app markets run URLs as shareable (draft persistence → "shareable as `/run/{run_id}`") — a shared link lands the recipient on a login wall, and (per the routing bug above) even the wrong page after login.
- **[Bug] SPA routes don't update the URL.** On Field labels the nav shows the page but the address bar stays `/`. That's the root cause of the `/field-labels` deep-link failing — there's no URL to link to. Run tabs (Figures/Notes/etc.) likewise aren't URL-addressable, so you can't bookmark or share a specific tab.

### Fill mTool template — modal ✅ mostly good
- Clear explanation, upfront counts ("73 values across 8 sheets", "25 written notes", "Excluded: 56 SOCIE/matrix"), sensible checkboxes, and a non-destructive preview link. **Esc closes it.**
- **[Bug/Content] Denomination label contradicts the run.** The modal says **"denomination: units"** while the run's Overview says **Denomination: RM**. Same concept, two different words — confusing, and "units" reads like a placeholder.
- **[UX] "Check notes against this template" is a silent no-op** when no file is chosen — clicking it does nothing and gives no feedback. Disable it (or show "Choose a template first") until a file is selected.
- **[Design] Native, unstyled `Choose File` input** with no drag-and-drop — inconsistent with the nice styled dropzone on the Extract page.
- **[UX] No corner “✕” close** — only a bottom "Close" text link (Esc does work, but that's not discoverable).

### Delete run — confirm ✅ good
- "Delete run 191?" clearly states what's removed vs kept ("The original PDF and workbook files on disk are kept"). Cancel + red Delete. Good pattern; use it as the template for other confirms.

### Re-extract notes — confirm ✅ good copy, ⚠️ clunky flow
- Confirm explains the consequence precisely ("it will overwrite 12 edited cells… your current edits stay in place until that new run finishes").
- **[UX] The action doesn't actually re-extract** — it sends you to the Extract page to **manually re-upload the same PDF** and start a new run. The app already has the PDF on disk; making the user re-find and re-upload it is avoidable friction.

### Validate figures — stateful button ⚠️ reveals stale counts
- **[Bug] The figures summary shows stale numbers until you manually click "Validate figures".** On load: **"8/11 checks passing"** (amber) + **"Needs attention (3)"**. After clicking Validate: **"8/8 … 0 failed · 0 warnings"** (green) + **"All clear."** The denominator silently changed (11 → 8) and 3 warnings vanished. A user will either trust wrong numbers or be alarmed by warnings that disappear on re-check. Validation should run (or the counts reconcile) on load, and the "passing" denominator should be consistent with the Cross-checks tab.

### Table style — inline panel ✅
- Expands an inline editor (not a modal) with the same controls as Settings + "Reset style to firm default". Good reuse; the "this run overrides the firm default" relationship is only in tiny helper text, and there's no adjacent live table preview to see the effect.

### Field labels — Rename ⚠️ broken inline edit
- **[Bug] Rename opens an EMPTY textbox** — it doesn't prefill the current label, so to rename "Notes – Issued capital" you must retype the whole thing, and you lose sight of the original text.
- **[Bug] No Save / Cancel affordance in edit mode.** The row's Rename button disappears and only a bare textbox remains — nothing tells the user how to commit or abort (Esc happens to cancel). Add explicit Save/Cancel (and prefill the value).
- **[UX] Template picker is a flat list of 45 cryptic IDs** (`mfrs-company-notes-issuedcapital-v1` … `mpers-group-sore-v1`) with no grouping or search — confirmed via the full option list.

## Suggested first sprint
1. Fix the AI-service-address binding bug and the leaked Notes error string.
2. Fix session persistence (stay logged in across reloads) and make SPA routes URL-addressable — deep links/shared run links currently break. Then fix History row clicks and the `/run/{id}` config-page misfire.
3. Fix the Figures "8/11 → 8/8" stale-count bug (validate on load / reconcile with Cross-checks).
4. Fix Field labels Rename (prefill current value + add Save/Cancel).
5. Unify primary button colour and the save model; reconcile "denomination: units" vs "RM".
6. Add human-readable labels over technical IDs (cross-checks, templates, run config); group/search the 45-item template picker.
7. Rework Overview to lead with extraction outcomes, not tokens/cost.
