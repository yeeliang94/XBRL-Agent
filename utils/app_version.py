"""Resolve the running application's version, once, for run provenance.

Every run is stamped with this so the Evals workspace can answer "are we better
over time?" — a trend line is meaningless if you can't tell which build produced
each point (docs/PLAN-evals-workspace.md, Step A2).

Resolution order (first hit wins), cached for the process lifetime:

1. ``XBRL_APP_VERSION`` env var — the deployment escape hatch (Windows/Azure,
   where there is no git checkout). A build step writes it.
2. A ``VERSION`` file at the repo root — the other build-time stamp option.
3. ``git describe`` on the working tree — the dev-box path.
4. ``"unknown"`` — never raises; a missing version must not break a run.
"""
from __future__ import annotations

import functools
import os
import subprocess
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
        files = sorted((base / "prompts").rglob("*.md"))
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
def get_app_version() -> str:
    """The resolved version string. Cached — resolves at most once per process."""
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

    git = _from_git()
    if git:
        # A dirty tree is ambiguous — many different uncommitted prompt states
        # share one describe string. Disambiguate with the prompt-state hash;
        # clean builds keep the stable describe output untouched.
        if git.endswith("-dirty"):
            ph = prompt_state_hash()
            if ph:
                return f"{git}+p{ph}"
        return git

    return "unknown"
