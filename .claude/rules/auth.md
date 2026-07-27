---
paths:
  - "auth/**"
  - "web/src/pages/LoginPage*"
  - "web/src/pages/SettingsPage*"
  - "tests/test_auth_*.py"
  - "tests/test_admin_routes.py"
  - "tests/test_manage_users.py"
  - "tests/test_change_password.py"
  - "tests/test_db_schema_v18.py"
  - "tests/test_db_schema_v20.py"
---
# Auth layer (gotcha #24)

> Extracted verbatim from the root CLAUDE.md (2026-07-25 context-slimming pass).
> This file is the authoritative detail for its gotchas; the root CLAUDE.md keeps
> a summary stub pointing here. Keep the two in sync the same way you would any
> other cross-file invariant (docs/SYNC-MATRIX.md).

### 24. Auth layer gates every `/api/*` route (schema v18)


The `auth/` package (`config`, `middleware`, `sessions`, `lockout`,
`passwords`, `routes`, `manage`) + `web/src/pages/LoginPage.tsx` add
email+password login (PLAN-azure-auth-deployment Phase 1). The DB side is
gotcha #11 (v18 `auth_users` / `auth_sessions`); the operational invariants:

- **`AUTH_MODE=dev` is required to run the test suite.** The middleware guards
  **every** `/api/*` route (exempt: prefix `/api/auth/*`, exact `/api/health`).
  `tests/conftest.py` defaults the whole suite into `AUTH_MODE=dev` (auto-session
  as `dev@localhost`, no login form) so pre-auth tests don't 401; auth-specific
  tests opt OUT with `monkeypatch.delenv("AUTH_MODE")`. **Running pytest with
  `AUTH_MODE` unset makes API-hitting tests 401.**
- **Production fails fast on misconfig.** `SESSION_SECRET` is mandatory in prod
  (startup refuses to boot without it; dev falls back to an insecure constant).
  A startup guard also **refuses to boot in `AUTH_MODE=dev` under production**
  (`WEBSITE_SITE_NAME` present) so dev-mode can never ship to Azure.
- **Sessions are server-side + revocable** (`auth_sessions` row, not a stateless
  JWT) with a **15-min sliding idle timeout** (`AUTH_IDLE_TIMEOUT_S`); the SPA
  keeps it alive via `/api/auth/refresh`. Brute-force lockout is per `(email, IP)`
  — 5 attempts → 15-min lock (`AUTH_LOGIN_MAX_ATTEMPTS` / `AUTH_LOGIN_LOCKOUT_S`).
- **Accounts = the email allowlist.** Provision with
  `python -m auth.manage add-user you@firm.com --name "Your Name"` (add `--admin`
  to mint an admin). There is no self-signup. Azure provisioning is still TODO.
- **Admin role + web user management (schema v20).** `auth_users.is_admin` is the
  privilege boundary. The CLI gained `--admin` / `make-admin` / `revoke-admin`
  (with a **last-admin guard** — refuses to demote/disable the only enabled
  admin); admin #1 is minted there since the admin UI is admin-gated. Web side:
  `/api/auth/me` reports `is_admin`; `/api/admin/users` (list/add/disable/enable/
  reset-password/promote) each independently enforce `is_admin` server-side via
  `_require_admin` (the hidden UI tab is NOT the boundary) and carry the same
  409 last-admin guard; `/api/auth/change-password` is self-service (re-auths
  with the current password). Frontend: the gear opens a consolidated **`/settings`
  page** (`SettingsPage.tsx`, `AppView "settings"`) with three tabs — **General**
  (the old model/proxy/run-defaults form, extracted into `GeneralSettingsForm`;
  `SettingsModal` is now a thin wrapper around it), **Account** (change password),
  **Users** (admin-only). Pinned by `tests/test_admin_routes.py`,
  `test_change_password.py`, `test_auth_me_reports_admin.py`,
  `test_db_schema_v20.py`, and `web` `SettingsPage`/`AccountTab`/`UsersTab` tests.

Pinned by `tests/test_auth_middleware.py`, `test_auth_password.py`,
`test_auth_sessions.py`, `test_auth_lockout.py`,
`test_auth_prod_requires_users.py`, `test_manage_users.py`,
`test_db_schema_v18.py`.
