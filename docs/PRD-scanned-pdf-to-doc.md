# Scanned PDF → Readable Document — PRD

> Status: **Draft for approval** · Created 2026-06-19 · Owner: William Chen
> This document is the single source of truth for the build. It is written for
> a non-technical reader; technical terms are explained in (parentheses).

---

## Overview

- **Problem:** Many financial-statement PDFs that users upload are **scanned
  images** (a photo/scan of paper, not real text). You cannot select, search,
  or copy anything from them — to read a figure you have to squint at the image
  and re-type it by hand. This is slow, error-prone, and frustrating.

- **Solution:** A standalone feature that converts a scanned PDF into a clean,
  **readable, copy-pasteable document** — viewable inside the app and
  downloadable as a Word file — that closely mirrors the original (headings,
  paragraphs, and **real tables with aligned numbers**).

- **Target User:** The accountants/reviewers already using the XBRL Agent web
  app, who receive scanned audited financial statements and want to read or
  lift text/figures without retyping.

- **Success Criteria (how we know it works):**
  1. For a scanned financial-statement page, **every figure and its row/column
     position is reproduced correctly** in the output table (verified by
     eyeballing against the scan — our bake-off achieved this on the FINCO
     Statement of Financial Position).
  2. A user can **select and copy a column of numbers** from the in-app view
     (or the downloaded Word file) and paste it into Excel/Word with alignment
     preserved.
  3. The conversion runs **fully offline** on the Azure server — **no external
     API calls, no internet fetches at runtime** (proven in the offline spike).

---

## User Stories

| # | Story | Priority |
|---|-------|----------|
| 1 | As a reviewer, I want to **convert a scanned PDF into a readable in-app view** so that I can read it without squinting at the image. | **MUST HAVE** |
| 2 | As a reviewer, I want to **copy text/numbers (including whole table columns)** from the readable view so that I don't have to retype them. | **MUST HAVE** |
| 3 | As a reviewer, I want to **download the converted document as a Word (.docx) file** so that I can edit or share it offline. | **MUST HAVE** |
| 4 | As a reviewer, I want the conversion to **run in the background with clear progress** so that I'm not stuck staring at a frozen screen on a long document. | **MUST HAVE** |
| 5 | As a reviewer, I want to view the **converted page next to the original scan** so that I can sanity-check fidelity. | NICE TO HAVE |

This feature is **completely separate from the XBRL extraction pipeline** — it
does not read, write, or depend on any extraction run, concept facts, or
templates. It is a self-contained "document reader" utility.

---

## Detailed User Flows

### Flow 1 + 2 + 4 (MUST HAVE) — Convert a scanned PDF and read/copy it

- **Trigger:** The user opens the new **"Readable Doc"** area of the app and
  uploads a PDF (or picks one they already uploaded), then clicks **Convert**.

- **Steps & inputs/responses:**
  1. **User input:** Uploads a PDF file (or selects an existing upload).
  2. **System response:** Saves the file, creates a **conversion job** record
     (status `queued`), and immediately returns a job id. The screen shows
     "Conversion queued…". The web request returns right away — it does **not**
     wait for the conversion (which can take minutes).
  3. **System response (background):** A background worker picks up the job and
     runs the converter (Docling — see Technical Approach) **page by page**.
     - Status moves `queued → running`.
     - After each page, it emits a progress update (e.g. "Converting page 4 of
       37"). The front-end shows a live progress bar (reusing the app's
       existing live-update mechanism — Server-Sent Events / SSE).
  4. **System response:** When finished, the worker saves the result as
     **HTML** (a web page with real headings and tables) into the job record,
     sets status to `done`, and emits a "complete" update.
  5. **Output:** The front-end renders the **Readable View** — a clean page
     with selectable text and real tables. The user can:
     - Read the document normally.
     - **Select and copy** any text or table cells; pasting into Excel/Word
       keeps the table columns aligned (we already have clipboard-decoration
       logic for tables — see gotcha #16 — that can be reused).

- **Error States:**
  - **Not a scanned PDF / already has text:** Still converts fine (Docling
    handles both image and text PDFs). No error.
  - **Corrupt or password-protected PDF:** Job ends `failed` with a plain
    message ("We couldn't open this PDF — it may be corrupted or password
    protected."). Nothing else breaks.
  - **Conversion crashes mid-run (e.g. out of memory):** Job ends `failed`
    with "Conversion failed — try a smaller file or contact support." The
    failure is captured in the job record; it never leaves the job stuck on
    `running` (mirrors the extraction pipeline's terminal-status guarantee,
    gotcha #10).
  - **Server restarts mid-job:** Any job left `running` by a dead process is
    reconciled to `failed` on startup so the screen resolves (mirrors the
    re-review reconcile pattern, gotcha #21).
  - **Conversion takes too long:** A wall-clock cap (e.g. N minutes per
    document) ends the job `failed` rather than running forever.

### Flow 3 (MUST HAVE) — Download as Word

- **Trigger:** From a `done` Readable View, the user clicks **"Download as
  Word."**
- **Steps:**
  1. **User input:** Clicks the button.
  2. **System response:** The server converts the stored HTML into a **.docx**
     file (using `pandoc`, a standard document-conversion tool, or Docling's
     own Word export) at download time.
  3. **Output:** The browser downloads `<original-filename>-readable.docx`,
     which opens in Microsoft Word with headings, paragraphs, and editable
     tables.
- **Error States:** If Word generation fails, the user still has the in-app
  Readable View; the button shows "Word export failed — the readable view is
  still available."

### Flow 5 (NICE TO HAVE) — Side-by-side with the original scan

- **Trigger:** A toggle in the Readable View ("Show original").
- **Output:** The original scanned page image renders next to the converted
  text for the same page, so the user can verify figures at a glance. (The app
  already renders PDF page images via PyMuPDF, so the building block exists.)

---

## Technical Approach

- **Stack & why:**
  - **Docling** (open-source document converter by IBM Research) is the
    conversion engine. In a head-to-head bake-off on a real scanned FINCO
    Statement of Financial Position, Docling reproduced the **entire table
    correctly** — note numbers, current/prior-year columns, every figure — as a
    real table. The lighter alternative the team considered, **LiteParse
    v2.1.1**, collapsed the columns and garbled the note column on the same
    scanned page **regardless of settings**, because it only rebuilds tables
    from a PDF's *native* text grid (which scans don't have). Docling wins
    because it runs a dedicated **table-structure model** on top of OCR
    (OCR = optical character recognition, "reading" text out of an image).
  - **Output format = HTML first, Word on demand.** HTML renders directly in
    the existing web UI as the Readable View and gives the best copy-paste of
    table cells; the Word file is generated from that HTML only when requested.
  - **Runs as a background job**, never inside the web request — conversions
    take seconds-per-page (≈5–12s) and a 37-page document is several minutes,
    well past Azure App Service's ~230-second request limit. We reuse the app's
    existing background-thread + SSE progress pattern (the re-review pass).

- **Key Dependencies:**
  - `docling` + `docling-ibm-models` (engine + models).
  - **CPU-only `torch`** (the math library Docling's models run on — we must
    install the CPU build, ~430MB, **not** the ~2GB GPU build).
  - `onnxruntime` (required to run the bundled OCR models — discovered in the
    spike; must be in `requirements.txt`).
  - `rapidocr` (the OCR component, bundled with Docling).
  - `pandoc` **or** Docling's Word exporter for the .docx download.
  - **Pre-downloaded model weights (~599MB)** bundled into the deployment — see
    Data Model and the offline recipe below.

- **Offline model recipe (proven in the spike — no runtime internet):**
  1. Pre-download weights once with Docling's `download_models(...)` into a
     `models/` folder (~599MB: table model 342MB + layout model 164MB + OCR
     93MB) and ship that folder with the deploy.
  2. At runtime, point Docling at that folder (`artifacts_path`) and point the
     OCR at the bundled **.onnx** model files.
  3. Set `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` so it never reaches out
     to the internet. Verified working with **all network physically blocked**.

- **Deployment (Azure App Service):**
  - Current production is a **Linux App Service, B2 tier (3.5GB RAM), single
    instance, zip-deploy**. The added footprint (~630MB libraries + 599MB
    weights ≈ **1.2GB**) is awkward for the current zip/Oryx build, so the
    recommendation is to **switch this app to a container image** (bundling
    libraries + weights cleanly) and **bump the plan to P1v3 (8GB RAM)**.
  - **Memory is the live constraint to validate:** loading the table + layout
    models spikes ~1.5–2GB; running a conversion *alongside* an extraction on a
    single 3.5GB instance risks running out of memory. The pre-build spike
    (see Open Questions) measures this on the real container.

- **Data Model (plain terms):** One new **conversion job** record per
  conversion, holding:
  - a job id, the source PDF reference, a **status** (`queued` / `running` /
    `done` / `failed`), progress (current page / total pages), the **converted
    HTML** result, an optional error message, and timestamps.
  - The model weights are **static files shipped with the app**, not data in
    the database.
  - This is entirely separate from the extraction tables (`runs`,
    `run_concept_facts`, etc.).

---

## Scope Boundaries

- **In Scope (v1):**
  - Upload/select a PDF and convert it to a readable in-app HTML view.
  - Correct reconstruction of **headings, paragraphs, and tables** (the core
    value for financial statements).
  - Copy/paste of text and table cells with alignment preserved.
  - Download as **Word (.docx)**.
  - Background processing with live progress and robust terminal states.
  - Fully **offline / no-API** operation with bundled model weights.

- **Out of Scope (not building yet):**
  - A **searchable PDF** output (explicitly not needed).
  - Feeding the converted output **into the XBRL extraction pipeline** (this is
    a standalone reader; any future link is a separate project).
  - **Handwriting**, charts/figures, photographs, or non-financial complex
    layouts beyond best-effort.
  - **Editing** the converted document inside the app (the Word download covers
    editing).
  - Language support beyond **English** (Malaysian FS are English; the OCR
    models can be extended later).
  - GPU acceleration.

- **Known Limitations (v1 won't be perfect):**
  - OCR is not 100% — occasional character mistakes are possible on poor-quality
    scans; the side-by-side view (Flow 5) helps users catch them.
  - Conversion is **CPU-only and not instant** (~5–12s per page); long
    documents take minutes (mitigated by background processing + progress).
  - Visual layout is **semantic, not pixel-perfect** — the output is a clean
    re-flow (correct structure and content), not a photographic replica of the
    original page.

---

## Decisions (resolved 2026-06-19)

These were open questions; the team accepted the recommended defaults so the
build can proceed:

1. **Word export tool → Docling's built-in Word exporter.** One fewer
   dependency than bundling `pandoc`; acceptable styling control for v1.
2. **Concurrency → serialize to one conversion at a time** on the single
   instance (simplest and safest for memory). Additional requests queue.
3. **Retention → align with the app's existing upload retention.** Converted
   results and source PDFs follow whatever lifecycle uploads already use; no
   new separate retention policy in v1.
4. **UI entry point → a new top-nav item ("Readable Doc").** It's a standalone
   utility, so it gets its own surface rather than being buried in the
   extraction upload screen.
5. **Multi-page tables → page-by-page for v1.** The converter does not stitch a
   table that spans pages into one; each page converts independently. Stitching
   is a possible later enhancement.

## Deferred — Azure deployment caveats (decide at deploy time)

These do **not** block building the feature; they only matter when we actually
ship it to Azure. Captured here so they aren't lost:

- **Container vs zip-deploy + tier.** Recommendation stands: **container image +
  P1v3 (8GB RAM)** to absorb the ~1.2GB of libraries + weights cleanly and
  guarantee offline operation. **Not decided yet.** If we instead stay on the
  current **zip-deploy / B2** path, we must pre-bundle the 599MB weights into the
  deploy artifact and accept tighter memory headroom + Oryx build-size risk.
  Either way the *application code* is the same — this is purely a packaging /
  hosting choice.
- **Memory-sizing spike.** Before the first real deploy, measure **peak memory
  when a conversion runs alongside an extraction** on the chosen tier/instance,
  to confirm sizing (the model load spikes ~1.5–2GB on a single instance).
- **`requirements.txt` additions** (`docling`, CPU-only `torch`, `onnxruntime`,
  `rapidocr`) and the bundled `models/` folder are needed for any hosting path.

## Open Questions

_None blocking. All v1 decisions resolved above; remaining items are deferred
Azure-deployment caveats, not build blockers._

---

### Appendix — Bake-off evidence (2026-06-19)

Converted the FINCO Statement of Financial Position (a scanned page) with both
engines. Docling produced a correct, copyable table (all figures matched the
scan); LiteParse collapsed the columns. Offline conversion verified with all
network blocked at ~4.7s/page from a 599MB local model bundle. Raw outputs were
produced under `/tmp/bakeoff/` during the spike (ephemeral).
