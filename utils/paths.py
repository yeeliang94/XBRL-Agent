"""Filesystem-path safety helpers.

Today's call sites trust their `output_dir` argument because it
originates server-side (server.py: ``OUTPUT_DIR / session_id`` where
session_id is a UUID, run.py: similar pattern). The helpers here are
defence-in-depth so a single upstream bug or a future caller can't
escalate into path traversal + blind directory creation against
sensitive locations like /etc, $HOME/.ssh, etc.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union


def validate_session_id(session_id: str) -> str:
    """Return ``session_id`` unchanged if it is a single safe path segment, else
    raise ``ValueError``.

    Defence-in-depth for URL-controlled session ids that are joined to
    ``OUTPUT_DIR`` (``OUTPUT_DIR / session_id``). We don't require the exact
    UUID4 shape the server mints — some callers use other opaque ids — only that
    the value is ONE path component with no traversal: rejecting ``..``, ``.``,
    empty strings, and anything containing ``/`` or ``\\`` (or a drive/root
    prefix) is enough to keep the join inside the output tree. Route handlers map
    the ``ValueError`` onto an HTTP 400.
    """
    # Reject both separators explicitly (``Path.name`` only treats the
    # native one as a separator, so a backslash slips through on POSIX even
    # though it IS a separator on the Windows deployment target — gotcha #1).
    if (
        not isinstance(session_id, str)
        or session_id in ("", ".", "..")
        or "/" in session_id
        or "\\" in session_id
        or session_id != Path(session_id).name
    ):
        raise ValueError(f"invalid session_id: {session_id!r}")
    return session_id


# Project root is two levels up from this file: utils/paths.py → repo/.
# Anything under it is a legal write target.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Temp directories are also legal — pytest's `tmp_path` fixture lives
# under tempfile.gettempdir(), and any caller writing under
# tempfile.mkdtemp() or similar is by construction not targeting a
# sensitive location. We collect the union of plausible POSIX temp
# roots because:
#   - macOS has TWO: /var/folders/... (per-user, what gettempdir()
#     returns) AND /tmp (POSIX-traditional, used by some legacy tests
#     and most CI containers)
#   - Linux has /tmp + /var/tmp + sometimes a per-user override
# Resolve once at import to avoid symlink shenanigans at validate time.
_TEMP_ROOTS: tuple[Path, ...] = tuple(
    sorted(
        {
            Path(tempfile.gettempdir()).resolve(),
            Path("/tmp").resolve() if Path("/tmp").exists() else None,
            Path("/var/tmp").resolve() if Path("/var/tmp").exists() else None,
        } - {None}  # type: ignore[arg-type]
    )
)


class UnsafePathError(PermissionError):
    """A file path outside every approved folder. An OSError, so the existing
    file-error handling around each operation applies to it too."""


# Folders named on a command line for this process. Web requests never add here.
_OPERATOR_ROOTS: list[Path] = []


def operator_entry_path(path: Union[str, os.PathLike]) -> Path:
    """``operator_path`` for deleting, renaming or replacing: the same rule as
    ``safe_entry_path``, without the folder limit."""
    candidate = Path(os.fspath(path)).expanduser()
    return _entry_under(operator_path(candidate.parent), candidate.name, path)


def approve_operator_folder(path: Union[str, os.PathLike]) -> Path:
    """Approve a folder someone chose with a command-line option such as
    ``--output-dir``. Lasts for this process only."""
    resolved = operator_path(path)
    _OPERATOR_ROOTS.append(resolved)
    return resolved


def _is_under_safe_root(resolved: Path) -> bool:
    roots = [PROJECT_ROOT, *_TEMP_ROOTS]
    # Read at call time: an operator's folders can sit outside the project, as
    # on Azure App Service (/home/data).
    configured = os.environ.get("XBRL_OUTPUT_DIR")
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    roots.extend(_OPERATOR_ROOTS)
    return any(resolved.is_relative_to(root) for root in roots)


def safe_path(path: Union[str, os.PathLike]) -> Path:
    """Return ``path`` fully resolved, or raise UnsafePathError if it leaves the
    approved folders. App file operations go through this; deletes, renames and
    replaces use ``safe_entry_path``.

    Symlinks and ``..`` resolve first, so neither can step outside a root.
    """
    text = os.fspath(path)
    if "\x00" in text:
        raise UnsafePathError(f"File path contains a null byte: {text!r}")
    resolved = Path(text).expanduser().resolve()
    if not _is_under_safe_root(resolved):
        raise UnsafePathError(f"File path is outside the approved folders: {resolved}")
    return resolved


def _entry_under(parent: Path, name: str, original: object) -> Path:
    if name in ("", ".", ".."):
        raise UnsafePathError(f"File path has no file name: {original!r}")
    return parent / name


def safe_entry_path(path: Union[str, os.PathLike]) -> Path:
    """``safe_path`` for deleting, renaming or replacing. Checks the containing
    folder but keeps the last step as it is, so the operation acts on a symbolic
    link itself and never on the file the link points to."""
    candidate = Path(os.fspath(path)).expanduser()
    return _entry_under(safe_path(candidate.parent), candidate.name, path)


def safe_db_target(path: Union[str, os.PathLike]) -> str:
    """``safe_path`` for sqlite3.connect, which also accepts an in-memory name."""
    text = os.fspath(path)
    return text if text == ":memory:" else str(safe_path(text))


def operator_path(path: Union[str, os.PathLike]) -> Path:
    """Resolve a path the person running a command-line tool typed. There is no
    folder limit: they already have this machine's access, and pointing the
    tool at their own file is its job. Never use this for web request data."""
    text = os.fspath(path)
    if "\x00" in text:
        raise UnsafePathError(f"File path contains a null byte: {text!r}")
    return Path(text).expanduser().resolve()


def assert_writable_output_dir(
    output_dir: Union[str, Path],
    label: str = "output_dir",
) -> Path:
    """Resolve `output_dir` and confirm it lives under a safe root.

    Returns the resolved Path on success so callers can use a
    canonical path for their writes (no .. games left to play). Raises
    ValueError when the path escapes both safe roots, so the caller
    fails loudly rather than silently writing into /etc or $HOME.

    Allowed roots:
      - PROJECT_ROOT — production case (output/<session_id>).
      - OS temp dir — tests + ad-hoc tooling that uses tempfile.
      - XBRL_OUTPUT_DIR — the operator's configured output root, which on
        Azure App Service sits outside the project (/home/data).

    Symlinks resolve before the comparison so a symlink under output/
    that points elsewhere is still rejected.
    """
    resolved = Path(output_dir).expanduser().resolve()
    if _is_under_safe_root(resolved):
        return resolved

    raise ValueError(
        f"{label}={output_dir!r} resolves to {resolved} which is "
        f"outside PROJECT_ROOT ({PROJECT_ROOT}), XBRL_OUTPUT_DIR and all temp "
        f"roots ({list(_TEMP_ROOTS)}). Refusing to write — this is almost "
        f"certainly a bug."
    )
