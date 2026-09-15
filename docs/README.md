# Documentation index

## Maintained references

| Document | Purpose |
| --- | --- |
| [Application overview](APP-OVERVIEW.html) | Product capabilities |
| [MPERS](MPERS.md) | Filing-standard reference |
| [Field descriptions](xbrl-field-descriptions.md) | Taxonomy field reference |
| [Statement workflows](workflows/) | Statement-specific extraction guidance |
| [mTool operator guide](../mtool/README.md) | Offline filling and validation |
| [mTool filling journey](MTOOL-FILL-JOURNEY.md) | Workbook preparation behavior |
| [Windows mTool retest guide](GUIDE-mtool-broken-file-windows-retest.md) | Broken-workbook diagnosis procedure |
| [Navigation journey](NAVIGATION-JOURNEY.md) | Navigation contract and prior audit context |

## Design and prompt contracts

| Document | Role |
| --- | --- |
| [XBRL design system](xbrl-design-system.html) | Current design authority |
| [UI prototype](prototype-ui-overhaul.html) | Direction A is the production contract |
| [Implementation matrix](xbrl-ui-overhaul-implementation.md) | Design requirements and pinning tests |
| [Compatibility design reference](pwc-design-system.html) | Retained for older references; not the design authority |
| [Agent prompt audit](agent-prompt-audit.html) | Generated prompt inventory; refresh with `scripts/refresh_prompt_audit.py` |

These paths are retained because instructions, generators, or tests reference them.

## Repository map

| Location | Purpose |
| --- | --- |
| `server.py`, `run.py`, `coordinator.py`, `api/` | Entry points and orchestration |
| `scout/`, `extraction/`, `correction/`, `notes/` | Extraction and review |
| `concept_model/`, `cross_checks/`, `mtool/`, `eval/`, `db/` | Canonical facts, filing, evaluation, persistence |
| `web/src/`, `tests/` | Frontend and backend tests |
| `prompts/` | Runtime agent prompts; tracked application inputs |
| `SSMxT_2022v1.0/`, `XBRL-template-*/` | Taxonomy and templates; backup originals are immutable |
| [Chomp](../tools/chomp/index.html) | Standalone HTML tool, moved from the repository root |
| `output/` | Local application database, settings, uploads, workbooks, and traces; preserve as application data |
| `data/` | Local source documents and existing tracked fixtures |

## AI working documents

Write working plans and PRDs in `docs/local/plans/`, handoffs and agent briefs
in `docs/local/handoffs/`, and investigations, experiments, session reports,
and reviews in `docs/local/reviews/`. This entire directory is ignored by Git.
Use a descriptive topic filename. Do not force-add working documents.
Maintained product references, contracts, and runtime prompts remain tracked.

Existing working documents were moved locally without changing their contents.
The optional `docs/local/README.md` and `docs/local/move-manifest.json` record
old-to-new paths and original hashes. They are not available in fresh clones.
Source comments that cite former plan paths are historical citations, not
required document dependencies. Local readers can resolve them using the move
index; current behavior is governed by code, invariants, and pinning tests.

The name `docs/PLAN.md` was reused for several projects. Its last contents now
live at `docs/local/plans/PLAN-template-map.md`. Older citations may describe
notes review, mTool filling, or prompt caching. Do not reinterpret all of those
citations as template-mapping references. The mTool plan has the explicit name
`docs/local/plans/PLAN-mtool-fill-pipeline.md`.

Working-document progress and status claims have not been revalidated. A move
does not mark work completed or superseded. Previously committed copies remain
in Git history; this cleanup does not rewrite history.

## Historical archive

[Archive](Archive/) remains tracked, unchanged, and read-only audit history.
The [notes pipeline history](Archive/NOTES-PIPELINE.md) and
[canonical notes decision](Archive/ADR-001-notes-db-canonical.md) retain their
existing paths and contract tests. Consult current invariants before applying
historical guidance.
