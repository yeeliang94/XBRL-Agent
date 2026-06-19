# Implementation Plan: Scanned PDF → Readable Document

**Overall Progress:** `92%` (Phases 1–6 complete; only the NICE-TO-HAVE
side-by-side view deferred)
**PRD Reference:** [docs/PRD-scanned-pdf-to-doc.md](PRD-scanned-pdf-to-doc.md)
**Last Updated:** 2026-06-19

## Summary

A standalone feature (independent of the XBRL extraction pipeline) that converts
a scanned PDF into a clean, copy-pasteable **Readable View** in the web app, plus
a **Download as Word** option. The engine is **Docling**, run **fully offline**
from a pre-bundled model folder. Conversions run as a **background job** with
live progress, reusing the durable background-thread + SSE patterns the re-review
pass already established.

## Key Decisions

- **Engine = Docling** (not LiteParse) — only Docling reconstructs scanned
  financial tables correctly (proven in the 2026-06-19 bake-off; LiteParse
  collapses columns on scans regardless of settings).
- **Offline by bundle** — pre-download ~599MB of weights once into a local
  `models/` folder; point Docling at it (`artifacts_path`) + bundled `.onnx` OCR
  + `HF_HUB_OFFLINE=1`. Verified working with all network blocked. Requires
  `onnxruntime` and **CPU-only** `torch`.
- **Output = HTML first, Word on demand** — HTML renders in-app and gives the
  best table copy-paste; `.docx` generated from it only when requested via
  Docling's built-in Word exporter (no `pandoc` dependency).
- **Background job, never inline** — conversions take ~5–12s/page; far past
  App Service's ~230s request cap. One conversion at a time (serialized).
- **Durable job state** — a new DB table (schema **v21**), mirroring
  `run_review_tasks`: survives restarts, stale `running` rows reconciled at
  startup.
- **Heavy content on disk** — the converted HTML is written to the output dir
  (like `*_conversation_trace.json`), DB holds a pointer + status only
  (hybrid-storage principle, gotcha #6).
- **Deployment packaging deferred** — container-vs-zip + tier + memory spike are
  Azure-deploy-time caveats (PRD "Deferred" section), not build blockers.

## Pre-Implementation Checklist

- [x] 🟩 All questions from exploration resolved (PRD Decisions section)
- [x] 🟩 PRD approved / up to date (`docs/PRD-scanned-pdf-to-doc.md`)
- [ ] 🟥 No conflicting in-progress work — **note:** working tree already has
  uncommitted changes (auth/admin/settings work + `docs/PLAN.md`). This feature
  touches separate files; confirm it lands on its own branch/commits so it
  doesn't tangle with that work.

---

## Tasks

### Phase 1: Conversion engine, offline (no app integration yet) — 🟩 DONE

- [x] 🟩 **Step 1: Add dependencies + offline model fetch script** — get the
  engine installable and the weights reproducibly bundleable.
  - [x] 🟩 Added `docling`, `onnxruntime`, `rapidocr` to `requirements.txt`
    with a CPU-only-torch deploy note (torch comes in transitively).
  - [x] 🟩 Added `scripts/fetch_docling_models.py` (layout + tableformer +
    rapidocr → `models/docling/`); added `models/` to `.gitignore`.
  - [x] 🟩 Fetch step documented in the script docstring (parallels setup_data.sh).
  - **Verify:** ✅ fetch script produced a 599MB `models/docling/` with 5
    RapidOcr `.onnx` files, exit 0.

- [x] 🟩 **Step 2: Standalone converter module** (`docconvert/converter.py`) —
  takes a PDF path → readable HTML, fully offline, page-by-page.
  - [x] 🟩 `convert_pdf_to_html(pdf_path, *, model_dir=None, progress_cb=None)`
    with `artifacts_path` + ONNX `RapidOcrOptions` + `HF_HUB_OFFLINE`.
  - [x] 🟩 Per-page conversion (matches no-stitch decision) → real
    `progress_cb(done, total)`; one model load reused across pages.
  - [x] 🟩 Reads `DOCLING_MODELS_DIR` env with repo default; clear
    `DocConvertError` for bad/password/empty PDFs + missing bundle.
  - **Verify:** ✅ `tests/test_docconvert.py` — 2 passed; SOFP page converts
    **with sockets blocked**, HTML has `<table>` + `3,141,738` + `Receivables`
    + `391,675`; progress == [(1,1)].
  - **Note (deviation):** added per-page error-resilience (a bad page embeds a
    marker and the rest continue) — small robustness add within Step 11's
    error-handling intent, not new scope. Flagging per the rules.

### Phase 2: Persistence (durable job state) — 🟩 DONE

- [x] 🟩 **Step 3: Schema v21 — `doc_conversions` table** — durable job records,
  walk-forward migration (new table, pure `CREATE TABLE IF NOT EXISTS`).
  - [x] 🟩 Bumped `CURRENT_SCHEMA_VERSION` 20 → 21; added the table + v20→v21
    walk-forward step (with the re-read after the v20 block). No `CHECK` on
    `status`.
  - **Verify:** ✅ `tests/test_db_schema_v21.py` — 4 passed; v18–v20 chain still
    green (15 passed).

- [x] 🟩 **Step 4: Repository CRUD** in `db/repository.py`.
  - [x] 🟩 `DocConversion` dataclass + `create_doc_conversion`,
    `fetch_doc_conversion`, `update_doc_conversion_progress`,
    `mark_doc_conversion_finished`, `is_doc_conversion_running` (serialise),
    `reconcile_stale_doc_conversions`.
  - **Verify:** ✅ `tests/test_doc_conversion_repo.py` — 3 passed; full state
    round-trip + stale reconcile (running + queued → failed, done untouched).

### Phase 3: Backend orchestration (endpoints + background worker) — 🟩 DONE

- [x] 🟩 **Step 5: Convert + status endpoints** — `docconvert/routes.py`
  (`register_doc_convert_routes`, mirrors reviewer_routes). `POST /api/doc-convert`
  (serialised, 409 if busy), `GET /{id}` (poll), `GET /{id}/events` (SSE),
  `GET /{id}/view` (path-confined HTML). Wired into server.py registration block.
- [x] 🟩 **Step 6: Background worker + SSE + terminal-status guarantee** —
  `docconvert/worker.py` daemon thread, own DB conn, per-page commits, wall-clock
  cap (`XBRL_DOC_CONVERT_TIMEOUT_S`), try/except landing every exit terminal.
  Startup `reconcile_stale_doc_conversions` added to `server._lifespan`.
  - **Verify:** ✅ `tests/test_doc_convert_worker.py` (4) + `test_doc_convert_routes.py`
    (6) — 10 passed: happy path, user-error, crash→failed (no raw leak), restart
    reconcile, upload→poll→view, non-pdf 400, serialise 409, 404s, view-409,
    SSE complete event.
  - **Note:** progress surfaced via a DB-tailing SSE endpoint (decoupled from the
    worker) rather than the run pipeline's shared `event_queue` — simpler and
    naturally durable for a standalone job. Within plan intent.

#### (original Step 5/6 detail retained below)

- [x] 🟩 **Step 5: Convert + status endpoints** — launch async, poll for result.
  - [ ] 🟥 `POST /api/doc-convert` (accepts an upload or an existing PDF ref) →
    creates a `queued` job, launches the worker on a dedicated thread with its
    own loop (the re-review thread pattern, gotcha #21), returns `{job_id,
    status:"queued"}` immediately. Serialize: if one is already `running`,
    queue rather than start a second (PRD decision).
  - [ ] 🟥 `GET /api/doc-convert/{job_id}` → status + progress + (when done) a
    link/handle to the HTML.
  - [ ] 🟥 `GET /api/doc-convert/{job_id}/view` → serves the stored HTML
    (path-confined under the output dir, like the trace endpoint, gotcha #6).
  - [ ] 🟥 Routes sit under `/api/*` so the auth middleware already gates them
    (gotcha #24) — no new auth work.
  - **Verify:** `tests/test_doc_convert_routes.py` (with `AUTH_MODE=dev`) posts a
    PDF, polls status to `done`, fetches the HTML, asserts the table content.

- [ ] 🟥 **Step 6: Background worker + SSE progress + terminal-status guarantee**
  — the worker that actually runs the conversion.
  - [ ] 🟥 Worker: `queued→running`, calls `convert_pdf_to_html` with a
    `progress_cb` that updates `current_page`/`total_pages` and emits SSE
    `doc_convert_progress` events; writes result HTML to disk; `running→done`.
  - [ ] 🟥 try/except/finally so **every** exit (success, exception, OOM,
    cancel, timeout) lands a terminal status — never stuck `running` (gotcha
    #10). Add a wall-clock cap (env-overridable) that fails the job.
  - [ ] 🟥 Startup hook (`server._lifespan`) calls `reconcile_stale_conversions`
    (gotcha #21 pattern).
  - **Verify:** `tests/test_doc_convert_worker.py` — happy path reaches `done`
    with progress events in order; a forced converter exception lands `failed`
    (not `running`); a simulated restart reconciles a stale `running` job.

### Phase 4: Word export — 🟩 DONE

- [x] 🟩 **Step 7: Download-as-Word endpoint** — `.docx` generated on demand.
  - [x] 🟩 `GET /api/doc-convert/{job_id}/download/docx` streams
    `<name>-readable.docx`; clean 500 on export failure (HTML view stays usable);
    409 before done; 404 unknown.
  - **Verify:** ✅ `tests/test_doc_convert_docx.py` — 3 passed; downloaded docx
    opens with python-docx and its table carries `3,141,738`.
  - **⚠️ DEVIATION (resolved by user):** Docling has **no** `.docx` exporter
    (HTML/MD/JSON/text only), so the PRD's "Docling built-in Word exporter" is
    impossible. **User chose pandoc.** Implemented via **`pypandoc_binary`** (the
    pandoc executable ships inside the pip wheel — no host `apt install`, so the
    offline-deploy story is preserved). Verified producing a real Word table with
    the figures intact. Isolated to `_html_to_docx_bytes`.

### Phase 5: Frontend (Readable Doc surface) — 🟩 DONE

- [x] 🟩 **Step 8: "Readable Doc" nav + page shell** — `AppView "doc-convert"` +
  `/readable-doc` route (appReducer + App.tsx URL sync) + TopNav item +
  `pages/ReadableDocPage.tsx`. Inline styles + pwc tokens.
- [x] 🟩 **Step 9: Progress + Readable View + copy** — POST then **poll** status
  (deviation: poll instead of consuming the SSE stream — simpler for a single
  standalone job; the SSE endpoint still exists). Progress bar "Converting page
  X of Y"; done renders the HTML in a sandboxed `<iframe>` (native select/copy).
- [x] 🟩 **Step 10: Download-as-Word button** — `<a download>` to the docx
  endpoint, shown only on `done`.
  - **Verify:** ✅ `web/src/__tests__/ReadableDocPage.test.tsx` (4) + typecheck
    clean + App/AppRouting (17) + **full frontend suite 721 passed**. Browser:
    page renders with correct PwC styling, no console errors, and a **real
    end-to-end conversion through the live backend** (upload→convert→view→docx)
    succeeded.

### Phase 6: Polish / edge cases / docs — 🟩 DONE (1 nice-to-have deferred)

- [x] 🟩 **Step 11: Error-state coverage** — `tests/test_doc_convert_errors.py`:
  corrupt PDF + password-protected PDF both raise a clear `DocConvertError`.
  (Empty/0-page PDF guard kept as defence but is unconstructable via PyMuPDF, so
  not unit-tested — noted in the test.)
- [ ] 🟥 **Step 12: (NICE TO HAVE) Side-by-side original scan** — **DEFERRED.**
  Needs a new PDF-page-image endpoint for the standalone job; out of MVP scope.
  Cleanly addable later.
- [x] 🟩 **Step 13: Docs + CLAUDE.md** — schema line → v21, v20→v21 migration
  bullet, **new gotcha #26** (offline Docling recipe + onnxruntime + pandoc docx
  + standalone/durable-job invariants), Deeper-References pointer.
  - **Verify:** ✅ all new backend (24) + frontend (721) + regression (77) green;
    CLAUDE.md reads v21. (ARCHITECTURE.md / SYNC-MATRIX.md edits left as optional
    follow-up — gotcha #26 already captures the load-bearing facts.)

---

## Peer-review hardening (2026-06-19)

Three findings from a second team-lead review, all addressed:

- **[HIGH→fixed] Unsandboxed iframe / user-derived HTML.** The converted HTML is
  now served with a restrictive **CSP** (`default-src 'none'; style-src
  'unsafe-inline'; img-src data:`) + `nosniff`, and rendered in a
  **`sandbox=""` iframe** (opaque origin, no scripts, no same-origin) — active
  content can neither run nor reach authenticated APIs. Pinned by
  `test_doc_convert_routes.py::test_view_sets_restrictive_csp` +
  `ReadableDocPage.test.tsx` (asserts `sandbox`).
- **[HIGH→recalibrated+fixed] Serialise race + orphaned file.** The race was not
  exploitable in the current single-worker async deployment (no `await` in the
  check→insert section), but the concrete **orphaned-file-on-409** was real. Now
  an atomic `repo.create_doc_conversion_if_idle` (`BEGIN IMMEDIATE`) does the
  check+insert **before** the upload bytes are written, so a 409 leaves nothing
  on disk and duplicate jobs can't be created even under multiple workers. Pinned
  by `test_create_if_idle_is_atomic_and_serialised` +
  `test_409_upload_leaves_no_orphan_file`.
- **[MEDIUM→fixed] Model resolution before PDF validation.** The converter now
  splits/validates the PDF **before** resolving the model bundle, so a
  bad/password PDF reports its own error even on a bundle-less machine — and the
  error tests are no longer coupled to the 599MB bundle. Pinned by
  `test_bad_pdf_error_precedes_missing_models` (passes with `model_dir` pointed
  at a non-existent path).

## Code-review hardening (2026-06-19, five-axis review)

One Important + four Suggestions, all applied:

- **[Important] docx download filename was unsanitized user input.** A non-ASCII
  filename (e.g. `财报.pdf`) would 500 the download (latin-1 header encode) and a
  `"` could break the header. Now `_safe_download_filename` emits an ASCII
  `filename=` + RFC 5987 `filename*` (UTF-8). Pinned by
  `test_download_docx_sanitizes_unsafe_filename`.
- **[Suggestion] All-pages-fail reported as `done`.** The converter now tracks a
  success count and raises `DocConvertError` if **zero** pages converted (a
  wholesale OCR/model failure → `failed`, not a wall of error markers). Pinned by
  `test_all_pages_failing_raises_not_silently_done`.
- **[Suggestion] Upload buffered before size check.** Added a `Content-Length`
  pre-check (413 before reading the body); the post-read length check stays as
  defence-in-depth.
- **[Suggestion] SSE reopened a DB connection every 0.5s.** Now one `db_session`
  for the whole stream (WAL still sees the worker's committed progress).
- **[Suggestion] `init_db` ran on every upload.** Removed — `_lifespan` already
  inits at startup; route tests init in their fixtures.

## Rollback Plan

If something goes badly wrong:

- **Feature is additive and isolated** — it adds new files
  (`docconvert/`, `scripts/fetch_docling_models.py`, new routes, new frontend
  page) and one new DB table. Reverting the feature commits removes it cleanly;
  no existing extraction code paths are modified.
- **Schema:** v21 only **adds** the `doc_conversions` table (no ALTER on
  existing tables), so a forward-migrated DB keeps working even if the feature
  code is reverted — the unused table is harmless. Do **not** write a
  down-migration (the project has no down-migrations by design).
- **Dependencies:** if `docling`/`torch`/`onnxruntime` cause install or memory
  trouble, revert the `requirements.txt` additions; nothing else depends on
  them. The bundled `models/` folder is gitignored, so there's nothing to
  un-commit.
- **What to check after a revert:** `python -m pytest tests/ -q` green, the app
  boots (schema reconcile no-ops with no `doc_conversions` rows), and the
  existing extraction flow is untouched.
- **Deferred-deploy safety:** because container-vs-zip is unresolved, do **not**
  ship this to Azure until the memory-sizing spike (PRD Deferred section) is
  done — a conversion + extraction on one undersized instance could OOM both.
