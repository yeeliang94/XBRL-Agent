import errno
from pathlib import Path

import pytest


def test_replace_with_retry_survives_transient_windows_sharing_violation(tmp_path, monkeypatch):
    from utils import atomic_io

    source = tmp_path / "pending.json"
    destination = tmp_path / "current.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    real_replace = atomic_io.os.replace
    attempts = 0
    delays = []

    def transient_replace(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = PermissionError(errno.EACCES, "Access is denied", str(dst))
            error.winerror = 5
            raise error
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", transient_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", delays.append)

    atomic_io.replace_with_retry(source, destination)

    assert attempts == 2
    assert delays == [atomic_io.INITIAL_RETRY_DELAY_SECONDS]
    assert destination.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_replace_with_retry_survives_transient_linux_busy_error(tmp_path, monkeypatch):
    from utils import atomic_io

    source = tmp_path / "pending.json"
    destination = tmp_path / "current.json"
    source.write_text("new", encoding="utf-8")
    real_replace = atomic_io.os.replace
    attempts = 0

    def transient_replace(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.EBUSY, "Device or resource busy", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", transient_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: None)

    atomic_io.replace_with_retry(source, destination)

    assert attempts == 2
    assert destination.read_text(encoding="utf-8") == "new"


def test_replace_with_retry_exhausts_bounded_attempts_and_preserves_destination(tmp_path, monkeypatch):
    from utils import atomic_io

    source = tmp_path / "pending.json"
    destination = tmp_path / "current.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    attempts = 0

    def locked_replace(_src, dst):
        nonlocal attempts
        attempts += 1
        error = PermissionError(errno.EACCES, "Access is denied", str(dst))
        error.winerror = 32
        raise error

    monkeypatch.setattr(atomic_io.os, "replace", locked_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: None)

    with pytest.raises(PermissionError, match="Access is denied"):
        atomic_io.replace_with_retry(source, destination)

    assert attempts == atomic_io.REPLACE_ATTEMPTS
    assert destination.read_text(encoding="utf-8") == "old"
    assert source.read_text(encoding="utf-8") == "new"


def test_replace_with_retry_does_not_retry_non_transient_error(tmp_path, monkeypatch):
    from utils import atomic_io

    source = tmp_path / "pending.json"
    destination = tmp_path / "current.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0

    def missing_replace(_src, dst):
        nonlocal attempts
        attempts += 1
        raise FileNotFoundError(errno.ENOENT, "Missing", str(dst))

    monkeypatch.setattr(atomic_io.os, "replace", missing_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: pytest.fail("must not sleep"))

    with pytest.raises(FileNotFoundError, match="Missing"):
        atomic_io.replace_with_retry(source, destination)

    assert attempts == 1


def test_replace_with_retry_does_not_hide_linux_permission_error(tmp_path, monkeypatch):
    from utils import atomic_io

    source = tmp_path / "pending.json"
    destination = tmp_path / "current.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0

    def denied_replace(_src, dst):
        nonlocal attempts
        attempts += 1
        raise PermissionError(errno.EACCES, "Permission denied", str(dst))

    monkeypatch.setattr(atomic_io.os, "replace", denied_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: pytest.fail("must not sleep"))

    with pytest.raises(PermissionError, match="Permission denied"):
        atomic_io.replace_with_retry(source, destination)

    assert attempts == 1
