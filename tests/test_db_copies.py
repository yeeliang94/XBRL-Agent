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
    assert copies.restore(fresh, folder, "b") == "restored"
    assert _runs(fresh) == ["finished.pdf"]
    assert copies.read_owner(folder) == "b"


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
    copies.restore(restored, folder, "next")
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
    copies.restore(fresh, folder, "b")
    assert len(_runs(fresh)) == copies.KEEP + 2


def test_a_live_database_already_on_this_disk_is_kept(tmp_path, folder):
    live = _live(tmp_path)
    _saver(live, folder, "a").save_if_changed()
    _add_run(live, "after-last-copy.pdf")  # newer than every copy

    assert copies.restore(live, folder, "a2") == "kept"
    assert _runs(live) == ["after-last-copy.pdf"]


def test_no_copies_starts_an_empty_database(tmp_path, folder):
    assert copies.restore(tmp_path / "x" / "xbrl_agent.db", folder, "a") == "new"


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
    copies.restore(restored, folder, "b")
    assert _runs(restored) == ["done.pdf"]
