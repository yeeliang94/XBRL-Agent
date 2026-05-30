# Handoff — First-Principles Rewrite (next session)

**Read this first, then `docs/PLAN-first-principles-rewrite.md` (the source of
truth).** This is the "how to pick up" note; the plan has the full task list +
per-step design + resume notes.

## Where the work lives (READ THIS — easy to get wrong)

The rewrite is on branch **`rewrite/first-principles`**, checked out in a **git
worktree**, NOT the main checkout:

- Worktree (do your work here): `/Users/user/Desktop/xbrl-agent/.claude/worktrees/interesting-neumann-674269/`
- Main checkout (`/Users/user/Desktop/xbrl-agent`) sits on branch
  `claude/interesting-neumann-674269` at the **pre-rewrite baseline** — leave it alone.
- Because the branch is already checked out in the worktree, you **cannot**
  `git checkout rewrite/first-principles` in the main dir. `cd` into the worktree.

`main` is untouched and must stay that way until a proven merge. Baseline tag:
`pre-rewrite-baseline`.

## Environment / how to run tests

- **Interpreter:** `/Users/user/Desktop/xbrl-agent/venv/bin/python` (Python
  3.12, pydantic_ai 1.77, pytest 9). System `python3` is 3.9 and missing deps —
  do not use it.
- Backend: `cd <worktree> && /Users/user/Desktop/xbrl-agent/venv/bin/python -m pytest tests/ -q`
- Frontend: `cd <worktree>/web && npx vitest run`
- **Green baseline = `2 failed, 1827 passed, 9 skipped`.** The 2 failures are
  pre-existing and NOT regressions: `test_docs_invariants.py::test_notes_pipeline_doc_mentions_html_contract`
  and `::test_adr_001_records_db_canonical_decision` (commit 80e20c7 archived
  the docs those invariants point at, before this branch existed). Treat "2
  failed" as green; anything beyond that is yours.
- Frontend baseline = `630 passed`.

## Current state (branch GREEN at `c931a90`)

Recent commits (most recent first):

```
c931a90 docs(rewrite): scope Phase 6.2 (error taxonomy + typed SSE) for handoff
0044cad feat(rewrite): precompute concept_targets, single exporter lookup (Phase 6.1)
e7881db docs(rewrite): log Phase 6.1 attempt + resume notes (reverted)
de06bc2 feat(rewrite): durable re-review task table (Phase 5.3)
77a63be feat(rewrite): scout source-honesty flags (Phase 6.3)
```

Plan progress: **~80%**. Phases 0,1,2,3,5.3,6.1,6.3 COMPLETE; 4.1 PARTIAL.

## What remains (recommended order)

Do ONE phase at a time, verify back to the green baseline above, update the
plan doc's status + progress % in the same commit, commit per phase.

1. **6.2 — error taxonomy + typed SSE** (medium-high; backend + frontend).
   **FULLY SCOPED** in the plan under "Step 6.2 → RESUME NOTE (scoped
   2026-05-30)". Single `event_queue` choke point; 6 current SSE shapes; a
   3-bucket Advisory/Recoverable/Fatal rule already latent in ~11 emit sites
   and the frontend's `isRunning` logic; ~168 try/except but only ~11 need a
   `bucket` (the rest commemorate gotchas #5/#10/#19/#20/#22 — do NOT
   bulk-remove). Recommended **additive** slice (keeps all 4 backend + 3
   frontend pin tests green). It touches the frontend — do it with a healthy
   tool channel.

2. **5.1 — split `server.py` routes into `api/`** (LARGE; ~6k lines, ~30
   routes). High-churn; give it its own fresh-context session. Watch import
   cycles + shared module-level state (`PHASE_MAP`, `AUDIT_DB_PATH`, the
   `_run_*`/`_export_*` helpers, `run_review_tasks` access). Pin:
   `test_server_run_lifecycle.py` (gotcha #10) + all route tests.

3. **5.2 — explicit phase pipeline** (LARGE; deep `run_multi_agent_stream`
   rewrite). `PHASES = [Validate, Extract, Cascade, Check, Review, Render]`.
   Preserve terminal-status guarantee, `mark_run_merged`-before-status,
   draft-row, Stop-All partial-merge (gotcha #10) as pipeline properties. Fold
   in deferred PR-1: the CLI (`run.py`) bypasses the canonical pipeline — share
   the server phase pipeline so a CLI run also projects facts + exports +
   reviews. Pin: `test_server_run_lifecycle.py`, `test_pipeline_stage_events.py`,
   `test_stop_all_preserves_partial.py`. Best done AFTER 5.1.

4. **4.1 render-last + 4.2 live A/B** — **BLOCKED, needs an API key.** Don't
   attempt without live-LLM access. See the plan's BLOCKERS / Phase 4 notes:
   the keystone ("render the download only from facts") is quality-affecting
   and its acceptance gate is a live A/B on real PDFs. Concrete known
   regression: `exporter.export_run_to_xlsx` fills a fresh template copy and
   row-1 reporting-period date cells are non-concept writes that don't project
   to facts — a literal "render only from facts" would regress the download's
   dates. Land render-last together with an exporter fix (carry forward
   non-concept cells, or project the period dates as facts), gated on zero
   face-statement regressions, with the Phase 0.3 A/B harness built first
   (self-diff must report zero on the baseline before judging anything).

## House rules (from the original handoff — still apply)

- **"Done" = the pinning test passes, cited — not "looks right."** Almost every
  invariant in CLAUDE.md names a `tests/…` file that guards it.
- Stay surgical; don't "improve" adjacent template formulas / prompts / inline
  styles (gotchas #3, #7). Don't soften the abstract-row guard / no-residual-plug
  prompts (#17). Don't convert frontend inline styles back to Tailwind (#7).
- Commit per phase with the Co-Authored-By trailer; update the plan doc's status
  + progress % in the same commit.
- If a peer review arrives, verify each finding against the actual code before
  accepting — last sessions, real findings were mixed with false ones.

## Tool-channel caveat (this session)

This session's tool-output channel intermittently batched/delayed large parallel
tool blocks (outputs arrived all at once on a later turn, not lost). It made an
18-test / 9-file fixture sweep hard to verify in one pass. Mitigations that
worked: keep tool batches small and serial, write results to a temp file and
`Read` it, prefer single-line `echo "TAG=$(...)"` tokens over multi-line dumps.
If the channel is healthy in your session, ignore this.
