"""Saved database copies on Azure (db/copies.py, db/location.py).

On Azure the live SQLite file sits on the container's own disk, which is
wiped when the container is replaced. Finished runs, notes edits and accounts
survive only through the copies saved on /home, so these tests check what a
restart would bring back.
"""
from __future__ import annotations

import gzip
import logging
import sqlite3
import time
from pathlib import Path

import pytest

from db import copies, location
from db import repository as repo
from db.schema import init_db


def _live(tmp_path: Path, name: str = "container") -> Path:
    path = tmp_path / name / "xbrl_agent.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(path)
    return path


def _add_run(db: Path, filename: str) -> None:
    conn = sqlite3.connect(db)
    repo.create_run(conn, filename)
    conn.commit()
    conn.close()


def _runs(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT pdf_filename FROM runs ORDER BY id")]
    finally:
        conn.close()


def _saved(folder: Path) -> list[Path]:
    return sorted(folder.glob("xbrl_agent-*.db.gz"))


@pytest.fixture()
def folder(tmp_path):
    return tmp_path / "db-copies"


def _saver(live: Path, folder: Path, me: str, **kw) -> copies.CopySaver:
    copies.claim(folder, me)
    return copies.CopySaver(live, folder, me, **kw)


def test_a_copy_is_saved_only_after_the_database_changed(tmp_path, folder):
    live = _live(tmp_path)
    saver = _saver(live, folder, "a")

    assert saver.save_if_changed() is False
    _add_run(live, "first.pdf")
    assert saver.save_if_changed() is True
    assert saver.save_if_changed() is False
    assert len(_saved(folder)) == 1
    assert saver.status()["last_saved_at"] is not None


def test_a_restart_brings_back_the_newest_good_copy(tmp_path, folder):
    live = _live(tmp_path, "old")
    saver = _saver(live, folder, "a")
    _add_run(live, "finished.pdf")
    saver.save_if_changed()
    # A newer copy that is damaged, as a half-lost write would leave it.
    good = _saved(folder)[0]
    damaged = folder / "xbrl_agent-29991231T000000.000000Z-a.db.gz"
    damaged.write_bytes(gzip.compress(gzip.decompress(good.read_bytes())[:8192]))

    fresh = tmp_path / "new" / "xbrl_agent.db"
    assert copies.restore(fresh, folder) == "restored"
    assert _runs(fresh) == ["finished.pdf"]


def test_the_previous_owners_copy_wins_over_a_late_copy_from_an_older_container(tmp_path, folder):
    older = _live(tmp_path, "older")
    newer = _live(tmp_path, "newer")
    _add_run(older, "older.pdf")
    _add_run(newer, "newer.pdf")
    newer_saver = _saver(newer, folder, "newer", dirty=True)
    newer_saver.save_if_changed()
    # The older container lands one copy after the newer one took over.
    late = copies.CopySaver(older, folder, "older", dirty=True)
    copies.claim(folder, "older")
    late.save_if_changed()
    copies.claim(folder, "newer")

    restored = tmp_path / "next" / "xbrl_agent.db"
    copies.restore(restored, folder)
    assert _runs(restored) == ["newer.pdf"]


def test_a_container_that_lost_ownership_saves_nothing(tmp_path, folder, caplog):
    live = _live(tmp_path)
    saver = _saver(live, folder, "old")
    copies.claim(folder, "new")
    _add_run(live, "late.pdf")

    with caplog.at_level(logging.WARNING, logger="db.copies"):
        assert saver.save_if_changed() is False
        assert saver.save_if_changed() is False
    assert _saved(folder) == []
    assert saver.status()["saving"] is False
    assert sum("stops saving" in r.message for r in caplog.records) == 1


def test_a_copy_is_abandoned_when_ownership_moves_while_it_is_written(tmp_path, folder, monkeypatch):
    live = _live(tmp_path)
    saver = _saver(live, folder, "old")
    _add_run(live, "x.pdf")
    answers = iter(["old", "new"])  # owner before writing, then after
    monkeypatch.setattr(copies, "read_owner", lambda _folder: next(answers))

    assert saver.save_if_changed() is False
    assert list(folder.glob("xbrl_agent-*")) == []


def test_only_the_newest_copies_are_kept(tmp_path, folder):
    live = _live(tmp_path)
    saver = _saver(live, folder, "a")
    for i in range(copies.KEEP + 2):
        _add_run(live, f"{i}.pdf")
        assert saver.save_if_changed()
    kept = _saved(folder)
    assert len(kept) == copies.KEEP
    fresh = tmp_path / "fresh" / "xbrl_agent.db"
    copies.restore(fresh, folder)
    assert len(_runs(fresh)) == copies.KEEP + 2


def _start_saving_on(live: Path, folder: Path) -> copies.CopySaver:
    """The startup path: restore, then claim saving."""
    copies.restore_at_startup(live, folder)
    return copies.start_saving()


def test_a_live_database_already_on_this_disk_is_kept(tmp_path, folder):
    live = _live(tmp_path)
    try:
        _start_saving_on(live, folder).save_if_changed()
    finally:
        copies.stop()
    _add_run(live, "after-last-copy.pdf")  # the app restarted before the next copy

    assert copies.restore(live, folder) == "kept"
    assert _runs(live) == ["after-last-copy.pdf"]


def test_a_database_left_by_a_start_that_failed_is_replaced_by_the_copies(tmp_path, folder):
    serving = _live(tmp_path, "serving")
    copies.claim(folder, "serving")
    _add_run(serving, "saved-by-the-serving-container.pdf")
    copies.CopySaver(serving, folder, "serving", dirty=True).save_if_changed()
    # This disk restored an older state, then its startup failed before claiming.
    stale = _live(tmp_path, "retrying")
    _add_run(stale, "older.pdf")

    assert copies.restore(stale, folder) == "restored"
    assert _runs(stale) == ["saved-by-the-serving-container.pdf"]


def test_an_empty_copy_is_skipped(tmp_path, folder):
    live = _live(tmp_path)
    saver = _saver(live, folder, "a")
    _add_run(live, "real.pdf")
    saver.save_if_changed()
    (folder / "xbrl_agent-29991231T000000.000000Z-a.db.gz").write_bytes(gzip.compress(b""))

    fresh = tmp_path / "fresh" / "xbrl_agent.db"
    assert copies.restore(fresh, folder) == "restored"
    assert _runs(fresh) == ["real.pdf"]


def test_a_failure_before_the_claim_leaves_saving_with_the_old_owner(tmp_path, folder, monkeypatch):
    live = _live(tmp_path)
    copies.claim(folder, "serving")
    copies.restore_at_startup(live, folder)
    monkeypatch.setattr(copies, "CopySaver", _refuse)
    with pytest.raises(OSError):
        copies.start_saving()
    assert copies.read_owner(folder) == "serving"


def test_no_copies_starts_an_empty_database(tmp_path, folder):
    assert copies.restore(tmp_path / "x" / "xbrl_agent.db", folder) == "new"


def test_saving_that_falls_behind_is_reported(tmp_path, folder, monkeypatch, caplog):
    live = _live(tmp_path)
    saver = _saver(live, folder, "a")
    monkeypatch.setattr(copies, "STALE_S", 0.0)
    monkeypatch.setattr(copies.gzip, "open", _refuse)
    _add_run(live, "x.pdf")

    with caplog.at_level(logging.ERROR, logger="db.copies"):
        assert saver.save_if_changed() is False
    assert saver.status()["stale"] is True
    assert any("no copy saved" in r.message for r in caplog.records)


def _refuse(*_a, **_k):
    raise OSError("network drive unavailable")


def test_on_azure_the_live_database_sits_on_the_containers_own_disk(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    assert location.live_database_path(tmp_path) == tmp_path / "xbrl_agent.db"
    location.check_live_location("/home/data/xbrl_agent.db")  # laptop: allowed

    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    assert location.live_database_path("/home/data") == Path("/tmp/xbrl-live/xbrl_agent.db")
    with pytest.raises(RuntimeError, match="/home network drive"):
        location.check_live_location("/home/data/xbrl_agent.db")


def test_a_finished_run_triggers_a_copy(tmp_path, folder):
    import server

    live = _live(tmp_path)
    copies.claim(folder, "a")
    copies._saver = saver = copies.CopySaver(live, folder, "a", request_delay_s=0.0, interval_s=3600)
    saver.start()
    try:
        conn = sqlite3.connect(live)
        run_id = repo.create_run(conn, "done.pdf")
        conn.commit()
        assert server._safe_mark_finished(conn, run_id, "completed")
        conn.close()
        deadline = time.monotonic() + 10
        while not _saved(folder) and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        copies.stop()
    assert _saved(folder), "no copy saved after the run finished"
    restored = tmp_path / "after" / "xbrl_agent.db"
    copies.restore(restored, folder)
    assert _runs(restored) == ["done.pdf"]


def test_copies_that_cannot_be_restored_stop_startup(tmp_path, folder):
    folder.mkdir()
    (folder / "xbrl_agent-20261123T000000.000000Z-a.db.gz").write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="could be restored"):
        copies.restore(tmp_path / "x" / "xbrl_agent.db", folder)


def test_an_unreadable_owner_file_skips_one_save_but_not_the_rest(tmp_path, folder):
    live = _live(tmp_path)
    saver = _saver(live, folder, "a")
    _add_run(live, "x.pdf")
    (folder / copies.OWNER_FILE).write_text("{half-writ", encoding="utf-8")
    assert saver.save_if_changed() is False
    assert saver.status()["saving"] is True

    copies.claim(folder, "a")  # the drive answers again
    assert saver.save_if_changed() is True


def test_stopping_saves_the_last_changes(tmp_path, folder):
    live = _live(tmp_path)
    copies.claim(folder, "a")
    saver = copies.CopySaver(live, folder, "a", interval_s=3600)
    saver.start()
    _add_run(live, "just-before-stop.pdf")
    saver.stop()
    restored = tmp_path / "after" / "xbrl_agent.db"
    copies.restore(restored, folder)
    assert _runs(restored) == ["just-before-stop.pdf"]


def _azure_startup(tmp_path, monkeypatch):
    """Point the server at a pretend Azure container and run its real startup."""
    import asyncio
    import server
    import concept_model.bootstrap as boot

    home = tmp_path / "home-data"
    live = tmp_path / "container" / "xbrl_agent.db"
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-test")
    monkeypatch.delenv("AUTH_MODE", raising=False)  # the suite's default; refused on Azure
    monkeypatch.setattr(server, "OUTPUT_DIR", home)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", live)
    monkeypatch.setattr(boot, "import_all_face_templates", lambda *a, **k: [])
    monkeypatch.setattr(boot, "import_all_notes_templates", lambda *a, **k: [])

    async def run():
        async with server._lifespan(server.app):
            pass

    return home / "db-copies", live, lambda: asyncio.run(run())


def _seed_copy_with_admin(tmp_path, folder, owner):
    from auth import passwords

    seed = _live(tmp_path, "seed")
    conn = sqlite3.connect(seed)
    repo.upsert_auth_user(conn, "owner@example.com", "Owner", passwords.hash_password("x" * 16))
    conn.commit()
    conn.close()
    _add_run(seed, "kept.pdf")
    copies.claim(folder, owner)
    copies.CopySaver(seed, folder, owner, dirty=True).save_if_changed()


def test_azure_startup_restores_first_and_claims_saving_last(tmp_path, monkeypatch):
    folder, live, start = _azure_startup(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "s" * 64)
    _seed_copy_with_admin(tmp_path, folder, "previous")

    start()

    assert "kept.pdf" in _runs(live)
    assert copies.read_owner(folder) != "previous"
    assert copies.status() is None  # stopped at shutdown
    # Startup's own writes (schema check, failed-run cleanup) were saved too.
    assert any(copies.read_owner(folder) in p.name for p in _saved(folder))


def test_a_container_that_fails_to_start_leaves_saving_with_the_old_one(tmp_path, monkeypatch):
    folder, live, start = _azure_startup(tmp_path, monkeypatch)
    monkeypatch.delenv("SESSION_SECRET", raising=False)  # this startup will fail
    _seed_copy_with_admin(tmp_path, folder, "serving")

    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        start()
    assert copies.read_owner(folder) == "serving"


def test_the_account_tool_refuses_to_create_the_azure_database(tmp_path, monkeypatch):
    from auth import manage

    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-test")
    monkeypatch.setattr(location, "AZURE_LIVE_DIR", tmp_path / "not-started")
    with pytest.raises(SystemExit, match="Start the app first"):
        manage.main(["list-users"])
    assert not (tmp_path / "not-started").exists()
