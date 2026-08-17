# Implementation Plan: Template Map — Give the Agent the Field List the Database Already Knows

**Overall Progress:** `0%`
**PRD Reference:** none — shaped in-session 2026-08-17 from a walkthrough of how
extraction agents learn which fields to fill (`/explain`), followed by
measurements on the live templates, the concept database and 303 historic
conversation traces. Companion doc: `docs/PLAN-extraction-harness-efficiency.md`
(the cost-side plan this file replaces in place — see the note below).
**Last Updated:** 2026-08-17

> Replaces the previous PLAN.md, **Extraction Harness Efficiency — Measure
> Right, Then Cut the Static Payload** (2026-07-27, 0% done). That plan is
> **not cancelled** — it was copied verbatim to
> `docs/PLAN-extraction-harness-efficiency.md`. Its Phase 2 Step 4
> ("row-oriented template summary") is **superseded by Phase 2 of this plan**,
> which produces the row-level rendering from the concept database rather than
> re-rendering the Excel dump; its Step 5 (move the template into the static
> prefix) is sequenced here as Phase 5 because it depends on the map being
> compact first. Everything else in that plan (pricing fix, economics report,
> scout default, fan-out cap, SOCI/SOPL merge probe) stays open in its new home
> and is untouched here.

## Summary

The extraction agent learns the fillable fields from a per-cell dump of the
Excel template (`read_template`), and that dump is wrong in a way that costs
turns: on the MFRS Company SOFP face sheet **37 of 63 rows are labelled
`[DATA_ENTRY]` although their value cells are formulas** — the correct place to
write is the sub-sheet. The concept database (`concept_nodes`,
`concept_render_aliases`, `concept_edges`, `concept_definitions_*.json`)
already holds the correct row-level classification, the "linked to sub-sheet
row N" relationship, the summation structure, and the official SSM definitions.
This plan renders the agent's template map from that database — one line per
row, one status word, scoped per sheet, with definitions one call away — and
adds a stable row key to `write_facts` so an ambiguous label can no longer
misroute a write. The goal is fewer wasted turns and fewer wrong cells on
cheaper models; cost reduction is a side effect, not the target.

## Why now — the measurements

| Signal | Value | Source |
|---|---|---|
| `read_template` payload, MFRS Company SOFP | 79,563 chars ≈ 20k tokens, 754 lines, one line per **cell** | `_summarize_template` on the live template |
| Same figure, other MFRS Company face templates | OrderOfLiquidity 13.8k tok · SOPL 6.0–6.3k · SOCIE 10.2k · SOCF 2.5–4.3k · SOCI 1.7–2.0k | same |
| Face-sheet rows shown `[DATA_ENTRY]` whose value cell is a formula (SOFP-CuNonCu) | **37 of 63** | `tools/template_reader.read_template` per-row scan |
| Face-sheet rows that are truly writable (SOFP-CuNonCu) | 26 | same |
| Refused writes across 303 historic traces — "formula cell" | **121** | `output/*/*_conversation_trace.json`, count of the guard message |
| Refused writes — "no matching label" | 28 | same |
| Refused writes — "abstract row" | 2 | same (the guard + `[ABSTRACT]` tag work — keep both) |
| Face rows with an exact SSM definition available | MFRS 60% (783/1285) · MPERS 77% (715/927) | `concept_definitions_*.json` joined on normalised label |
| Face agents whose first tool call is `read_template` | 89% (332/373) | companion plan, 2026-07-27 |
| Eval scores ever computed | 0 (1 benchmark, 102 gold facts) | companion plan |

The `formula_cell` refusal is the dominant wasted turn, and it is the direct
consequence of the per-cell rendering: the tag next to the label says
"writable", the prose in `prompts/sofp.md` says "many cells are formulas, fill
the sub-sheet first", and a cheaper model follows the tag.

## Key Decisions

- **Correctness of the map is the target, not token count.** The companion plan
  cuts the same payload for cost. This plan is judged on refused-write count and
  written-fact accuracy on a cheap model. If the map is right, the payload gets
  smaller as a consequence; if it is merely smaller, that is not enough.
- **Render from the concept database, not from a re-styled Excel dump.** The DB
  is where `LEAF / COMPUTED / ABSTRACT`, the alias-to-sub-sheet link, and the
  summation edges already live. Re-deriving them from Excel a second time keeps
  two parsers that can disagree. Formula *text* is the one thing the DB lacks
  (recorded decision Q3 in `docs/PLAN-excel-free-verification.md`); the agent
  does not need `='SOFP-Sub-CuNonCu'!B39` — it needs "linked → write on
  Sub r39", which the alias table states directly.
- **The Excel reader stays for the writer and for the fallback.** `tools/
  template_reader.py` still feeds `fill_workbook`'s guards and section-header
  detection (gotcha #17). This plan does not touch the write-side guards.
- **Ships behind a flag, default off during rollout** — repo convention
  (`XBRL_TEMPLATE_MAP`). Flag off = today's byte-identical summary. Flip is a
  Settings change after the Phase 4 gate, not a code revert.
- **Notes agents are out of scope.** They already seed compact col-A labels
  into their system prompt (`notes/agent.py::_render_label_catalog`) and their
  own `read_template` returns labels only. The defect being fixed lives on the
  face-statement path.
- **A stable row key is added, the label path is not removed.** `write_facts`
  already accepts `row`; what it lacks is a cross-check that the row the agent
  names carries the label the agent thinks it does. Adding that check makes the
  row key safe on cheaper models; keeping the label path means no prompt is
  forced to change on day one.
- **Measurement gate before and after, on a cheap model.** Baseline and A/B run
  on the same reference PDF with the same cheap model. The Evals workspace
  exists for this; the accuracy instrument is currently unused (0 scores) and
  is switched on in Phase 0.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm the companion plan's owner accepts that its Step 4 is
      superseded here (this file says so; the other file should get a one-line
      pointer back once agreed).
- [ ] 🟥 No in-flight branch edits `extraction/agent.py::_summarize_template`,
      `tools/fill_workbook.py::FactWrite`, or `extraction/history_processors.py`
      (checked 2026-08-17: `feat/batched-write-tools` and
      `feat/compaction-economics` are both fully merged; current branch
      `feat/pdf-source-sidecar` does not touch these files).
- [ ] 🟥 Pick the reference case: `data/FINCO-Audited-Financial-Statement-2021.pdf`,
      MFRS Company, all five statements, scout on. Pick the cheap model for the
      A/B from the configured list (candidates: `openai.gpt-5.4-mini`,
      `vertex_ai.gemini-3.5-flash`, `bedrock.anthropic.claude-haiku-4-5`) and
      write the choice into this file.
- [ ] 🟥 Freeze the Phase 4 gate numbers (below) before the first flag-on run.

---

## Tasks

### Phase 0: Baseline — Count the Wasted Turns Before Changing Anything

Everything later is judged against these numbers. Read-only; no agent
behaviour changes.

- [ ] 🟥 **Step 0.1: Refused-write census script** — turn the ad-hoc trace grep
  into a repeatable instrument.
  - [ ] 🟥 Dev-only `scripts/report_refused_writes.py [run_id | --all]`: for each
        face agent, count `write_facts` tool returns containing each guard
        message (`formula_cell`, `abstract_row`, `no_label`,
        `redirect_other_sheet`, `labelless_row`), plus the number of
        `read_template` calls, the number of model requests, and the model.
        Reads `{output_dir}/{stmt}_conversation_trace.json` and `run_agents`;
        writes nothing.
  - [ ] 🟥 A `--compare A B` mode printing two runs side by side, so an A/B is
        one command.
  - [ ] 🟥 Group by model so the cheap-model figures are visible on their own.
  - **Verify:** `--all` reproduces the 2026-08-17 hand count within rounding
    (formula_cell 121 · no_label 28 · abstract_row 2 across 303 traces).

- [ ] 🟥 **Step 0.2: Turn the accuracy instrument on** — the same step as the
  companion plan's Step 3; do it once, here, since this plan needs it first.
  - [ ] 🟥 Score the existing benchmark against every historical run matching
        its template set via the existing `eval/grader.py` path — no new grading
        logic.
  - [ ] 🟥 If the benchmark covers too few cheap-model runs to be a usable gate,
        seed one more from a known-good run (`POST /api/benchmarks/from-run`;
        never the workbook-upload path — it drops uncached formula cells,
        gotcha #23).
  - **Verify:** the Evals workspace shows a non-zero score for at least one run;
    the number and its gold fingerprint are recorded in this file.

- [ ] 🟥 **Step 0.3: Baseline runs on the reference case** — two runs with the
  chosen cheap model, flag off. Record per face agent: refused writes by cause,
  `read_template` calls, model requests, eval score, cross-check pass/fail.
  - **Verify:** the table is written into this file under "Baseline (measured)".
    Two runs, not one — the second tells us how noisy the cheap model is, which
    sets how large a change has to be before we believe it.

**Phase 0 gate:** one command reports refused writes for any run; at least one
accuracy number exists; the cheap-model baseline table is in this file.

---

### Phase 1: The Map Renderer — a Pure Function Over the Concept Database

No agent wiring yet. Build the renderer, pin it against every template, and
prove it says the same thing about writability as the Excel reader.

- [ ] 🟥 **Step 1.1: `concept_model/template_map.py::render_template_map`** —
  input `(conn, template_id, sheet=None)`; output a string, one line per row.
  - [ ] 🟥 Row status vocabulary, exactly four words: `WRITE` (`LEAF`, and
        `MATRIX_CELL` on SOCIE), `TOTAL` (`COMPUTED`), `HEADER` (`ABSTRACT`),
        `LINKED` (a face coordinate present in `concept_render_aliases` whose
        primary node is on another sheet).
  - [ ] 🟥 `LINKED` lines name the target: `LINKED → write on <sheet> r<row>`.
        `TOTAL` lines name their children from `concept_edges` with sign:
        `auto = r8 + r9 − r14 · do not write`. `WRITE` lines name the value
        columns for the run's filing level (`B=CY C=PY` Company; `B/C Group,
        D/E Company` on Group) — from `concept_targets`, not hard-coded.
  - [ ] 🟥 A one-line sheet header with counts: `SOFP-CuNonCu (26 writable ·
        37 linked · 12 headers · 24 totals)`. Mandatory rows keep their `*`
        and the header explains it once: `* = mandatory row`.
  - [ ] 🟥 Row-1 period date cells (`B1`/`C1`) get one explicit line, since
        `prompts/_base.md` tells the agent to write them and the writer has a
        row-1 carve-out.
  - [ ] 🟥 Keep the `=== Sheet:` banner as the first line of each sheet block.
        `extraction/history_processors._is_template_summary` and
        `strip_duplicate_template` key on that marker
        (`_TEMPLATE_SUMMARY_MARKER`); the compaction path must keep working
        without knowing which renderer produced the text.
  - [ ] 🟥 Template scoping by `template_id`, never a `{standard}-{level}-`
        prefix (gotcha #21 — same `(sheet,row)` exists under every family with
        different uuids).
  - **Verify:** `tests/test_template_map.py` — for every one of the 58
    Company/Group face templates across both standards: (a) the set of rows the
    map calls `WRITE` equals the set of rows the Excel reader has a non-formula,
    non-abstract label cell **and** a non-formula value cell in col B (the truth
    `_summarize_template` was failing to say); (b) every row the Excel reader
    marks abstract is `HEADER`; (c) every alias in `concept_render_aliases` is a
    `LINKED` line naming the right target; (d) SOFP-CuNonCu renders under 5,000
    chars for the face sheet alone and under 25,000 for both sheets.

- [ ] 🟥 **Step 1.2: `describe_rows` — definitions keyed by the map's row ids**
  — the "confirm what this field means" call, one turn, no fuzzy search.
  - [ ] 🟥 `concept_model/template_map.py::describe_rows(conn, template_id,
        sheet, rows: list[int])` → for each row: label, status, and the SSM
        definition when the normalised label matches an entry in
        `concept_definitions_{standard}.json` (60% MFRS / 77% MPERS today).
        Rows without a definition say so explicitly ("no official definition on
        file"), never silently omit — the agent must be able to tell "looked
        and found nothing" from "didn't look".
  - [ ] 🟥 Reuse `concept_model/definitions.load_definitions` and
        `notes.labels.normalize_label`; no new index, no new dependency.
  - **Verify:** `tests/test_template_map.py::test_describe_rows` — a known row
    with a definition returns it; a known row without one returns the explicit
    marker; an out-of-range row is reported, not raised.

- [ ] 🟥 **Step 1.3: Startup availability** — the map needs `concept_nodes` to
  be populated, which the mandatory bootstrap guarantees (gotcha #21). Confirm
  the renderer fails loudly, not blankly, if a `template_id` has no nodes.
  - **Verify:** rendering an unknown `template_id` raises with the id in the
    message; a test pins it.

**Phase 1 gate:** the map agrees with the Excel reader about writability on all
58 templates, and is at most a quarter of the size of today's summary on SOFP.

---

### Phase 2: Wire the Map into the Face Agent — Behind the Flag

- [ ] 🟥 **Step 2.1: `read_template(sheet=None)` serves the map when the flag is
  on** — `extraction/agent.py`.
  - [ ] 🟥 New flag `XBRL_TEMPLATE_MAP` (default off during rollout, read at
        call time so tests can toggle). Off = today's `_render_template_summary`
        path, byte-identical (the existing
        `tests/test_read_template_cache.py::test_cached_summary_is_byte_identical_for_every_template`
        keeps pinning the off state).
  - [ ] 🟥 On = `render_template_map(conn, deps.template_id, sheet)`; the tool
        gains an optional `sheet` argument so the agent can pull one sheet at a
        time. No `sheet` = all sheets (same call shape as today).
  - [ ] 🟥 Memoise like today: process-global, keyed by
        `(template_id, sheet, filing_level)`; the DB rows are deterministic per
        template so this is safe. No mtime in the key — the DB, not the file,
        is the source now; a template regeneration goes through the bootstrap.
  - [ ] 🟥 Falls through to the legacy summary if `template_id`/`db_path` are
        missing (some CLI paths) — graceful degradation, log once.
  - **Verify:** `tests/test_read_template_cache.py` green in both flag states;
    a new test asserts the flag-on return carries `_TEMPLATE_SUMMARY_MARKER`
    so `strip_duplicate_template` still collapses repeats.

- [ ] 🟥 **Step 2.2: Register `describe_rows` as a face-agent tool** — flag-on
  only. Docstring says: "Confirm what a template row means before writing to
  it. Pass ALL the rows you are unsure about in ONE call." Batched, like
  `calculator` and `lookup_definitions`.
  - [ ] 🟥 `lookup_definitions` stays registered (free-text search still has a
        use when the agent has a PDF phrase, not a row).
  - **Verify:** `tests/test_extraction_agent.py` — flag on: tool present; flag
    off: agent factory snapshot (tools, instructions, capabilities) byte-identical
    to today.

- [ ] 🟥 **Step 2.3: Prompt wording for the map** — the prompts describe the
  old shape ("the read_template() output lists every row label under each
  section", `[FORMULA]`, `DATA_ENTRY`). Under the flag, `render_prompt` injects
  a short block explaining the four status words and the two calls; the old
  wording is untouched when the flag is off.
  - [ ] 🟥 Face prompts only: `prompts/_base.md` and the statement files that
        name `read_template` (`sofp.md`, `sofp_orderofliquidity.md`, `sopl.md`,
        `soci.md`, `socf.md`, `socie.md`, `socie_mpers.md`, `socie_sore.md`).
        Notes prompts untouched.
  - [ ] 🟥 Regenerate `docs/agent-prompt-audit.html`
        (`python scripts/refresh_prompt_audit.py`) — pinned by
        `tests/test_prompt_audit_matches_live.py`.
  - [ ] 🟥 Existing prompt pinning tests
        (`tests/test_prompt_residual_plug_rule.py`,
        `test_prompt_standard_neutrality.py`, `test_group_socie_overlay_routing.py`,
        `test_extraction_hardening_prompts.py`) green in both flag states.
  - **Verify:** the rendered flag-on SOFP prompt contains both the map block
    and — still — the "fill the sub-sheet first" failure-mode text; the two no
    longer contradict the tool output.

**Phase 2 gate:** flag off is byte-identical everywhere; flag on, a mocked e2e
run (`tests/test_e2e.py` with the flag forced on) completes with the same
written facts as flag off.

---

### Phase 3: Stable Row Keys on `write_facts` — Close the Label-Ambiguity Class

- [ ] 🟥 **Step 3.1: `row` + `field_label` together = cross-checked write** —
  `tools/fill_workbook.py`. Today `row` alone skips label resolution and
  `field_label` alone does fuzzy matching. When BOTH are given, resolve by
  `row`, then require the col-A label at that row to match `field_label` after
  the writer's own normalisation (leading `*`, taxonomy suffixes); refuse with a
  message naming the actual label at that row when they disagree.
  - [ ] 🟥 New guard kind `row_label_mismatch` in the `guard_rejections`
        tally. All existing guards (formula cell, abstract row, labelless row,
        leaf-over-header) still apply after resolution — no guard weakened.
  - [ ] 🟥 `FactWrite` docstring and the `write_facts` docstring describe the
        third mode: "Row-keyed (preferred when the map gives you the row):
        `{"sheet","row","field_label","col","value","evidence"}`".
  - **Verify:** `tests/test_fill_workbook_row_key.py` — matching row+label
    writes; mismatched pair is refused and the message names the real label;
    row alone and label alone behave exactly as today
    (`tests/test_fill_workbook_abstract_guard.py`,
    `test_fill_workbook_cross_sheet_hint.py` green).

- [ ] 🟥 **Step 3.2: Prompt nudge under the flag** — the map block from Step
  2.3 says: "When you have the row from the map, send both `row` and
  `field_label`." Off-flag prompts unchanged.
  - **Verify:** flag-on prompt contains the sentence; audit regenerated;
    pinning tests green.

- [ ] 🟥 **Step 3.3: Refusal messages point at the map, not at the dump** — the
  five guard messages currently say "call read_template()"; under the flag they
  say "call `read_template(sheet=…)` and use the row id shown as `rN`". Off-flag
  wording byte-identical (`tests/test_verifier_feedback_wording.py` and the
  guard tests pin it).
  - **Verify:** guard tests green in both flag states.

**Phase 3 gate:** the row-keyed mode exists, is cross-checked, and no existing
guard test changed.

---

### Phase 4: Measure — Flag On vs Off, Same Cheap Model, Same PDF

- [ ] 🟥 **Step 4.1: A/B on the reference case** — two flag-on runs with the
  chosen cheap model, same config as Step 0.3.
  - [ ] 🟥 `scripts/report_refused_writes.py --compare <baseline> <flag_on>`
        and the eval score for each.
  - [ ] 🟥 Record the table in this file under "Result (measured)".
  - **Verify:** the table exists and every cell in it is a number from a run id
    named in the file.

- [ ] 🟥 **Step 4.2: Also run once on the default model** (`TEST_MODEL`) — the
  change must not regress the strong model to help the weak one.
  - **Verify:** written facts identical or better vs a same-model flag-off run;
    no new failing cross-check.

**Phase 4 gate (frozen before the first flag-on run):**
1. `formula_cell` + `no_label` refusals per face agent down ≥50% on the cheap
   model (baseline from Step 0.3).
2. Eval accuracy on the cheap model equal or better; on the default model equal
   or better.
3. No new abstract-row or residual-plug violation on either model.
4. No new failing cross-check on either model.
Miss any → flag stays off, and this file records which one and why.

---

### Phase 5: Flip and Tidy — Only After the Gate

- [ ] 🟥 **Step 5.1: Flip `XBRL_TEMPLATE_MAP` default on**; expose it in
  `/api/settings` + `/api/config` like the other rollout flags; add it to the
  CLAUDE.md `.env` block and to a new gotcha describing the map, the four
  status words, and the row-key cross-check.
  - **Verify:** `tests/test_settings_api.py` green; full suite green with the
    default on.

- [ ] 🟥 **Step 5.2: Hand the static-prefix move back to the companion plan** —
  with the map compact and correct, `docs/PLAN-extraction-harness-efficiency.md`
  Step 5 (render the template into the system prompt, keep `read_template`
  as a pointer) becomes a small change. Update that file's Step 4 to point here
  and leave Step 5 to that plan's owner.
  - **Verify:** the companion file carries the pointer; nothing else in it
    changed.

- [ ] 🟥 **Step 5.3: Retire the legacy per-cell renderer** — a later,
  separate decision, only once the flag has been on across a full week of real
  runs. Not part of this plan's 100%.

---

## Rollback Plan

- **Any phase before 5:** the flag is off by default; nothing changes for a
  live run. Revert is `git revert` of the phase's commits; no data to migrate —
  this plan adds no table and no column.
- **After the flip (5.1):** set `XBRL_TEMPLATE_MAP=0` in Settings. That restores
  the byte-identical legacy summary and the legacy prompt wording without a
  deploy. Row-keyed writes already in the DB are ordinary facts — nothing to
  undo.
- **What to check after a rollback:** `scripts/report_refused_writes.py` on the
  next run shows the legacy `read_template` payload size (~79k chars on SOFP)
  and no `row_label_mismatch` entries; `tests/test_read_template_cache.py`
  green.

## Out of Scope (named so nobody adds it by accident)

- Notes agents' template view (already compact; different defect class).
- Removing the Excel write path (`fill_workbook`) — a separate project;
  the guards there are load-bearing (gotcha #17, #22).
- Cost accounting, scout default, fan-out cap, SOCI/SOPL merge — all live in
  `docs/PLAN-extraction-harness-efficiency.md`.
- Adding formula text to `concept_nodes` — decided against (Q3,
  `docs/PLAN-excel-free-verification.md`); the map does not need it.
- Any deterministic label-matching in the notes pipeline (repo rule).
