# Notes Pipeline

Companion to `CLAUDE.md` gotcha #14. This is the full walkthrough; CLAUDE.md
only carries the load-bearing invariants.

## What It Does

Fills MBRS notes templates (sheets 10–14 on MFRS, 11–15 on MPERS) in parallel
with the face-statement agents. Discovery is PDF-first: scout extracts a
`notes_inventory: list[NoteInventoryEntry]` from the PDF, then per-template
agents read those notes and write content into the matching template rows.

There is **no deterministic matching, no OCR, no synonym dictionary** — every
matching decision is pure LLM judgement on the rendered PDF pages.

## Templates and Runners

| Template (MFRS slot) | Sheet | Runner |
|---|---|---|
| `10-Notes-CorporateInfo.xlsx` | `Notes-CI` | single agent |
| `11-Notes-AccountingPolicies.xlsx` | `Notes-SummaryofAccPol` | single agent |
| `12-Notes-ListOfNotes.xlsx` | `Notes-Listofnotes` | **N parallel sub-agents** (model-aware) |
| `13-Notes-IssuedCapital.xlsx` | `Notes-Issuedcapital` | single agent (numeric) |
| `14-Notes-RelatedParty.xlsx` | `Notes-RelatedPartytran` | single agent (numeric) |

On MPERS, the same five templates live in slots 11–15 (see `docs/MPERS.md`).
The MPERS notes pipeline is MPERS-aware: prompts branch on
`filing_standard`, labels carry the `[text block]` taxonomy suffix
which is stripped before comparison, and `create_notes_agent` seeds
the template's actual row labels into the system prompt. See
[`docs/MPERS.md` — Notes Pipeline MPERS-Awareness](MPERS.md#notes-pipeline-mpers-awareness-2026-04-23-hardening)
for the full breakdown.

## Sheet-12 Fan-Out

Sheet 12 has 138 target rows — a single agent choosing among 138 labels is slow
and error-prone. The sub-coordinator splits scout's inventory into
page-contiguous batches, runs N agents in parallel, aggregates payloads, and
writes once.

### Model-Aware Parallelism

`pricing.resolve_notes_parallel(model)` reads the `notes_parallel` field from
`config/models.json`:

- **Cheap/fast models drop to 2-way** — `gpt-5.4-mini`, `gemini-*-flash-*`,
  `claude-haiku-4-5`. They ship requests through the provider's TPM bucket fast
  enough to trigger HTTP 429 at 5-way.
- **Heavy/slow models stay at 5** — `gpt-5.4`, `claude-sonnet-4-6`,
  `claude-opus-4-6`, `gemini-3.1-pro-preview`.
- **Unknown model ids** fall back to `DEFAULT_NOTES_PARALLEL = 5`.

The 429 retry infrastructure in `notes/_rate_limit.py` is unchanged — the
per-model parallelism just reduces how often the retry path triggers.

**Provider-prefix contract:** `pricing._normalize` must strip the same prefixes
as `server._PROVIDER_PREFIXES`, otherwise direct-mode OpenAI/Anthropic models
(which reach PydanticAI as bare names like `gpt-5.4-mini`) silently default the
lookup.

## Retry Budget

Every single notes agent is retried at most once on non-cancellation errors.
Sub-agents for Sheet 12 have the same max-1-retry budget.

Exhausted budgets emit side-logs under the run's `output/` directory:

- `notes_<TEMPLATE>_failures.json` — single sheet retry exhaustion
- `notes12_failures.json` — Sheet 12 sub-agents that lost coverage
- `notes12_unmatched.json` — notes funnelled into row 112 ("Disclosure of other
  notes to accounts"); only written when non-empty

## Cell Format

- Plain text. `\n\n` for paragraph breaks (Excel renders as Alt+Enter line
  breaks).
- ASCII-aligned tables.
- 30,000-char cap (`notes.writer.CELL_CHAR_LIMIT`). Longer content is truncated
  with a `[truncated -- see PDF pages N, M]` footer.

## Group vs Company Column Rules

- **Prose rows** (sheets 10, 11, 12) write content to col B only — Company CY
  on company filings, Group CY on group filings. Other value columns stay
  empty.
- **Numeric rows** (sheets 13, 14) fill all four value columns on group
  filings: `B=Group-CY, C=Group-PY, D=Company-CY, E=Company-PY`.
- **Evidence** always lands in col D (company) or col F (group).

The writer enforces this in `notes/writer.py`. The prompt in
`prompts/_notes_base.md` mirrors the same rules so the LLM produces consistent
output.

## Invocation

```bash
# CLI
python3 run.py data/FINCO.pdf --notes corporate_info list_of_notes

# Web UI — 5 checkboxes in PreRunPanel, default OFF
```

## Scanned-PDF Fallback for `notes_inventory`

`scout.notes_discoverer.build_notes_inventory` runs a fast PyMuPDF-regex pass
by default. On image-only (scanned) PDFs PyMuPDF returns empty text and the
regex finds nothing. In that case, if the caller passed a `vision_model` (the
scout always does; it's the same PydanticAI `Model` driving the scout run),
the function falls back to `scout.notes_discoverer_vision._vision_inventory`.

**How the vision fallback works:**

1. Renders the notes section to PNG in 8-page batches with a 1-page overlap.
2. Runs up to 5 batches in parallel through a dedicated one-shot
   `_VisionBatch`-schemad agent.
3. Stitches the batches back together:
   - Non-terminal notes get `last_page = next_note.first_page - 1` (LLM's end
     is ignored).
   - The terminal note uses `min(LLM-last_page, notes_end)` so it can't
     silently absorb Directors' Statement / auditor's report pages
     (peer-review MEDIUM fix, 2026-04-20).

**Tightening the scan range:** callers who know the true notes-section end
(e.g. scout walking the TOC for "Statement by Directors" / "Independent
Auditors' Report") can pass `notes_end_page=N` to tighten the vision scan range
and the terminal clamp.

**Safety:** scout mis-offsets that push `notes_start_page` past `pdf_length`
short-circuit to `[]` with a warning rather than raising. Per-batch failures
log and skip; all-batch failure returns `[]`, preserving the loud-fail
contract in `notes/coordinator.py` for Sheet 12.

**Temperature** is pinned at 1.0 (same constraint as face agents on Gemini 3).

**Observability:** look for `vision inventory tokens: input=X output=Y across
N/M batches` in the logs to see what the fallback cost on a given run.

## Key Files

- `notes_types.py` — registry + `notes_template_path(..., standard=)`
- `notes/agent.py` — per-template agent factory
- `notes/coordinator.py` — top-level fan-out + retry budget
- `notes/listofnotes_subcoordinator.py` — Sheet-12 sub-agents
- `notes/writer.py` — xlsx writer + column rules
- `notes/payload.py` — `NotesPayload` schema
- `notes/_rate_limit.py` — 429 retry helper
- `scout/notes_discoverer.py` — regex-first inventory
- `scout/notes_discoverer_vision.py` — vision fallback
- `prompts/_notes_base.md` — shared notes persona + contract
- `prompts/notes_*.md` — per-template prompts
- `config/models.json` — `notes_parallel` per model
- `pricing.py` — `resolve_notes_parallel`

## Tests

| Test | Pin |
|---|---|
| `tests/test_notes_retry_budget.py` | max-1-retry + failure side-log shape |
| `tests/test_notes_continuation.py` | multi-page continuation prompt contract |
| `tests/test_notes_char_limit.py` | 30K char-limit truncation |
| `tests/test_notes_parallel_resolver.py` | `resolve_notes_parallel` lookup |
| `tests/test_notes12_parallel_wiring.py` | parallelism wired end-to-end |
| `tests/test_notes_writer.py` | Group/Company column rules |
| `tests/test_notes_discoverer_vision.py` | chunker/stitcher/scan/orchestrator |
| `tests/test_scout_notes_inventory.py` | text-PDF regressions + scanned-PDF wiring |
| `tests/test_scout_notes_inventory_vision_live.py` | live integration (`pytest -m live`) |
| `tests/test_notes_e2e_*.py` | per-sheet E2E with mocked agents |
| `tests/test_notes_e2e_full_pipeline.py` | full cross-sheet coordinator run |
| `tests/test_server_notes_api.py` | `notes_to_run` request plumbing + SSE shape |
