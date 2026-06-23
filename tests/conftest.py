import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
