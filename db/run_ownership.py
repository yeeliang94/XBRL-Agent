"""OS-held extraction ownership shared by CLI and web processes.

The OS releases the lock on a crash. Empty lock files remain so a competing
process always probes the same inode; no PID/age guess or database migration.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading

_guard = threading.RLock()
_held: dict[tuple[str, int], object] = {}


def _key(conn, run_id):
    path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    return (str(Path(path).resolve()), int(run_id)) if path else None


def _open(key):
    database, run_id = key
    directory = Path(database).parent / (Path(database).name + ".run-owners")
    directory.mkdir(exist_ok=True)
    return (directory / f"{run_id}.lock").open("a+b")


def _lock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


def claim(conn, run_id):
    key = _key(conn, run_id)
    if key is None:
        return
    with _guard:
        if key in _held:
            return
        handle = _open(key)
        try:
            _lock(handle)
        except BaseException:
            handle.close()
            raise
        _held[key] = handle


def release(conn, run_id):
    key = _key(conn, run_id)
    with _guard:
        handle = _held.pop(key, None)
        if handle:
            try:
                _unlock(handle)
            finally:
                handle.close()


def is_live(conn, run_id):
    key = _key(conn, run_id)
    if key is None:
        return False
    with _guard:
        if key in _held:
            return True
        with _open(key) as handle:
            try:
                _lock(handle)
            except BlockingIOError:
                return True
            except OSError as exc:
                if os.name == "nt" and exc.errno in (13, 36):
                    return True
                raise
            _unlock(handle)
        return False


def protected_runs(conn):
    return [int(row[0]) for row in conn.execute("SELECT id FROM runs WHERE status = 'running'")
            if is_live(conn, row[0])]
