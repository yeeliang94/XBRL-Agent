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


def test_untracked_changes_append_prompt_hash(monkeypatch):
    """Peer-review Step 16: `git describe --dirty` misses untracked prompt files,
    so a clean describe + untracked changes must still append the prompt hash."""
    av = _fresh_resolver()
    monkeypatch.setattr(av, "_static_build_stamp", lambda: None)
    monkeypatch.setattr(av, "_from_git", lambda: "v1.2.3")  # clean describe
    monkeypatch.setattr(av, "_git_has_local_changes", lambda: True)  # untracked
    monkeypatch.setattr(av, "prompt_state_hash", lambda root=None: "abc12345")
    assert av.get_app_version() == "v1.2.3-dirty+pabc12345"


def test_two_prompt_experiments_get_different_versions(monkeypatch):
    """Peer-review Step 16: the version is no longer cached for the process
    lifetime on a dirty checkout, so editing a prompt and re-running (same
    process) yields a DIFFERENT version — not the stale first one. (TTL forced
    to 0 here; two real experiments are minutes apart, far past the 5s TTL.)"""
    av = _fresh_resolver()
    monkeypatch.setattr(av, "_GIT_CACHE_TTL_S", 0.0)
    monkeypatch.setattr(av, "_static_build_stamp", lambda: None)
    monkeypatch.setattr(av, "_from_git", lambda: "v1.2.3-dirty")
    monkeypatch.setattr(av, "_git_has_local_changes", lambda: True)
    seq = iter(["hashA", "hashB"])
    monkeypatch.setattr(av, "prompt_state_hash", lambda root=None: next(seq))
    v1 = av.get_app_version()
    v2 = av.get_app_version()
    assert v1 == "v1.2.3-dirty+phashA"
    assert v2 == "v1.2.3-dirty+phashB"
    assert v1 != v2


def test_git_version_cached_within_ttl(monkeypatch):
    """Back-to-back calls inside the TTL (e.g. /api/config on every page load)
    reuse the derived value instead of re-running git + re-hashing prompts."""
    av = _fresh_resolver()
    monkeypatch.setattr(av, "_static_build_stamp", lambda: None)
    calls = {"n": 0}

    def counting_describe():
        calls["n"] += 1
        return "v1.2.3"

    monkeypatch.setattr(av, "_from_git", counting_describe)
    monkeypatch.setattr(av, "_git_has_local_changes", lambda: False)
    assert av.get_app_version() == "v1.2.3"
    assert av.get_app_version() == "v1.2.3"
    assert calls["n"] == 1


def test_untracked_changes_without_hash_still_mark_dirty(monkeypatch):
    """If the prompt-state hash fails on a clean-describe-but-untracked tree,
    the version must still carry the honest -dirty marker — never read clean."""
    av = _fresh_resolver()
    monkeypatch.setattr(av, "_static_build_stamp", lambda: None)
    monkeypatch.setattr(av, "_from_git", lambda: "v1.2.3")
    monkeypatch.setattr(av, "_git_has_local_changes", lambda: True)
    monkeypatch.setattr(av, "prompt_state_hash", lambda root=None: None)
    assert av.get_app_version() == "v1.2.3-dirty"


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


def test_list_runs_carries_app_version(tmp_path):
    """RunSummary + the History serializer expose app_version (Step A2 surface)."""
    from db.repository import list_runs
    import server

    db = tmp_path / "runs.db"
    init_db(db)
    with db_session(db) as conn:
        create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s",
                   app_version="build-77")
        summaries = list_runs(conn)
    assert summaries[0].app_version == "build-77"
    wire = server._run_summary_to_dict(summaries[0])
    assert wire["app_version"] == "build-77"
