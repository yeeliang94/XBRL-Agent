# PLAN — Authentication Layer + Azure App Service Deployment

Status: **Phases 1 + 3 (code) IMPLEMENTED** (2026-06-16). Phase 2 (SSO
registration) is deferred by design; the Phase 3 *manual Azure provisioning*
(resource group, App Service, App Settings) and Phase 4 (hardening) remain —
those need the enterprise subscription, not code. Authored 2026-06-11;
**revised 2026-06-16** — the owner reversed the original SSO-only decision (see
"Auth method" below). Email+password is the primary method, built first; SSO is
layered on later.

**Implemented + tested locally (🟩).**
- **Phase 1 — auth layer:** schema v18 (`auth_users` + `auth_sessions`), the
  `auth/` package (passwords, sessions, lockout, middleware, routes, admin
  CLI), server wiring (gate middleware, fail-closed startup checks, router +
  `/api/health`), and the frontend (LoginPage, boot gate, idle ping, header
  logout).
- **Phase 3 — deploy code:** `XBRL_OUTPUT_DIR` override (durable state →
  `/home/data`), SSE `: keepalive` comments + **mid-stream session expiry**
  (now built — see below), `--proxy-headers` startup command, and
  `scripts/deploy_azure.bat` (guarded build → test → stamp → zip → `az webapp
  deploy`).

Backend + frontend suites green. **One deliberate deviation remaining:**

- **Frontend boot is OPTIMISTIC, not blank-until-resolved (§1.4).** The shell
  renders immediately and flips to the login page once `/api/auth/me` (or any
  401) reports anonymous, rather than holding a blank first paint. Same
  security outcome (no data renders — every data call is server-gated; a 401
  flips to login) and it keeps the existing synchronous App tests valid.

**SSE mid-stream expiry — now implemented (was deferred).** Done at the HTTP
layer (`server.sse_stream_with_keepalive`), wrapping the run generator with a
persistent-task keepalive loop so the core drain loop is untouched. It uses a
non-cancelling in-flight `__anext__` (asyncio.wait, not wait_for — wait_for
would cancel the pull each tick and corrupt the generator), checks expiry on
each tick, emits `session-expired`, and hands a closed stream to a background
drain so the run still finishes server-side ("runs outlive sessions"). Pinned
by `tests/test_sse_keepalive.py`; the frontend drops to login on the
`session-expired` event.

**Manual Azure steps still required (not code):** Phase 3.0–3.3 + 3.6 — create
the resource group + App Service in Southeast Asia, set the App Settings
(`SESSION_SECRET`, provider keys, `XBRL_OUTPUT_DIR=/home/data`,
`WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`, Always On, 1 instance), seed
accounts with the CLI over SSH/Kudu, and run the smoke checklist.

## Requirements (as agreed)

| Decision | Choice |
|---|---|
| Hosting | Azure App Service (Linux) in the **company/enterprise Azure subscription**; deploys run manually from the enterprise Windows laptop |
| Login methods | **Email + password** (primary, built first — works everywhere incl. local dev and Azure prod); **enterprise SSO (Microsoft Entra)** layered on **later**, tested on the Windows laptop. Google SSO deferred indefinitely. |
| Users | Small known team; **per-user accounts** (one row each, admin-provisioned). The account table *is* the allowlist. |
| Session | Auto-logout after **15 minutes of inactivity** (sliding window) |
| Data sensitivity | Real client financial statements (confidential) |
| Region | Southeast Asia (Singapore) — closest Azure region to Malaysia |
| Local development | Real email+password login on localhost (a seeded `dev@localhost` account); `AUTH_MODE=dev` auto-session kept only for CI/offline |

### Auth method: why email+password first, SSO later (revised 2026-06-16)

The original plan dropped username/password (storing passwords is the
highest-risk part of any auth system). The owner reversed this for a
pragmatic reason: enterprise Entra app registrations are IT-gated (Phase 3.0
flags this), and the owner needs a login that is **buildable and testable now
without waiting on any IdP** — including on a personal Mac that has no
enterprise SSO. SSO is therefore **deferred, not cancelled**: Microsoft Entra
is added later and exercised on the Windows laptop (same Entra infra as M365);
Google SSO is dropped (the Mac is a personal Gmail machine — not worth a
Google OAuth registration just to test).

**Security responsibilities this takes on** (the reason the original plan
avoided it — now mandatory, not optional):

- **Hashing:** `argon2id` (via `argon2-cffi`), never plaintext or a fast hash.
- **Brute-force defence:** per-`(email, IP)` rate-limit + temporary lockout on
  the password endpoint. A public Azure login form is a guessable surface SSO
  never exposed.
- **No self-signup, no password reset** (kept from the original scope) →
  accounts are **admin-provisioned** via a small CLI (below). An admin re-runs
  the CLI to rotate a password.
- **Password hashes are secrets at rest** — they live in the SQLite DB under
  `/home/data`, so the Phase 4 backup + Key Vault posture covers them.

**Per-user, not a shared credential** (decided 2026-06-16): this app is an
audit-trail system (runs/History/agent traces record provenance — gotchas #6,
#10, #11). A shared login would attribute every run to one anonymous account,
defeating "who extracted this client's statements." Per-user costs the same to
build (same table, hashing, endpoint — just N seeded rows instead of 1) and
preserves attribution + individual revocation.

If the password burden is ever unwanted, **Microsoft Entra External ID**
(Microsoft hosts the passwords) is the upgrade path — but it needs Entra
config, which is exactly what this decision defers.

## Architecture decision: in-app auth module, not Easy Auth alone

Two candidate designs were considered:

1. **Azure Easy Auth** (App Service's built-in authentication): zero code, but
   it cannot express this feature set — it has no email+password local-account
   mode, its session cookie has no sliding 15-minute inactivity semantics, the
   per-user check still needs app-side enforcement, and none of it exists on
   localhost.
2. **In-app auth layer** (chosen): a small auth module inside the FastAPI app —
   email+password verification against the `auth_users` table now, plus
   [`authlib`](https://docs.authlib.org/) for the Microsoft OIDC flow later —
   server-side sessions in SQLite, and a React login page. Identical behaviour
   locally and on Azure; full control of the timeout and the account list.

Easy Auth remains available later as optional defence-in-depth in front of
the app (Phase 4), but the app must not depend on it.

The security-critical surface is: cookie handling, **password storage +
verification + lockout**, and (later) the OIDC redirect/callback. Password
storage is the part the original SSO-only plan avoided — §1 specs it
explicitly.

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

- Two new tables (schema **v18**, following the gotcha #11 walk-forward
  pattern; pure `CREATE TABLE IF NOT EXISTS`):
  - `auth_users` — the account list **and** the allowlist (a row = authorised):
    `email (PK, lowercased)`, `display_name`, `password_hash` (argon2id;
    nullable so a future SSO-only user can exist with no password),
    `disabled (default 0)`, `created_at`, `password_set_at`.
  - `auth_sessions` — `session_id (PK, random 256-bit)`, `email`,
    `display_name`, `provider` (`password` now; `microsoft` later),
    `created_at`, `last_seen_at`.
  > **Why v18, not v17:** `CURRENT_SCHEMA_VERSION` is already **17**
  > ([db/schema.py:115](../db/schema.py:115) — v17 added
  > `run_agents.error_type`, pinned by `tests/test_db_schema_v17.py`).
  > Assigning auth to v17 would collide: `init_db` sees stored version 17 ==
  > current and skips the walk-forward, so the auth tables never migrate onto
  > an existing DB. Bump `CURRENT_SCHEMA_VERSION` to 18, add a v17→v18 step
  > (both `CREATE TABLE IF NOT EXISTS`), and a new `tests/test_db_schema_v18.py`
  > (the v17 test name is taken too).
- **Password hashing:** `argon2id` via `argon2-cffi` (add to
  `requirements.txt`). Verify on login; transparently re-hash on parameter
  upgrade. Never log or return the hash.
- Pre-auth OAuth transaction store (state / nonce / PKCE `code_verifier`):
  **deferred to the SSO phase** (not needed for password login). When
  Microsoft SSO is added, store the `{state, nonce, code_verifier, provider,
  expires_at}` in a **separate short-lived signed cookie**
  (`__Host-xbrl_oauth_txn`, `HttpOnly`, `SameSite=Lax`, `Secure`, ≤ 5 min,
  single-use — deleted on callback). The cookie is bound to the browser that
  started the flow, which also defeats login-CSRF. (A server-side
  `auth_oauth_txns` table is the alternative — heavier, only worth it if we
  later need cross-device flows.)
- Cookie: `__Host-xbrl_session` (falls back to a plain name on `http://`
  localhost only), `HttpOnly`, `SameSite=Lax`, `Secure`. Value = opaque
  session id, HMAC-signed with `SESSION_SECRET`. Server-side sessions (not
  stateless JWT) so logout/timeout are enforceable and revocable.
  - **Production forces secure flags unconditionally.** Azure App Service
    terminates TLS at its front end, so uvicorn sees the *forwarded* request
    and `request.url.scheme` is `http` unless proxy headers are trusted — the
    "Secure when https" heuristic would silently downgrade the cookie (and
    `__Host-` *requires* `Secure`, so it would fall back to the plain
    non-secure name in production, exactly backwards). Therefore: when
    `WEBSITE_SITE_NAME` is present (or an explicit production flag is set),
    **always** emit `Secure` + the `__Host-` name + `Path=/` + no `Domain`,
    independent of the observed scheme, and configure uvicorn to trust the
    platform proxy headers (`--proxy-headers`, `forwarded-allow-ips`). The
    http-localhost fallback applies **only** in local/dev mode. Pinned by a
    test asserting the prod cookie carries `Secure` + `__Host-` even when the
    request scheme reads `http`.
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
- **SSE streams must re-check expiry mid-stream, not just at connect.** A
  run stream is a *single long-lived request*; the middleware only
  authenticates at request **start**. Without a mid-stream check, a stream
  opened while valid keeps pushing confidential extraction events to an
  abandoned tab long after the 15-minute idle timeout (the refresh ping stops
  when the user walks away, `last_seen_at` goes stale, but nothing closes the
  connection). The fix piggybacks on the keepalive loop already added for
  Azure's ~230 s idle drop (see "SSE idle timeout" above): on every ~25 s
  keepalive tick, the event-queue drain loop re-reads the session row and, if
  `now − last_seen_at > AUTH_IDLE_TIMEOUT_S`, deletes the row, emits a final
  `event: session-expired`, and closes the stream. The frontend's
  any-401-⇒-login wrapper already handles the reconnect-to-login path. Pinned
  by a test that opens a stream, ages the session past the timeout, and
  asserts a `session-expired` close.

### 1.2 Backend endpoints + middleware (`auth/` package)

**Password login (built now):**

- `POST /api/auth/login/password` — body `{email, password}`. Looks up
  `auth_users` by lowercased email, rejects if `disabled`, verifies the
  argon2id hash, then creates a session (the same session/cookie path SSO
  will reuse). Returns `{ok, email, display_name}`; a wrong email or
  password returns the **same** generic 401 ("invalid email or password" —
  no user-enumeration leak).
- **Brute-force defence (mandatory):** per-`(email, client-IP)` counter with
  temporary lockout — e.g. 5 failures ⇒ 15-minute lock (configurable via
  `AUTH_LOGIN_MAX_ATTEMPTS` / `AUTH_LOGIN_LOCKOUT_S`). A locked key returns
  429 regardless of password correctness. In-process dict is acceptable
  (single-instance invariant — gotcha-aligned); persisting it is a later
  nice-to-have. Constant-time-ish: always run an argon2id verify (against a
  dummy hash on user-miss) so response timing doesn't reveal account
  existence.
- **Admin provisioning CLI** (`scripts/manage_users.py` or a
  `python -m auth.manage` entry): `add-user EMAIL --name "..."` (prompts for a
  password, argon2id-hashes it, inserts the row), `set-password EMAIL`,
  `disable-user EMAIL`, `list-users`. No self-signup, no reset endpoint —
  rotation is an admin re-run. This is how the `dev@localhost` local account
  and every Azure teammate account get created.

**Shared (now):**

- `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/refresh`.
- `GET /api/health` — **new, add in this phase** (no health route exists
  today). Unauthenticated liveness probe returning `{"status": "ok"}`; used
  by the Azure App Service health check and the §3.6 smoke checklist. Adding
  it here keeps the auth-middleware exemption from encoding a phantom route.
- Starlette middleware guarding **every `/api/*` route** (downloads, SSE,
  uploads, everything) except `/api/auth/*` and `/api/health`. Static SPA
  assets stay public (they contain no data); the SPA itself redirects to
  the login page when `/api/auth/me` returns 401.
- The account list **is** the allowlist: authorisation = "a non-disabled
  `auth_users` row exists." No separate `AUTH_ALLOWED_EMAILS` env for the
  password path (it returns when SSO lands — see below).
- **Fail-closed config check:** in production mode, missing `SESSION_SECRET`
  aborts startup with a clear error; additionally, **production refuses to
  start with zero `auth_users` rows** (an empty account table on Azure would
  be either a lockout or, worse, a wide-open misconfiguration). Mirrors the
  canonical-bootstrap fail-fast philosophy (gotcha #21).

**SSO (Microsoft, added later — not in the first build):**

- `GET /api/auth/login/microsoft` — authlib redirect with `state` + PKCE,
  storing the transaction in the `__Host-xbrl_oauth_txn` cookie (§1.1).
- `GET /api/auth/callback/microsoft` — reads + clears the transaction cookie,
  verifies `state` + provider, exchanges code, reads the verified email
  claim, **matches it against an `auth_users` row** (the email allowlist
  returns here, optionally via `AUTH_ALLOWED_EMAILS` for SSO-only users with
  no password), creates the session. Microsoft `common`/enterprise endpoint;
  the account list, not the tenant, is the gate. A non-matching email ⇒
  friendly "not authorised" page, no session. Tested on the Windows laptop
  (same Entra infra as M365). Google SSO is **not** planned.

### 1.3 Local development (the "how do I log in locally?" answer)

- **Default: real email+password login on localhost.** Seed a `dev@localhost`
  account once with the provisioning CLI; you log in for real (same code path
  as Azure), exercise the timeout, logout, etc. — exactly the production
  experience, no IdP needed. This is the everyday mode on both the Mac and
  the Windows laptop.
- `AUTH_MODE=dev` (optional convenience): auto-session as `dev@localhost`
  with no login form, for **CI and fully-offline** work only. **Guard:** dev
  mode refuses to start when `WEBSITE_SITE_NAME` is present (i.e. on App
  Service) — pinned by test so the bypass can never ship to Azure. Most local
  work uses the real password login above, not this.

### 1.4 Frontend

- `LoginPage.tsx`: an **email + password form** ("Sign in") posting to
  `POST /api/auth/login/password`; show the generic error on 401 and a
  "too many attempts, try again later" message on 429. A disabled
  "Sign in with Microsoft" button (or none) is a placeholder for the later
  SSO phase. Inline styles + `theme.ts` tokens (gotcha #7).
- App boot: call `/api/auth/me`; 401 ⇒ render LoginPage instead of the app.
  A small fetch/EventSource wrapper treats any 401 as "session expired ⇒
  show login" so timeout mid-use degrades gracefully.
- Idle tracker (throttled refresh ping) + optional "you'll be logged out
  in 1 minute" toast.
- Header: signed-in email + Logout.

### 1.5 Tests (the "done" bar, per CLAUDE.md)

Password path (built now):

- `tests/test_auth_password.py` — correct credentials create a session;
  wrong password and unknown email both return the **same** generic 401
  (no user enumeration); a `disabled` account is refused; the stored hash is
  argon2id (never plaintext) and re-verifies.
- `tests/test_auth_lockout.py` — N failures lock the `(email, IP)` for the
  window (429 even on a subsequently-correct password); lock expires; the
  user-miss path still runs a verify so timing doesn't reveal existence.
- `tests/test_manage_users.py` — the provisioning CLI: `add-user` inserts an
  argon2id row, `set-password` rotates, `disable-user` blocks login,
  `list-users` never prints hashes.
- `tests/test_auth_prod_requires_users.py` — production startup
  (`WEBSITE_SITE_NAME` set) **aborts** with zero `auth_users` rows.

Shared:

- `tests/test_auth_sessions.py` — sliding expiry, denylisted endpoints
  don't refresh, logout revokes, cookie flags. Includes the **production
  cookie-hardening** case: when `WEBSITE_SITE_NAME` is set, the session
  cookie carries `Secure` + `__Host-` + `Path=/` + no `Domain` **even when
  the observed request scheme reads `http`** (proxy-terminated TLS).
- `tests/test_auth_sse_expiry.py` — an SSE stream opened while valid is
  closed with a `session-expired` event once the session ages past
  `AUTH_IDLE_TIMEOUT_S` mid-stream (the stream itself never bumped activity).
- `tests/test_auth_middleware.py` — every `/api/*` route 401s without a
  session (walk the route table programmatically so new endpoints can't
  ship unguarded); `/api/auth/*` + `/api/health` exempt, and `/api/health`
  actually exists (no phantom-route exemption).
- `tests/test_auth_dev_mode.py` — dev bypass works locally, **fails fast
  under `WEBSITE_SITE_NAME`**.
- `tests/test_db_schema_v18.py` — migration walk-forward (v17→v18 adds
  `auth_users` + `auth_sessions`; v17 is already taken by
  `run_agents.error_type`).
- Frontend: LoginPage email+password render + submit, 401→login redirect,
  429 "too many attempts" message, idle-ping throttle.

SSO path (deferred — added with the Microsoft phase):

- `tests/test_auth_oauth_txn.py` — the pre-auth transaction cookie: state
  mismatch rejected (CSRF/replay), expired `code_verifier` rejected,
  provider-mismatch rejected, single-use (consumed on callback).
- `tests/test_auth_sso_allowlist.py` — SSO email matched against
  `auth_users`/allowlist; deny path creates no session, case-insensitive.

## Phase 2 — Identity provider registration (DEFERRED — only when adding SSO)

**Not part of the first build.** The password layer (Phase 1) ships and runs
on Azure with no IdP at all. Do this phase only when you choose to add the
Microsoft SSO button — and it is exercised on the **Windows laptop** (same
Entra infra as M365). **Google SSO is dropped** (the Mac is a personal Gmail
machine; not worth a Google OAuth registration).

When that time comes (one-time, ~15 min, produces a client ID + secret):

1. **Microsoft Entra ID** (portal.azure.com → App registrations → New).
   Account type depends on tenant policy (Phase 3.0): "any org directory +
   personal" if allowed, else single-tenant (corporate accounts only — the
   `auth_users` list is still the real gate). Web redirect URIs
   `http://localhost:8002/api/auth/callback/microsoft` and
   `https://<app>.azurewebsites.net/api/auth/callback/microsoft`; create a
   client secret (note its **expiry date** — calendar reminder to rotate,
   default max 24 months). Enterprise registrations are often IT-gated — see
   Phase 3.0.
2. Collect `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, and (for SSO-only users with no
   password) `AUTH_ALLOWED_EMAILS`.

## Phase 3 — Azure provisioning + deployment

### 3.0 Enterprise prerequisites (confirm with the Azure/cloud admin first)

Because the subscription is corporate, several things the personal-cloud
path takes for granted need confirming before any resources are created:

- **Subscription permissions:** rights to create a resource group + App
  Service (or have the admin create them and grant Contributor on the
  resource group).
- **Entra app registrations** are often locked down in enterprise tenants —
  this only matters for the **deferred** Microsoft SSO phase, not the
  password build, which needs no IdP. When SSO is added, Phase 2 may need to
  go through IT; if tenant policy forbids "any org + personal" registrations,
  register single-tenant (corporate accounts only). The per-user `auth_users`
  list (email+password) remains the real gate regardless, and covers any
  non-corporate users a single-tenant SSO registration would exclude.
- **Tenancy policies:** allowed regions (Southeast Asia?), naming/tagging
  conventions, and whether a public-facing App Service is permitted or
  Private Endpoint / VPN-only access is mandated (the latter changes how
  the team reaches the app, not the app itself).
- **Outbound LLM traffic:** the firm may require routing through its
  GenAI shared-service proxy instead of direct provider keys — the app
  already supports this (`LLM_PROXY_URL`, proxy mode), and it would also
  resolve the confidentiality lever flagged above.
- **Azure CLI on the corporate laptop:** whether installing it is allowed
  (see §3.5 fallbacks if not).

### 3.1 — 3.6 Provisioning + deployment

1. **Resources** (portal or `az` CLI): resource group `rg-xbrl-agent`
   in **Southeast Asia**; Linux App Service plan — start **B2**
   (3.5 GB RAM; B1's 1.75 GB is tight for PyMuPDF + concurrent agents),
   ~US$25–35/month, upgradeable in place; Web App, Python 3.12.
2. **Configuration** (App Settings — these are the production `.env`):
   provider API keys, `SESSION_SECRET` (random 64 hex chars),
   `XBRL_OUTPUT_DIR=/home/data`, `PYTHONUTF8=1`,
   `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`, `TEST_MODEL`/`SCOUT_MODEL`,
   `LLM_PROXY_URL` empty (direct mode). **No `AUTH_MODE`** (real password
   login is the default) — and emphatically **not** `AUTH_MODE=dev` (the
   `WEBSITE_SITE_NAME` guard would abort the boot anyway). The Microsoft
   OIDC values (`MS_CLIENT_ID`/`MS_CLIENT_SECRET`) + `AUTH_ALLOWED_EMAILS`
   are added **only** when the SSO phase lands.
   - **Seed the accounts after first deploy:** run the provisioning CLI
     (§1.2) against the production DB to create each teammate's
     email+password — over SSH/Kudu console, e.g.
     `python -m auth.manage add-user you@firm.com --name "..."`. Production
     refuses to start with zero `auth_users` rows, so this is a required
     first-boot step.
3. **Platform settings:** Always On = on; scale out **fixed at 1
   instance**; HTTPS Only = on; min TLS 1.2; FTP disabled.
4. **Code changes in this phase:** `XBRL_OUTPUT_DIR` env override for
   `OUTPUT_DIR`/`AUDIT_DB_PATH`; SSE keepalive comments; startup command
   `uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers
   --forwarded-allow-ips='*'` (single worker — see single-instance
   invariant; `--proxy-headers` so the request scheme is seen as https
   behind Azure's TLS-terminating front end — §1.1 cookie hardening) wired
   so `mount_spa` still runs.
5. **Deployment — manual, from the enterprise Windows laptop.** GitHub
   Actions **cannot** deploy here: that would require enterprise Azure
   credentials inside a personal GitHub repo (policy violation, and
   conditional access would likely block it anyway). Instead:
   - **Primary path: `scripts\deploy_azure.bat`** (mirroring `start.bat`
     conventions — `PYTHONUTF8=1`, Node auto-discovery). Steps, aborting
     on any failure: warn if the working tree has uncommitted changes →
     `npm ci && npm run build` (web/dist) → backend + frontend tests →
     stamp the current commit hash into a `version.txt` inside the
     package (keeps "what's live?" answerable) → zip (exclude `output/`,
     `.env`, `node_modules`, `backup-originals/`; **include** the
     `XBRL-template-*` and `SSMxT_2022v1.0` runtime data) →
     `az webapp deploy --type zip`. Raw `az webapp deploy` without the
     script is unguarded (no tests, stale `web/dist`) — use the script.
   - **Corporate-proxy note:** the `az` CLI has the same MITM-cert issue
     as gotcha #5 — it may need `HTTPS_PROXY` plus `REQUESTS_CA_BUNDLE`
     pointed at the corporate root CA. Document the working values in the
     script's header once known.
   - **Fallbacks if the Azure CLI can't be installed:** (a) per-user
     `pip install azure-cli` (no admin MSI required); (b) **Azure Cloud
     Shell** — a terminal in the Azure portal with `az`, `git`, `node`,
     and Python preinstalled: clone the GitHub repo there (private repo
     ⇒ a GitHub fine-grained PAT) and run the build + deploy entirely in
     the browser, nothing installed on the laptop; (c) last resort:
     build the zip with the script's package step and drag-drop it onto
     the App Service's Kudu/"Deployment Center" page in the portal.
   - **GitHub Actions remains, tests-only:** build + pytest + vitest on
     every push (no Azure credentials anywhere). Green checks on GitHub
     mean "safe to pull and deploy from Windows".
6. **Smoke checklist:** seed an account via the CLI, then log in with
   email+password; wrong password rejected (generic 401); repeated wrong
   passwords lock the account (429); 15-min timeout fires; upload→extract→
   download a sample PDF; SSE survives a long run; History persists across an
   app restart (proves `/home/data` wiring + that seeded accounts survive);
   `/api/*` without a cookie ⇒ 401. (Microsoft SSO login is added to this
   checklist only when that phase lands.)

## Multi-environment workflow (Mac → personal GitHub → enterprise Windows + Azure)

The owner's actual day-to-day topology, and how auth behaves in each spot:

```
Mac (develop) ──push──▶ personal GitHub
                              │  git pull (manual, as today)
                              ▼
            enterprise Windows laptop  (local use, password login)
                              │  scripts\deploy_azure.bat (manual)
                              ▼
            enterprise Azure App Service  (production, password login;
                                           Microsoft SSO added later)
```

The Windows laptop is the **bridge**: it is the only machine with both the
code (pulled from personal GitHub) and enterprise Azure access. Enterprise
Azure credentials must **never** be stored in the personal GitHub repo
(neither as Actions secrets nor in files) — which is also why GitHub
Actions cannot be the deploy path here.

Principles that make this work with zero per-machine code changes:

- **One codebase, per-environment config.** All differences live in `.env`
  (local) / App Settings (Azure), never in code. `.env` stays gitignored;
  ship a `.env.example` documenting every auth variable. The same `git pull`
  on Windows keeps working exactly as today — the deploy script is simply
  the next command after it.
- **Deploys are manual, never automatic.** Production updates only when the
  owner runs the deploy script on the Windows laptop (Phase 3 §5). Pushing
  to GitHub never touches Azure. GitHub Actions is kept for **tests only**
  (build + pytest + vitest on push — needs no Azure credentials), so GitHub
  still tells you whether what you pushed is deployable before you pull it
  on Windows.
- **Both laptops use real email+password login** (a seeded `dev@localhost`
  account), the same code path as Azure. `AUTH_MODE=dev` auto-session stays
  available for CI/offline only. No IdP is involved locally for the password
  build, so the corporate proxy's Google block / OIDC MITM is irrelevant.
- **Windows is where Microsoft SSO gets tested (later phase).** When SSO is
  added, Microsoft login should work on the corporate network (same Entra
  infrastructure as M365); Google is not planned. `localhost` redirect URIs
  are machine-independent — one `http://localhost:8002/...` registration
  covers Mac *and* Windows. The outbound token-exchange call needs the
  corporate proxy env vars + the existing `truststore` injection (gotcha #5),
  which already handles the MITM root CA. Until then, password login is the
  only method everywhere.
- **Secrets placement per environment:** Mac `.env` (`SESSION_SECRET` for
  local; provider API keys for extraction), Windows `.env` (same), GitHub
  repo secrets (**none** — CI is tests-only and needs no Azure credential;
  enterprise Azure creds must never live in the personal GitHub repo, which
  is *why* Actions can't deploy), Azure App Settings (everything production).
  **User passwords are never in any `.env` or App Setting** — they live only
  as argon2id hashes in the DB, created by the provisioning CLI. The later MS
  client secret exists **only** in Azure App Settings.

> **Governance flag (raise-once, owner's call):** hosting in the
> enterprise Azure subscription is the right home for client-confidential
> data. The remaining wrinkle is that the *source code* lives in a
> personal GitHub repo that the enterprise laptop pulls from; firm policy
> may prefer an enterprise repo (GitHub EMU / Azure DevOps). Migrating
> later is a remote swap — the app and this plan don't change.

## Phase 4 — Hardening (after it works)

- **Key Vault references** for secrets instead of plain App Settings.
- **Backups:** nightly snapshot of `/home/data` (SQLite + artifacts) to a
  Blob Storage container with soft delete (a small WebJob/cron or Azure
  Backup for App Service).
- **Monitoring:** Application Insights (failures, response times), alert
  on 5xx spike; App Service log stream for live debugging.
- **Account management UI** (nice-to-have): a small admin-only page to
  add/disable users + trigger a password rotate, replacing CLI-over-SSH. The
  CLI stays the source of truth; the page is convenience.
- **Microsoft SSO** (the deferred Phase 2 work) slots in here or whenever
  wanted — purely additive to the password layer.
- Optional: custom domain; Easy Auth in front as a second layer; access
  restrictions (IP allowlist / Private Endpoint) if the client data
  posture demands it; secret-rotation calendar (MS client secret + a
  periodic user-password review).

## Cost ballpark

App Service B2 ≈ US$25–35/month (Southeast Asia pricing). Application
Insights/Storage add single-digit dollars. LLM API usage remains the
dominant variable cost, unchanged by hosting.

## Out of scope (explicitly)

- Self-signup and password reset (email+password login itself is implemented;
  accounts are admin-provisioned via `python -m auth.manage`).
- Multi-instance scale-out (would require Postgres + blob storage — a
  separate plan).
- Routing LLM traffic through Azure OpenAI (flagged above as the main
  confidentiality lever; decide separately).
