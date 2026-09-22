"""Small cross-platform primitives for atomic file publication."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, TypeAlias


Pathish: TypeAlias = str | bytes | os.PathLike[str] | os.PathLike[bytes]

REPLACE_ATTEMPTS = 8
INITIAL_RETRY_DELAY_SECONDS = 0.05
MAX_RETRY_DELAY_SECONDS = 0.5

_TRANSIENT_ERRNOS = frozenset({
    errno.EBUSY,
    getattr(errno, "ETXTBSY", errno.EBUSY),
})
_TRANSIENT_WINDOWS_ERRORS = frozenset({
    5,   # ERROR_ACCESS_DENIED (often returned by scanners and sync clients)
    32,  # ERROR_SHARING_VIOLATION
    33,  # ERROR_LOCK_VIOLATION
})


def _is_transient_replace_error(exc: OSError) -> bool:
    return (
        getattr(exc, "winerror", None) in _TRANSIENT_WINDOWS_ERRORS
        or exc.errno in _TRANSIENT_ERRNOS
    )


def replace_with_retry(source: Pathish, destination: Pathish) -> None:
    """Atomically replace *destination*, retrying only transient file locks.

    Windows readers may temporarily deny rename/delete sharing. Linux and
    network filesystems can transiently report a busy target. Bounded retry
    keeps those conditions recoverable without hiding missing paths, invalid
    destinations, or Linux permission and ownership errors.
    """
    delay = INITIAL_RETRY_DELAY_SECONDS
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if attempt == REPLACE_ATTEMPTS - 1 or not _is_transient_replace_error(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)


def write_json_atomic(
    path: str | Path,
    payload: dict[str, Any],
    *,
    replace: Callable[[Pathish, Pathish], None] = replace_with_retry,
) -> None:
    """Durably publish a JSON object by replacing a same-directory temp file."""
    destination = Path(path)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=".json",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


__all__ = ["replace_with_retry", "write_json_atomic"]
