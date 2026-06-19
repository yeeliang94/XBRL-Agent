"""Admin CLI for provisioning login accounts.

There is no self-signup and no password-reset endpoint by design — an admin
runs this to create or rotate accounts. Usage:

    python -m auth.manage add-user you@firm.com --name "Your Name"
    python -m auth.manage set-password you@firm.com
    python -m auth.manage disable-user old@firm.com
    python -m auth.manage enable-user back@firm.com
    python -m auth.manage list-users

It targets the same SQLite DB the server uses (OUTPUT_DIR/xbrl_agent.db, with
the XBRL_OUTPUT_DIR override Phase 3 also wires into the server), or an explicit
--db path. Passwords are read interactively (never as an argv that would land in
shell history) and stored only as argon2id hashes.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from db import repository as repo
from db.repository import db_session
from db.schema import init_db

from . import passwords

# Minimum password length lives in auth.passwords (shared with the web routes).
# Aliased here so the existing local references keep reading.
_MIN_PASSWORD_LEN = passwords.MIN_PASSWORD_LEN


def _default_db_path() -> Path:
    base = Path(__file__).resolve().parent.parent
    output_dir = Path(os.environ.get("XBRL_OUTPUT_DIR") or (base / "output"))
    return output_dir / "xbrl_agent.db"


def _prompt_new_password() -> str:
    """Prompt for a password twice and return it once confirmed + long enough.

    Exits non-zero on mismatch or too-short input rather than looping, so the
    command is scriptable and a typo doesn't trap an SSH session.
    """
    pw = getpass.getpass("New password: ")
    if len(pw) < _MIN_PASSWORD_LEN:
        sys.exit(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    again = getpass.getpass("Confirm password: ")
    if pw != again:
        sys.exit("Passwords do not match.")
    return pw


def cmd_add_user(args: argparse.Namespace) -> None:
    pw = _prompt_new_password()
    with db_session(args.db) as conn:
        existing = repo.fetch_auth_user(conn, args.email)
        repo.upsert_auth_user(
            conn, args.email, args.name or "", passwords.hash_password(pw)
        )
        # --admin promotes in the same breath as creation so the bootstrap
        # admin can be minted in one command. We only ever SET the flag here
        # (never clear it) — re-running add-user without --admin must not
        # silently demote an existing admin, mirroring how upsert leaves
        # `disabled` untouched. Use revoke-admin to demote explicitly.
        if args.admin:
            repo.set_auth_user_admin(conn, args.email, True)
    verb = "Updated" if existing else "Created"
    role = " (admin)" if args.admin else ""
    print(f"{verb} account {args.email.strip().lower()!r}{role}.")
    # upsert deliberately leaves `disabled` untouched (so re-provisioning can't
    # silently re-enable a blocked account). Make that visible so the admin isn't
    # surprised that the password they just set still can't log in.
    if existing and existing.disabled:
        print(
            "  NOTE: this account is DISABLED — the new password won't work "
            "until you run `enable-user`."
        )


def cmd_make_admin(args: argparse.Namespace) -> None:
    with db_session(args.db) as conn:
        if not repo.set_auth_user_admin(conn, args.email, True):
            sys.exit(f"No account {args.email!r}. Use add-user to create one first.")
    print(f"Granted admin to {args.email.strip().lower()!r}.")


def cmd_revoke_admin(args: argparse.Namespace) -> None:
    # Last-admin guard: refuse to demote the only ENABLED admin, or the web
    # Users tab + CLI would have no one left who can promote anyone. This is the
    # CLI mirror of the server-side guard in the /api/admin routes.
    with db_session(args.db) as conn:
        user = repo.fetch_auth_user(conn, args.email)
        if user is None:
            sys.exit(f"No account {args.email!r}.")
        # Only block when the target is ENABLED — count_admins() counts enabled
        # admins only, so demoting a DISABLED admin can't reduce the actable
        # count and must stay allowed (mirrors the route guard at
        # auth/routes.py; Codex review P3). Without the `not user.disabled`
        # qualifier a stale disabled-admin flag is impossible to clear here.
        if user.is_admin and not user.disabled and repo.count_admins(conn) <= 1:
            sys.exit(
                f"Refusing to demote {args.email.strip().lower()!r}: it is the "
                "only enabled admin. Promote another account first."
            )
        repo.set_auth_user_admin(conn, args.email, False)
    print(f"Revoked admin from {args.email.strip().lower()!r}.")


def cmd_set_password(args: argparse.Namespace) -> None:
    with db_session(args.db) as conn:
        existing = repo.fetch_auth_user(conn, args.email)
        if existing is None:
            sys.exit(
                f"No account {args.email!r}. Use add-user to create one first."
            )
    pw = _prompt_new_password()
    with db_session(args.db) as conn:
        repo.upsert_auth_user(
            conn, existing.email, existing.display_name,
            passwords.hash_password(pw),
        )
    print(f"Password rotated for {existing.email!r}.")


def cmd_disable_user(args: argparse.Namespace) -> None:
    with db_session(args.db) as conn:
        if not repo.set_auth_user_disabled(conn, args.email, True):
            sys.exit(f"No account {args.email!r}.")
    print(f"Disabled {args.email.strip().lower()!r}.")


def cmd_enable_user(args: argparse.Namespace) -> None:
    with db_session(args.db) as conn:
        if not repo.set_auth_user_disabled(conn, args.email, False):
            sys.exit(f"No account {args.email!r}.")
    print(f"Enabled {args.email.strip().lower()!r}.")


def cmd_list_users(args: argparse.Namespace) -> None:
    with db_session(args.db) as conn:
        users = repo.list_auth_users(conn)
    if not users:
        print("(no accounts — run add-user to create one)")
        return
    # Never print the hash. Show whether a password is set (vs SSO-only),
    # whether the account is disabled, and whether it carries the admin role.
    print(f"{'EMAIL':<32} {'NAME':<20} {'STATUS':<10} {'ROLE':<8} PASSWORD")
    for u in users:
        status = "disabled" if u.disabled else "active"
        role = "admin" if u.is_admin else "user"
        has_pw = "yes" if u.password_hash else "no (SSO-only)"
        print(f"{u.email:<32} {u.display_name:<20} {status:<10} {role:<8} {has_pw}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m auth.manage",
        description="Provision login accounts for the XBRL agent.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to the SQLite DB (default: OUTPUT_DIR/xbrl_agent.db).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add-user", help="Create or update an account.")
    p_add.add_argument("email")
    p_add.add_argument("--name", default="", help="Display name.")
    p_add.add_argument(
        "--admin", action="store_true",
        help="Grant the admin role (may manage other accounts).",
    )
    p_add.set_defaults(func=cmd_add_user)

    p_mk = sub.add_parser("make-admin", help="Grant the admin role to an account.")
    p_mk.add_argument("email")
    p_mk.set_defaults(func=cmd_make_admin)

    p_rv = sub.add_parser("revoke-admin", help="Remove the admin role from an account.")
    p_rv.add_argument("email")
    p_rv.set_defaults(func=cmd_revoke_admin)

    p_set = sub.add_parser("set-password", help="Rotate an account's password.")
    p_set.add_argument("email")
    p_set.set_defaults(func=cmd_set_password)

    p_dis = sub.add_parser("disable-user", help="Block login without deleting.")
    p_dis.add_argument("email")
    p_dis.set_defaults(func=cmd_disable_user)

    p_en = sub.add_parser("enable-user", help="Re-enable a disabled account.")
    p_en.add_argument("email")
    p_en.set_defaults(func=cmd_enable_user)

    p_ls = sub.add_parser("list-users", help="List accounts (never prints hashes).")
    p_ls.set_defaults(func=cmd_list_users)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Resolve + ensure the DB exists (idempotent) so a first-run add-user works
    # against a brand-new install.
    args.db = str(args.db) if args.db else str(_default_db_path())
    init_db(args.db)
    args.func(args)


if __name__ == "__main__":
    main()
