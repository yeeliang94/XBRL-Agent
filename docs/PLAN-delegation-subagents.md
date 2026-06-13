# PLAN — Delegation sub-agents (item 29)

> Status: **design** (expanded 2026-06-13; original charter 2026-06-12). This is
> the design-first spec the orchestration-hardening plan (item 29) requires
> before any code lands. It is NOT yet a frozen API contract — signatures below
> are the proposed shape, to be confirmed against the tree when the prototype is
> cut. Prototype-gated: build on ONE statement behind a flag, measure with the
> eval regression harness (item 26, `scripts/eval_regression.py`), then decide
> whether to widen.

---

## 1. Problem

A face-extraction agent runs one long `agent.iter()` conversation that does
**three jobs in one context**: reading the PDF (page images + deep note detail),
writing facts to the template, and verifying/saving. The reading job is the
context hog — a single deep note read (e.g. SOFP needing the full PPE movement
schedule across three pages, or the deferred-tax reconciliation) pulls multiple
rendered page images and long prose into the message history. Because
pydantic-ai re-bills the full history every turn, that detail is **paid for on
every subsequent write/verify turn**, not just the turn that fetched it.

`extraction/history_processors.py` and item 30's compaction attack this from the
*transport* side (trimming stale tool results before re-send). Delegation
attacks it from the *structural* side: never let the heavy read into the
parent's history in the first place.

The Sheet-12 fan-out (`notes/listofnotes_subcoordinator.py`) already proves the
cheap-sub-agent pattern works end-to-end (spawn → retry → receipt → usage
rollup). Nothing today lets a **face** agent use it.

## 2. Goal & non-goals

**Goal.** Let a face agent **delegate a focused note read** to a cheap,
short-lived, read-only sub-agent that returns a compact *structured* summary, so
the parent's context stays lean. The parent retains ownership of template
writing, verification, the save gate, and coverage receipts — delegation
offloads *reading only*.

**Non-goals (explicitly out of scope for the prototype).**

- No sub-agent **writes**. Writing, `verify_totals`, `save_result`, the
  no-residual-plug guard (gotcha #17), and DB projection (gotcha #21 Phase B)
  stay parent-owned. A sub-agent that could write would bypass every save-side
  invariant.
- No **default-on**. Ships behind `XBRL_DELEGATE_READS` (default off) on one
  statement until measured.
- No **deterministic note→sheet routing** is introduced (that would trip gotcha
  #14 — but that rule governs the *notes* pipeline; face delegation is
  question-driven, not note-table-driven, so it stays clear of it by design).
- No new heavy storage. Sub-agent transcripts are not persisted per-read in the
  prototype (see §7 — only a rollup counter + the parent trace mention them).

## 3. Proposed shape

### 3.1 The tool (parent side)

Registered on the face agent in `extraction/agent.py`'s `create_extraction_agent`
**only when `deps.delegation_enabled`** (mirrors the conditional registration of
`submit_face_coverage` at `extraction/agent.py:924–961`):

```python
@agent.tool
async def delegate_note_read(
    ctx: RunContext[ExtractionDeps],
    note_num: int,
    question: str,
) -> str:
    """Hand a focused note-reading question to a cheap read-only sub-agent.

    Use when you need figures from a note whose detail would bloat your own
    context (movement schedules, reconciliations, multi-page breakdowns).
    Returns ONLY a compact structured summary — the figures, their PDF
    page(s), and a one-line ambiguity note. You still write and verify.
    Advisory: if the summary is insufficient you may read the note yourself.
    """
```

- **Async tool**, not sync. The sub-agent is itself an `await`ed
  `run_agent_loop`; an async `@agent.tool` runs on the event loop without the
  anyio-worker-thread hop that sync tools take (relevant to gotcha #22 — no
  shared-workbook save happens here anyway, since the sub-agent can't write, but
  staying async avoids a needless thread).
- **Input is a question + note number, never a page range.** The sub-agent
  navigates with scout hints + `search_pdf_text`, preserving gotcha #13's
  soft-hint discipline (no `allowed_pages`, no page restriction).
- **`note_num` is validated** against the same plausibility bound item 4 added
  (`MAX_PLAUSIBLE_NOTE_NUM`); an out-of-range note returns a degraded message
  rather than spawning a sub-agent on a hallucinated note.

### 3.2 Structured return schema

The sub-agent is forced to emit this (small, capped) shape; the tool serialises
it to a string for the parent. Define in a new `extraction/delegation.py`:

```python
@dataclass
class DelegatedFigure:
    label: str          # e.g. "Property, plant and equipment — net"
    value: str          # verbatim as printed, incl. sign/parens: "(1,234)"
    pdf_page: int       # 1-based page the figure was read from

@dataclass
class DelegatedReadResult:
    note_num: int
    figures: list[DelegatedFigure]     # capped: MAX_DELEGATED_FIGURES = 40
    ambiguity: str = ""                # one line; "" = none
    pages_read: list[int] = field(default_factory=list)
    failed: bool = False               # True → parent must read it itself
    message: str = ""                  # human-readable note / failure reason

    def to_summary(self) -> str:
        """≤ DELEGATE_SUMMARY_CHAR_CAP (≈1500) chars, deterministic format."""
```

- **Hard size cap** on `to_summary()` (`DELEGATE_SUMMARY_CHAR_CAP ≈ 1500`).
  This is the whole point — a verbose sub-agent must not be able to re-bloat the
  parent. Over-cap summaries are truncated with a `[truncated — read note N
  yourself for the rest]` footer (mirrors the notes writer's `CELL_CHAR_LIMIT`
  truncation idiom).
- `figures` capped at `MAX_DELEGATED_FIGURES` to bound the structured payload
  independent of prose length.
- Values are returned **verbatim as printed** (sign, parentheses, thousands
  separators). Sign/scale normalisation stays a parent + verifier
  responsibility — the sub-agent reports what it sees, the parent decides what
  to write (keeps gotcha #17's "agent judges, never auto-plugs" boundary on the
  write side).

### 3.3 The sub-agent (factory)

A new `create_delegate_agent(...)` in `extraction/delegation.py`, structured
like `create_extraction_agent` but **read-only and write-tool-free**:

| Aspect | Face agent (`create_extraction_agent`) | Delegate sub-agent |
|---|---|---|
| Tools | calculator, lookup_definitions, read_template, view_pdf_pages, search_pdf_text, **write_facts, verify_totals, save_result, submit_face_coverage** | calculator, lookup_definitions, view_pdf_pages, search_pdf_text **only** |
| Output type | `str` (free-form, gated by save) | `DelegatedReadResult` (forced structured output) |
| System prompt | full extraction prompt | "read note N, answer the question, return ONLY the structured summary; you cannot write to the workbook" |
| Deps | `ExtractionDeps` | `DelegateDeps` (subset: pdf_path, model, output_dir, page_hints, filing_level/standard — **no** filled_path, run_id/db_path, no write surface) |
| Model | run's extraction model | cheap default via `pricing.resolve_delegate_model` (§3.5) |

The absence of `run_id`/`db_path` on `DelegateDeps` is load-bearing: the sub-agent
*cannot* reach the canonical projection path, so there is no way for it to write
facts even by accident.

### 3.4 The runner (control flow)

A new `run_delegate_read(...)` in `extraction/delegation.py`, modelled directly
on `_run_list_of_notes_sub_agent` (`notes/listofnotes_subcoordinator.py:345`)
but for a single question rather than a batch:

```python
async def run_delegate_read(
    *,
    note_num: int,
    question: str,
    deps: ExtractionDeps,          # parent deps — read pdf_path/hints/model from here
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
    parent_agent_id: str,          # for telemetry rollup attribution
) -> tuple[DelegatedReadResult, int, int]:  # (result, prompt_tokens, completion_tokens)
```

Flow per call:

1. Resolve the delegate model (§3.5) and build `create_delegate_agent`.
2. Build an `AgentLoopSpec` (§3.6) with tight caps and drive it with
   `run_agent_loop` (reusing the existing loop — **not** a hand-rolled iterate).
3. On success, capture `(result, prompt_tokens, completion_tokens)` the same way
   `_invoke_sub_agent_once` returns its 4-tuple
   (`notes/listofnotes_subcoordinator.py:806`); coverage is N/A here so it's a
   3-tuple.
4. On any failure path (transient-exhausted, timeout, wall-clock, structured-
   output failure), return `DelegatedReadResult(failed=True, message=…)` —
   **never raise into the parent**. The tool turns this into the
   "read note N yourself" degraded string.

### 3.5 Model selection

New `pricing.resolve_delegate_model(model)` mirroring `resolve_notes_parallel`
(`pricing.py:230`), reading a `config/models.json` key:

- Returns a **cheap model name** for raw reading (the parent's expensive model
  shouldn't pay to OCR a movement schedule). Default falls back to the run's own
  model if no cheap mapping is configured — i.e. delegation still *works*
  unconfigured, it just doesn't save model cost until the registry is populated
  (same graceful-default posture as `resolve_notes_parallel` returning
  `DEFAULT_NOTES_PARALLEL`).
- Per-request override path is **not** needed in the prototype (the flag + the
  registry default are enough to measure).

### 3.6 Concurrency, retry, timeout, caps

- **Concurrency.** Face agents already run in parallel (one per statement). A
  single face agent issues delegated reads **sequentially within its own turn
  flow** (a tool call blocks that agent until it returns) — so there is no new
  fan-out width to govern in the prototype. If a future version lets one agent
  issue several reads at once, gate the width on `resolve_notes_parallel`
  semantics and share the proxy budget; out of scope now (keep-it-minimum).
- **Retry.** Reuse `notes/_rate_limit.py` wholesale (the same module item 10
  wired into face agents): rate-limit errors honour `compute_backoff_delay` up
  to `RATE_LIMIT_MAX_RETRIES`; one generic retry on connection-class errors;
  generic exceptions fail the *read* (not the parent) immediately. Backoff
  sleeps go inside the try so a user abort during backoff surfaces as
  `CancelledError` (the `pending_backoff` pattern).
- **Timeout / caps** via the `AgentLoopSpec` fields items 6/7/17 added
  (`agent_runner.py:158`):
  - `turn_timeout` = `DELEGATE_TURN_TIMEOUT` (propose **120.0** — reads are
    shorter than the 180s face turn).
  - `max_iters` well below the pydantic-ai 50 cap (gotcha #18) — propose
    `DELEGATE_MAX_ITERS = 12` (a focused read shouldn't need 40 turns).
  - `wallclock_timeout` = `XBRL_DELEGATE_WALLCLOCK_S` resolver, default **180.0**
    (same positive/zero-disables semantics as `XBRL_CORRECTION_WALLCLOCK_S`).
  - `token_budget` optional via `XBRL_MAX_TOKENS_PER_DELEGATE` (default 0 =
    disabled), reusing item 7's machinery.

### 3.7 Telemetry rollup

Sub-agent usage **rolls into the parent's `run_agents` row** — the Sheet-12
"sub-agents merge into one row" precedent (gotcha #6). Concretely:

- `delegate_note_read` accumulates each call's `(prompt_tokens,
  completion_tokens)` into a per-parent accumulator on `ExtractionDeps` (e.g.
  `deps.delegated_prompt_tokens` / `deps.delegated_completion_tokens` /
  `deps.delegated_reads` counter).
- When the coordinator finalises the parent via `finish_run_agent`
  (`db/repository.py:487`), it **adds** the delegated totals into the
  `prompt_tokens` / `completion_tokens` / `tool_call_count` rollups already on
  that signature. No new column, no new `run_agents` row per read (no orphan
  rows — the acceptance test asserts this).
- Per-turn `run_agent_turns` rows are **not** emitted for delegated reads (the
  Sheet-12 fan-out leaves per-turn rows empty too — CLAUDE.md gotcha #6). The
  parent's rollup absorbs the cost so the Telemetry tab still totals correctly.
- A `delegated_reads: int` field on the parent `AgentResult` surfaces the count
  for the Agents tab (additive, read-only, like item 9's `error_type` badge).

### 3.8 Interaction with coverage receipts (item 23)

Open design point, leaning **yes**: a figure the sub-agent *found* but the parent
never *wrote* should still be accountable. Proposal for the prototype: the parent
prompt instructs that any `delegate_note_read` result it acts on must still be
reflected in its `submit_face_coverage` receipt (written | skipped-with-reason)
exactly as if it had read the note itself. No code coupling in the prototype —
the receipt mechanism (`extraction/coverage.py`) is unchanged; this is a prompt
contract, measured by whether delegated runs show *more* unaccounted-ref warnings
than direct runs. If they do, wire the delegate result into the receipt
expectation in a follow-up. (Keeps the prototype's blast radius minimal.)

## 4. Invariants honoured

| Gotcha | How |
|---|---|
| **#6** hybrid storage / rollup | Sub-agent usage merges into the parent `run_agents` row; no per-read DB rows; verbatim content (if ever persisted) stays on disk, never SQLite. |
| **#13** soft hints | Sub-agent navigates freely via hints + `search_pdf_text`; no `allowed_pages`, no page restriction. `note_num` plausibility filter reuses item 4's bound. |
| **#14** retry budget | One generic retry + the rate-limit budget from `notes/_rate_limit`; degrade-don't-fail; a sub-agent failure never fails the parent. |
| **#17** save-side guards | Sub-agent has **no** write tools and **no** `db_path` — `write_facts`, `verify_totals`, `save_result`, the no-plug guard, and DB projection stay parent-only. |
| **#18** iteration caps | Per-sub-agent `max_iters` (12) ≪ 50; turn + wall-clock caps via `AgentLoopSpec`. |
| **#22** atomic saves | N/A by construction — the sub-agent never saves a workbook; only the parent does, through the existing atomic path. |
| **Keep-it-minimum** | Design-first; one statement; flag-gated; no new fan-out width; no new storage; reuses 5 existing subsystems. |

## 5. File-by-file change list (prototype)

| File | Change |
|---|---|
| `extraction/delegation.py` *(new)* | `DelegatedFigure` / `DelegatedReadResult` / `DelegateDeps` dataclasses; `create_delegate_agent`; `run_delegate_read`; module constants (`DELEGATE_TURN_TIMEOUT`, `DELEGATE_MAX_ITERS`, `DELEGATE_SUMMARY_CHAR_CAP`, `MAX_DELEGATED_FIGURES`). |
| `extraction/agent.py` | Add delegation accumulator fields to `ExtractionDeps`; add `delegation_enabled: bool`; conditionally register `delegate_note_read` (pattern: the `submit_face_coverage` conditional at `:924`). |
| `pricing.py` | `resolve_delegate_model(model)` mirroring `resolve_notes_parallel` (`:230`); `config/models.json` gains a `delegate_model` map. |
| `coordinator.py` | Resolve `XBRL_DELEGATE_READS` + caps; thread `delegation_enabled` into `ExtractionDeps`; add the delegated token/read totals into the `finish_run_agent` rollup; surface `delegated_reads` on `AgentResult`. |
| `db/repository.py` | **No schema change.** The existing `finish_run_agent` rollup args (`prompt_tokens`, `completion_tokens`, `tool_call_count` — `:487`) absorb the delegated totals. |
| `config/models.json` | Add `delegate_model` mapping (cheap model per extraction model). |
| Frontend (`RunDetailView` Agents tab) | Optional additive `delegated_reads` badge — defer until after measurement; not required for the prototype gate. |

No DB migration, no new SSE event vocabulary, no new endpoint.

## 6. Pinning tests (written with the prototype)

New `tests/test_delegation_subagents.py`:

1. **Flag gating** — `delegate_note_read` is registered **iff**
   `deps.delegation_enabled` (assert the agent's tool set, mirroring how the
   `submit_face_coverage` conditional is tested).
2. **No write surface** — `create_delegate_agent`'s tool set contains none of
   `write_facts` / `verify_totals` / `save_result` / `submit_face_coverage`;
   `DelegateDeps` has no `db_path` / `run_id` / `filled_path` attribute.
3. **Degrade-don't-fail** — a sub-agent that raises / times out / exhausts
   retries → tool returns the "read note N yourself" string and the parent run
   still completes `succeeded`.
4. **Output is size-capped** — a sub-agent emitting a huge summary →
   `to_summary()` ≤ `DELEGATE_SUMMARY_CHAR_CAP`, truncation footer present;
   `figures` clipped at `MAX_DELEGATED_FIGURES`.
5. **Telemetry rollup, no orphan row** — after a parent run that issued N
   delegated reads, there is exactly **one** `run_agents` row for that
   statement, its `prompt_tokens`/`completion_tokens` include the delegated
   spend, and `delegated_reads == N`.
6. **note_num plausibility** — `delegate_note_read(note_num=743, …)` on a
   30-note filing returns a degraded message without spawning a sub-agent
   (reuses item 4's bound).
7. **Caps below pydantic-ai 50** — `DELEGATE_MAX_ITERS < 50`
   (extends the spirit of `test_max_agent_iterations_below_pydantic_cap.py`).

Existing suites that must stay green: `tests/test_notes_retry_budget.py`
(shared `_rate_limit` untouched), `tests/test_page_hints.py` (no page
restriction introduced), the face-coverage tests (receipt mechanism unchanged).

## 7. Measurement protocol (the go/no-go gate)

The prototype is judged **only** by item 26's harness, on SOFP (the most
note-heavy face statement, where delegation should help most):

```bash
# Baseline (flag off) vs delegated (flag on), same benchmark(s), same model.
XBRL_DELEGATE_READS=0 ./venv/bin/python scripts/eval_regression.py \
    --benchmark-id <SOFP_BENCH> --model <MODEL> --report eval_baseline.md
XBRL_DELEGATE_READS=1 ./venv/bin/python scripts/eval_regression.py \
    --benchmark-id <SOFP_BENCH> --model <MODEL> --report eval_delegated.md
```

Two numbers per arm, both already produced by `eval_regression.py`:

- **Token spend** (prompt + completion, summed across the parent + its delegated
  reads — the rollup makes this a single per-run figure).
- **Headline accuracy** (`matched / gold_cells` from `grade_run`).

**Acceptance to widen:** delegated arm shows a **measured token reduction** at a
**non-inferior** eval score (within the harness `--tolerance`, default 0.01). A
token win that *costs* accuracy is a fail. A neutral token result is a fail
(complexity not earning its keep). Run on ≥2 benchmark documents before
trusting the direction.

## 8. Phasing

1. **Design (this doc).** ← you are here.
2. **Prototype** — `extraction/delegation.py` + the SOFP-only wiring behind
   `XBRL_DELEGATE_READS` (default off). Land with `tests/test_delegation_subagents.py`
   in the same commit (house rule). Cheap read-only sub-agent.
3. **Measure** — §7 protocol on ≥2 SOFP benchmarks.
4. **Decide — widen.** Only if (3) holds, extend the conditional registration to
   SOCIE / SOCF / SOPL (still flag-gated), re-measure per statement (the open
   question in §10 — note-light statements may show no win).
5. **Decide — default-on.** A *separate* decision after a clean measured win
   across the widened set. Flipping `XBRL_DELEGATE_READS` default to on, and/or
   populating `config/models.json delegate_model` with a cheaper model, are the
   two independent levers.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Sub-agent summary loses a figure the parent needed → silent under-extraction | Parent prompt frames the tool as advisory; parent may re-read. §7 accuracy gate catches net regressions. `failed=True` always routes back to direct reading. |
| Two reads of the same note (sub then parent) → *more* tokens, not fewer | The measurement gate fails the feature if net tokens don't drop. Prompt discourages redundant re-reads. |
| Cheap delegate model misreads scanned pages | Sub-agent uses the same `view_pdf_pages` image path as the parent; if the cheap model underperforms on scans, `resolve_delegate_model` can map scanned-heavy runs back to the parent model (future; not in prototype). |
| Coverage receipts under-count delegated figures | §3.8 prompt contract in the prototype; measured via unaccounted-ref warning delta; wired into the receipt expectation only if needed. |
| Structured-output failure from a weak model | Treated as a transient → one retry → then `failed=True` degrade. |

## 10. Open questions

- Does delegation help **SOPL/SOCF** (note-light) at all, or only **SOFP/SOCIE**?
  Measure per statement before widening — do not assume uniform benefit.
- Should the delegate summary feed **coverage receipts** (item 23) so a
  delegated-but-unwritten figure is still accounted for? Leaning yes; prototype
  via prompt contract first, code-couple only if the warning delta says so.
- Is a **per-read trace** worth persisting for debugging (gotcha #6 hybrid
  storage), or is the parent trace + rollup counter enough? Default: no per-read
  trace in the prototype; revisit if delegated reads become a debugging blind
  spot.
- Does a **cheaper model** actually hold accuracy on movement schedules, or does
  the cost saving evaporate into re-reads? This is the core empirical question
  the §7 gate answers.

## 11. Reuse map

| Reused subsystem | For |
|---|---|
| `notes/listofnotes_subcoordinator.py` | Structural template: runner shape, retry/backoff, usage capture, rollup. |
| `notes/_rate_limit.py` | Transient classification + backoff + budget constants (same module item 10 uses for face agents). |
| `agent_runner.py` `AgentLoopSpec` / `run_agent_loop` | Per-sub-agent turn/wall-clock/token caps + the loop itself (no hand-rolled iterate). |
| `pricing.resolve_notes_parallel` (`pricing.py:230`) | Template for `resolve_delegate_model`. |
| `tools/pdf_search.py` `search_pdf_text` (item 19) | Sub-agent navigation without page restriction. |
| `extraction/coverage.py` (item 23) | Receipt accounting the parent still owns. |
| `db/repository.py` `finish_run_agent` rollup args | Telemetry merge — no schema change. |
| `scripts/eval_regression.py` (item 26) | The go/no-go measurement gate. |
