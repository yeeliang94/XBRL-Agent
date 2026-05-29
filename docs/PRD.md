# Reviewer Agent — PRD

> Status: Draft for review · Date: 2026-05-29 · Owner: William Chen
> Supersedes the autonomous canonical correction pass (`_run_canonical_correction_pass`)
> for the default canonical pipeline. The legacy direct-xlsx correction path is unchanged.

## Overview

- **Problem:** When the pipeline finishes, the cross-checks usually surface a few
  inconsistencies (e.g. the balance sheet doesn't balance). Today's correction agent
  reacts to the *symptom* and **writes fixes straight into the authoritative data as it
  reasons**. Two things go wrong:
  1. It often can't find the *real* cause, because the error frequently originates
     deeper than the failing cell — a sub-statement (sub-sheet) total was filled wrongly
     from a misread note in the PDF, and that wrong total rolls up into the face
     statement. The agent patches the visible face number instead of the source.
  2. Because it writes as it thinks, a wrong guess silently corrupts the workbook the
     user downloads. This is the "it could break the whole process" fear.

- **Solution:** A reviewer agent that **investigates the full face↔sub-sheet↔PDF chain to
  find the root cause and applies its grounded fixes into a separate, fully-reversible
  "reviewer version"** of the run — leaving the original untouched. The user reviews that
  version as a visual diff and can **revert to the original in one click**. The agent
  *flags* (and explains) only the cases it's genuinely stuck on or where it believes a
  prior agent erred.

- **The key safety idea — versioning, not gating.** The reviewer's entire output is an
  isolated version layered on top of the original extraction. Nothing the reviewer does is
  destructive, because the original is always one button away. That reversibility — not a
  tight "don't let the agent write" gate — is what makes it safe to let the reviewer apply
  its grounded fixes freely.

- **Target User:** The accountant/preparer reviewing an extraction run before filing. They
  want trustworthy numbers, a clear "here's what the reviewer changed and why" diff, a way
  to throw it all away if they don't trust it, and a short list of "here's what I was stuck
  on / here's where I think the first pass got it wrong."

- **Success Criteria:**
  1. **Always reversible, never destructive.** The original extraction is preserved
     verbatim; "Revert to original" restores it exactly. (Measured: after revert, every
     fact equals its pre-reviewer value; regression test on the mocked pipeline.)
  2. **Grounded fixes, never plugs.** Every change the reviewer applies is grounded (a
     cited PDF page or pure arithmetic of disclosed rows) and never a balancing plug into a
     catch-all/abstract row. (Measured: 100% of applied changes carry grounding; 0
     residual-plug writes.)
  3. **Roots, not symptoms.** On runs where the cause is a sub-sheet error, the reviewer
     identifies and fixes the sub-sheet source (not just the face number) in the majority
     of cases. (Measured against a small labelled set of known sub-sheet-origin failures.)
  4. **A run is never left worse than it started.** Failing cross-checks after review are
     ≤ before, and the run always lands in a terminal status with a downloadable workbook.

---

## User Stories

1. **(MUST HAVE)** As a preparer, when a run finishes with cross-check errors, I want the
   reviewer to investigate the root cause and apply its grounded fixes into a separate
   reviewer version, so that the obvious mistakes get fixed without me chasing them.

2. **(MUST HAVE)** As a preparer, I want to see the reviewer version as a visual diff
   against the original (each change: old → new, why, and the source it's based on), so
   that I can judge the reviewer's work at a glance.

3. **(MUST HAVE)** As a preparer, I want one button to **revert to the original** and throw
   away all the reviewer's changes at once, so that I'm never stuck with a version I don't
   trust.

4. **(MUST HAVE)** As a preparer, I want the reviewer to **flag and explain** the cases it's
   genuinely stuck on (can't reconcile) or where it thinks the first pass made a mistake —
   correcting them if it can — so that I focus my attention only where judgement is needed.

5. **(MUST HAVE)** As a preparer, I want to optionally type guidance, then click
   **Re-review** once to have the reviewer take another pass — with or without my notes — so
   that I decide when the next pass runs and stay in control of judgement calls.

---

## Detailed User Flows

### Flow 1 — Automatic review pass (Stories 1–3)

- **Trigger:** Extraction + merge + the first cross-check pass have completed for a
  canonical-mode run, and at least one cross-check failed (or at least one open conflict
  exists in `run_concept_conflicts`). This replaces the point where
  `_run_canonical_correction_pass` runs today.

- **Steps & System Response:**

  1. **Snapshot the original.** Before the reviewer touches anything, the system snapshots
     the run's current facts as the immutable **original version**. This is what
     "Revert to original" restores, and what the diff is computed against.

  2. The system emits a `pipeline_stage` event with stage `reviewing` so the UI shows
     "Reviewing and reconciling…" instead of a silent gap.

  3. The system assembles the **review packet** and hands it to the reviewer agent:
     - the list of failing cross-checks (name, expected, actual, difference, and the
       failing cell's `target_sheet`/`target_row` where known);
     - the list of open conflicts from `run_concept_conflicts`;
     - read access (via tools) to all face-statement and sub-sheet facts for the run.

  4. The reviewer **investigates** using read-only tools:
     - `read_facts` — read any current value (face row or sub-sheet row) for the run.
     - `trace_cascade_source` — given a failing face cell, return which sub-sheet total
       feeds it (the shared concept + its child rows), so the agent can walk *down* the
       chain to the origin instead of re-deriving the structure. (New tool; built on the
       existing `concept_uuid` / `concept_render_aliases` / `concept_edges` linkage.)
     - `view_pdf_pages` — re-read the relevant note/face pages in the source PDF to check
       what the number *should* be.
     - `calculator` — exact arithmetic to confirm a subtotal or recompute a difference.

  5. When it understands a problem, the reviewer acts via two tools that write into the
     **reviewer version** (the original snapshot is untouched):

     - **`apply_fix`** — correct a value. The fix is written through the existing facts API
       (`write_fact` / `apply_fact`), which cascades it up the totals. Every fix must carry
       a **reason** and a **grounding** (a cited PDF page, or `"arithmetic"` when it's the
       clean sum of rows already disclosed). Because the whole version is reversible, the
       agent applies grounded fixes freely — these become the **visible diff**, not flags.

       *Deterministic guard (code, not prompt):* `apply_fix` **rejects** a write that lacks
       grounding, and **rejects** any write that would balance a statement by plugging a
       residual into a catch-all / "Other" / abstract row — preserving invariant #17. A
       rejected write is reported back to the agent so it can investigate properly instead
       of plugging. If a breakdown genuinely can't reconcile, the agent leaves it and raises
       a flag (next tool).

     - **`raise_flag`** — surface something for the human. A flag is **not** "a change I
       wasn't allowed to make"; it's reserved for two cases:
       1. **Stuck:** the agent cannot reconcile a statement / cannot ground a correction.
          No value is changed; the flag explains what's unresolved and why.
       2. **Disputes the first pass:** the agent believes an earlier agent got something
          wrong. It records its reasoning, and — when it can ground a better value — it both
          **applies the fix *and* raises the flag** so the human can see "I changed this,
          here's why I think the original was a mistake."

  6. After the agent finishes (or hits its turn/time cap), if any facts changed the system
     **re-exports** the affected statements from the facts and **re-merges** the workbook —
     the same mechanism the canonical pass uses today — then **re-runs the cross-checks**
     and stores the post-review results (the `post_correction` cross-check phase already
     exists).

  7. The system emits `pipeline_stage: done`. The run lands in a terminal status
     (`completed` if no failing checks and no open flags; otherwise `completed_with_errors`).

- **Output (what the user sees on the Review tab):**
  - A clear indicator that a **reviewer version** exists, with a **"Revert to original"**
    button.
  - The **diff**: every value the reviewer changed, shown as original → reviewer, each with
    its reason and grounding (clickable PDF page where applicable). These are the applied
    fixes — shown visually, no action required.
  - The **Flags** list: the stuck / disputes-the-first-pass items, each with the agent's
    reasoning and the relevant cell/PDF page. A flag may or may not have an accompanying
    applied fix.
  - Cross-checks in their *post-review* state.

- **Error States:**
  - *Agent times out or hits the iteration cap mid-investigation:* whatever it already
    applied stays in the reviewer version (it's reversible anyway); the run lands
    `completed_with_errors`. (Mirrors the existing exhaustion handling.)
  - *Agent crashes / LLM error:* caught and surfaced as a structured SSE error
    (`reviewer_exception`). Because the original was snapshotted first, the run is never
    left in a half-written state — the user can revert to original. Run still finishes with
    a workbook.
  - *Re-export applies zero facts for a statement:* fall back to the existing per-statement
    agent workbook (same fallback the canonical exporter already has).
  - *A fix doesn't actually reduce failing checks:* it stays applied (it's grounded and
    reversible) but the remaining failures stay visible. Exactly one automatic review pass
    runs per trigger — no automatic looping.

### Flow 2 — User re-reviews, optionally with guidance (Stories 3–4)

- **Trigger:** The user clicks the **Re-review** button on the Review tab. This is always
  manual — the next pass never starts on its own.

- **User Input (optional):** A free-text box on the Review tab where the user can type
  guidance before clicking — either a general note or answers to specific open flags
  (e.g. "treat the RM2.1m as finance cost, not other expense"). **The text is optional:**
  if the user has nothing to add and just clicks Re-review, the agent simply
  re-investigates what's still wrong on its own. The intended pattern is *batch*: answer
  the flags you care about, then click Re-review once.

- **System Response:**
  1. Any typed guidance is saved against the run (and against the specific flags it
     answers, where the user attached it to a flag), and the answered flags move to
     `status = answered`.
  2. The reviewer agent is launched again. The review packet now **includes the current
     facts (the existing reviewer version), the failing cross-checks, the open/answered
     flags, and the human guidance text** as additional context. The original snapshot from
     Flow 1 is preserved — re-review keeps building on the *same* reviewer version, so
     "Revert to original" always goes all the way back to the first extraction.
  3. The reviewer re-investigates with that guidance and applies/raises via the same
     `apply_fix` + `raise_flag` tools, guard, re-export, and re-check pipeline as Flow 1.
     A flag the human answered and the agent then resolves moves to `status = resolved`.

- **Output:** Updated diff and Flags lists on the Review tab; resolved flags drop off the
  open list and appear in a resolved/history view.

- **Error States:** Same terminal-status and exception guarantees as Flow 1. Re-review
  reuses the existing run; it does not create a new run row. Each click runs exactly one
  pass — no automatic looping.

### Flow 3 — Revert to original (Story 3, MUST HAVE)

- **Trigger:** User clicks **"Revert to original"** on the Review tab.
- **System Response:** The system **discards the entire reviewer version and restores the
  original snapshot** taken at the start of Flow 1 — every fact returns to its
  pre-reviewer value at once. The facts are restored through the facts API (so cascades and
  totals recompute), the workbook re-exports, and the diff/flags from the reviewer pass are
  cleared (or moved to a "discarded" history view). The user is back to exactly the
  post-extraction state.
- **Output:** The download and the Values/face statements reflect the original extraction
  again; the "reviewer version exists" indicator disappears (until the user runs Re-review).
- **Note (finer-grained revert):** Reverting a *single* change while keeping the rest is a
  possible follow-up (the facts API + audit trail support it), but v1 ships the
  one-button **all-or-nothing** revert the user asked for. See Open Questions.

---

## Technical Approach

- **Stack (plain language):**
  - **The reviewer is an LLM agent** (built with PydanticAI, like the existing agents)
    given read-only investigation tools (`read_facts`, `trace_cascade_source`,
    `view_pdf_pages`, `calculator`) plus two write tools (`apply_fix`, `raise_flag`).
  - **Safety comes from versioning, not from forbidding writes.** Before the reviewer runs,
    the system snapshots the original facts. The reviewer writes into a reversible *reviewer
    version*; the original is always one button away. So the agent can apply its grounded
    fixes freely without risk of permanent corruption.
  - **The no-plug guardrail is deterministic code, not a prompt instruction.** `apply_fix`
    is a thin code wrapper around the existing facts API that *rejects* ungrounded writes
    and residual-plug writes before they land. The LLM cannot talk its way past it.

- **Why this over the alternatives:**
  - *Why not keep the current write-as-you-reason agent?* It writes irreversibly into the
    one authoritative copy — a wrong guess corrupts the download. Versioning removes that:
    same freedom to write, none of the permanence.
  - *Why investigation tools (vs. a one-shot reviewer)?* Because the real errors live down
    the cascade chain (sub-sheet → PDF note), and finding them is a multi-step trace, not a
    single guess.

- **Key Dependencies (all already in the codebase):**
  - Facts read/write API — `concept_model/facts_api.py` (`apply_fact`, `write_fact`,
    `patch_fact_value`) and cascade recompute (`concept_model/cascade.py`).
  - Cross-sheet rollup linkage — `concept_nodes`, `concept_render_aliases`,
    `concept_edges`, `concept_model/cell_resolver.py` (basis for the new
    `trace_cascade_source` tool).
  - Cross-check framework — `cross_checks/framework.py` (re-run + `post_correction`
    phase, with the existing `cross_check_*` SSE progress events).
  - PDF rendering — the existing `view_pdf_pages` tool.
  - Canonical export/merge — `_export_canonical_workbooks` and the merge step in
    `server.py`.
  - Pipeline-stage + SSE plumbing — the existing `event_queue` / `pipeline_stage` path.

- **Data Model (new) — plain terms:**
  - **Original snapshot** (new): a copy of the run's facts taken just before the first
    reviewer pass. This is the "original version" that "Revert to original" restores and
    that the diff is computed against. Simplest form: a `run_fact_snapshots` table holding
    the original `(concept_uuid, period, entity_scope, value, value_status, …)` rows for
    the run, written once before Flow 1. (We keep just the original; we don't stack every
    pass — re-review keeps building on the live reviewer version.)
  - **The reviewer version is just the live facts.** The reviewer writes through the
    existing facts API into `run_concept_facts`; "the reviewer version" = current facts,
    "the original" = the snapshot. The **diff** shown on the Review tab is computed by
    comparing current facts against the snapshot — no separate "applied changes" store
    needed (the audit trail in `concept_fact_events` carries the reason/grounding metadata).
  - **Reviewer flags** (new table, schema **v12**): one row per thing the agent wants the
    human to see. Each flag stores: the run, the concept/cell it's about, a `category`
    (`stuck` / `disputes_prior`), the agent's reasoning text, the PDF page reference, an
    optional link to the applied fix (when it both corrected and flagged), a status
    (`open` → `answered` → `resolved` / `dismissed`), and the human's free-text answer when
    given. Added via the established idempotent migration pattern (bump
    `CURRENT_SCHEMA_VERSION` to 12, add `CREATE TABLE IF NOT EXISTS` rows for the flags
    table and the snapshot table).

---

## Scope Boundaries

- **In Scope:**
  - A new reviewer agent (new prompt + new agent module).
  - Read tools: `read_facts`, `trace_cascade_source` (new), `view_pdf_pages`, `calculator`.
  - Write tools: `apply_fix` (guarded — rejects ungrounded writes and residual plugs) and
    `raise_flag` (`stuck` / `disputes_prior`).
  - The **original snapshot** mechanism (`run_fact_snapshots`) + one-button revert-to-original.
  - The `reviewer_flags` table (schema v12, brand-new — not an extension of
    `run_concept_conflicts`) and endpoints to list flags, attach a free-text answer, trigger
    a re-review, and revert to original.
  - Investigation over the **face statements and their sub-sheets** plus **re-reading the
    PDF** (including note pages in the PDF).
  - A **dedicated "Review" tab** in the run detail view, showing: a "reviewer version exists"
    indicator + **Revert to original** button, the original → reviewer **diff** (each change
    with reason + grounding), the Flags list, post-review cross-checks, an optional free-text
    guidance box, and a single **Re-review** button.
  - A `reviewing` `pipeline_stage` label and a `reviewer_exception` structured error.

- **Out of Scope (for v1):**
  - Editing the **notes-pipeline templates** (sheets 10–14 / 11–15) directly. The reviewer
    *reads notes in the PDF* but does not write to the notes-template sheets. (The notes
    pipeline stays all-LLM-judgement and separate.)
  - The legacy non-canonical correction path (`_run_correction_pass`) — left as-is.
  - Synchronous mid-run pausing for human input. Human feedback is **async**.
  - **Per-change revert** (reverting one fix while keeping the rest). v1 ships all-or-nothing
    revert-to-original only.
  - **Version history / stacking.** Only two anchors exist: the original snapshot and the
    live reviewer version. Re-review builds on the live version; intermediate passes are not
    individually preserved.
  - Multi-round automatic looping. Exactly one automatic pass per trigger; further passes
    are human-initiated.

- **Known Limitations (v1):**
  - The reviewer reasons over each pass in a bounded number of turns (respecting the
    iteration cap that must stay below pydantic-ai's internal request limit). Very tangled
    multi-error runs may need a couple of human-triggered re-reviews to fully resolve.
  - The no-plug guard will sometimes block a fix the agent *could* have grounded better; in
    that case it raises a `stuck` flag instead of plugging — correct, if occasionally
    conservative.
  - The reviewer touches values only; it never rewrites template structure or formulas
    (consistent with the no-hand-editing-formulas invariant).

---

## Resolved Decisions (from shaping)

- **Versioning is the safety model.** The reviewer applies grounded fixes freely into a
  reversible reviewer version; the original is snapshotted first and restorable in one click.
- **What the reviewer applies vs. flags:** it *applies* (and shows in the diff) any grounded,
  non-plug fix. It *flags* only when it's **stuck** (can't reconcile / can't ground) or when
  it **disputes the first pass** — and in the dispute case it may both fix and flag.
- **No-plug guardrail is code, not prompt:** `apply_fix` rejects ungrounded writes and
  residual plugs (invariant #17), reporting the rejection back to the agent.
- **Re-review trigger:** a single manual **Re-review** button; the free-text guidance box is
  optional. Batch pattern: answer flags, then click once.
- **Revert:** one-button all-or-nothing **revert to original** (MUST HAVE).
- **UI:** a dedicated **Review** tab.
- **Flags store:** a brand-new `reviewer_flags` table.
- **One combined list:** the existing auto-detected `run_concept_conflicts` are fed into the
  reviewer's packet as investigation input; the reviewer's flags are the **single
  user-facing "needs attention" surface.** The old conflicts list is not shown separately.
- **Snapshot scope:** facts only (`run_fact_snapshots`). The workbook is rebuilt from facts,
  so a facts snapshot is sufficient to revert. No separate file snapshot.
- **Cutover:** the new reviewer **replaces `_run_canonical_correction_pass` outright** — no
  side-by-side toggle. (The legacy *non-canonical* `_run_correction_pass` is untouched, since
  canonical mode is default-on.)

## Open Questions

None outstanding — all shaping decisions are resolved above. Ready to turn into an
implementation plan.
