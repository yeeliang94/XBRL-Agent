# PLAN — Authentication Layer + Azure App Service Deployment

Status: **proposed** (no code written yet). Authored 2026-06-11 from a
requirements conversation with the project owner.

## Requirements (as agreed)

| Decision | Choice |
|---|---|
| Hosting | Azure App Service (Linux) |
| Login methods | SSO only — **Microsoft** and **Google** buttons (no username/password) |
| Users | Small known team; access controlled by an **email allowlist** |
| Session | Auto-logout after **15 minutes of inactivity** (sliding window) |
| Data sensitivity | Real client financial statements (confidential) |
| Region | Southeast Asia (Singapore) — closest Azure region to Malaysia |
| Local development | Must still be able to log in / bypass safely on localhost |

Username/password was explicitly dropped: storing passwords is the
highest-risk part of any auth system, and with a small SSO-capable team it
buys nothing. If a password tier is ever needed, prefer adding **Microsoft
Entra External ID** (Microsoft hosts the passwords) over rolling our own.

## Architecture decision: in-app OIDC, not Easy Auth alone

Two candidate designs were considered:

1. **Azure Easy Auth** (App Service's built-in authentication): zero code,
   but it cannot express this feature set — with two providers it needs a
   custom provider-picker page anyway, its session cookie has no sliding
   15-minute inactivity semantics, the allowlist still needs app-side
   enforcement, and none of it exists on localhost.
2. **In-app OIDC layer** (chosen): a small auth module inside the FastAPI
   app using [`authlib`](https://docs.authlib.org/) for the two OAuth2/OIDC
   flows, server-side sessions in SQLite, and a React login page. Identical
   behaviour locally and on Azure; full control of the timeout and
   allowlist.

Easy Auth remains available later as optional defence-in-depth in front of
the app (Phase 4), but the app must not depend on it.

Because we never store passwords, the security-critical surface is small:
cookie handling, the OIDC redirect/callback, and the allowlist check.

## What to be aware of (this app on App Service)

These flow from invariants already documented in CLAUDE.md:

- **Single instance, single worker — non-negotiable.** State is SQLite at
  `output/xbrl_agent.db` plus on-disk run artifacts, with in-process run
  registries and background re-review threads (gotchas #6, #11, #21). The
  App Service plan must be pinned to **1 instance**, and the startup
  command must run **one** uvicorn worker (no gunicorn multi-worker).
- **Persistent storage.** Only `/home` survives restarts on App Service.
  `server.py` hardcodes `OUTPUT_DIR = BASE_DIR / "output"` — Phase 3 adds
  an `XBRL_OUTPUT_DIR` env override so DB + uploads + filled workbooks live
  under `/home/data`. Requires `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`.
- **Always On.** Extraction runs and the re-review background thread die if
  the app is idled out. Requires Basic tier or above.
- **SSE idle timeout.** Azure's front end drops responses silent for ~230 s.
  The run stream has known quiet stretches (the "silent dead zones" that
  pipeline_stage events label, gotcha #19) — add a `: keepalive` SSE
  comment every ~25 s to the event-queue drain loop. Comments are invisible
  to `EventSource`, so no frontend change.
- **LLM mode on Azure: direct, not proxied.** `start.sh`/LiteLLM is the Mac
  dev convenience; on Azure leave `LLM_PROXY_URL` empty and set provider
  keys directly (CLAUDE.md "LLM Provider Setup").
- **Confidential-data reality check:** regardless of where the app is
  hosted, uploaded PDFs are sent to the configured LLM provider
  (OpenAI/Anthropic/Google) for extraction. Hosting in Azure does not change
  that. If client confidentiality terms require it, point proxy mode at an
  approved enterprise endpoint (e.g. Azure OpenAI via LiteLLM, or the PwC
  shared-service proxy) — out of scope here, but it is the single biggest
  data-governance lever.
- **Runs outlive sessions.** A logged-out (timed-out) user's in-flight run
  keeps going server-side; they log back in and find it in History. This is
  the desired behaviour and needs no extra code — just stating it.

## Phase 1 — In-app auth layer (buildable + testable locally, before any Azure work)

### 1.1 Session store

- New table `auth_sessions` (schema **v17**, following the gotcha #11
  walk-forward pattern; pure `CREATE TABLE IF NOT EXISTS`):
  `session_id (PK, random 256-bit)`, `email`, `display_name`, `provider`,
  `created_at`, `last_seen_at`.
- Cookie: `__Host-xbrl_session` (falls back to a plain name on `http://`
  localhost), `HttpOnly`, `SameSite=Lax`, `Secure` when the request is
  https. Value = opaque session id, HMAC-signed with `SESSION_SECRET`.
  Server-side sessions (not stateless JWT) so logout/timeout are
  enforceable and revocable.
- **Sliding 15-minute expiry:** every authenticated request compares
  `now − last_seen_at`; > 15 min ⇒ 401 + session row deleted. Configurable
  via `AUTH_IDLE_TIMEOUT_S` (default 900).
- **What counts as activity:** any API request **except** a denylist of
  background traffic that would otherwise keep a session alive forever:
  the SSE stream itself, `GET /api/runs/{id}/re-review/status` polls, and
  any other poll endpoints. Those are still *authenticated* (401 if
  expired) but do not bump `last_seen_at`. The frontend additionally sends
  a throttled `POST /api/auth/refresh` on real user input (mouse/key,
  ≤ 1/min) so "watching a long run while moving the mouse" stays logged in
  and "left the tab open overnight" does not.

### 1.2 Backend endpoints + middleware (`auth/` package)

- `GET /api/auth/login/{provider}` — provider ∈ {`microsoft`, `google`};
  authlib redirect with `state` + PKCE.
- `GET /api/auth/callback/{provider}` — exchanges code, reads the verified
  email claim, **checks the allowlist**, creates the session, redirects to
  `/`. Non-allowlisted email ⇒ friendly "not authorised" page, no session.
- `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/refresh`.
- Starlette middleware guarding **every `/api/*` route** (downloads, SSE,
  uploads, everything) except `/api/auth/*` and `/api/health`. Static SPA
  assets stay public (they contain no data); the SPA itself redirects to
  the login page when `/api/auth/me` returns 401.
- Allowlist: `AUTH_ALLOWED_EMAILS` env (comma-separated, case-insensitive).
  Deliberately env-based, not a DB+UI — a small fixed team changes this via
  an App Setting edit. A DB-backed admin page is a later nice-to-have.
- Microsoft: `common` endpoint (work/school **and** personal accounts) —
  the allowlist, not the tenant, is the gate. Google: standard OIDC.
- **Fail-closed config check:** in production mode, missing
  `SESSION_SECRET` / client IDs+secrets aborts startup with a clear error
  (mirrors the canonical-bootstrap fail-fast philosophy, gotcha #21).

### 1.3 Local development (the "how do I log in locally?" answer)

Two supported modes, explicit via `AUTH_MODE`:

- `AUTH_MODE=oidc` (default): real SSO works on localhost too — both
  Microsoft and Google allow `http://localhost:8002/api/auth/callback/…`
  redirect URIs on dev app registrations. Same code path as production.
- `AUTH_MODE=dev`: auto-session as `dev@localhost`, no IdP needed
  (offline work, CI, demos). **Guard:** dev mode refuses to start when
  `WEBSITE_SITE_NAME` is present (i.e. running on App Service) — pinned by
  test so the bypass can never ship to Azure.

### 1.4 Frontend

- `LoginPage.tsx`: two buttons ("Sign in with Microsoft", "Sign in with
  Google") linking to the login endpoints. Inline styles + `theme.ts`
  tokens (gotcha #7).
- App boot: call `/api/auth/me`; 401 ⇒ render LoginPage instead of the app.
  A small fetch/EventSource wrapper treats any 401 as "session expired ⇒
  show login" so timeout mid-use degrades gracefully.
- Idle tracker (throttled refresh ping) + optional "you'll be logged out
  in 1 minute" toast.
- Header: signed-in email + Logout.

### 1.5 Tests (the "done" bar, per CLAUDE.md)

- `tests/test_auth_sessions.py` — sliding expiry, denylisted endpoints
  don't refresh, logout revokes, cookie flags.
- `tests/test_auth_middleware.py` — every `/api/*` route 401s without a
  session (walk the route table programmatically so new endpoints can't
  ship unguarded); `/api/auth/*` + health exempt.
- `tests/test_auth_allowlist.py` — case-insensitivity, deny path creates
  no session.
- `tests/test_auth_dev_mode.py` — dev bypass works locally, **fails fast
  under `WEBSITE_SITE_NAME`**.
- `tests/test_db_schema_v17.py` — migration walk-forward.
- Frontend: LoginPage render, 401→login redirect, idle-ping throttle.

## Phase 2 — Identity provider registrations (one-time, ~30 min, no code)

Step-by-step for the owner (each produces a client ID + secret):

1. **Microsoft Entra ID** (portal.azure.com → App registrations → New):
   account type "Accounts in any organizational directory and personal
   Microsoft accounts"; web redirect URIs
   `http://localhost:8002/api/auth/callback/microsoft` and
   `https://<app>.azurewebsites.net/api/auth/callback/microsoft`;
   create a client secret (note its **expiry date** — calendar reminder to
   rotate, default max 24 months).
2. **Google** (console.cloud.google.com → APIs & Services → Credentials →
   OAuth client ID, type Web): same two redirect URIs with `/google`;
   configure the consent screen; since this is a small fixed team, keep
   the app in "Testing" and add the team as test users (avoids Google's
   verification review) — or publish if preferred.
3. Collect: `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`, and the team's `AUTH_ALLOWED_EMAILS`.

## Phase 3 — Azure provisioning + deployment

1. **Resources** (portal or `az` CLI): resource group `rg-xbrl-agent`
   in **Southeast Asia**; Linux App Service plan — start **B2**
   (3.5 GB RAM; B1's 1.75 GB is tight for PyMuPDF + concurrent agents),
   ~US$25–35/month, upgradeable in place; Web App, Python 3.12.
2. **Configuration** (App Settings — these are the production `.env`):
   provider API keys, `SESSION_SECRET` (random 64 hex chars), the four
   OIDC values, `AUTH_ALLOWED_EMAILS`, `AUTH_MODE=oidc`,
   `XBRL_OUTPUT_DIR=/home/data`, `PYTHONUTF8=1`,
   `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`, `TEST_MODEL`/`SCOUT_MODEL`,
   `LLM_PROXY_URL` empty (direct mode).
3. **Platform settings:** Always On = on; scale out **fixed at 1
   instance**; HTTPS Only = on; min TLS 1.2; FTP disabled.
4. **Code changes in this phase:** `XBRL_OUTPUT_DIR` env override for
   `OUTPUT_DIR`/`AUDIT_DB_PATH`; SSE keepalive comments; startup command
   `uvicorn server:app --host 0.0.0.0 --port 8000` (single worker — see
   single-instance invariant) wired so `mount_spa` still runs.
5. **CI/CD:** GitHub Actions workflow — build the frontend
   (`cd web && npm ci && npm run build`), run backend + frontend tests,
   deploy via `azure/webapps-deploy` with a publish profile (or OIDC
   federated credentials). Deploy on push to `main` only.
6. **Smoke checklist:** login via both providers; non-allowlisted account
   rejected; 15-min timeout fires; upload→extract→download a sample PDF;
   SSE survives a long run; History persists across an app restart
   (proves `/home/data` wiring); `/api/*` without a cookie ⇒ 401.

## Multi-environment workflow (Mac → personal GitHub → enterprise Windows + Azure)

The owner's actual day-to-day topology, and how auth behaves in each spot:

```
Mac (develop) ──push──▶ personal GitHub ──GitHub Actions──▶ Azure App Service  (production, AUTH_MODE=oidc)
                              │
                          git pull
                              ▼
                   enterprise Windows machine  (local use only, AUTH_MODE=dev)
```

Principles that make this work with zero per-machine code changes:

- **One codebase, per-environment config.** All differences live in `.env`
  (local) / App Settings (Azure), never in code. `.env` stays gitignored;
  ship a `.env.example` documenting every auth variable. The same `git pull`
  on Windows keeps working exactly as today — deployment to Azure is a
  *parallel* consumer of the GitHub repo (Actions fires on push to `main`),
  not a change to the pull workflow.
- **`main` = deployable.** Day-to-day work happens on branches; merging to
  `main` is what triggers the Azure deploy. This is the one workflow-habit
  change: don't push half-finished work to `main` once Actions is wired.
- **Enterprise Windows runs `AUTH_MODE=dev`.** SSO there is unnecessary
  (the machine is the access control; the app binds to localhost) and
  unreliable: the corporate proxy blocks direct Google calls (403 — see
  docs/PORTING-WINDOWS.md) and would MITM the OIDC token exchange. Dev mode
  sidesteps all of it. The `WEBSITE_SITE_NAME` guard (§1.3) still ensures
  dev mode can never reach Azure.
- **If real SSO on Windows is ever wanted:** Microsoft login should work on
  the corporate network (it's the same Entra infrastructure as M365);
  Google likely stays blocked. `localhost` redirect URIs are
  machine-independent — the same `http://localhost:8002/...` registration
  covers Mac *and* Windows. The outbound token-exchange call needs the
  corporate proxy env vars + the existing `truststore` injection (gotcha
  #5), which already handles the MITM root CA.
- **Secrets placement per environment:** Mac `.env` (dev IdP secrets,
  optional), Windows `.env` (none needed in dev mode), GitHub repo secrets
  (only the Azure deploy credential), Azure App Settings (everything
  production). The IdP client secrets and `SESSION_SECRET` exist **only**
  in Azure App Settings — they are never needed on either laptop unless
  testing the real OIDC flow locally.

> **Governance flag (raise-once, owner's call):** production will be
> client-confidential data running on a *personal* Azure subscription,
> deployed from a *personal* GitHub repo, with the enterprise machine
> pulling from it. Depending on firm policy that combination may need
> sign-off; moving later means re-pointing GitHub Actions at an enterprise
> subscription — the app itself doesn't change.

## Phase 4 — Hardening (after it works)

- **Key Vault references** for secrets instead of plain App Settings.
- **Backups:** nightly snapshot of `/home/data` (SQLite + artifacts) to a
  Blob Storage container with soft delete (a small WebJob/cron or Azure
  Backup for App Service).
- **Monitoring:** Application Insights (failures, response times), alert
  on 5xx spike; App Service log stream for live debugging.
- Optional: custom domain; Easy Auth in front as a second layer; access
  restrictions (IP allowlist / Private Endpoint) if the client data
  posture demands it; secret-rotation calendar (MS client secrets expire).

## Cost ballpark

App Service B2 ≈ US$25–35/month (Southeast Asia pricing). Application
Insights/Storage add single-digit dollars. LLM API usage remains the
dominant variable cost, unchanged by hosting.

## Out of scope (explicitly)

- Username/password accounts, self-signup, password reset.
- Multi-instance scale-out (would require Postgres + blob storage — a
  separate plan).
- Routing LLM traffic through Azure OpenAI (flagged above as the main
  confidentiality lever; decide separately).
