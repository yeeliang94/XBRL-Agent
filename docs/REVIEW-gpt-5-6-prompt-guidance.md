# Comprehensive agent-prompt review for GPT-5.6 Luna

**Research date:** 2026-08-24

**Scope:** Every live LLM instruction surface in the filing pipeline, with emphasis on `gpt-5.6-luna`

**Sources:** Repository code, templates, and tests. External guidance is cited only from official OpenAI documentation. Repository-specific recommendations are identified as recommendations or inferences.

**Change status:** Implemented and covered by the complete backend test suite. The
historical finding descriptions below preserve the pre-fix behavior for audit
traceability; the resolution table records the current state.

## Conclusion

The prompt review should optimize the whole instruction surface, not only the Markdown system-prompt files. The effective instruction surface includes the developer or system message, runtime prompt fragments, tool names and descriptions, tool parameter schemas, structured-output schemas, tool results, retry messages, and conversation history. OpenAI notes that function definitions are injected into the model's system context and count as input tokens, while tool definitions and structured-output schemas also form part of a cacheable prompt prefix. [Function calling — Token usage](https://developers.openai.com/api/docs/guides/function-calling#token-usage), [Prompt caching — What can be cached](https://developers.openai.com/api/docs/guides/prompt-caching#what-can-be-cached)

For GPT-5.6, the main priorities are:

1. Remove repeated instructions and examples that do not correct a measured failure.
2. Give every rule one canonical owner so the same requirement is not restated with different wording or scope.
3. Move syntactic output requirements from prose into Structured Outputs or strict function schemas.
4. Keep tool descriptions short but complete about inputs, outputs, and errors. Expose only the tools needed for the current stage.
5. Set reasoning effort and model tier per stage, then compare configurations on representative filings.
6. Prefer the Responses API for direct OpenAI reasoning, tool-calling, and multi-turn work.
7. Place stable instructions, tools, and schemas before run-specific context. Measure GPT-5.6 cache reads and writes instead of assuming that caching helps.
8. Treat prompt edits as behavior changes. Validate them with stage-specific and end-to-end evaluations.

OpenAI reports that, in one internal coding-agent evaluation sample, leaner system prompts improved evaluation scores by about 10–15% while reducing tokens by 41–66% and cost by 33–67%. OpenAI also says these figures are directional and must be validated on the application's own workload. [Using GPT-5.6 — Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#favor-leaner-prompts)

## Executive verdict

The current prompts contain strong accounting safeguards. Evidence is mandatory. Formula cells and abstract rows are protected. The face-statement writer already uses typed tool arguments. The reviewer packets also give the model useful precomputed context instead of making it reconstruct the run.

The correctness and tool-contract defects found in the review have been fixed.
The remaining optimization opportunity is measured prompt reduction: the notes
prompt is still large because it carries substantial accounting and HTML
semantics. Further shortening should be an evaluation-backed ablation, not an
unmeasured deletion of load-bearing rules.

The GPT-5.6 transport caveat remains operationally important. Direct OpenAI
GPT-5.6 uses Responses by default. An unverified LiteLLM proxy remains on Chat
Completions unless `XBRL_OPENAI_RESPONSES=1` is enabled, and GPT-5.6 function
tools on that transport are pinned to effective reasoning `none`. Every run and
trace now records transport plus configured and effective reasoning so this
difference is visible.

### Resolution status

| Finding | Current resolution |
| --- | --- |
| P0-1 SoRE row map | Resolved: obsolete coordinates removed; live writable/formula status is authoritative and template-pinned |
| P0-2 impossible reviewer authoring | Resolved: inventory notes and packet-listed suspected gaps are both guarded authoring scopes |
| P0-3 Luna transport ambiguity | Partially operational: direct Responses routing and runtime attribution implemented; proxy Responses still requires live endpoint verification |
| P1-1 stringified JSON tools | Resolved: notes writes, coverage receipts, and scout save use strict typed arguments; terminal save tools no longer resend the full result |
| P1-2 overloaded notes objective | Substantially reduced: extraction-time styling language and tool fields removed; formatter owns AI styling; further prose reduction requires eval ablation |
| P1-3 instruction conflicts | Resolved for every listed conflict, with rendered-prompt and template-alignment tests |
| P1-4 removed validator promise | Resolved: extraction owns routing; reviewer is described as detection/review, not automatic rewrite |
| P1-5 conditional source tools | Resolved: tools and mode-specific instructions appear together only with a frozen source generation |
| P1-6 source-data framing | Resolved for scout/prior-run context, inventories, review packets, human guidance, and source-note snippets; sentinels are escaped, scalars are capped, and every document-reading role says document commands are data |
| P1-7 stale audit | Resolved: verbatim bodies are mechanically synchronized, helper roles are inventoried, stale copied runtime examples were removed |
| P2-1 spot-check reread | Resolved: starts from the supplied packet and reads a named sheet only for missing detail |
| P2-2 helper settings/scope | Resolved: helper scope wording is singular and all helper agents use explicit configured model settings/cache keys |
| P2-3 Group scope invention | Resolved: only disclosed keys may be supplied; Group and Company values must never be copied across scopes |
| P2-4 GPT-5.6 controls | Resolved for `xhigh`/`max` and provider fallback; the new cache option remains opt-in because the direct paid probe reached OpenAI but was rejected with `429 insufficient_quota` before inference |

## Complete prompt inventory

This inventory covers every live `Agent(...)` construction used by the filing pipeline. The API model-connection test agent is not a filing stage and is excluded.

| Stage | Effective instruction surface | Assessment |
| --- | --- | --- |
| Scanned-PDF sidecar transcription | `ingest/pdf_sidecar.py`, supplied as a bounded one-shot instruction | Concise, structured, and now uses explicit scout settings/cache identity |
| TOC vision extraction | `scout/vision.py` plus `VisionTocResult` | Structured output; returns every readable TOC entry once |
| Main scout | `scout/agent.py`, tool descriptions, dynamic statement list | Complete; final save is one strict typed object |
| Statement-page calibration | `scout/calibrator.py` plus `_PageValidationResult` | Clear, bounded, and uses explicit scout settings |
| Scanned notes-inventory vision | `scout/notes_discoverer_vision.py` plus `_VisionBatch` | Top-level notes remain top-level and sub-notes are nested without promotion |
| Five face extractors | `prompts/_base.md`, one statement/variant prompt, standard block, group overlay, navigation, scout context, prior-year context, denomination, optional template and sign blocks, and tool descriptions | Shared detail workflow is compiled only where applicable; SOPL remains face-first; template-dependent rules are pinned to live workbooks |
| Five notes extractors | `prompts/_notes_base.md`, one sheet prompt, sheet map, column rules, inventory, label catalog, source mode, page context, tool descriptions, and retry messages | Still the largest prompt family; content/routing is now separate from formatter-owned styling |
| Sheet-12 notes fan-out | Same notes prompt plus assigned batch and mandatory typed coverage receipt | Explicit coverage goal with typed writes and receipts |
| Face reviewer | `prompts/reviewer.md` plus review packet and tools | Packet-first, batch-fix, phase-boundary verification with source-derived values framed as data |
| Clean-run spot-check reviewer | `prompts/spot_check.md` plus the already-inlined whole-run fact summary | Starts from the packet and reads only missing named detail |
| Notes reviewer | `prompts/notes_reviewer.md` plus detector packet, source-integrity behavior, and conditional tools | Authoring scope and frozen-source workflows match their deterministic guards |
| Notes formatter | `prompts/notes_formatter.md`, current cells, source pages, size signals, structured patch schema, and repair/self-check messages | Clear content-preservation boundary; structured and JSON-fallback prompts are mutually exclusive |

### Measured prompt size

The following measurements use the live prompt builders without embedded face-template dumps. They do not include tool schemas, page images, tool results, or growing conversation history.

| Prompt sample | Characters | Lines | Approximate words |
| --- | ---: | ---: | ---: |
| SOFP Company MFRS with scout context | 25,464 | 428 | 3,895 |
| SOPL Company MFRS | 17,362 | 285 | 2,680 |
| SOCF Company MFRS | 19,894 | 324 | 3,062 |
| Sheet-12 List of Notes, three-note sample and four labels | 38,640 | 688 | 5,850 |
| Accounting Policies, same sample context | 33,685 | 594 | 5,077 |

After the implemented reduction, `_notes_base.md` is 23,014 characters and
399 lines (down from 27,164 characters and 473 lines). The representative
Sheet-12 prompt is about 10% smaller. It remains a material attention burden
for Luna even though the context window can hold it, so the next reduction
should be a measured ablation of examples and repeated semantics.

## Prioritized findings

### P0-1 — The live MPERS SoRE prompt has obsolete and unsafe row instructions

**Behavior:** `prompts/socie_sore.md:16-34` says opening retained earnings is row 12, profit is row 16, dividends are row 19, and total increase is row 20. It gives row 12 as the worked write coordinate. The current Company and Group workbooks instead place opening retained earnings at row 7, profit input at row 11, the profit total formula at row 12, dividends at row 14, the increase/decrease formula at row 15, and closing retained earnings at row 16.

**Impact:** The worked example targets a formula cell. More seriously, the prompt can send profit to the live closing-retained-earnings row. The formula description at `prompts/socie_sore.md:23` also says `row 17 + row 19`, while the live formula subtracts dividends. `read_template()` is described as authoritative, but Luna must first reject the detailed row map it was just given.

**Recommendation:** Remove the hard-coded row map from this prompt. Prefer unique `field_label` addressing if the writer supports it for SoRE. If explicit coordinates remain necessary, generate the row map from the live template. Add a test that compares every prompt-mentioned writable row and formula sign with `XBRL-template-MPERS/{Company,Group}/10-SoRE.xlsx`. The current tests only prove that the SoRE variant prompt was selected and that it contains positive-dividend wording; they do not validate its coordinates.

### P0-2 — The notes reviewer is instructed to take an action its guard rejects

**Behavior:** `prompts/notes_reviewer.md:38` and the dynamic packet at `notes/reviewer_agent.py:641-648` tell the reviewer to author a real note found in a suspected inventory gap. `prompts/notes_reviewer.md:46` then says authoring is allowed only for a note already in the scout inventory. The deterministic guard enforces the latter at `notes/reviewer_agent.py:247-248` and `notes/reviewer_agent.py:272-276`.

**Impact:** A correctly discovered missed note enters a guaranteed rejection/retry path. The run can end with the suspected gap unresolved even though the reviewer found the disclosure.

**Recommendation:** Define one supported route. The smallest safe prompt-only correction is to tell the reviewer to `raise_flag` for a real note absent from the inventory. A fuller correction can add a narrowly scoped, grounded workflow that records the discovered note in the inventory before authoring it. Pin both the prompt and the guard in one test.

### P0-3 — The normal proxy path can make Luna run with reasoning disabled

**Behavior:** `CLAUDE.md:121-134` documents that `./start.sh` launches a local LiteLLM proxy. `model_settings.py:260-286` enables Responses by default only when GPT-5.6 is direct, unless `XBRL_OPENAI_RESPONSES=1` overrides it. On Chat Completions, `model_settings.py:352-368` pins GPT-5.6 tool calls to reasoning `none`, even if the operator selected another level.

**Impact:** A Luna `medium` result through direct Responses and a Luna result through the normal proxy path are not equivalent tests. Prompt changes can be blamed for a quality loss caused by transport and effort instead.

**Recommendation:** Add endpoint, model tier, and effective reasoning effort to every run trace and evaluation result. Verify that the local proxy supports `/v1/responses`; if it does, enable `XBRL_OPENAI_RESPONSES=1`. Otherwise use the direct OpenAI path for Luna evaluations. OpenAI recommends Responses for reasoning, tool-calling, and multi-turn GPT-5.6 workloads. [Using GPT-5.6 — Migration quickstart](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#update-api-and-model-parameters)

### P1-1 — Notes and scout tools make the model hand-serialize nested JSON strings

**Behavior:** The face writer correctly declares `write_facts(facts: List[FactWrite])` at `extraction/agent.py:1046`. In contrast, notes declare `write_notes(payloads_json: str)` at `notes/agent.py:2615`, manually call `json.loads`, and document a production failure of six consecutive `Invalid JSON: Extra data` results at `notes/agent.py:2627-2636`. `submit_batch_coverage(receipt_json: str)` at `notes/agent.py:2865-2889` and `save_infopack(infopack_json: str)` at `scout/agent.py:1345-1375` use the same fragile pattern.

**Impact:** The model can understand the accounting task and still fail the run because it escaped or framed the payload incorrectly. The long JSON examples then have to remain in the prompt, increasing prompt size without improving accounting judgement.

**Recommendation:** Use typed Pydantic tool arguments: `list[NotesPayloadInput]`, `list[CoverageReceiptInput]`, and an `InfopackInput` object. Put field types, required keys, and enums in the schema. Keep only semantic requirements such as evidence quality, row ownership, and allowed skip reasons in the prompt. Luna supports function calling and Structured Outputs. [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

### P1-2 — The notes prompt is too broad and repeats schema, styling, routing, and coverage policy

**Behavior:** `_notes_base.md` is 473 lines. It teaches hierarchy, cross-sheet routing, HTML, heading ownership, a full formatting-operation language, examples, page batching, source faithfulness, and a hand-written payload schema. Sheet prompts then repeat routing and coverage rules. Tool descriptions repeat the payload shape again.

**Impact:** Luna must track accounting classification, verbatim transcription, HTML construction, table geometry, visual styling, exact labels, provenance, and JSON syntax in the same pass. Important sheet-specific exceptions are surrounded by several thousand words of common material.

**Recommendation:** Split ownership:

- Keep accounting routing, evidence, completeness, and stopping rules in the system prompt.
- Move payload syntax to typed tool schemas.
- Move table styling to the formatter unless an evaluation proves extraction-time `format_ops` improves final fidelity enough to justify the added objective.
- Keep one HTML whitelist and one heading rule.
- Retain examples only for measured failure cases.

Run this as ablations. Remove one instruction group at a time and compare the same filings. OpenAI specifically recommends that method for GPT-5.6. [Using GPT-5.6 — Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#favor-leaner-prompts)

### P1-3 — Shared and stage-specific prompts contain conflicting policies

The following are concrete conflicts, not style preferences:

| Conflict | Locations | Safe correction |
| --- | --- | --- |
| Face base says inspect every linked note and use component rows; SOPL says do not follow revenue/expense notes | `prompts/_base.md:113-133`, `prompts/sopl.md:28-40` | Compile a SOPL-specific base that omits the general note-drill procedure instead of appending an override |
| Catch-all rows are permitted only when the entity is genuinely coarse; SOPL mandates an “Other” leaf even when a breakdown exists | `prompts/_base.md:61-68`, `prompts/sopl.md:53-67` | State the SOPL face-routing exception in one canonical rule and remove the incompatible general text from the SOPL prompt |
| Base permits a flagged unresolved save; both SOFP variants say save only when balanced | `prompts/_base.md:74-88`, `prompts/sofp.md:55`, `prompts/sofp_orderofliquidity.md:47` | Give SOFP the same explicit two-path stop condition: balanced save or grounded unresolved acknowledgement |
| Body subsection labels must use bold paragraphs; later they may use `<h3>` | `prompts/_notes_base.md:63-72`, `prompts/_notes_base.md:285-286` | Reserve `<h3>` for writer-owned parent/sub-note headings; use `<p><strong>` for all labels inside `content` |
| Labels must be copied verbatim; base and Sheet-12 say fuzzy matching is acceptable | `notes/agent.py:384-395`, `prompts/_notes_base.md:134-136`, `prompts/notes_listofnotes.md:175-180` | Require exact catalog labels when a catalog exists; describe fuzzy matching only as backend recovery |
| The shared notes output contract omits `note_num`; Sheet-12 later makes it mandatory | `prompts/_notes_base.md:129-190`, `prompts/notes_listofnotes.md:40-45` | Put the field in the typed Sheet-12 tool schema and omit it from schemas for sheets that do not use it |
| Formatter says “JSON only” and also says there is no JSON to hand-write | `prompts/notes_formatter.md:6-10`, `prompts/notes_formatter.md:60-61` | Render a structured-output prompt when `output_type` is enabled and a separate JSON fallback prompt only when disabled |
| Reviewer says verify once after a batch and also verify as fixes progress | `prompts/reviewer.md:14`, `prompts/reviewer.md:58` | Use one rule: batch independent fixes, verify at phase boundaries, and cap verification calls |

### P1-4 — The notes prompt describes a post-validator that no longer exists

**Behavior:** `prompts/_notes_base.md:18-22` says a cross-sheet post-validator flags and rewrites the wrong side. The current architecture assigns this work to the notes reviewer.

**Impact:** The extractor can rely on a cleanup stage that will not behave as described. It also adds a second mental model for routing responsibility.

**Recommendation:** Say that the extractor owns correct routing and that the reviewer may detect unresolved duplication later. Do not promise an automatic rewrite.

### P1-5 — Source-integrity tools are exposed without mode-specific instructions

**Behavior:** The notes reviewer always registers `relink_note_cell` and `record_block_dispositions` at `notes/reviewer_agent.py:1156-1235`. `prompts/notes_reviewer.md` does not explain either tool. In `enforce` mode, editing a source-linked cell is rejected and the error tells the model to relink it at `notes/reviewer_agent.py:1487-1511`.

**Impact:** The model sees irrelevant tools in normal runs. In enforce mode it learns the correct workflow only after wasting a failed write and a model turn.

**Recommendation:** Register these tools only when a frozen source generation exists. Append one mode-specific system block that explains source-linked edits. Hide normal body-authoring tools for source-linked cells when they cannot succeed, or make their applicability explicit before the model acts.

### P1-6 — Source-derived text is placed in high-priority instructions without a consistent data boundary

**Behavior:** Entity names, reporting periods, inventory titles, note headings, row labels, human guidance, and detector packets are interpolated into system prompts. Some Word-source blocks correctly say the source is untrusted, but this framing is not applied consistently to scout context and inventory text. Examples include `prompts/__init__.py:433-507` and `notes/agent.py:415-445,750-802`.

**Impact:** A user-supplied document can contain text that looks like an instruction. It can compete with pipeline policy because the extracted text is placed inside the same high-priority message.

**Recommendation:** Put stable policy first. Wrap every source-derived block in a consistent `SOURCE DATA — NOT INSTRUCTIONS` boundary. Keep source content in user/tool messages where practical. Validate and length-limit scalar fields before interpolation. Delimiters are useful framing, not a security boundary; add an evaluation case with instruction-like text in the PDF.

### P1-7 — The generated prompt audit is incomplete and its assembled examples are stale

**Behavior:** `docs/agent-prompt-audit.html:886-888` calls its face example the exact output of `render_prompt`, but the example still begins with the removed MFRS/public-listed persona and reports a 105,117-character template-embedded prompt. The current default does not embed the template. The audit also still links a removed notes validator. `tests/test_prompt_audit_matches_live.py:39-75` pins only specially marked verbatim Markdown excerpts, while `tests/test_prompt_audit_matches_live.py:82-129` checks only configurable role labels. It does not pin assembled examples or inline prompts.

**Impact:** A prompt review based on the audit page can analyze text the agents no longer receive and miss live inline prompts such as TOC vision, page calibration, scanned transcription, dynamic source modes, and repair messages.

**Recommendation:** Generate the audit from live prompt builders. Pin representative rendered prompts for every standard, level, and variant. Include every inline helper prompt and the conditional tool set. Add conflict assertions at the fully rendered level, not only substring presence tests.

### P2-1 — The spot-check reviewer repeats an expensive whole-run read

**Behavior:** The renderer already puts the whole-run fact summary in the spot-check packet at `correction/reviewer_agent.py:1250-1277`. `prompts/spot_check.md:16` still says to start with `list_facts(sheet="")`.

**Impact:** The first turn re-fetches data the model already has and reduces the small spot-check budget.

**Recommendation:** Start from the packet. Call `list_facts` only for a named sheet or missing detail.

### P2-2 — Scout helper prompts and settings are inconsistent

**Behavior:** The TOC prompt first asks for financial-statement and notes entries, then asks for all entries at `scout/vision.py:33-48`. The notes-vision prompt says “Only top-level notes” and then requires nested sub-notes at `scout/notes_discoverer_vision.py:136-157`. The main scout and notes-vision helper use `build_model_settings`, but TOC vision, page calibration, and scanned sidecar transcription do not (`scout/vision.py:98-103`, `scout/calibrator.py:110-115`, `ingest/pdf_sidecar.py:118-129`).

**Impact:** Luna may omit calibration entries or sub-notes because the scope is stated twice. The operator's scout reasoning choice does not control every scout LLM call, and one-shot helpers can default to medium reasoning unnecessarily.

**Recommendation:** Say “return every readable TOC entry” once. Say “emit top-level entries and attach, but never promote, nested sub-notes.” Route all helper calls through an explicit stage profile. Compare Luna `none`/`low` for bounded validation and transcription and `low`/`medium` for discovery.

### P2-3 — Group numeric notes can be pressured to invent missing entity scopes

**Behavior:** `prompts/notes_issued_capital.md:19-23`, `prompts/notes_related_party.md:18-21`, and `notes/agent.py:505-515` tell group filings to provide all four Group/Company period keys.

**Impact:** When the note discloses only one scope, a literal reading encourages duplication or invention.

**Recommendation:** Say to provide each disclosed scope only and leave undisclosed keys absent. Never copy Group amounts into Company fields or vice versa.

### P2-4 — GPT-5.6 model controls do not expose the full supported vocabulary

**Behavior:** `model_settings.py:151-187` intentionally offers a cross-provider subset. For GPT-5.6 it exposes `none`, `low`, `medium`, and `high`, but not `xhigh` or `max`. Luna supports both. GPT-5.6 also uses the legacy cache field by default at `model_settings.py:241-257`; the current cache option is opt-in.

**Impact:** The reviewer cannot test all supported GPT-5.6 effort levels through settings, and cache behavior can differ from current OpenAI guidance.

**Recommendation:** Keep portable defaults, but expose model-specific choices when the selected model supports them. Move GPT-5.6 to `prompt_cache_options` after a live compatibility check. Record cache reads, writes, and effective TTL in evaluations.

## Stage-specific model starting points

These are hypotheses to evaluate, not deployment conclusions. They assume direct OpenAI Responses or a verified equivalent proxy.

| Stage | First comparison |
| --- | --- |
| TOC extraction and page validation | Luna `none` vs `low` |
| Scanned-page transcription | Luna `none` vs `low`; score omission and table fidelity |
| Main scout and notes-inventory vision | Luna `low` vs `medium` |
| SOPL coarse extraction and simple SOCI | Luna `low` vs `medium` |
| SOFP, SOCF, SOCIE/SoRE | Luna `medium` vs `high` |
| Corporate information, issued capital, related party | Luna `low` vs `medium` |
| Accounting policies and Sheet-12 List of Notes | Luna `medium` vs `high` |
| Notes formatter | Luna `low` vs `medium` |
| Face and notes reviewers | Luna `medium` vs `high`; test `xhigh` only on unresolved hard cases |

Luna is positioned for high-volume, cost-sensitive work. In line with the
operator's preference, it should be the default for every stage; effort can be
raised selectively when the Luna-only evaluation shows a benefit. This is a
deployment choice to verify, not a guarantee that one effort level fits every
stage. [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

## Evaluation and rollout plan

1. **Record a real baseline.** For every run save prompt version, endpoint, tier, effective effort, tool set, prompt characters, tool turns, cache reads/writes, latency, and cost.
2. **Fix correctness contradictions first.** Correct SoRE and notes-reviewer routing. Add prompt-to-tool and prompt-to-workbook contract tests.
3. **Move syntax into schemas.** Convert notes writes, coverage receipts, and scout save to typed arguments. Measure parse/retry rate before and after.
4. **Create stage scorecards.** Use known-good filings and historical failures. Score concept/value/sign/scope accuracy, note coverage, evidence accuracy, completion before limits, invalid tool calls, and reviewer lift.
5. **Run prompt ablations.** Remove one duplicate group at a time. Start with hand-written JSON schema prose, repeated formatting examples, repeated no-plug wording, and redundant tool descriptions.
6. **Compare model tiers on the same prompts.** Do not change model, effort, prompt, and transport in one experiment.
7. **Add adversarial instruction tests.** Put instruction-like prose in an entity name, note title, table cell, tool result, and human guidance. Confirm system policy still wins.
8. **Promote only measured gains.** The primary metric should be successful filings per unit of total cost and latency, not token price alone.

### Required contract tests

- Every prompt-mentioned template row exists and has the claimed writable/formula status.
- Every prompt-recommended tool action is accepted by the guard under the stated preconditions.
- Fully rendered prompts contain no mutually exclusive entity, standard, variant, sign, save, heading, or label rules.
- Conditional prompts and conditional tool sets agree for source-integrity, source-HTML, scanned-PDF, structured-output, and group modes.
- Prompt audit examples are generated from and byte-match the live renderers.
- Luna evaluations record Responses versus Chat Completions and the effective reasoning effort.

## 1. Lean prompts and instruction ownership

OpenAI's GPT-5.6 guidance says to state each instruction once, remove one group of instructions or examples at a time, expose only relevant tools, and retain examples or style guidance only when they encode a product requirement or correct a measured gap. It also warns that repeated prompt and tool content can become more significant as a conversation grows. [Using GPT-5.6 — Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#favor-leaner-prompts)

### Repository audit rule

Classify every instruction into one of four categories:

| Category | Keep in the system prompt? | Examples in this pipeline |
| --- | --- | --- |
| Hard invariant | Yes, once | Do not invent a disclosed value; do not write abstract rows; use the filing's exact entity scope |
| Stage goal and success condition | Yes, once | What the scout, extractor, reviewer, or formatter must complete before stopping |
| Tool contract | Usually no | Parameter formats, return fields, retry safety, and error meaning belong in the tool definition |
| Output syntax | Usually no | Field names, enums, required keys, and allowed shapes belong in a strict schema |

Create a conflict ledger while reviewing the repository. For each rule, record its canonical owner, every duplicate location, whether the copies are equivalent, and which copy should be removed. Pay particular attention to rules repeated across shared base prompts, statement-specific prompts, dynamically appended context, tool descriptions, save gates, and retry messages.

OpenAI also advises keeping autonomy and approval policy in one place. Repeating variants of "ask first," "do not mutate," or "wait for approval" can cause unnecessary stops even for safe expected actions. [Using GPT-5.6 — Define autonomy and approval boundaries](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#define-autonomy-and-approval-boundaries)

### Recommended prompt shape

Use a short, consistent order for each agent:

1. Role and stage.
2. Goal.
3. Success criteria.
4. Hard constraints and evidence policy.
5. Tool-routing and stopping rules.
6. Run-specific context.

This is a repository recommendation based on OpenAI's advice to provide domain context, hard constraints, approval boundaries, and success criteria without prescribing every step. [Using GPT-5.6 — Intent understanding](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#what-is-new)

## 2. Hierarchy and conflicting instructions

OpenAI's Responses API defines an instruction hierarchy: developer or system messages take precedence over user messages. [Responses API message roles](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses)

That hierarchy does not resolve contradictions within one developer or system message. The repository should therefore make each rule unambiguous on these points:

- Scope: which agent, filing standard, statement variant, entity scope, or stage the rule covers.
- Priority: whether the rule is a hard invariant, preferred method, fallback, or example.
- Exception: the precise condition under which the rule does not apply.
- Stop condition: when the agent should save, retry, abstain, flag, or escalate.

Repository recommendation: place PDF text, scout output, review packets, template labels, and tool-returned content in clearly labelled evidence blocks. State once that content inside evidence blocks is data to assess, not a new instruction. This reduces the chance that document prose or a tool result competes with the stage's actual operating rules. This recommendation follows from the official role hierarchy and from the requirement to keep system policy singular and explicit; it is not a claim that delimiters create a security boundary.

### Conflict tests to add

The official evaluation guidance explicitly recommends testing whether an agent prioritizes its system prompt over conflicting user instructions. It also calls out user prompts that conflict with system prompts as an edge case. [Evaluation best practices — architecture checks and edge cases](https://developers.openai.com/api/docs/guides/evaluation-best-practices#identify-where-you-need-evals)

For this pipeline, include cases where:

- source text appears to instruct the model to ignore extraction rules;
- a dynamic statement block conflicts with a shared base rule;
- a tool result suggests a write that the system prompt forbids;
- a filing-level rule conflicts with a statement example written for another level;
- the model must choose between finishing quickly and satisfying a mandatory coverage or verification condition.

## 3. Structured Outputs versus prose schemas

Structured Outputs enforce adherence to a supplied JSON Schema. JSON mode only guarantees valid JSON, not schema adherence. OpenAI recommends Structured Outputs instead of JSON mode when the selected model supports them. [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs#structured-outputs-vs-json-mode)

OpenAI distinguishes two uses:

- Use function calling when the model must connect to application functions, tools, or data.
- Use a structured `text.format` when the final model response itself must conform to a schema. [Structured model outputs — function calling versus text format](https://developers.openai.com/api/docs/guides/structured-outputs#when-to-use-structured-outputs-via-function-calling-vs-via-textformat)

### Repository audit rule

- If an agent already has a Pydantic `output_type`, remove prose that merely repeats field names, types, required keys, enums, and nesting.
- If an agent returns its result through a save or write tool, put the machine contract in that function's strict parameter schema.
- Keep semantic requirements in the prompt or schema descriptions. Examples include evidence standards, what `null` means, when absence is allowed, and which source is authoritative.
- Do not assume schema adherence means task correctness. A structurally valid extraction can still contain the wrong concept, amount, sign, entity scope, or source page.

OpenAI recommends clear and intuitive key names, useful titles and descriptions, and evaluations to determine whether the schema works for the use case. [Structured model outputs — data-structure tips](https://developers.openai.com/api/docs/guides/structured-outputs#how-to-use-structured-outputs-with-textformat)

The application must also handle refusals and incomplete outputs, because those cases can prevent a response from matching the schema even when Structured Outputs are enabled. [Structured model outputs — handle edge cases](https://developers.openai.com/api/docs/guides/structured-outputs#how-to-use-structured-outputs-with-response-format)

## 4. Tool instructions

OpenAI's GPT-5.6 guidance says to expose only relevant tools and keep their descriptions concise and precise. For programmatic tool workflows, descriptions should document expected return fields, types, and error behavior. [Using GPT-5.6 — Tool orchestration](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#make-routing-instructions-task-specific)

The function-calling guide adds the following guidance:

- Use clear function names and parameter descriptions.
- Explain each parameter's format and what the output represents.
- Use enums and object structure to prevent invalid states.
- Do not ask the model to fill values the application already knows.
- Combine functions that must always run in sequence.
- Keep the initially available function set small; fewer than 20 is a soft target, not a hard limit.
- Evaluate tool accuracy as the available tool count changes. [Function calling — Best practices for defining functions](https://developers.openai.com/api/docs/guides/function-calling#best-practices-for-defining-functions)

### Division of responsibility

Use this division to avoid duplicating tool rules:

| Location | Content |
| --- | --- |
| System prompt | Cross-tool policy, stage order only when required, stopping criteria, side-effect boundaries |
| Tool description | What the tool does, when it is applicable, its inputs, outputs, error modes, and retry safety |
| Tool schema | Required fields, types, enums, and invalid-state prevention |
| Application code | Known arguments, deterministic sequencing, validation, authorization, and irreversible safeguards |

Repository recommendation: audit tool-result strings as prompts. Success messages, validation failures, and retry guidance should report facts and the next valid action. They should not introduce a second, differently worded copy of the system policy.

## 5. Reasoning effort

`gpt-5.6-luna` supports `none`, `low`, `medium`, `high`, `xhigh`, and `max`, and its default reasoning effort is `medium`. The model is designed for cost-sensitive, high-volume workloads and is positioned as roughly corresponding to the earlier nano tier. [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

OpenAI describes the effort levels as workload choices:

- `none`: latency-critical work that does not benefit from reasoning or multi-chained tool calls.
- `low`: efficient tool use, planning, search, and multi-step decisions.
- `medium`: balanced quality and reliability for planning and judgement.
- `high` and above: difficult, quality-first tasks, only when evaluations justify the added latency and cost. [Reasoning models — Reasoning effort](https://developers.openai.com/api/docs/guides/reasoning#reasoning-effort)

OpenAI recommends that a GPT-5.5 or GPT-5.4 migration keep the existing effort as the first baseline, then compare the same setting and one level lower on representative tasks. It recommends `medium` as a balanced GPT-5.6 starting point and `low` for latency-sensitive work. [Using GPT-5.6 — Update API and model parameters](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#update-api-and-model-parameters)

### Repository recommendation

Use Luna for every stage, but do not choose one reasoning setting for every
agent. Use an evaluation matrix such as:

| Stage shape | First configurations to compare |
| --- | --- |
| Bounded classification, page validation, or simple formatting decision | Luna `low` versus `medium` |
| Multi-page extraction with several tool calls and accounting judgement | Luna `medium` versus `high` |
| Reviewer root-cause analysis and difficult reconciliation | Luna `medium`, `high`, and only then `xhigh` |
| Deterministic post-processing | Code, not additional model reasoning |

This table is a workload-routing hypothesis, not an OpenAI guarantee. Promote a stage to a more capable tier or higher effort only when the same evaluation set shows a material gain.

Do not compensate for a low reasoning setting by adding repeated instructions such as "think harder" or long step-by-step reasoning scripts. OpenAI says Pro mode should keep the same outcome-focused prompt and does not require instructions to "use pro mode" or "think harder." [Using GPT-5.6 — Configure pro mode](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#configure-pro-mode-in-the-api)

## 6. Responses API, model routing, and multi-turn state

For direct OpenAI workloads, OpenAI recommends the Responses API for reasoning, tool-calling, and multi-turn workflows. It says reasoning models achieve improved intelligence and performance through Responses even though Chat Completions remains supported. [Using GPT-5.6 — Migration quickstart](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#update-api-and-model-parameters), [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)

GPT-5.6 defaults to persisted reasoning across all turns when earlier response items are available. With `previous_response_id`, earlier reasoning can be made available to later turns. When history is managed manually, OpenAI says to preserve previous user inputs and every response output item; stateless or Zero Data Retention flows should replay the returned encrypted reasoning items. [Using GPT-5.6 — Persisted reasoning](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#update-api-and-model-parameters)

Important state rule: Responses API `instructions` from a previous response are not automatically carried into a new response when `previous_response_id` is used. The application must supply the intended developer or system instructions for the new response. [Responses API create reference — `instructions`](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

### Repository-specific transport caveat

The repository's `CLAUDE.md` gotcha 2 records a separate compatibility constraint: its GPT-5.6 Chat Completions path with function tools pins effective reasoning to `none`, while the direct OpenAI path uses Responses. This means a Luna run through the Chat Completions proxy is not a quality-equivalent test of Luna with `medium` reasoning through Responses. Record endpoint and effective effort alongside every prompt-evaluation result.

Official OpenAI documentation cannot establish whether a third-party OpenAI-compatible proxy preserves Responses semantics, reasoning items, cache fields, or every GPT-5.6 option. Verify those capabilities against the actual proxy before changing the repository's transport gate.

## 7. Context and prompt caching

GPT-5.6 Luna has a 1,050,000-token context window, but that capacity is not a reason to retain duplicate instructions. The GPT-5.6 prompt guidance still recommends lean prompts and warns that repeated prompt and tool content can accumulate during long sessions. [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Using GPT-5.6 — Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#favor-leaner-prompts)

Prompt caching requires exact prefix matches. OpenAI recommends placing stable instructions and examples first and run-specific content last. Tools, tool order, schemas, images, and shared context must also remain identical through the reusable prefix. [Prompt caching — Caching best practices](https://developers.openai.com/api/docs/guides/prompt-caching#caching-best-practices)

For GPT-5.6 and later:

- The minimum cacheable prefix is 1,024 tokens.
- Explicit breakpoints and implicit caching are supported.
- Cache writes cost 1.25 times the uncached input-token rate.
- Cache reads and writes are reported separately as `cached_tokens` and `cache_write_tokens`.
- `prompt_cache_options.ttl` supports `30m`, which is also the default. [Prompt caching — GPT-5.6 behavior](https://developers.openai.com/api/docs/guides/prompt-caching#prompt-caching-for-gpt-56-and-later-models)

The current official control for GPT-5.6 is `prompt_cache_options`; `prompt_cache_retention` applies to earlier-model behavior and is deprecated in the current Responses reference. Any older repository plan recommending `prompt_cache_retention` for GPT-5.6 should be updated before implementation. [Prompt caching — model differences](https://developers.openai.com/api/docs/guides/prompt-caching#how-caching-differs-by-model), [Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

The existing [prompt and caching review](REVIEW-prompts-and-caching.html) recommends `prompt_cache_retention` as an OpenAI lever. That recommendation predates GPT-5.6 and is not current for Luna. Do not copy it into a GPT-5.6 implementation without replacing it with the current `prompt_cache_options` behavior above.

### Repository audit rule

1. Keep the shared base prompt, stable tool definitions, and stable output schema at the start.
2. Put filing-specific data, scout findings, page hints, review packets, and assigned-note batches after the shared prefix.
3. Consider an explicit breakpoint immediately after the stable prefix.
4. Compare implicit mode with explicit-only mode. Explicit-only mode can avoid repeatedly writing a changing run-specific suffix that is unlikely to be reused.
5. Measure `cached_tokens`, `cache_write_tokens`, latency, total input tokens, and task success together.

Caching reduces processing cost and latency; it does not make a bloated or contradictory prompt easier to follow, and it does not guarantee identical outputs. [Prompt caching — Frequently asked questions](https://developers.openai.com/api/docs/guides/prompt-caching#frequently-asked-questions)

## 8. Evaluation strategy

OpenAI recommends evaluation-driven development: evaluate early and often, use task-specific tests that reflect production traffic, log runs so failures become future cases, automate scoring where possible, and calibrate automated graders with human feedback. [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices#how-to-read-evals)

OpenAI also recommends changing one prompt or tool group at a time and rerunning the same evaluations. [Using GPT-5.6 — Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#favor-leaner-prompts)

### Minimum evaluation matrix for this repository

Evaluate each agent stage separately and then evaluate the complete filing:

| Axis | What to measure |
| --- | --- |
| Task completion | Required save or coverage action occurred before the turn cap |
| Instruction following | Hard invariants held under conflicting source text and dynamic prompt blocks |
| Tool selection | Correct tool, correct arguments, no unnecessary repeated calls |
| Extraction quality | Correct concept, value, sign, period, entity scope, and source evidence |
| Completeness | Required statements, notes, and disclosures were not silently omitted |
| Structured contract | Output parsed; refusal and incomplete cases were handled explicitly |
| Review quality | Root cause was corrected without a balancing plug or new regression |
| Efficiency | Turns, tool calls, input tokens, reasoning tokens, cache reads/writes, latency, and cost |

OpenAI's agent evaluation guidance specifically identifies instruction following, functional correctness, tool selection, tool-argument precision, and agent handoffs as distinct sources of nondeterminism that should be tested. [Evaluation best practices — Single-agent and multi-agent architectures](https://developers.openai.com/api/docs/guides/evaluation-best-practices#single-agent-architectures)

The evaluation set should contain ordinary filings, known historical failures, edge cases, and adversarial cases. OpenAI calls out long contexts, ambiguous tool fields, multiple tool calls, multiple handoffs, jailbreak attempts, and user/system conflicts as important edge conditions. [Evaluation best practices — Handle edge cases](https://developers.openai.com/api/docs/guides/evaluation-best-practices#handle-edge-cases)

For model-based grading, OpenAI recommends pairwise comparison or pass/fail for reliability and says to start with the most capable `gpt-5.6` model, then validate agreement against human labels before optimizing the grader for cost or latency. Since the `gpt-5.6` alias routes to Sol, do not use Luna as the sole judge of a Luna-versus-Terra/Sol comparison. [Evaluation best practices — LLM-as-a-judge](https://developers.openai.com/api/docs/guides/evaluation-best-practices#llm-as-a-judge-and-model-graders), [Using GPT-5.6 — Model naming](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#introduction)

The official hosted Evals platform is scheduled for deprecation, but the evaluation principles above are platform-independent and can be implemented in the repository's existing local evaluation harness. [Evaluation best practices — deprecation notice](https://developers.openai.com/api/docs/guides/evaluation-best-practices)

## 9. GPT-5.6 Luna tier suitability

OpenAI positions the GPT-5.6 tiers as follows:

- Sol: flagship capability for complex professional work.
- Terra: balance of intelligence and cost.
- Luna: cost-sensitive, high-volume workloads and the lowest-cost tier. [Models](https://developers.openai.com/api/docs/models), [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

Luna supports function calling and Structured Outputs, so it is technically capable of participating in this pipeline. Technical support does not establish that it meets the accuracy threshold for every stage. [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

### Repository recommendation

Use Luna as the default for every stage, matching the operator's preference.
Use observable failure signals—repeated validation failure, exhausted tool
turns, unresolved evidence conflicts, or a difficult reviewer packet—to raise
Luna's effort for that role. Compare the following on the same filings:

1. Luna at `low` and `medium` for bounded stages.
2. Luna at `medium` and `high` for extraction and review.
3. Luna at `xhigh` only where `high` still misses the required quality bar.

The decision metric should be successful filings per unit of cost and latency, not token price or single-stage accuracy alone.

## 10. Recommended audit order

1. Freeze a representative baseline and record endpoint, model tier, effective reasoning effort, prompt version, tool set, and cache metrics.
2. Build the instruction conflict ledger across prompts, runtime fragments, tools, schemas, and retry messages.
3. Remove exact and semantic duplicates while preserving hard invariants.
4. Move duplicated syntax contracts into strict schemas.
5. Tighten tool descriptions and reduce each stage's available tool set.
6. Reorder stable content before run-specific content and measure GPT-5.6 caching behavior.
7. Compare Luna effort levels and tier escalation on the unchanged evaluation set.
8. Change one prompt group at a time. Retain only changes that improve task success without violating safety, source integrity, or filing correctness.

## Verification performed for this review

- `venv/bin/python -m pytest tests/ -n auto` — 4,684 passed, 2 skipped.
- Focused prompt, typed-tool, source-authority, model-settings, and template-alignment checks were also run during implementation; the final focused audit set passed 28 tests and the final contract correction set passed 19 tests.
- `venv/bin/python -m py_compile …` across every changed Python production module — passed.
- `venv/bin/python scripts/refresh_prompt_audit.py` followed by `tests/test_prompt_audit_matches_live.py` — passed.
- `git diff --check` — passed.

The direct paid Luna probe used `gpt-5.6-luna` on the Responses transport at
`medium` reasoning with a typed function and the new cache option enabled. The
request reached OpenAI, but OpenAI returned `429 insufficient_quota` (no credits
remaining) before inference. No completion or paid token result was produced,
so live task quality and the `prompt_cache_options` request shape remain
unverified until account credits are available. The local transport/model
construction and schema tests pass; that is not a substitute for the blocked
provider call.

## Primary official sources

- [Using GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)
- [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
- [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [Responses API create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
