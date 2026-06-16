"""Guards for ``utils.paths.validate_session_id`` and the route handlers that
use it.

Code-review fix: URL path params (``/api/scout/{session_id}``,
``/api/run/{session_id}``, ``/api/rerun/{session_id}``) are joined to
``OUTPUT_DIR``. A crafted ``session_id`` containing ``..`` or a separator could
otherwise escape the output tree. The validator rejects anything that is not a
single safe path segment; handlers map the failure onto HTTP 400.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from utils.paths import validate_session_id


@pytest.mark.parametrize(
    "good",
    [
        "test-session",
        "sess-a",
        "550e8400-e29b-41d4-a716-446655440000",  # a real UUID4
        "test-abc.123",
        "run_001",
    ],
)
def test_safe_segments_pass(good):
    assert validate_session_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        ".",
        "..",
        "../etc/passwd",
        "a/b",
        "foo/../bar",
        "/etc/passwd",
        "sub\\dir",  # backslash separator (Windows)
    ],
)
def test_traversal_and_separators_rejected(bad):
    with pytest.raises(ValueError):
        validate_session_id(bad)


def test_non_string_rejected():
    with pytest.raises(ValueError):
        validate_session_id(None)  # type: ignore[arg-type]


def test_scout_endpoint_rejects_traversal_session_id(tmp_path, monkeypatch):
    """A path-traversal session_id is refused with 400 before any path join."""
    import server

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    client = TestClient(server.app)
    # A backslash-containing id is a single URL segment (so it reaches the
    # handler rather than being normalized to a 404/405), but is not a safe path
    # segment — the validator must refuse it with 400 before any path join.
    resp = client.post("/api/scout/sub\\dir")
    assert resp.status_code == 400
