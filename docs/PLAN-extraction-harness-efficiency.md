# Implementation Plan: Extraction Harness Efficiency — Measure Right, Then Cut the Static Payload

**Overall Progress:** `70%` — Phase 1 done; Phase 2 built behind flags (live A/B open); Phase 3 built (live A/B open); Phase 4 probe logging in, decision open; Step 9 not started (owner gate). See [Status — 2026-08-18](#status--2026-08-18).
**PRD Reference:** none — shaped in-session 2026-07-27 from a telemetry review of
235 runs / 462 agent executions / 1,298 per-turn rows / 297 conversation traces
in `output/xbrl_agent.db`. Companion doc:
`docs/PLAN-agent-efficiency-and-recovery.md` (the reviewer-side portfolio — this
plan is its extraction-side counterpart and deliberately does not overlap it).
**Last Updated:** 2026-08-18

> Replaces the previous PLAN.md for **mTool Fill Pipeline — Facts → Filled MBRS
> Template**, which was at **75% with Phases 1 / 3 / 5 still open** (all gated on
> Windows recon evidence or a later variant pass). That work is **not cancelled**
> — it was copied verbatim to `docs/PLAN-mtool-fill-pipeline.md` before this file
> was replaced. Same replace-in-place convention this file has used before, but
> the previous occupant was unfinished, so it was preserved rather than left to
> git history alone.


## Status — 2026-08-18

Implemented on branch `feat/pdf-source-sidecar` in one pass. Every behaviour
change is behind a default-off env flag as planned. Items marked **open** need
either a live LLM run or an owner decision; neither can be produced offline.

| Step | State | Where | Open gate |
|---|---|---|---|
| 1 Cache-aware cost | done | `pricing.estimate_cost_cache_adjusted`, `get_cached_input_price`; `cached_input_price_per_mtok` on every OpenAI + Anthropic entry in `config/models.json` (10% of input — published rate cards; Google left absent → full price, never under-reports); `scripts/cache_report.py` prints `$pre` and `$adj` | none — but see the correction below: **the plan's "Anthropic prompt_tokens EXCLUDES cache reads" premise is wrong for the pinned library** |
| 2 Economics report | done | `scripts/report_run_economics.py <run>` / `--compare A B`; read-only (`mode=ro`) | none |
| 3 Accuracy instrument | done for the existing benchmark | `scripts/score_benchmark_history.py 2 --pdf …` graded 11 runs into `eval_scores` (was 0; the only writing step — see Rollback) | **The reference case (FINCO) has no benchmark and no complete 5-statement FINCO run exists in the DB to seed one from.** Seeding needs one fresh full run, then `POST /api/benchmarks/from-run`. Alternative: adopt the Oriental document (benchmark 2, 11 graded runs) as the reference case — owner call. |
| 4 Row-oriented summary | built, default off | `XBRL_TEMPLATE_SUMMARY_COMPACT`; `extraction/agent._summarize_template_compact` | live reference run (written facts identical) |
| 5 Template in prompt | built, default off | `XBRL_TEMPLATE_IN_PROMPT`; `read_template` returns `READ_TEMPLATE_IN_PROMPT_POINTER` when embedded; identity pin `tests/test_template_in_prompt.py` | live reference run (`--compare`: −1 request/agent, cache hit from request 2) |
| 6 Scout on by default | done for CLI (`--no-scout`); web already defaulted on (`XBRL_SCOUT_ENABLED_DEFAULT=true`, PreRunPanel seed `true`) | `run.py::_run_cli_scout` (best-effort; failure never fails the run) | live with/without A/B on total cache-adjusted cost |
| 7 Fan-out cap | done, default unbounded | `XBRL_MAX_CONCURRENT_AGENTS`; `agent_concurrency.py`; both coordinators; sub-agents ride their parent's slot | none |
| 8 Compaction-vs-cache probe | logging in, decision open | `history_rewrite …` lines (`extraction/history_processors._log_rewrite`) + `turn_cache …` lines (`agent_runner`) | one reference run with the two log lines, then the dollar figure + decision here |
| 9 SOCI/SOPL merge | **not started** | — | owner approval (decision gate) |

**Correction to Step 1's premise (found in review, 2026-08-18).** The plan
says "OpenAI's `prompt_tokens` already includes cached reads; Anthropic's
does not." On the pinned pydantic-ai 2.9.0 / genai-prices 0.0.71 that is
false: genai-prices' Anthropic extractor sums `input_tokens +
cache_creation_input_tokens + cache_read_input_tokens` into the
`input_tokens` this app records, so EVERY provider's prompt count includes
the reads. The estimator uses one rule (uncached = prompt − reads);
`scripts/cache_report.py`'s Anthropic denominator was corrected to match;
`tests/test_pricing_cache_adjusted.py::test_pinned_library_sums_anthropic_cache_tokens_into_input_tokens`
reads the library's mapping table so an upgrade that changes it fails
loudly. The audit DB has no Anthropic run, so no stored figure was affected.
Anthropic's 1.25× cache-WRITE surcharge is not applied (no write rate in the
registry) — a small, stated under-count on the write slice only.

**CLI scout has no `run_agents` row.** `run.py::_run_cli_scout` runs before
the pipeline creates the `runs` row, so its tokens are not in
`report_run_economics.py --compare`. The Step 6 total-cost gate must be
measured on WEB runs (whose scout endpoint records a `SCOUT` row) or the
scout cost added by hand from `SCOUT_conversation_trace.json`.

**Note on Step 4 vs `docs/PLAN.md`.** `docs/PLAN.md` (2026-08-17) supersedes
this Step 4 with a DB-rendered template map. Step 4 was still built as
written because it is small, flagged, and default-off; if PLAN.md Phase 2
lands, delete `_summarize_template_compact` and its flag together. Step 5 is
renderer-agnostic — it embeds whatever `_render_template_summary` returns —
so it survives that swap unchanged.

**Prompt files were NOT edited for Step 5.** The flag-off identity pin
requires the eight statement prompts to stay byte-identical. When the summary
is embedded, `prompts/__init__.render_prompt` prefixes the TEMPLATE STRUCTURE
block with an instruction that supersedes every "call read_template()" line;
`tests/test_template_in_prompt.py` pins the wording.

### Frozen accuracy baseline (Step 3, measured 2026-08-18)

Benchmark 2 — *Oriental 1936 Berhad* (MFRS / Company; templates
`sofp-orderofliquidity`, `sopl-function`, `soci-beforetax`, `socf-indirect`,
`socie`), gold fingerprint
`31de3b42be392d9854bbae4410e740470daa3b704d6b4d658e2ba3846cba4108`, 96
gradeable gold cells. Score = matched ÷ gold cells; extras are a flag, not in
the denominator (gotcha #23).

| Run | Status | Accuracy | Matched / Missing / Wrong | Extra | Cross-checks (passed / warning / failed / n-a) |
|---|---|---|---|---|---|
| 122 | completed | 85.4% | 82 / 10 / 4 | 32 | 5 / 5 / 0 / 1 |
| 125 | completed | 85.4% | 82 / 10 / 4 | 38 | 5 / 5 / 0 / 1 |
| 127 | completed | 85.4% | 82 / 10 / 4 | 32 | 5 / 5 / 0 / 1 |
| 128 | completed | 88.5% | 85 / 7 / 4 | 36 | 5 / 5 / 0 / 1 |
| 131 | completed_with_errors | 84.4% | 81 / 11 / 4 | 38 | 4 / 2 / 1 / 1 |
| 153 | completed_with_errors | 86.5% | 83 / 9 / 4 | 12 | 1 passed, 5 pending |
| 155 | completed_with_errors | 91.7% | 88 / 6 / 2 | 12 | 5 / 5 / 0 / 1 |
| 159 | completed_with_errors | 100.0% | 96 / 0 / 0 | 0 | 5 / 0 / 0 / 1 — **the seed run; not evidence** |
| 168 | completed_with_errors | 63.5% | 61 / 15 / 20 | 34 | 4 / 4 / 1 / 1 |
| 191 | completed | 88.5% | 85 / 9 / 2 | 20 | 8 / 3 / 0 / 1 |
| 216 | completed_with_errors | 57.3% | 55 / 26 / 15 | 20 | 6 / 0 / 1 / 2 |

Excluding the seed run: median 85.4%, range 57.3–91.7%.

### Cost figures re-stated with Step 1 (run 235, SOFP, gpt-5.4)

| | Pre-cache (stored `total_cost`) | Cache-adjusted |
|---|---|---|
| SOFP | $1.763 | **$0.364** (91.7% cache hit; 677,666 prompt / 621,568 cached) |
| CORRECTION | $0.305 | $0.305 (no cache reads) |
| Run | $2.068 | $0.669 |

Zero cache reads reproduce the stored figure to the cent
(`tests/test_pricing_cache_adjusted.py`). Model requests for SOFP: 8
(`node_kind='model_request'`), tool batches 8, static-prefix share of billed
TEXT 99.1% (text-only measure — images excluded; see the script docstring).

### Phase 2 gate — frozen wording (per the checklist), numbers to be filled by the live A/B

Written facts identical or better on the reference case; no new failing
cross-check; no new abstract-row or residual-plug violation; cache-adjusted
extraction cost down ≥20%; model requests down ≥1 per face agent. Compare
with `scripts/report_run_economics.py --compare <baseline> <flag-on>`; grade
with the benchmark once the reference case has one (Step 3 gate above).

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
- [x] 🟩 Confirm no in-flight branch already edits `extraction/agent.py`
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

- [x] 🟩 **Step 1: Cache-aware cost accounting** — stop overstating spend ~3×.
  - [x] 🟩 Add a cache-read price per model to `config/models.json`
        (`cached_input_price_per_mtok`), defaulting to the full input price when
        absent so an unpriced model can never silently under-report.
  - [x] 🟩 Update `pricing.py`'s estimator to take cache-read tokens and bill
        them at the cached rate. Reuse the provider denominator logic already
        proven in `scripts/cache_report.py` — **OpenAI's `prompt_tokens` already
        includes cached reads; Anthropic's does not.** Getting this backwards
        double-counts or under-counts.
  - [x] 🟩 Keep the pre-cache estimate available alongside the adjusted figure;
        do not silently change what historical `run_agents.total_cost` rows mean.
  - [x] 🟩 Test `tests/test_pricing_cache_adjusted.py`: OpenAI-shaped and
        Anthropic-shaped inputs each produce the expected figure; a model with no
        cached rate falls back to full price; zero cache reads reproduces today's
        number exactly.
  - **Verify:** `./venv/bin/python scripts/cache_report.py 235` reports a SOFP
    cost materially below $1.53 (expect ~$0.50–0.60), and the same run with cache
    reads forced to 0 reproduces the old figure to the cent.

- [x] 🟩 **Step 2: One repeatable run-economics report** — the instrument every
  later "Verify" depends on, so we stop hand-rolling SQL per question.
  - [x] 🟩 Dev-only `scripts/report_run_economics.py <run_id>`: per agent —
        cache-adjusted cost, pre-cache cost, model requests
        (`node_kind='model_request'`, never raw graph-node counts), tool-call
        batches, prompt/completion/cache tokens, wall time, PDF pages viewed
        (calls and unique pages), and the static-prefix share of billed text.
  - [x] 🟩 A `--compare A B` mode printing two runs side by side with deltas, so
        an A/B is one command rather than a spreadsheet.
  - [x] 🟩 Read-only: no writes to the audit DB, no new tables, no schema change.
  - **Verify:** run it against the frozen reference case; every number it prints
    for run 235 reconciles with the raw `run_agents` / `run_agent_turns` rows.

- [x] 🟩 **Step 3: Turn the accuracy instrument on** — `eval_scores` is empty, so
  "improve accuracy" currently has no target to move.
  - [x] 🟩 Score the existing benchmark (1 benchmark, 102 gold facts) against
        every historical run matching its template set, via the existing
        `eval/grader.py` path — no new grading logic.
  - [x] 🟩 Record the resulting accuracy figures in this doc as the frozen
        accuracy baseline, alongside per-run cross-check pass/fail counts.
  - [ ] 🟨 If the existing benchmark covers too few runs to be a usable gate, say
        so explicitly here (done — see Status: the FINCO reference case has no
        benchmark and no complete run to seed from) and seed one more from a
        known-good run
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

- [x] 🟨 **Step 4: Row-oriented template summary** — `_summarize_template` in
  `extraction/agent.py` emits one line per *cell* (755 lines for SOFP, including
  `B1`/`C1`/`D1` header cells and the same label repeated across value columns).
  Emit one line per *row* with its label and which columns accept data.
  - [x] 🟩 Rewrite the renderer only. **No change to `TemplateField` parsing, to
        `read_template`'s contract, or to which rows are writable.**
  - [x] 🟩 Preserve verbatim, in the same words: the
        `[ABSTRACT (section header — do not write)]` marking, the `DATA_ENTRY`
        marking, and formula visibility. Gotcha #17 — the abstract marking is a
        load-bearing defence against the 2026-04-26 SOPL-Analysis incident.
  - [x] 🟩 Keep the existing process-global memoisation working
        (`_TEMPLATE_SUMMARY_CACHE`, keyed by `template_id` + mtime).
  - [x] 🟩 Flag `XBRL_TEMPLATE_SUMMARY_COMPACT` (default off during rollout, read
        at call time).
  - [x] 🟩 Extend `tests/test_template_reader.py`: existing
        `test_abstract_rows_marked_in_sopl_analysis` and
        `test_mpers_templates_carry_header_fills_like_mfrs` pass in **both** flag
        states; a new test pins that every row present in the verbose rendering
        is still present in the compact one, for MFRS and MPERS × Company and
        Group.
  - [x] 🟩 `tests/test_fill_workbook_abstract_guard.py` green in both states.
  - **Verify:** SOFP's `read_template` payload drops from 79,563 chars to under
    35,000 with no row lost; the reference case produces the same written facts
    as baseline; full suite green in both flag states.

- [x] 🟨 **Step 5: Move the template into the static prefix** — removes one model
  round trip per agent and makes the payload cache-eligible from request #1
  instead of #3.
  - [x] 🟩 Render the (now compact) summary into the agent's system prompt at
        construction time. It is fully determined by the template file, and 89%
        of agents fetch it unconditionally as their first act.
  - [x] 🟩 **Keep `read_template` registered as a tool.** On the new path it
        returns a short pointer ("template structure is in your instructions
        above") rather than being removed — an agent that calls it anyway must
        not fail, and older prompts must not break.
  - [x] 🟩 Update `prompts/_base.md` and any statement prompt instructing the
        agent to call `read_template` first; update the matching prompt pinning
        tests in the same commit (repo convention).
  - [x] 🟩 Face extraction agents only in this step. Notes agents already seed
        template labels into their system prompt (`create_notes_agent`) — leave
        that path alone.
  - [x] 🟩 Flag `XBRL_TEMPLATE_IN_PROMPT` (default off during rollout).
  - [x] 🟩 Flag-off identity pin: with both Phase 2 flags off, the agent factory
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

- [x] 🟨 **Step 6: Scout on by default** — scout exists to tell agents which pages
  to open, and 73 of the last 81 runs ran without it. Agents hunt instead: median
  2 view calls, mean 3.4, max 36.
  - [x] 🟩 Flip the default to on for CLI and web run creation, with an explicit
        off switch, surfaced in the run config as today.
  - [x] 🟩 **Do not** re-introduce page restriction. Hints stay advisory —
        gotcha #13, pinned by `tests/test_page_hints.py` negative assertions.
  - [x] 🟩 Price scout honestly in the comparison: it is a real agent (~$0.21,
        ~9 requests). The gate is total run cost, not view-call count.
  - **Verify:** reference case with and without scout, compared with
    `--compare`: unique pages viewed by face agents falls, **and cache-adjusted
    total run cost including scout** falls. If total cost rises, scout stays
    opt-in and we record that outcome here.

- [x] 🟩 **Step 7: Bound the fan-out** — `coordinator.py` creates one task per
  statement with no semaphore; a full run launches up to 15 concurrent agents,
  and real 429 rate-limit errors are already in `agent_events`.
  - [x] 🟩 Add a configurable concurrency cap (`XBRL_MAX_CONCURRENT_AGENTS`,
        default high enough to be a no-op for today's 5+5 shape) around agent
        launch in `coordinator.py` and `notes/coordinator.py`.
  - [x] 🟩 **Preserve every existing lifecycle guarantee**: independent per-agent
        cancellation, `task_registry` registration for the abort API, the
        `CancelledError` grace-period path, and the sentinel push in `finally`
        (gotcha #10). A queued-but-not-yet-started agent must still reach a
        terminal status.
  - [x] 🟩 Extend `tests/test_coordinator.py`: a cap of 2 with 5 statements still
        completes all 5; Stop-All while agents are queued leaves none `running`.
  - **Verify:** a capped run of the reference case completes with identical
    results; `tests/test_stop_all_preserves_partial.py` green.

---

### Phase 4: Structural — Only If the Numbers Still Justify It

Entry gate: Phases 1–3 are live and their savings recorded in this doc. Both
steps are judged against the **post-optimisation** baseline, not today's.

- [ ] 🟨 **Step 8: Compaction-vs-cache probe** — measurement, then a decision. No
  behaviour change in this step.
  SOFP's cache-read tokens fall in *absolute* terms mid-run (37.5k → 31.1k, and
  57.0k → 31.9k) while the prompt grows. That is what a changed prefix looks
  like, and `extraction/history_processors.py` rewrites earlier messages by
  design. If confirmed, we may be paying full price on a ~60k-token prefix to
  trim a payload that was already heavily discounted.
  - [x] 🟩 Log cache-read deltas immediately before and after each history
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
- **Steps 2 and 3:** dev-only scripts. Step 2 is read-only. Step 3 WRITES
  `eval_scores` rows (the same upsert the run-completion hook uses); rolling
  back means deleting the rows for the benchmark
  (`DELETE FROM eval_scores WHERE benchmark_id = 2`).
- **Step 7 (semaphore):** set the cap high enough to be a no-op, which reproduces
  today's unbounded behaviour exactly.
- **State to check after any rollback:**
  - flag-off agent factory snapshot tests green (Step 5's identity pin)
  - `./venv/bin/python -m pytest tests/ -n auto` green
  - a fresh run of the reference case shows the baseline tool sequence
    (`read_template > view_pdf_pages > … > save_result`) and a normal Telemetry
    tab
  - no `run_agents` row left in `running` (gotcha #10)
