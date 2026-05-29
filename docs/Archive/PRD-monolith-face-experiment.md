# PRD — Monolith Face-Statement Agent (Experiment)

**Status:** Draft / pre-spike
**Owner:** TBD
**Target outcome:** decision in ~2 weeks: monolith path replaces / augments / does not replace the split face-statement pipeline.

---

## 1. Problem

The current pipeline runs five specialist agents in parallel (one per face
statement: SOFP, SOPL, SOCI, SOCF, SOCIE), merges, runs cross-checks, then
invokes a correction agent if any check fails.

**The recurring failure mode:** each specialist agent only sees its own
statement. Cross-statement identities (SOFP cash = SOCF closing cash; SOPL
profit feeds SOCIE current-year profit; SOFP retained earnings = SOCIE ending
retained earnings; etc.) break in ~30% of complex real-world filings.
Correction then has to re-trace evidence across two or three statements with
no shared working memory between them.

**Hypothesis:** a single agent that sees all five face templates + the full
PDF + a tight observe-write-reconcile feedback loop will produce a higher
cross-check **pre-accept pass rate** (i.e. the first call to `done()`)
than the current split pipeline's **first-pass pass rate** (i.e.
immediately after the 5 specialist agents merge, before correction
runs). Both compare honest first-attempt agent capability. See §2b for
the four-stage measurement that captures this rigorously.

The point of this PRD is the **experiment**, not a migration. We want a clean
side-by-side comparison before committing to anything.

---

## 2. Goal & success metrics

Run both pipelines on a fixed test set (FINCO + 2–4 other audited filings,
N=3 trials per PDF per pipeline — see §2b reproducibility protocol) and
decide on these metrics.

### 2a. Cross-check outcome — tri-state, not binary

Every cross-check resolves to exactly one of three states per run:

- **`passed`** — check evaluates true on the live workbook.
- **`accepted_residual`** — check fails numerically, but agent declared
  it in `done({accept_imbalance: [...]})` with PDF-page-grounded
  evidence the server could verify (see §6 `done` contract).
- **`failed`** — check fails and was not accepted.

This is the key methodological lock against metric-gaming: an agent that
"wins" by mass-accepting residuals registers as accepted_residual, not
passed. The acceptance gate (next subsection) compares `passed` and
`failed` directly; accepted_residual is reported alongside but never
counted as a win.

### 2b. The four numbers we score on

The hypothesis (§1) is about **first-pass** behaviour. Comparing
split-post-correction vs. monolith-final hides that. Capture all four:

| Stage | What it measures |
|---|---|
| **Split first-pass** | Cross-checks evaluated *immediately after the 5 specialist agents merge*, before the correction agent runs. The direct comparable to monolith pre-accept. |
| **Split post-correction** | Cross-checks evaluated after the correction-agent pass completes (current production "final" state). |
| **Monolith pre-accept** | Cross-checks evaluated when the agent first calls `done()`, before any `accept_imbalance` is honoured. The honest read of agent capability. |
| **Monolith final** | After honouring `accept_imbalance`. The shipping-equivalent state. |

### 2c. Acceptance criteria

| Metric | Primary signal | Target |
|---|---|---|
| Cross-checks **passed** at monolith pre-accept | Quality (the hypothesis) | ≥ split first-pass `passed` rate |
| Cross-checks **failed** at monolith final | Quality (shipping floor) | ≤ split post-correction `failed` rate |
| Cross-checks **accepted_residual** at monolith final | Honest sign-off rate | Reported, not gated. Reviewed manually; flag if > 20% on any PDF. |
| Cell-level accuracy vs. FINCO reference (FINCO only) | Quality | Monolith ≥ split – 2% |
| **Evidence-sampled cell accuracy on every non-FINCO PDF** (N=20 cells per PDF, sampled with bias toward cells touched to resolve cross-checks; manual verification against the PDF) | Quality — catches wrong-but-consistent workbooks | Monolith accuracy ≥ split accuracy |
| Wall-clock per run | Cost | Monolith ≤ 2× split |
| Tokens billed per run (input + output, post-cache) | Cost | Monolith ≤ 2× split |
| Turns consumed | Convergence | Monolith ≤ 60 turns on FINCO |

**Acceptance:** monolith ties or beats split on **both** the
passed-pre-accept rate *and* the evidence-sampled accuracy, with cost
within 2× and accepted_residual not flagged on any PDF. Loses on
quality → ship the `get_state` tool into the split pipeline as a
smaller win and walk away.

### 2d. Why evidence-sampling is non-negotiable for non-FINCO PDFs

An agent satisfying SOFP-cash = SOCF-cash by moving the *wrong* value
to both sides will pass every cross-check identity in §2a — and Phase
1 with cell-accuracy only against FINCO would miss it entirely.
Sampling 20 random cells per non-FINCO PDF (biased toward the cells
the agent touched to resolve cross-checks) catches this failure mode
at ~95% confidence for a 10%+ defect rate; sufficient for a go/no-go
decision without requiring a full reference workbook for every
filing.

### 2e. Reproducibility protocol

LLM pipelines are stochastic. A single run per PDF on either pipeline
proves nothing. The protocol below makes results reproducible enough
to support a decision:

- **N = 3 trials per (PDF × pipeline)**. Report median + min/max
  spread per metric. Disqualify any metric where intra-pipeline spread
  exceeds the inter-pipeline gap.
- **Pin all stochastic inputs:**
  - Model + exact version string (e.g. `claude-opus-4-7-20251201`).
  - Temperature = 1.0 (Gemini 3 hard constraint per
    `CLAUDE.md`; same value used for any other model in the
    experiment so the comparison stays apples-to-apples).
  - Proxy route — direct vs. LiteLLM vs. Windows enterprise proxy —
    pinned per trial-set, never mixed within a comparison.
- **Snapshot derived artefacts:** Before any trial, capture and reuse:
  - Scout output (`scout_output.json`) → identical face/note page
    hints feed both pipelines.
  - PyMuPDF text extraction → identical text seed for both pipelines'
    in-prompt or in-context PDF content.
  Store under `experiment_artifacts/{pdf_hash}/` and reference by hash
  in the comparison-script CSV.
- **Per-trial logging:** every trial records exact model invocation,
  prompt-cache hit ratio, retry counts, and any structured exhaustion
  outcome (`iteration_exhausted`, `wallclock_exhausted`,
  `correction_exhausted`) so unstable runs are visible, not averaged
  away.
- **Excluded from averaging:** any trial that failed for a reason
  unrelated to agent capability (proxy 5xx, OOM, etc.). Report these
  separately as **pipeline-environment failures**; they're a real
  signal but not a quality signal.

**Cost of the protocol.** 5 PDFs × 2 pipelines × 3 trials = 30 runs.
At an estimated 10–15 minutes per monolith run and 5–8 per split, the
full grid is ~6–8 hours of operator time on the experiment day, most
of it unattended. Acceptable.

---

## 3. Scope

**In:**
- Five face statements only: SOFP, SOPL, SOCI, SOCF, SOCIE
- **MFRS, Company** filing standard / level (simplest variant; 4-column templates)
- One model, chosen for long-context + caching strength (recommend Claude Opus 4.7 1M; fallback GPT-5.4)
- **CLI flag** `--orchestration {monolith|split}` on `run.py`
- **Web UI toggle** on the run-config page so the experiment is runnable on Windows against real client PDFs (operator-visible; labelled "Experimental: single-agent monolith")
- Reuse: `tools.fill_workbook.write_cells`, `tools.verifier.verify_totals`, `cross_checks.run_all`, `agent_tracing`, the `runs` audit DB, `scout`
- A comparison script `scripts/compare_orchestration.py`
- **Windows-first validation.** The experiment runs primarily on Windows against larger client PDFs (≥ FINCO scale), so every Windows-specific invariant (UTF-8 codec, truststore SSL, Node PATH discovery — gotchas #1, #5, #8) is in-scope from day one.

**Out (Phase 2 if Phase 1 wins):**
- Notes templates (sheets 10–14 / 11–15) — different scale problem, bigger context
- MPERS — different label conventions and sign rules (see gotcha #15)
- Group filings — 6-column layout + dual cross-check passes
- Replacing the correction agent
- Toggle exposed to *end users* (it stays operator-facing — labelled "Experimental" and not the default — until the experiment concludes)

---

## 4. Agent workflow

A tight observe → act → reconcile loop:

```
turn 1   ── system prompt loaded (cached): PDF text + 5 templates + rules
         ── get_state()                     → all sheets blank, all checks fail vacuously
         ── plan internally
         ── view_pdf_pages(face_pages)      → if scout didn't already capture
         ── write_cells([…cross-sheet batch…])

turn 2   ── get_state()                     → filled cells visible; verifier + cross-checks computed
         ── identify failing checks + diffs
         ── view_pdf_pages(…)               → if a value needs tracing
         ── write_cells([…fixes…])

turn N   ── get_state() shows all ✓         → done({})

OR

turn N   ── get_state() shows residual ✗    → done({accept_imbalance: [...]})   ← forced honest sign-off
```

Convergence: all cross-checks + verifier pass → `done({})`; or agent calls
`done` with an `accept_imbalance` list whose entries carry
**page-grounded evidence** that the server validates against the actual
PDF (see §6 `done` contract); or hit iteration / wall-clock cap →
coordinator force-finalises.

---

## 5. The status tool (the heart of the design)

`get_state()` is the agent's only window into "what's done and what's
broken". Everything else hangs off this returning a useful, compact view.

**Returns:**

```jsonc
{
  "filing": {"standard": "mfrs", "level": "company"},
  "turn": 7,
  "sheets": {
    "SOFP": {
      "filled": 18, "writable": 24,
      // Each cell carries `cy` (col B), `py` (col C), and `evidence` (col D);
      // SOCIE rows carry a `matrix_cols` map keyed by equity-component label
      // (resolved from `concept_nodes.matrix_col`). See §6 write_cells contract.
      "rows": [
        {"row": 5,  "concept": "Inventories",       "label": "Inventories",
         "cy": null, "py": null, "evidence": null, "kind": "leaf"},
        {"row": 6,  "concept": "TradeReceivables",  "label": "Trade receivables",
         "cy": 12345, "py": 11000, "evidence": "Note 14 (p.42)", "kind": "leaf"},
        {"row": 10, "concept": "CurrentAssets",     "label": "Total current assets",
         "cy": {"formula": "=SUM(B5:B9)", "computed": 50000, "warnings": []},
         "py": {"formula": "=SUM(C5:C9)", "computed": 47500, "warnings": []},
         "kind": "formula"},
        {"row": 15, "concept": "EquityLiabAbstract","label": "EQUITY AND LIABILITIES",
         "cy": null, "py": null, "kind": "abstract"}
      ]
    },
    "SOCIE": {
      "filled": 12, "writable": 30,
      "rows": [
        // Matrix sheet: cell-level values keyed by equity-component column.
        // `evidence_col` is the agent-resolved source column (typically X=24 on MFRS,
        // D/F on MPERS; see _resolve_socie_evidence_col in tools/fill_workbook.py).
        {"row": 8,  "concept": "DividendsRecognised", "label": "Dividends recognised",
         "matrix_cols": {"RetainedEarnings": 5000, "ShareCapital": null, "Reserves": null},
         "evidence": "Note 22 (p.61)", "kind": "matrix_leaf"}
      ]
    },
    "SOPL": {…}, "SOCI": {…}, "SOCF": {…}
  },
  "verifier": [
    {"sheet": "SOFP", "check": "assets_eq_equity_plus_liab",
     "lhs": 50000, "rhs": 49955, "diff": 45, "direction": "assets > equity+liab",
     "pass": false}
  ],
  "cross_checks": [
    {"id": "sofp_to_socie_retained_earnings",
     "lhs_ref": "SOFP!B7 (retained earnings)", "lhs": 12345,
     "rhs_ref": "SOCIE!N48 (ending balance)",   "rhs": 12300,
     "diff": 45, "direction": "SOFP higher by 45",
     "pass": false},
    {"id": "sopl_to_socie_current_year_profit", "pass": true}
  ],
  "history_hints": [
    /* surfaced only if agent has written the same cell same value 3+ times */
    {"sheet": "SOPL", "row": 22, "value": 8500,
     "note": "you've written this same value 3 times — try a different approach"}
  ]
}
```

**Design points worth defending:**

- **Full snapshot every turn.** Token cost is bounded (~15–25 KB per
  call with CY/PY/evidence + computed-formula values; ~125 writable
  rows total across all 5 sheets). Agents are bad at maintaining
  state across long contexts — let the tool be the state.
- **Formula cells return both text and value.** Reuse
  `tools/verifier.py::_resolve_cell_value` (the existing recursive
  formula evaluator with cycle detection). Agent gets
  `{formula: "=SUM(...)", computed: 50000, warnings: [...]}` so it
  can reconcile against numeric targets without re-evaluating
  formulas in its head.
- **Diffs carry direction, not just magnitude.** `"SOFP higher by 45"`
  collapses two follow-up reasoning steps the agent would otherwise need.
- **`lhs_ref` / `rhs_ref` carry sheet!cell coordinates** so the agent knows
  exactly where to look.
- **`history_hints`** prevents the most common long-context failure mode:
  the agent re-applying the same wrong fix in a loop. Implemented
  server-side: track last N writes per (sheet, row), inject a nudge when
  repetition detected.
- **Abstract rows surfaced as `kind: "abstract"`** — agent sees them, can't
  miss them, but the write tool will also reject them with the existing
  guard (gotcha #17 stays intact).

---

## 6. Tools

### `get_state() → StateSnapshot`
As above. No arguments. Server-computed each call from the live workbook
state + a fresh cross-check pass.

### `view_pdf_pages(start_page: int, end_page: int) → vision`
Unchanged from current. Server enforces `1 ≤ page ≤ N`. No `allowed_pages`
restriction (gotcha #13 preserved).

### `write_cells(writes: list[CellWrite]) → WriteResult`

The `CellWrite` schema mirrors `FieldMapping` at
[tools/fill_workbook.py:19](tools/fill_workbook.py:19) — anything
less loses column / evidence / section information the writer needs.

```jsonc
// CellWrite
{
  "sheet": "SOFP",                       // required
  "row": 6,                              // OR omit and use label + section
  "label": "Trade receivables",          // optional row resolver
  "section": "current",                  // disambiguates duplicate labels
                                         //   (e.g. current vs non-current lease liabilities)
  "col": "cy",                           // "cy" (col 2/B) | "py" (col 3/C) | "evidence" (col 4/D)
                                         //   For SOCIE matrix rows: use `matrix_col` instead.
  "matrix_col": "RetainedEarnings",      // SOCIE only — equity-component label resolved
                                         //   via concept_nodes.matrix_col + matrix_col_label
  "value": 12345,                        // number for cy/py/matrix_col; string for evidence
  "evidence": "Note 14 (p.42)"           // recommended on every value write; written to
                                         //   evidence col (D on Company; F on Group;
                                         //   resolved via _resolve_socie_evidence_col for SOCIE)
}
```

**Worked examples.**

```jsonc
// SOFP current-year value with PY in same batch:
[
  {"sheet": "SOFP", "row": 6, "col": "cy", "value": 12345, "evidence": "Note 14 (p.42)"},
  {"sheet": "SOFP", "row": 6, "col": "py", "value": 11000}
]

// SOCIE matrix: dividends row, retained-earnings column only
[
  {"sheet": "SOCIE", "row": 8, "matrix_col": "RetainedEarnings",
   "value": 5000, "evidence": "Note 22 (p.61)"}
]
```

**Server-side validation** (all load-bearing — every rejection comes
back to the agent with a structured reason):

- Reject `col: "cy"`/`"py"` on a matrix sheet (SOCIE) — agent must
  use `matrix_col`.
- Reject `matrix_col` on a non-matrix sheet.
- Reject abstract / header rows (gotcha #17 guard reused verbatim).
- Reject formula-bearing cells (preserves linkbase invariant, gotcha #3).
- Reject row out of range, unknown `matrix_col` label, type mismatch
  (string vs number per `col`).
- Reject duplicate writes to the same `(sheet, row, col-or-matrix_col)`
  within one batch — agent must order its own intent.

Returns:

```jsonc
{
  "written": [{"sheet": "SOFP", "row": 6, "col": "cy", "value": 12345}],
  "rejected": [
    {"write": {...}, "reason": "abstract row — write to row 5 or 7 instead"},
    {"write": {...}, "reason": "matrix_col 'Goodwill' not present on SOCIE; valid: [...]"}
  ]
}
```

### `done(accept_imbalance: list[Accept]=[]) → CompletionResult`

Agent signals completion. If any check is still failing and not in
`accept_imbalance`, the tool returns a `not_done` result naming the
failing checks. Forces honest sign-off; coordinator only finalises on
a truly clean `done` or a `done` with explicit, server-validated
acceptance.

```jsonc
// Accept
{
  "check_id": "sofp_to_socie_retained_earnings",   // must match an emitted check id
  "reason": "RM45 rounding diff; SOFP shows RM12,345 vs SOCIE RM12,300",
  "pdf_page": 42,                                  // 1-indexed; server checks page exists
  "evidence_excerpt": "Retained earnings, RM12,345 (rounded)"  // ≤200 chars
}
```

**Server-side validation** (in this order, all required):

1. `check_id` corresponds to a check that is *currently failing* — you
   cannot accept a passing check or a non-existent one.
2. `pdf_page` exists in the run's PDF (`1 ≤ page ≤ N`).
3. `evidence_excerpt` is non-empty and ≤200 chars.

Any failure returns `not_done` with the offending entry surfaced; the
run is not finalised. This is the lock against the "name failures
instead of fix them" gaming pattern (§2 P0-3 review finding). The
metric system in §2a then counts every server-accepted entry as
`accepted_residual`, never `passed`.

---

## 6a. Monolith artifact model (single agent ↔ five workbooks)

The split pipeline writes one workbook per statement
(`{stmt}_filled.xlsx`) and the existing partial-merge path
(`_attempt_partial_merge` at [server.py:2101](server.py:2101)) walks
those files. Monolith writes **one** workbook and produces **one**
agent trace. The mapping below preserves every downstream contract:

| Subsystem | Split-pipeline shape | Monolith-pipeline shape |
|---|---|---|
| Workbook on disk | 5 × `{stmt}_filled.xlsx` → `_attempt_partial_merge` → `filled.xlsx` | 1 × `monolith_filled.xlsx` (the live workbook). `_attempt_partial_merge` skipped on this path — `mark_run_merged` points directly at it. |
| Snapshot cadence | Each agent saves on `fill_workbook` calls | Save after every successful `write_cells` batch (atomic copy → rename → fsync). Crash/cancel between turns preserves whatever landed; the most recent snapshot is what gets surfaced as the partial result. |
| `run_agents` rows | 5 rows, one per statement | **1 row**, `statement = "monolith"`. Same lifecycle (started/ended/status/turn_count/tool_call_count rollups). |
| `run_agent_turns` rows | 1 stream per statement | 1 stream, joined to the single `run_agents` row. Telemetry tab gets a single-agent view rather than 5 tabs (§7a covers the label change). |
| Conversation trace | 5 × `{stmt}_conversation_trace.json` | 1 × `monolith_conversation_trace.json`. Same JSON schema as today; `save_messages_trace` failure path (gotcha #6) covers it. |
| Cross-checks | Same `cross_checks.run_all` against final workbook | Same call — runs against `monolith_filled.xlsx`. Same SSE event family (gotcha #19), `phase: "monolith"` to disambiguate. |
| Download endpoint | `GET /api/runs/{id}/download/filled` reads `merged_workbook_path` | Unchanged. Pointer set on `mark_run_merged` regardless of path. |
| Cancel mid-run | Cancel → `_attempt_partial_merge` → SSE `partial_merge` event | Cancel → the most recent `monolith_filled.xlsx` snapshot is already on disk; coordinator emits the same `partial_merge` event with `statements_included = [...derived by inspecting which sheets had any non-formula writes...]`. The 2026-04-27 invariant (gotcha #10) stays intact. |
| `run_complete` event | Per-statement summary | Single-agent summary with the four-stage metric block (§2b). |

**Telemetry caveat already documented in gotcha #6.** "The Sheet-12
fan-out leaves per-turn rows empty (its sub-agents merge into one
row) — rollups still populate." The monolith is the *inverse* pattern
— one agent, one row, full per-turn rows. No collision with existing
telemetry semantics.

---

## 7. System prompt — rewritten, not concatenated

**Do not** just glue the five existing per-statement prompts together. The
current prompts carry rules + worked examples + per-statement narrative
guidance. Concatenated, they're ~50 KB of prose, much of it redundant, and
the load-bearing rules get lost.

**Target:** one ~8–12 KB prompt that carries only what the monolith agent
actually needs, plus the PDF text and template structure cached alongside.

### Prompt structure (cached chunk)

```
1. Role + objective                                    (~30 lines)
   - "Fill all 5 face statements from one PDF using observe→write→reconcile."
   - Loop description with explicit guidance on when to call each tool.

2. Filing context                                       (~10 lines)
   - Standard: MFRS  /  Level: Company  /  Currency
   - Reporting period / comparative period from scout.

3. Load-bearing rules (consolidated, deduplicated)      (~80 lines)
   ┌─ Sign conventions: dividends entered POSITIVE on SOCIE (gotcha #15).
   ├─ Abstract rows: read-only; write_cells rejects them.
   ├─ No residual plugs: catch-all rows ("Other …", "Miscellaneous …") are
   │  for genuine coarse disclosures only — never to balance verifier or
   │  cross-checks. If the breakdown won't reconcile, leave the leaf empty
   │  and surface the imbalance via accept_imbalance. (gotcha #17)
   ├─ Cross-statement identities (the agent's main job to satisfy):
   │    SOFP!cash         == SOCF!closing_cash
   │    SOPL!profit       == SOCIE!current_year_profit
   │    SOFP!retained     == SOCIE!ending_retained
   │    SOCI!tci          == SOCIE!tci_row
   │    SOFP!equity_total == SOCIE!ending_equity
   └─ Currency / unit consistency across all 5 sheets.

4. Template structure (cached; static)                  (~60 lines per sheet × 5)
   - For each sheet: an indexed list of writable rows
     (row, concept_id, col-A label, kind, expected_units).
   - Section headings inlined so agent sees structure visually.
   - Cross-reference annotations at the row level, e.g.
     "SOFP!B7 (Retained earnings) ↔ SOCIE!N48 (Ending retained earnings)".
     This is the single biggest unlock — the agent doesn't have to
     re-derive the identity each turn.

5. PDF context (cached; biggest payload)               (~80–150 KB)
   - PyMuPDF-extracted text of the financial-statement section + relevant
     notes (driven by scout's face_page + note_pages output).
   - Page-numbered markers ("=== page 12 ===").
   - The rest of the PDF accessible via view_pdf_pages tool (vision).

6. Workflow contract                                    (~20 lines)
   - "Start with get_state(), then plan."
   - "After every write_cells batch, call get_state() before writing again."
   - "Never call write_cells more than once without an intervening get_state."
   - "Convergence: all checks pass, OR explicit accept_imbalance with PDF-grounded reason."
```

### Per-statement guidance — where it lives now

The split-pipeline prompts (`prompts/sofp.md`, `prompts/sopl.md`,
`prompts/socie.md`, etc.) carry per-statement narrative guidance. The
monolith doesn't get those verbatim. Instead:

- **Distill** the operational rules from each into the consolidated
  rules block (section 3 above). Worked examples → 1-2 inline examples
  in the rules section, focused on the failure modes most likely to bite.
- **Cross-reference annotations** in the template structure section
  (section 4) carry the relationships that previously lived as prose in
  the per-statement prompts.
- **Drop everything else.** The agent has full PDF + full template
  structure; it doesn't need ~150 lines of "how to read SOFP" prose.

### What we deliberately do NOT carry into the monolith prompt
- SOCIE matrix-specific guidance for Group filings → Phase 2.
- MPERS sign / label conventions → Phase 2.
- Notes-pipeline overlap rules → out of scope.
- Group 6-column behaviour → out of scope.

This keeps the prompt focused and the cache hit rate maximal.

---

## 7a. UI toggle (Windows-runnable experiment)

The toggle has to be operator-visible because the primary validation
environment is Windows + real client PDFs — the CLI flag alone won't fit
the operator workflow there.

**Placement.** Sits next to the existing filing-standard (MFRS/MPERS) and
filing-level (Company/Group) controls in
[web/src/components/StatementRunConfig.tsx](web/src/components/StatementRunConfig.tsx).
Same visual treatment as those — radio-group pair with the experimental
option clearly labelled.

```
Orchestration  (●) Split (default)    ( ) Experimental: single-agent monolith
                                            └─ MFRS Company face-only.
                                               Disabled when MPERS, Group, or notes selected.
```

**State + persistence.**
- New `orchestration: "split" | "monolith"` field on the existing
  `RunConfigRequest` (server) and `RunConfig` (frontend). Defaults to
  `"split"`. Persisted in the `runs` row (new column,
  schema migration **v9 → v10** — single nullable TEXT column with
  `'split'` default; idempotent like every other step in
  `db/schema.py`. v9 is already in main (SOCIE matrix labels,
  `concept_nodes.matrix_col_label` — see [db/schema.py:49](db/schema.py:49)),
  so this PRD's column lands on top as v10.
- Visible on the History page row + Overview tab so operators can tell
  at a glance which path produced which run.

**Disable rules** (enforced in the frontend AND server — server validation
is the load-bearing one, frontend is just UX):
- Disabled when `filing_standard = "mpers"` (out of scope for v1).
- Disabled when `filing_level = "group"` (out of scope for v1).
- Disabled when any notes template is selected (out of scope for v1).
- Disabled when fewer than 5 face statements are checked (the monolith
  is the all-five path; partial selections route through split).

Switching to a disabled combination silently reverts the toggle to
`split` with an inline note, matching how the existing standard/level
toggles handle invalid pairs.

**Surfacing on the run page.** The Overview tab shows a badge:
`Orchestration: monolith` (or split). The Telemetry tab labels the
single agent's turn rows as `monolith` instead of a statement name so
the per-turn telemetry (gotcha #6) stays interpretable.

**Tests** (pinning).
- `web/src/__tests__/StatementRunConfig.test.tsx`: toggle renders, disabled
  states correct, switching to disabled combination reverts to split.
- `tests/test_run_config_orchestration.py`: server rejects monolith +
  MPERS / Group / notes combinations with a 4xx; accepts the supported
  combination.
- `tests/test_db_schema_v10.py`: orchestration column present + default
  `'split'`, idempotent migration step. Coexists with the existing
  `tests/test_db_schema_v9.py` (SOCIE matrix labels).

---

## 8. Caching strategy

The whole point of putting the PDF in the system prompt is provider-level
prompt caching. To make that real:

- **Stable prefix:** role + rules + template structure + PDF text in one
  block. Never edited mid-run.
- **Volatile suffix:** tool-call results (`get_state` returns, `write_cells`
  results, `view_pdf_pages` vision payloads) appended in user-role turns.
  Each turn's new content is cheap; the prefix is paid once and then ~10%
  of original cost per turn (Claude pricing; similar for OpenAI).
- **PDF size handling.** If the extracted text exceeds the cache's
  cost-efficient ceiling (~200 KB on Claude), trim using scout's
  `face_page` + `note_pages` to keep only the financial-statement section
  + relevant notes in the cached block. The rest stays accessible via the
  vision tool.

**Failure mode to watch:** if the agent triggers `view_pdf_pages` every
turn, vision payloads pile up in history and erode the cache benefit
behind them. Mitigation: rules section explicitly directs the agent to use
text-in-prompt first and vision only when text is ambiguous (tables,
scanned pages).

---

## 9. Iteration & wall-clock caps

Gotcha #18 caps `MAX_AGENT_ITERATIONS=40` and notes pydantic-ai's silent
`UsageLimits.request_limit=50` ceiling. Monolith doing ~5× the work of any
specialist needs more headroom.

**For the monolith path only** (gated by the orchestration flag, not a
global change):

- `MAX_AGENT_ITERATIONS_MONOLITH = 80` (separate constant in
  `agent_tracing` or a new `monolith/config.py`).
- Pass `UsageLimits(request_limit=100)` explicitly to the pydantic-ai
  `Agent.iter` call on this path — must move in lockstep with our cap or
  the gotcha #18 incident reappears.
- Wall-clock cap: 15 minutes. Soft warning emitted at 10 min via the SSE
  `pipeline_stage` channel (gotcha #19).

The existing pinning test
(`tests/test_max_agent_iterations_below_pydantic_cap.py`) only governs the
**default / split-pipeline** constant. Add a parallel test asserting
monolith uses its own constant and that its `request_limit` override is
strictly greater than its iteration cap.

---

## 10. Edge cases (large-context-specific)

| # | Edge case | Mitigation |
|---|---|---|
| 1 | PDF text exceeds cache-efficient ceiling | Scout-driven trimming: cache only financial-statement section + relevant note pages; vision tool covers the rest. |
| 2 | Status payload grows late in run | Numbers are short; even fully filled, payload ~15 KB. Acceptable. |
| 3 | Agent loops re-writing the same cell with the same value | `history_hints` in `get_state` flags ≥3× repetition with a "try a different approach" nudge. |
| 4 | Agent calls `done()` while checks still fail | `done` rejects unless every failing check is named in `accept_imbalance` with a reason. Forces honest sign-off. |
| 5 | Multi-statement discrepancy ambiguity (which side is wrong?) | Cross-check returns both LHS / RHS values, cell coordinates, and a direction string. Agent has the data to decide. |
| 6 | Sign-error cascades (dividend sign flip → retained earnings → equity) | Sign convention rule front-and-centre in the rules block; cross-check direction hints make cascades easier to localise. |
| 7 | Cache invalidation between turns | Strict structural separation: cached prefix never edited; volatile content appended only as user-role turns. |
| 8 | Vision tool breaks cache temporarily | Acceptable; cache restored next turn. Rules section directs agent to prefer text. |
| 9 | Agent ignores `kind: abstract` and tries to write a header row | `write_cells` rejects via the existing guard (gotcha #17); rejection message names a nearby leaf row. |
| 10 | Run hits wall-clock or iteration cap | Coordinator force-finalises via existing partial-merge path (gotcha #10); run lands `completed_with_errors`. Whatever was written is preserved. |
| 11 | Scout fails or returns empty | Monolith path falls back to including the full PDF text up to cache ceiling; agent can still navigate via vision. |
| 12 | Same value can be written via two different concepts (e.g. legitimate dual-presentation) | Out of scope for v1; if it bites in testing, document and defer. |
| 13 | `view_pdf_pages` returns very long vision payload that fills context | Coordinator caps single tool result at 100 KB (same convention as `agent_tracing` trace saver, gotcha #6). |
| 14 | Provider rate-limit / proxy SSL error mid-run | Inherits existing retry + truststore behaviour (gotcha #5); no new path needed. |
| 15 | Windows: `charmap` codec crash on non-ASCII chars in PDF text inlined into prompt | `PYTHONUTF8=1` already set by `start.bat` (gotcha #1); the monolith path adds an extra `write_text(..., encoding="utf-8")` safety net when persisting the cached-prefix preview for debugging. |
| 16 | Windows enterprise proxy rejects huge single-request payload (PDF text ~200 KB + templates) | Stay within the proxy's per-request body limit. If hit, fall back to `view_pdf_pages` tool calls for the bulk of the PDF and cache only templates + rules + a TOC. Comparison-script run-1 instruments this. |
| 17 | Larger client PDF (≥ 100 pages, often scanned) yields very long extracted text | Scout-driven trimming becomes mandatory, not optional. If scout fails on a scanned filing, fall back to the vision inventory pass (same path notes uses, gotcha #14) before deciding the run is unviable for the monolith. |
| 18 | Scanned-image-heavy filing has near-empty text extraction | Auto-detect (text length / page count below threshold) and either (a) route to split path with an inline warning on the run config, or (b) accept vision-tool-only operation with a wider iteration cap. Decide via the spike. |
| 19 | Real client PDFs may contain sensitive data the operator doesn't want in provider caches | **Promoted to slice-0b hard prerequisite** (§11). The monolith's cached-prefix pattern is materially different from the split path's tool-call pattern — written operator sign-off required before any client-PDF run. |
| 20 | Operator on Windows toggles monolith → MPERS combination | Disable rules in §7a catch this both server-side and frontend-side; toggle silently reverts to split with inline note. |

---

## 11. Implementation plan

Slice-thin, each slice independently shippable behind the flag.

**Slice 0 is a hard prerequisite.** Both sub-tasks below must pass
before any code lands. Either failure means the design needs to
change (route PDF text via tool calls instead of caching it, or use
a different model) before the rest of the plan is viable.

| Slice | Deliverable | Pinning test |
|---|---|---|
| **0a** | **Windows proxy probe.** Send a 200 KB system prompt (representative shape: rules + 5 template scaffolds + PyMuPDF text from a real client PDF) + 3 tool definitions through the Windows enterprise proxy with the candidate model. Record: HTTP status, body-size headroom from proxy headers, model-version response, and whether the proxy strips/rejects prompt-cache hint headers. 30-min spike. | n/a (decision artefact: `experiment_artifacts/slice0_proxy_probe.md`). |
| **0b** | **Client-data privacy gate.** Written operator confirmation that the Windows-environment client PDFs may flow into the chosen provider's prompt cache. Note: the split pipeline routes PDF content via on-demand `view_pdf_pages` tool calls; the monolith intentionally puts ~200 KB of PDF text into a cached prefix that the provider may retain for cache-TTL minutes per their pricing model. **Different exposure pattern**, even on the same provider. Includes: provider name, cache TTL, the cache-retention clause from the provider's contract, and a sign-off line. | n/a (decision artefact: `experiment_artifacts/slice0_privacy_gate.md`). |
| 1 | `monolith/state.py`: builds `StateSnapshot` by composing `read_template` + `verifier.verify_totals` + `cross_checks.run_all`. Uses `_resolve_cell_value` from `tools/verifier.py` for formula evaluation. | `tests/test_monolith_state.py`: snapshot shape, abstract rows marked, diffs carry direction, formula cells return computed value + warnings. |
| 2 | `monolith/tools.py`: `get_state`, `write_cells` (cross-sheet wrapper), `done`. | `tests/test_monolith_tools.py`: cross-sheet write, abstract rejection, done-blocks-on-failing-check. |
| 3 | `prompts/monolith_face.md`: rewritten consolidated prompt + template structure renderer. | `tests/test_monolith_prompt.py`: rules block contains the load-bearing invariants verbatim from existing prompts; cross-reference annotations present. |
| 4 | `monolith/coordinator.py`: parallel to `coordinator.py`; reuses merge + audit. | `tests/test_monolith_coordinator.py`: full run with mocked agent, partial-merge on cancel, iteration cap fires structured outcome. |
| 5 | DB migration **v9→v10**: `runs.orchestration` column (nullable TEXT, default `'split'`). Lands on top of the in-main v9 SOCIE-matrix-labels step. | `tests/test_db_schema_v10.py`. |
| 6 | CLI flag `--orchestration` + server `RunConfigRequest.orchestration` + validator rejecting unsupported combinations. | `tests/test_orchestration_flag.py`, `tests/test_run_config_orchestration.py`. |
| 7 | **UI toggle in `StatementRunConfig.tsx`** with disable rules + History/Overview badge + Telemetry label adjustment. | `web/src/__tests__/StatementRunConfig.test.tsx` (added cases), `web/src/__tests__/RunDetailView.test.tsx` (badge present). |
| 8 | `scripts/compare_orchestration.py`: runs both paths on a list of PDFs, dumps a CSV. | Smoke test on FINCO; CSV columns documented inline. |
| 9 | **Windows smoke run** on the operator's machine against 1 real client PDF before the formal experiment, to catch UTF-8 / proxy / Node-PATH issues early (gotchas #1, #5, #8). | n/a (operator runbook artefact). |
| 10 | Run experiment on FINCO + 2–4 real client PDFs from Windows; write up findings; decide. | n/a (decision artefact). |

Estimate: ~2 weeks of focused work for slices 1–8, ~1 day for slice 9 (the
Windows smoke), ~3 days for slice 10 (the experiment + write-up).

---

## 12. Open questions

1. **Model choice.** Recommend Claude Opus 4.7 1M (long context + strong
   reasoning + best caching economics). Fallback GPT-5.4. Gemini 3 likely
   struggles with the long iteration discipline this design demands —
   include it only if proxy economics force the issue.

2. **Should the monolith feed into the correction agent on failure?**
   Probably no. The whole point of the monolith is to make correction
   unnecessary; if it still fails, that's data. (Easy to change later.)

3. ~~**`done({accept_imbalance})` strictness.**~~ **Resolved (2026-05-28
   peer review):** every entry requires `{check_id, reason, pdf_page,
   evidence_excerpt}`; server validates the check is currently failing,
   the page exists, and the excerpt is non-empty (§6). Accepted entries
   register as `accepted_residual` in the metric system (§2a), never
   `passed`. Free-form acceptance would let the agent "win" the
   experiment by naming failures instead of fixing them.

4. **Test-set selection.** FINCO is in-repo. Need 2–4 other audited
   filings of varying complexity. Pick PDFs that currently produce known
   cross-check failures in the split pipeline — that's where the monolith
   should differentiate.

5. **Cost ceiling.** What's an acceptable per-run cost? Frames whether
   Opus + 200KB cached PDF is on the table.

---

## 13. Decision criteria — what we do based on results

| Result | Action |
|---|---|
| Monolith wins on cross-check pass rate AND cost within 2× | Promote to default for MFRS Company. Plan Phase 2 (MPERS / Group / notes). |
| Monolith ties on quality, costs ≥ 2× | Keep split as default. Reuse `get_state` tool in split + correction pipeline (still a win). |
| Monolith loses on quality | Abandon monolith. Document why for the record. Consider whether the consolidated rules block in the rewritten prompt helps the correction agent specifically. |
| Mixed / unclear after 5 PDFs | Expand test set to 10 PDFs before deciding. |

---

## 14. Non-goals (for the experiment)

- This is **not** a deprecation of the split pipeline.
- This is **not** a rewrite of cross-checks or the verifier (those are reused as-is).
- This is **not** a notes-pipeline change.
- The UI toggle is **operator-facing**, not an end-user feature — labelled "Experimental", non-default, and constrained to MFRS Company face-only until the experiment concludes.

---

## 15. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Iteration cap insufficient even at 80 | Medium | High | Wall-clock cap catches it; structured `iteration_exhausted` outcome surfaces honestly. |
| PDF text quality bad (scanned-image-heavy filings) | Medium | High | Vision-tool fallback for any page the text-extraction missed; scout already flags scanned filings. |
| Long-context attention degradation on Opus | Low | Medium | Status tool re-surfaces structured state every turn so agent doesn't have to "remember" earlier-in-context. |
| Cache misses make cost prohibitive | Medium | Medium | Strict prefix/suffix discipline; instrument cache-hit ratio in the comparison script. |
| Agent gaming `done({accept_imbalance})` to "pass" the experiment | Low | Medium | Server-validated entries (§6 — `pdf_page` must exist, `check_id` must be currently failing, `evidence_excerpt` non-empty) + tri-state metric (§2a — accepted entries register as `accepted_residual`, never `passed`). Belt-and-braces. |
| Real client PDFs much larger than FINCO push the cached prefix past the cost-efficient ceiling | High | Medium | Scout-driven trimming mandatory; comparison script reports cache-hit ratio so we see the cost cliff before it bites. |
| Windows enterprise proxy bandwidth / per-request limits trip on big payloads | Medium | High | Slice 9 (Windows smoke run on 1 real PDF) catches this before the formal experiment commits time. |
| Scanned-image-heavy client filings make text-in-prompt useless | Medium | Medium | Auto-detect at scout time; route to split path with a clear UI message. Don't burn a long monolith run on a PDF the design can't help. |
| Toggle exposed in UI tempts an operator to use it on MPERS / Group by accident | Low | Medium | Disable rules in §7a (frontend + server validation, both pinned by tests). |

---

*End of PRD.*
