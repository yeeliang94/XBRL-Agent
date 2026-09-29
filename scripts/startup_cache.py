"""Skip unchanged local setup steps in the startup scripts.

Each stamp lives inside the output it validates, so removing an environment or
build also removes its stamp. This module uses only the standard library.
"""

import hashlib
import os
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


class _BuildAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"src", "href"} and value and value.startswith("/assets/"):
                self.paths.append(value.lstrip("/"))


def _build_output_exists() -> bool:
    dist = ROOT / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return False
    assets = _BuildAssets()
    assets.feed(index.read_text(encoding="utf-8"))
    return all((dist / path).is_file() for path in assets.paths)


def _inputs(stage: str) -> list[Path]:
    if stage == "pip":
        return [ROOT / "requirements.txt", ROOT / "constraints.txt"]
    if stage == "npm":
        return [WEB / "package.json", WEB / "package-lock.json"]
    if stage == "build":
        root_files = [
            path for path in WEB.iterdir()
            if path.is_file() and path.suffix in {".html", ".js", ".json", ".ts", ".css"}
        ]
        source_files = [
            path for directory in (WEB / "src", WEB / "public") if directory.exists()
            for path in directory.rglob("*") if path.is_file()
        ]
        return root_files + source_files
    raise ValueError(f"unknown startup stage: {stage}")


def _stamp(stage: str) -> Path:
    if stage == "pip":
        return ROOT / "venv" / ".startup-pip"
    if stage == "npm":
        return WEB / "node_modules" / ".startup-npm"
    if stage == "build":
        return ROOT / "dist" / ".startup-build"
    raise ValueError(f"unknown startup stage: {stage}")


def _fingerprint(stage: str) -> str:
    digest = hashlib.sha256()
    for path in sorted(_inputs(stage)):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"check", "mark"}:
        print("usage: startup_cache.py check|mark pip|npm|build", file=sys.stderr)
        return 2
    action, stage = sys.argv[1:]
    try:
        fingerprint = _fingerprint(stage)
        stamp = _stamp(stage)
        if action == "check":
            if stage == "build" and not _build_output_exists():
                return 1
            return 0 if stamp.is_file() and stamp.read_text() == fingerprint else 1
        stamp.parent.mkdir(parents=True, exist_ok=True)
        temporary = stamp.with_name(stamp.name + ".tmp")
        temporary.write_text(fingerprint)
        os.replace(temporary, stamp)
        return 0
    except (OSError, ValueError) as exc:
        print(f"startup cache: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
