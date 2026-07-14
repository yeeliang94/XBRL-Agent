"""Resolve the running application's version, once, for run provenance.

Every run is stamped with this so the Evals workspace can answer "are we better
over time?" — a trend line is meaningless if you can't tell which build produced
each point (docs/PLAN-evals-workspace.md, Step A2).

Resolution order (first hit wins):

1. ``XBRL_APP_VERSION`` env var — the deployment escape hatch (Windows/Azure,
   where there is no git checkout). A build step writes it. Cached.
2. A ``VERSION`` file at the repo root — the other build-time stamp option. Cached.
3. ``git describe`` on the working tree — the dev-box path. Recomputed (behind
   a short TTL, with a prompt-state hash on a dirty OR untracked tree) so two
   prompt experiments run in one process are told apart.
4. ``"unknown"`` — never raises; a missing version must not break a run.
"""
from __future__ import annotations

import functools
import os
import subprocess
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _from_git() -> str | None:
    """`git describe --tags --always --dirty`, or None if git isn't available.

    Runs with a short timeout and swallows every failure — a run must never
    wait on or crash from version resolution.
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


def _git_has_local_changes() -> bool:
    """True when the worktree has ANY local change — tracked mods, staged, OR
    untracked files. `git describe --dirty` only sees TRACKED changes, so a
    brand-new untracked prompt file wouldn't flip it (peer-review Step 16); a
    non-empty `git status --porcelain` catches that case too."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return bool(out.stdout.strip())


def prompt_state_hash(root: Path | None = None) -> str | None:
    """Short content hash over the behaviour-bearing surface git-describe can't
    see (PLAN-evals-hardening Step 16): every prompt file + the pricing/model
    registry. Two uncommitted prompt experiments both read ``...-dirty`` from
    git — this suffix tells their trend points apart. Returns None on any I/O
    failure (provenance must never break a run)."""
    import hashlib

    base = root or _REPO_ROOT
    h = hashlib.sha256()
    try:
        # Both .md prompt files AND the .py modules that build prompt blocks
        # inline (prompts/__init__.py, _sign_conventions.py, …) — an edit to
        # either changes extraction behaviour.
        files = sorted(
            list((base / "prompts").rglob("*.md"))
            + list((base / "prompts").rglob("*.py"))
        )
        files.append(base / "pricing.py")
        found = False
        for f in files:
            if f.is_file():
                found = True
                h.update(str(f.relative_to(base)).encode())
                h.update(f.read_bytes())
        if not found:
            return None
        return h.hexdigest()[:8]
    except OSError:
        return None


@functools.lru_cache(maxsize=1)
def _static_build_stamp() -> str | None:
    """A STABLE build stamp — the env var or VERSION file a build step writes.
    Cached for the process: these can't change under a running server, and a
    fixed prod version is exactly what we want stamped on every run."""
    env = os.environ.get("XBRL_APP_VERSION", "").strip()
    if env:
        return env
    version_file = _REPO_ROOT / "VERSION"
    try:
        if version_file.is_file():
            text = version_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    return None


# The git-derived version is recomputed so two prompt experiments in one
# process get DIFFERENT stamps, but each recompute costs two git subprocesses
# plus a hash over the whole prompt tree. A short TTL caps that for callers
# that hit it often (/api/config on every page load) while staying far below
# the minutes that separate two real prompt experiments.
_GIT_CACHE_TTL_S = 5.0
_git_cache: tuple[float, str] | None = None


def _git_derived_version() -> str:
    """The dev-box version: git describe, disambiguated on a dirty tree."""
    git = _from_git()
    if not git:
        return "unknown"
    # A dirty/untracked tree is ambiguous — many uncommitted prompt states
    # share one describe string. Disambiguate with the prompt-state hash;
    # clean builds keep the stable describe output untouched. `--dirty` only
    # sees tracked edits, so also consult `git status` for untracked files.
    if git.endswith("-dirty") or _git_has_local_changes():
        base = git[: -len("-dirty")] if git.endswith("-dirty") else git
        ph = prompt_state_hash()
        # Even if the hash fails, keep the honest -dirty marker — a clean
        # describe over untracked changes must not read as a clean build.
        return f"{base}-dirty+p{ph}" if ph else f"{base}-dirty"
    return git


def get_app_version() -> str:
    """The resolved version string.

    A fixed build stamp (env / VERSION) is cached for the process. On a dev
    checkout it is derived from git and recomputed behind a short TTL, so two
    prompt experiments run in one process get DIFFERENT versions (peer-review
    Step 16 — the old process-lifetime cache handed them the same one). A dirty
    OR untracked worktree appends the prompt-state hash to disambiguate.
    """
    stamped = _static_build_stamp()
    if stamped:
        return stamped

    global _git_cache
    now = time.monotonic()
    if _git_cache is not None and now - _git_cache[0] < _GIT_CACHE_TTL_S:
        return _git_cache[1]
    value = _git_derived_version()
    _git_cache = (now, value)
    return value
