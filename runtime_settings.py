"""Local, operator-managed runtime settings.

The Settings page writes this JSON file instead of editing ``.env``.  Values
are applied to ``os.environ`` so existing pipeline readers keep one runtime
interface.  Environment variables remain the deployment fallback when a key
has not been saved through the UI.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Mapping, Optional


_LOCK = RLock()
_FALLBACKS: dict[str, Optional[str]] = {}
_APPLIED: dict[str, str] = {}


def read_settings(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def apply_settings(path: Path) -> dict[str, str]:
    settings = read_settings(path)
    with _LOCK:
        for key, value in settings.items():
            current = os.environ.get(key)
            # Remember the deployment/.env value underneath the local overlay.
            # A fresh load_dotenv() may update it between calls; distinguish
            # that from the value this module applied on the previous call.
            if key not in _FALLBACKS or current != _APPLIED.get(key):
                _FALLBACKS[key] = current
            os.environ[key] = value
            _APPLIED[key] = value
    return settings


def update_settings(
    path: Path, updates: Mapping[str, Optional[str]],
) -> dict[str, str]:
    """Merge and atomically persist validated settings, then apply them.

    ``None`` removes a local override and restores the deployment/.env value
    that was present underneath it. Empty strings remain ordinary values —
    they are meaningful for settings such as ``LLM_PROXY_URL``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        merged = read_settings(path)
        for raw_key, value in updates.items():
            key = str(raw_key)
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = str(value)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(merged, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        for raw_key, value in updates.items():
            key = str(raw_key)
            if value is None:
                fallback = _FALLBACKS.pop(key, None)
                _APPLIED.pop(key, None)
                if fallback is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = fallback
            else:
                current = os.environ.get(key)
                if key not in _FALLBACKS or current != _APPLIED.get(key):
                    _FALLBACKS[key] = current
                os.environ[key] = str(value)
                _APPLIED[key] = str(value)
        return merged
