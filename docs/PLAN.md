# Implementation Plan: Notes Reviewer Agent

**Overall Progress:** `100%` (all 13 steps + Phases 0–5 done). Deferred cleanup: delete the now-dead old validator agent (Step 7) — left green to avoid churning ~30 tests mid-stream.
**Design Reference:** shaped in the 2026-06-23 `/explore` + `/peer-review` session (this file is the source of truth). Mirrors the face reviewer: `docs/Archive/PLAN-reviewer-agent.md`, `correction/reviewer_agent.py`, `server.py::_run_reviewer_pass`. Related: `docs/PLAN-notes-template-registry.md` (`notes_nodes`), gotcha #16 (`notes_cells` canonical store), gotcha #21 (reviewer pass).
**Last Updated:** 2026-06-23

> Replaces the previous (completed, 100%) PLAN.md for "Configurable Notes-Table
> Clipboard Formatting" — that work is done and preserved in git history.

## Summary
Promote the single-iteration `notes_validator` (a cross-sheet duplication resolver) into a full **`notes_reviewer`** agent that inspects five check families over the prose notes sheets (10/11/12) and **acts** to fix them through guarded, snapshot-protected tools — the same shape as the face reviewer, but writing to `notes_cells` (DB) instead of `run_concept_facts`. Tier 3 scope: it may also **author** missing content grounded in the PDF and **re-route** a mis-placed note to a different template row.

## Key Decisions
- **Scope tier: Tier 3** — inspect + fix duplication/collision/titles AND author missing subnote/coverage content grounded from the PDF. Most capable, highest fabrication risk → strongest guard.
- **Collision policy: re-route allowed** — move a note to a different LEAF row only if a clearly-correct empty one exists; otherwise raise a flag. Never delete a valid note with no alternative.
- **Frontend home: the Notes tab** (beside `NotesReviewTab`), not the face Review tab — notes prose and concept facts are different data models.
- **Write surface = `notes_cells` ONLY** (peer-review #1) — tools mutate the DB in one transaction; xlsx is overlaid once after the pass + at download. No per-tool xlsx writes. Also fixes the latent clobber bug (old validator xlsx edits were overwritten by the `notes_cells` overlay on download).
- **Revert = full-set replace** (peer-review #2) — snapshot the full pre-review prose state; revert deletes all live prose rows then restores the snapshot, so authored (previously-blank) rows are correctly removed.
- **Provenance recomputed from DB, not disk** (peer-review #3) — persist detector inputs (`source_note_refs` + parent/sub refs per cell, scout inventory + subnotes) to the DB at extraction completion. Findings are derived, never the durable contract. (`infopack.json` + `*_payloads.json` are run-dir files, not durable for a re-review on a fresh process.)
- **No fuzzy matching in reviewer tooling** (peer-review #5) — write tools take an explicit `(sheet,row)` validated against a `notes_nodes` **LEAF** row + emptiness. No `_resolve_row`/SequenceMatcher; that stays in the extraction writer only, preserving the "notes pipeline is all-LLM-judgement" invariant.
- **Evidence enforced, not trusted** (peer-review #4) — `deps.viewed_pages` populated by `view_pdf_pages`; every write takes structured `source_pages` that must be a non-empty subset of viewed pages; evidence text derived from them.
- **Title/heading repair advisory-only in v1** (peer-review #6) — writer-owned `<h3>` headings can't be re-derived (structured `parent_note`/`sub_note` aren't persisted), so the reviewer flags title issues but does not auto-rewrite headings; `edit_note_cell` preserves leading `<h3>` + in-prose `<strong>(a)</strong>` and edits body only.
- **Invocation gate loosened** (peer-review #7) — pass runs whenever ANY prose sheet (10/11/12) was targeted; each family fires only where its inputs exist (cross-sheet dup still needs both 11 & 12).
- **Internal agent id stays `NOTES_VALIDATOR`** (avoids History/`run_agents` breakage); UI relabels to "Notes Reviewer". Numeric notes (13/14) stay with the face reviewer — prose only, no double-write.

## Per-family sheet scope
| Family | Sheets | Needs both 11 & 12? | Fix |
|---|---|---|---|
| Comprehensiveness (no content) | 10/11/12 | no | author (grounded) |
| Sub-note consistency (partial) | 11/12 | no | author append (a)/(b) |
| Cross-sheet duplication | 11↔12 | yes (skips if one absent) | clear wrong-sheet copy |
| Same-sheet collision | 12 | no | re-route or flag |
| Title/format | 10/11/12 | no | **advisory flag only (v1)** |

## Pre-Implementation Checklist
- [ ] 🟥 Peer-review findings folded in (all 8 accepted — see Key Decisions)
- [x] 🟩 Confirm the latent clobber bug with a focused test before Phase 1 (Step 0)
- [ ] 🟥 No conflicting in-progress work on `notes/` or `db/schema.py`

## Anchors (verified this session)
- `CURRENT_SCHEMA_VERSION = 22` → new steps are **v23**, **v24**.
- `notes_cells` cols: `sheet,row,label,html,evidence,source_pages,concept_uuid` — no provenance. Repo: `upsert_notes_cell`, `list_notes_cells_for_run`, `delete_notes_cells_for_run_sheet` (`db/repository.py`).
- `notes_nodes.kind` = `'ABSTRACT' | 'LEAF'` (`db/schema.py:505`).
- Mirror: `snapshot_facts` / `revert_to_original` (`concept_model/versioning.py`), `_run_reviewer_pass` (`server.py:1193`), `_reexport_and_remerge_from_facts` (`server.py:296`).
- `_AGENT_ROLES = ("scout","reviewer","SOFP","SOPL","SOCI","SOCF","SOCIE")` (`server.py:2381`); model allowlist `api/config_routes.py:179`.
- `NOTES_VALIDATOR_AGENT_ID = "NOTES_VALIDATOR"` (`server.py:1000`); current pass `_run_notes_validator_pass`; gate `have_both_sheets` (`server.py:4868`).
- Detectors already built last task: `detect_same_sheet_row_collisions`, `detect_subnote_coverage_gaps`, sidecar `row_label`, `notes_structural_findings.json` (become packet inputs here).

---

## Tasks

### Phase 0: Prove the premise 🟩
- [x] 🟩 **Step 0: Pin the clobber bug** — demonstrate the old validator's xlsx write is lost on the `notes_cells` overlay, justifying DB-only writes.
  - [ ] 🟥 Test: populate `notes_cells` for a run; edit the same cell in the merged xlsx via `_rewrite_cell_impl`; run `overlay_notes_cells_into_workbook`; assert the DB value (not the xlsx edit) wins.
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_reviewer_clobber.py -q` documents the overwrite. Pins the DB-only direction; repoint after Phase 1.

### Phase 1: Correctness foundation (write surface, safety, durable provenance) 🟩
- [x] 🟩 **Step 1: Durable detector-provenance store (schema v23)** — persist what the detectors need so re-review recomputes from the DB, not run-dir files.
  - [ ] 🟥 Add `notes_cell_provenance(run_id,sheet,row,source_note_refs JSON,parent_ref,sub_ref)` + `run_notes_inventory(run_id,note_num,subnote_refs JSON,title,page_lo,page_hi)`; bump `CURRENT_SCHEMA_VERSION` to 23 with idempotent `CREATE TABLE IF NOT EXISTS` steps.
  - [ ] 🟥 Write both at extraction completion (from writer sidecars + `infopack.notes_inventory`).
  - [ ] 🟥 Repo helpers: `upsert_notes_provenance`/`fetch_notes_provenance(run_id)`, `upsert_notes_inventory`/`fetch_notes_inventory(run_id)`.
  - **Verify:** `pytest tests/test_db_schema_v23.py` (fresh-init + step-migration); new test runs the mocked notes pipeline then asserts the leases `(3 → [3.1,3.2,3.3,(a),(b)])` shape returns from the DB with no disk reads.
- [x] 🟩 **Step 2: Detectors read from DB provenance** — keep the pure functions, swap only their feed.
  - [ ] 🟥 `load_provenance_entries(run_id, db_path)` returns the same `entries` shape detectors already consume (`sheet,row,row_label,source_note_refs,content_preview`).
  - [ ] 🟥 Factory accepts `run_id`+`db_path`; falls back to sidecars only if provenance rows are absent (legacy runs).
  - **Verify:** existing `tests/test_notes_validator_agent.py` detector tests pass unchanged; new test asserts identical findings from DB vs sidecars.
- [x] 🟩 **Step 3: Notes snapshot + revert (schema v23, replace-semantics)** — reversibility before any write tool exists.
  - [ ] 🟥 `run_notes_cell_snapshots` (mirrors `run_fact_snapshots`: `run_id,sheet,row,label,html,evidence,source_pages,snapshot_at`).
  - [ ] 🟥 `snapshot_notes_cells(db_path,run_id)` copies ALL current prose rows once; `revert_notes_to_original(db_path,run_id)` deletes all live prose rows then restores the snapshot set.
  - **Verify:** `pytest tests/test_notes_versioning.py`: snapshot → author new row + clear existing row + edit a third → revert → assert exact original set (authored row gone, cleared row back, edit undone).
- [x] 🟩 **Step 4: `viewed_pages` tracking** — prerequisite for the evidence guard.
  - [ ] 🟥 `deps.viewed_pages: set[int]`; `view_pdf_pages` adds every successfully-rendered page.
  - **Verify:** unit test asserts `deps.viewed_pages` reflects exactly the rendered (valid) pages, never the invalid ones.

### Phase 2: Reviewer core (guarded fix tools + 5-family packet) 🟩
- [x] 🟩 **Step 5: New title/format detector** — the only missing detector.
  - [ ] 🟥 `detect_title_format_issues(entries, inventory)` — flags heading-hierarchy mismatch vs scout note title + dropped in-prose `(a)/(b)` `<strong>` labels (advisory). Pure function.
  - **Verify:** `pytest tests/test_notes_validator_agent.py -k title_format` — flags a `<h3>`-divergent cell, leaves a clean cell.
- [x] 🟩 **Step 6: The no-fabrication guard** — deterministic gate every write passes (analogue of `classify_apply_fix_guard`).
  - [ ] 🟥 Rejects: empty/zero-overlap `source_pages` vs `viewed_pages`; target not a `notes_nodes` LEAF; `author`/`move` target not empty; `author` for a `note_num` absent from `run_notes_inventory`; over-cap content; unsanitary HTML. Returns a machine-readable rejection kind (telemetry).
  - **Verify:** `pytest tests/test_notes_reviewer_guard.py` — one rejection per kind + one accept.
- [x] 🟩 **Step 7: Write tools (DB-only) + read tools** — the agent can now act.
  - [ ] 🟥 Read: `view_pdf_pages`, `read_note_cell`, `list_note_cells(sheet)`, `read_template_labels(sheet)` (from `notes_nodes`).
  - [ ] 🟥 Write (all: guard → snapshot-on-first-write → `upsert_notes_cell`/delete → sanitize → cap → audit): `edit_note_cell` (body-only, preserves `<h3>`+`<strong>(a)</strong>`), `author_note_cell`, `move_note_cell` (explicit LEAF+empty target), `clear_note_cell`, `raise_flag`.
  - [ ] 🟥 Remove the old `rewrite_cell`→xlsx path + `_REWRITE_ALLOWED_SHEETS` xlsx logic.
  - **Verify:** `pytest tests/test_notes_reviewer_tools.py` (`TestModel`): each tool mutates only `notes_cells`; `move` to occupied/abstract row refused; `edit` preserves a leading `<h3>`.
- [x] 🟩 **Step 8: Packet + prompt + agent factory** — wire 5 families into one reviewer prompt; loosen scope.
  - [ ] 🟥 `create_notes_reviewer_agent(...)` builds the packet from all five detectors with per-family sheet scope; `prompts/notes_reviewer.md` (inspect → fix grounded → flag when unsure; re-route rules; advisory titles).
  - [ ] 🟥 Reuse `compute_reviewer_turn_cap` (dynamic, <50).
  - **Verify:** `pytest -k reviewer_packet` — renders present families, omits absent ones; turn cap `< 50`.

### Phase 3: Orchestration parity 🟩
- [x] 🟩 **Step 9: `_run_notes_reviewer_pass`** — replace `_run_notes_validator_pass`.
  - [ ] 🟥 `snapshot_notes_cells` once → agent loop (`run_agent_loop`, wall-clock cap) → overlay `notes_cells` into durable workbook → `reviewing_notes` stage event. Gate: any prose sheet targeted.
  - **Verify:** `pytest tests/test_notes_reviewer_pipeline.py` (mocked) — runs on a single-prose-sheet run, lands terminal, emits stage event, snapshot taken once.
- [x] 🟩 **Step 10: Flags table + async manual re-review (schema v24)** — schema v24 + repo helpers + reconcile + flag-persist + GET/re-review/status/revert/flag-answer ROUTES (api/notes_reviewer.py) — durable, restart-safe.
  - [ ] 🟥 `notes_review_flags` + `notes_review_tasks` (one row/run, stale-reconcile at startup), mirroring `reviewer_flags`/`run_review_tasks`.
  - [ ] 🟥 Endpoints: `GET /api/runs/{id}/notes-review`, `POST …/re-review` (async thread, model override, 503 on launch-persist failure), `GET …/re-review/status`, `POST …/revert-to-original`, `POST …/notes-flags/{id}/answer`.
  - **Verify:** `pytest tests/test_db_schema_v24.py` + `tests/test_notes_reviewer_routes.py` — async launch, status polls to done, revert restores, simulated-restart reconciles a stale `running` row.
- [x] 🟩 **Step 11: Settings + model resolution** — `notes_reviewer` selectable + auto-trigger toggle.
  - [ ] 🟥 Add `notes_reviewer` to `server._AGENT_ROLES`; `XBRL_NOTES_AUTO_REVIEW` (default on) + `XBRL_DEFAULT_MODELS["notes_reviewer"]`; round-trip via `/api/settings`+`/api/config`; update settings types.
  - **Verify:** `pytest tests/test_settings_api.py -k notes_reviewer` — round-trips without 400; auto-trigger toggle honoured.

### Phase 4: Frontend (Notes tab) 🟩
- [x] 🟩 **Step 12: Notes-reviewer panel** — findings, diff, revert, re-review, flags.
  - [ ] 🟥 Extend `NotesReviewTab` (lazy, inline styles per gotcha #7): findings grouped by family, per-fix before/after prose diff, Revert-to-original, Re-review + model dropdown, flags list with answer. Reuse `ReviewTab.tsx` patterns.
  - **Verify:** `cd web && npx vitest run NotesReviewTab` — panel renders findings from a mocked `/notes-review`; revert/re-review call the right endpoints; flag answer posts.

### Phase 5: Comprehensiveness hardening 🟩
- [x] 🟩 **Step 13: Adversarial tests on `author_note_cell`** — the riskiest writes.
  - [ ] 🟥 Tests: refused without viewed-page evidence; refused for a note scout never saw; refused into occupied/abstract row; accepted only with `source_pages` ⊆ `viewed_pages`; authored row removed on revert.
  - **Verify:** `pytest tests/test_notes_reviewer_authoring.py` green; full notes/reviewer suite (`pytest tests/ -k "notes or reviewer"`) green except the pre-existing unrelated `test_notes_models_unknown_key_ignored`.

---

## Rollback Plan
If something goes badly wrong:
- **Schema:** v23/v24 are additive `CREATE TABLE IF NOT EXISTS` (+ nullable columns) — a forward DB is unaffected by reverting code; new tables go unused. Never destructive to existing rows.
- **Behaviour kill-switch:** `XBRL_NOTES_AUTO_REVIEW=false` disables the auto pass (manual re-review still available, never auto-fires). Pipeline then behaves as pre-reviewer; gotcha #20 keeps post-extraction failures non-fatal.
- **Per-run undo:** every reviewed run has a `run_notes_cell_snapshots` set — `POST …/revert-to-original` restores the original extraction prose in one call.
- **Code revert:** Phases 2–5 are independent of Phase 1's DB tables; reverting agent/tooling commits leaves the durable provenance/snapshot tables harmlessly in place.
- **State to check:** compare `notes_cells` vs `run_notes_cell_snapshots`; confirm the download overlay still reads `notes_cells`; check `notes_review_tasks` has no orphaned `running` row (startup reconcile handles it).
