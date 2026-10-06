"""Read-only, run-scoped support bundles. Never archive arbitrary output files."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

_SECRET_KEY = re.compile(r"(?i)^(?:authorization|cookie|(?:[\w-]+[_-])?(?:password|secret|token|api[_-]?key|credential))$")
_SECRET_TEXT = re.compile(
    r"(?i)((?:authorization|cookie|[\w-]*(?:password|secret|token|api[_-]?key))"
    r"[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\"'\s,;&#]+"
)
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_PATTERNS = ("*_conversation_trace.json", "notes*_failures.json", "notes*_unmatched.json")


def build_diagnostics_bundle(detail, usage: dict, output_root: Path):
    """Return a temporary ZIP plus an explicit inventory of unavailable data."""
    secrets = [value for key, value in os.environ.items()
               if _SECRET_KEY.search(key) and len(value) >= 8]

    def clean(value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else clean(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)((?:bearer|basic)\s+)[\w.+/=\-]+", r"\1[REDACTED]", value)
            value = _SECRET_TEXT.sub(r"\1[REDACTED]", value)
            value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
            value = re.sub(r"(?i)([?&](?:key|api[_-]?key|access_token|token)=)[^&#\s]+", r"\1[REDACTED]", value)
            value = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", value)
        return value

    bundle = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    manifest = {
        "format_version": 1, "run_id": detail.run.id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_status": detail.run.status,
        "snapshot_note": "Saved data at export time; active runs may have unsaved activity. Traces may have expired under retention rules.",
        "contains_financial_content": True,
        "excluded": ["source documents", "workbooks", "database", "global settings", "unrelated application logs"],
        "included": [], "unavailable": [],
    }
    total_bytes = 0
    try:
        with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
            def write_json(name, payload):
                nonlocal total_bytes
                data = json.dumps(clean(payload), ensure_ascii=False, indent=2).encode("utf-8")
                if len(data) > _MAX_FILE_BYTES or total_bytes + len(data) > _MAX_BUNDLE_BYTES:
                    manifest["unavailable"].append({"file": name, "reason": "Export size limit exceeded"})
                    return
                archive.writestr(name, data)
                total_bytes += len(data)
                manifest["included"].append(name)

            write_json("run.json", asdict(detail))
            write_json("usage.json", usage)
            write_json("environment.json", {
                "app_version": detail.run.app_version, "python": sys.version,
                "platform": platform.system(), "platform_release": platform.release(),
            })
            root = output_root.resolve()
            run_dir = Path(detail.run.output_dir).resolve() if detail.run.output_dir else None
            if run_dir is None or run_dir == root or not run_dir.is_relative_to(root) or not run_dir.is_dir():
                manifest["unavailable"].append({"file": "traces/", "reason": "Run output directory unavailable or outside output root"})
            else:
                paths = sorted({path for pattern in _PATTERNS for path in run_dir.glob(pattern)})
                for path in paths:
                    name = f"traces/{path.name}"
                    if (path.is_symlink() or path.resolve().parent != run_dir
                        or re.search(r'[<>:"/\\|?*\x00-\x1f]', path.name)):
                        manifest["unavailable"].append({"file": name, "reason": "Unsafe diagnostic path"})
                        continue
                    try:
                        if path.stat().st_size > _MAX_FILE_BYTES:
                            raise ValueError("Diagnostic exceeds file size limit")
                        # Bounded reads also handle files growing during export.
                        with path.open("rb") as source:
                            data = source.read(_MAX_FILE_BYTES + 1)
                        if len(data) > _MAX_FILE_BYTES:
                            raise ValueError("Diagnostic exceeds file size limit")
                        write_json(name, json.loads(data))
                    except (OSError, ValueError, UnicodeError, RecursionError):
                        manifest["unavailable"].append({"file": name, "reason": "Diagnostic unreadable, incomplete or oversized"})
                if not paths:
                    manifest["unavailable"].append({"file": "traces/", "reason": "No saved traces or failure logs; they may have expired"})

            log_path = os.environ.get("XBRL_APP_LOG_PATH", "").strip()
            records = []
            scanned_bytes = 0
            matched_bytes = 0
            correlations = {item.correlation_id for item in detail.incidents if item.correlation_id}
            if log_path:
                path = Path(log_path).expanduser()
                # Sessions can contain reruns; match only run/support identifiers.
                for candidate in [path, *sorted(path.parent.glob(f"{path.name}.[0-9]*"))]:
                    if scanned_bytes >= _MAX_BUNDLE_BYTES:
                        manifest["unavailable"].append({"file": "application_logs.json", "reason": "Log scan size limit exceeded"})
                        break
                    try:
                        with candidate.open("rb") as source:
                            data = source.read(_MAX_FILE_BYTES + 1)
                        scanned_bytes += len(data)
                        if len(data) > _MAX_FILE_BYTES:
                            raise ValueError("Oversized log")
                        for line in data.splitlines():
                            try:
                                record = json.loads(line)
                            except (ValueError, UnicodeError, RecursionError):
                                continue
                            if not isinstance(record, dict):
                                continue
                            if (str(record.get("run_id", "")) == str(detail.run.id)
                                or record.get("correlation_id") in correlations):
                                if matched_bytes + len(line) > _MAX_FILE_BYTES:
                                    manifest["unavailable"].append({"file": "application_logs.json", "reason": "Matching log size limit exceeded"})
                                    break
                                records.append(record)
                                matched_bytes += len(line)
                    except (OSError, ValueError):
                        manifest["unavailable"].append({"file": "application_logs.json", "reason": "An application log segment is unavailable or oversized"})
            if records:
                write_json("application_logs.json", records)
            else:
                manifest["unavailable"].append({"file": "application_logs.json", "reason": "No correlated file logs available; durable activity and incidents are in run.json"})
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            archive.writestr("README.txt", "Run diagnostics for developer investigation.\n"
                             "Start with manifest.json, then run.json (agent activity, turns, incidents and pipeline events).\n"
                             "Traces can contain confidential financial content. Share only with the intended recipient.\n"
                             "Credential patterns and configured environment secrets are redacted; review content before sharing.\n")
        bundle.seek(0)
        return bundle
    except BaseException:
        bundle.close()
        raise
