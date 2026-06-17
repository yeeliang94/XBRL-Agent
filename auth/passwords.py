"""Password hashing — argon2id, with a constant-time verify on user-miss.

The hash string is opaque to the rest of the app; only this module knows it is
argon2id. argon2-cffi's PasswordHasher uses argon2id with sane modern defaults
and embeds the parameters in the hash, so a future parameter bump re-verifies
old hashes and we can transparently re-hash on login.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()

# A throwaway hash verified on the user-miss path so a request for an unknown
# email costs the same ~argon2 time as a request for a real one — otherwise
# response timing would reveal which emails have accounts (enumeration).
_DUMMY_HASH = _ph.hash("constant-time-dummy-password")


def hash_password(password: str) -> str:
    """Return an argon2id hash string (parameters embedded) for storage."""
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True iff the password matches the stored hash.

    Returns False (never raises) on a mismatch, a malformed/empty hash, or a
    NULL hash (an SSO-only account with no password). Callers MUST still run
    dummy_verify on the no-account path to keep timing flat.
    """
    if not password_hash:
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash used older parameters and should be upgraded on
    the next successful login."""
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def dummy_verify(password: str) -> None:
    """Burn argon2 time against a throwaway hash so the unknown-email path is
    not distinguishable from a wrong-password path by timing."""
    try:
        _ph.verify(_DUMMY_HASH, password)
    except (VerifyMismatchError, InvalidHashError):
        pass
