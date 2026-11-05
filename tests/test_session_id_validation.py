"""Guards for ``utils.paths.validate_session_id`` and the route handlers that
use it.

Code-review fix: URL path params (``/api/scout/{session_id}``,
``/api/run/{session_id}``, ``/api/rerun/{session_id}``) are joined to
``OUTPUT_DIR``. A crafted ``session_id`` containing ``..`` or a separator could
otherwise escape the output tree. The validator rejects anything that is not a
single safe path segment; handlers map the failure onto HTTP 400.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils import paths
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


def test_configured_output_root_outside_the_project_is_writable(monkeypatch):
    # Azure keeps runs in /home/data, outside both the app folder and temp.
    root = Path("/home/data")
    monkeypatch.setattr(paths, "_TEMP_ROOTS", ())
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(root))
    assert paths.assert_writable_output_dir(root / "session") == (root / "session").resolve()
    with pytest.raises(ValueError):
        paths.assert_writable_output_dir("/home/other/session")


@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "output/../../../../../../../../etc/passwd",
    "~/.ssh/id_rsa",
    "output/a\x00.xlsx",
])
def test_safe_path_refuses_paths_outside_approved_folders(path):
    with pytest.raises(paths.UnsafePathError):
        paths.safe_path(path)
    assert issubclass(paths.UnsafePathError, OSError)


def test_safe_path_refuses_a_symlink_that_leaves_the_folder(tmp_path, monkeypatch, make_symlink):
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(paths, "_TEMP_ROOTS", (tmp_path / "project",))
    (tmp_path / "project").mkdir()
    (tmp_path / "secret.txt").write_text("x")
    link = tmp_path / "project" / "link.txt"
    make_symlink(link, tmp_path / "secret.txt")
    with pytest.raises(paths.UnsafePathError):
        paths.safe_path(link)


def test_safe_path_accepts_project_temp_and_configured_folders(tmp_path, monkeypatch):
    assert paths.safe_path(paths.PROJECT_ROOT / "output" / "a.xlsx").is_absolute()
    assert paths.safe_path(tmp_path / "a.xlsx") == (tmp_path / "a.xlsx").resolve()
    monkeypatch.setenv("XBRL_OUTPUT_DIR", "/home/data")
    assert paths.safe_path("/home/data/run/a.xlsx").name == "a.xlsx"


def test_database_open_is_guarded_but_memory_still_works():
    from db.repository import db_session

    assert paths.safe_db_target(":memory:") == ":memory:"
    with pytest.raises(paths.UnsafePathError):
        with db_session("/etc/xbrl-test.db"):
            pass


def test_command_line_tools_may_name_a_file_anywhere():
    assert paths.operator_path("/etc/passwd").is_absolute()
    with pytest.raises(paths.UnsafePathError):
        paths.operator_path("a\x00.xlsx")


def test_a_folder_named_on_the_command_line_is_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_OPERATOR_ROOTS", [])
    chosen = "/opt/xbrl-chosen-runs"
    with pytest.raises(paths.UnsafePathError):
        paths.safe_path(f"{chosen}/run_001/a.xlsx")
    paths.approve_operator_folder(chosen)
    assert paths.safe_path(f"{chosen}/run_001/a.xlsx").name == "a.xlsx"
    assert paths.assert_writable_output_dir(f"{chosen}/run_001").name == "run_001"


def test_deleting_a_symlink_removes_the_link_and_keeps_its_target(tmp_path, make_symlink):
    target = tmp_path / "uploaded.pdf"
    target.write_text("source")
    link = tmp_path / "SOFP_conversation_trace.json"
    make_symlink(link, target)

    paths.safe_entry_path(link).unlink()

    assert target.read_text() == "source"
    assert not link.is_symlink()


def test_replacing_a_symlink_replaces_the_link_and_keeps_its_target(tmp_path, make_symlink):
    from utils.atomic_io import replace_with_retry

    target = tmp_path / "workbook.xlsx"
    target.write_text("original")
    link = tmp_path / "linked.xlsx"
    make_symlink(link, target)
    new = tmp_path / "new.xlsx"
    new.write_text("new")

    replace_with_retry(new, link)

    assert target.read_text() == "original"
    assert not link.is_symlink() and link.read_text() == "new"


@pytest.mark.parametrize("path", ["/etc/passwd", "output/.."])
def test_entry_path_refuses_outside_folders_and_nameless_paths(path):
    with pytest.raises(paths.UnsafePathError):
        paths.safe_entry_path(path)


def test_parser_command_line_reads_a_template_the_person_named(tmp_path, monkeypatch, capsys):
    import shutil

    from concept_model.parser import _cli

    template = tmp_path / "chosen.xlsx"
    shutil.copyfile(paths.PROJECT_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx", template)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "elsewhere")
    monkeypatch.setattr(paths, "_TEMP_ROOTS", ())
    monkeypatch.delenv("XBRL_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(paths, "_OPERATOR_ROOTS", [])
    with pytest.raises(paths.UnsafePathError):
        paths.safe_path(template)

    assert _cli([str(template)]) == 0
    assert capsys.readouterr().out.strip()
