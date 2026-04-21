"""Filesystem-path safety helpers.

Today's call sites trust their `output_dir` argument because it
originates server-side (server.py: ``OUTPUT_DIR / session_id`` where
session_id is a UUID, run.py: similar pattern). The helpers here are
defence-in-depth so a single upstream bug or a future caller can't
escalate into path traversal + blind directory creation against
sensitive locations like /etc, $HOME/.ssh, etc.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Union


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

    Symlinks resolve before the comparison so a symlink under output/
    that points elsewhere is still rejected.
    """
    resolved = Path(output_dir).expanduser().resolve()

    def _under(parent: Path) -> bool:
        try:
            return resolved.is_relative_to(parent)
        except AttributeError:
            # Defensive: very old Python — manual prefix check.
            return str(resolved).startswith(str(parent))

    if _under(PROJECT_ROOT):
        return resolved
    for temp_root in _TEMP_ROOTS:
        if _under(temp_root):
            return resolved

    raise ValueError(
        f"{label}={output_dir!r} resolves to {resolved} which is "
        f"outside PROJECT_ROOT ({PROJECT_ROOT}) and all temp roots "
        f"({list(_TEMP_ROOTS)}). Refusing to write — this is almost "
        f"certainly a bug."
    )
