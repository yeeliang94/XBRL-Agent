# Evals Workspace — PRD

**Status:** Draft for review · 2026-07-10
**Origin:** Brainstorm session 2026-07-10 (Approach B: first-class Evals workspace, phased delivery)
**Builds on:** the existing gold-benchmark feature (docs/PLAN-eval-benchmark.md, schema v16) and the mTool fill pipeline's file-reading capability (docs/PLAN.md, mtool/).

---

## Overview

### Problem

Today we can grade ONE run against ONE hand-attached gold benchmark. That answers
"was this run right?" but not the questions that actually matter for running this
product:

- **Are we getting better over time?** When we change a prompt, a model, or ship a
  new app version, there is no way to prove quality went up (or catch it going down)
  across a body of documents.
- **Are we consistent?** If the same PDF run twice gives different numbers, that's a
  trust problem even when we can't say which one is right. We have no measure of this.
- **Gold answers are expensive to create.** Seeding gold from a run and hand-correcting
  it takes real effort per document, which caps us at a handful of benchmarks.
- **Notes are unmeasured.** The notes pipeline (the narrative disclosures, sheets
  10–14/11–15) has no quality score at all.

Meanwhile a new asset has appeared: **human-filled mTool workbooks**. Accountants
already fill the official SSM mTool Excel template by hand for real filings. Each one
is a ready-made gold answer — if we can read it.

### Solution

A first-class **Evals** area of the app: build a library of documents with gold
answers (including gold ingested straight from human-filled mTool files), run the
whole library as a batch — optionally several repeats per document — and see
accuracy, consistency, and health scores on a dashboard that tracks change over time
and compares one configuration against another.

### Target User

- **Primary:** the product/quality owner (a PM) who decides whether a prompt/model/app
  change ships, and reports quality to stakeholders.
- **Secondary:** an engagement reviewer who wants a per-document score against the
  human-filled version as a QA aid.

### Success Criteria

1. **Gold at scale:** creating a reviewed gold benchmark from a human-filled mTool
   workbook takes **under 15 minutes** (upload → auto-ingest → human review in the
   gold editor), vs. the current hand-authoring effort. Target: **10–50 benchmarks**
   in the library within a quarter.
2. **"Did we improve?" is a one-screen answer:** after any change, one suite run +
   one comparison view shows per-document and aggregate accuracy deltas across the
   whole library — no spreadsheet assembly by hand.
3. **Consistency is a number:** any document can be run N times and get an agreement
   score (what % of extracted values were identical across all repeats), with the
   disagreements listed.
4. **No runaway spend:** every suite run shows an upfront cost/time estimate, live
   progress, and survives interruption (an aborted batch keeps its finished results
   and can resume).

---

## User Stories

| # | Story | Priority | Phase |
|---|-------|----------|-------|
| 1 | As the quality owner, I want to **upload a human-filled mTool workbook and get a gold benchmark out of it**, so that building the gold library scales with files I already have instead of hours of hand-entry. | MUST HAVE | 1 |
| 2 | As the quality owner, I want to **run one document N times and get a consistency score**, so that I can detect flaky extraction even on documents with no gold answer. | MUST HAVE | 1 |
| 3 | As the quality owner, I want to **group documents into a Suite and run the whole suite as one batch**, so that evaluating a change across 10–50 documents is one action, not 50. | MUST HAVE | 2 |
| 4 | As the quality owner, I want to **compare two suite runs side by side and see trends over time**, so that "are we better than last month / than config A?" has a visual, shareable answer. | MUST HAVE | 2 |
| 5 | As the quality owner, I want each document's **notes placement/coverage** rolled into the scorecard, so that notes quality stops being invisible. | NICE TO HAVE (v1) | 2 |

Out of MVP entirely (Phase 3, see Scope): AI-judge grading against the PDF, notes
prose-fidelity scoring, numeric-values-inside-notes grading.

---

## Detailed User Flows

### Flow 1 — Create a gold benchmark from a human-filled mTool workbook

**Trigger:** On the Benchmarks page, the user clicks **"New benchmark → From mTool
file"** (a third source, alongside today's "seed from run" and "upload filled
workbook").

**Steps:**

1. **User input:** picks filing standard (MFRS/MPERS) + filing level (Company/Group),
   names the benchmark (e.g. "FINCO FY2021 — human filing"), **declares the figure
   unit the file is stated in** (full figures vs. thousands / RM'000 — mandatory,
   no auto-guessing), and uploads the `.xlsx` mTool file the human filled.
2. **System:** opens the file with the same zip/XML reading machinery the mTool fill
   feature already uses (no Excel needed, works server-side). It detects which
   columns hold current-year/prior-year values (the same column-detection logic used
   for filling, run in reverse), reads every filled value row, and matches each row
   label to our internal concept catalogue — the same label mapping the mTool
   exporter uses when writing, reversed.
3. **System:** creates the benchmark and writes the matched values as gold facts
   (into the same gold storage the current feature uses — nothing about grading
   changes downstream).
4. **Output — the ingest report** (shown before the user leaves the screen):
   - ✅ values matched and imported (count, by statement)
   - ⚠️ rows read but not matched to a concept (listed verbatim, so a systematic
     label mismatch is visible immediately)
   - ⚠️ a **scale backstop warning**: the user's declared unit is authoritative
     (values are converted accordingly before saving), but if the numbers look
     wildly inconsistent with that declaration (~1000× off from what a financial
     statement plausibly contains), the report warns loudly so a wrong declaration
     gets caught before it corrupts gold.
5. **User input:** reviews the imported gold in the existing gold editor (humans make
   entry mistakes too — gold stays human-reviewable, exactly like the current
   seed-from-run flow), corrects anything, saves.

**Error states:**

- Column layout can't be auto-detected with confidence → the screen asks the user to
  point at the value columns manually (same "explicit column map" fallback the fill
  endpoint already uses). Never silently guesses.
- File isn't a recognisable mTool package → clear rejection message naming what was
  expected.
- Zero gold values extracted → rejected with an explanation (matches the existing
  "useless 0/0 benchmark" guard).
- Standard/level chosen doesn't match what the file's sheets look like → warning
  listing the mismatched sheets before anything is saved.

---

### Flow 2 — Repeat runs + consistency score

**Trigger:** On the extract page, a new **"Repeats"** control (default 1, max 5) next
to the existing model/benchmark options. Also available when launching a suite
(Flow 3), where it applies per document.

**Steps:**

1. **User input:** uploads a PDF as usual, sets Repeats = e.g. 3, starts.
2. **System:** executes 3 complete, independent extraction runs of the same document
   with the same configuration, one after another (each is a normal run — it appears
   in History, has its own trace, cross-checks, etc.). The runs are linked together
   as one **repeat group**.
3. **System (after the last repeat):** computes the consistency score by lining up
   the extracted values across the repeats, value slot by value slot (keyed by the
   internal concept identity, so "Revenue, current year" in run 1 is compared to
   exactly "Revenue, current year" in runs 2 and 3):
   - **Agreement rate** = % of value slots where every repeat produced the identical
     number (or every repeat left it blank).
   - **Disagreement list** = every slot where repeats differ, showing each repeat's
     value side by side.
4. **Output:** a consistency panel on the run-group page: the headline agreement %,
   the disagreement table (sortable by size of spread), and — when a gold benchmark
   was attached — each repeat's accuracy score, so the user sees both "how right"
   and "how stable" together.

**Error states:**

- One repeat fails or is aborted → the group computes consistency over the repeats
  that finished (minimum 2), and clearly labels the failed one. A group with <2
  finished repeats shows "consistency unavailable" rather than a misleading 100%.
- Consistency needs identical configuration — the repeats are launched by the system
  from one form submission, so config drift within a group is impossible by design.

---

### Flow 3 — Suites: build a corpus, run it as a batch

**Trigger:** A new top-level **Evals** section in the navigation, with a **Suites**
tab. User clicks "New suite".

**Steps:**

1. **User input (suite setup, one-time):** names the suite (e.g. "MFRS Company
   regression set"), then adds documents. Each document = a PDF (or Word file) +
   optionally a gold benchmark from the library + its filing standard/level. A
   document without gold still contributes consistency + health scores.
2. **User input (launching a suite run):** picks the model, repeats per document
   (**default 1**, max 5 — repeats are opt-in, for consistency measurement; a
   30-document suite run once = 30 extraction runs, not 90), and the usual toggles
   (scout, notes on/off). The screen shows an **estimate before starting**: number
   of extraction runs (docs × repeats), rough wall-clock (based on recent average
   run duration), and a reminder that each run spends real tokens.
3. **System:** queues the runs and executes them with limited parallelism
   (**3 documents at a time**) to respect provider rate limits. Every
   child run is a completely normal run reusing the existing pipeline end-to-end;
   the suite runner is a thin layer that launches, watches, and records.
4. **Output (live):** a suite-run progress page: a row per document showing queued /
   running (with the current pipeline stage) / done (with scores) / failed. Overall
   progress bar and running token/duration totals.
5. **System (per finished document):** records the scorecard — accuracy vs gold
   (when gold attached), consistency (when repeats ≥ 2), and **health metrics** the
   pipeline already computes for free: cross-check pass rate, notes coverage
   status, reviewer flags raised, agents failed.
6. **Output (suite run complete):** the suite run's summary page — aggregate scores,
   per-document table, and the configuration it ran under (model + **app version**,
   see Technical Approach) permanently stamped on it.

**Error states:**

- A document's run fails → marked failed in the suite run, the batch continues.
  Failure never poisons the aggregate silently: the summary states "aggregates
  computed over N of M documents".
- User hits Stop → in-flight runs are aborted (existing Stop-All machinery),
  finished documents keep their scores, and the suite run is marked "partial".
  A **Resume** button re-launches only the documents that didn't finish, appending
  into the same suite run.
- Server restarts mid-batch → same as Stop: the suite run reconciles to "partial"
  on startup (mirroring how stale review tasks are already reconciled) and offers
  Resume.

---

### Flow 4 — Results: trends over time + side-by-side comparison

**Trigger:** The **Results** tab of the Evals section.

**Steps:**

1. **User input:** picks a suite.
2. **Output — trend view:** a chart of the suite's runs over time, one line each for
   accuracy, consistency, and cross-check pass rate. Each point is one suite run,
   labelled with its date, model, and app version. This is the "are we improving?"
   picture, shareable via screenshot.
3. **User input:** selects any two suite runs (e.g. "before prompt change" vs
   "after") → **Compare**.
4. **Output — comparison view:** a per-document table with both runs' scores and the
   delta, colour-coded (improved / regressed / unchanged), plus the aggregate delta
   at the top. Clicking a document drills into the value-level diff: which specific
   line items were right in A but wrong in B, and vice versa — so a regression is
   traceable to actual numbers, not just a score.
5. **Experiments are just labelled comparisons:** launching a suite run lets the user
   attach a free-text label ("gpt-5.4 baseline", "new SOCF prompt"). The compare
   picker shows labels, so an A/B experiment = run the suite twice with different
   configs and compare — no separate experiment machinery to learn.

**Error states:**

- Comparing suite runs with different document sets → the table shows the union,
  greys out documents present in only one run, and excludes them from the aggregate
  delta (stated explicitly on screen).
- A suite whose documents' gold was edited between two runs → the compare view
  warns that gold changed in between (gold edits are timestamped), since that can
  move scores without any pipeline change.

---

## Technical Approach

*(Plain-language; a build plan will follow the PRD.)*

**Stack:** everything rides the existing app — FastAPI + SQLite backend, React
frontend with inline styles (per house rules). No new services, no job-queue
infrastructure: the suite runner is a background loop inside the server process,
the same pattern the reviewer pass already uses.

**Key reuse (this is mostly assembly, not invention):**

- **Grading:** unchanged — the existing grader and gold storage do all scoring.
- **mTool ingest:** the mTool package already reads these files (it does so today to
  fill them); the new work is running its label/column mapping in reverse and piping
  results into gold storage.
- **Health metrics:** cross-check results, coverage checklist, reviewer flags, and
  per-agent status are already persisted per run — the suite layer only aggregates.
- **Batch execution:** each suite child is a normal run through the normal pipeline,
  so every existing guarantee (audit rows, terminal statuses, traces, partial-merge
  on abort) applies automatically.

**New data (described plainly):**

- A **Suite**: name + list of documents (each: source file, standard/level, optional
  benchmark link).
- A **Suite Run**: which suite, when, the configuration snapshot (model, repeats,
  toggles, label), status, and its per-document scorecards.
- A **Repeat Group**: links N runs of the same document launched together; stores
  the computed consistency result.
- **App-version stamp on every run** (new column on the existing runs table): which
  version of the application/prompts produced this run. Without this, "better over
  time" is guesswork. Smallest possible addition, disproportionate value — lands in
  Phase 1.
- Suite documents store the source file itself (copied into managed storage), so a
  suite re-run months later uses byte-identical inputs.

**Key dependencies:** none new on the backend. Frontend adds ONE chart library —
**Recharts** (the standard React charting library; renders plain SVG inline, so it
coexists with the inline-style house rule and needs no CSS classes).

**Chart inventory** (everything the workspace plausibly needs — deliberately short):

| Chart | What it answers | Phase |
|---|---|---|
| **Score trend line** — accuracy / consistency / cross-check pass rate per suite run, over time | "Are we improving?" — the core chart | 2 |
| **Compare delta bars** — per-document score change between two selected suite runs, sorted worst-first, colour-coded | "What exactly regressed after this change?" | 2 |
| **Document × run heatmap** — grid of documents (rows) vs suite runs (columns), cell colour = score | "Which documents are chronically weak vs. one-off blips?" | 2 (nice-to-have) |
| **Cost/tokens trend** — spend per suite run over time (data already in telemetry) | "What does our eval habit cost?" | 3 |

Everything else (consistency disagreements, ingest reports, scorecards) is tables,
not charts — tables with sorting beat charts for detail work.

---

## Scoring Design

This section is the full scoring specification, grounded in the data the app
already stores. Guiding principles:

1. **Every number decomposes.** The suite aggregate is explainable by document
   scores, a document score by statement scores, a statement score by individual
   value slots. No metric at the top that can't be traced to rows at the bottom.
2. **Denominators anchor to gold, not to run behaviour**, so scores stay
   comparable across time and across configurations.
3. **Exact match, no tolerance bands.** Financial statements are exact-figure
   documents; a fuzzy ±% band would mask real errors. (A hair's-width float
   tolerance absorbs computer-arithmetic noise only — that's already how the
   grader works.)
4. **No perverse incentives.** Nothing in the headline rewards leaving cells
   blank, and nothing punishes legitimately filling more than the human did.

### The unit of grading: a value slot

Everything numeric is graded at the **value slot**: one concept (a specific line
item) × one period (current year / prior year) × one entity scope (company /
group). This is exactly how facts are stored, so grading is a direct lookup — no
cell-coordinate matching, no spreadsheet diffing. Company filings have up to 2
slots per line item (CY, PY); Group filings up to 4. Only human-enterable slots
count (LEAF / MATRIX_CELL); computed totals are excluded — they're derived by
formula, so including them would double-count their inputs and inflate scores.

### Family 1 — Accuracy (vs gold)

**Headline: `accuracy = matched ÷ gold slots`** — "of the values the human
filed, the % we got exactly right." Unchanged formula, same denominator as
today: matched + missing + wrong. `not_disclosed` gold stays out of the
denominator; `explicit_zero` gold grades as the number 0.

**What's new: every wrong answer gets a diagnosis.** The current grader knows
only "missing" and "mismatch (scale-flagged)". The fact structure lets us
classify failures deterministically — and each class maps to a known,
historically-observed failure mode with a different fix:

| Diagnosis | Detection rule | What it means / historical incident class |
|---|---|---|
| **Scale error** | run = gold × 10^±k | Misread the "RM'000" header — the silent 1000× failure the scout context block guards against |
| **Sign flip** | run = −gold | Sign-convention error — the SOCF/dividend class of bugs (ADR-002, run-50) |
| **Period swap** | run's CY holds gold's PY *and* vice versa, same line item | Column transposition — read the right numbers, put them in the wrong year |
| **Scope swap** (Group only) | run's Group slot holds gold's Company value and vice versa | Group/Company column confusion |
| **Misplaced value** | a missing gold value appears, exactly, on a *different* line item the gold left blank — only claimed when that number is unique and non-zero in the gold set (so round-number coincidences don't produce false diagnoses) | "Right number, wrong row" — the most prompt-actionable failure there is |
| **False not-disclosed** | run explicitly asserted "not in the PDF" but the human filed a value | Judgement failure — the agent looked and wrongly concluded absence |
| **Unaddressed** | run has no record for the slot at all | Coverage failure — the agent never dealt with this line |
| **Plain wrong** | none of the above | Residual bucket |

**All of these count as wrong in the headline** — the taxonomy never softens the
score. Its job is the drill-down and the trend: after a prompt change aimed at
sign conventions, the compare view can show *sign flips specifically* went from
7 → 0, which is a far stronger signal than accuracy moving half a point.

**Beyond-gold rate (watchdog):** `values we filled where gold is blank ÷ all
values we filled`. Stays out of the headline — a blank in gold is ambiguous
(with mTool-sourced gold even more so: the file only contains what the human
chose to enter). Trended separately; a sudden jump is the hallucination alarm.
Drill-down lists beyond-gold values largest-first, since a large invented number
is more suspicious than a small one.

**Per-statement breakdown:** every slot belongs to a statement (SOFP, SOPL,
SOCI, SOCF, SOCIE) via its template, so each scorecard carries per-statement
accuracy for free. "The SOCF prompt change moved SOCF from 84% → 93% and touched
nothing else" is the sentence experiments exist to produce.

**Reviewer lift (drill-down, Phase 2):** the pipeline already snapshots facts
*before* the reviewer pass makes corrections. Grading both snapshots gives
`final accuracy − pre-reviewer accuracy` = what the reviewer pass actually
contributes — measured, not assumed, and free from existing data.

### Family 2 — Consistency (repeats, no gold needed)

**Domain:** the union of slots that *any* repeat filled. (Grading against all
template slots would let thousands of never-touched empty slots inflate
agreement to ~100%; anchoring to the union keeps the number honest.)

**Headline: `consistency = unanimous slots ÷ union slots`** — the % of slots
where every finished repeat produced the identical number. Two disagreement
types, because they have different fixes:

- **Presence disagreement** — some repeats filled the slot, others left it
  blank. Flaky *discovery*: the agent doesn't reliably find the line.
- **Value disagreement** — all repeats filled it, with different numbers. Flaky
  *judgement*: the agent finds it but reads it differently each time.

**The gold × consistency cross (the most decision-useful number here):** when a
repeat group also has gold, every unanimous slot is either **unanimously right**
or **unanimously wrong**. Unanimously-wrong slots are *systematic* errors — fix
the prompt once and all repeats improve. Disagreeing slots are *stochastic* —
prompt tweaks won't stabilise them; model/config changes might. This one table
tells you where improvement effort should go.

Rules: needs ≥ 2 finished repeats (else "consistency unavailable" — never a
misleading 100%); a failed repeat is excluded and labelled; repeats share one
launch, so config drift within a group is impossible by design.

### Family 3 — Notes

**Placement coverage (v1): `placed notes ÷ (placed + missing + suspected-gap)`**
over the top-level notes found in the document. Intentionally-skipped notes
(the pipeline records skip receipts) are excluded from the denominator — a
deliberate skip is not a failure. Sub-section verification is reported alongside:
% of sub-references verified present. If the note inventory is unavailable the
score reads "unavailable", loudly — never silently green.

**Honesty caveat, stated on the scorecard:** coverage is *self-reported* — the
inventory comes from the same system being evaluated. It's a strong regression
signal (it catches "notes stopped landing"), but it is not ground truth, which
is why it lives beside health, not accuracy.

**Prose gold — capture now, grade later:** the human-filled mTool file contains
the *actual prose the human filed* (the hidden footnote text payloads the mTool
reader already parses). Phase 1 ingest **stores these as gold prose** alongside
the numeric gold — cheap to keep, and it means Phase 3 prose-fidelity scoring
(text similarity + AI judge against human prose) has its ground truth waiting
instead of requiring a second ingestion campaign. Numeric-values-inside-notes
grading remains deferred (notes values live outside the fact store today).

### Family 4 — Health (no gold needed)

All already computed per run; the scorecard aggregates, never re-derives:
cross-check pass rate (final, post-correction pass), reviewer flags raised,
agents failed, iteration-cap hits, duration, and token spend. Positioned
honestly: health is the system's *self-assessment* — a run can pass every
cross-check and still be wrong, which is exactly why accuracy and health are
separate families.

### Aggregation & persistence rules

- **Document → suite:** the suite headline is the **simple average of
  per-document accuracy** — each filing counts equally, so one giant group
  filing can't drown ten small companies. The pooled slot-level figure
  ("12,431 of 13,102 slots") is shown as secondary, and the **worst document**
  is always surfaced — regressions hide in averages.
- **Scorecards are stamped at grading time** (extending the existing saved
  scorecard with the taxonomy counts + per-statement breakdown). Slot-level
  detail is recomputed on demand from the stored run facts and gold facts —
  both are durable, so no heavyweight new storage. Because gold is editable,
  every scorecard records *when* it was graded, and the compare view warns if
  gold changed between two runs being compared.
- **Repeat groups** persist their computed consistency results with the group.

### Why this can't be gamed (design check)

- Leaving slots blank never helps: missing counts fully wrong, and blanks don't
  reduce the beyond-gold watchdog's meaning.
- Filling everything never helps: extras don't raise accuracy, and the watchdog
  trend exposes spray-and-pray immediately.
- Computed totals can't pad the score (excluded), and a correct total can't
  mask wrong components (components are the slots).
- Coverage can't silently self-certify: unavailable inventory reads
  "unavailable", and the caveat is printed on the scorecard.

---

## Scope Boundaries

### In Scope

- **Phase 1 — Gold at scale + consistency:** mTool-as-gold ingestion (with review
  step + scale sanity check), Repeats control + consistency scoring, app-version
  stamping on runs.
- **Phase 2 — Suites + Results:** Suites CRUD, batch runner (estimate, progress,
  partial/resume), health-metric aggregation, notes placement/coverage in the
  scorecard, trend view, side-by-side compare with drill-down, run labels.
- **Word (.docx) documents in suites** — first-class, same as single-run uploads
  (the pipeline already converts Word to PDF at the door; suites inherit this
  unchanged).
- **Access:** the Evals area is open to all signed-in users (no admin gate). Note:
  today's benchmark endpoints are admin-only — they will be relaxed to
  all-authenticated as part of this work (a deliberate, small change to existing
  behaviour).

### Out of Scope (deliberately, for now)

- **AI judge vs. the PDF** (an LLM re-reading the source to grade without gold):
  Phase 3 at the earliest — it's the most expensive signal and adds its own error;
  we add it only if the cheaper signals prove insufficient.
- **Notes prose-fidelity scoring** (is the wording faithful?): needs AI-judge-style
  machinery; Phase 3.
- **Grading numeric values inside notes**: notes values live in a different storage
  than face-statement facts, so this is not the free win it sounds like; deferred.
- **Scheduled/automatic suite runs** (nightly evals): manual trigger only in v1.
- **Any change to extraction itself.** Evals observe; they never alter pipeline
  behaviour.
- **CI integration** (blocking a deploy on eval scores): the human reads the
  dashboard and decides.

### Known Limitations (v1)

- **Suite runs cost real money and hours.** The estimate + progress + resume make
  this manageable, not cheap. 30 docs × 3 repeats ≈ 90 extraction runs.
- **mTool ingest inherits strict label matching:** a human file using off-template
  wording will surface unmatched rows in the ingest report for manual gold-editor
  entry, not silently fuzzy-match (consistent with the mTool pipeline's "strict is a
  feature" stance).
- **Consistency needs ≥ 2 finished repeats**, and measures stability, not
  correctness — all repeats can agree on a wrong answer.
- **Gold quality is human quality.** The review step mitigates; it doesn't
  guarantee.
- **Trends are only as comparable as the corpus is stable.** Adding documents to a
  suite mid-history makes older points cover fewer docs; the UI must label this
  (compare view already handles it explicitly).

---

## Decisions (2026-07-10 review)

| # | Question | Decision |
|---|---|---|
| 1 | History clutter from suite child runs | Suite children hidden from History by default, visible via a toggle. (Note: a suite run only produces docs × repeats runs; repeats default to 1, so a 30-doc suite = 30 runs — the "90" figure was the 3-repeat worst case.) |
| 2 | Suite concurrency | **3 documents in parallel**, fixed. |
| 3 | App-version source | **Confirmed** — stamp every run with the app version. Git commit on dev; deployment carries a build-time version file. |
| 4 | mTool figure scale | **User declares the unit at upload** (mandatory field); the declared unit is authoritative. An anomaly warning remains as a backstop against a wrong declaration. |
| 5 | Chart rendering | **Chart library (Recharts).** Chart inventory fixed in Technical Approach — trend line + compare delta bars (Phase 2), heatmap nice-to-have, cost trend Phase 3. |
| 6 | Access control | **No separation for now** — Evals open to all signed-in users; existing admin-only benchmark endpoints relax to all-authenticated. |
| 7 | Scoring of beyond-gold values | **Revisited in full** — see the Scoring Design section: accuracy stays the headline; failure taxonomy added (scale/sign/period-swap/scope-swap/misplaced/false-not-disclosed/unaddressed); beyond-gold is a trended watchdog, never a headline penalty; consistency split into presence vs value disagreement with a systematic-vs-stochastic cross against gold. |
| 8 | Word files in suites | **Yes, first-class** — suites accept .docx exactly like single-run uploads; pipeline identical to normal extraction. |

## Open Questions

1. **Scoring Design sign-off.** Two defaults were chosen with rationale and can
   be overridden:
   - Suite headline = simple average of per-document accuracy (each filing
     counts equally), with the pooled slot-level figure secondary.
   - Consistency requires *unanimous* agreement across repeats (strictest,
     simplest to explain); majority-agreement can be added later if repeat
     counts grow.

---

## Phasing Summary

| Phase | Delivers | User value unlocked |
|-------|----------|--------------------|
| 1 | mTool gold ingestion · Repeats + consistency score · app-version stamp | Gold library grows from files you already have; flakiness becomes measurable today |
| 2 | Suites · batch runner (estimate/progress/resume) · health aggregation · notes coverage in scorecard · trends + compare | "Are we better?" answered in one screen across the whole corpus |
| 3 *(not committed)* | AI judge · notes prose fidelity · numeric-notes grading · scheduled runs | Deeper signals, if Phase 1–2 metrics prove insufficient |
