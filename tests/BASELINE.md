# Test Suite Baseline

Captured: 2026-04-06 (start of Multi-Statement Rollout, phase 0)

This file freezes the known pass/fail state at the beginning of the multi-statement
rollout so we can detect regressions. Re-run the two commands below at the end of
each phase and diff against this list.

Commands:

```bash
# Backend
python -m pytest tests/ -v

# Frontend
cd web && npx vitest run
```

## Backend (pytest): 70 passed, 2 failed (72 total)

### Passing (70)
- tests/test_download_api.py::test_download_filled_excel
- tests/test_download_api.py::test_download_result_json
- tests/test_download_api.py::test_download_missing_file_returns_404
- tests/test_e2e.py::test_full_extraction_flow
- tests/test_integration.py::test_integration_mocks_vision_fills_workbook
- tests/test_integration.py::test_integration_token_tracking
- tests/test_integration.py::test_integration_full_flow
- tests/test_pdf_viewer.py::test_count_pdf_pages
- tests/test_pdf_viewer.py::test_render_single_page
- tests/test_pdf_viewer.py::test_render_page_range
- tests/test_pdf_viewer.py::test_render_all_pages
- tests/test_pdf_viewer.py::test_page_images_are_png
- tests/test_pdf_viewer.py::test_invalid_page_range_raises
- tests/test_run_numbering.py (3 tests)
- tests/test_settings_api.py (3 tests)
- tests/test_sse_api.py (3 tests)
- tests/test_startup_config.py::test_requirements_txt_has_deps
- tests/test_startup_config.py::test_start_sh_is_executable
- tests/test_startup_config.py::test_start_bat_exists
- tests/test_template_reader.py (10 tests)
- tests/test_token_tracker.py (4 tests)
- tests/test_upload_api.py (3 tests)
- tests/test_verifier.py (14 tests)
- tests/test_workbook_filler.py (15 tests)

### Failing (2) — pre-existing, NOT caused by this rollout
- tests/test_startup_config.py::test_env_example_exists
- tests/test_startup_config.py::test_env_example_has_required_keys

**Reason:** `.env.example` was deleted prior to this rollout (see `git status`:
`D .env.example`). These two tests will remain red until the file is restored.
Not a regression target for this project.

## Frontend (vitest): 83 passed (13 files)

All 83 tests across 13 files pass.
