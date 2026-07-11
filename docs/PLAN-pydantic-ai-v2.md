# PLAN — Pydantic AI V2: context, upgrade plan, and Harness learnings

**Status:** PEER-REVIEWED PROPOSAL (2026-07-12). Research capture + implementation plan.
No code change yet. Supersedes the earlier `PLAN-harness-learnings.md`
(folded in as Part D).

**Review note (2026-07-12, resolved after verification):** a peer review
validated this plan against the official V2 announcement, upgrade guide, PyPI
release history, and the live call sites. Its genuinely new catches are kept
below (B.2 items 5–7, B.3 items 6–9, the go/no-go section, failure drills).
Its headline claim — "the reviewed workspace reports pydantic-ai 0.8.1, so the
baseline is not reproducible" — was a **misdiagnosis of a known environment
quirk**: the reviewer imported via the bare system `python3`, which is a stale
Python 3.9 carrying pydantic-ai 0.8.1. The authoritative interpreter is
`./venv/bin/python`, which reports **1.77.0** (verified 2026-07-12; this
footgun is already documented in the project memory — always run tests via the
venv). The reviewer's underlying point survives in softened form: `>=1.77.0`
is a floor, not a pin, so U0 records the authoritative interpreter and adds a
constraints/lock artifact.

**One-paragraph summary:** Pydantic AI — the agent framework this whole
product is built on — shipped its V2 major version on 2026-06-23. We run
1.77.0 (venv-verified), so a full major-version jump is ahead of us. This
document is four things: (A) plain-language context on what V2
actually is; (B) a grounded audit of exactly what breaks in OUR code, by
file and line; (C) a phased upgrade implementation plan with verification
gates and a rollback story; (D) the independent "Harness learnings" — six
design patterns from Pydantic's companion batteries library that we
re-implement natively, which need **neither** V2 nor the Harness and can
ship any time.

---

## Part A — Context, in plain language

### A.1 Naming: three similar names, three different things

| Name | What it is | Our status |
|---|---|---|
| **Pydantic** (the validation library) | Checks that data has the right shape. Its own "2.0" shipped back in 2023. | Already on `2.12.5`. Nothing to do. |
| **Pydantic AI V1** | The agent framework we build on — runs our extraction agents, tools, retries. | Declared `>=1.77.0` (a lower bound, not a pin); the authoritative venv has **1.77.0** installed. Beware the stale bare `python3` (0.8.1) — always use `./venv/bin/python`. U0 adds a lock/constraints artifact. |
| **Pydantic AI V2** | The new major version of that framework. | **Not adopted. This plan.** |
| **Pydantic AI Harness** | An optional add-on library of ready-made agent parts for V2. | **Not adopting as a dependency** (Part D explains; we steal patterns instead). |

### A.2 What V2 is about

Two ideas, per the official announcement:

1. **"Capabilities"** — instead of passing an agent its tools, prompt
   add-ons, lifecycle hooks, and model settings as separate constructor
   arguments, V2 bundles them into composable units you attach as a list.
   Several V1 constructor arguments *only* exist as capabilities in V2 —
   including one we use (see B.2).
2. **A leaner core + the Harness** — the core library stays small and
   stable; fast-moving batteries (memory, guardrails, context management,
   checkpointing, code mode) live in the separate
   `pydantic-ai-harness` package. Default install now bundles only
   OpenAI / Anthropic / Google providers (all three are ours — no impact).

### A.3 Timeline and support window

- V2.0 stable: **2026-06-23**, after seven betas; releases are rapid.
  PyPI is the release authority at implementation time; do not encode an
  assumed “latest” minor in the dependency range without testing that exact
  artifact and recording it in a lock/constraints file.
- **V1 gets security fixes for at least 6 months** after V2 stable — i.e.
  a soft deadline around **year-end 2026** to be off 1.x. Not urgent, but
  not indefinite.
- Official upgrade path: bump to **latest V1 first** (1.100.0+, which
  emits deprecation warnings for everything V2 removes), clear the
  warnings, then flip to V2.

### A.4 Why we should care (and why not panic)

Care: the framework runs every agent in the product; staying current keeps
us on the security train, new model support (they add models within days
of release), and unlocks post-V2 features we actually want (checkpointing
— see Part C.4). Not panic: our model-construction layer is *already*
V2-shaped (CLAUDE.md gotcha #2 got us there early), the changes that do
hit us are mostly mechanical renames, and our ~3,100 pinning tests are a
strong safety net.

---

## Part B — Impact audit (grounded, 2026-07-12)

Sourced from the official changelog/upgrade guide cross-checked against a
grep audit of live code (`tests/` counted separately). Line numbers drift;
re-run the greps at implementation time. This is a **source audit** against
the venv-verified 1.77.0 install; API claims about V2 come from the official
changelog and should be re-proven against the exact locked version at U1/U2.

### B.1 Already V2-clean — no action

- **Model/provider construction** (`server.py::_create_proxy_model`,
  ~:1063–1121; `model_settings.py`): already `OpenAIChatModel` +
  `provider=`, consolidated `GoogleProvider`, `AnthropicModel`. Exactly
  the V2 shape.
- **Provider bundle:** OpenAI/Anthropic/Google stay in the slim default
  install.
- **No bare model-name strings** in live code (we construct Model objects
  explicitly, so V2's "bare `'gpt-5'` now raises" doesn't bite).
- **No `StreamedRunResult` / `run_stream` / `event_stream_handler`** in
  live code — our streaming is `agent.iter()` + `node.stream()`
  (`agent_runner.py`), so the convenience-method renames do not apply.
  The streamed **event schema does change**, however; see B.2 items 6–7.
- **Instrumentation v5 default:** we don't use pydantic-ai's
  instrumentation (we have our own tracing, gotcha #6) — no-op for us.
- **Parallel tool execution** stays the default (gotcha #22 unaffected).
- `tests/test_save_result_contract.py:292` already imports `RunUsage` —
  the new name exists in 1.77, so some of the rename is already available
  to do early.

### B.2 Breaking — must change (the real work)

1. **`Agent(history_processors=[...])` → `capabilities=[ProcessHistory(...)]`.**
   Three live call sites register our token-compaction layer this way:
   `extraction/agent.py:704`, `notes/agent.py:1344`, `scout/agent.py:1129`.
   The processor *functions* in `extraction/history_processors.py` should
   port as-is (pure functions); what changes is the registration wrapper.
   **This is one of the highest-risk changes** because that module encodes
   the run-126 / scanned-PDF thrash regression fixes — its pinning tests
   (`test_history_processors.py`, `test_history_processor_escalation.py`)
   are the gate, plus one manual scanned-PDF run.

2. **`agent_run.usage()` method → `.usage` property.** ~8 live call
   sites across 7 files: `coordinator.py:119`, `agent_runner.py:441`,
   `server.py:1764`, `notes/coordinator.py:867`,
   `notes/listofnotes_subcoordinator.py:765,796`, `scout/agent.py:1518`,
   `scout/notes_discoverer_vision.py:425`. Mechanical, but it's the
   token-telemetry spine (gotcha #6).

3. **Token field renames** `request_tokens → input_tokens`,
   `response_tokens → output_tokens` (`Usage → RunUsage`,
   `UsageLimits(request_tokens_limit=) → input_tokens_limit=`). Live
   reads in 6 files: `agent_runner.py` (3), `coordinator.py` (2),
   `notes/coordinator.py` (4), `notes/listofnotes_subcoordinator.py` (4),
   `scout/agent.py` (2), `server.py` (3). **The trap:** compatibility
   helpers in `agent_runner.py`, `server.py`, **and `notes/coordinator.py`**
   read via `getattr(u, "request_tokens", 0)` —
   under V2 that doesn't crash, it **silently reports 0 tokens** and the
   Telemetry tab quietly goes dark. Fix by preferring the new names with
   the old as fallback *now* (safe on 1.77, since 1.x added the new names
   as the primary API), which converts a silent failure into a no-op.

4. **Test fixtures using old names:** 9 files
   (`test_coordinator.py`, `test_sse_contract.py`, `test_notes_cost_report.py`,
   `test_face_transient_retry.py`, `test_save_result_contract.py`,
   `test_peer_review_hardening_2026_05_21.py`, `test_notes12_subcoordinator.py`,
   `test_notes_turn_timeout.py`, `test_notes_coordinator.py`). Mechanical
   rename in stub Usage objects.

5. **Generic dependency type defaults (`None → object`).** The official guide
   calls this type-checking-only, but one live annotation is affected:
   `scout/notes_discoverer_vision.py` uses `Agent[None, _VisionBatch]` in the
   builder return and parameter annotations. Decide whether `None` is a real
   dependency contract; if not, migrate both to `Agent[object, _VisionBatch]`
   and run the project's static type check (or add one if none exists).

6. **Tool-stream event result shape changed (`result → part`).** This is a
   live, high-impact migration item the original audit missed. Three
   production consumers (`agent_runner.py`, `scout/agent.py`, and
   `notes/listofnotes_subcoordinator.py`) read
   `FunctionToolResultEvent.result`; V2 requires `.part`. SSE contract tests
   also construct events with `result=`. Migrate on latest V1 if its
   compatibility API permits; otherwise make it an explicit U2 change. Prove
   tool name, call ID, result summary, and duration still reach telemetry.

7. **Output-tool events split from function-tool events.** V2 emits
   `OutputToolCallEvent` / `OutputToolResultEvent` for terminal output tools.
   Because `save_result` is the product's terminal boundary, verify whether it
   is registered as a normal function tool or an output tool in every agent.
   If output-tool events can occur, handle both event families or consciously
   exclude terminal events with a pinning test. “Core graph API” does not make
   the event schema immune to this change.

### B.3 Behavior changes — no compile error, needs a decision or a test

1. **`end_strategy` default flips `'early' → 'graceful'`.** We never set
   it, so we inherit the change: after a successful terminal/output tool
   call, other function tools requested in the same batch now *also run*
   (V1 skipped them). For our agents the terminal tool is `save_result` —
   under V2 a same-batch stray tool call would execute after the save.
   **Decision at U2:** pin `end_strategy='early'` explicitly on every
   `Agent(...)` to preserve V1 semantics through the upgrade (surgical,
   reversible), and evaluate `'graceful'` separately later. Don't change
   two things at once.
2. **Gotcha #18 (`request_limit=50`) — re-verify, likely unchanged.** The
   changelog renames only the *token* limits; the request-*count* default
   of 50 appears to survive. Our cap of 40 (`agent_tracing.py:65-114`,
   pinned by `test_max_agent_iterations_below_pydantic_cap.py`) must stay
   below whatever V2's default is. Action: read
   `pydantic_ai.usage.UsageLimits` source at U2 and update the pinning
   test's comment with the verified V2 value.
3. **`google-gla:` prefix strings must support the V2 rename** in
   `server.py:964` and
   `pricing.py:114` (string matching for routing/pricing, not provider
   construction). The official guide explicitly renames `google-gla:` to
   `google:`. Add the new prefix without prematurely deleting legacy/proxy
   spellings that still appear in stored runs or inbound configuration; pin
   routing and pricing tests for both compatibility inputs.
4. **Test-harness surface:** the suite leans heavily on
   `pydantic_ai.models.test.TestModel` (28 imports) and
   `models.function.FunctionModel` (~16). Both exist in V2, but any
   signature drift multiplies across the suite — this is why U1's
   "run the whole suite on latest V1 with warnings surfaced" phase exists.
5. **`openai:` prefix now means the Responses API** (`openai-chat:` for
   Chat Completions). We construct `OpenAIChatModel` explicitly, so live
   code is unaffected; only doc/test strings mention prefixes.
6. **Prepare callbacks returning `None` now hard-fail.** The current grep found
   no `prepare_tools=` or tool `prepare=` callback in live code, so this is a
   verified no-impact item, but retain the audit grep at U2 because new tools
   may land before the upgrade.
7. **Interrupted-message capture changed.** V2 makes
   `capture_run_messages()` retain partial interrupted messages. The current
   grep found no use, so no live impact; re-audit tests and tracing adapters at
   U2 rather than assuming exact-message-count assertions are absent forever.
8. **Model profiles changed from dataclasses to `TypedDict`.** No live profile
   reads/mutations were found. Construction-only use remains compatible, but
   re-run the profile grep at U2.
9. **Sequential-tool semantics changed.** A `sequential=True` tool becomes a
   per-tool barrier rather than serializing the whole batch. No live use was
   found, so this is presently no-impact; retain the audit because write-side
   effect ordering is load-bearing here.

### B.4 Explicitly out of scope for the upgrade

- Adopting capabilities beyond the forced `ProcessHistory` wrapper (no
  refactor of how agents assemble tools/prompts — that's a separate
  discussion, Part C.4).
- Adopting the Harness package (Part D covers why not).
- Any behavior change bundled with the version bump (`end_strategy`
  evaluation, deferred tool loading, CodeMode — all post-upgrade).

---

## Part C — Upgrade implementation plan

Run as its own focused branch (`chore/pydantic-ai-v2`), never bundled with
feature work. Each phase lands as separate commits; the suite must be
green at every phase boundary.

### Phase U0 — Preflight (no dep change) — 🟩 DONE 2026-07-12

> Completed on `chore/pydantic-ai-v2`. Gate: 3304 passed / 3 skipped
> (`-n auto`, 56s); `pip check` clean; `constraints.txt` committed;
> evidence bundle in `docs/BASELINE-pydantic-ai-v2.md`. Two findings:
> (1) the `agent_runner.py` / `server.py` helpers were ALREADY
> new-name-first — the only real silent-zero site was
> `notes/coordinator.py:64-65`, now fixed; (2) we also converted the
> *direct* old-name reads (coordinator/scout/subcoordinator — B.2 item 3's
> list) at U0 rather than U1, since the new names are primary on 1.77 —
> this pulls work forward, nothing is riskier. Benchmark scorecard NOT
> captured (needs a live run) — carried as an open item to the U1 gate.

- **Record the authoritative interpreter:** all commands in this plan run
  via `./venv/bin/python` (venv-verified pydantic-ai 1.77.0). The bare
  system `python3` is a stale 3.9 with pydantic-ai 0.8.1 — a documented
  footgun that produces phantom import errors and misleading version
  reads; never draw conclusions from it. Run
  `./venv/bin/python -m pip check` once and note the result.
- Add a reproducible dependency artifact: freeze the venv's resolved graph
  (`pip freeze` → a committed constraints file, or the repo's chosen lock
  format) so upgrades and rollbacks resolve the same transitive tree.
  `>=1.77.0` stays as the floor in `requirements.txt`; the constraints
  file is the pin.
- Capture a baseline evidence bundle: backend test count/results, frontend
  results, one representative benchmark scorecard, installed dependency
  snapshot, and the exact commit SHA. U3 compares against this bundle.
- Convert all three silent-zero compatibility helpers (B.2 item 3 —
  `agent_runner.py`, `server.py`, `notes/coordinator.py:64-65`) to prefer
  `input_tokens`/`output_tokens` with old-name fallback. Safe on 1.77.
- Rename token fields in the 9 test files' stubs to the new names (also
  valid on 1.77).
- Re-run the audit greps in Part B and update this doc if the surface
  moved.
- Gate: `./venv/bin/python -m pip check` clean; `./venv/bin/python -m
  pytest tests/ -n auto` green (`pytest-xdist>=3.8.0` is declared in
  `requirements.txt` and installed); `cd web && npx vitest run` green;
  baseline evidence bundle saved.

### Phase U1 — Bump to the latest V1 line (1.107.1 as of 2026-07-11) — 🟩 DONE 2026-07-12

> Locked 1.107.1; floor bumped to `>=1.107.1` (the code now REQUIRES it:
> `pydantic_ai.capabilities` + property-style `.usage` don't exist on
> 1.77); constraints.txt regenerated (256 pkgs); `pip check` clean.
> Deprecation census found exactly the four items in the audit, nothing
> unforeseen. All four cleared ON V1 — including the two the plan had
> scheduled for U2, pulled forward because 1.107 already ships the V2
> API shapes: (a) the three `history_processors=` registrations ported to
> `capabilities=[ProcessHistory(...)]`; (b) tool-event consumers moved
> `.result → .part`. Also `usage()` → `.usage` property everywhere, and
> test fakes updated to property-style usage (the `MagicMock(
> return_value=...)` fakes silently became int(...)=1 under property
> access — worth remembering when writing agent-run stubs).
> Gate: full suite 3315 passed / 3 skipped WITH
> `-W error::pydantic_ai._warnings.PydanticAIDeprecationWarning`.
> Live smoke run: 🟩 PASSED (run 220 — SOFP on FINCO, gpt-5.4 direct
> mode): extraction ok, cross-checks + reviewer pass ran, merged
> filled.xlsx, Success: True. Telemetry proof: 626,086 tokens on the
> SOFP agent across 18 per-turn rows + 159,745 on the reviewer — the
> silent-zero trap is closed end-to-end. Benchmark scorecard anchor:
> still open (needs a saved gold benchmark run via the web workspace).

- Install and lock the **exact latest V1** (1.107.1 per PyPI on
  2026-07-11; the V1 line still receives parallel maintenance releases,
  so reconfirm PyPI at implementation time and record the version
  actually locked), rather than a floating `>=1.100,<2` range.
- Run the suite with pydantic-ai deprecation warnings surfaced
  using Pydantic AI's own warning category where available; first run with
  warnings visible, then fail CI on that category. Avoid a brittle module-name
  filter because warnings can be emitted with caller-oriented stack levels.
- Fix every deprecation: `.usage()` → `.usage`, remaining old token names,
  event `.result → .part` where V1 exposes the compatibility shape, and
  whatever the locked V1 flags that this audit did not foresee (that's the
  point of the phase).
- Gate: full suite green with the warning filter on; one live smoke run
  (`python run.py data/FINCO-….pdf --statements SOFP` with a real key);
  `pip check` clean; resolved dependency snapshot committed.

### Phase U2 — Flip to V2 — 🟩 DONE 2026-07-12

> Locked 2.9.0 via constraints.txt (floor stays `>=1.107.1` — the code
> deliberately runs on BOTH 1.107.1 and 2.x, the best rollback story).
> **The flip itself was a non-event: the full suite passed on 2.9.0 with
> zero code changes** — clearing every deprecation on V1 first (U1) did
> its job. U2 verifications, all confirmed against the installed 2.9.0:
> gotcha #18's `request_limit` default is STILL 50 (our 40-cap safe; now
> asserted directly by a new pinning test); all three provider paths
> construct; `google-gla:` is REMOVED in V2 (`Unknown provider`) while
> `google:` parses — the U1 prefix-table addition was necessary.
> `Agent[None, _VisionBatch]` annotations kept as-is: the vision agent
> genuinely has no deps, so `None` is the real contract and stays valid
> on V2 (B.2 item 5 resolved as no-change). prepare_tools /
> capture_run_messages / model-profile / sequential greps re-run: still
> zero usage. CLAUDE.md gotcha #2 rewritten for the V2 reality.
> Gate: 3316 passed / 3 skipped on 2.9.0; pip check clean; V2 live smoke
> recorded below.

- Select one exact V2 minor after reviewing its release notes (2.9.0 is
  latest per PyPI on 2026-07-11; releases land weekly, so reconfirm at
  implementation time). Lock that exact version for the migration.
  Broaden to a compatible range only after the upgrade is proven.
- Port the three `history_processors=` registrations to
  `capabilities=[ProcessHistory(...)]` (keep the processor functions
  byte-identical).
- Pin `end_strategy='early'` on every `Agent(...)` construction
  (extraction, notes, scout, reviewer, formatter) — preserve-then-evaluate.
- Verify gotcha #18's V2 request-limit default; update
  `test_max_agent_iterations_below_pydantic_cap.py` comments/assertions.
- Add and test the `google:` routing/pricing prefix while retaining any legacy
  aliases required for stored-run and proxy compatibility (B.3 item 3).
- Migrate/confirm the `Agent[None, _VisionBatch]` annotations, and repeat the
  `prepare_tools`, `prepare=`, `capture_run_messages`, and model-profile audits.
- Migrate the three tool-event consumers and SSE fixtures to `.part`; explicitly
  test whether terminal/output-tool events require parallel handling.
- Resolve anything else the flip surfaces (TestModel/FunctionModel drift).
- Gate: full suite green; static type check green (or a documented targeted
  type check for the changed annotation if the repository has no global type
  gate); `pip check` clean; import smoke for every provider path.

### Phase U3 — Verification beyond the suite — 🟩 AUTOMATABLE ITEMS DONE 2026-07-12 (two operator gates open)

> **V2 live smoke PASSED** (run 002/run-id 221: SOFP on FINCO via 2.9.0 —
> extraction ok, cross-checks + reviewer, merged filled.xlsx; telemetry
> 547,493 tokens across 24 per-turn rows + 160,849 reviewer — item 4's
> non-zero check ✅ on V2). **Live E2E PASSED** (2m38s, Gemini flash
> through the coordinator) — after U3 caught a REAL V2 break the mocked
> suite could not see: `tests/test_e2e.py` hardcoded
> `google-gla:gemini-2.0-flash`, the exact prefix V2 removed; fixed to
> `google:gemini-3.5-flash`. **Vision-inventory live test PASSED**
> (partial scanned-PDF-path coverage). Failure-path drills: covered by
> the mocked lifecycle/wallclock/cancel suites, green on V2; live
> forced-timeout drill optional.
> **Operator gates still open (the only U3 remainder):**
> (1) Windows enterprise-box run (item 5, gotcha #5 truststore path);
> (2) benchmark-threshold sign-off + gold-benchmark scorecard anchor
> (go/no-go section) — needs a saved benchmark run via the web workspace;
> (3) MPERS live smokes skip without `MPERS_TEST_PDF` — set it to an
> MPERS sample and run `pytest -m live tests/test_mpers_wiring.py`.

The suite proves API compatibility; these prove *behavior* didn't drift:

1. **Live E2E**: `python -m pytest -m live -v` (needs API key).
2. **Benchmark suite run** (the eval workspace, gotcha #30): run a saved
   suite against a known-gold benchmark on both sides of the flip;
   compare accuracy + token cost scorecards. This is our objective
   "did extraction quality regress" instrument — use it.
3. **One scanned-PDF run** — exercises the compaction layer end-to-end
   under its new registration (the thrash-regression watch).
4. **Telemetry check**: open the run page Telemetry tab; per-turn tokens
   must be non-zero (the silent-zero trap's end-to-end proof).
5. **Windows gate** (operator): one run on the enterprise box —
   truststore/proxy path (gotcha #5) re-verified under the new dep tree.
6. **Failure-path drills:** force one model timeout, one cancellation, and one
   tool rejection. Confirm the run-lifecycle invariant still leaves terminal
   audit statuses and that interrupted streaming does not duplicate tool side
   effects or token totals.

### Go/no-go criteria

Record the thresholds **before** running U3 so the result cannot be judged
after the fact:

- **No-go:** any lifecycle row remains `running`; any duplicate/late write
  occurs after terminal save; telemetry tokens become zero/negative; a
  history-compaction pinning test fails; provider import/routing fails on Mac
  or Windows; or any critical/high regression is unresolved.
- **Benchmark threshold:** the owner must set the allowed accuracy delta and
  token-cost delta against U0's saved suite before U2. “Looks similar” is not
  an acceptance criterion. Report per statement type so aggregate scores do
  not hide a face-specific regression.
- **Ship:** all automated gates, live E2E, scanned-PDF, telemetry, failure
  drills, and Windows proxy run pass; the resolved dependency artifact and
  rollback rehearsal are recorded; remaining differences are explicitly
  accepted in a dated decision log.

### Rollback story

Rollback is reproducible only from the recorded dependency artifact: restore
the previous requirements **and lock/constraints file**, rebuild a fresh
environment, run `pip check`, then run the targeted telemetry/history/lifecycle
tests. Merely reverting `requirements.txt` and reinstalling can resolve a
different transitive graph. U0's renames are V1-compatible by design, so even
a U2 rollback leaves source compatible. No DB schema, template, or frontend
change is part of this plan — the blast radius is Python dependencies + call
sites only.

### Effort estimate

U0: S–M (the interpreter question is settled; the new work is the
constraints file + baseline evidence bundle).
U1: M (mostly triage). U2: M. U3: M–L (live comparisons, failure drills, and
Windows coordination). Elapsed: **2–4 focused engineering days plus operator
availability and benchmark runtime**; treat this as a planning range, not a
commitment.

---

## Part D — Harness learnings (independent of the upgrade)

> This part is deliberately self-contained: none of it requires V2 or the
> Harness package. Ships in any order relative to Part C.

### D.1 What the Harness is, and the adoption verdict

The Harness (`pydantic/pydantic-ai-harness`, source read @ `310ffad`,
2026-07-10) is Pydantic's box of ready-made agent parts. Verdict: **do not
adopt as a dependency** — it's 0.x pre-1.0 with an explicit
breaking-changes policy, requires pydantic-ai V2, and adds a package to
clear through the enterprise Windows proxy. But its internals are
well-designed, and six mechanisms are worth re-implementing natively.

Research provenance, three levels of evidence: docs/announcement → README
capability matrix (✅ vs 🚧 markers) → **full source read** (three parallel
deep-reads: compaction; guardrails/persistence; subagents/workflow/
filesystem/context, each cross-checked against our counterpart modules).
Findings below cite the source read unless noted.

**What source reading corrected from the docs-level picture:**

- **Checkpointing IS shipped** (✅, ~1,750-line `step_persistence/`
  package): snapshots only at "provider-valid" boundaries (every tool
  call paired with its return — safe to resume from), three storage
  backends (memory/file/SQLite), plus a **tool-side-effect ledger**
  (`ToolEffectRecord` with idempotency keys and an `unknown_after_crash`
  state) so a resumed run knows which writes actually landed. Post-V2
  candidate — see C.4/D.5.
- **NOT shipped despite README/blog implication** (🚧 open PRs @
  `310ffad`): hard cost/token budget *enforcement*, secret masking,
  tool-approval workflows, tool access control, memory backends.
- **Their shipped guardrails cannot gate tool calls** — `InputGuard`
  wraps the first model request, `OutputGuard` the final output; nothing
  guards an individual tool invocation, which is exactly where all our
  guards live (the write boundary). Our "don't adopt guardrails" verdict
  got *stronger* on contact with source.

### D.2 Decision record — what we do NOT adopt, and why

| Harness capability | Our counterpart | Why ours stays |
|---|---|---|
| Context compaction (LLM summarization tier) | `extraction/history_processors.py` — deterministic, free, stage-aware | Their summarizer pays LLM calls and has **zero image awareness** (README declares binary content out of scope). Ours is keyed on a domain event (first successful write) and encodes the run-126 / scanned-PDF thrash fixes. Applied naively, their size-only triggers would reintroduce that exact bug. |
| Guardrails capability | Abstract-row guard (gotcha #17), reviewer grounding gate, sanitiser (#16), format gates | Theirs guards prompt-in/answer-out only; tool-call gating is 🚧. Ours validate real state (rendered pages, cell coordinates) at the write boundary — a stronger place to stand. |
| Memory | `entity_memory.py` (advisory prior-year hints) | Their memory backends are an open PR. Nothing to adopt yet. |
| FileSystem / Shell tools | `view_pdf_pages`, `read_template`, `read_source_note` | Generic file read/write is a step backwards in safety and quality vs purpose-built tools. (But their *result formatting* is worth stealing — D.4.) |
| DynamicWorkflow / SubAgents (model-written orchestration) | `coordinator.py` + `notes/listofnotes_subcoordinator.py` deterministic asyncio fan-out | Structurally incompatible, not just philosophically: their sandbox forbids `asyncio.sleep`, and our fan-out depends on sleep-based launch staggering for provider rate limits. Regulated-finance auditability favors deterministic orchestration; our event bus / stable agent IDs / Stop-All cancellation are a more evolved product surface than their single raw event handler. |

Re-evaluate this table only if the Harness reaches 1.0 AND Part C has
landed.

### D.3 The six patterns to steal

Ordered by (value ÷ effort). Each is independently shippable and lists the
invariants (CLAUDE.md gotchas) it brushes. Harness refs are to
`pydantic_ai_harness/` @ `310ffad`; ours to repo root.

#### Item 1 — In-band limit warnings before hard caps ("LimitWarner") — TOP PICK — 🟩 DONE 2026-07-12

> Shipped as `limit_warner.py` + `tests/test_limit_warnings.py` (11 tests)
> on `feat/limit-warnings`; full suite 3315 passed. Four deviations from
> the sketch below, all noted deliberately:
> (1) implemented as a **ctx-aware history processor** (the pydantic-ai
> 1.x-blessed pre-request seam, same as the compaction wrappers), not
> runner-loop injection — the runner cannot modify the request pipeline
> without unsupported state mutation; the module stays pure.
> (2) the warning is appended **into the last ModelRequest's parts**, not
> as a standalone message — immune to provider role-alternation rules.
> (3) **wall-clock** is not warned about (the processor cannot see the
> runner's per-run deadline) — iterations + token budget only; wallclock
> nudge deferred.
> (4) coverage is the **face / notes (incl. Sheet-12 sub-agents) / scout**
> factories; the reviewer/formatter agents have their own dynamic caps and
> do not share these factories — "inherit for free" below assumed
> runner-loop injection and was wrong; extending to them is a follow-up.
> Kill switch: `XBRL_LIMIT_WARNINGS` (default on).

**Problem (plain language):** when one of our agents hits its iteration
cap, wall-clock limit, or token budget, we kill it mid-thought. Whatever
it was about to write is lost, and the run lands as a failure even when
the agent was one turn from saving. The agent is never told the end is
near.

**Their mechanism** (`compaction/_limit_warner.py`):
- Hooks before each model request. At **70% of any tracked limit** it
  appends a plain user-style message: `[LimitWarner] URGENT: …
  Iterations: 34/40 requests used (85%); 6 remaining. Complete the
  current task efficiently…`, escalating to `CRITICAL` near the boundary
  (≤3 iterations remaining).
- **Idempotent:** strips any previous `[LimitWarner]` message before
  deciding to inject a fresh one — exactly one live warning, never
  accumulating (`_strip_old_warnings`, `:118-131`).
- Injected as a **user-turn, not a system prompt** — deliberate; models
  attend more to user messages (docstring `:38-42`).

**Where it lands in our code:** the shared agent loop in
`agent_runner.py`, next to the existing hard raises —
`IterationLimitReached` (:331), `WallclockExceeded` (:341),
`TokenBudgetExceeded` (:488) — reading the same counters those raises
read. Face, notes, Sheet-12 sub-agents, reviewer, and formatter all
inherit it for free because they share `run_agent_loop`. Natural extra:
wire the wording to the save-gate so the wind-down advice is concrete
("run verify_totals then save_result now";
`save_result(acknowledge_unresolved=true)` exists precisely for
finishing a flagged-but-unsaved state).

**Invariants touched:** gotcha #18 (warnings key off OUR cap, 40, not
50); gotcha #6 (the injected message must not corrupt per-turn token
accounting; it rides the next request like any other prompt part).
Prefer injecting in the runner loop, not as a history processor — keeps
`history_processors.py` pure.

**Pinning tests:** new `tests/test_limit_warnings.py` — warning appears
at threshold, exactly one instance after multiple turns, escalation
wording, and a run that saves successfully post-warning. Existing
`test_agent_loop_wallclock.py` / `test_face_wallclock_cap.py` stay green.

**Effort:** S–M. **Risk:** low — additive; worst case the model ignores
the nudge and today's hard cap still fires.

#### Item 2 — One structured verdict contract for all guards ("GuardResult")

**Problem:** we have ~6 deterministic guards (abstract-row +
formula-cell + double-booking in `tools/fill_workbook.py:340-480`;
reviewer grounding gate `notes/reviewer_agent.py:185-239`; format gate
`notes/format_patch.py`; save-gate `extraction/agent.py:89-102`). Each
reports rejections in its own shape — a `(kind, message)` tuple here, a
free-text `errors` list entry there. No shared vocabulary, so
cross-guard telemetry ("how often does each guard fire, and does the
agent recover?") is ad hoc.

**Their mechanism** (`guardrails/_capability.py:54-116`): a frozen
`GuardResult` with a closed `Literal['allow','block','replace','retry']`
action, classmethod constructors, and `__post_init__` validation of the
per-action contract. The standout outcome is **`retry`** → re-raised as
the framework's `ModelRetry`, so "here's a correction, try again" is
budgeted by the normal retry counter instead of looping forever (`:450`,
docstring `:403-405`). Violations also emit structured trace spans with
direction/action attributes, content-gated so redacted values never
enter traces (`:204-228`).

**Where it lands in our code:** a small `tools/guard_result.py`
dataclass adopted guard-by-guard, starting with the two chattiest
(fill_workbook write guards, reviewer grounding gate). Our rejection
messages are already the best part ("first view the PDF page(s) with
view_pdf_pages…") — they become the `message` of a `retry`/`block`
verdict. Keep our advisory-vs-fatal nuance (double-booking warns without
flipping success, `fill_workbook.py:423-428`) — maps to `allow` +
warning payload, a case their binary model handles less well.

**Invariants touched:** gotcha #17 (guard *behavior* must not soften —
this is a reporting-shape refactor only; pinned messages stay verbatim);
`classify_notes_fix_guard` stays exported and pure for unit tests.

**Pinning tests:** existing guard tests
(`test_fill_workbook_abstract_guard.py`, `test_notes_reviewer_guard.py`,
…) pass unchanged apart from mechanical assertion updates; add one
contract test for the verdict type itself.

**Effort:** M (mostly mechanical, several files). **Risk:** low-mid —
behavioral drift while refactoring; mitigated by pinning tests and one
guard per commit.

#### Item 3 — Cache-aware compaction (min-reclaim gate + real size estimator)

**Problem (two related gaps in `extraction/history_processors.py`):**
1. Once thresholds hit, we rewrite history parts every turn. Every
   rewrite changes the outbound prompt bytes → **invalidates the
   provider's prompt cache** → we pay full input-token price on content
   that was cache-priced before. We never ask "is this rewrite
   reclaiming enough to be worth it?"
2. Our escalation watermark reads `ctx.usage.total_tokens` — a
   **cumulative, monotonic** counter for the whole run — not the size of
   the history we are about to send. It can never de-escalate and
   doesn't measure actual context pressure.

**Their mechanisms:**
- `min_clear_tokens` gate (`compaction/_clear_tool_results.py:73-77,120-123`):
  compute reclaimed tokens first; skip the whole rewrite if below
  threshold.
- `estimate_token_count` (`compaction/_shared.py:72-96`): a pluggable
  tokenizer with a `len(text)//4` fallback, applied to the outbound
  message list — measures what we're actually about to pay for.
- Idempotency guard (`_shared.py:393,408`): skip parts whose content
  already equals the placeholder, so repeat passes are true no-ops (our
  `strip_duplicate_template` currently re-replaces already-collapsed
  copies each call — harmless but wasteful,
  `history_processors.py:477-481`).
- The `TieredCompaction` shape (`_tiered_compaction.py:83-105`): run
  cheapest strategy first, **re-measure**, stop as soon as under budget.
  Our escalation is a one-way threshold-tightening latch with no
  re-measurement.

**Where it lands:** inside `compact_old_text_results` /
`_over_soft_watermark` in `extraction/history_processors.py`.
**Explicitly out of scope: the image path** (`strip_stale_images`) — its
"keep all images until first successful write, even under token
pressure" rule is a pinned regression fix and stays exactly as is.

**Invariants touched:** the module's purity contract (all changes via
`dataclasses.replace` on copies — pydantic-ai persists processed history
into traces, gotcha #6); the scanned-PDF thrash fix — hence the
image-path carve-out.

**Pinning tests:** extend `tests/test_history_processors.py` +
`test_history_processor_escalation.py`:
no-rewrite-below-min-reclaim, idempotent second pass, estimator-driven
trigger/release.

**Effort:** M. **Risk:** mid — this file encodes multiple incident
post-mortems; change only the text path, one mechanism per commit, and
measure cache-hit deltas on a benchmark suite run (gotcha #30 gives us
the scorecards to prove cost impact).

#### Item 4 — Clamp single oversized messages

**Problem:** all our compaction targets *old* content (age thresholds).
A single **fresh** runaway part — a degenerate model response, a giant
tool-call argument — has no defense and can alone blow the context
budget.

**Their mechanism** (`compaction/_clamp_oversized_messages.py`):
per-part trigger on `max_part_tokens`/`max_part_chars`; keeps head +
tail (default 2000/2000 chars) with a `[clamped: removed N of M
characters]` marker; clamped tool-call args stay **valid JSON**
(`{"_clamped": "<head>…<tail>"}`) so the provider never sees malformed
function arguments; only clamps if the result actually shrinks;
request-side parts (user prompts, tool returns) exempt by design.

**Where it lands:** a fourth processor in
`extraction/history_processors.py` (and the notes agent's processor
list), with our own exemptions mirrored from
`compact_old_text_results`: never clamp write confirmations, template
summaries, or image batches.

**Invariants touched:** same purity contract as item 3; gotchas #16/#14
— notes cells legitimately run to 30k rendered chars, so the notes-side
threshold must sit comfortably above legitimate `write_notes` payloads
(or exempt write-tool args entirely).

**Pinning tests:** new cases in `test_history_processors.py` —
oversized text part clamped with marker, JSON-validity of clamped args,
exemptions honored.

**Effort:** S–M. **Risk:** low with the exemption list; the main hazard
is clamping a payload the agent still needed, which the exemptions +
generous threshold address.

#### Item 5 — Optimistic-concurrency fingerprints on shared writes

**Problem:** up to 5 Sheet-12 sub-agents write concurrently into shared
state. Gotcha #22 gives us *physical* safety (io_lock + atomic save —
no corrupted files), and the notes formatter already does *logical* CAS
at the DB layer (`cas_update_notes_cell_html`, gotcha #16). But the
agent-facing write tools themselves have no read-before-write freshness
contract — two sub-agents can logically clobber the same region without
either noticing.

**Their mechanism** (`filesystem/_toolset.py:71-73, 252-306`): every
read returns a 12-char content hash in a metadata header;
`write_file`/`edit_file` accept `expected_hash` and refuse stale writes
with a *recoverable* error ("Re-read the file and retry") raised as
`ModelRetry` via a `@_recoverable` decorator, so the agent self-corrects
instead of aborting. `edit_file` additionally requires the target text
to appear exactly once.

**Where it lands:** the notes write path first (highest concurrency):
reads return a region fingerprint; `write_notes` optionally checks it.
Face statements are single-writer per sheet, so lower priority there.

**Invariants touched:** gotcha #22 (complements, does not replace, the
io_lock + atomic save); gotcha #14 retry budget (a refused stale write
consumes a normal tool retry, not an agent restart).

**Pinning tests:** new `tests/test_write_freshness_guard.py` —
stale-hash write refused with actionable message; fresh write passes;
Sheet-12 concurrent scenario.

**Effort:** M. **Risk:** mid — must not make the happy path chattier
(hash checks advisory first, enforced once proven in eval runs).

#### Item 6 — Salvage completed work on agent retry

**Problem:** when a face agent fails and its single retry fires
(`run_agent_with_retries`, `agent_runner.py:512-683`), the fresh attempt
starts from zero — everything the failed attempt already extracted and
wrote is re-derived at full token cost (or worse, re-written).

**Their mechanism** (`dynamic_workflow/_toolset.py:287-311, 699-702`):
`_completed_retry_section` — on failure, list the sub-results that DID
complete back to the retry ("reuse these values instead of re-calling;
their budget was already spent"), with truncated previews.

**Where it lands in our shape:** our writes are durable in
`run_concept_facts` / `notes_cells`, so the retry prompt can open with a
deterministic summary: "A previous attempt already wrote N facts to
{sheets}: {compact list}. Verify rather than re-extract; fill only the
gaps." Face retries first (`coordinator.py:803` path), notes second. The
Sheet-12 sub-agent retry (`listofnotes_subcoordinator.py:416-493`)
already re-sends with context — align its wording with the same pattern.

**Invariants touched:** gotcha #14 (retry budget stays max-1 — this
changes what the retry *knows*, not how many fire); gotcha #21 (the
summary reads canonical facts, the same source of truth the exporter
uses); notes rerun-CLOBBER semantics (gotcha #16) — the notes
coordinator deletes the sheet's cells before a rerun, so the notes-side
salvage summary must be captured BEFORE the delete, or the face pattern
ships alone first.

**Pinning tests:** extend `tests/test_face_transient_retry.py` — retry
prompt contains prior-work summary; a run with zero prior writes gets no
summary block.

**Effort:** M. **Risk:** mid — the summary must be framed as
scout-hint-style advisory ("VERIFY against the PDF", gotcha #13's
framing discipline) so a bad first attempt doesn't anchor the retry.

### D.4 Smaller honorable mentions (opportunistic, no dedicated phase)

- **Cache placement rule of thumb** (their planning capability + context
  loader): *static content → system prompt (cached prefix); volatile
  content → message tail, never re-written into the prefix.* Adopt as a
  review checklist rule whenever we inject live state into prompts.
- **Read-paging protocol** (`filesystem/_toolset.py:59-61`): truncated
  tool results end with an explicit continuation instruction ("… N more
  lines, use offset=X"). Our `read_template` summaries and
  `search_pdf_text` could adopt the same self-serve paging footer.
- **Effort-floor routing** (`subagents/_effort.py:18-33`): a tiny
  rank-map clamp for thinking effort per agent role. Only relevant once
  we expose per-role effort at all; note for the V2 era.
- **Tool-pair-safe cutoffs** (`compaction/_shared.py:203-294`): we never
  drop whole messages today (we blank in place), so we don't need this —
  but if a sliding window over old turns is ever proposed, port their
  ±5-message pairing check first.

### D.5 Post-V2 opportunities (blocked on Part C; revisit then)

Recorded here so the ideas don't get lost; none are commitments.

1. **Checkpointing / resume** — the shipped `step_persistence` package
   (D.1). Would supersede parts of Item 6 and pair with the eval
   workspace (re-run a failed doc from its last safe snapshot instead of
   paying for the whole run). The provider-valid-boundary +
   side-effect-ledger model is the part to study even if we build our
   own on `run_agent_turns`.
2. **Deferred tool loading** (core V2) — tools appear as one-line stubs
   until the model asks; could trim our notes agents' large prompts.
3. **CodeMode spike** — the model writes one sandboxed Python script
   (Monty: an embedded mini-interpreter, no terminal/subprocess, no
   filesystem/network, stdlib-subset only; our tools surface as callable
   functions and execute host-side so all write guards still fire)
   instead of N sequential tool calls. Potential turn/latency win for
   calculator-heavy verify loops; real costs: per-turn telemetry
   collapses into `run_code` blobs and trace auditability degrades.
   Decision instrument: one statement type, benchmark suite scorecard
   (gotcha #30), accuracy + cost + trace-readability compared before
   any wider adoption.
4. **Capabilities refactor** — bundling each agent role's tools + prompt
   + hooks as a capability could DRY the reviewer/notes/formatter
   assembly. Pure refactor; only worth it if it demonstrably reduces
   drift between roles.

---

## Part E — Sequencing across both tracks

| Track | Phase | Items | Effort | Depends on |
|---|---|---|---|---|
| Harness patterns | A | D.3 Item 1 (limit warnings) | S–M | nothing |
| Harness patterns | B | Items 3 + 4 (compaction economics) | M | nothing; measure via benchmark suite |
| Harness patterns | C | Item 2 (GuardResult) | M | nothing; one guard per commit |
| Harness patterns | D | Item 6 (retry salvage, face first) | M | nothing |
| Harness patterns | E | Item 5 (write fingerprints) | M | best after D |
| V2 upgrade | U0 | preflight renames (also de-risks the silent-zero trap NOW) | S | nothing |
| V2 upgrade | U1 | latest V1 + deprecation sweep | M | U0 |
| V2 upgrade | U2 | flip to V2 | M | U1 |
| V2 upgrade | U3 | behavior verification + Windows gate | M | U2 |
| Post-V2 | — | D.5 (checkpointing, deferred loading, CodeMode spike) | per-item | U3 |

The two tracks are independent; U0 is cheap risk-reduction worth doing
early regardless. Suggested overall order: **U0 → Phase A → U1–U3 → the
rest by appetite.** Planning target: finish before year-end 2026. Officially,
V1 receives security fixes for **at least** six months after 2026-06-23; that
is a minimum support promise, not a published hard end-of-support date.

## Part F — Open questions (decide at implementation time)

1. **U2 `end_strategy`** — pin `'early'` (proposed) vs adopt
   `'graceful'` at flip time. Proposal: pin, evaluate `'graceful'` later
   as its own change with an eval-suite comparison.
2. **Item 1 thresholds** — copy their 70%/critical-3 defaults, or tune
   to our cap of 40 (warn at 30, critical at 37)? Proposal: theirs, then
   tune from Telemetry-tab evidence.
3. **Item 3 estimator** — `len//4` fallback only, or wire a real
   tokenizer? Proposal: fallback only; trend matters, not precision.
4. **Item 5 enforcement** — advisory (log + warn) first, or refuse
   immediately? Proposal: advisory for one eval cycle, then enforce.
5. **Item 6 notes-side** — worth the pre-delete snapshot plumbing, or
   face agents only? Proposal: face only until a notes retry shows up
   wasting real money in Telemetry.
6. **When to schedule U1–U3** — after the motion-transitions branch
   merges, as its own branch. Owner/date TBD with the operator.

## Part G — Sources

- [Pydantic AI V2 announcement](https://pydantic.dev/articles/pydantic-ai-v2)
- [V2 upgrade guide / changelog](https://pydantic.dev/docs/ai/project/changelog/)
- [Official version policy](https://pydantic.dev/docs/ai/project/version-policy/)
- [Official usage API reference](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/)
- [PyPI release history](https://pypi.org/project/pydantic-ai/)
- [GitHub releases (v2.0–v2.8)](https://github.com/pydantic/pydantic-ai/releases)
- [Harness overview docs](https://pydantic.dev/docs/ai/harness/overview/)
- [CodeMode docs](https://pydantic.dev/docs/ai/harness/code-mode/) +
  [CodeMode README (Monty details)](https://github.com/pydantic/pydantic-ai-harness/blob/main/pydantic_ai_harness/code_mode/README.md)
- [pydantic/pydantic-ai-harness](https://github.com/pydantic/pydantic-ai-harness)
  — source read at commit `310ffad` (2026-07-10)
- Internal audit (2026-07-12 greps): `coordinator.py`, `agent_runner.py`,
  `server.py`, `notes/coordinator.py`, `notes/listofnotes_subcoordinator.py`,
  `scout/agent.py`, `scout/notes_discoverer_vision.py`,
  `extraction/history_processors.py`, `extraction/agent.py`,
  `notes/agent.py`, `model_settings.py`, `pricing.py`, plus 9 test files
  named in B.2.
