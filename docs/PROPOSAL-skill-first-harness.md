# Proposal: Skill-First Harness for the XBRL Agent

**Status:** `Phase 0.5 + Phase 1 IMPLEMENTED on a branch (rev. 4) — pending the Windows gold accept-or-revert gate`
**Author:** Research + architecture pass, 2026-06-25
**Revision:** 2026-06-25 (rev. 4) — **Phase 0.5 (canonicalize) + Phase 1 (loader) built** in this Mac environment, guarded by environment-independent static pinning tests (the operating model in §8). Shipped: the canonicalized reference shelf `prompts/references/*.md` (9 files, one per (statement, variant)); the path-safe, deps-keyed `extraction/workflow_reference.py` loader + `load_workflow_reference()` tool in `extraction/agent.py`; the `strip_duplicate_workflow_reference` history processor; the deterministic refuse-once activation gate on the first SOCIE/SOCF `write_facts` (`XBRL_WORKFLOW_REFERENCE_GATE`, default on, conftest-off for the suite); `_base.md` wiring. Tests: `tests/test_workflow_reference_loader.py`, `tests/test_workflow_reference_canonicalization.py` (65 cases). **Finding that narrows §4/§9:** only **SOCIE** actually carried the cell-reference `field_label` + dividends-negative drift — the other 8 docs already used correct text labels and signs that agree with `_base.md`; their canonicalization was a preamble + stale-"Open Questions" removal. Phases 0 + 2 (accuracy on real gold) remain the operator's Windows accept-or-revert step; Phases 3 + 4 not built (Phase 3 is "drop if it doesn't pay"; Phase 4 is gated behind the Phase 2 result).
**Revision:** 2026-06-25 (rev. 3) — reconciled against the concurrent uncommitted **reviewer self-verify** work: the `verify_fixes()` (reviewer + spot-check) and `verify_findings()` (notes reviewer) "close the loop" pass added in `prompts/reviewer.md`, `prompts/notes_reviewer.md`, `prompts/spot_check.md`, `correction/reviewer_agent.py`, `notes/reviewer_agent.py`, `db/repository.py`, plus `tests/test_reviewer_self_verify.py` / `tests/test_notes_reviewer_self_verify.py`. That feature is **orthogonal** to this proposal — it touches none of the core-track files (`docs/workflows/*`, `extraction/agent.py::write_facts`, the statement prompts), so **Phase 0.5 / Phase 1 are unchanged**. Only the reviewer-facing notes (§1, §6, Phase 4) are adjusted to treat the reviewer/notes-reviewer prompts as a *moving target* rather than frozen.
**Revision:** 2026-06-25 (rev. 2) — implementation plan (§8) revised against an internal peer review of the findings: adds a mandatory reference-canonicalization gate (Phase 0.5), a path-safe deps-resolved loader, a deterministic activation gate, an explicit cache + dedup-history-processor mechanism, a per-cell eval gate, and a downscoped Phase 3. §1/§4/§7/§9 adjusted for consistency.
**Decision sought:** Whether (and how far) the runtime extraction agents should adopt an Anthropic-style "skill-first" harness.
**Scope:** Extraction / notes / reviewer agents and their prompt + domain-knowledge loading. **Out of scope:** the Python coordinator, cross-checks, verifier, concept model, DB schema, and any output-quality change.

> **Filename note:** follows the repo's `docs/<TYPE>-<topic>.md` convention. This is a proposal + implementation plan, not a load-bearing API contract — treat it like the other `docs/PLAN-*.md` / `docs/PRD-*.md` files (a "why and how" snapshot, per the CLAUDE.md "PLAN docs are historical context" rule).

---

## 1. TL;DR / Recommendation

**Conditional yes — adopt the skill *pattern* selectively; do not rewrite the harness.**

The codebase already implements the *intent* of skills (progressive disclosure of instructions, modular domain knowledge) through its prompt-layering system. A wholesale migration to a skill framework would add a loading layer and a security surface without solving a problem we currently have, and it cuts against the determinism our regulated-extraction domain demands.

The high-value, low-risk move is to close one real gap: **the ~53 KB of per-statement fill know-how in `docs/workflows/*.md` is developer-only documentation that the agents never see.** That is exactly the kind of reusable, load-on-demand domain expertise a skill is meant to package.

Recommended adoption, in priority order:

1. **Canonicalize, then promote `docs/workflows/*.md` into agent-loadable, progressively-disclosed reference files** (the "skill-ify the workflows" track). Highest value — but the docs have already drifted from the live prompts/writer contract (e.g. the SOCIE dividend sign), so a mandatory canonicalization gate (Phase 0.5) precedes exposure. Promoting them verbatim would *lower* accuracy.
2. **(Optional, low payoff) Consolidate the *low-level* shared tool impls.** The per-agent wrappers differ (batching, async, return shapes) and the impls are already shared, so this is maintainability only — not an accuracy or token lever.
3. **Keep** the coordinator, cross-checks, verifier, concept model, and reviewer pass out of the skill migration. (The reviewer / notes-reviewer prompts are *independently* gaining a `verify_fixes()` / `verify_findings()` self-verify loop — concurrent uncommitted work, orthogonal to this proposal. "Keep as-is" means *this migration doesn't touch them*, not that they are frozen; Phase 4 plans against their current state, self-verify included.)

Explicitly **rejected**: replacing the coordinator with a skill-calling orchestrator, or adopting a full external Agent Skills runtime, given the OpenAI/Gemini-default provider mix (see §7).

---

## 2. Background: what "skill-first" means

A **skill** is a directory containing a `SKILL.md` file (YAML frontmatter with `name` + `description`, then markdown instructions), plus optional `scripts/`, `references/`, and `assets/` subfolders. The defining mechanic is **progressive disclosure in three stages**:

1. **Discovery** — at startup the agent sees only each skill's `name` + `description` (cheap; "just enough to know when it might be relevant").
2. **Activation** — when a task matches the description, the agent loads the full `SKILL.md` into context.
3. **Execution** — the agent follows the instructions, loading referenced files (`references/`) or running bundled code (`scripts/`) *only as needed*.

The motivation is empirical: long context does not reliably improve performance, and detailed-but-irrelevant instructions become "reasoning noise." Loading everything upfront — many tools, many instructions — degrades the agent before it begins. Skills are the harness-level primitive that fixes this by deferring detail.

Anthropic's own guidance is **evaluation-first and incremental**: identify where agents struggle for lack of context, then build skills to close those specific gaps. "If a piece of information is only needed 20% of the time, put it in a reference file."

See §10 for sources.

---

## 3. Where the codebase already does this

The XBRL agent is unusually well-aligned with the skill philosophy already, which is the central reason a forced migration buys little:

| Skill concept | Existing equivalent in this codebase |
|---|---|
| Progressive disclosure of instructions | `prompts/__init__.py::render_prompt()` assembles `_base.md` → `{stmt}_{variant}.md` / `{stmt}_{standard}.md` → `{stmt}.md` → group overlays → runtime-injected blocks (scout context, sign conventions, page offset, prior-year advisory). Only the instructions a given run needs are assembled. |
| Lazy reference loading | Template structure is **not** dumped into the system prompt; the agent pulls it on demand via the `read_template()` tool, with provider-side caching. |
| `scripts/` (bundled executable know-how) | Tools delegate to focused modules in `tools/` (`calculator.py`, `pdf_viewer.py`, `fill_workbook.py`, `verifier.py`, …). |
| Composable, independently-testable units | Pluggable `cross_checks/` classes; the staged `scout/` pipeline producing a typed `Infopack`; the 5 independent notes-template agents. |
| Parametric "skill bundles" | Factory functions `create_extraction_agent()`, `create_notes_agent()`, `create_reviewer_agent()` build an agent + its deps + its tools by parameter. |

In other words: the prompt-layering precedence ladder *is* a hand-rolled progressive-disclosure system. We are not starting from a monolithic prompt.

---

## 4. The real gap worth closing

`docs/workflows/*.md` contains ~53 KB across 9 files of dense, statement-specific fill know-how:

```
SOCF-Direct-Fill-Workflow.md          SOCF-Indirect-Fill-Workflow.md
SOCI-BeforeTax-Fill-Workflow.md       SOCI-NetOfTax-Fill-Workflow.md
SOCIE-Fill-Workflow.md                SOFP-CuNonCu-Fill-Workflow.md
SOFP-OrderOfLiquidity-Fill-Workflow.md SOPL-Function-Fill-Workflow.md
SOPL-Nature-Fill-Workflow.md
```

Take `SOCIE-Fill-Workflow.md` as the exemplar. It documents — exhaustively, though **not currently correctly** — the 52×24 matrix structure, the B–X equity-component column map, which movements are formula rows vs. cell-level data entry, the "profit always lands in the retained-earnings column C" rule, the CY/PY two-block structure, and a fully worked FINCO example. Two parts have since drifted from the live contract and MUST be fixed before exposure (Phase 0.5): its **dividend sign convention** (the doc says *negative*; the live `prompts/socie.md` requires *positive* magnitudes because the Total-increase formula subtracts the row) and its **worked-example JSON** (stale `field_label: "C11"` strings; the live `write_facts` tool takes explicit `row`/`col` coordinates). The structural reasoning is still high-value — the drift is correctable, not disqualifying.

**This is precisely the knowledge a SOCIE extraction agent needs, and it currently never reaches the agent.** It exists only for human developers. The SOCIE prompt (`prompts/socie.md`, ~5.4 KB) carries a condensed subset; the deep matrix reasoning, the worked example, and the common-mistakes catalogue stay on the shelf.

This is the textbook case for a skill `references/` file: needed only when working SOCIE, too long to inline into every run, and high-leverage when it *is* relevant. The same holds (to varying degrees) for the SOCF direct/indirect articulation rules and the SOPL function/nature split.

A secondary, smaller gap: shared tools (`calculator`, `lookup_definitions`, `read_template`, `view_pdf_pages`, `search_pdf_text`) are re-registered inside every agent factory. Not a correctness problem, but a candidate for a shared "toolkit" module.

---

## 5. Why *not* go all-in

Two arguments specific to this product:

**5.1 The domain is exactly where determinism is supposed to win.** Regulated financial extraction with an audit trail is the canonical case where predefined, coded, tested workflows remain essential — each step gated, replayable, and aligned to institutional standards. The pure-Python coordinator is the reliability moat: token budgets, graceful cancellation, Stop-All partial merge, the terminal-status guarantee (gotcha #10), the fact-based verifier (gotcha #25), the concept-model cascade (gotcha #21). Skills are an *agent-context* primitive. They can shape what an agent reads; they cannot and should not replace orchestration logic. Industry signal is consistent: enterprise finance deployments are pipeline-centric with bounded agent autonomy, not autonomous-agent-centric.

**5.2 We'd absorb cost for little gain.** The repo already carries ~25 DB schema versions, pinning tests on nearly every invariant, and a working provider-aware caching/temperature abstraction. A full skill harness adds a loader and a trust/security surface (skills direct tool and code execution, so they must come only from trusted sources) without removing a current pain point. The progressive-disclosure benefit is *already captured* by prompt layering for the instructions we load today.

---

## 6. Mapping skill structure onto the existing architecture

If we adopt the *pattern* (not a framework), here is how the pieces line up. Nothing below requires a new runtime — `references/` files are loaded by an existing tool, and the precedence logic already lives in `render_prompt()`.

```
skills/
  extraction/
    SKILL.md                    # name + description + general extraction rules (≈ _base.md persona)
    references/
      sofp-cunoncu.md           # promoted from docs/workflows/SOFP-CuNonCu-Fill-Workflow.md
      sofp-orderofliquidity.md
      sopl-function.md
      sopl-nature.md
      socf-direct.md
      socf-indirect.md
      soci-beforetax.md
      soci-netoftax.md
      socie.md                  # the big one — matrix structure + worked example
    scripts/ -> (existing tools/ modules; reference, do not duplicate)
  notes/
    SKILL.md
    references/
      corporate-info.md  accounting-policies.md  list-of-notes.md
      issued-capital.md  related-party.md
  reviewer/
    SKILL.md                    # holistic-audit rules (≈ prompts/reviewer.md, incl. the verify_fixes() close-the-loop pass)
```

> **Resolved (§11 decision 4):** the references live under `prompts/references/`, not a
> top-level `skills/` dir — the tree above is illustrative of the *pattern*, not the path.

**Activation mechanism (no new framework needed):** add one tool, e.g. `load_workflow_reference(statement, variant)`, that returns the relevant `references/*.md` file. The agent calls it once when it begins a statement it needs depth on — exactly the "Activation → Execution" stage of progressive disclosure, implemented with the lazy-load mechanism the codebase *already* uses for `read_template()`. The `render_prompt()` precedence ladder stays the discovery/assembly layer.

This keeps the coordinator untouched: it still fans out agents; the agents simply have a richer, on-demand reference shelf.

---

## 7. Provider caveat (important)

The default model is OpenAI (`openai.gpt-5.4`), with Gemini and Anthropic also routed through `_create_proxy_model()`. Native skill loaders now exist beyond Anthropic (Claude's Agent Skills + the Agent SDK loader; OpenAI Codex skills; the OpenAI Agents SDK's shell/container tools) — so "skills are Anthropic-only" is no longer the accurate framing. The load-bearing fact for *this* codebase is narrower: **it runs none of those runtimes** — every model goes through PydanticAI + the LiteLLM proxy — and the Gemini/LiteLLM leg has no first-class skill loader at all. Adopting any vendor's loader would mean either a provider lock-in we don't have today or a partial solution that skips Gemini.

This strengthens the recommendation: **borrow the pattern, skip the framework.** We get the progressive-disclosure benefit using the lazy-load tool we already have, with no dependency on a provider-specific runtime and no change to the multi-provider abstraction.

---

## 8. Implementation plan

Phased, behaviour-preserving, evaluation-gated. Each phase is independently shippable and reversible. **Revised against the peer review of the findings:** the canonicalization gate (Phase 0.5), the loader's path-safety + activation design and explicit caching (Phase 1), the sharpened per-cell eval gate (Phase 2), and the downscoped Phase 3 all come from that review.

**Operating model — gold lives only on Windows; the eval gate is a Windows acceptance step.** The build (Phase 0.5 + Phase 1, optional Phase 3) happens on a **branch in this (Mac) environment** and is guarded by *environment-independent static pinning tests* that run in CI here. Those tests answer **"did we break the contract?"** — a reference contradicting its live prompt, an unsafe loader path, a missing activation gate, a re-billed reference — deterministically, **no gold required**. The *accuracy* measurement (Phase 0 baseline + Phase 2 gate) is run by the operator on **Windows against the real gold** and answers the separate question **"did accuracy actually improve?"** — that run is the **accept-or-revert** decision for the whole branch. **Commit Phase 0.5 (doc canonicalization) separately from Phase 1 (the loader)** so the corrected workflow docs survive even a full loader revert (they fix genuinely-stale developer docs regardless of the experiment's outcome). Per-file reversibility (Phase 2) additionally lets a single regressing reference be dropped without unwinding the rest.

### Phase 0 — Establish the baseline (operator-run on Windows, where the gold lives)
- [ ] Pick a FIXED, named scenario set spanning MFRS/MPERS × Company/Group, including at least one SOCIE-heavy and one SOCF-indirect case. **Record the benchmark IDs** — every later phase compares against these exact gold sets, not ad-hoc PDFs.
- [ ] Seed gold from **completed terminal runs** (`create_benchmark_from_run`), NOT fresh workbook uploads — an un-recalculated export silently drops most SOCIE / cross-sheet formula leaves (CLAUDE.md gotcha #23, the 2026-06-05 incident). Hand-correct in the gold editor after seeding.
- [ ] Run extraction on each; record a **per-statement scorecard**: matched / missing / mismatch / extra / scale-mismatch counts, plus token + tool-call totals. Prefer **N ≥ 3 repeated runs per scenario** so run-to-run LLM variance doesn't masquerade as a treatment effect. This is the bar every later phase must beat or hold.
- [ ] **Rationale:** Anthropic's guidance is evaluation-first. We adopt skills to close *measured* gaps, not on principle.

### Phase 0.5 — Canonicalize the references (MANDATORY gate — before any agent sees them)
The workflow docs have already drifted from the live contract (§4, §9). Promoting them verbatim would *lower* accuracy — e.g. the SOCIE "dividends are negative" line directly contradicts the live prompt and would break `socie_to_sofp_equity`.
- [ ] For **every** workflow doc (all 9 use the stale `field_label` addressing — this is NOT SOCIE-only; the recently-hardened SOCF sign/articulation docs are prime suspects too), diff against the live `prompts/{stmt}*.md`, the live template, and the `write_facts` contract in `extraction/agent.py`.
- [ ] Reconcile **semantics, not just coordinates**: sign conventions (dividends, treasury shares, SOCF articulation), addressing mode (`field_label` strings → explicit `row`/`col`), and stale "open questions" the tooling already answers. A coordinate-existence check is INSUFFICIENT — `C11` exists; only its *sign* is wrong.
- [ ] **Pinning test:** assert each canonicalized reference agrees with its live prompt on the load-bearing sign conventions and addressing mode (parametrise across statements, à la `test_notes_prompt_phase1.py`). This is what stops a future prompt change from silently re-staling a reference.
- [ ] **Gate:** no reference advances to Phase 1 until its canonicalization test passes. Scope is **all 9 workflows** (§11 decision 3) — canonicalize the batch, validating SOCIE first as the hardest case.

### Phase 1 — Promote canonicalized references into an agent-loadable, path-safe loader
- [ ] Build the reference shelf under **`prompts/references/`** (§11 decision 4), one normalized file per (statement, variant) (§11 decision 2), matching the existing `docs/workflows/` split. Source = the **canonicalized** Phase 0.5 text, never the raw doc.
- [ ] Add a `load_workflow_reference()` tool to `extraction/agent.py`. **Path-safety is a design constraint, not an afterthought:** the tool takes NO model-supplied path. It resolves the file from a **static enum map keyed by `ctx.deps`** (`statement_type` / `variant` / `filing_standard` / `filing_level`, the way `cell_resolver` scopes by `template_id`), caps output size, and returns an explicit "no reference available" for any unknown combo. It must never degrade into a general file-read primitive by joining model strings into paths.
- [ ] **Deterministic activation — not "the agent remembers."** For statements whose reference is required (SOCIE, SOCF), gate the first `write_facts` on it: refuse the first write until `load_workflow_reference` has been called this run (mirror the existing `last_verify_result = None` re-gate already in `write_facts`), OR inject a short reference digest + a required-call flag into `deps`. Either way it is pin-able with a unit test; relying on the model to self-trigger is not.
- [ ] **Caching is explicit — not "provider magic."** Add a `_WORKFLOW_REFERENCE_CACHE` (process cache, like `_render_template_summary`) AND a `strip_duplicate_workflow_reference` history processor (like `strip_duplicate_template`) so a reloaded reference is billed once, not per turn. Without the dedup processor the Phase 2 token measurement is meaningless.
- [ ] Update `_base.md` / the statement prompts to name the tool and *when* to call it ("for SOCIE and other matrix/articulation-heavy statements, load the workflow reference before writing facts").
- [ ] **Do not** inline the reference text into the system prompt — that defeats progressive disclosure and inflates every run.
- [ ] **Pinning tests:** loader resolves the correct file per (statement, variant) from deps (never a model path), honouring the same variant→standard→generic precedence as `render_prompt()`; unknown combos return "not available"; output is size-capped; the system prompt does **not** embed the reference body; the activation gate refuses a pre-load write; the dedup history-processor test (analogue of the `strip_duplicate_template` test) passes.

### Phase 2 — Measure (operator-run on Windows; the accept-or-revert gate)
- [ ] Re-run the Phase 0 scenario set (same benchmark IDs, same N repeats). Produce a **per-statement, per-cell delta**: change in matched / missing / mismatch / extra / scale-mismatch, plus token + tool-call deltas.
- [ ] **Gate:** keep a reference only if its statement's accuracy improves-or-holds AND the simpler statements show no per-cell regression and no material token regression. An aggregate "accuracy held" is NOT sufficient — a SOCIE matrix-cell regression can hide inside it. If a statement regresses, scope its reference smaller or revert that one file (per-file reversibility is the point).

### Phase 3 — (Optional, low payoff) Shared toolkit consolidation
The earlier "byte-identical refactor" framing was wrong: the wrappers are NOT identical — extraction batches `calculator` (`expressions: List[str]`); notes uses a single-expression + async/off-thread form; `read_template` returns different shapes. The **low-level impls are already shared** (`_calculator_impl`, `_lookup_definitions_impl`, `_read_template_impl`, `search_pdf_text_json`), so the DRY win is largely already banked and this phase moves neither accuracy nor tokens.
- [ ] If done at all: share **low-level helper implementations only**; keep each per-agent `@agent.tool` wrapper, docstring, and signature as-is unless a dedicated test proves the tool schema and behaviour are unchanged.
- [ ] Re-scoped to maintainability only — **drop it from the plan** if it doesn't pay for the test surface it adds.

### Phase 4 — (Optional) Notes + reviewer parity
- [ ] If Phase 2 shows clear wins, apply the same canonicalize-then-load pattern to the notes templates and the reviewer's holistic-audit knowledge. Notes references go through the same Phase 0.5 gate before exposure. Lower priority — the notes pipeline is deliberately all-LLM-judgement (gotcha #14) and already modular.
- [ ] **Canonicalize against the *current* reviewer / notes-reviewer prompts** — which now carry the `verify_fixes()` / `verify_findings()` self-verify ("close the loop") sections from the concurrent uncommitted diffs, not the pre-self-verify text. Any reviewer reference must agree with the live "verify before you finish" contract, the same way an extraction reference must agree with its live statement prompt (Phase 0.5). The two `*_self_verify` pinning tests are part of the contract those references must not contradict.

### Non-goals (explicit)
- No change to the coordinator orchestration, retry budgets, or SSE event shapes.
- No change to cross-checks, the verifier, the concept model, or the DB schema.
- No new external runtime dependency; no provider lock-in (§7).
- No change to extraction *accuracy targets* beyond what the eval gate measures.
- `load_workflow_reference` is **not** a general file-read tool (Phase 1 path-safety).

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Reference files have ALREADY drifted from the live prompts/writer contract — present-tense, not a future risk. The SOCIE doc says dividends are *negative* (live prompt: positive), uses stale `field_label` JSON (live tool: explicit `row`/`col`), and still poses coordinate-mode "open questions" the `write_facts` tool already answers; all 9 docs use the legacy `field_label` addressing. | **Phase 0.5 is the mitigation, and it must diff *semantics*, not just coordinates.** A coordinate-existence test is insufficient — `C11` exists; only its *sign* is wrong. Canonicalize sign conventions, addressing mode, and stale open-questions against the live prompt + `write_facts` contract before any doc is exposed; the pinning test then keeps a future prompt change from silently re-staling it. |
| Token cost rises if the agent over-eagerly loads references. | Progressive disclosure: load only on the statements that need it; process-cache + `strip_duplicate_workflow_reference` so it bills once (Phase 1); measure in Phase 2. |
| Skill-ification tempts a coordinator rewrite. | Hard non-goal (§8). The coordinator stays the deterministic spine. |
| Provider mismatch with vendor-native skill tooling. | We implement the loader ourselves with the existing lazy-load mechanism — no framework dependency, and it covers the Gemini/LiteLLM leg that has no native loader (§7). |
| Maintenance: two homes for statement knowledge (prompt + reference). | Define the split clearly — prompt = *always-needed* rules; reference = *depth needed ~20% of the time*. Document it in the SKILL.md. |

---

## 10. Sources

- [Equipping agents for the real world with Agent Skills — Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Agent Skills overview — Claude API Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Harness design for long-running application development — Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [The Anatomy of an Agent Harness — LangChain](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness)
- [Agent Skills: Progressive Disclosure as a System Design Pattern — SwirlAI](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)
- [Agentic AI for Finance: Workflows, Tips, and Case Studies — CFA Institute](https://rpc.cfainstitute.org/research/the-automation-ahead-content-series/agentic-ai-for-finance)
- [AI Agents vs. AI Workflows: Why Pipelines Dominate — IntuitionLabs](https://intuitionlabs.ai/articles/ai-agent-vs-ai-workflow)

---

## 11. Resolved decisions (reviewer Q&A, 2026-06-25)

1. **Eval coverage → gold lives only on Windows; the gate is a Windows acceptance step.** No gold is available in this (Mac) build environment — it exists in the operator's Windows setup. So the branch is built *without* a local eval: Phase 0.5 + Phase 1 are guarded by environment-independent static pinning tests (canonicalization-vs-prompt, loader path-safety, activation gate, dedup) that run in CI here and catch the dangerous contract-drift class deterministically. The accuracy measurement — Phase 0 baseline + Phase 2 gate — is run by the operator **on Windows against the real gold**, and that run is the **accept-or-revert** decision for the whole branch. Build here; measure on Windows; keep or revert there (see §8 "Operating model").
2. **Reference granularity → one file per (statement, variant).** Matches the existing `docs/workflows/` split and the loader's deps key directly (no variant-slicing), and keeps each file small so a SOCF-direct run never reads SOCF-indirect text. More files, but trivially so.
3. **Phase 1 scope → all 9 workflows.** Canonicalize all 9 in Phase 0.5 (low-risk, gated by static consistency tests); validate SOCIE first as the hardest case. The all-9 batch is accepted or reverted as a unit by the Windows gold run (decision 1); per-file reversibility (Phase 2) lets a single regressing reference be dropped without unwinding the other 8.
4. **Loader home → `prompts/references/`.** Keep all agent-facing text in one tree alongside `render_prompt()`; avoid a top-level `skills/` dir whose name implies a framework §7 explicitly declines. Supersedes the illustrative `skills/…` layout drawn in §6.
