# Implementation Plan: Agent Efficiency and Recovery Portfolio

**Overall Progress:** `45%` — 2026-07-23: Phase 0 measurement ran on real data
and its gate DECIDED the portfolio (see Results below); Phases 1A + 2 are
implemented behind default-off flags; Phase 4A (rules, schema v34, CLI resume,
drills) is implemented flag-gated. Remaining work is operator-gated: the
replay corpus + live A/B for the 1A/2 decision gates, and one live resume
canary for 4A.
**PRD Reference:** none — decision instruments are `docs/PLAN-codemode-spike.md`
(closed DELETE, 2026-07-21) and `docs/PLAN-pydantic-ai-v2.md` §D.5. This is v2 of
the 2026-07-23 draft, revised after a same-day peer review that verified every
claim against the codebase and telemetry DB (deltas listed below).
**Last Updated:** 2026-07-23
**Owner decision required:** approve Phase 0 only. Step 0.1 is the single code
change requested now; everything else is measurement whose output decides what
(if anything) gets built.

## Summary

The SOFP CodeMode spike failed because it wrapped a code-writing layer around a
workflow whose expensive steps are PDF vision and accounting judgement — steps a
script cannot collapse. This plan pursues the same goals (cheaper, faster,
recoverable runs) as a portfolio of independent enhancements, each with its own
flag, pre-registered gate, and one-commit rollback: reviewer context reduction,
tool-schema lazy loading, a read-only investigation bundle, a conditional
reviewer-only CodeMode re-spike, and stage-level run resume. **Measurement comes
first and may legitimately kill the economics phases** — the current data
suggests the reviewer is already cheap, and the recovery work (Phase 4A) likely
carries more value than any token saving.

## What the Peer Review Changed (v2 deltas — audit trail)

1. **Phase 0 was unrunnable as drafted.** It planned to query `run_agent_turns`
   for reviewer passes, but the reviewer persists **zero** per-turn rows — only
   end-of-pass rollups. All 22 reviewer rows (stored under
   `statement_type='CORRECTION'`, not "REVIEWER") have no turn rows. The shared
   loop (`agent_runner.run_agent_loop`) already collects the rows in memory
   (`_turn_records` in `server._run_reviewer_pass`); the pass simply never calls
   `repo.insert_agent_turns`. Step 0.1 now fixes that first.
2. **Phase 1A's premise was wrong.** The draft protected "existing stage-aware
   history processors" and the run-126 image protections — both live on the
   *extraction* agents (`extraction/history_processors.py`). The reviewer agent
   is built with **no history processors at all** (`create_reviewer_agent` passes
   no `capabilities=`), so every viewed PDF page image is re-billed on every
   later request with nothing trimming it. That is now a first-class measured
   candidate (Step 1A.2) instead of a mis-aimed caveat.
3. **Expectations set with real numbers** (Current-Data Snapshot below). Phase 4A
   is promoted: it may start once Step 0.1 lands, independent of the economics
   gate.
4. **Gate fixes:** Phase 1A no longer needs a 15% wall-time win it could never
   produce; percentage gates gained absolute floors (a 20% request reduction on a
   4-request pass is 0.8 of a request — noise).
5. **Reference fixes:** reviewer lift lives at `eval/grader.py::reviewer_lift`
   (there is no `eval/reviewer_lift.py`); `XBRL_STAGE_RESUME` is now named in its
   phase, not only in rollback; the Phase 4A "source document hash" check is new
   work (no hash is recorded today — `runs` stores only `pdf_filename`; the hash
   must be computed from the parent's kept `uploaded.pdf`).

## Current-Data Snapshot (2026-07-23, `output/xbrl_agent.db`)

These numbers frame every gate below. They are the "before" picture, not decision
data — Phase 0 produces the decision data.

- **22 reviewer passes** on record (`run_agents.statement_type='CORRECTION'`):
  mean cost **~$0.39** (max $1.30), typically 3–4 turns, mostly clean-run
  spot-checks. **Zero per-turn telemetry rows** (v2 delta 1). 17
  `CORRECTION_conversation_trace.json` files exist on disk for historical mining.
- **SOFP extraction alone costs ~$1.60–1.90/run** (spike Phase 0 baseline). The
  reviewer is roughly 20% of a run's cost; a 15% reviewer saving is **~$0.06 per
  run**. The economics phases must clear that honesty bar.
- **Corpus coverage for replay:** 227 MFRS-Company runs, 7 MPERS-Company,
  1 MPERS-Group, **0 MFRS-Group**. The Group and MPERS replay archetypes must be
  freshly generated (operator provides a Group PDF); they cannot be selected from
  history.
- **Implication:** Phases 1A/1B/2/3 may all die at the Step 0.3 gate. That is
  the gate working. Phase 4A (recovery) is valuable regardless of these numbers.

## Results — Step 0.2/0.3 measurement + gate decision (2026-07-23)

`scripts/report_reviewer_costs.py` over all 17 on-disk reviewer traces + 22
rollup rows (see the script for caveats: end-state traces, text-char shares,
image weight in bytes):

| metric | median | p75 |
|---|---|---|
| model requests | 4.0 | 6.0 |
| read-only tool calls | 4.0 | 8.0 |
| PDF view calls (pages) | 2.0 (21) | 2.0 (36) |
| write / verify calls | 0 / 0 | 0 / 0 |
| stage cost $ | 0.381 | 0.414 |

Billed text-char shares (sum over passes): **read-tool returns 61.2%**,
system prompt 32.2%, everything else < 6%. Image payloads: **468 MB
introduced, 916 MB billed (2.0× overall; 1.3× median, up to 8.5× on heavy
passes)** — images dominate total billed input. Incidental finding fixed in
passing: reviewer cache rollups were silently 0 (never captured) — now
persisted with the per-turn rows.

**Gate decision (per the Step 0.3 bar):**

- **Image-history re-billing: PASS — the dominant lever.** Phase 1A
  proceeds with the stale-image processor as its primary candidate
  (implemented, default off).
- **Read-only investigation: PASS on the p75 tail** (8+ read calls vs 4
  requests; 61% of billed text). Phase 2 bundle proceeds (implemented,
  default off).
- **Tool-schema share: NOT MEASURABLE from traces** (schemas never appear
  in message history). Phase 1B stays deferred pending a live probe — not
  built.
- **Phase 3 entry gate: NOT MET.** Stays closed.
- Phase 4A proceeds on reliability grounds (independent of this gate).

## Key Decisions

- **Portfolio, not one release.** Each enhancement has its own env flag
  (repo kill-switch convention: `XBRL_FACT_BASED_CHECKS`, `XBRL_SPOT_CHECK`,
  …), its own pre-registered gate frozen before its first live run, and its own
  one-commit rollback. Failure of one does not block the others.
- **CodeMode must beat the best non-CodeMode design**, not the unoptimised
  baseline. Phase 3 runs only if Phase 0/2 evidence justifies it, and compares
  against the best adopted Phase 1/2 configuration.
- **"Deferred loading" ≠ pydantic-ai's "deferred tools."** Phase 1B concerns
  tools marked `defer_loading=True` discovered via tool search (verified present
  in our pinned pydantic-ai 2.9.0: `_tool_search.py`, native path for
  Anthropic/OpenAI-Responses, local `search_tools` fallback elsewhere). Our
  proxy path speaks Chat Completions via `OpenAIChatModel`, so **expect the
  local fallback** — every discovery costs an extra model round trip; the
  experiment must price that in.
- **Harness Step Persistence is not a checkpoint.** Its docs exclude graph-state
  restore and workspace snapshots; it offers settled message snapshots plus a
  tool-effect ledger an application may build on. Phase 4B treats it as one of
  two candidate mechanisms, not a given.
- **Costs are reported honestly:** every experiment table carries input, output,
  cache-read, cache-write tokens alongside the repo's pre-cache estimate; invoice
  data when available.
- **Reviewer telemetry label is `CORRECTION`** (`server.CORRECTION_AGENT_ID`).
  All queries and scripts in this plan use that label.

## Lessons Carried Forward from the SOFP Spike

1. Measure the reducible portion first — a fast tool call is irrelevant if the
   model must still reason between calls.
2. A later call is collapsible only when it does not depend on model
   interpretation of an earlier result. Verification and PDF reading are
   judgement boundaries.
3. Test model-natural argument construction (the spike's fake-model tests used
   dict literals Monty accepted; the live model built a `list[dict]` variable it
   rejected against `list[FactWrite]`).
4. Generated code has a token cost — count script tokens, retries, compiler
   errors, not just outer tool calls.
5. Flag-off identity needs a direct pin (factory snapshot + fresh-subprocess
   import check), not "suite is green."
6. A failed workbook dominates modest savings. Completion and accuracy are hard
   gates; economics cannot compensate.

## Non-Negotiable Safety Boundaries (verified against code, 2026-07-23)

- Canonical mode remains the only extraction → review → export pipeline
  (gotcha #21).
- `apply_fixes` and `mark_not_disclosed` remain the only reviewer fact-write
  paths; every write passes the family-scoped grounding/no-plug guard
  (`classify_apply_fix_guard`); both are already batched (list-taking,
  per-item isolation) — write-side batching is **done**, this plan adds none.
- `snapshot_facts` still runs before the first reviewer write; Revert to
  original stays valid.
- `view_pdf_pages` remains a normal multimodal tool. Page images must reach the
  model's own eyes — never a text sandbox or host bundle.
- `verify_fixes` remains an ordinary tool after writes; nothing may conceal its
  result from the model.
- Group entity scope and MFRS/MPERS template-family scoping stay explicit
  (`ReviewerDeps.filing_standard` / `filing_level`, `_family_prefix`).
- Reviewer turn caps (dynamic 8–25, `compute_reviewer_turn_cap`) stay below the
  pydantic-ai 50-request ceiling (gotcha #18).
- Tool events, token telemetry, failure traces, and terminal run statuses stay
  honest (gotchas #6, #10, #20).
- No resumption mechanism may blindly replay an effect whose prior outcome is
  unknown.
- **No experiment reopens the deleted extraction CodeMode seam.**

## Pre-Registered Portfolio Decision Rules

Frozen before the first live experimental run of each phase. The product owner
may revise a phase's numbers any time before that phase's first flag-on run.

### Quality gate shared by every reviewer enhancement (all required)

- No reduction in benchmark reviewer lift where gold is available
  (`eval/grader.py::reviewer_lift`).
- No additional newly-failing cross-checks after review.
- No increase in ungrounded/rejected write attempts above the baseline band
  (`ReviewerDeps.rejections` tally).
- No unsafe write, template-family leak, entity-scope leak, or lost revert
  snapshot.
- Operator acceptance no worse on cases without gold.
- A failure remains diagnosable from telemetry + trace alone.

### Economics classifications (shared default)

- **ADOPT:** quality passes AND mean stage cost and wall time each improve ≥15%,
  or one improves ≥20% with the other regressing ≤3%.
- **KEEP as off-by-default experiment:** quality passes, both in the 8–15% band,
  credible path to more.
- **DELETE:** any quality regression, any outright failure absent from baseline,
  savings <8% on both axes, or >5% regression on either.
- **Absolute floors (v2):** any gate phrased as "requests fall X%" also requires
  **≥1 whole model request saved on the median eligible case**. Phase 1A uses
  its own gate (below) — a prompt-only change cannot move wall time 15% and is
  not held to it.

---

## Tasks

### Phase 0: Instrumentation, Measurement, and Replay Foundation

- [x] 🟩 **Step 0.1: Persist reviewer per-turn telemetry** (2026-07-23:
  `outcome["turn_records"]` → `insert_agent_turns` at both finalize sites;
  manual re-review REPLACES via `repo.replace_agent_turns`; cache rollups
  captured too — pinned by `tests/test_reviewer_turn_telemetry.py`) — the
  blocker fix.
  `run_agent_loop` already fills `_turn_records` in `_run_reviewer_pass`; the
  pass computes rollups from it and discards it.
  - [ ] 🟥 After `finish_run_agent` on the CORRECTION row, call
    `repo.insert_agent_turns(db_conn, run_agent_id, _turn_records)` — wrapped in
    the same advisory try/except as the extraction/notes sites (telemetry must
    never fault a run; gotcha #10 shape). All exit paths (success, exhausted,
    wall-clock, exception) persist whatever turns ran — mirroring the run-168
    fix that made rollups exit-path-complete.
  - [ ] 🟥 Scope: face reviewer (CORRECTION) only. Note in code comment that the
    notes reviewer/formatter share the gap; they are Phase 5's problem.
  - [ ] 🟥 Test `tests/test_reviewer_turn_telemetry.py`: mocked reviewer pass →
    N `run_agent_turns` rows keyed to the CORRECTION `run_agent_id`, with
    `node_kind` populated; failure path still persists partial turns.
  - **Verify:** test green; one live reviewer pass shows turn rows in the
    Telemetry tab (no frontend change needed — the tab already renders v8 rows).

- [x] 🟩 **Step 0.2: Historical trace-mining report** (2026-07-23:
  `scripts/report_reviewer_costs.py`, run on the live output dir — table in
  Results above) — a baseline from what already exists, while fresh per-turn
  data accumulates.
  - [ ] 🟥 Dev-only script `scripts/report_reviewer_costs.py`: parse the 17
    on-disk `CORRECTION_conversation_trace.json` files + the 22 rollup rows.
    Classify per pass: model requests, `view_pdf_pages` calls (+ pages, + an
    estimate of image re-billing = images in history × later requests), read
    tool calls (`read_facts`/`list_facts`/`find_candidate_rows`/
    `trace_cascade_source`/`search_pdf_text`/`lookup_definitions`/`calculator`),
    write calls, `verify_fixes` calls, guard rejections (`fix_rejections`).
  - [ ] 🟥 Respect the gotcha #6 trace caveat: traces show end-state history.
    The reviewer has no compaction processors, so end-state ≈ full history —
    state this assumption in the report header.
  - **Verify:** script runs against the live output dir; median + p75 activity
    table lands in the Results section of this doc.

- [x] 🟩 **Step 0.3: Activity inventory + THE GATE** (2026-07-23: decision
  recorded in Results above — images PASS, read-bundling PASS on the tail,
  1B not measurable/deferred, Phase 3 closed) — decide which economics
  phases (if any) are worth building.
  - [ ] 🟥 Combine Step 0.2's historical mining with fresh Step 0.1 per-turn
    rows (target ≥20 passes total, which the existing 22 + new runs satisfy).
  - [ ] 🟥 Report median and p75 reviewer separately; never only a pathological
    run. Use `node_kind='model_request'` for round trips — never raw graph-node
    counts.
  - [ ] 🟥 Explicitly size: (a) read-only investigation share, (b) tool-schema
    share of input tokens, (c) image-history re-billing share, (d) packet/prompt
    share.
  - **Gate:** proceed with 1A/1B/2 only for the components that are a material
    share of reviewer cost (suggested bar: ≥20% of median-pass input tokens or
    ≥2 model requests on the p75 pass). If PDF vision dominates everything,
    skip straight to Phase 4A and record that outcome here.
  - **Verify:** gate decision written into this doc with the table beside it.

- [ ] 🟥 **Step 0.4: Frozen reviewer replay corpus** (needed only if the gate
  passes for any of 1A/1B/2, or if Phase 3 is later authorised).
  - [ ] 🟥 ≥10 unique pre-review cases: SOFP imbalance/duplicate, cross-statement
    mismatch, wrong/missing leaf, misclassification (clear + rewrite), clean-run
    spot check, Group dual-scope, MPERS, one should-end-in-human-flag.
  - [ ] 🟥 **Group and MPERS cases must be freshly generated** (snapshot: 0
    MFRS-Group runs exist; operator supplies a Group PDF). Prefer
    benchmark-linked runs so `reviewer_lift` can score results.
  - [ ] 🟥 Freeze per case: source PDF bytes (hash), run config, pre-review
    `run_concept_facts`, cross-check packet, concept/template metadata. Manifest
    with hashes checked into `data/evals/`.
  - **Verify:** manifest exists; re-reading a case reproduces identical inputs
    byte-for-byte (hash check in a unit test).

- [ ] 🟥 **Step 0.5: Reviewer replay command** `scripts/replay_reviewer.py`.
  - [ ] 🟥 Clone the frozen case into a temp DB + temp output dir (concept-tree
    bootstrap via the existing importer path; never a production DB in place).
  - [ ] 🟥 Reconstruct `ReviewerDeps` (small: db_path, run_id, filing level/
    standard, pdf_path, verify_scope) and drive the real
    `create_reviewer_agent` + `run_agent_loop` — no server routes.
  - [ ] 🟥 Persist the same trace + per-turn telemetry shape as a live pass;
    emit one machine-readable comparison row per case; support 2 repeats per
    case/condition and interleaved condition order.
  - **Verify:** deterministic fixture tests for cloning/scoping; one live replay
    whose trace + DB diff match a manual reviewer rerun.

- [ ] 🟥 **Step 0.6: Pin the baseline.** Run the corpus on current reviewer
  code; capture quality, requests, tokens, cost, cache behaviour, wall time,
  tool mix, flags, guard rejections, failures. Manually review non-gold cases.
  - **Verify:** baseline table in this doc; success criteria for downstream
    phases frozen at this moment.

### Phase 1A: Reviewer Prompt and Context Reduction

Premise (corrected): the reviewer has **no** history processors today. Its
big-ticket context items are the system-prompt review packet, tool schemas,
retained tool results, and — uniquely — every viewed page image, retained
forever. Compaction is deterministic and free; no LLM summarisation.

- [ ] 🟥 **Step 1A.1: Token-account the reviewer request** — dev-only report
  splitting first and median-later requests into: static instructions, tool
  schemas/docstrings, review packet, fact summary, image payload, retained tool
  results, warning/limit nudges, output/retry text. Verbatim content stays in
  trace files (gotcha #6) — nothing heavy enters SQLite.
  - **Verify:** report over ≥5 replay cases; shares sum to ~100% of billed input.
- [x] 🟩 **Step 1A.2: Image-history policy (the new first-class candidate)**
  (2026-07-23: `correction/history_processors.py::strip_stale_reviewer_images`
  — keeps the newest TWO view batches (the cross-reference pattern; median
  passes untouched, the 8.5× tail trimmed), registered only when
  `XBRL_REVIEWER_COMPACT_CONTEXT` is on; pinned by
  `tests/test_reviewer_compact_context.py`. The replay-corpus quality proof
  remains open — the flag stays off until it runs.)
  Design a reviewer-specific stale-image rule — e.g. keep the most recent
  view's images, replace older ones with a "page N viewed earlier" placeholder
  once the model has acted on them. **Do not blind-copy extraction's
  processors** — they key on extraction domain events (first successful write)
  and encode the run-126/scanned-PDF thrash fixes for a different loop shape;
  the reviewer needs its own safety argument, proven on the replay corpus
  (no quality loss on the scanned-PDF and Group cases specifically).
  - **Verify:** replay corpus quality gate green with the rule on; token delta
    quantified per case.
- [ ] 🟥 **Step 1A.3: Deterministic compaction of measured duplication** —
  applied individually, only where Step 1A.1 shows waste: stable rules in the
  cacheable system prefix; run-specific packet in the message tail; repeated
  full fact listings replaced by IDs + summaries when retrievable via
  `read_facts`/`list_facts`; paging footers on truncated results. No-plug,
  grounding, scoping, disposition, and limit-warning text preserved verbatim
  (pinning tests updated in the same commit).
  - **Verify:** each compaction has a before/after token measurement on replay.
- [x] 🟩 **Step 1A.4: Flag + identity pins** (2026-07-23: flag-off ⇒ zero
  ProcessHistory capabilities, pinned structurally). `XBRL_REVIEWER_COMPACT_CONTEXT`
  (default off, read at call time). Flag-off factory snapshot: instructions,
  tools, capabilities, model settings byte-identical. Interleaved
  baseline/compact replay.
  - **Verify:** snapshot test green in both flag states; full suite green.

**Phase 1A gate (own gate — v2):** quality gate passes; mean reviewer input
tokens fall ≥10%; stage cost falls ≥8%; wall time regresses ≤3%; model requests
rise ≤3%; cache-read rate reported — a lower nominal prompt with a materially
worse cache-hit rate is investigated before adoption.
**Rollback:** unset the flag; remove the old rendering branch only in a later
cleanup commit after a live run + full suite.

### Phase 1B: Tool Search / Lazy Tool-Schema Loading Spike

- [ ] 🟥 **Step 1B.1: Measure schema weight; select candidates.**
  Always-visible: `view_pdf_pages`, `apply_fixes`, `mark_not_disclosed`,
  `verify_fixes`, `raise_flag`. Deferred candidates: `lookup_definitions`,
  `list_facts`, `find_candidate_rows`, `trace_cascade_source`,
  `search_pdf_text`, possibly `calculator`; `read_facts` only if evidence shows
  it is not used on nearly every pass. Never defer a core tool to inflate the
  reported saving.
  - **Verify:** per-tool schema token table from Step 1A.1's accounting.
- [ ] 🟥 **Step 1B.2: Provider compatibility matrix** — scratch agent under
  `agent.iter()` + the real model factory: direct OpenAI, direct Anthropic,
  direct Google, local LiteLLM proxy (expected: **local `search_tools`
  fallback**, since our proxy path is Chat Completions — price the extra round
  trip), Windows enterprise proxy when available. Record: native vs local, tool
  events shape, whether revealed tools persist across turns, prompt-cache
  stability.
  - **No-go:** any path requires replacing `run_agent_loop`, hides tool
    events/usage, breaks `end_strategy='early'`, or behaves inexplicably in the
    trace.
  - **Verify:** matrix table in this doc; explicit go/no-go recorded.
- [ ] 🟥 **Step 1B.3: Product seam** — `XBRL_REVIEWER_TOOL_SEARCH` (default
  off); current pydantic-ai capability only (no Harness dependency); factory
  stays single-sourced; flag-off import + factory-snapshot tests.
  - **Verify:** snapshot green both flag states; replay A/B rows produced.

**Phase 1B gate:** quality passes; schema/input tokens fall ≥15% on
pre-discovery requests; mean model requests rise ≤0.25/pass; total stage
economics meet the shared rule; discovery failures/wrong-tool selections no more
frequent than baseline. **Delete trigger:** search adds a request on most cases
and saves less than that request costs.

### Phase 2: Read-Only Reviewer Investigation Bundle

The highest-confidence economics enhancement: write batching is already done;
this supersedes the optional read-side cut line in
`docs/PLAN-batched-write-tools.md` with a measured, reviewer-specific design.

- [x] 🟩 **Step 2.1: Derive real call shapes from traces** (2026-07-23: the
  Step 0.2 report gives the read-call distribution — median 4, p75 8-19,
  dominated by list_facts/trace_cascade_source/read_facts sequences) —
  cluster baseline
  replay traces by repeated read sequences; count combinations; reject a
  do-everything payload if most fields would be empty.
  - **Verify:** shape-frequency table; chosen contract justified by it.
- [x] 🟩 **Step 2.2: Define the bundle contract** (2026-07-23: implemented in
  `correction/reviewer_agent.py` — `InvestigateItem` + 
  `run_investigation_bundle`, 12-item cap, 4k/20k char budgets with explicit
  continuation footers, sequential execution, per-item isolation, family
  scoping from deps) — one ordinary guarded tool
  `investigate_review_items(items=[...])`; each item a small discriminated kind
  with primitive args (`read_fact`, `list_sheet`, `find_candidates`,
  `trace_cascade`, `lookup_definitions`, `search_pdf_text`, `calculate`).
  Rules: read-only/side-effect-free; every op calls the existing helper (no
  duplicate resolution/scoping logic); family prefix + scopes from
  `ReviewerDeps`, never model-supplied; per-item isolation (one bad item never
  discards siblings — the `apply_fixes` precedent); stable item IDs; bounded
  count + output budget + paging footer; sequential execution unless helpers
  are proven read-only-thread-safe (correctness wins — host calls are cheap);
  envelope format (JSON vs compact text) chosen by measured token cost.
  - **Verify:** contract doc section + reviewed by owner before implementation.
- [x] 🟩 **Step 2.3: Judgement boundaries stay outside** (2026-07-23: no such
  kinds exist; negative test pins it) — bundle never
  includes `view_pdf_pages`, `apply_fixes`, `mark_not_disclosed`, `raise_flag`,
  `verify_fixes`, or any mutation. Enforced by construction and a negative
  test.
  - **Verify:** negative test green.
- [x] 🟩 **Step 2.4: Prompt + observability** (2026-07-23: code-injected
  addendum, flag-gated; ordinary tool events unchanged) — one short prompt
  paragraph:
  batch only already-known independent reads; sequential judgement remains
  legitimate. Standard tool events via `run_agent_loop`; per-item timings in
  trace metadata only if the ordinary event lacks detail (do not recreate the
  deleted CodeMode ledger unnecessarily).
- [x] 🟩 **Step 2.5: Tests** (2026-07-23:
  `tests/test_reviewer_investigation_bundle.py` — 8 tests incl. parity,
  scoping, no-mutation, bounds, flag-off identity) — family scoping across
  MFRS/MPERS ×
  Company/Group; mixed success/error batch; bounds/truncation/paging;
  no-mutation assertion (facts, flags, snapshots, files unchanged); parity with
  individual tools' outputs; prompt pin; flag-off factory identity
  (`XBRL_REVIEWER_INVESTIGATION_BUNDLE`, default off).
  - **Verify:** all green; flag-off suite fully green.

**Phase 2 gate:** quality passes; read-only tool turns fall ≥25% on eligible
cases; total reviewer model requests fall ≥15% **and ≥1 whole request on the
median eligible case**, or stage cost + time meet the shared ADOPT rule; <10%
of bundle items unused/immediately-repeated (else the contract gathers
speculative noise); no increase in PDF pages viewed. **Rollback:** unset flag;
no schema/data migration.

### Phase 3: Conditional Reviewer CodeMode Re-Spike

**Entry gate — do not start by default.** Only if Step 0.3 shows diverse
read-only chains the fixed bundle cannot express, or Phase 2 lands useful but
sub-threshold savings with traces showing more scriptable work. Baseline = best
adopted Phase 1/2 configuration.

- [ ] 🟥 **Step 3.1: Resolve the old schema failure first** — scratch agent,
  exact pinned Harness/Monty versions, model-natural patterns: dynamically
  built list variables, comprehensions, JSON-decoded lists, helper-appended
  items, TypedDict-likes, keyword-only calls, malformed args. Primitive/JSON
  contracts only — no nested pydantic-model params unless Monty's checker
  accepts the dynamic forms live models actually emit.
  - **No-go:** any normal list-building pattern produces retry storms or forces
    giant literals at the call site.
  - **Verify:** findings + explicit go/no-go in this doc before any product
    wiring.
- [ ] 🟥 **Step 3.2: Read-only sandbox allowlist** — calculator, definitions,
  read/list facts, candidate rows, cascade trace, PDF text search. Always
  ordinary: `view_pdf_pages`, all writes, `raise_flag`, `verify_fixes`. No
  mutation route by construction → no partial-write semantics at all.
- [ ] 🟥 **Step 3.3: Seam + telemetry** — `XBRL_REVIEWER_CODE_MODE` (default
  off); lazy import; exact pin in a reviewer-specific requirements file; one
  branch point in `create_reviewer_agent`; normal `agent.iter()` +
  `run_agent_loop` preserved; outer `run_code` calls, nested host calls,
  type/runtime retries, script output tokens recorded separately; reuse
  Harness's successful-call metadata, custom ledger only for gaps; ledger into
  the conversation trace, never SQLite.
- [ ] 🟥 **Step 3.4: Interleaved A/B on the frozen corpus** — A = best
  non-CodeMode config, B = A + read-only CodeMode; same model/corpus/provider/
  sitting; ≥2 repeats per case/condition; reviewer stage only.

**Phase 3 gate (stricter):** zero missing reviews or unhandled sandbox
failures; zero mutating sandbox calls by construction; ≤1 type/runtime retry
per 20 scripts; mean model requests fall ≥20% **and ≥1 whole request at the
median**; cost and wall time each ≥15% better than the best non-CodeMode
baseline; completion tokens rise ≤10% unless total cost still clears 20%; one
deliberate failure diagnosable from trace/metadata alone. **Any miss → delete
the seam and the optional dependency in the same decision round.**

### Phase 4A: Stage-Level Resume and Reuse

May start once Step 0.1 lands — independent of the economics gate. Durable
facts, per-agent statuses, traces, partial merge, and deterministic re-export
are stronger restart anchors than conversation state; stage-level recovery also
never resumes inside an unknown write.

- [x] 🟩 **Step 4A.1: Define reusable boundaries** (2026-07-23: pure
  functions in `recovery/stage_resume.py` — `build_resume_plan` encodes
  every rule below; drills in `tests/test_stage_resume.py`) (face
  statements only).
  Reusable requires ALL: parent `run_agents.status='succeeded'`; canonical
  facts exist for the expected family/scopes; **source identity matches — new
  work: no hash is recorded today, so compute sha256 of the parent's kept
  `uploaded.pdf` at resume time and store it on the lineage row** along with
  filing standard/level/variant/config match; no in-flight tool effect at
  agent end; trace/artifacts readable (or a structured warning). Never reuse:
  failed agents; partial writes without terminal save; reviewer-modified facts
  as raw extraction (unless mode says "resume from reviewed state"); notes
  cells (first release); anything from a different source hash or family.
  **v1 honesty notes:** "no in-flight effect" and "trace readable" are
  proxied by `status='succeeded'` (the save gate means a succeeded agent
  finished its terminal save; trace files are advisory for facts-based
  reuse). There is NO "resume from raw extraction" mode yet — a parent that
  ran the reviewer contributes its REVIEWED (final) facts, labelled as such
  in the plan reason and in every copied fact's provenance prefix. Notes
  are fully out of scope: never reused AND not rerun by the child (the CLI
  preview warns when the parent had notes).
  - **Verify:** rules table reviewed by owner; encoded as pure functions with
    unit tests before any wiring.
- [x] 🟩 **Step 4A.2: Lineage schema (v34)** (2026-07-23: `run_lineage`
  table, pure CREATE-IF-NOT-EXISTS walk-forward, pinned by
  `tests/test_db_schema_v34.py`) — additive nullable table/columns
  linking child → parent + reused/rerun statement IDs + parent source hash,
  per gotcha #11 rules (nullable-or-default, no CHECK on status).
  `tests/test_db_schema_v34.py` pins the migration step.
  - **Verify:** migration walks a v33 DB up cleanly; fresh init identical.
- [x] 🟩 **Step 4A.3: Resume flow, CLI-first** (2026-07-23: `run.py
  --resume-from N` — prints the reuse/rerun preview always (verified live
  against run 233); launching the child needs `XBRL_STAGE_RESUME=1`;
  staging is one rollback-safe transaction; the child rides the proven
  `existing_run_id` path the repeat-group runner uses. Two v1 caveats,
  deliberate: the LIVE stream's merge covers rerun statements only — the
  download re-export (which reads DB rows) assembles the complete workbook;
  and in-stream cross-checks scope to the rerun statements — use "Re-run
  checks" post-hoc for full-set checks. A live canary remains the operator
  gate before any default-on.) — flag `XBRL_STAGE_RESUME`
  (default off during rollout).
  `./venv/bin/python run.py --resume-from <run-id>` (retry-failed is the
  implicit and only v1 mode — no separate flag).
  A resume creates a **new child run** (parent stays terminal + immutable);
  copies forward facts as new child-run rows with provenance (never shared
  mutable rows); copies/regenerates succeeded workbooks into the child output
  dir; reruns only failed/unfinished statements; export/merge/cross-checks/
  reviewer rerun in the child — with the two v1 caveats above (complete
  workbook via the download re-export; full-set cross-checks via post-hoc
  re-check); child reaches a terminal status through the existing lifecycle
  (gotcha #10 — incl. a pre-stream failure guard that marks the child
  failed/aborted rather than stranding it at 'running').
  - **Verify:** staging + refusal drills are unit tests; the end-to-end
    "4 reused + 1 rerun → child completes, History shows lineage" proof is
    the LIVE canary (operator gate) — not claimed done here.
- [x] 🟩 **Step 4A.4: Failure drills** (2026-07-23: encoded as unit drills in
  `tests/test_stage_resume.py` — selection, Stop-All partial, refusals,
  provenance, cross-family protection, transactional rollback; the LIVE
  drill run remains open with the canary) — one-of-five fails; Stop-All
  after two
  successes (reuse exactly those); source/config mismatch refused with plain
  reason; copy failure rolls back child staging transaction; re-export/merge
  failure leaves child terminal with facts intact; reviewer failure doesn't
  force re-extraction next time; Group/MPERS cannot copy cross-family.
  - **Verify:** each drill is a test; all green.
- [ ] 🟥 **Step 4A.5: UI (only after lifecycle proven)** — "Retry failed work"
  on the run page with reused-vs-rerun preview; refusal reasons surfaced;
  lineage in History/Overview. No new `role="tab"` (gotcha #7).

**Phase 4A gate:** 100% correct reuse/rerun selection in drills; no duplicate/
lost/cross-family facts; same final workbook/eval score as a clean rerun within
deterministic tolerance; ≥30% cost/time avoided when ≥half the statements were
already complete; parent immutable, lineage complete. **Rollback:** disable
`XBRL_STAGE_RESUME`; the additive schema sits inert.

### Phase 4B: Settled-Turn Continuation (research only)

- [ ] 🟥 **Step 4B.1: Evaluate two paths** — Harness `StepPersistence` (exact
  optional pin) vs a small repo-owned snapshot built from pydantic-ai public
  messages + existing trace conventions. Compare maturity, storage, media
  handling, `agent.iter()` fidelity, Windows support. Do not pick Harness
  because it exists.
- [ ] 🟥 **Step 4B.2: Resume safety policy** — continue only from settled
  snapshots (every call has a result); interrupted tool = `unknown_after_crash`,
  never auto-replayed; reads may rerun after identity checks; writes require
  durable-state inspection + fresh verify; `ReviewerDeps` rebuilt from DB +
  files (history ≠ state); page images durably externalised or regenerated from
  the unchanged PDF (existing traces elide binaries — not resumable snapshots);
  continuation = new framework run ID with lineage.
- [ ] 🟥 **Step 4B.3: First scope** — reviewer read-only failures before any
  write. Nothing else until idempotency drills prove it.
  - **Verify:** feasibility report in this doc; implementation only on explicit
    GO.

**Phase 4B gate:** continuation succeeds from every settled boundary in the
matrix; unknown effects surfaced, never replayed; outcome matches clean-rerun
quality; saves ≥1 expensive model request on median eligible failures; storage
bounded, binaries stay on disk. **No-go:** needs graph/workspace restore the
library lacks, or a second lifecycle parallel to `run_agent_loop`.

### Phase 5: Conditional Expansion (notes reviewer / scout)

Do not start until one reviewer enhancement is adopted and stable live.
Note: the notes reviewer and formatter also lack per-turn telemetry rows —
instrument (Step 0.1 pattern) before measuring. Notes candidate scope:
consolidate independent reads (cells, template labels, coverage rows) via the
bundle pattern — existing notes batch tools may leave too little to win;
measure first. Scout: only on born-digital PDFs with ≥3 expensive text-only
orchestration turns; prefer a purpose-built host pipeline over model-authored
code. **Non-targets:** cross-check engine, export/merge/mTool, notes formatter,
face extraction (closed negative experiment).

---

## Test Matrix

Targeted per phase, plus for every adopted change:

- Reviewer: `tests/test_reviewer_tools.py`, `test_reviewer_agent.py`,
  `test_reviewer_pipeline.py`, `test_reviewer_versioning.py`,
  `test_reviewer_disposition.py`, `test_prompt_residual_plug_rule.py`,
  `test_max_agent_iterations_below_pydantic_cap.py`, `test_agent_tracing.py`.
- Lifecycle/persistence: `test_server_run_lifecycle.py`,
  `test_stop_all_preserves_partial.py`, canonical export + family-scoping
  tests, `test_db_schema_v34.py` (Phase 4A).
- New: replay, tool-search, bundle, resume, reviewer-turn-telemetry tests.
- Full gates: `./venv/bin/python -m pytest tests/ -n auto` and
  `cd web && npx vitest run` (frontend only when Phase 4A UI or shared event
  types change).
- Live gates: direct provider + local LiteLLM proxy; Windows enterprise proxy
  before enabling anything there; ≥1 Company MFRS, 1 Group, 1 MPERS reviewer
  case; one deliberate failure for trace/recovery validation.

## Telemetry and Reporting Contract

Every experiment table reports: model requests (`run_agent_turns` rows,
`node_kind='model_request'`, agent label `CORRECTION`); tool nodes
(`call_tools`, separate); input/output/cache-read/cache-write tokens; cost
(labelled pre-cache); wall time (`started_at`→`ended_at` + summed request
time); PDF views (calls + pages); read/write/verify call classification;
retries (validation, guard, sandbox type/runtime separately); quality
(reviewer lift, resolved/new failures, flags); completion outcome. Never merge
nested calls into request counts; never report raw Telemetry-tab graph-node
totals as round trips.

## Rollout Strategy

1. One enhancement per `feat/`-prefixed branch (repo convention).
2. Experimental flags env-only, default off; no Settings UI during a spike.
3. Replay corpus → one live canary → limited opt-in → promote one at a time.
4. After full suite + live matrix, default-on with a one-release kill switch.
5. Remove dead branches, experiment-only deps, and flags after the rollback
   window — no permanent seams.

## Rollback Plan

- **Any phase, instant:** unset its flag (all default off) — behaviour returns
  to today with zero code changes.
- **Step 0.1:** telemetry-only, advisory writes; revert the commit if needed —
  no schema change (v8 tables already exist).
- **Phase 4A:** disable `XBRL_STAGE_RESUME`; v34 lineage schema stays inert
  (gotcha #11 model — like `doc_conversions`).
- **Phase 3:** revert the seam commit + uninstall the optional dependency (the
  spike-proven shape).
- **State to check after any rollback:** flag-off factory-snapshot tests green;
  full suite green; a fresh run's Telemetry tab normal.

## Out of Scope (explicitly)

- Reopening extraction CodeMode in any form.
- LLM-based prompt summarisation (compaction stays deterministic and free).
- Notes cells in the first stage-resume release.
- Settings UI for any experimental flag.
- Windows enablement before the compatibility matrix covers it.

## First Decision Requested

Approve **Phase 0 only** — and within it, Step 0.1 is the only code change.
Steps 0.2–0.3 produce the numbers that decide whether 1A, 1B, or 2 is worth
building (or none). Phase 4A may be green-lit in parallel on reliability
grounds. Phase 3 CodeMode and Phase 4B continuation remain explicit second
decisions, never implied by approving this plan.
