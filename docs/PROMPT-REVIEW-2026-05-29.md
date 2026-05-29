# Prompt Review — Length & Redundancy Audit

**Date:** 2026-05-29
**Scope:** All agent / system prompts: `prompts/*.md`, the dynamic
`prompts/_sign_conventions.py` block, and the inline scout prompts in
`scout/*.py`.
**Status:** Findings only — no prompt files changed. Each recommendation
flags whether it is *safe to cut* or *pinned by a test* (with the exact
asserted phrase).

> **Read this first.** A large share of the repetition in these prompts is
> deliberate, test-pinned defense — not accident. Gotchas #16 and #17 plus
> ADR-002 are each guarded by string assertions in
> `tests/test_prompt_residual_plug_rule.py`, `tests/test_notes_prompt_phase1.py`,
> and `tests/test_verifier_feedback_wording.py`. The wins below are about
> **consolidating to one authoritative home per assembled prompt**, not
> deleting rules. Verify against the pinned-phrase appendix (§7) before
> editing anything.

---

## 1. Executive summary

The prompts are well-written and domain-rich. They are **not catastrophically
bloated**, but they carry removable fat from two sources:

1. **The same rule restated 4–6× across files that load into the *same*
   context window.** Sign conventions, the no-residual-plug rule, and the
   "follow notes → roll up" procedure each appear in the shared base *and*
   again in every statement file. Within one agent's assembled prompt that is
   direct duplication.
2. **House style explains *why* at paragraph length** where a sentence would
   carry the same constraint.

Realistic, behavior-neutral savings: **~20–30 % off face-extraction prompts**
and **~25 % off the notes prompts**, concentrated in `_notes_base.md`,
`sofp.md`, and the sign-convention prose in `_base.md`.

There is **no single "delete this file" win** — the bloat is distributed.

---

## 2. How prompts assemble (load-bearing for the analysis)

| Agent | Assembled system prompt | Source |
|---|---|---|
| Face extraction | `_base.md` + `{stmt}[_variant\|_standard].md` + **[SCOUT-OBSERVED CONTEXT block]** + nav block (**+ [FACE LINE → NOTE REFERENCES block]** when scout enriched) + [group overlay] + [dynamic sign block for SOCF/SOCIE] | `prompts/__init__.py::render_prompt` |
| Notes | `_notes_base.md` + **sheet-map block** + **target block** + **column-rules block** + `notes_{X}.md` + **[SCOUT-OBSERVED CONTEXT block]** + **inventory block** (now incl. nested `└ sub-note` lines) + [MPERS overlay] + [label catalog] + [page hints + offset] | `notes/agent.py::render_notes_prompt` (the `parts = [...]` assembly, ~line 541) |
| Notes-12 sub-agent | as above + batch inventory | notes Sheet-12 fan-out |
| Correction (legacy) | `correction.md` + failed-checks block + hints | `correction/agent.py` |
| Correction (canonical) | `correction_canonical.md` + **`=== OPEN CONFLICTS ===` block** | `correction/canonical_agent.py::render_canonical_correction_prompt` (~line 42) |
| Monolith | `monolith_face.md` (self-contained) | `monolith/coordinator.py` |
| Scout | inline `_SYSTEM_PROMPT` etc. | `scout/agent.py`, `scout/vision.py`, … |

> **UPDATE 2026-05-29 (scout-coverage diff).** The face and notes assembly
> rows above were extended by the scout-coverage change
> (`docs/PLAN-scout-coverage-quality.md`, CLAUDE.md gotcha #13). Two new
> *conditional* blocks now render when the scout enriched the Infopack:
> a **`=== SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING) ===`** block
> (entity / period / currency / scale-unit / consolidation — face **and**
> notes prompts) and a **`=== FACE LINE → NOTE REFERENCES ===`** block
> (face prompts only, inside scoped navigation). Both are generated in
> `prompts/__init__.py` (`_render_scout_context_block`,
> `_render_face_line_refs_block`), degrade to empty strings when scout
> couldn't enrich, and are **test-pinned** — see §7. They add length only on
> enriched runs, and the scale-unit line deliberately overlaps `_base.md`'s
> "check the statement header for the unit" guidance (a conditional, defensible
> redundancy — see §5).

**Key finding that changes the math:** in the live coordinator path,
`create_extraction_agent` is called **without** `cache_template`
(`coordinator.py:533`), and `cache_template` defaults to `False`
(`extraction/agent.py:369`). So the live template structure is **not** dumped
into the system prompt — the agent calls `read_template()` as a tool instead.

Consequences:
- The prose "TEMPLATE STRUCTURE" sections in `sofp.md`, `socie.md`, etc. are
  **not** duplicated in-context by an embedded dump (my first guess was wrong).
- But several of them **hardcode coordinates** (SOCIE column letters B–X,
  "revaluation → column 9", row ranges 6–25 / 30–49) that the agent re-reads
  live in step 1 anyway. That's drift-prone duplication of live data — exactly
  the brittleness the `__init__.py` comment warns about for MPERS.

---

## 3. Static size inventory (raw .md, pre-assembly)

| File | Bytes | ~Tokens | Notes |
|---|---|---|---|
| `_notes_base.md` | 13.4 K | ~3.3 K | **Heaviest.** Shared across all notes agents. |
| `sofp.md` | 10.3 K | ~2.6 K | ~60 lines are the high-value AFS→SSM mapping table. |
| `notes_listofnotes.md` | 10.0 K | ~2.5 K | Sheet-12 sub-agent. |
| `monolith_face.md` | 7.3 K | ~1.8 K | Self-contained; re-derives shared rules. |
| `_base.md` | 6.5 K | ~1.6 K | Shared across all face agents. |
| `socie_mpers.md` | 5.6 K | ~1.4 K | |
| `sopl.md` | 5.8 K | ~1.5 K | |
| `notes_accounting_policies.md` | 5.5 K | ~1.4 K | |
| `correction.md` | 5.3 K | ~1.3 K | |
| `correction_canonical.md` | 4.9 K | ~1.2 K | |
| `sofp_orderofliquidity.md` | 4.0 K | ~1.0 K | |
| `socie.md` | 3.7 K | ~0.9 K | |
| `_sign_conventions.py` (dynamic) | n/a | varies | Best sign guidance — keep. |
| `notes_validator.md` | 3.2 K | ~0.8 K | Tight; leave. |
| `socie_sore.md` | 2.9 K | ~0.7 K | |
| `socf.md` | 2.8 K | ~0.7 K | |
| `notes_corporate_info.md` | 2.4 K | ~0.6 K | Tight; leave. |
| `soci.md` | 2.2 K | ~0.6 K | Tight; leave. |
| `notes_issued_capital.md` | 2.1 K | ~0.5 K | Tight; leave. |
| `notes_related_party.md` | 1.8 K | ~0.5 K | Tight; leave. |
| `_group_overlay.md` | 1.7 K | ~0.4 K | |
| `_group_socie_overlay.md` | 1.6 K | ~0.4 K | |
| Scout `_SYSTEM_PROMPT` | 3.2 K | ~0.8 K | Lean; leave. |
| Scout `_VISION_SYSTEM_PROMPT` | 1.1 K | ~0.3 K | Leave. |
| Scout `_TOC_EXTRACTION_PROMPT` | 0.9 K | ~0.2 K | Leave. |
| Scout `_VALIDATION_PROMPT` | 0.4 K | ~0.1 K | Leave. |

**Per-agent assembled totals (static, before PDF/tool output):**
- SOFP face agent ≈ `_base` + `sofp` + nav ≈ **~4.5 K tokens** (**+ ~0.2–0.5 K**
  on scout-enriched runs for the SCOUT-OBSERVED CONTEXT + FACE LINE → NOTE
  REFERENCES blocks)
- Accounting-policies notes agent ≈ `_notes_base` + `notes_accounting_policies` **+ sheet-map + target + column-rules + inventory + [MPERS overlay] + [label catalog] + [page hints]** ≈ **~6–7 K tokens** (the notes figure is materially higher than `_notes_base + notes_X` alone — peer-review Finding 5)

These are defensible for meticulous extraction, but both are the prime trim
targets.

> **Caveat on the percentages (peer-review Finding 5).** The "~25 % off notes
> prompts" headline measures the trimmable `.md` files (`_notes_base.md` +
> `notes_{X}.md`) against themselves. Because `render_notes_prompt` also
> appends several dynamic blocks (sheet map, target, column rules, inventory,
> MPERS overlay, label catalog, page hints), those `.md` files are a smaller
> fraction of the *delivered* system prompt than the raw byte counts suggest.
> The trim targets are still valid; the % saving against the full assembled
> prompt is lower than against the `.md` files in isolation.

---

## 4. Cross-cutting redundancy (the real wins)

### 4.1 Sign conventions — up to 3 copies per face agent, 6 files carry the dividend rule

- `_base.md` → full "SIGN-CONVENTION TROUBLESHOOTING" block covering
  SOPL / SOCF / SOCIE / OCI (lines ~82–104).
- Each statement file repeats its own slice: `sopl.md` CRITICAL RULES,
  `socf.md` CRITICAL RULES, `socie.md` dividend block, `soci.md` OCI sign.
- `_sign_conventions.py` *also* injects a live per-row block for SOCF/SOCIE.
- **The dividends-paid-positive rule appears in 6 files:** `_base.md`,
  `socie.md`, `socie_mpers.md`, `socie_sore.md`, `correction.md`,
  `monolith_face.md`.

A SOFP agent (no sign issues) still hauls SOPL+SOCF+SOCIE+OCI sign prose from
`_base.md` for nothing.

**Recommendation (REVISED 2026-05-29 after peer review):** treat
`_sign_conventions.py` as canonical **for formula-derived SOCF / SOCIE / SoRE
row signs only** — it is injected solely for those statements and only when
`template_path` is present (`test_socf_sign_convention.py`:
`test_render_prompt_no_sign_block_for_non_socf`,
`test_render_prompt_omits_block_when_template_path_missing`). It is NOT a
general-purpose sign source and cannot replace the static prose.

**This recommendation is heavily test-pinned — the prior "prose is safe to
consolidate" claim was WRONG.** The sign/dividend *prose* is asserted by tests
in **seven files**, so it cannot be reduced to a pointer:

- `_base.md` — `test_base_prompt_has_sign_convention_troubleshooting` pins
  `sign-convention troubleshooting`, `do not infer the sign from wording
  alone`, `foreign exchange loss`, `if the formula subtracts a row`. The
  "reduce `_base.md` to a 2–3 line pointer" idea **would fail CI**.
- `sopl.md` — `test_sopl_prompt_keeps_loss_expenses_positive` pins
  `loss-labelled expense rows are also positive magnitudes`,
  `foreign exchange loss`, `impairment loss on trade receivables`.
- `socie.md` / `socie_mpers.md` / `socie_sore.md` —
  `test_equity_prompts_follow_dividend_formula_sign` pins (all three)
  `do not apply the sopl`, `dividends paid are entered as positive
  magnitudes`, `subtracts the dividends row`/`formula subtracts it`,
  `reconciles to sofp`.
- `monolith_face.md` — `test_monolith_prompt.py` pins `dividends` + `positive`.
- `correction.md` — `test_correction_agent.py` pins `dividends as a positive
  magnitude` and `nearest subtotal formula subtracts a row`.

**Net:** the achievable sign-convention win is much smaller than first stated.
The genuinely cuttable surface is limited to *non-pinned connective prose*
around these sentinel phrases (e.g. redundant lead-ins, extra examples beyond
the pinned ones). The pinned sentences themselves stay. Re-scope this item
from "biggest win" to "modest cleanup" and run the seven test files above
after any edit.

### 4.2 No-residual-plug — 2 copies per SOFP/SOPL agent

- `_base.md` "INTEGRITY RULE — NEVER PLUG RESIDUALS" (~23 lines).
- `sofp.md` "NO-RESIDUAL-PLUG RULE (sub-sheet)" (~15 lines).
- `sopl.md` CRITICAL RULES catch-all discussion.
- Plus copies in `correction.md`, `correction_canonical.md`, `monolith_face.md`.

`_base.md` + `sofp.md` both land in the SOFP agent saying the same thing with
different example lists.

**Recommendation:** keep `_base.md` as canonical; reduce each statement file
to the *statement-specific row names only* (the genuinely new part).

**Test-pinned — do NOT cut these exact strings** (from
`test_prompt_residual_plug_rule.py`):
- `_base.md` must contain `NEVER use a catch-all row` **and**
  (`balancing figure` OR `balancing plug`).
- `sofp.md` must contain `NO-RESIDUAL-PLUG RULE` (named section),
  `other property, plant and equipment`, `never plug a residual`/`never plug`,
  `genuinely coarse`, `motor vehicles`, `construction in progress`.
- `sopl.md` must contain `catch-all`/`catch all` + `never` +
  (`balancing`/`plug`/`residual`).
- `correction.md` must contain `NEVER write a residual`/`NEVER plug` +
  `catch-all`.

So `sofp.md`'s no-plug section can be *tightened* but must keep the named
header and that specific vocabulary.

### 4.3 Extraction procedure described twice per face agent

`_base.md` "ACCOUNTANT EXTRACTION PROCEDURE" (read template → list note refs →
follow notes → roll up → evidence) is re-walked as a numbered "STRATEGY" in
every statement file. ~80 % overlap.

**Recommendation:** `_base.md` owns the generic procedure; statement files keep
only statement-specific deltas. SOFP's "fill the **sub-sheet first** because
face cells are formulas" is genuinely specific — keep it. The generic "call
read_template, view the face page, follow note refs, roll up" preamble in each
statement file is the cuttable part.

**Test-pinned:** `_base.md` must keep `accountant extraction procedure`,
`before writing any face-statement line that has a note reference`, and
`only write a lump-sum face value` (`test_base_prompt_requires_following_linked_notes_before_lumping`).
`sofp.md` must keep the `linked-note cash case` worked example with
`cash on hand`, `fixed deposits with licensed banks`, and
`do not write rm1,200,000 only to the face statement`
(`test_sofp_prompt_has_linked_note_split_example`).

### 4.4 Notes: `(a)/(b)` preservation stated 3× in one file + repeated across files

In `_notes_base.md` alone the in-prose sub-section preservation rule appears:
1. "NOTE HIERARCHY AND GRANULARITY" section,
2. "Heading markup is writer-owned" section (scoping clarification),
3. a full worked-example JSON payload (Note 2.14, ~15 lines of policy prose).

Then it repeats in `notes_accounting_policies.md` and is alluded to in
`notes_listofnotes.md`.

**Recommendation:** the three in-file copies can collapse to (1) the rule +
(1) a short worked example. The cross-file copies in
`notes_accounting_policies.md` are partly forced (that agent doesn't re-read
`_notes_base.md` mentally, and the test pins it).

**Test-pinned — keep these exact strings** (from `test_notes_prompt_phase1.py`):
- `_notes_base.md`: `preserve the sub-section labels themselves in the body`,
  `<strong>(a) short term benefits</strong>`, `<strong>(b) defined
  contribution plans</strong>`, `"number": "2.14"`, plus the hierarchy
  phrases `not like a text splitter`, `finance costs`,
  `interest on lease liabilities`,
  `do not split content into a different template row merely because`.
- `notes_accounting_policies.md`: `preserve any "(a)/(b)/(i)/(ii)" sub-section
  labels` + (`do not flatten them`/`do not strip`).

So the worked example **cannot** be deleted — but the *duplicate prose
explanations around it* can be trimmed.

### 4.5 Minor cross-file repeats (low value, easy)

- "Cite the PDF page, not the printed folio" — `_notes_base.md` **and**
  `notes_listofnotes.md`. **Both test-pinned** (`pdf page` + `printed
  folio`/`printed page`) — keep one sentence in each, drop the surrounding
  explanation.
- "Issue independent tool calls in one response" / batching — `_base.md`,
  `_notes_base.md`, and the scoped-nav block. Three near-identical paragraphs;
  one home suffices.
- "Use `calculator()`, don't compute mentally" — `_base.md`, `_notes_base.md`,
  `correction.md`, `correction_canonical.md`. Fine to keep one per assembled
  prompt; today some agents see it twice.

---

## 5. Per-file findings

### `_notes_base.md` (heaviest, ~3.3 K tokens) — **trim ~25–30 %**
Strong content, over-explained. OUTPUT CONTRACT + CELL FORMAT + ALLOWED HTML
TAGS + worked examples run ~150 lines. The three worked-example payloads can
become one or two. The `(a)/(b)` rule is stated 3× (§4.4). The ALLOWED HTML
TAGS list duplicates the per-tag guidance already in CELL FORMAT. **Keep** the
test-pinned strings in §7.

### `sofp.md` (~2.6 K tokens) — **trim ~20 %, keep the mapping table**
The "AFS NOTE → SSM ROW MAPPING (known confusing cases)" section (~60 lines)
is your **highest-value content** — hard-won mapping knowledge a model won't
infer. **Keep it intact.** The redundancy is that TEMPLATE STRUCTURE +
STRATEGY + FAILURE MODE + WORKED EXAMPLES + CRITICAL RULES restate
"fill sub-sheet first / let template drive granularity" ~4 times. Consolidate
those into one. Keep test-pinned strings (§4.2, §4.3).

### `socie.md` / `socie_sore.md` — **DO NOT de-hardcode the MFRS row ranges (test-pinned); only the docstring/wording is touchable**
**REVISED 2026-05-29 after peer review.** The original recommendation here —
"trim the column letters / `revaluation → column 9` / row ranges 6–25, 30–49
and let coordinates come from the tool" — **would fail CI.**
`tests/test_socie_prompt_mpers.py::TestMfrsSocieDefaultPromptUnchanged`
explicitly pins the MFRS SOCIE prompt as still containing `matrix`, `6-25`,
and `30-49` (the test rationale: "If these vanish, the MFRS SOCIE agent will
lose its anchoring"). The MFRS coordinate description is an intentional
contract, not accidental drift-risk.

These coordinates are therefore **only** changeable if the test and prompt
contract are revised together — out of scope for a length trim. The genuinely
safe cleanup here is limited to non-pinned connective prose. `socie_sore.md`'s
row-by-row formula list (rows 12–21) is not pinned by that test, but verify it
isn't asserted elsewhere before touching it. **Also note** the negative pin
`test_socie_mpers_group_section_does_not_advertise_efg_columns`: `socie_mpers.md`
must NOT reintroduce `additionally use: e (col=5)` / `f (col=6) = evidence`,
and MUST keep `no additional value columns`/`no e/f`.

### `sopl.md` (~1.5 K tokens) — **light trim**
CRITICAL RULES restates the integrity/no-plug rule from `_base.md` at length.
Keep the Function-vs-Nature catch-all distinction (genuinely specific) and the
test-pinned vocabulary; trim the generic restatement.

### `monolith_face.md` (~1.8 K tokens) — **defer or factor out**
Self-contained and largely fine, but it re-derives sign conventions, no-plug,
and abstract-row rules that already live in the shared files — effectively a
4th copy. If the monolith path stays experimental (schema-v10 `orchestration`
flag), low priority. If it goes to production, factor shared rules into a
shared include. The worked-example and tool-contract sections are appropriate.

### `correction.md` (~1.3 K tokens) — **leave**
Its "SIGN-CONVENTION REPAIR RULES" duplicate `_base.md`, but the correction
agent does **not** load `_base.md`, so this is justified (no other source).
Well-scoped. No action.

### `correction_canonical.md` (~1.2 K tokens) — **leave**
Tight and specific to the concept-tree API. No action.

### `soci.md`, `socf.md`, `sofp_orderofliquidity.md` — **light**
`socf.md` sign rules overlap `_base.md` + the dynamic block; trim once §4.1
lands. `soci.md` is already tight. `sofp_orderofliquidity.md` repeats the SOFP
procedure but for a standalone (no-formula) sheet — keep the "main sheet is
standalone, won't be overwritten" distinction, trim the rest to match the
slimmed `sofp.md`.

### `notes_corporate_info.md`, `notes_issued_capital.md`, `notes_related_party.md`, `notes_validator.md` — **leave**
All tight and task-specific. No meaningful fat.

### `notes_listofnotes.md` (~2.5 K tokens) — **light trim**
Long but mostly justified (coverage-receipt protocol, scope-boundary rules).
The MATCHING HEURISTICS + the accounting-policies scope boundary partly
restate `_notes_base.md`'s hierarchy rule. Trim the overlap; keep the
coverage-receipt spec and the test-pinned `pdf page`/`printed folio` and
hierarchy phrasing.

### Scout prompts — **leave**
`_SYSTEM_PROMPT` (~0.8 K) and the three smaller inline prompts are lean and
appropriate.

### `prompts/__init__.py` docstring (not a prompt, but stale)
The `filing_standard` arg docstring (lines ~33–40) still says the MPERS
overlay is "reserved / Phase 6.2 … not needed yet" — but `socie_mpers.md` now
exists and the precedence comment below it documents the real behavior. The
docstring contradicts the code. Worth a one-line fix while you're in here.

### Scout-coverage blocks (ADDED 2026-05-29) — net-new conditional content
The scout-coverage diff added two conditional blocks to face prompts (and the
context block to notes prompts) plus a new `_base.md` bullet about the
face-line map. Two small redundancies are worth noting, both **defensible**:
- The SCOUT-OBSERVED CONTEXT block's scale-unit line ("verify the unit … 1000×
  error") overlaps `_base.md`'s existing "Values are often in RM thousands —
  check the statement header for the unit". The scout version is conditional
  (only on enriched runs) and carries the louder, higher-value 1000× warning —
  keep both; do not try to merge (the scout line is test-pinned, §7).
- The new `_base.md` bullet ("the scout's face-line map … is a starting index,
  NOT a substitute for reading the linked note pages") restates the verify
  framing the FACE LINE → NOTE REFERENCES block already carries. This is the
  *right* place for it — `_base.md` always loads, the block is conditional — so
  it's intentional reinforcement, not bloat. No action.

These are flagged for completeness; neither is a trim target.

---

## 6. Rule → file duplication map

**REVISED 2026-05-29 (peer-review Finding 6):** the "same context" column is
now scoped **by agent family**. A face agent loads exactly **one** statement
prompt (`_base` + *one* of `sofp`/`sopl`/…), never two together; and the face
family (`_base.md`) and notes family (`_notes_base.md`) never co-occur in a
single agent. The earlier "`_base`+`sofp`+`sopl` → yes" row was wrong.

| Rule | Files that carry it | True in-window co-occurrence |
|---|---|---|
| No-residual-plug | `_base.md`, `sofp.md`, `sopl.md`, `correction.md`, `correction_canonical.md`, `monolith_face.md` | **Yes**, `_base` + the *one* loaded statement file (e.g. `_base`+`sofp`). NOT `sofp`+`sopl` together. |
| Dividends-paid-positive | `_base.md`, `socie.md`, `socie_mpers.md`, `socie_sore.md`, `correction.md`, `monolith_face.md` | **Yes**, `_base` + the one SOCIE-family file actually loaded (`socie` XOR `socie_mpers` XOR `socie_sore`). |
| Sign-from-formula | `_base.md`, `sopl.md`, `socf.md`, `socie.md`, `soci.md`, `correction.md`, `monolith_face.md`, `_sign_conventions.py` | **Yes**: `_base` (static) + the one statement file + (SOCF/SOCIE/SoRE only) the dynamic block → up to **3 layers** for those three statements; 2 for others. |
| Extraction procedure / follow notes before lumping | `_base.md`, `sofp.md`, `sopl.md`, `sofp_orderofliquidity.md` | **Yes**, `_base` + the one loaded statement file. |
| `(a)/(b)` preservation | `_notes_base.md` (×3 internally), `notes_accounting_policies.md`, `notes_listofnotes.md` | **Yes (3× within `_notes_base.md` itself)**, plus once more in whichever notes file is loaded. |
| Cite PDF page not folio | `_notes_base.md`, `notes_listofnotes.md` | **Yes** — both load in the Sheet-12 sub-agent. |
| Tool-call batching | `_base.md` (face), `_notes_base.md` (notes), nav block | Within face agents: `_base`+nav. Within notes agents: `_notes_base`. **`_base` and `_notes_base` never co-occur** — this is cross-family, not same-window. |
| Use calculator, not mental math | `_base.md` (face), `_notes_base.md` (notes), `correction.md`, `correction_canonical.md` | At most **2× in one agent** (e.g. `_base` repeats it; correction files repeat it). `_base`+`_notes_base` is cross-family, not same-window. |

The genuine in-window duplication worth consolidating first is the
`_base` + one-statement-file overlap and the 3× internal repetition inside
`_notes_base.md` — **subject to the §7 pins**, which block most of the
sign/dividend rows above.

---

## 7. Appendix — test-pinned phrases (must survive any edit)

From `tests/test_prompt_residual_plug_rule.py`:
- `_base.md`: `NEVER use a catch-all row`; `balancing figure` or `balancing plug`
- `sofp.md`: `NO-RESIDUAL-PLUG RULE`; `other property, plant and equipment`;
  `never plug a residual`/`never plug`; `genuinely coarse`; `motor vehicles`;
  `construction in progress`
- `sopl.md`: `catch-all`/`catch all`; `never`; `balancing`/`plug`/`residual`
- `correction.md`: `NEVER write a residual`/`NEVER plug`; `catch-all`

From `tests/test_notes_prompt_phase1.py`:
- `_notes_base.md`: `pdf page`; `printed folio`/`printed page`;
  `SCHEDULE`/`SCHEDULES`; `do not drop`/`do not replace`;
  `note hierarchy and granularity`; `not like a text splitter`;
  `finance costs`; `interest on lease liabilities`;
  `do not split content into a different template row merely because`;
  `preserve the sub-section labels themselves in the body`;
  `<strong>(a) short term benefits</strong>`;
  `<strong>(b) defined contribution plans</strong>`; `"number": "2.14"`
- `notes_listofnotes.md`: `pdf page`; `printed folio`/`printed page`;
  `SCHEDULE`/`SCHEDULES` (rendered); hierarchy-beats-visual-granularity phrasing
- `sofp.md`: `matching sub-sheet field`; `lump sum` + `face sheet`;
  `linked-note cash case`; `cash on hand`;
  `fixed deposits with licensed banks`;
  `do not write rm1,200,000 only to the face statement`;
  must NOT contain `one sub-sheet row per breakdown line` /
  `must write 5 sub-sheet rows` (a previously-rejected rule)
- `sopl.md`: `matching` + `analysis`; `lump`/`single line`
- `_base.md`: `accountant extraction procedure`;
  `before writing any face-statement line that has a note reference`;
  `only write a lump-sum face value`
- `notes_accounting_policies.md`: `preserve any "(a)/(b)/(i)/(ii)" sub-section
  labels`; `do not flatten them`/`do not strip`; `2-3 pages`; `supersede`;
  small read→write cycle phrasing

From `tests/test_verifier_feedback_wording.py` (verifier code, not a prompt,
but related): SOFP imbalance feedback must say `assets section is lower` /
`equity+liabilities section is lower` (directional) and must carry a
no-plug clause + a leave-the-gap clause; must NOT use the old directive
`Action:` wording.

**Sign / dividend prose pins (ADDED 2026-05-29 after peer review — these were
missing from the first draft and are the doc's biggest correction):**

From `tests/test_notes_prompt_phase1.py`:
- `_base.md` (`test_base_prompt_has_sign_convention_troubleshooting`):
  `sign-convention troubleshooting`; `do not infer the sign from wording
  alone`; `foreign exchange loss`; `if the formula subtracts a row`
- `sopl.md` (`test_sopl_prompt_keeps_loss_expenses_positive`):
  `loss-labelled expense rows are also positive magnitudes`;
  `foreign exchange loss`; `impairment loss on trade receivables`
- `socie.md`, `socie_mpers.md`, `socie_sore.md`
  (`test_equity_prompts_follow_dividend_formula_sign`, all three files):
  `do not apply the sopl`; `dividends paid are entered as positive
  magnitudes`; `subtracts the dividends row`/`formula subtracts it`;
  `reconciles to sofp`
- `notes_listofnotes.md` (hierarchy test ~line 295):
  `one finance-costs payload`; `do not move the lease-interest sub-section`
- scout `_SYSTEM_PROMPT` (`test_scout_prompt_preserves_face_note_references`):
  `capture the note-reference column`; `best-effort note page hints`

From `tests/test_socie_prompt_mpers.py`:
- `socie.md` (MFRS, `TestMfrsSocieDefaultPromptUnchanged`): `matrix`;
  `6-25`; `30-49`
- `socie_mpers.md` (`test_mentions_mpers_explicitly`): `mpers` (case-insensitive)
- `socie_mpers.md` negative pin
  (`test_socie_mpers_group_section_does_not_advertise_efg_columns`): must NOT
  contain `additionally use: e (col=5)` or `f (col=6) = evidence`; MUST
  contain `no additional value columns` or `no e/f`
- `socie_sore.md` (`TestMpersSoreVariantStillWins`): `Retained Earnings`

From `tests/test_monolith_prompt.py`:
- `monolith_face.md`: `dividends` + `positive` (SOCIE dividend sign)

From `tests/test_correction_agent.py`:
- `correction.md`: `dividends as a positive magnitude`;
  `nearest subtotal formula subtracts a row`

**Implication:** sign/dividend prose is pinned across **seven files**. The §4.1
"consolidate sign conventions" item is therefore the *most* constrained, not
the least — almost every copy has a guarding test. Treat §4.1 as a modest
connective-prose cleanup, not a structural consolidation.

**Generated-block pins (ADDED 2026-05-29 — scout-coverage diff).** These are
asserted on the *rendered* prompt, not on a `.md` file — they live in
`prompts/__init__.py`, so a future "trim the prompt blocks" effort must respect
them too:

- `_render_scout_context_block` (`tests/test_prompts_render_context.py`):
  `SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING)`; `thousands (RM '000)`;
  `1000×`; `UNKNOWN` + `MUST read` (the `scale_unit="unknown"` path);
  `Currency: USD` only when currency ≠ RM; `Consolidation level: group` only
  when set; empty dict / all-default → **must return `""`** (degrade-gracefully
  contract).
- `_render_face_line_refs_block` / `_build_scoped_navigation`
  (`tests/test_prompts_render_scout_face_refs.py`):
  `FACE LINE → NOTE REFERENCES`; `VERIFY against the PDF`; section grouping
  like `[non-current assets]`; `jump straight to` (when
  `face_read_in_detail=True`); `starting hypothesis` + `verify` (when False);
  empty list → **must return `""`**.
- `_render_inventory_preview` nested sub-notes
  (`tests/test_inventory_preview_renders_hierarchy.py`): the count line stays
  top-level-only (`Scout identified N notes`), with `└ Note <subnote_ref>`
  child lines.

---

## 8. Prioritized recommendation

If/when you decide to act, in descending value-per-effort
(**re-ordered 2026-05-29 after peer review** — sign-convention consolidation
dropped from #1 to a minor item because §7 pins block most of it; SOCIE
de-hardcoding removed entirely as test-blocked):

1. **Trim `_notes_base.md`** (§4.4, §5) — now the **biggest real win**: the
   heaviest file, with the `(a)/(b)` rule stated 3× and triple worked
   examples. Keep the §7-pinned strings (hierarchy phrases, the `2.14` worked
   example, `pdf page`/`printed folio`); cut the surrounding duplicate prose.
2. **Trim `sofp.md`** (§5) — consolidate the 4× restatement of "fill sub-sheet
   first / let template drive granularity"; **keep** the AFS→SSM mapping table
   and all §7-pinned strings.
3. **Collapse procedure duplication** (§4.3) — `_base.md` owns generic steps;
   statement files keep only deltas. Mind the `_base.md` procedure pins in §7.
4. **De-dupe no-residual-plug connective prose** (§4.2) — tighten `sofp.md`/
   `sopl.md` around the pinned sentinels; keep `_base.md` canonical.
5. **Fix the stale `__init__.py` docstring** (§5) — trivial correctness, no
   test risk.
6. **Sign-convention prose cleanup** (§4.1) — **downgraded**: pinned in seven
   files, so only non-pinned lead-ins/extra examples are touchable. Low
   value-per-effort; do last, if at all.
7. **Defer `monolith_face.md`** unless that path is going to production (and
   note `test_monolith_prompt.py` pins its dividend sign).

**Removed from the plan:** "de-hardcode SOCIE coordinates" — blocked by
`test_socie_prompt_mpers.py`, which pins `matrix`/`6-25`/`30-49` as an
intentional MFRS contract. Only revisable by changing test + prompt together.

**Process guardrail (per CLAUDE.md "How to Behave Here"):** every edit near a
pinned rule is only "done" when the **full prompt-pin test set** passes. The
first draft listed only three files — that was incomplete (peer-review
Finding 3). The recommended edits touch sign prose, SOCIE/MPERS, monolith, and
correction prompts, so the command must include all of:

```bash
python -m pytest \
  tests/test_prompt_residual_plug_rule.py \
  tests/test_notes_prompt_phase1.py \
  tests/test_verifier_feedback_wording.py \
  tests/test_socf_sign_convention.py \
  tests/test_socie_prompt_mpers.py \
  tests/test_monolith_prompt.py \
  tests/test_correction_agent.py \
  -v
```

Run it after each file, not at the end. (If you grep for `_PROMPT_DIR` /
`read_text` / `_SYSTEM_PROMPT` across `tests/`, add any further files that
assert prompt strings — the list above is the known set as of this review,
not a proof of completeness.)
