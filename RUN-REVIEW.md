# Run Review 

**Purpose of this document.** This is a handoff brief to another AI coding agent. It records
what a prior reviewer observed in a single concrete run of the XBRL Agent pipeline, the
root causes it traced each observation to, and a prioritised list of improvements that
should be implemented without compromising existing guardrails. The reader has not seen
the prior conversation — everything needed is inline.

> Repo context lives in `CLAUDE.md` at the repo root. Pay particular attention to the
> "Load-Bearing Invariants (Gotchas)" section — especially #4 (template-row offset
> illusion), #6 (per-turn token zeros), #12 (Company vs Group column layout),
> #13 (page hints are soft), #16 (notes cells are HTML), and #17 (abstract-row guard +
> no-residual-plug rule). Do not weaken those defences while acting on this report.

## 0. Portability note — this report is self-contained

The investigation that produced this report was done on a Windows workstation with
access to the source AFS PDF, the filer's SSM-submitted Excel, and the sqlite
`output/xbrl_agent.db`. **You do not have those artefacts.** Everything load-bearing
from them has been lifted into this markdown: the exact cross-check numbers, the
CORRECTION error string, the residual-plug evidence quote, the row-by-row
classification divergences, and the per-agent efficiency snapshot. Treat §3 as
ground-truth observations, not as claims you need to re-verify against the
original run.

Your workflow is:

1. Read §3 as given evidence.
2. Build the synthetic fixtures in §6 — those are the regression tests your fixes
   must satisfy.
3. Implement §4 in priority order, running the test suite after each change.

You will not be able to re-run the full pipeline against the original PDF. That's
fine — none of the P0/P1 items require it. They are all reachable via synthetic
fixtures plus the existing test harness. The original outputs are summarised in
§3 to the level of detail needed to act.

---

## 1. Run under review

| Field | Value |
|---|---|
| PDF source | Amway Malaysia Sdn Bhd, Audited Financial Statements FY2024 (MFRS). 65 pages; standard Malaysian AFS format (cover → directors' statements → face statements pp.14–17 → notes 1–29 pp.19–65). |
| Standard / Level | MFRS / Company |
| Statements | SOFP (CuNonCu), SOPL (Function), SOCI (NetOfTax), SOCF (Indirect), SOCIE (Default) |
| Notes templates | CORP_INFO, ACC_POLICIES, LIST_OF_NOTES, ISSUED_CAPITAL, RELATED_PARTY |
| Models | `openai.gpt-5.4` (main + ACC_POLICIES + LIST_OF_NOTES), `openai.gpt-5.4-mini` (CORP_INFO / ISSUED_CAPITAL / RELATED_PARTY), `vertex_ai.gemini-3-flash-preview` (CORRECTION + NOTES_VALIDATOR) |
| Scout | Enabled; face_pages SOFP=15, SOPL=14, SOCI=14, SOCF=17, SOCIE=16; variants all HIGH confidence and correct |
| Wall-clock | 22 min 29 s |
| Final status | `completed_with_errors` |

The ground-truth reference used during the investigation was the filer's own
SSM MBRS submission for the same entity and period. You don't have that file,
and you don't need it — the specific row/value divergences that matter are
enumerated in §3.3.

---

## 2. Executive summary

- **Face-statement accuracy is high.** SOPL has 0 labeled-row mismatches; SOCI has 1
  minor label alignment issue; SOCF reconciles; SOCIE reconciles; SOFP-CuNonCu matches
  except for the balance discrepancy below. Cross-checks `sopl→socie`, `soci→socie`,
  `socie→sofp equity` and `socf→sofp cash` all pass at ±0.
- **SOFP out of balance by RM 5,795** — the only failing numerical cross-check. Traced
  to (a) a double-classification of the restoration provision, and (b) divergent
  row-picking on a few current-liability leaves where AI and filer used different
  SSM rows for the same AFS amount.
- **The CORRECTION agent failed silently by hitting the 50-turn request cap**
  (`error: "The next request would exceed the request_limit of 50"`). The safety net
  that is supposed to close such gaps never closed this one.
- **Notes cells contain HTML content; the filer's submission contains the literal token
  `[Text block added]`** and stores real narrative in hidden `+FootnoteTextsN` sheets.
  Almost certainly a blocker for downstream SSM ingestion.
- **Efficiency is fine** — 22 min for 10 agents, 77–82 % prompt-cache read ratios on
  the heavy agents, single-digit tool-error count across all traces.

Detailed evidence for each of these claims is in §3. Prioritised recommendations with
file/line pointers are in §4.

---

## 3. Detailed findings

### 3.1 Where the run did well

| Area | Evidence |
|---|---|
| Scout infopack | `run_config_json.infopack.statements` has correct face pages (SOPL=14, SOFP=15, SOCIE=16, SOCF=17) with HIGH confidence, correct variant hints for all 5 statements, and a 29-entry `notes_inventory` matching the AFS table of contents. |
| PDF coverage | SOFP agent viewed 20 pages (15, 41–59); SOCF viewed 14 pages (17–18, 38–43, 49–50, 55–59); ACC_POLICIES viewed 19 pages (19–37) end-to-end. No page-budget starvation. |
| Evidence discipline | Every `fill_workbook` payload carries page citations in the `evidence` field (e.g. `"Pages 43-44, Note 13 PPE net carrying amount: buildings RM23,345 in 2024"`). Auditable. |
| Cross-statement reconciliation | 5 of 6 cross-checks pass at ±0 — see §3.2 for the detail. |
| SOPL / SOCI / SOCIE | 0 numerical mismatches between AI and filer on face rows that both populated. |
| Prompt cache discipline | Cache-read ratios from the trace `usage` blocks: SOFP 77 %, SOCF 81 %, ACC_POLICIES 69 %. |

### 3.2 Cross-check results (from `cross_checks` table)

| check_name | status | expected | actual | diff | message |
|---|---|---|---|---|---|
| `sofp_balance` | **failed** | 432 035 | 437 830 | **5 795** | Company CY: assets < equity+liab |
| `sopl_to_socie_profit` | passed | 100 816 | 100 816 | 0.00 | — |
| `soci_to_socie_tci` | passed | 100 816 | 100 816 | 0.00 | — |
| `socie_to_sofp_equity` | passed | 215 110 | 215 110 | 0.00 | — |
| `socf_to_sofp_cash` | passed | 124 765 | 124 765 | 0.00 | — |
| `sore_to_sofp_retained_earnings` | n/a | — | — | — | MPERS-only; this run is MFRS |

### 3.3 The SOFP imbalance — root cause walkthrough

A naive diff of `SOFP-Sub-CuNonCu` against the filer's file looks catastrophic
(hundreds of "differences"). Almost all of that is noise from two sources:

1. **20-row header offset.** The SSM MBRS tool prepends 20 rows of XBRL scaffolding
   (`#LAYOUTSCSR#`, `#STDTENDTDATE#`, concept URIs in col A, …) before the actual
   data region. Once aligned on "Issued capital" (AI row 199 = HU row 219), leaf
   values line up one-to-one — e.g. `Long term leasehold land` 12 420 / 12 688,
   `Building on long term leasehold land` 23 345 / 23 978, `Office equipment,
   fixtures` 16 958 / 9 906, `Prepayments` 2 914 / 1 110, `Trade payables` 14 831 /
   12 566, `Accruals` 102 488 / 158 337, `Capital from ordinary shares` 81 804 /
   81 804 all agree.

2. **Uncomputed formulas.** ~60 `*Total …` rows in the AI workbook are Excel
   formulas. The workbook was never opened in Excel, so `openpyxl(data_only=True)`
   returns `None` for every one of them. They are not missing — they are uncached.
   `compare_results.py`-style row diffs will false-flag them as "missing" until a
   recalc is forced. (This is gotcha #4 in CLAUDE.md.)

After stripping those two sources, the **real differences** on SOFP-Sub-CuNonCu are:

**A. Inventory mis-classification (CY 159 389, PY 120 685).**
AI populated row 135 "Other inventories"; filer populated row 133 "Finished goods".
AFS Note 17 calls the balance "Consumer products: at cost / at NRV" which is finished
goods. Filer is correct.

**B. Subsidiary receivable 95 / 276 placed under "Other" vs "Trade".**
AI: row 152 `Other receivables due from subsidiaries`.
Filer: row 142 `Trade receivables due from subsidiaries`.
AFS Note 18 splits trade receivables as "Third parties 22 060 + Due from a subsidiary",
making the subsidiary amount trade. Filer is correct. Offsetting entry on row 166
`Other current non-trade receivables` (AI 217 / 5 732 vs filer 312 / 6 008) restores
the aggregate — zero net value impact, but an XBRL concept error.

**C. Warranty / refunds provisions broken out (AI) vs lumped (filer).**
AI filled row 394 `Warranty provision` 4 556 / 5 726 and row 397 `Refunds provision`
627 / 450 from AFS Note 23(e). Filer left both rows blank and folded the amounts
into row 434 `Other current non-trade payables` (filer CY 15 126, AI CY 9 943;
diff 5 183 = 4 556 + 627). **The AI's split follows the AFS disclosure structure
and is arguably more correct XBRL-wise, but it shifts the distribution of totals
between SSM rows.** This is the main driver of the 5 795 discrepancy: the
face-level totals still balance (provisions `*Total` now holds 5 183 instead of 0),
but the `sofp_balance` cross-check as currently implemented evidently reads only
a subset of current-liability rows. Confirm by inspecting `tools/verifier.py`
and `server.py`'s cross-check evaluator.

**D. Restoration provision double-booked in PY.**
AI wrote PY 1 881 on row 287 `Provision for decommissioning, restoration and
rehabilitation costs` **and** on row 318 `Other non-current non-trade payables`.
Filer only populated row 318. This double-counts 1 881 into PY non-current
liabilities. CY has the same amount (2 761) only on row 318 in both, so this
specific bug doesn't drive the CY 5 795 diff — but it is a clean, fixable
bug and would materialise in PY cross-checks if the verifier ran the prior-year
column.

**E. A sub-sheet residual plug that violates gotcha #17.**
In `SOFP_conversation_trace.json` the agent's evidence string for `Other property,
plant and equipment` CY 9 525 literally says: *"Amount comprises leasehold
fixtures and improvements RM3,882 + motor vehicles RM56 + capital work-in-progress
RM2,275 + untemplated excess from furniture fixtures and equipment after classing
to office equipment/computer hardware not separately available. **Total row needed
to match face PPE RM64,579.**"* This is a textbook residual plug. The sub-sheet
has dedicated rows for `Construction in progress` (r37) and `Motor vehicles` (r24)
which the agent then double-counted by lumping them here as well. The current
no-residual-plug defence in `prompts/_base.md`, `prompts/sopl.md`, and
`prompts/correction.md` is scoped at SOPL and the verifier — it doesn't cover
SOFP-Sub PPE detail.

### 3.4 CORRECTION agent — the safety net that didn't deploy

The CORRECTION agent (model `vertex_ai.gemini-3-flash-preview`) exited with
`status=failed`. Its event trail shows ~40 consecutive `inspect_workbook`
calls interleaved with three `fill_workbook` + `verify_totals` cycles that
never converged, then:

```
error    | {"message": "The next request would exceed the request_limit of 50",
            "agent_id": "CORRECTION", "agent_role": "CORRECTION"}
complete | {"success": false, "error": "The next request would exceed the request_limit of 50", …}
```

Symptoms:
- The prompt is leading the model to *re-inspect* the workbook after each partial
  write instead of committing a planned set of edits and re-verifying once.
- Gemini 3 Flash is demonstrably struggling with the reasoning load for this
  particular class of bug (classification reconciliation across sub-sheet leaves).
- The 50-turn cap is a blunt instrument — it fires silently, without surfacing the
  underlying run as "needs human follow-up" in the UI beyond the generic
  `completed_with_errors` status.

### 3.5 Notes cell format vs SSM MBRS

All five notes agents wrote rich HTML directly into column B of the notes sheets.
The filer's file, however, carries the literal string `[Text block added]` in
those same cells and stores the real narrative in hidden `+FootnoteTextsN` sheets
(`+FootnoteTexts0` … `+FootnoteTexts4`, `+Elements`, `+Lineitems`). Notes content
in the AI workbook **is semantically correct** (verified on Notes-SummaryofAccPol
against AFS pp. 19–36) — but it is in the wrong place for SSM to validate.

This is likely the single biggest ingestion blocker, because the SSM validator
expects the `+FootnoteTextsN` sheet to carry the text and the visible cell to
carry only the placeholder.

### 3.6 SOCF sign convention divergences (ambiguous, not definitively wrong)

- `(Gain) loss on disposal of PPE`: AI = -70, filer = 70.
- `Cash payments for the principal portion of lease liabilities`: AI = 3 732,
  filer = -3 732.

Both are valid interpretations of the template row labels. Which one is
"correct" depends on how the MBRS template's sum formulas are wired — if the
formula adds the cell (i.e. the cell is an outflow magnitude), positive is
expected; if the formula already negates, negative is expected. Fix is to
pin the convention from the live template formula bar, not from model priors.

### 3.7 Minor data quality items (non-blocking)

- SOFP r28 `Current tax assets` PY blank vs filer 0 — harmless.
- Notes-RelatedPartytran: filer populated only one period column on several
  rows; AI populated both. Could be a filer choice (filing the first year
  only) rather than an AI error. Worth confirming with the filer.
- Face `Trade receivables` (r29 → Sub r139): AI 21 030 / 35 674 (gross),
  filer 20 935 / 35 395 (net of subsidiary). Same total once the subsidiary
  split is reclassed; net effect of §3.3-B.

### 3.8 Efficiency snapshot

The numbers below were extracted from the per-agent conversation traces
during the original investigation. They're inline here so you don't need
the traces themselves. (The `run_agents.total_tokens` column was zero
for every row — gotcha #6; the values below come from summing each
agent's usage blocks in its trace JSON.)

| Agent | Turns | Tool calls | Pages viewed | In / Out tokens | Cache-read |
|---|---:|---:|---:|---|---|
| SOFP | 9 | 10 | 20 | 583k / 6k | 447k (77 %) |
| SOCF | 11 | 10 | 14 | 439k / 10k | 357k (81 %) |
| ACC_POLICIES | 6 | 5 | 19 | 269k / 15k | 187k (69 %) |
| SOCIE | 8 | 8 | 2 | 160k / 2k | 120k |
| SOCI | 8 | 7 | 1 | 54k / 1k | 41k |
| SOPL | 7 | 7 | 3 | 110k / 4k | 79k |
| CORP_INFO / ISSUED_CAPITAL / RELATED_PARTY | 5–6 | 4–6 | 6 each | small | fine |
| **CORRECTION** | **40+** | **~45 (`inspect_workbook` flood)** | 2 | — (capped) | — |

The main agents are efficient; CORRECTION is the outlier.

---

## 4. Prioritised recommendations

Roughly ordered by impact / cost-to-fix ratio. Each item names the file or module
the next agent should touch and includes a regression-test hint.

### P0 — blockers for SSM ingestion and correctness

**P0-1. Rewrite the CORRECTION agent loop to be diff-first, not inspect-first.**

- Symptom: hit `request_limit=50` without converging (§3.4).
- File: the module that defines the correction agent (search for `CORRECTION` in
  `extraction/`, `server.py`, or `prompts/correction.md`).
- Fix direction: pass the full failing-check context (expected, actual, diff,
  the AFS note's evidence page, and every face+Sub row feeding the total) into
  a single prompt. Expect the agent to emit **one planned diff** as a
  `fill_workbook` call, then exactly one `verify_totals`. Cap at 5–8 turns.
  Remove the freeform `inspect_workbook` invitation from the prompt.
- Also consider swapping `vertex_ai.gemini-3-flash-preview` for the same family
  as the primary agent (`openai.gpt-5.4`) — Flash is evidently underpowered for
  this class of task. Leave the model as a config override, not a hard pin.
- Surface the `request_limit` outcome to the UI distinctly from
  `completed_with_errors`. A user who sees "completed_with_errors" has no idea
  the corrector silently gave up.
- Test: extend `tests/test_server_run_lifecycle.py` (or add a new file) with a
  synthetic failing cross-check, assert the agent closes within 8 turns and
  exits with a distinct status string (e.g. `correction_exhausted`) when it
  cannot.

**P0-2. Emit SSM-compatible notes cells by default.**

- Symptom: inline HTML where SSM expects `[Text block added]` + a footnote
  sheet (§3.5).
- Files: `notes/persistence.py`, `notes/writer.py`, `notes/html_to_text.py`,
  probably `tools/fill_workbook.py` or a merger step.
- Fix direction: when flattening the DB `notes_cells` rows into the output
  xlsx, write `[Text block added]` into the visible cell and append the real
  content into the corresponding `+FootnoteTextsN` sheet (or create one
  matching the SSM template's naming). Gate behind a config flag
  (e.g. `RunConfig.notes_output_format: "inline" | "ssm"`) with `"ssm"` as
  default. Keep the DB `notes_cells` row canonical (gotcha #16) — this is
  purely a render-time decision.
- Do **not** truncate or re-sanitise — the canonical HTML lives in
  `notes_cells` and stays there for the editor UI.
- Test: add `tests/test_notes_ssm_output_format.py` that opens the merged
  xlsx and asserts (a) the visible cell is `[Text block added]`, (b) the
  `+FootnoteTextsN` sheet contains the rendered content, (c) the DB row is
  unchanged.

**P0-3. Force Excel recalc at merge time so `*Total …` formulas carry cached
values.**

- Symptom: 60+ false positives in any reviewer diff because openpyxl cannot
  evaluate formulas (§3.3, introduction).
- File: `workbook_merger` / `server.py` merge step.
- Fix direction: after the per-agent files are merged into `filled.xlsx`,
  invoke LibreOffice headless (`soffice --headless --calc --convert-to xlsx …`)
  or `xlcalculator`/`formulas` library to force evaluation and write back
  cached values. On Windows, LibreOffice may not be available — in that case
  fall back to pure-Python evaluation (`formulas` package) for the SOFP / SOCF
  roll-ups specifically.
- Test: add `tests/test_merge_formula_recalc.py` that fills a known template,
  merges, re-opens with `data_only=True`, and asserts a sample `*Total …` cell
  returns the expected numeric value, not `None`.

### P1 — agent accuracy improvements

**P1-1. Guard against classification double-booking.**

- Symptom: restoration provision PY 1 881 written to two non-current rows (§3.3-D).
- File: `tools/fill_workbook.py`.
- Fix direction: after each batch of writes, if two rows on the same sheet
  within the same section (`non-current liabilities`, `current liabilities`,
  `non-current assets`, `current assets`) hold the exact same RM value and
  share evidence-string overlap, emit a `WARNING` back to the agent and
  request it resolve before continuing. Deterministic, no prompt changes.
- Test: `tests/test_fill_workbook_double_booking_guard.py`.

**P1-2. Extend the no-residual-plug guard to SOFP-Sub PPE detail.**

- Symptom: gotcha #17 violated on `Other property, plant and equipment`
  (§3.3-E).
- Files: `prompts/_base.md` (or a new `prompts/sofp_sub.md` overlay), plus
  the abstract-row guard area in `tools/fill_workbook.py`.
- Fix direction: add an explicit rule to the SOFP (or SOFP-Sub) prompt:
  *"Do not populate any row whose label begins with 'Other …' on the PPE,
  intangibles, or investments sub-blocks in order to match a face-sheet total.
  Every PPE component must map to its own dedicated row (Motor vehicles,
  Construction in progress, Office equipment, etc.); any truly unmappable
  residual must stay empty and be reported as a reconciliation gap."*
- Test: pin with a prompt-assertion test analogous to
  `tests/test_prompt_residual_plug_rule.py`.

**P1-3. Feed the agent an AFS-concept → SSM-row mapping for the common
cases.**

- Symptom: "finished goods" vs "other inventories" and the subsidiary-trade
  misclassification (§3.3-A, §3.3-B).
- File: `prompts/sofp.md` or a new reference block loaded by
  `extraction/agent.py::_summarize_template`.
- Fix direction: a 15–20 entry cheat-sheet embedded in the SOFP system prompt
  listing AFS phrasings ↔ SSM row labels that are known to confuse models
  (Consumer products → Finished goods; Trade receivables due from subsidiary →
  Trade receivables due from subsidiaries, not Other receivables; etc.).
  Keep it terse to preserve the prompt cache.
- Test: `tests/test_prompt_concept_mapping.py` asserting the cheat-sheet is
  present in the rendered SOFP prompt.

**P1-4. Extend the verifier to cross-check sub-sheet leaf sums against AFS
note disclosure totals.**

- Symptom: SOFP imbalance went unnoticed by the agent's own `verify_totals`
  loop (§3.3-C); the agent only saw face totals matching even though the
  distribution across sub rows diverged from the AFS.
- File: `tools/verifier.py`.
- Fix direction: when verifying SOFP, in addition to the current face-sum
  check, pull the AFS-note totals surfaced during extraction (e.g. Note 18
  trade receivables total, Note 23 trade-and-other-payables total) and
  assert each `*Total trade and other …` cell equals that note total within
  tolerance. Requires the agent to push note totals into a small "notes
  context" dict as it extracts, which feeds the verifier.
- Test: extend `tests/test_cross_checks.py` with a SOFP fixture where the
  face balances but the trade-payables sub-sheet is short; assert the new
  check fires.

### P2 — quality-of-life / reviewer tooling

**P2-1. Label-plus-section disambiguation in `compare_results.py`.**

- Symptom: two rows literally labelled "Lease liabilities" (current and
  non-current) collide under label-only keys; any naive diff tool will
  misreport them as "changed" when they are in fact both correct.
- File: `compare_results.py`.
- Fix direction: walk the sheet and tag each data row with the nearest
  preceding section header ("Non-current liabilities", "Current
  liabilities", …). Key the diff on `(section, label)`, not `label`.
- Test: add a fixture with the duplicated label and assert the new diff
  keys don't collide.

**P2-2. Pin SOCF sign convention from the template formula bar.**

- Symptom: `(Gain) loss on disposal of PPE` and `Cash payments for the
  principal portion of lease liabilities` sign divergences (§3.6).
- File: `prompts/socf.md`.
- Fix direction: walk `XBRL-template-MFRS/Company/04-SOCF-Indirect.xlsx`
  (and its MPERS twin) at prompt-build time, read the formula in each
  `*Total …` cell, and surface to the agent prompt a per-row signed
  convention line (e.g. *"Row 38 `(Gain) loss on disposal of PPE`: sum
  formula adds this cell → enter POSITIVE magnitude; a gain is negative,
  a loss is positive."*). The same pattern you already used for SOCIE
  dividend sign in ADR-002.
- Test: `tests/test_socf_sign_convention.py` pinning the generated prompt
  against a fixture.

**P2-3. Backfill per-agent token/cost in the DB.**

- Symptom: `run_agents.total_tokens = 0` for every row (gotcha #6).
- Files: `server.py` completion handler (the place that already backfills
  the overall `runs` totals from `result.usage`).
- Fix direction: at `_finish_agent` (or equivalent), pull per-turn usage
  from the `AgentRunResult.all_messages()` stream, sum, and write into
  `run_agents.total_tokens` + `total_cost`. Pricing lookup lives in
  `pricing.py` already.
- Test: `tests/test_agent_token_backfill.py` using a mocked
  `AgentRunResult` with a known usage trail.

---

## 5. Out of scope / non-goals

- **Do not** soften the abstract-row guard (gotcha #17) or the no-residual-plug
  prompts. P1-2 extends them; it must not weaken them.
- **Do not** re-introduce `allowed_pages` filtering on scout hints (gotcha #13).
- **Do not** introduce deterministic label-matching in the notes pipeline
  (CLAUDE.md "How to work here"). P1-3's concept mapping is for SOFP-face
  specifically and lives in the extraction prompt, not in the notes writer.
- **Do not** hand-edit template formulas (gotcha #3). If P0-3 surfaces any
  formula drift, regenerate from the SSM linkbase via
  `scripts/generate_mpers_templates.py --snapshot` (MPERS) or via the
  corresponding MFRS pipeline.

---

## 6. Test fixtures you must build first (your regression tests)

You do not have the original AFS PDF, the filer's Excel, or the sqlite
`output/xbrl_agent.db`. **Do not try to obtain them.** Instead, build the
fixtures below — each encodes the failure mode the corresponding §4 fix
must eliminate. Put them under `tests/fixtures/run_review/` (or wherever
the repo's test fixture convention dictates — grep `tests/` for
`fixtures` before creating a new path).

Every fixture is LLM-free: synthetic workbooks and mocked agent events
are enough. None of the P0/P1 work requires a live LLM call.

### 6.1 Fixture: SOFP imbalance from classification split (for P1-4)

- **Input:** A copy of `XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx`
  pre-filled with the following leaves (numbers below are the actual ones
  from the reviewed run):

  SOFP-Sub-CuNonCu
  - `Long term leasehold land` (CY / PY): 12 420 / 12 688
  - `Building on long term leasehold land`: 23 345 / 23 978
  - `Motor vehicles`: 56 / blank
  - `Office equipment, fixture and fittings`: 16 958 / 9 906
  - `Construction in progress/Asset work-in progress`: 2 275 / blank
  - `Other property, plant and equipment`: 9 525 / 9 592
  - `Computer software`: 2 398 / 849
  - `Unquoted shares, net of impairment losses`: 7 648 / 7 648
  - `Other inventories`: 159 389 / 120 685  ← **intentionally wrong row**
  - `Trade receivables`: 21 030 / 35 674
  - `Other receivables due from subsidiaries`: 95 / 276  ← **intentionally wrong row**
  - `Other receivables due from other related parties`: 259 / 241
  - `Prepayments`: 2 914 / 1 110
  - `Deposits`: 1 795 / 1 737
  - `Other current non-trade receivables`: 217 / 5 732
  - `Balances with Licensed Banks`: 61 668 / 81 920
  - `Deposits placed with licensed banks`: 63 097 / 156 767
  - `Capital from ordinary shares`: 81 804 / 81 804
  - `Provision for decommissioning, restoration and rehabilitation costs`: 0 / **1 881**  ← double-booked
  - `Other non-current non-trade payables`: 2 761 / 1 881  ← also holds the 1 881
...