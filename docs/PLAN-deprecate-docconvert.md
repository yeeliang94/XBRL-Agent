# PLAN — Deprecate & Remove the Scanned-PDF → Readable-Doc Feature

**Status:** IMPLEMENTED (2026-06-25, branch `chore/remove-docconvert`). Open
choices resolved: PLAN/PRD **archived** to `docs/Archive/`; on-disk `models/`
bundle (707MB) **deleted** before dropping the gitignore rule.
**Scope:** full removal of the `docconvert/` feature (gotcha #26) — code, UI, tests,
fetch script, model assets, and the heavy dependencies it alone pulls in.

## Decisions (confirmed with owner)

- **Full removal** of code, UI, tests, fetch script, and the docconvert-only deps.
- **Leave the DB table + v21 migration step as-is.** `db/schema.py` walks old
  databases up one version at a time, so the `v20 → v21` step that creates
  `doc_conversions` **cannot be deleted** without breaking the upgrade chain.
  The table becomes an orphaned-but-harmless artifact in existing DBs.
  `CURRENT_SCHEMA_VERSION` stays **25**. No new migration.
- **Delete `models/` assets, the fetch script, and the `.gitignore` rule.**

## Key finding (verified)

Every heavy dependency — `docling`, `torch`, `onnxruntime`, `rapidocr`,
`easyocr`, `pypandoc_binary`, `python-docx` — is imported **only** by
`docconvert/` and its tests. Nothing else in the app uses them. `PyMuPDF`
(`fitz`) and `beautifulsoup4` are shared with the extraction/scout pipeline and
**must stay**. Full removal is therefore clean, with no dependency-resolution
risk. Removing these deps drops ~1.2GB (torch + bundled weights) from the deploy
artifact — a direct win for the Azure zip-deploy problem noted in gotcha #26.

## Guiding constraint

This brushes several load-bearing invariants — the schema migration chain
(gotcha #11), the `_lifespan` reconcile pattern, the `/api/config` contract, and
the auth-gated `/api/*` routes (gotcha #24). Removal must be surgical: delete the
feature, touch nothing adjacent.

---

## Phase 1 — Backend code removal

| Action | Location |
|---|---|
| Delete the entire package | `docconvert/` (`__init__.py`, `converter.py`, `routes.py`, `worker.py`) |
| Remove route registration | `server.py:2603–2604` (import + `_register_doc_convert_routes(...)`) |
| Remove startup reconcile block | `server.py:2505–2520` (the `reconcile_stale_doc_conversions` try/except in `_lifespan`) |
| Remove setting from `_load_extended_settings()` | `server.py:2892` (`"docling_ocr_engine": ...`) — surfaced by **`/api/settings`** (GET, `config_routes.py:127`), **not** `/api/config` (which does not carry it) |
| Remove settings-write branch | `api/config_routes.py:239–246` (the `docling_ocr_engine` POST handler + `from docconvert.converter import SUPPORTED_OCR_ENGINES`) |

**Repository methods** — `db/repository.py:1418–1589`: delete `DocConversion`
(dataclass), `_row_to_doc_conversion`, `create_doc_conversion`,
`create_doc_conversion_if_idle`, `fetch_doc_conversion`,
`update_doc_conversion_progress`, `mark_doc_conversion_finished`,
`is_doc_conversion_running`, `reconcile_stale_doc_conversions`.

**DB schema** — `db/schema.py`: **leave untouched.** The
`CREATE TABLE doc_conversions` in the v20→v21 step (~lines 717–729 / 1746–1770)
and the version comment stay, because the migration walker replays every
historical step. Add a one-line code comment marking the table as
deprecated/unused so a future reader doesn't mistake it for live.

## Phase 2 — Frontend removal

| Action | Location |
|---|---|
| Delete the page | `web/src/pages/ReadableDocPage.tsx` |
| Remove nav entry | `web/src/components/TopNav.tsx:28–30` (`{ id: "doc-convert", label: "Readable Doc" }`) |
| Remove `AppView` member + URL parse | `web/src/lib/appReducer.ts:126`, `232–235` |
| Remove import + route + render | `web/src/App.tsx:19`, `264–266`, `622–625` |
| Remove API client + types | `web/src/lib/api.ts:485–545` — the whole docconvert block: `DocConvertStatus`, `startDocConvert`, `getDocConvertStatus`, `docConvertViewUrl`, `docConvertDocxUrl`, `DocConvertModelsStatus`, `getDocConvertModels`, **and `fetchDocConvertModels` (api.ts:533–545)**. Also remove the `docling_ocr_engine` field from the **settings-update POST body type** at `api.ts:187` (this is the `updateSettings` payload, *not* `ConfigResponse`) |
| Remove stale GET-response type field | `web/src/lib/types.ts:476` — `docling_ocr_engine?` on `ExtendedSettingsResponse` |
| Remove OCR-engine setting UI | `web/src/components/GeneralSettingsForm.tsx` (~20 refs: type fields, load/save, selector UI ~513–519) |

> **Atomicity note:** the frontend reads/writes `docling_ocr_engine` through
> `/api/settings`. Remove every side in one change — `_load_extended_settings()`
> (server.py:2892), the POST handler (config_routes.py:239–246), the api.ts:187
> update-body type, `ExtendedSettingsResponse` (types.ts:476), and
> `GeneralSettingsForm` — or the build/typecheck breaks.

## Phase 3 — Tests

**Delete (backend):** `tests/test_docconvert.py`, `test_doc_convert_errors.py`,
`test_doc_convert_models.py`, `test_doc_convert_worker.py`,
`test_doc_convert_routes.py`, `test_doc_conversion_repo.py`,
`test_doc_convert_docx.py`.

**Keep:** `tests/test_db_schema_v21.py` — the migration step still exists and
still needs to pass. Confirm it does **not** import from `docconvert` before
finalizing (it should assert only the table shape).

**Frontend:** delete `web/src/__tests__/ReadableDocPage.test.tsx`; remove the
OCR-selector assertions in `SettingsModal.test.tsx:99–109`.

## Phase 4 — Dependencies & assets

- `requirements.txt` — remove lines **28–56** (the entire "Scanned PDF →
  Readable Document" block: `docling`, `onnxruntime`, `rapidocr`, `easyocr`, the
  torch CPU-wheel note, `pypandoc_binary`, `python-docx`).
- `.env` — remove line 58 (`XBRL_DOCLING_OCR_ENGINE='rapidocr'`).
- `scripts/fetch_docling_models.py` — delete.
- `.gitignore` — remove the `models/` rule (lines 46–49, including the comment).
  **Ordering constraint (P1):** the current rule ignores *all* of `models/`
  ([.gitignore:46](../.gitignore)). Deleting it *before* the on-disk bundle is
  gone makes the ~599MB `models/docling/` show as untracked and trivially
  stageable by accident. **Therefore: delete `models/docling/` from disk FIRST,
  then drop the ignore rule** — or, if the bundle must linger on a dev machine,
  keep/narrow the rule until cleanup is confirmed. In this repo `models/` holds
  only the docling weights, so once they're gone the rule has nothing left to
  guard.
- On-disk `models/docling/` (~599MB) — `rm -rf` it as the prerequisite above.
  **Owner to confirm** before deleting local files; note for the deploy/Azure
  bundle owner.

## Phase 5 — Docs & memory

- `CLAUDE.md` — remove gotcha #26 (~lines 1382–1415), the `XBRL_DOC_CONVERT_TIMEOUT_S`
  mention, and the Deeper-References row pointing at the PLAN doc. **Keep** the
  v20→v21 bullet in the gotcha #11 migration list (the step remains) but drop its
  "see gotcha #26" cross-ref.
- `docs/PLAN-scanned-pdf-to-doc.md` + `docs/PRD-scanned-pdf-to-doc.md` — move to
  `docs/Archive/` (repo convention for completed/retired plans) **or** delete.
  *(Owner choice — pending.)*
- `docs/PLAN.md:77` (P3) — the design-system-sweep checklist references
  `ReadableDocPage.tsx:210` as a `pwc.weight.light → regular` edit site. Once the
  page is deleted that checklist line is moot; strike it (or annotate
  "removed — file deleted") so the design sweep doesn't chase a missing file.
- `memory/project_scanned_pdf_to_doc.md` — update to mark the feature removed.
  **Note (P3):** this lives in the external agent-memory dir
  (`~/.claude/projects/-Users-user-Desktop-xbrl-agent/memory/`), **not** in the
  repo — a repo grep won't find it, but it does exist and is indexed in
  `MEMORY.md`. Update both the file and its `MEMORY.md` pointer line.

## Phase 6 — Verification

1. `rg -i "docconvert|docling|doc_conver|readable-doc|ReadableDoc|ocr_engine" \
      -g '!docs/PLAN-deprecate-docconvert.md' -g '!docs/Archive/**'`
   (exclude this plan and any archived PLAN/PRD so they don't self-match).
   **Expected retained matches** — anything else is a miss:
   - `db/schema.py` — the v20→v21 `CREATE TABLE doc_conversions` step + version
     comment (intentionally retained; migration chain).
   - `tests/test_db_schema_v21.py` — still pins that step (confirm it has **no**
     `from docconvert ...` import; it should assert only the table shape).
   If P5's "archive vs delete" picks *delete* for the PLAN/PRD, drop the
   `docs/Archive/**` exclusion — there should then be zero doc matches.
2. `./venv/bin/python -m pytest tests/ -q`
   (use the venv interpreter; bare `python3` is stale 3.9 / pydantic-ai 0.8.1).
3. `cd web && npx vitest run` and a `tsc`/build to catch dangling type refs.
4. Boot the server: confirm `_lifespan` and `/api/config` are clean, and a fresh
   DB still initializes to **v25**.

---

## Risks / watch-list

- **`/api/settings` contract** (see atomicity note above) — the `docling_ocr_engine`
  field spans five places (server `_load_extended_settings`, POST handler,
  api.ts:187 update-body, `ExtendedSettingsResponse`, `GeneralSettingsForm`);
  remove all in one commit.
- **gitignore ordering** — delete `models/docling/` from disk *before* dropping
  the `models/` ignore rule, or the bundle becomes accidentally stageable (P1).
- **`test_db_schema_v21.py`** must stay green — confirm no `docconvert` import
  before deleting the package.
- **Deploy bundle** — dropping torch + weights removes ~1.2GB; coordinate with
  whoever owns the deploy artifact.

## Open choices for the owner

1. Archive vs. delete the two `docs/*-scanned-pdf-to-doc.*` files.
2. Whether to `rm -rf models/docling/` on local machines / deploy hosts now or
   leave that to the operator.
