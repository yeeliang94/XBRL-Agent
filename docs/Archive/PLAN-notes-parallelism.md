# Implementation Plan: Model-Aware Sheet-12 Parallelism

**Overall Progress:** `83%` — **CODE COMPLETE, NOT YET VALIDATED IN PROD.**
Steps 1-5 🟩 Done and covered by unit tests. Step 6 (live runs on FINCO
with `gpt-5.4-mini` and `gpt-5.4`) is a ship blocker — the whole feature's
success criterion is real TPM/429 behaviour, which no unit test can model.
Do NOT promote to production until Step 6 evidence is attached.
**Last Updated:** 2026-04-21

## Post-Implementation Peer Review (2026-04-21)

Two in-scope findings fixed after initial implementation:

- **[HIGH] `_normalize` missed OpenAI / Anthropic prefixes.** Direct-mode
  runtime path constructs models with bare names (`gpt-5.4-mini`,
  `claude-haiku-4-5`); the resolver fell through to default=5 and the
  feature was a no-op on that path. Fixed in `pricing._normalize` —
  now strips the full set (`bedrock.anthropic.`, `bedrock.`, `openai.`,
  `vertex_ai.`, `google-gla:`, `google-vertex:`). Kept in sync with
  `server._PROVIDER_PREFIXES`. Regression tests pin bare-name
  resolution for all buckets (`test_bare_openai_name_*`,
  `test_bare_anthropic_name_*`, 5 new tests).
- **[MEDIUM] No bounds / type validation on `notes_parallel`.** A
  config `0` would raise `ZeroDivisionError` in
  `split_inventory_contiguous`; negatives silently skipped the sheet;
  `bool` sneaked past `isinstance(x, int)`. Fixed in
  `_load_notes_parallel` — bools rejected explicitly, range enforced
  `[1, 10]`, `KeyError` added to the exception handler, malformed
  entries skipped individually rather than poisoning the whole load.
  7 new regression tests covering each rejection path plus dedup
  behaviour.

Out-of-scope peer-review findings (I-1 rate-limit cap, I-2 `pp.` regex,
I-3 symlink cache key, I-4 ANSI sanitisation) are logged as separate
follow-ups and do not block this PR.

## Summary

Sheet 12 (`List of Notes`) fans out 5 parallel sub-agents by default. Cheap/fast
models finish each request quickly and burn through the provider's TPM bucket
faster than slow ones — a 5-way fan-out on `gpt-5.4-mini` reliably hits HTTP 429.
This plan adds a per-model `notes_parallel` field in `config/models.json` so
cheap/fast models drop to 2-way fan-out while heavy/slow models keep 5.

The existing 429 retry infrastructure (`notes/_rate_limit.py`,
`RATE_LIMIT_MAX_RETRIES=3`, launch stagger) stays in place — this change
reduces how often that safety net is triggered, it does not replace it.

## Key Decisions

- **Per-model override in `config/models.json`**: single source of truth
  alongside pricing. Operator tunes one file; no code change to retune.
- **Default parallelism = 5**: fail-open for unknown / newly-added models —
  the retry path still catches TPM overruns.
- **Only Sheet 12 is affected**: it's the only fan-out. Other notes sheets run
  1 agent each; the 5-template-level stagger (`NOTES_LAUNCH_STAGGER_SECS = 0.8`)
  stays as-is.
- **Resolver lives next to pricing** (`pricing.py`-adjacent, not a new module):
  both read `config/models.json` and need the same prefix-normalisation. One
  loader, one cache.
- **Stagger untouched**: `_SUB_AGENT_LAUNCH_STAGGER_SECS = 0.6` is right for
  5-way; with 2-way there's nothing to stagger meaningfully. Not worth a knob.
- **Cheap/fast bucket (parallel=2)**: `gemini-3-flash-preview`,
  `gemini-3.1-flash-lite-preview`, `claude-haiku-4-5`, `gpt-5.4-mini`,
  `gpt-5.4-nano`.
- **Heavy/slow bucket (parallel=5)**: `gemini-3.1-pro-preview`,
  `claude-sonnet-4-6`, `claude-opus-4-6`, `gpt-5.4`.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm cheap-bucket parallel value (2 vs 3) — plan locks in 2
- [ ] 🟥 No conflicting in-progress work on `notes/coordinator.py` or
      `notes/listofnotes_subcoordinator.py`

## Tasks

### Phase 1: Config + Resolver

- [x] 🟩 **Step 1: Add `notes_parallel` field to `config/models.json`** —
      set concrete values for all 9 registered models.
  - [x] 🟩 Heavy bucket → `"notes_parallel": 5` (4 entries)
  - [x] 🟩 Cheap bucket → `"notes_parallel": 2` (5 entries)
  - **Verify:** `python3 -c "..."` confirmed heavy=4, cheap=5, mapping
      matches the bucket spec exactly.

- [x] 🟩 **Step 2: Add resolver `resolve_notes_parallel(model) -> int`** —
      added in `pricing.py` (file now ~175 LOC, still under the ~200 line
      we said would trigger the model_policy.py split).
  - [x] 🟩 Accepts plain strings and PydanticAI Model instances via the
        existing `_resolve_model_name` helper.
  - [x] 🟩 Normalises `vertex_ai.` / `google-gla:` / `google-vertex:`
        via the shared `_normalize` helper.
  - [x] 🟩 Returns `DEFAULT_NOTES_PARALLEL = 5` for unknown / missing.
        File-load failure logs once via `_parallel_load_failed`; per-call
        unknown lookups stay silent (the 429 retry path is the backstop).
  - **Verify:** `tests/test_notes_parallel_resolver.py` — 8 tests covering
    cheap id, heavy id, proxy prefix normalisation, PydanticAI instance
    shape, unknown fallback, cache reuse, and full cheap+heavy bucket
    guards. All pass (`pytest tests/test_notes_parallel_resolver.py -v`).

### Phase 2: Wire Through Coordinator

- [x] 🟩 **Step 3: Thread `parallel` into Sheet-12 fanout** — resolver
      called after the empty-inventory guard in `_run_list_of_notes_fanout`
      so we don't log a parallelism line on a sheet we're about to fail.
  - [x] 🟩 `resolve_notes_parallel(model)` called once per fan-out.
  - [x] 🟩 Passed as `parallel=resolved` into `run_listofnotes_subcoordinator`.
  - [x] 🟩 INFO log line: `"Notes-12 fan-out: N-way (model=...)"`.
  - **Verify:** `tests/test_notes12_parallel_wiring.py` — 3 tests (cheap
      → 2, heavy → 5, unknown → `DEFAULT_NOTES_PARALLEL`) all pass.

- [x] 🟩 **Step 4: End-to-end smoke** —
      `pytest tests/test_notes12_e2e.py tests/test_notes12_subcoordinator.py -v`
      → **34/34 green**. Broader sanity (`test_notes_coordinator` +
      `test_notes_retry_budget` + resolver + wiring) → **46/46 green**.
      No new warnings.

### Phase 3: Docs + Guardrails

- [x] 🟩 **Step 5: Update `CLAUDE.md`** — concept paragraph added under
      the Sheet-12 block (lines ~378-386); "Notes-12 parallelism
      (model-aware)" row added to the Files-That-Must-Stay-in-Sync table.
  - **Verify:** `grep -n "notes_parallel" CLAUDE.md` → 2 hits (one in
      concept block, one in sync table). Confirmed.

- [ ] 🟥 **Step 6: Sanity live run on FINCO** — one manual run with
      `gpt-5.4-mini` and Sheet 12 enabled, confirm no 429-driven retries
      in the logs (or at most one transient retry, not the previous
      sustained storm).
  - [ ] 🟥 Capture log output showing the resolved `parallel=2` line.
  - [ ] 🟥 Repeat with `gpt-5.4` (heavy) and confirm `parallel=5` line.
  - **Verify:** grep run output for `"Notes-12 fan-out: "` prints the
      expected values; grep for `"hit 429"` returns zero hits on the
      mini run (vs. the 3+ hits the user observed previously).

## Rollback Plan

If something goes badly wrong:

- **Config-only revert**: delete the `notes_parallel` fields from
  `config/models.json`. The resolver defaults to 5 and the system behaves
  exactly as it does today. No code rollback needed.
- **Full revert**: `git revert <commit-sha>` of the PR — single commit,
  touches 3-4 files (`config/models.json`, `pricing.py` or
  `model_policy.py`, `notes/coordinator.py`, one test file, `CLAUDE.md`).
- **State check**: no schema or DB impact. In-flight runs are unaffected —
  the override only applies at the start of Sheet-12 fan-out.
