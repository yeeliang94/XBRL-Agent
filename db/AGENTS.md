# db/ — Agent Rules

These rules add to the root `AGENTS.md`. Detail: invariant 11 in
`CLAUDE-REFERENCE.md`.

- Database migrations advance one version at a time and remain idempotent.
  Read `db/schema.py::CURRENT_SCHEMA_VERSION`; do not cache its value here.
- Preserve documented inert fields and tables so every older database can
  migrate forward.
- A schema change also regenerates `db/mssql_baseline.py`
  (`scripts/export_mssql_schema.py`) and adds the matching SQL Server step to
  `db/mssql.py::MIGRATIONS`. Pinned by `tests/test_mssql_schema.py`.
