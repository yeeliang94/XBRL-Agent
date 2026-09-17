# Local PDF parser benchmark

This experiment lives in the application repository so both laptops share its
instructions and code. It does not import the application or change its extraction
pipeline, database, requirements, or templates. Delete this directory to remove it.

## What is implemented

- Separate environments and pinned top-level packages for pdfplumber, LiteParse,
  Docling and PaddleOCR. Full Windows/Python 3.12 locks for the two tested engines.
- Read-only PDF inventory with hashes, page counts and advisory text-layer classification.
- Six configurations: pdfplumber, LiteParse native, LiteParse Tesseract,
  LiteParse with a local OCR server, Docling with Tesseract CLI, Paddle PP-StructureV3.
- Import probes, timeouts, per-document execution, raw and normalized output,
  dependency inventories, text/table scoring, HTML reports and cross-run comparison.
- All parser/model dependencies, corpus files and outputs are isolated here.

Native pdfplumber and LiteParse were smoke-tested on a synthetic table PDF on
Windows x64/Python 3.12.14. Both completed; both correctly produced `partial`
on the image-only version with OCR disabled. This is execution evidence, not a
claim about real financial-statement accuracy.

Docling, Paddle, Tesseract OCR and the external OCR integration require target-machine
validation. Their top-level package versions are pinned but their transitive
dependencies are not yet locked. The local OCR-server configuration is a client
integration, not a bundled RapidOCR server. It requires a separately installed,
locally bound server implementing the LiteParse OCR API. No cloud service is used.

## Windows quick start

Run from the repository root in PowerShell. The repository `venv` bootstraps setup;
the actual parsers run in `experiments/parser_bench/.venvs/<engine>`. Use a supported
Python version for all environments, preferably Python 3.12 x64 for these locks.
If the root venv is missing, follow root AGENTS.md (`start.bat`). These examples
do not change PowerShell execution policy and need no activation script.

```powershell
$env:PYTHONUTF8 = '1'
venv\Scripts\python.exe experiments/parser_bench/bench.py setup pdfplumber --lock experiments/parser_bench/requirements/windows-py312-pdfplumber.lock.txt
venv\Scripts\python.exe experiments/parser_bench/bench.py setup liteparse --lock experiments/parser_bench/requirements/windows-py312-liteparse.lock.txt
venv\Scripts\python.exe experiments/parser_bench/bench.py doctor
```

Setup makes network requests for packages unless `--wheelhouse` is supplied.
It refuses to overwrite an existing environment. On this laptop those two
environments already exist; skip their setup. A failed installation is retained
with its logs. Inspect it before repairing it using that environment's interpreter.
`doctor` reports missing optional environments and returns nonzero; this is expected
until all requested environments exist. Use `doctor --engines pdfplumber,liteparse`
to probe only the initial pair. Import success does not initialize models or prove
that extraction works.

Put approved source PDFs under `experiments/parser_bench/documents/`, optionally
using `native/`, `scanned/`, `mixed/` or `unsorted/`. Subfolders are discovered
automatically. Paths are relative to this corpus root. Classification uses native
text quantity only; it cannot identify a bad pre-existing OCR layer reliably.

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py inventory
venv\Scripts\python.exe experiments/parser_bench/bench.py run --machine-label enterprise --timeout 600
```

Open the printed `report.html`. The default run uses only the two native-text
configurations. Native extractors will often produce empty text for scans, which
is reported as `partial`, not successful extraction. Blank pages are also flagged
for review, not assumed to be extraction failures or removed from coverage.

To test only a selected corpus, inventory it with `--documents <folder> --out
<manifest.json>` and pass that same folder and manifest to `run`. Inventory replaces
the specified manifest; copy it first if you need to retain an earlier selection.
Each run freezes its manifest and configuration. It verifies each source hash
before processing; changed PDFs require a new inventory. Original files are read-only.

## OCR and structured parsers

After authorization for the target machine's package/model downloads:

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py setup docling
venv\Scripts\python.exe experiments/parser_bench/bench.py setup paddle
venv\Scripts\python.exe experiments/parser_bench/bench.py run --engines liteparse-tesseract,docling,paddle-structure --allow-model-init --machine-label enterprise --timeout 1200
```

`--allow-model-init` explicitly permits model initialization, which may download
weights. It is deliberately separate from package installation. LiteParse bundles
Tesseract but may still need language data. **Docling uses a separate Tesseract CLI
installation**, with English language data on PATH; its layout/table models must
also be present. A missing executable should remain a recorded failure until an
approved installation is possible. Paddle selects CPU and disables orientation,
unwarping and formula modules in the checked-in configuration.

To supply local models, copy `configs/engines.json` to ignored `configs/local.json`,
edit the engine's `options`, and pass `--config experiments/parser_bench/configs/local.json`.
Examples: LiteParse `tessdata_path`; Docling `artifacts_path`; Paddle's documented
per-module `*_model_dir` options. Check each installed version's API before editing.
Record English versus multilingual model choices. The worker sets local HF and
PaddleX cache locations; engine-specific caches may still need explicit paths.
Docling remote services are disabled. OCR-server URLs must use localhost.

For an existing approved RapidOCR server bound to `127.0.0.1:8000`, implementing
`POST /ocr` with the documented multipart image request and JSON response:

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py run --engines liteparse-rapidocr --allow-model-init --local-ocr-server-ready
```

The flags do not start or test the server. Save its exact package lock, model names,
weight hashes and startup configuration with the run. That service's warm-model
timing is not equivalent to a fresh standalone parser's timing.

## Accurate, reproducible scoring

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py gold-template
```

This creates ignored `gold/local/<full-document-sha256>.json` files without replacing
existing answers. Fill selected pages manually, for example:

```json
{
  "sha256": "COPY THE FULL HASH FROM THE INVENTORY",
  "verified": false,
  "verified_by": "",
  "pages": [{
    "page": 3,
    "text": "The complete text of this page in reading order",
    "cells": [
      {"table": 0, "row": 0, "column": 1, "text": "Group 2025"},
      {"table": 0, "row": 1, "column": 1, "text": "(1,250)"}
    ]
  }]
}
```

Page numbers are physical PDF pages, starting at 1, not printed page labels. Table,
row and column indices start at 0. Include header rows in the grid. Table order is
source order on each page. For merged cells, repeat their content across all covered
grid positions; Paddle output also retains the spans and original HTML. Verify
coordinates against raw output and source before scoring; unsupported structures
are explicit, not silently inferred by an LLM.

Only set `verified: true` and fill `verified_by` after a human checks the answers
against the source. Agents can prepare drafts but cannot certify their own outputs.
For a cell-only reference, omit `text`. For text scoring provide the complete selected
page, not a short excerpt. Do not manually correct parser outputs before scoring.

Metrics currently implemented:

- Character and word error rates: exact edit distance after whitespace normalization.
  Case, punctuation, numbers and signs are preserved; lower is better. Error rates
  can exceed 1 when there are many insertions. Missing pages incur deletion errors.
- Positional cell exact accuracy: matched annotated cells / annotated cells. A correct
  number in the wrong column fails. Blank, missing, dash and zero remain different.
- Page coverage, empty pages, execution status and elapsed time are independent of
  accuracy. An unverified/missing reference never yields an accuracy percentage.

This initial implementation does **not** compute TEDS/GriTS, table detection precision/
recall, numerical semantic equivalence, entity/period facts, peak memory, or AI-agent
cost/accuracy. Headers and selected numeric cells can be evaluated positionally now.
Use a separate held-out document set after tuning; do not extrapolate selected-page
scores to the whole corpus. A future agent-input trial needs its own authorized model
runs and fixed prompts. This experiment does not connect to the application's agents.

## Enterprise reproducibility and offline evidence

Keep these stages distinct: installation, import, model availability, extraction,
and offline execution. Setup/run logs preserve the failing stage. Errors are initially
`runtime_failed`; an agent should inspect the log to identify download/proxy/DLL/model
causes, rather than classify all failures as blocked downloads. Do not disable TLS.

For an approved offline installation, download wheels on a compatible Windows/Python
machine with the environment's `pip download -r <lock> -d <wheel-directory>`, then use
`setup <engine> --lock <lock> --wheelhouse <wheel-directory>` on a clean target.
The wheelhouse option uses `--no-index`; model files and native executables are separate.
Do not copy virtual environments. Record model files with:

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py models-manifest
```

Only after network access is independently blocked, rerun in a fresh process with
`--network-condition externally-blocked`. This is an operator declaration, **not** a
firewall implementation or proof by the harness. Record how blocking was established.
Cached/normal-network success alone must not be described as offline verification.

Copy approved PDFs and reference files to the Enterprise laptop separately: Git ignores
them. Confirm the same full hashes and gold hashes. If confidential files cannot leave
Enterprise, test them only there and label them as a separate corpus. Reports contain
source text, local paths and machine labels, so handle them like the source documents.

Each run saves configs, source hashes, gold hashes, Python/package versions, machine
information, stdout/stderr and raw outputs. Timings include fresh-process engine loading;
repeat runs to examine cache effects. Failures stay in the results and cause exit code 1.
Usage/configuration errors return 2. Existing result folders are never overwritten.

To compare two approved run folders collected on one machine:

```powershell
venv\Scripts\python.exe experiments/parser_bench/bench.py compare <personal-run-folder> <enterprise-run-folder>
```

Comparison joins by source hash and engine, keeps missing results, and exposes whether
gold/config match. Also inspect the saved package inventories and model manifests.
It produces JSON, not an unsupported overall ranking. HTML reports link to original
PDF pages and escaped extracted text; source links may need opening in a local browser.

## Instructions to give the Enterprise agent

After sharing these repository changes and placing approved PDFs in the local corpus,
paste this instruction into the agent running on the Enterprise laptop:

> Read root AGENTS.md and experiments/parser_bench/AGENTS.md and README.md. Work only
> on the isolated parser experiment. Inspect git status and preserve existing changes.
> Check Python/runtime availability. Set up the pdfplumber and LiteParse environments
> using the supplied Windows/Python 3.12 locks when compatible. Do not overwrite an
> existing environment. Run doctor, inventory my local documents, and benchmark the
> native parsers with machine label enterprise. Save and explain installation, import,
> extraction and timeout failures separately. Create unverified gold templates and
> identify representative text/table pages for my review. Do not claim accuracy without
> verified references. For OCR/model downloads, establish authorization and the permitted
> installation method on this machine before initializing models. Then test LiteParse
> Tesseract, Docling and Paddle PP-StructureV3 individually. Treat the RapidOCR client
> configuration as conditional on an approved local server. Preserve package locks,
> model hashes and raw outputs. Do not bypass enterprise controls or upload documents.
> Claim offline success only after independently blocked-network testing. Run the focused
> benchmark tests after fixes and report what ran, what failed, and what remains untested.

If downloads have already been authorized for that machine, state that in the same
message; the agent should not ask again. Existing run reports on this laptop are local
and will not travel with Git. Root working plans remain under docs/local.

## Validation and upstream references

```powershell
venv\Scripts\python.exe -m pytest experiments/parser_bench/tests -q
```

The tests use synthetic data. No application pinning contract was changed, so its
backend suite/frontend build are outside this experiment's validation scope.

- [LiteParse Python API](https://github.com/run-llama/liteparse/tree/main/packages/python)
- [LiteParse OCR protocol](https://github.com/run-llama/liteparse/blob/main/OCR_API_SPEC.md)
- [pdfplumber](https://github.com/jsvine/pdfplumber)
- [Docling local artifacts](https://docling-project.github.io/docling/usage/advanced_options/)
- [Paddle PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html)

Package availability was checked on PyPI on 2026-09-17. Availability is not proof
that a wheel, native dependency or model download works under enterprise controls.
