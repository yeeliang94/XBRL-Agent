"""SQLite-backed audit trail for multi-statement XBRL runs.

Why: the Phase 2 rollout needs one durable store for runs, per-agent tool
events, extracted fields, and cross-check outcomes — so that re-opening the
UI shows historical runs and cross-agent checks have a place to read from.
File-based SQLite keeps deployment identical on Mac and Windows (no daemon)
and avoids adding a dependency.

Entry points: `db.schema.init_db(path)` creates the tables; helpers in
`db.repository` wrap typed reads/writes.
"""
from .schema import init_db  # noqa: F401  re-exported for callers
