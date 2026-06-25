# Runbook: Skill-First Harness — Windows accept-or-revert gate

**Branch:** `feat/skill-first-workflow-references`
**What this gates:** whether the canonicalized workflow-reference shelf + the
`load_workflow_reference` loader/activation gate (Phase 0.5 + Phase 1 of
[docs/PROPOSAL-skill-first-harness.md](PROPOSAL-skill-first-harness.md)) actually
improve extraction accuracy — measured on the real gold, which lives only on the
Windows setup.

The build is done and is guarded by environment-independent static tests (below).
This runbook is the **operator's accept-or-revert decision**: run Phase 0
(baseline) and Phase 2 (treatment) against the gold, compare, then keep or revert.

Per the proposal's operating model: *build here, measure on Windows, keep or
revert there.* Phase 0.5 (doc canonicalization) is committed **separately** from
Phase 1 (the loader wiring), so a single regressing reference — or the whole
loader — can be reverted without unwinding the corrected docs.

---

## 0. Prereqs (once)

```bat
git fetch && git checkout feat/skill-first-workflow-references
:: install/refresh deps as usual for this machine
```

### 0a. Static verification (no gold needed — must pass before measuring)

These answer "did we break the contract?" deterministically:

```bat
python -m pytest tests/test_workflow_reference_loader.py ^
                 tests/test_workflow_reference_canonicalization.py -v
```

Expect all green (65 cases): loader resolves per (statement, variant) from deps
with no model path, unknown combos return "not available", the system prompt
never embeds the reference body, the activation gate refuses a pre-load
SOCIE/SOCF write, the dedup processor bills a reloaded reference once, and every
reference agrees with its live prompt on dividend sign + addressing mode.

---

## 1. Phase 0 — baseline (activation gate OFF)

1. **Fix the scenario set.** Pick a NAMED set spanning MFRS/MPERS × Company/Group,
   including at least one **SOCIE-heavy** and one **SOCF-indirect** case (the two
   statements the gate targets). Write the list down — every later step uses these
   exact scenarios, not ad-hoc PDFs.

2. **Seed gold from COMPLETED runs, not workbook uploads.** Use
   `POST /api/benchmarks/from-run` (the "Seed benchmark from this run" action on a
   finished run), then hand-correct in the gold editor. An un-recalculated workbook
   upload silently drops most SOCIE / cross-sheet formula leaves (CLAUDE.md gotcha
   #23, the 2026-06-05 incident). **Record the benchmark IDs.**

3. **Disarm the gate** so the baseline reflects today's behaviour. In the repo
   `.env` (or the process env):

   ```env
   XBRL_WORKFLOW_REFERENCE_GATE=0
   ```

4. **Run extraction** on each scenario with its benchmark attached (extract-page
   benchmark picker). Do **N ≥ 3 repeated runs per scenario** so run-to-run LLM
   variance doesn't masquerade as a treatment effect. **Record the baseline run
   IDs** (History list).

   Grading fires automatically at run completion when a benchmark is attached
   (gotcha #23); the score shows in the History score column + the Eval tab.

---

## 2. Phase 1 — confirm activation actually happens (quick sanity, gate ON)

Set `XBRL_WORKFLOW_REFERENCE_GATE=1` (or just unset it — ON is the default) and
run ONE SOCIE and ONE SOCF scenario. Open each agent's conversation trace
(run page → Agents → trace) and confirm:

- the agent **called `load_workflow_reference`** (you'll see the wrapped
  `=== WORKFLOW REFERENCE: … ===` body once), and
- if it tried to `write_facts` first, it got the **refuse-once** message and
  recovered by loading the reference.

If the reference never loads, the treatment run is just a baseline run — stop and
check the prompt wiring before spending Phase 2 runs.

---

## 3. Phase 2 — treatment (activation gate ON) + the delta

1. With the gate **ON**, re-run the **same** scenario set (same benchmarks), again
   **N ≥ 3 repeats** each. **Record the treatment run IDs.**

2. Compute the delta (averages over the repeats, so variance doesn't masquerade
   as a treatment effect):

   ```bat
   python -m scripts.compare_eval_runs --db output\app.db ^
       --baseline 161,162,163 --treatment 167,168,169
   ```

   It prints per-group average score (matched / gold), the missing / mismatch /
   extra / scale-mismatch counts, and total tokens + tool calls, with the
   treatment − baseline delta and an IMPROVED-OR-HELD / REGRESSED verdict.

3. **Per-statement / per-cell signal.** The eval scorecard is per-(run, benchmark)
   aggregate. For the per-statement breakdown the gate needs (a SOCIE matrix-cell
   regression can hide inside an aggregate hold), either attach a single-statement
   benchmark per scenario, or compare the Concepts/Values diff in the run page for
   SOCIE and SOCF specifically.

---

## 4. The accept-or-revert decision

**Keep a reference** only if, for its statement:
- accuracy **improves-or-holds**, AND
- the simpler statements show **no per-cell regression**, AND
- **no material token regression**.

An aggregate "accuracy held" is NOT sufficient — confirm SOCIE and SOCF
per-statement.

- **Accept the branch:** merge `feat/skill-first-workflow-references` into `main`.
- **Drop ONE regressing reference (per-file reversibility):** delete that file
  under `prompts/references/` and remove its key from `_REFERENCE_FILES` in
  `extraction/workflow_reference.py` (the loader then returns "not available" for
  that combo and the gate no-ops for it). Re-run its scenarios to confirm the
  regression clears.
- **Revert the loader entirely but keep the corrected docs:** `git revert` the
  Phase 1 commit (`e3de75f`); the Phase 0.5 commit (`2308310`) leaves the
  canonicalized references + their guard intact (they fix genuinely-stale
  developer docs regardless of the experiment's outcome).
- **Abandon the experiment:** stay on `main`; the branch is never merged.

---

## Notes

- The gate is **default ON in production**; the test suite defaults it OFF
  (`tests/conftest.py`), so always set `XBRL_WORKFLOW_REFERENCE_GATE` explicitly
  for the baseline (0) vs treatment (1) runs — don't rely on the default.
- `scripts/compare_eval_runs.py` is **read-only**; it touches no run and writes
  nothing. It warns (never silently drops) when a listed run has no eval score.
- The references' row numbers/coordinates are an MFRS-Company / FINCO-FY2021
  illustration; the live `read_template()` is authoritative — this is stated in
  every reference's preamble, so a Group/MPERS run reading the shelf still confirms
  its own coordinates.
