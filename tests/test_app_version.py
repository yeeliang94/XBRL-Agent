"""App-version resolver + run stamping (docs/PLAN-evals-workspace.md, Step A2).

The resolver picks the first available of: XBRL_APP_VERSION env, a VERSION file,
`git describe`, then "unknown" — and never raises. create_run stamps the
resolved value onto every new run so the Evals workspace can trend by build.
"""
from __future__ import annotations

import importlib

from db.repository import create_run, db_session, fetch_run
from db.schema import init_db


def _fresh_resolver():
    """Reload the module so the lru_cache is empty for each test."""
    import utils.app_version as av

    importlib.reload(av)
    return av


def test_env_var_wins(monkeypatch):
    monkeypatch.setenv("XBRL_APP_VERSION", "2026.07.10-rc1")
    av = _fresh_resolver()
    assert av.get_app_version() == "2026.07.10-rc1"


def test_env_blank_falls_through_to_git_or_unknown(monkeypatch):
    monkeypatch.setenv("XBRL_APP_VERSION", "   ")
    av = _fresh_resolver()
    # In this repo git resolves; in a git-less sandbox it degrades to "unknown".
    # Either way it must be a non-empty string and never raise.
    value = av.get_app_version()
    assert isinstance(value, str) and value


def test_resolver_never_raises(monkeypatch):
    monkeypatch.delenv("XBRL_APP_VERSION", raising=False)
    av = _fresh_resolver()
    assert isinstance(av.get_app_version(), str)


def test_create_run_stamps_app_version(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_APP_VERSION", "test-build-42")
    _fresh_resolver()  # clear cache so create_run's lazy import sees the env
    db = tmp_path / "runs.db"
    init_db(db)
    with db_session(db) as conn:
        run_id = create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        run = fetch_run(conn, run_id)
    assert run.app_version == "test-build-42"


def test_create_run_accepts_explicit_override(tmp_path):
    db = tmp_path / "runs.db"
    init_db(db)
    with db_session(db) as conn:
        run_id = create_run(
            conn, "x.pdf", session_id="s", output_dir="/tmp/s",
            app_version="explicit-1.0", repeat_group_id=None, repeat_index=None,
        )
        run = fetch_run(conn, run_id)
    assert run.app_version == "explicit-1.0"
