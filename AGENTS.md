# AGENTS.md

This is the repository-wide operating contract for coding agents. Codex loads
it automatically. A closer `AGENTS.override.md` or `AGENTS.md` may specialize
these rules for its directory. Ignore instruction files owned by dependencies,
virtual environments, or generated artifacts.

`CLAUDE.md` is the task router. `CLAUDE-REFERENCE.md` contains the detailed
numbered invariants and their pinning tests. Read only the sections relevant to
the task; do not load the full reference by default.

## Outcome and Authority

- For a request to answer, explain, audit, review, diagnose, or plan, inspect the
  relevant material and report the result. Do not change product code unless the
  request also asks for a change.
- For a request to change, build, fix, or doctor files, make the smallest
  in-scope local change and run proportionate non-destructive validation.
- Read-only inspection, in-scope local edits, and relevant tests do not require
  confirmation. Ask before destructive actions, external writes, paid or live
  model runs, new production dependencies, or a material expansion of scope.
- Preserve user work in a dirty worktree. Do not discard, overwrite, or reformat
  unrelated changes.

## Product Contract

This application extracts Malaysian financial statements from PDF or Word
files into SSM MBRS XBRL Excel templates. It supports MFRS and MPERS, Company
and Group filings, five primary statements, and five supplementary notes
templates. The canonical concept model is the only extraction, review, and
export path.

## Before Changing Files

1. Use the task table in `CLAUDE.md` to select the relevant numbered
   invariants. Read those sections in `CLAUDE-REFERENCE.md` and the pinning
   tests named there.
2. Read the nested `AGENTS.md` of each directory you change, when one exists.
3. Inspect current code or configuration for values that change mechanically,
   including schema versions, model defaults, flags, paths, and test counts.
4. If the request is ambiguous at a load-bearing boundary, state the competing
   interpretations and ask only when the choice would materially change the
   result.
5. Before adding, rewriting, or deleting tests, read `docs/TESTING.md`. Extend
   an existing behavior test when it already owns the outcome; add a new test
   only for a distinct observable behavior or risk boundary.

### Python interpreter

- On macOS and Linux, run repository Python commands with `venv/bin/python`.
  Fresh agent shells do not inherit the activation performed inside `start.sh`,
  and the system `python3` may be Apple's unsupported Python 3.9.
- On Windows, use `venv\Scripts\python.exe` outside an activated `start.bat`
  session.
- Do not use bare `python` or `python3` for repository commands. If the venv is
  missing, run `./start.sh` on macOS/Linux or `start.bat` on Windows to create
  it with a supported interpreter.

## Working Agreements

- Keep every changed line traceable to the request. Do not refactor adjacent
  code or alter templates, prompts, or styles as cleanup.
- Prefer the smallest implementation that fits the current architecture. Add
  no speculative abstraction, configuration, compatibility path, or
  dependency.
- Explain the outcome in plain business English. Lead with the conclusion. Use
  short declarative sentences and define unavoidable technical terms.
- Use no metaphors, flowery language, or editorial praise in chat, plans,
  documents, reviews, or commit messages.
- Write AI working plans, PRDs, handoffs, session reports, and investigation
  reviews under `docs/local/` (gitignored). Do not stage or force-add them.
  Use topic-specific filenames; never reuse `PLAN.md` for unrelated work.
- Keep maintained product documentation, contracts, workflows, and generated
  prompt audits tracked. Working documents are optional local context, never
  required inputs for builds, tests, or agent instructions.
- Treat old plan references in source comments as historical rationale, not a
  current contract. See `docs/README.md` for document locations.
  Treat `docs/Archive/` as read-only audit history.

## Definition of Done

A change is complete when the requested behavior works, the applicable checks
below pass, and the final diff contains no unrelated edits.

| Change | Required verification |
|---|---|
| Backend logic | Focused serial pytest for the changed behavior and every pinning test named by the relevant invariant |
| Broad or cross-cutting backend behavior | `venv/bin/python -m pytest tests/ -n auto` after focused checks |
| Frontend component or reducer | Targeted Vitest test |
| Shared TypeScript, navigation, or build configuration | Targeted Vitest plus `cd web && npm run build` |
| Agent prompt | `venv/bin/python scripts/refresh_prompt_audit.py` plus `tests/test_prompt_audit_matches_live.py` |
| Shared contract or specification | Update the implementation, contract, and pinning tests together |
| Template or taxonomy generation | Run the invariant-specific generator and snapshot checks; never substitute a hand edit |

Focused backend runs stay serial, for example
`venv/bin/python -m pytest tests/test_foo.py -q`. The default backend suite excludes
`live` and `regression` markers through `pytest.ini`. Run those suites only when
the task requires them and the necessary credentials and fixtures are present.

In the final response, list every check run and every applicable check not run,
with the reason.

## Load-Bearing Floors

These are concise tripwires, not the full rationale. Follow the routed section
in `CLAUDE-REFERENCE.md` before changing the surrounding subsystem. Floors
whose code lives in one directory are in that directory's `AGENTS.md`
(`db/`, `mtool/`, `notes/`, `tools/`, `web/`).

### Templates and taxonomy

- Treat `XBRL-template-*/backup-originals/` as immutable snapshots.
- Regenerate formula changes from the SSM calculation linkbase and retain the
  required before/after snapshot. Run
  `scripts/generate_mpers_templates.py` only with `--snapshot`.
- Never place a balancing residual in a catch-all or abstract row; leave the
  imbalance visible (`tools/AGENTS.md`).

### Canonical pipeline and persistence

- Canonical mode is mandatory. Do not restore the direct-xlsx path,
  `XBRL_CANONICAL_MODE`, or either deleted correction agent. Concept-tree
  bootstrap failure ends the run.
- Create or reuse the audit row before validation. Every started run must reach
  a terminal status. Keep `_safe_mark_finished` exception-safe and persist a
  successful merge before the final status update.
- Route live workbook writes through `utils/workbook_io.py` or the established
  atomic replacement path. Shared workbook access is serialized.
- Background reviewer, formatter, and suite-runner threads own separate event
  loops. Use the cross-loop patterns in invariant 2a for shared state.

### Models, extraction, and review

- Keep live agents on `end_strategy="early"`. A structured cap must fire
  before PydanticAI's request limit: keep it below PydanticAI's default, or
  pass an explicit limit through `agent_tracing.agent_usage_limits`
  (invariant 18).
- Scout page hints are advisory. Extraction agents may inspect any valid PDF
  page; no `allowed_pages` or equivalent filter may restrict them.
- Keep filing standard, filing level, statement variant, and entity scope
  explicit. Apply the Group SOCIE matrix overlay only to MFRS Group SOCIE
  Default. Resolve reviewer concepts within the run's exact template family
  and entity scope.
- Tag deliberate cancellation of a registered reviewer task with
  `task_registry.USER_ABORT_REASON`.
- Emit pipeline and cross-check progress through the existing queue. Automatic
  figures and notes reviewers may overlap, but the automatic notes reviewer
  receives no merged-workbook path; apply its overlay only after both finish.

### Authentication and safety

- Authentication guards every `/api/*` route except the documented auth and
  health exemptions. Enforce authorization server-side. Production requires
  `SESSION_SECRET` and refuses `AUTH_MODE=dev`.
- Keep secrets out of source, logs, fixtures, and responses. A production
  dependency requires a request-backed reason and user approval.
- App file operations go through `utils.paths.safe_path` or `safe_db_target`,
  which refuse paths outside the approved folders. Deletes, renames and replaces
  use `safe_entry_path`, which acts on a symbolic link and not its target.
  `operator_path` and `operator_entry_path` are only for a path someone typed
  into a command-line tool, never web request data.

## Windows Enterprise Environment

The owner tests real runs there. It can pull from GitHub but cannot push. Name
any run that only works there as unverified in the report. When you need
something from it, write one self-contained instruction block the owner can
paste to the agent there, including exactly what output to send back.

## Code Review Rules

Prioritize behavior and regressions over formatting. Flag violations of the
floors above and in nested `AGENTS.md` files, false-success states, silent data
loss, wrong entity or template routing, non-terminal runs, non-atomic workbook
writes, broken migration paths, frontend-only authorization, content-changing
notes formatting, and tests that miss the user-visible failure mode.

For each finding, state the affected behavior, impact, file and tight line
range, and safe correction. Do not report formatting issues already enforced by
automation unless they cause functional harm.

## References

- `CLAUDE.md`: task router and stable invariant index.
- `CLAUDE-REFERENCE.md`: detailed invariants, incidents, and pinning tests.
- `docs/Archive/NOTES-PIPELINE.md`: notes subsystem history and detail.
- `docs/MPERS.md`: MPERS implementation detail.
- `docs/agent-prompt-audit.html`: generated prompt inventory.
- `docs/workflows/*.md`: statement-specific extraction workflows.
- `docs/xbrl-field-descriptions.md`: XBRL field reference.

"ADR-002" in source comments means the SOCIE dividend sign convention
(dividends stored positive, subtracted by formula). That document no longer
exists; do not search for it.
