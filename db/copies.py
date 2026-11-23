"""Saved copies of the live database, for Azure App Service.

On Azure the live SQLite file sits on the container's own disk
(``db/location.py``), which is wiped whenever the container is replaced. This
module keeps it safe:

- ``restore`` runs at startup, before the schema check. It keeps a live file
  this container already has, or else loads the newest good copy. When copies
  exist but none can be read, startup stops rather than run an empty database
  whose copies would push the good ones out.
- ``CopySaver`` runs on one background thread. Every minute, if anything
  changed, it saves a compressed copy to ``<output directory>/db-copies`` on
  the ``/home`` drive. It also saves soon after a run finishes, and once more
  when the app is told to stop. It keeps the newest ``KEEP`` copies.

Azure starts a new container before it stops the old one. ``owner.json`` names
the container that may save: the newest one claims it once its startup has
succeeded (a container that fails to start never takes saving away from the
one still serving), and an older container's saves are refused from then on,
so it cannot overwrite the newer container's copies. Changes the old container makes in that minute are
lost; the owner accepted this (2026-11-23).

Copies are only ever read whole and replaced by rename, never opened live on
``/home``.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from db import engine as db_engine
from utils.paths import safe_entry_path, safe_path

logger = logging.getLogger(__name__)

INTERVAL_S = 60.0
KEEP = 5
STALE_S = 600.0
# A save asked for when a run finishes waits this long, so the run's last
# commit lands first and a burst of requests becomes one save.
REQUEST_DELAY_S = 2.0
OWNER_FILE = "owner.json"
# Next to the live file: which saving process this disk's database belongs to.
SAVED_BY_FILE = "saved-by"
_COPY_RE = re.compile(r"^xbrl_agent-(\d{8}T\d{6}\.\d{6}Z)-(.+)\.db\.gz$")


def container_id() -> str:
    """A name for this app process, unique across containers and restarts."""
    raw = f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _copies(copies: Path) -> list[tuple[str, str, Path]]:
    """(time, owner, path) for every finished copy, newest first."""
    found = []
    folder = safe_path(copies)
    if folder.is_dir():
        for path in folder.iterdir():
            match = _COPY_RE.match(path.name)
            if match:
                found.append((match.group(1), match.group(2), path))
    return sorted(found, reverse=True)


def read_owner(copies: Path) -> str | None:
    try:
        return json.loads(safe_path(copies / OWNER_FILE).read_text(encoding="utf-8"))["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claim(copies: Path, me: str) -> None:
    safe_path(copies).mkdir(parents=True, exist_ok=True)
    part = safe_entry_path(copies / f"{OWNER_FILE}.{me}.part")
    part.write_text(json.dumps({"id": me, "claimed_at": _now_stamp()}), encoding="utf-8")
    os.replace(part, safe_entry_path(copies / OWNER_FILE))


def _remove_live_files(live: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = safe_entry_path(Path(f"{live}{suffix}"))
        if target.exists():
            target.unlink()


def _passes_check(path: Path) -> bool:
    try:
        conn = db_engine.connect(path)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            # An empty file passes quick_check; a real copy has the app's tables.
            return conn.execute("SELECT 1 FROM sqlite_master WHERE name = "
                                "'schema_version'").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def restore(live: Path, copies: Path) -> str:
    """Make sure the live database is ready. Claims nothing.

    Returns what happened: ``"kept"``, ``"restored"`` or ``"new"``. Raises
    RuntimeError when copies exist but none can be restored.
    """
    previous = read_owner(copies)
    safe_path(live.parent).mkdir(parents=True, exist_ok=True)
    if live.exists():
        # Keep it only when it belongs to the process that owns saving now:
        # the app restarted inside the same container. A database left by a
        # start that failed, or one another container has since moved past,
        # is older than the copies. File dates are not compared; in WAL mode
        # the main file's date lags its newest data.
        if previous is None or _saved_by(live) == previous:
            logger.info("database copies: keeping the live database already on this disk")
            return "kept"
        logger.info("database copies: the database on this disk is older than the copies; "
                    "restoring instead")
    candidates = _copies(copies)
    # A late copy from an older container must not win over the previous
    # owner's work, so the previous owner's copies are tried first.
    ordered = ([c for c in candidates if c[1] == previous]
               + [c for c in candidates if c[1] != previous])
    staging = safe_entry_path(live.with_name(live.name + ".restore"))
    for stamp, owner, path in ordered:
        try:
            with gzip.open(safe_path(path), "rb") as src, open(staging, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except (OSError, EOFError, gzip.BadGzipFile):
            logger.warning("database copies: skipping unreadable copy %s", path.name, exc_info=True)
            continue
        if not _passes_check(staging):
            logger.warning("database copies: skipping damaged copy %s", path.name)
            continue
        _remove_live_files(live)
        os.replace(staging, safe_entry_path(live))
        logger.info("database copies: restored %s (saved %s by %s)", path.name, stamp, owner)
        return "restored"
    if staging.exists():
        staging.unlink()
    if candidates:
        raise RuntimeError(
            f"None of the {len(candidates)} saved database copies in {copies.name}/ could be "
            "restored. Startup stopped so an empty database cannot replace them. Check "
            "that /home is reachable and restart. To start empty on purpose, move the "
            "copies folder aside first.")
    logger.info("database copies: no copies yet; starting an empty database")
    return "new"


class CopySaver:
    """Saves copies of ``live`` into ``copies`` on its own thread."""

    def __init__(self, live: Path, copies: Path, me: str, *, dirty: bool = False,
                 interval_s: float = INTERVAL_S, request_delay_s: float = REQUEST_DELAY_S) -> None:
        self._live = live
        self._copies = copies
        self._me = me
        self._interval_s = interval_s
        self._request_delay_s = request_delay_s
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()  # one save at a time
        self._lost = False
        self._dirty = dirty
        self._version: int | None = None
        self._last_saved_at: float | None = None
        self._dirty_since: float | None = time.time() if dirty else None
        self._stale_logged = False
        # Autocommit: the saver only reads, and must never hold a read open,
        # which would stop SQLite trimming its write log. Used from the saver
        # thread and from stop(); self._lock keeps that to one at a time.
        self._conn = db_engine.connect(live, check_same_thread=False)
        self._conn.isolation_level = None
        self._version = self._data_version()
        self._thread = threading.Thread(target=self._loop, name="db-copy-saver", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def request_save(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 60.0) -> None:
        """Save once more if anything changed, then stop the thread."""
        self._stopping.set()
        self._wake.set()
        self._thread.join(timeout)

    def status(self) -> dict:
        saved = self._last_saved_at
        return {
            "last_saved_at": (datetime.fromtimestamp(saved, timezone.utc).isoformat()
                              if saved else None),
            "saving": not self._lost,
            "stale": self._is_stale(),
        }

    def _data_version(self) -> int:
        return self._conn.execute("PRAGMA data_version").fetchone()[0]

    def _changed(self) -> bool:
        """Whether another connection committed since the last copy was taken."""
        version = self._data_version()
        if version != self._version and not self._dirty:
            self._dirty = True
            self._dirty_since = time.time()
        return self._dirty

    def _is_stale(self) -> bool:
        if self._lost or not self._dirty or self._dirty_since is None:
            return False
        return time.time() - self._dirty_since > STALE_S

    def _loop(self) -> None:
        try:
            while not self._stopping.is_set():
                requested = self._wake.wait(self._interval_s)
                if requested and not self._stopping.is_set():
                    self._stopping.wait(self._request_delay_s)
                self._wake.clear()
                self.save_if_changed()
            # Commits made while the last save ran.
            self.save_if_changed()
        finally:
            try:
                self._conn.close()
            except Exception:
                pass

    def save_if_changed(self) -> bool:
        """Save a copy when the database changed. True when a copy was saved."""
        with self._lock:
            try:
                if self._lost or not self._changed():
                    return False
                return self._save()
            except Exception:
                logger.exception("database copies: saving a copy failed")
                return False
            finally:
                if self._is_stale() and not self._stale_logged:
                    self._stale_logged = True
                    logger.error("database copies: no copy saved for over %d minutes while "
                                 "the database changed; a restart now would lose those changes",
                                 int(STALE_S // 60))

    def _owns(self) -> bool:
        owner = read_owner(self._copies)
        if owner == self._me:
            return True
        if owner is None:
            # Unreadable, not taken over: skip this save and try again on the
            # next tick. The stale warning still fires if this lasts.
            logger.warning("database copies: could not read %s; will try again", OWNER_FILE)
            return False
        if not self._lost:
            self._lost = True
            logger.warning("database copies: a newer copy of the app took over; "
                           "this one stops saving")
        return False

    def _save(self) -> bool:
        if not self._owns():
            return False
        version = self._data_version()  # commits after this trigger another save
        snapshot = safe_entry_path(self._live.with_name(self._live.name + ".snapshot"))
        if snapshot.exists():
            snapshot.unlink()
        self._conn.execute("VACUUM INTO ?", (str(snapshot),))
        name = f"xbrl_agent-{_now_stamp()}-{self._me}.db.gz"
        final = safe_entry_path(self._copies / name)
        part = safe_entry_path(self._copies / (name + ".part"))
        try:
            with open(snapshot, "rb") as src, gzip.open(part, "wb", compresslevel=1) as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            if not self._owns():  # ownership moved while this copy was written
                part.unlink()
                return False
            os.replace(part, final)
        finally:
            snapshot.unlink(missing_ok=True)
            if part.exists():
                part.unlink()
        self._version = version
        self._dirty = self._data_version() != version
        self._last_saved_at = time.time()
        self._dirty_since = self._last_saved_at if self._dirty else None
        self._stale_logged = False
        for _stamp, _owner, old in _copies(self._copies)[KEEP:]:
            try:
                safe_entry_path(old).unlink()
            except OSError:
                logger.warning("database copies: could not delete old copy %s", old.name)
        logger.info("database copies: saved %s", name)
        return True


_saver: CopySaver | None = None


_restored: tuple[Path, Path, str] | None = None


def restore_at_startup(live: Path, copies: Path) -> None:
    """First step of startup on Azure, before anything opens the database."""
    global _restored
    _restored = (live, copies, restore(live, copies))


def start_saving() -> CopySaver:
    """Last step of a successful startup: claim ``owner.json`` and start saving."""
    global _saver
    live, copies, _outcome = _restored
    me = container_id()
    # Startup itself writes (migrations, the bootstrap admin, failed-run
    # cleanup), so the first copy is always due. Everything that can fail is
    # done before the claim, so a failure leaves saving with the old owner.
    saver = CopySaver(live, copies, me, dirty=True)
    safe_entry_path(live.parent / SAVED_BY_FILE).write_text(me, encoding="utf-8")
    claim(copies, me)
    _remove_stale_parts(copies)
    saver.start()
    _saver = saver
    return saver


def _saved_by(live: Path) -> str | None:
    try:
        return safe_path(live.parent / SAVED_BY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _remove_stale_parts(copies: Path) -> None:
    """Delete half-written copies left by a container killed mid-save."""
    cutoff = time.time() - STALE_S
    for part in safe_path(copies).glob("xbrl_agent-*.part"):
        try:
            if part.stat().st_mtime < cutoff:
                safe_entry_path(part).unlink()
        except OSError:
            pass


def request_save() -> None:
    """Ask for a copy soon. Does nothing where copies are not kept."""
    if _saver is not None:
        _saver.request_save()


def stop() -> None:
    global _saver
    if _saver is not None:
        _saver.stop()
        _saver = None


def status() -> dict | None:
    return _saver.status() if _saver is not None else None
