"""Where the live database file sits.

On a laptop or the Windows machine it is ``<output directory>/xbrl_agent.db``,
as it always was. On Azure App Service the output directory is the ``/home``
network drive, which cannot hold the locks a live SQLite file needs. There the
live file sits on the container's own disk, and ``db/copies.py`` saves copies
of it onto ``/home``.

The server and ``auth/manage.py`` both ask here, so they always open the same
file.
"""
from __future__ import annotations

from pathlib import Path

DB_FILENAME = "xbrl_agent.db"
# Fixed, not tempfile.gettempdir(): an SSH shell into the container must find
# the same file as the server, whatever its TMPDIR says.
AZURE_LIVE_DIR = Path("/tmp/xbrl-live")


def on_azure() -> bool:
    from auth import config as auth_config

    return auth_config.is_production()


def live_database_path(output_dir: str | Path) -> Path:
    if on_azure():
        return AZURE_LIVE_DIR / DB_FILENAME
    return Path(output_dir) / DB_FILENAME


def copies_dir(output_dir: str | Path) -> Path:
    return Path(output_dir) / "db-copies"


def check_live_location(path: str | Path) -> None:
    """Refuse to run a live database on Azure's ``/home`` network drive."""
    if not on_azure():
        return
    # As written and as resolved: either may name /home (on macOS /home
    # resolves elsewhere, on Azure a link could point into it).
    candidates = (Path(path).absolute(), Path(path).resolve())
    if any(len(c.parts) > 1 and c.parts[1] == "home" for c in candidates):
        raise RuntimeError(
            f"The live database {path} is on Azure's /home network drive, which "
            "cannot lock it. It must sit on the container's own disk.")
