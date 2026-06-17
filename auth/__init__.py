"""In-app authentication layer (PLAN-azure-auth-deployment Phase 1).

Email + password is the primary login method, built first and working in every
environment (local dev, Windows, Azure prod). Microsoft SSO is layered on later
without touching this package's session/cookie/timeout machinery.

Module map:
  - config       — env-driven settings (production detection, secret, timeouts)
  - passwords    — argon2id hashing + constant-time verify
  - sessions     — opaque session ids, HMAC-signed cookies, sliding expiry
  - lockout      — in-process brute-force rate-limit + temporary lockout
  - middleware   — request-auth resolution (exempt paths, activity rules)
  - routes       — FastAPI router (login / logout / me / refresh / health)
  - manage       — admin CLI for provisioning accounts (no self-signup)
"""
