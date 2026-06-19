# Implementation Plan: Settings Page + Admin User Management

**Overall Progress:** `100%` (Steps 1–9 done — all phases complete)
**Design Reference:** _None — shaped directly in the 2026-06-19 brainstorm (no separate PRD)._
**Last Updated:** 2026-06-19

> Replaces the previous (completed) PLAN.md for Full-Template Notes Review +
> Notes Node Registry — that work is at 100% and preserved in git history.

## Summary

Add a real two-tier auth role (`is_admin`) and a consolidated `/settings` page
that replaces today's settings modal. The page has four tabs — **Account**
(change my own password), **Models & Providers** (the existing API-key/model/proxy
settings, moved out of the modal), **Run defaults** (auto-review, default models,
entity-memory — already wired to `/api/settings`), and **Users** (admin-only:
list / add / disable / reset-password / promote). All user-management operations
already exist in `db/repository.py` + `auth/manage.py`; the work is exposing them
over admin-gated HTTP routes and building the UI on top, plus one schema migration.

## Key Decisions

- **Admin model: a real `is_admin` role (schema v20), not "everyone-is-admin."**
  An admin portal without a privilege boundary is "every user can disable every
  other user" — unacceptable for a finance tool. Chosen in brainstorm.
- **Migration is v20, not v19.** `CURRENT_SCHEMA_VERSION` is already `19` (notes
  template registry, last commit). The auth-role step is `v19 → v20`.
- **One settings page, four tabs — not a separate "admin portal" surface.** The
  Users tab is admin-gated; everything else is visible to all logged-in users.
- **Server-side enforcement is the boundary; the hidden tab is only UX.** Every
  `/api/admin/*` route independently checks `is_admin` and 403s otherwise.
- **First admin is minted via the CLI.** You cannot create admin #1 through an
  admin-only UI from a cold start — `auth.manage` gets a `--admin` flag and a
  `make-admin`/`revoke-admin` subcommand as the bootstrap escape hatch.
- **Reuse, don't reinvent.** Operations exist (`repo.upsert_auth_user`,
  `set_auth_user_disabled`, `list_auth_users`, `count_auth_users`,
  `passwords.hash_password/verify_password/dummy_verify`). The only genuinely new
  logic is change-my-own-password (verify current → set new).
- **Inline styles only (gotcha #7).** Reuse `theme.ts` tokens + `uiStyles.ts`
  primitives. No Tailwind. Many frontend tests assert exact RGB from `theme.ts`.
- **Tests run via `./venv/bin/python` (memory: venv_interpreter_for_tests).**
  Bare `python3` is a stale 3.9 and gives phantom import errors.

## Out of Scope (deferred, do NOT build)

Teams/orgs, granular permissions, who-changed-what audit logs, invite emails,
SSO wiring, password-reset-by-email. None block the core ask.

## Pre-Implementation Checklist
- [x] 🟩 No in-flight schema work claims v20 (committed schema was v19; v20 is free).
- [x] 🟩 `AUTH_MODE=dev` default applies to the new routes (admin-route tests opt out with `delenv`).
- [x] 🟩 `resolve_session` does NOT carry the user role → `me()`/`_require_admin` do a follow-up `fetch_auth_user` (its signature left unchanged).

---

## Tasks

### Phase 1: Schema + role foundation (backend, no UI)

- [x] 🟩 **Step 1: Add `is_admin` column — schema v20** — the privilege boundary the whole feature rests on.
  - [x] 🟩 Bump `CURRENT_SCHEMA_VERSION` 19 → 20 in `db/schema.py`.
  - [x] 🟩 Add `is_admin INTEGER NOT NULL DEFAULT 0` to the `auth_users` CREATE TABLE block (fresh-init path).
  - [x] 🟩 Add the `v19 → v20` migration block (`_V20_MIGRATION_COLUMNS` + ALTER, idempotent; same BEGIN IMMEDIATE discipline as v15/16/17).
  - [x] 🟩 Add `is_admin: bool = False` to `AuthUser` + a shared `_row_to_auth_user` helper used by both `fetch_auth_user`/`list_auth_users`.
  - [x] 🟩 Add `set_auth_user_admin(...)` **and** `count_admins(...)` (the latter feeds the last-admin guard) to `db/repository.py`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_db_schema_v20.py -v` → 5 passed. ✅
  - _Note:_ added `count_admins` (not in the original step) because the last-admin guard in Step 4/CLI needs it; the row-mapping helper reads `is_admin` defensively so a pre-migration read degrades to non-admin.

- [x] 🟩 **Step 2: CLI can mint an admin (bootstrap escape hatch)** — so admin #1 exists before any UI.
  - [x] 🟩 Added `--admin` flag to `auth.manage add-user` (SET-only — never demotes on re-run, mirroring the `disabled` rule).
  - [x] 🟩 Added `make-admin <email>` / `revoke-admin <email>` subcommands; `revoke-admin` carries the last-admin guard.
  - [x] 🟩 Added `ROLE` column to `list-users`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_manage_users.py -q` → 13 passed. ✅

### Phase 2: Backend routes

- [x] 🟩 **Step 3: Surface `is_admin` in `/api/auth/me`** — the frontend needs to know whether to render the Users tab.
  - [x] 🟩 `me()` now does a follow-up `fetch_auth_user` for the role (left `resolve_session`'s widely-used signature unchanged). Fail-closed to non-admin on a missing row.
  - [x] 🟩 `_DEV_USER` carries `is_admin: True`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_auth_me_reports_admin.py -q` → 3 passed. ✅

- [x] 🟩 **Step 4: Admin-gated user-management routes** — the operations, enforced server-side.
  - [x] 🟩 `_require_admin(conn, request)` returns the deny-response or None; dev-bypass counts as admin.
  - [x] 🟩 `GET /api/admin/users` (no hash; `_user_public` exposes `has_password` instead), `POST /api/admin/users`, `/disable`, `/enable`, `/reset-password`, `/admin` (promote/demote).
  - [x] 🟩 **Last-admin guard** (`_is_last_enabled_admin`) on disable + demote → **409** with a clear message.
  - **Verify:** `./venv/bin/python -m pytest tests/test_admin_routes.py -q` → 13 passed. ✅
  - _Note:_ shared `MIN_PASSWORD_LEN` moved to `auth/passwords.py` (canonical home; CLI now aliases it). 422 uses the numeric code to match the codebase convention (reviewer_routes) and avoid Starlette's deprecated `HTTP_422_*` alias.

- [x] 🟩 **Step 5: Self-service change-my-own-password** — the only genuinely new logic.
  - [x] 🟩 `POST /api/auth/change-password` — re-auths with `verify_password(current)` (+ `dummy_verify` on miss for flat timing), rotates on success. 401 bad-current, 422 short-new, 400 in dev-mode (no real account).
  - **Verify:** `./venv/bin/python -m pytest tests/test_change_password.py -q` → 4 passed. ✅

### Phase 3: Frontend — consolidated settings page

**Scope decision (2026-06-19):** user chose the lowest-risk option — Models &
Providers + Run defaults stay together as ONE **General** tab (they share a single
Save + one `/api/settings` POST; splitting them would rewrite tested code for no
user gain). Tabs are therefore **General · Account · Users**. The existing
`SettingsModal` body is extracted into a reusable `GeneralSettingsForm` and the
modal becomes a thin wrapper so its pinning tests (incl. exact-RGB) stay green.

- [x] 🟩 **Step 6: Extract `GeneralSettingsForm` + keep `SettingsModal` as a thin wrapper** — reuse the tested form, un-modal-ized.
  - [x] 🟩 New `web/src/components/GeneralSettingsForm.tsx` = the modal's body, no overlay, loads on mount.
  - [x] 🟩 `SettingsModal.tsx` is now a thin overlay wrapper around it.
  - **Verify:** `SettingsModal.test.tsx` → 17 passed unchanged. ✅

- [x] 🟩 **Step 7: `SettingsPage` shell + routing + gear** — the destination.
  - [x] 🟩 `web/src/pages/SettingsPage.tsx` with a WAI-ARIA tablist (roving tabindex): General · Account · Users.
  - [x] 🟩 `"settings"` added to `AppView` + `parseRouteFromPath('/settings')` + URL-sync; gear dispatches `SET_VIEW settings`; render branch added.
  - [x] 🟩 Users tab gated on `user?.is_admin`; tab content mounts only when active.
  - **Verify:** `SettingsPage.test.tsx` → 5 passed; `App.test.tsx` + `AppRouting.test.tsx` → 30 passed. ✅

- [x] 🟩 **Step 8: Account tab + Users tab UI + api helpers** — the new surfaces.
  - [x] 🟩 `AccountTab.tsx` (change-password) + `UsersTab.tsx` (table + actions + add form + inline reset).
  - [x] 🟩 `api.ts`: `changePassword` + 5 admin helpers; `AuthMe.is_admin` + `AdminUser` types.
  - **Verify:** `AccountTab.test.tsx` (4) + `UsersTab.test.tsx` (6) pass. Full suite: **717 passed**, `tsc --noEmit` clean. ✅
  - _Note (deviation):_ per the 2026-06-19 scope decision, Models & Providers + Run defaults stayed as one **General** tab (3 tabs, not 4). The `SettingsModal` file was kept (as a thin wrapper) rather than deleted, so its pinning tests stay green with zero changes — lower risk than the plan's "delete + migrate tests".

### Phase 4: Polish + cross-file sync

- [x] 🟩 **Step 9: Docs + invariants sync** — keep the context pack honest.
  - [x] 🟩 Updated CLAUDE.md gotcha #11 (version → 20; v18→v19 + v19→v20 steps) and gotcha #24 (admin role + `/api/admin/*` + change-password + `/settings` page).
  - [x] 🟩 Updated the `auth_layer_status` memory file.
  - [x] 🟩 `docs/SYNC-MATRIX.md` does **not exist** in the repo (the CLAUDE.md pointer is stale) — nothing to update.
  - **Verify:** backend `-k "auth or admin or schema or password or manage or middleware"` → 189 passed; `cd web && npx vitest run` → 717 passed; `tsc --noEmit` clean; browser-verified the `/settings` page end-to-end (General + Users tabs against the live dev backend, no console errors). ✅

---

## Rollback Plan

If something goes badly wrong:

- **Schema:** the v20 migration is purely additive (one defaulted column) — it
  never drops or rewrites data. To revert code, restore `CURRENT_SCHEMA_VERSION = 19`
  and remove the migration block; an already-migrated DB keeps the unused
  `is_admin` column harmlessly (older code never reads it). No data migration to undo.
- **Routes/UI:** all new routes are additive; revert the `app.include_router`
  additions and the `/settings` route to fall back to the existing settings modal.
  The CLI (`auth.manage`) remains a working fallback for all user management
  throughout — nothing about it is removed.
- **State to check on rollback:** verify at least one enabled admin still exists
  via `./venv/bin/python -m auth.manage list-users`, so no one is locked out. If
  the last-admin guard ever misfired, re-mint with `make-admin <email>`.
