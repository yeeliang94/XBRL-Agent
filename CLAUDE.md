# XBRL Agent — Claude Code Instructions

Keep this file small. Claude Code loads it for every task. The shared operating
contract is [`AGENTS.md`](AGENTS.md); read and follow it before changing files.
The detailed incident history and pinning-test map live in
[`CLAUDE-REFERENCE.md`](CLAUDE-REFERENCE.md). Read only the sections relevant to
the task.

## Start Here

1. Read `AGENTS.md`.
2. Classify the task using the routing table below.
3. Read the listed numbered sections in `CLAUDE-REFERENCE.md` and the pinning
   tests named there. Do not read the reference end to end by default.
4. Inspect the current code and configuration before relying on a documented
   default, version, path, or count. The repository is authoritative for facts
   that can change mechanically.
5. Make the smallest in-scope change and meet the applicable definition of done
   in `AGENTS.md`.

## Task Routing

| When the task changes… | Read these detailed invariants |
|---|---|
| Model construction, provider routing, reasoning, caching, retries, telemetry, or agent loops | 2, 2a, 5, 6, 18 |
| Templates, taxonomy, formula generation, statement variants, filing standard, or filing level | 3, 4, 12, 15, 17, 21 |
| Run orchestration, persistence, cancellation, progress events, cross-checks, or workbook writes | 9–11, 18–22, 25 |
| Scout behavior, extraction prompts, page access, or source-document context | 1, 12–15, 17, 29 |
| Notes extraction, HTML, review, formatting, coverage, source lineage, or source integrity | 13, 14, 16, 22, 27, 29, 31 |
| mTool export, clipboard decoration, filing readiness, or receipts | 16, 21, 28 |
| Frontend layout, navigation, styles, accessibility, or shared design tokens | 7, 16, 19, 21, 27, 28, 30 |
| Authentication, authorization, production startup, or user administration | 10, 11, 24 |
| Benchmarks, grading, repeats, suites, or quality trends | 11, 21, 23, 30 |

Use the narrowest applicable set. If a change crosses subsystems, read the
union of those sections.

## Invariant Index

The numbering is stable because plans, tests, and historical reports refer to
it. Detailed explanations and pinning tests are in
[`CLAUDE-REFERENCE.md`](CLAUDE-REFERENCE.md).

1. [Windows requires `PYTHONUTF8=1`](CLAUDE-REFERENCE.md#1-pythonutf81-required-on-windows).
2. [PydanticAI uses the V2-compatible API and model-specific transport](CLAUDE-REFERENCE.md#2-pydantic-ai-on-the-v2-line-floor-11071-pinned-by-constraintstxt).
2a. [Background agent threads must be safe across event loops](CLAUDE-REFERENCE.md#2a-cross-loop-safety-agents-on-background-threads).
3. [Template formulas come from the SSM linkbase](CLAUDE-REFERENCE.md#3-xbrl-templates-derived-from-ssm-linkbase).
4. [`compare_results.py` and current templates have known row offsets](CLAUDE-REFERENCE.md#4-compare_resultspy-vs-current-templates--row-numbering-differs).
5. [Proxy pricing-map SSL warnings and real LLM SSL failures are different](CLAUDE-REFERENCE.md#5-ssl-two-distinct-things--only-one-is-harmless).
6. [Per-turn token counts are deltas; full traces live on disk](CLAUDE-REFERENCE.md#6-per-turn-token-counts-are-deltas-of-cumulative-usage-approximate).
7. [The frontend uses inline styles and the repository design system](CLAUDE-REFERENCE.md#7-frontend-uses-inline-styles-not-tailwind).
8. [Windows may need explicit Node.js discovery](CLAUDE-REFERENCE.md#8-nodejs-may-not-be-on-path-windows).
9. [CLI and web runs use different output-directory identifiers](CLAUDE-REFERENCE.md#9-output-directory-structure).
10. [Every started run reaches a terminal status and preserves a successful merge](CLAUDE-REFERENCE.md#10-run-lifecycle--runs-row-created-before-validation).
11. [Database migrations are sequential, idempotent, and preserve inert history](CLAUDE-REFERENCE.md#11-db-schema--version-stepped-auto-migration-on-startup).
12. [Company and Group filing shapes stay explicit end to end](CLAUDE-REFERENCE.md#12-filing-level--company-vs-group).
13. [Scout page hints are advisory and never restrict PDF access](CLAUDE-REFERENCE.md#13-scout-page-hints-are-soft-guidance-only).
14. [Notes extraction remains LLM-judged with structured Sheet-12 revision identity](CLAUDE-REFERENCE.md#14-notes-feature--five-supplementary-templates-parallel-with-face).
15. [`filing_standard` is a first-class MFRS/MPERS routing axis](CLAUDE-REFERENCE.md#15-mpers--first-class-filing-standard).
16. [`notes_cells` is canonical HTML; reruns clobber edited rows after confirmation](CLAUDE-REFERENCE.md#16-notes-cells-are-html-excel-download-regenerates-from-the-db).
17. [Abstract rows are unwritable and balancing residuals never go into catch-all rows](CLAUDE-REFERENCE.md#17-abstract-section-header-rows-are-never-writable-agents-must-not-plug-residuals).
18. [Agent request caps stay below PydanticAI's default limit](CLAUDE-REFERENCE.md#18-iteration-caps-must-stay-below-pydantic-ais-silent-50-cap).
19. [Pipeline and cross-check progress use the shared event queue](CLAUDE-REFERENCE.md#19-pipeline-stage--cross-check-progress-events).
20. [Post-extraction failures surface as structured events](CLAUDE-REFERENCE.md#20-silent-post-extraction-failures-are-now-structured-sse-errors).
21. [The canonical concept model is the only extraction, review, and export path](CLAUDE-REFERENCE.md#21-canonical-concept-model--the-mandatory-pipeline).
22. [Shared workbooks use serialized access and atomic saves](CLAUDE-REFERENCE.md#22-agent-workbook-tools-must-serialise--atomic-save-shared-files).
23. [Gold evaluation compares canonical facts within an exact template set](CLAUDE-REFERENCE.md#23-gold-standard-eval--gold-is-facts-scoped-by-template-set).
24. [Authentication guards the API and authorization is enforced server-side](CLAUDE-REFERENCE.md#24-auth-layer-gates-every-api-route-schema-v18).
25. [Fact-based cross-checking and verification default on with explicit fallbacks](CLAUDE-REFERENCE.md#25-fact-based-verification-item-32--both-flags-default-on).
26. [The scanned-PDF readable-document feature is removed](CLAUDE-REFERENCE.md#26-scanned-pdf--readable-document--removed).
27. [Notes coverage reports incomplete assessment as unresolved, never clean](CLAUDE-REFERENCE.md#27-notes-coverage-checklist--post-reviewer-visibility--status-tipping).
28. [mTool filling uses semantic addressing, one standard-library patcher, and receipts](CLAUDE-REFERENCE.md#28-mtool-fill-pipeline--semantic-addressing-one-patcher-receipts).
29. [Word input converts at upload; PDF remains the extraction spine](CLAUDE-REFERENCE.md#29-word-docx-input--convert-at-the-door-pdf-stays-the-spine).
30. [Eval repeats and suites reuse normal runs and fixed scoring rules](CLAUDE-REFERENCE.md#30-evals-workspace--repeatsconsistency-mtool-gold-suites-trends).
31. [Source-integrity assessment must never produce a false clean result](CLAUDE-REFERENCE.md#31-notes-source-integrity--a-count-not-a-claim-ships-off).

## Reference Maintenance

- Put universal action, safety, and completion rules in `AGENTS.md` once.
- Put detailed subsystem rationale and failure history in
  `CLAUDE-REFERENCE.md`, under the existing stable invariant number.
- Add a new always-loaded rule only when it changes behavior for most tasks.
- For a new task-specific invariant, add a routing trigger, a checkable
  completion criterion, and the pinning test that proves it.
- Keep volatile facts in code or configuration. Link to their authoritative
  location instead of copying values into both instruction files.
