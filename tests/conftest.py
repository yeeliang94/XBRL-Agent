import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# This must run at conftest import time, before pytest imports any test module
# that imports ``server``. A fixture is too late: server applies local settings
# during module import, so a developer's real output/settings.json would have
# already polluted os.environ before the fixture could redirect the path.
_TEST_SETTINGS_ROOT = Path(tempfile.mkdtemp(prefix="xbrl-agent-test-settings-"))
os.environ["XBRL_SETTINGS_FILE"] = str(_TEST_SETTINGS_ROOT / "settings.json")


@pytest.fixture(autouse=True)
def _auth_dev_mode_by_default():
    """Default the whole suite into AUTH_MODE=dev so the auth middleware (which
    guards every /api/* route) bypasses for tests that predate the auth layer
    and don't log in.

    Set via os.environ (not the function-scoped `monkeypatch`) on purpose: some
    tests call `monkeypatch.undo()` mid-body, which would otherwise wipe this
    default and make their *next* API call hit the gate and 401. Auth-specific
    tests opt OUT with `monkeypatch.delenv("AUTH_MODE")` (and set
    WEBSITE_SITE_NAME where they exercise production), so this never masks the
    gate in the tests that actually verify it.
    """
    prior = os.environ.get("AUTH_MODE")
    os.environ["AUTH_MODE"] = "dev"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = prior


@pytest.fixture(autouse=True)
def _notes_auto_review_off_by_default():
    """Default the notes reviewer's auto-launch OFF for the suite.

    The notes reviewer auto-fires on any prose-notes run; leaving it on (the
    production default) would add an extra NOTES agent/pass + audit row to every
    mocked notes run and break the exact pipeline counts. Tests that exercise it
    opt IN with `monkeypatch.setenv("XBRL_NOTES_AUTO_REVIEW", "true")`; the
    settings round-trip test `delenv`s to verify the true default is ON. Set via
    os.environ for monkeypatch.undo() resilience (like XBRL_SPOT_CHECK above).
    """
    prior = os.environ.get("XBRL_NOTES_AUTO_REVIEW")
    os.environ["XBRL_NOTES_AUTO_REVIEW"] = "false"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("XBRL_NOTES_AUTO_REVIEW", None)
        else:
            os.environ["XBRL_NOTES_AUTO_REVIEW"] = prior


@pytest.fixture(autouse=True)
def _pdf_notes_auto_format_off_by_default():
    """Keep paid PDF formatter passes opt-in in pipeline tests."""
    prior = os.environ.get("XBRL_PDF_NOTES_AUTO_FORMAT")
    os.environ["XBRL_PDF_NOTES_AUTO_FORMAT"] = "false"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("XBRL_PDF_NOTES_AUTO_FORMAT", None)
        else:
            os.environ["XBRL_PDF_NOTES_AUTO_FORMAT"] = prior


@pytest.fixture(autouse=True)
def _isolate_env_file_from_repo_dotenv(tmp_path_factory, monkeypatch):
    """Point ``server.ENV_FILE`` at an empty temp file for every test.

    The run path (`api/run_control.py`), scout, reviewer, and settings endpoints
    all reload deployment and local runtime settings on each invocation.
    With ``override=True`` that re-reads the developer's real repo ``.env`` and
    **clobbers** the env-var defaults the autouse fixtures above set — e.g. a
    local ``.env`` carrying ``XBRL_SPOT_CHECK='true'`` re-enables the spot-check
    mid-run and adds an unexpected CORRECTION agent, breaking the deterministic
    pipeline-count tests (the failure only shows up on a machine that HAS a
    populated ``.env``; CI has none, which is why it hid).

    Redirecting ENV_FILE to an empty file makes that `load_dotenv` a no-op, so
    the conftest env defaults hold regardless of the developer's `.env`. Tests
    that need their own env file (e.g. ``test_settings_api`` / ``test_server_scout``
    / ``test_server_run_lifecycle``) still set ``server.ENV_FILE`` themselves —
    those `monkeypatch.setattr` calls run after this autouse fixture and win.
    """
    empty_env = tmp_path_factory.mktemp("env") / ".env-test"
    empty_env.write_text("", encoding="utf-8")
    settings_file = empty_env.with_name("settings-test.json")
    import server
    monkeypatch.setattr(server, "ENV_FILE", empty_env)
    monkeypatch.setattr(server, "SETTINGS_FILE", settings_file)
    # The CLI (`run.py`) loads its own hard-pathed copy with override=True too.
    import run
    monkeypatch.setattr(run, "ENV_FILE", empty_env)
    yield


@pytest.fixture(autouse=True)
def _spot_check_off_by_default():
    """Default the clean-run spot-check (issue 1) OFF for the suite.

    The spot-check fires a reviewer pass on a run with NO failing checks —
    which is exactly the shape of the deterministic full-pipeline tests, so
    leaving it on (the production default) would add an extra CORRECTION
    agent/event to every clean mocked run and break their exact counts. Tests
    that exercise the spot-check opt IN with `monkeypatch.setenv` (the trigger
    test) or call `_run_reviewer_pass(spot_check=...)` directly; the settings
    round-trip test `delenv`s this to verify the true default is ON. Set via
    os.environ for the same monkeypatch.undo() resilience as AUTH_MODE above.
    """
    prior = os.environ.get("XBRL_SPOT_CHECK")
    os.environ["XBRL_SPOT_CHECK"] = "false"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("XBRL_SPOT_CHECK", None)
        else:
            os.environ["XBRL_SPOT_CHECK"] = prior


@pytest.fixture(autouse=True)
def _notes_coverage_off_by_default():
    """Default the notes coverage checklist (PLAN-notes-coverage-and-routing)
    OFF for the suite.

    Coverage runs inside the notes reviewer pass and can tip an otherwise-clean
    mocked run to `completed_with_errors` (a missing inventory note / suspected
    gap), which would perturb the deterministic full-pipeline count/status
    tests. Coverage-specific tests opt IN with
    `monkeypatch.setenv("XBRL_NOTES_COVERAGE", "true")`; the settings round-trip
    test `delenv`s this to verify the true default is ON. Set via os.environ for
    the same monkeypatch.undo() resilience as the fixtures above.
    """
    prior = os.environ.get("XBRL_NOTES_COVERAGE")
    os.environ["XBRL_NOTES_COVERAGE"] = "false"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("XBRL_NOTES_COVERAGE", None)
        else:
            os.environ["XBRL_NOTES_COVERAGE"] = prior


@pytest.fixture(autouse=True)
def _pdf_sidecar_off_by_default():
    """Force the scanned-PDF transcribed sidecar (PLAN-pdf-source-sidecar)
    OFF for the suite.

    The true default is already off, but a developer who flipped the Settings
    toggle carries ``XBRL_PDF_SIDECAR='true'`` in .env (loaded at server
    import), and the run generator would then attempt a transcription pass in
    every mocked pipeline test. Sidecar tests opt IN with
    ``monkeypatch.setenv("XBRL_PDF_SIDECAR", "true")``; the settings
    round-trip test ``delenv``s this to verify the true default is OFF.
    """
    prior = os.environ.get("XBRL_PDF_SIDECAR")
    os.environ["XBRL_PDF_SIDECAR"] = "false"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("XBRL_PDF_SIDECAR", None)
        else:
            os.environ["XBRL_PDF_SIDECAR"] = prior
