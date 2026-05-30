# Session Report — 2026-05-30 (Phases 5.3, 6.1; 6.2 scoped)

**Branch:** `rewrite/first-principles` (worktree
`.claude/worktrees/interesting-neumann-674269/`)
**Start state:** `80e20c7` baseline in main checkout; rewrite branch at `77a63be`
(Phase 6.3 done), green at `2 failed / 1817 passed`.
**End state:** `c931a90`, green at `2 failed / 1827 passed / 9 skipped` backend,
`630 passed` frontend. Tree clean. Plan progress ~72% → ~80%.

## Goal

User asked to work through the three self-contained remaining phases in order:
**5.3 → 6.1 → 6.2**. 5.3 and 6.1 shipped; 6.2 scoped + handed off per the user's
choice at the end.

---

## Phase 5.3 — durable re-review task table  ✅ shipped (`de06bc2`)

**Problem:** manual re-review state lived in an in-process `_REVIEW_TASKS:
dict[int, dict]` in `server.py`. A re-review runs on a daemon thread for
minutes; a process restart lost both in-flight and finished passes — a poll
would then report `idle` and hang, and the outcome was gone.

**Change:**
- **`db/schema.py` (v12 → v13):** new `run_review_tasks` table. `run_id` is the
  PRIMARY KEY (one latest pass per run; a relaunch overwrites the slot,
  mirroring the dict's `[run_id] = state`). Columns: `status` (`running|done`),
  `model_name`, `outcome_json` (NULL while running), `started_at`, `updated_at`.
  Pure `CREATE TABLE IF NOT EXISTS` walk-forward (same shape as v10→v11,
  v11→v12); FK cascades on run delete. Absence of a row == `idle`.
- **`db/repository.py`:** `upsert_review_task` (preserves `started_at` across
  running→done via an existence check + `ON CONFLICT` upsert), `fetch_review_task`
  (decodes `outcome_json`; returns None → caller maps to `idle`),
  `reconcile_stale_review_tasks` (flips every `running` row to a terminal `done`
  carrying "Server restarted while the re-review was running.").
- **`server.py`:** the re-entrancy guard and `GET /re-review/status` now read
  via `fetch_review_task`; the POST launcher and the background `_thread_main`
  write via a new best-effort `_save_review_task` (wraps DB writes in
  try/except so a DB hiccup never crashes the daemon thread — worst case a
  stale `running` row that startup later reconciles). `_lifespan` calls
  `reconcile_stale_review_tasks` after `init_db`, before the canonical
  bootstrap, so a crashed pass resolves and a relaunch isn't blocked.
- **Status JSON shape (`idle`/`running`/`done` + outcome) is byte-identical**,
  so **zero frontend change** (630 vitest pass unchanged).

**Tests:** new `tests/test_db_schema_v13.py` (8 cases: fresh init, round-trip
running→done with started_at preserved, relaunch-overwrite, stale reconcile,
cascade, v12→v13 upgrade, idempotent). 2 new `tests/test_reviewer_routes.py`
cases: `test_re_review_outcome_survives_simulated_restart` (a finished outcome
is durable on a fresh DB connection — what a restarted process opens) and
`test_stale_running_task_reconciled_at_startup` (drives the real `_lifespan`
async-context-manager and confirms the reconcile wiring). All existing
reviewer-route tests pass against the DB-backed impl.

**Docs:** CLAUDE.md gotcha #11 (v12→v13 step) + gotcha #21 (durable-across-restarts
sub-note); plan status + progress %.

---

## Phase 6.1 — precompute concept_targets, single exporter lookup  ✅ shipped (`0044cad`)

**Problem (report §5.1):** `exporter.export_run_to_xlsx` routed each fact to a
cell via a three-way branch — matrix (SOCIE) via `concept_targets`, linear Group
via `concept_targets`, linear Company via a `render_col` + PY=C fallback. The
plan's goal: precompute routing targets at import so the exporter does ONE keyed
lookup with no fallback. Hard constraint: do **not** fold render-aliases into
targets — cross-sheet formula cells must stay live (schema v11, gotcha #21).

**Change:**
- **`concept_model/importer.py`:** new `import_company_targets(db, template_id)`
  mirroring `import_group_targets`: per non-ABSTRACT, non-SOCIE concept, write
  `(Company,CY) → render_col` and `(Company,PY) → C`. Iterates `concept_nodes`
  only (primary coords), so a cross-sheet rolled-up concept's **face alias coord
  gets no target** — its formula stays live.
- **`concept_model/bootstrap.py`:** `_import_one` now fills targets for every
  linear template — `import_company_targets` for non-group, `import_group_targets`
  for group; matrix keeps inline targets.
- **`concept_model/exporter.py`:** routing collapsed to a single `concept_targets`
  lookup. New `_APPLICABLE_SCOPES = {"company": ("Company",), "group":
  ("Group","Company")}` drops out-of-scope facts (e.g. a Group-scope fact on a
  Company filing); an in-scope (or matrix) fact with no precomputed target
  appends to `unmapped` → raises (importer-bug signal). Removed the `render_col`
  fallback branch and the now-dead `is_group` local. The CY=B/PY=C result and
  Group-drop behaviour are unchanged — only the mechanism moved from an inline
  fallback to a precomputed table.

**The fixture sweep (the real work + the trap):** making a precomputed target
MANDATORY for every Company fact red-lit 18 tests across ~9 hand-rolled Company
fixtures that called `import_template` but not the new company-targets helper.
Each got an `import_company_targets(db, tid)` after its `import_template(...)`
(mirroring how Group fixtures already call `import_group_targets`).
`test_phase6_mpers.py` is **mixed** (company + group) — the company call went
ONLY on its company path, beside the existing `import_group_targets` on the
group path. `test_phase4_group::test_group_export_raises_on_unmapped_target`
still asserts the raise (the new "in-scope fact with no target → raise" path
preserves it).

**Process note (worth knowing):** 6.1 was implemented → reverted → re-applied.
First attempt got reverted to keep the branch green (committed as `e7881db` with
a detailed resume note) when the fixture-sweep scope looked risky under the
flaky tool channel. The user chose "finish it now", so the sweep was completed
and verified. A folding-targets-into-`import_template` alternative was considered
and rejected (import_template can't see filing level; auto-writing Company B/C
for a Group template would clobber Group's D/E) — documented so it isn't
re-discovered.

**Tests:** the 4 pin tests stay green — `test_canonical_cross_sheet_rollup.py`
(incl. `test_exporter_preserves_cross_sheet_formula_on_alias_coord`),
`test_canonical_export.py` PY-column tests, `test_phase4_group.py` (21),
`test_phase6_mpers.py` (27). Full backend 1827 passed; frontend 630 passed;
backend-only change.

**Docs:** CLAUDE.md gotcha #21 "Single-lookup routing (2026-05-30)" note; plan
6.1 → DONE + progress %.

---

## Phase 6.2 — error taxonomy + typed SSE  🟦 scoped, handed off (`c931a90`)

Not started (user chose to hand off — it touches the frontend and is best done
with a healthy tool channel). A read-only exploration produced a full scope,
written into the plan under "Step 6.2 → RESUME NOTE (scoped 2026-05-30)":

- Single `event_queue` choke point; the 6 current SSE shapes (`pipeline_stage`,
  `cross_check_{start,result,complete}`, `partial_merge`, typed `error` with a
  `data.type` discriminator) + their exact fields.
- The 3-bucket Advisory/Recoverable/Fatal rule, already latent in the ~11 emit
  sites and the frontend's `data.type`-presence `isRunning` logic.
- ~168 try/except in the run path, but only ~11 emit sites need a `bucket`; the
  rest are gotcha-commemorating guards (#5/#10/#19/#20/#22) to leave alone.
- Recommended additive slice (keeps all 4 backend + 3 frontend pin tests green);
  the big-bang error-schema rewrite is the wrong move (breaks every error test
  in one commit).
- The frontend files to touch (`sse.ts`, `types.ts`, `appReducer.ts`,
  `PipelineStages.tsx`, `ValidatorTab.tsx`) and the highest-risk part
  (Recoverable↔Fatal line + `isRunning` interaction — add one reducer test per
  bucket).

---

## Commits this session

```
c931a90 docs(rewrite): scope Phase 6.2 (error taxonomy + typed SSE) for handoff
0044cad feat(rewrite): precompute concept_targets, single exporter lookup (Phase 6.1)
e7881db docs(rewrite): log Phase 6.1 attempt + resume notes (reverted)
de06bc2 feat(rewrite): durable re-review task table (Phase 5.3)
```

## Verification at end of session

- Backend: `1827 passed, 2 failed (pre-existing doc invariants), 9 skipped`.
- Frontend: `630 passed`.
- Working tree clean; `main` untouched.

## Memory written

Added `rewrite-worktree-location` memory (rewrite lives in a worktree, not the
main checkout; use `venv/bin/python`; green baseline = 2 failed / ~1827 passed)
— this tripped me up at session start.

## Remaining after this session

6.2 (scoped above), 5.1 (server route split — LARGE), 5.2 (phase pipeline —
LARGE), 4.1 render-last + 4.2 A/B (BLOCKED on a live-LLM API key). See
`docs/HANDOFF-next-session.md` for the recommended order + per-phase guidance.
