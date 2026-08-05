# Implementation Plan: Extraction Harness Efficiency — Measure Right, Then Cut the Static Payload

**Overall Progress:** `0%`
**PRD Reference:** none — shaped in-session 2026-07-27 from a telemetry review of
235 runs / 462 agent executions / 1,298 per-turn rows / 297 conversation traces
in `output/xbrl_agent.db`. Companion doc:
`docs/PLAN-agent-efficiency-and-recovery.md` (the reviewer-side portfolio — this
plan is its extraction-side counterpart and deliberately does not overlap it).
**Last Updated:** 2026-07-27

> Replaces the previous PLAN.md for **mTool Fill Pipeline — Facts → Filled MBRS
> Template**, which was at **75% with Phases 1 / 3 / 5 still open** (all gated on
> Windows recon evidence or a later variant pass). That work is **not cancelled**
> — it was copied verbatim to `docs/PLAN-mtool-fill-pipeline.md` before this file
> was replaced. Same replace-in-place convention this file has used before, but
> the previous occupant was unfinished, so it was preserved rather than left to
> git history alone.

## Summary

Telemetry says the extraction pipeline's cost is not spent where we assumed.
**90–97% of every extraction agent's billed text is a static prefix re-sent on
every turn**, and its largest single item — the `read_template` summary, ~20,000
tokens on SOFP — is fetched via an avoidable tool round trip and rendered one
line per *cell* instead of one line per *row*. Separately, our reported dollar
figures are roughly 3× too high because `pricing.py` bills cache reads at the
full input rate. This plan fixes the measurement first, then cuts the static
payload, then stops the five face agents from re-reading the same PDF pages.

## Key Decisions

- **Measurement before optimisation.** `pricing.py` prices every prompt token at
  the full rate (its own docstring admits this), while SOFP runs at a 72% cache
  hit. Reported $1.53 for SOFP is really ~$0.50–0.60. Every later step here is
  gated on a number, so the numbers have to be right first.
- **Attack extraction, not the reviewer.** The reviewer is ~15% of run cost and
  `docs/PLAN-agent-efficiency-and-recovery.md` already covers it. SOFP alone is
  ~45% — more than the other four face statements combined — and 97% of its text
  is static. That is where the money is.
- **This is not CodeMode.** The deleted spike failed because it tried to script
  away vision and accounting judgement. Nothing here touches judgement: we are
  moving a deterministic string from a tool return into a system prompt, and
  making it shorter.
- **Compress before relocating.** Two separate changes to the template payload,
  landed separately, so if quality moves we know which one moved it.
- **Every behaviour change ships behind an env kill-switch, default off during
  rollout** — repo convention (`XBRL_FACT_BASED_CHECKS`, `XBRL_SPOT_CHECK`, …).
- **The SOCI/SOPL merge is decision-gated, not pre-approved.** It is the only
  step that changes agent topology, and it is sequenced last so it is judged
  against the post-optimisation baseline rather than today's.
- **Report both numbers.** Until Step 1 is live everywhere, every cost figure in
  this doc carries the pre-cache estimate *and* the cache-adjusted figure.

## Current Baseline (measured 2026-07-27 — the "before" picture)

> **Corrected 2026-07-27 (second pass).** The first draft of this table was
> contaminated two ways and both are now fixed:
> (a) **CodeMode runs were included.** Runs **231, 233, 235** ran with the
> since-deleted `XBRL_CODE_MODE` seam on (`run_code` calls in `agent_events`;
> run 233's SOFP is the flag-on failure recorded in the spike's A/B). Those
> runs cost ~$2.09 mean vs ~$1.42 without, and inflated the baseline ~8%. They
> are now excluded from every figure below.
> (b) **Static-prefix share was over-counted** by a recursive measurement that
> double-counted nested parts. Re-measured flat: **73–90%, not 90–97%.**
> The direction and ranking of levers are unchanged; the Phase 2 gate is
> measured against these corrected numbers.

| Signal | Value (CodeMode runs excluded) |
|---|---|
| SOFP reported cost / true cost | $1.42 mean, $1.60 median / ~$0.45–0.55 (72% cache hit) |
| SOFP cumulative prompt tokens | 552,290 mean |
| SOFP `read_template` payload | **79,563 chars** (~20k tokens), 755 lines, 2 sheets — byte-identical on every run, CodeMode or not (it is deterministic from the template file). Median across older traces on other variants: 55,095 |
| Static share of billed text | SOCIE 90%, SOPL 75%, SOCI 75%, SOCF 74%, **SOFP 73%**, notes 75–86% |
| Agents whose first tool is `read_template` | 332 / 373 (89%) |
| Model requests : tool-call batches (SOFP) | 202 : 202 (exactly 1:1) |
| Face-agent PDF page duplication | 1.56× median (max 3.74×); ~10 wasted fetches/run |
| Highest-overlap pair | SOCI / SOPL — 42% of pages shared |
| Runs with scout enabled (since June) | 8 / 81 |
| Eval scores ever computed | **0** (1 benchmark, 102 gold facts loaded) |
| Top cross-check failures | sopl_to_socie_profit 49, socie_to_sofp_equity 46, socf_to_sofp_cash 45, soci_to_socie_tci 40 — all cross-statement |
| Concurrency control in `coordinator.py` | none (unbounded `create_task`); real 429s in `agent_events` |

## Pre-Implementation Checklist

- [ ] 🟥 Confirm `docs/PLAN-mtool-fill-pipeline.md` is the accepted home for the
      superseded mTool work, and that its open phases are still wanted
- [ ] 🟥 Confirm no in-flight branch already edits `extraction/agent.py`
      `_summarize_template` or the `coordinator.py` fan-out
      (candidates to check: `feat/batched-write-tools`, `feat/compaction-economics`)
- [ ] 🟥 Agree the Phase 2 and Phase 4 gate numbers **before** Phase 2 lands, so
      they are frozen before the data that would tempt us to move them
- [ ] 🟥 Pick one fixed reference PDF + run config as the standard A/B case, so
      every "Verify" below compares like with like

---

## Tasks

### Phase 1: Make the Instruments Honest

Nothing else in this plan can be judged until cost and accuracy are measured
correctly. All three steps are additive and change no agent behaviour.

- [ ] 🟥 **Step 1: Cache-aware cost accounting** — stop overstating spend ~3×.
  - [ ] 🟥 Add a cache-read price per model to `config/models.json`
        (`cached_input_price_per_mtok`), defaulting to the full input price when
        absent so an unpriced model can never silently under-report.
  - [ ] 🟥 Update `pricing.py`'s estimator to take cache-read tokens and bill
        them at the cached rate. Reuse the provider denominator logic already
        proven in `scripts/cache_report.py` — **OpenAI's `prompt_tokens` already
        includes cached reads; Anthropic's does not.** Getting this backwards
        double-counts or under-counts.
  - [ ] 🟥 Keep the pre-cache estimate available alongside the adjusted figure;
        do not silently change what historical `run_agents.total_cost` rows mean.
  - [ ] 🟥 Test `tests/test_pricing_cache_adjusted.py`: OpenAI-shaped and
        Anthropic-shaped inputs each produce the expected figure; a model with no
        cached rate falls back to full price; zero cache reads reproduces today's
        number exactly.
  - **Verify:** `./venv/bin/python scripts/cache_report.py 235` reports a SOFP
    cost materially below $1.53 (expect ~$0.50–0.60), and the same run with cache
    reads forced to 0 reproduces the old figure to the cent.

- [ ] 🟥 **Step 2: One repeatable run-economics report** — the instrument every
  later "Verify" depends on, so we stop hand-rolling SQL per question.
  - [ ] 🟥 Dev-only `scripts/report_run_economics.py <run_id>`: per agent —
        cache-adjusted cost, pre-cache cost, model requests
        (`node_kind='model_request'`, never raw graph-node counts), tool-call
        batches, prompt/completion/cache tokens, wall time, PDF pages viewed
        (calls and unique pages), and the static-prefix share of billed text.
  - [ ] 🟥 A `--compare A B` mode printing two runs side by side with deltas, so
        an A/B is one command rather than a spreadsheet.
  - [ ] 🟥 Read-only: no writes to the audit DB, no new tables, no schema change.
  - **Verify:** run it against the frozen reference case; every number it prints
    for run 235 reconciles with the raw `run_agents` / `run_agent_turns` rows.

- [ ] 🟥 **Step 3: Turn the accuracy instrument on** — `eval_scores` is empty, so
  "improve accuracy" currently has no target to move.
  - [ ] 🟥 Score the existing benchmark (1 benchmark, 102 gold facts) against
        every historical run matching its template set, via the existing
        `eval/grader.py` path — no new grading logic.
  - [ ] 🟥 Record the resulting accuracy figures in this doc as the frozen
        accuracy baseline, alongside per-run cross-check pass/fail counts.
  - [ ] 🟥 If the existing benchmark covers too few runs to be a usable gate, say
        so explicitly here and seed one more from a known-good run
        (`POST /api/benchmarks/from-run` — the workbook-upload path silently
        drops formula cells, gotcha #23).
  - **Verify:** the Evals workspace shows a non-zero score for at least one run;
    that number and its gold fingerprint are written into this doc.

**Phase 1 gate:** cost figures are cache-adjusted, one command reproduces them,
and at least one accuracy number exists. No agent behaviour has changed — a
re-run of the reference case must show an identical tool sequence.

---

### Phase 2: Cut the Static Template Payload

The single largest line item in extraction. Two independent changes, landed
separately.

- [ ] 🟥 **Step 4: Row-oriented template summary** — `_summarize_template` in
  `extraction/agent.py` emits one line per *cell* (755 lines for SOFP, including
  `B1`/`C1`/`D1` header cells and the same label repeated across value columns).
  Emit one line per *row* with its label and which columns accept data.
  - [ ] 🟥 Rewrite the renderer only. **No change to `TemplateField` parsing, to
        `read_template`'s contract, or to which rows are writable.**
  - [ ] 🟥 Preserve verbatim, in the same words: the
        `[ABSTRACT (section header — do not write)]` marking, the `DATA_ENTRY`
        marking, and formula visibility. Gotcha #17 — the abstract marking is a
        load-bearing defence against the 2026-04-26 SOPL-Analysis incident.
  - [ ] 🟥 Keep the existing process-global memoisation working
        (`_TEMPLATE_SUMMARY_CACHE`, keyed by `template_id` + mtime).
  - [ ] 🟥 Flag `XBRL_TEMPLATE_SUMMARY_COMPACT` (default off during rollout, read
        at call time).
  - [ ] 🟥 Extend `tests/test_template_reader.py`: existing
        `test_abstract_rows_marked_in_sopl_analysis` and
        `test_mpers_templates_carry_header_fills_like_mfrs` pass in **both** flag
        states; a new test pins that every row present in the verbose rendering
        is still present in the compact one, for MFRS and MPERS × Company and
        Group.
  - [ ] 🟥 `tests/test_fill_workbook_abstract_guard.py` green in both states.
  - **Verify:** SOFP's `read_template` payload drops from 79,563 chars to under
    35,000 with no row lost; the reference case produces the same written facts
    as baseline; full suite green in both flag states.

- [ ] 🟥 **Step 5: Move the template into the static prefix** — removes one model
  round trip per agent and makes the payload cache-eligible from request #1
  instead of #3.
  - [ ] 🟥 Render the (now compact) summary into the agent's system prompt at
        construction time. It is fully determined by the template file, and 89%
        of agents fetch it unconditionally as their first act.
  - [ ] 🟥 **Keep `read_template` registered as a tool.** On the new path it
        returns a short pointer ("template structure is in your instructions
        above") rather than being removed — an agent that calls it anyway must
        not fail, and older prompts must not break.
  - [ ] 🟥 Update `prompts/_base.md` and any statement prompt instructing the
        agent to call `read_template` first; update the matching prompt pinning
        tests in the same commit (repo convention).
  - [ ] 🟥 Face extraction agents only in this step. Notes agents already seed
        template labels into their system prompt (`create_notes_agent`) — leave
        that path alone.
  - [ ] 🟥 Flag `XBRL_TEMPLATE_IN_PROMPT` (default off during rollout).
  - [ ] 🟥 Flag-off identity pin: with both Phase 2 flags off, the agent factory
        snapshot (instructions, tools, capabilities, model settings) is
        byte-identical to today.
  - **Verify:** on the reference case,
    `scripts/report_run_economics.py --compare` shows **one fewer model request
    per face agent** and a cache hit beginning at request 2 rather than 3;
    written facts and cross-check outcomes unchanged;
    `tests/test_extraction_agent.py` and `tests/test_e2e.py` green in both flag
    states.

**Phase 2 gate (frozen before the first flag-on run):** written facts identical
or better on the reference case; no new failing cross-check; no new abstract-row
or residual-plug violation; cache-adjusted extraction cost down ≥20%; model
requests down ≥1 per face agent. Miss any of these → flags stay off and we
record why here.

---

### Phase 3: Stop the Blind Page Hunt

- [ ] 🟥 **Step 6: Scout on by default** — scout exists to tell agents which pages
  to open, and 73 of the last 81 runs ran without it. Agents hunt instead: median
  2 view calls, mean 3.4, max 36.
  - [ ] 🟥 Flip the default to on for CLI and web run creation, with an explicit
        off switch, surfaced in the run config as today.
  - [ ] 🟥 **Do not** re-introduce page restriction. Hints stay advisory —
        gotcha #13, pinned by `tests/test_page_hints.py` negative assertions.
  - [ ] 🟥 Price scout honestly in the comparison: it is a real agent (~$0.21,
        ~9 requests). The gate is total run cost, not view-call count.
  - **Verify:** reference case with and without scout, compared with
    `--compare`: unique pages viewed by face agents falls, **and cache-adjusted
    total run cost including scout** falls. If total cost rises, scout stays
    opt-in and we record that outcome here.

- [ ] 🟥 **Step 7: Bound the fan-out** — `coordinator.py` creates one task per
  statement with no semaphore; a full run launches up to 15 concurrent agents,
  and real 429 rate-limit errors are already in `agent_events`.
  - [ ] 🟥 Add a configurable concurrency cap (`XBRL_MAX_CONCURRENT_AGENTS`,
        default high enough to be a no-op for today's 5+5 shape) around agent
        launch in `coordinator.py` and `notes/coordinator.py`.
  - [ ] 🟥 **Preserve every existing lifecycle guarantee**: independent per-agent
        cancellation, `task_registry` registration for the abort API, the
        `CancelledError` grace-period path, and the sentinel push in `finally`
        (gotcha #10). A queued-but-not-yet-started agent must still reach a
        terminal status.
  - [ ] 🟥 Extend `tests/test_coordinator.py`: a cap of 2 with 5 statements still
        completes all 5; Stop-All while agents are queued leaves none `running`.
  - **Verify:** a capped run of the reference case completes with identical
    results; `tests/test_stop_all_preserves_partial.py` green.

---

### Phase 4: Structural — Only If the Numbers Still Justify It

Entry gate: Phases 1–3 are live and their savings recorded in this doc. Both
steps are judged against the **post-optimisation** baseline, not today's.

- [ ] 🟥 **Step 8: Compaction-vs-cache probe** — measurement, then a decision. No
  behaviour change in this step.
  SOFP's cache-read tokens fall in *absolute* terms mid-run (37.5k → 31.1k, and
  57.0k → 31.9k) while the prompt grows. That is what a changed prefix looks
  like, and `extraction/history_processors.py` rewrites earlier messages by
  design. If confirmed, we may be paying full price on a ~60k-token prefix to
  trim a payload that was already heavily discounted.
  - [ ] 🟥 Log cache-read deltas immediately before and after each history
        processor fires, on the reference case.
  - [ ] 🟥 Report the net effect: tokens saved by trimming vs. cache value
        destroyed, in cache-adjusted dollars.
  - [ ] 🟥 Record the decision here. Options: leave as-is, delay trimming until a
        size threshold, or trim only the tail. **Do not weaken the run-126
        stage-aware protections** (pre-write images stay whole) without the
        scanned-PDF case passing — that regression caused extraction thrash.
  - **Verify:** a findings section in this doc with the dollar figure and an
    explicit "change / no change" decision.

- [ ] 🟥 **Step 9: SOCI/SOPL agent merge** — the highest page overlap (42%) and
  one of the four cross-statement checks that dominate our failures
  (`soci_to_socie_tci`, 40). SOCI is the OCI continuation of SOPL, usually on the
  same page, currently extracted by two agents that bill two copies of it and
  then have to agree with each other.
  - [ ] 🟥 **Decision gate first** — do not start until Phase 1–3 numbers are in
        this doc and the owner approves. This is the only step that changes agent
        topology, and it touches `statement_types.py`, the coordinator, prompts,
        cross-checks, and coverage.
  - [ ] 🟥 If approved: one agent producing both statements' facts, behind
        `XBRL_MERGE_SOCI_SOPL` (default off).
  - [ ] 🟥 Both statements keep their own `run_agents` row and telemetry so
        History, the Agents tab, and per-statement resume stay meaningful.
  - [ ] 🟥 Cross-checks unchanged in definition; `soci_to_socie_tci` and
        `sopl_to_socie_profit` must still run and still be able to fail.
  - [ ] 🟥 MFRS and MPERS × Company and Group all covered, including the
        variant-specific SOCI templates.
  - **Verify:** reference case + one Group + one MPERS case: same or better
    accuracy score (Step 3 instrument), no new failing cross-check, unique pages
    viewed down, cache-adjusted cost down. Any accuracy regression → flag off,
    step abandoned, recorded here.

---

## Out of Scope (deliberately — recorded so it isn't lost)

- **Reviewer economics** (context reduction, investigation bundle, tool search) —
  owned by `docs/PLAN-agent-efficiency-and-recovery.md`. Not duplicated here.
- **Reviewer not verifying its own work** — `apply_fix` was called 12 times
  across 19 passes; `verify_fixes` once. Real finding, reviewer-side, belongs in
  the companion plan.
- **The reviewer's serial tail** (20–41% of wall clock) — architectural; needs
  its own shaping session.
- **Collapsing `verify_totals → save_result` into one round trip** — plausible,
  but it crosses a judgement boundary and the CodeMode spike is the cautionary
  tale. Not without its own experiment.
- **Reopening extraction CodeMode in any form.**
- **Notes agents' template seeding** — already in their system prompt; untouched.
- **Any new abstraction, config surface, or Settings UI for these flags.**

## Rollback Plan

- **Any behaviour step, instantly:** unset its flag. Every step in Phases 2–4 is
  default-off during rollout, so unsetting returns to today's behaviour with zero
  code changes.
- **Step 1 (pricing):** revert the commit. It changes reported figures only — no
  schema change, no stored value rewritten. Historical `total_cost` rows keep
  their original pre-cache meaning either way.
- **Steps 2 and 3:** a dev-only script and read-only scoring — nothing to roll
  back beyond deleting the script.
- **Step 7 (semaphore):** set the cap high enough to be a no-op, which reproduces
  today's unbounded behaviour exactly.
- **State to check after any rollback:**
  - flag-off agent factory snapshot tests green (Step 5's identity pin)
  - `./venv/bin/python -m pytest tests/ -n auto` green
  - a fresh run of the reference case shows the baseline tool sequence
    (`read_template > view_pdf_pages > … > save_result`) and a normal Telemetry
    tab
  - no `run_agents` row left in `running` (gotcha #10)
