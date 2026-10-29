# db/ — Agent Rules

These rules add to the root `AGENTS.md`. Detail: invariant 11 in
`CLAUDE-REFERENCE.md`.

- Database migrations advance one version at a time and remain idempotent.
  Read `db/schema.py::CURRENT_SCHEMA_VERSION`; do not cache its value here.
- Preserve documented inert fields and tables so every older database can
  migrate forward.
