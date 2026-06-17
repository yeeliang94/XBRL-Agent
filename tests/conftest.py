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
