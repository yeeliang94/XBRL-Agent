# Baseline evidence bundle — Pydantic AI V2 upgrade (Phase U0)

Captured 2026-07-12 on branch `chore/pydantic-ai-v2` (forked from `main`
@ `74635f4`). U3 compares against this bundle
(docs/PLAN-pydantic-ai-v2.md, Phase U0/U3).

## Environment (authoritative interpreter: `./venv/bin/python`)

- Python **3.12.13**; `pip check`: no broken requirements.
- pydantic-ai **1.77.0** (slim/evals/graph all 1.77.0), pydantic 2.12.5.
- Full resolved graph: `constraints.txt` (252 packages, committed at U0).
- Known footgun: bare system `python3` is a stale 3.9 with pydantic-ai
  0.8.1 — never use it for version reads or tests.

## Test baseline (post-U0 renames)

- Backend: `./venv/bin/python -m pytest tests/ -n auto` →
  **3304 passed, 3 skipped** (56s).
- Frontend: `cd web && npx vitest run` → **1062 passed** (72 files),
  captured same day on the identical tree pre-branch (`74635f4`).

## Benchmark scorecard

**Not captured at U0** — requires a live extraction run against a saved
gold benchmark (API key + wall-clock). Capture at the U1 gate's live
smoke run, or at latest before U2, so the U3 before/after comparison has
a pre-flip anchor. Recorded here as an open item rather than silently
skipped.

## U0 changes included in this baseline

- Silent-zero fix: `notes/coordinator.py` cost backfill now reads
  `input_tokens`/`output_tokens` first (old names only as fallback).
- Old-name direct reads converted to the 1.x primary names in
  `coordinator.py`, `scout/agent.py`, `notes/coordinator.py`,
  `notes/listofnotes_subcoordinator.py`.
- 9 test files' usage stubs renamed to the new field names.
- `requirements.txt` header documents the constraints-file install.
