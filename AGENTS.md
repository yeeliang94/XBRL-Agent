# AGENTS.md

Repository-wide instructions for coding agents. Codex loads this file
automatically. These rules apply to the whole repository unless a closer
`AGENTS.override.md` or `AGENTS.md` says otherwise. Dependency-owned files under
`node_modules/` and virtual environments do not change this repository's rules.

`CLAUDE.md` is the full source of truth. Read it before changing code, then read
the task-specific section and the pinning tests named there. This file is the
short operational guide, not a replacement for `CLAUDE.md`.

## Product

This application extracts Malaysian financial statements from PDF or Word files
into SSM MBRS XBRL Excel templates. It supports five primary statements and five
supplementary notes templates across:

- filing standards: MFRS and MPERS;
- filing levels: Company and Group;
- model providers: OpenAI, Google Gemini, and Anthropic through PydanticAI and,
  when configured, an OpenAI-compatible LiteLLM proxy.

The canonical concept model is the only extraction, review, and export path.

## Repository Map

- `server.py`, `run.py`, `coordinator.py`: web, CLI, and orchestration entry
  points.
- `extraction/`, `scout/`, `correction/`: statement discovery, extraction, and
  reviewer agents.
- `notes/`: notes extraction, review, formatting, coverage, persistence, and
  source-integrity logic.
- `concept_model/`: canonical concepts, facts, cascades, resolution, and export.
- `api/`, `auth/`, `db/`: API routes, authentication, and SQLite persistence.
- `cross_checks/`, `eval/`, `mtool/`: validation, evaluation, and mTool export.
- `web/src/`: React and TypeScript frontend.
- `XBRL-template-MFRS/`, `XBRL-template-MPERS/`: generated filing templates.
- `SSMxT_2022v1.0/`: source taxonomy and linkbases.
- `tests/`, `web/src/__tests__/`: backend and frontend tests.
- `docs/xbrl-design-system.html`: canonical web visual and interaction spec.
- `docs/prototype-ui-overhaul.html`: exact Direction A product reference.

## Run and Verify

```bash
# Mac web UI: LiteLLM proxy + server
./start.sh
# UI: http://localhost:8002; local proxy: http://localhost:4000

# CLI examples
python3 run.py data/FINCO.pdf
python3 run.py data/FINCO.pdf --level group
python3 run.py data/FINCO.pdf --standard mpers
python3 run.py data/FINCO.pdf --notes corporate_info list_of_notes

# Focused backend test: keep focused runs serial
python -m pytest tests/test_foo.py -q

# Default backend suite: excludes regression and live LLM tests
python -m pytest tests/ -n auto

# Frontend tests and production build
cd web && npx vitest run
cd web && npm run build

# Explicit live suites: require the matching API keys and fixtures
python -m pytest -m live -v
python -m pytest -m regression -v
```

On Windows, use `start.bat`; it sets `PYTHONUTF8=1`. The backend test setup
defaults to `AUTH_MODE=dev`. Production must not use dev auth.

## Working Agreements

- Keep changes limited to the request. Do not refactor adjacent code or alter
  templates, prompts, or styles as cleanup.
- Preserve user changes in a dirty worktree. Do not discard or overwrite work
  you did not create.
- State assumptions when a request touches a load-bearing invariant and the
  intended behavior is unclear.
- Prefer the smallest implementation that fits the current architecture. Do not
  add speculative abstractions, configuration, or dependencies.
- Explain outcomes in plain business English. Lead with the conclusion. Use
  short declarative sentences and define unavoidable technical terms.
- Do not use metaphors, flowery language, or editorial praise in chat, plans,
  documents, or commit messages.
- Treat `docs/PLAN-*.md` as historical rationale, not current contracts. Treat
  `docs/Archive/` as read-only audit history.

## Definition of Done

A change is complete only when all applicable items below are true:

1. The requested behavior works and has focused automated coverage.
2. Tests named by the relevant `CLAUDE.md` invariant pass.
3. Backend changes pass focused pytest checks. Broad or cross-cutting backend
   changes also pass `python -m pytest tests/ -n auto`.
4. Frontend changes pass targeted Vitest checks. Shared TypeScript, navigation,
   or build changes also pass `npm run build` in `web/`.
5. Prompt edits are followed by
   `python scripts/refresh_prompt_audit.py` and the prompt-audit pinning test.
6. Shared contracts, specifications, and their pinning tests are updated
   together where an invariant requires lock-step changes.
7. The final diff contains no unrelated changes. Report every check run and any
   check not run, with the reason.

Do not run live or regression LLM suites unless the task requires them and the
needed credentials and fixtures are available.

## Load-Bearing Rules

### Templates and taxonomy

- Never edit `XBRL-template-*/backup-originals/`.
- Never hand-edit template formulas. Regenerate them from the SSM calculation
  linkbase and preserve the required before/after snapshot.
- Never run `scripts/generate_mpers_templates.py` without `--snapshot`.
- Abstract section-header rows are not writable. Do not weaken the guard or let
  agents put balancing residuals into catch-all rows.

### Canonical pipeline and persistence

- Canonical mode is mandatory. Do not restore the deleted direct-xlsx path,
  `XBRL_CANONICAL_MODE`, or either legacy correction agent. A failed concept-tree
  bootstrap fails the run; there is no fallback rebuild.
- `run_multi_agent_stream` creates or reuses the audit row before validation.
  Every started run must finish in a terminal status. Keep
  `_safe_mark_finished` exception-safe, and mark a successful merge before the
  final status update.
- Database migrations are sequential and idempotent. Current schema version is
  37. Preserve inert historical fields and tables documented in `CLAUDE.md` so
  old databases can migrate forward.
- Any live workbook writer must use the shared atomic-save helper. Do not add a
  bare in-place `wb.save(path)` where another task may read the same workbook.
- Background reviewer, formatter, and suite-runner threads use separate event
  loops. Never share loop-bound `asyncio` primitives across them. Follow the
  cross-loop patterns in `CLAUDE.md` gotcha 2a.

### Models and agent execution

- PydanticAI is on the V2-compatible line with floor `>=1.107.1`, pinned by
  `constraints.txt`. Use `OpenAIChatModel` or `OpenAIResponsesModel` with
  `provider=OpenAIProvider(...)`; do not pass `base_url=` or `openai_client=`
  directly to the deprecated `OpenAIModel` alias.
- Do not assume `Agent._function_tools` exists. Stream tool events through
  `agent.iter()` and `node.stream()`. Usage is a property, not a method.
- Keep agent request caps below PydanticAI's default limit of 50. The project cap
  is 40 unless a tighter role-specific limit applies.
- Keep `end_strategy="early"` on live agents unless an eval-backed change proves
  another behavior is safe.
- Gemini 3 through the proxy must use temperature `1.0`. Do not send Gemini 3
  through an unverified remote OpenAI-compatible proxy that drops thought
  signatures.
- Model transport is model-specific. Preserve the GPT-5.6 Responses/Chat
  Completions routing, reasoning-level translation, and distinct cache option
  shapes documented in `CLAUDE.md` gotcha 2.
- Preserve the face retry lanes: provider-response timeouts receive two retries
  and must be recognized through PydanticAI's wrapped exception chain;
  connection-establishment failures remain in their separate one-retry lane.

### Extraction and review

- Scout page hints are advisory. Never add `allowed_pages` or any other page
  filter; extraction agents may inspect any valid PDF page.
- Do not add deterministic label matching, synonym dictionaries, or OCR to the
  notes pipeline. Matching remains LLM judgment.
- Keep MFRS, MPERS, Company, Group, and statement-variant routing explicit.
  Apply the MFRS Group SOCIE overlay only to MFRS Group SOCIE Default.
- The reviewer resolves concepts within the run's exact template family and
  entity scope. Do not resolve by sheet and row globally.
- Tag every deliberate cancellation of a registered reviewer task with
  `task_registry.USER_ABORT_REASON`; reviewer passes treat bare cancellation as
  a recoverable provider interruption.
- Automatic figures and notes reviewers may overlap, but the automatic notes
  reviewer must not receive the merged-workbook path. Apply its notes overlay
  atomically only after both reviewers finish.
- Preserve structured pipeline and cross-check progress events. Emit them
  through the existing queue so disconnect finalization remains safe.

### Notes and mTool

- `notes_cells` is the canonical notes store. Excel downloads are regenerated
  from database rows, including tombstones that represent reviewer deletions.
- In Sheet-12 fan-out, a later call for the same structured note number and
  resolved row replaces the earlier version; multiple chunks within one call
  remain combinable. Never infer note identity from prose.
- Extraction and reviewer agents must not author notes formatting. The formatter
  may apply validated style-only patches and must not change rendered text,
  numbers, table geometry, row or column structure, or placement.
- Source Word table markup may pass through only under the documented
  `data-source-styled="true"` contract. Prose remains style-free.
- Keep source-styled behavior synchronized across the sanitizer, TipTap schema,
  review CSS, mTool decorator, and clipboard decorator.
- `mtool/notes_decorate.py` and `web/src/lib/clipboard.ts` are behavioral twins
  at decorate/copy time. Change them together. Do not move their TX-specific
  white-border or legacy-width behavior into the database, sanitizer, or review
  page.
- Source-integrity status must never report clean when assessment is incomplete.
  Before changing this subsystem, read
  `tests/test_notes_integrity_false_greens.py` and `CLAUDE.md` gotcha 31.
- `mtool/offline_fill.py` stays standard-library-only and is the one patcher used
  by both CLI and server. Do not reserialize mTool files with openpyxl.

### Frontend

- Keep component styling in inline `style={}` props. Do not migrate it back to
  Tailwind or class-based utility styling.
- Follow `docs/xbrl-design-system.html` and Direction A in
  `docs/prototype-ui-overhaul.html` exactly. `docs/pwc-design-system.html` is
  a compatibility mirror for older pinning references, not a design authority.
  Shared tokens live in
  `web/src/lib/theme.ts`, shared layout primitives in `web/src/lib/uiStyles.ts`,
  and hover, focus, animation, and responsive states in `web/src/index.css`.
  Update the spec, implementation, and pinning tests together for shared rules.
- Run detail and Notes-12 both contain ARIA tabs. Scope role-based tests to the
  correct labelled tab list. Keep heavy tab content lazy-mounted.

### Authentication and safety

- Authentication guards every `/api/*` route except the documented auth and
  health exemptions. Authorization must be enforced server-side; hiding a UI
  control is not a security boundary.
- Production requires `SESSION_SECRET` and must refuse `AUTH_MODE=dev`.
- Do not expose secrets, weaken authorization, or add production dependencies
  without a request-backed reason.

## Code Review Rules

When reviewing changes, prioritize behavior and regressions over formatting.
Flag:

- violations of any load-bearing rule above;
- changes that create a false-success state, silent data loss, wrong entity or
  template routing, or a non-terminal run;
- workbook writes that are not serialized and atomic;
- database migrations that cannot upgrade every prior schema version;
- frontend-only authorization checks;
- notes formatting changes that alter content or move TX-only styling upstream;
- tests that assert implementation details while missing the user-visible
  failure mode.

For each finding, name the affected behavior, impact, file and tight line range,
and the safe correction. Do not report lint or formatting issues already covered
by automation unless they cause a functional problem.

## References

- `CLAUDE.md`: full invariant and troubleshooting reference.
- `docs/Archive/NOTES-PIPELINE.md`: notes subsystem history and detail.
- `docs/MPERS.md`: MPERS implementation details.
- `docs/agent-prompt-audit.html`: generated prompt inventory.
- `docs/workflows/*.md`: statement-specific extraction workflows.
- `docs/xbrl-field-descriptions.md`: XBRL field reference.

The following historically referenced files are absent. Do not spend time
searching for them: `docs/ARCHITECTURE.md`, `docs/SYNC-MATRIX.md`,
`docs/PORTING-WINDOWS.md`, `docs/ADR-002-socie-dividend-sign.md`, and
`docs/Archive/TEMPLATE-FORMULA-FIX-GUIDE.md`.
