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

# Minimum password length. Deliberately modest — this gates a small fixed team,
# not the public; the real defences are argon2id + lockout, not a length policy.
_MIN_PASSWORD_LEN = 8


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
    verb = "Updated" if existing else "Created"
    print(f"{verb} account {args.email.strip().lower()!r}.")
    # upsert deliberately leaves `disabled` untouched (so re-provisioning can't
    # silently re-enable a blocked account). Make that visible so the admin isn't
    # surprised that the password they just set still can't log in.
    if existing and existing.disabled:
        print(
            "  NOTE: this account is DISABLED — the new password won't work "
            "until you run `enable-user`."
        )


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
    # Never print the hash. Show whether a password is set (vs SSO-only) and
    # whether the account is disabled.
    print(f"{'EMAIL':<32} {'NAME':<20} {'STATUS':<10} PASSWORD")
    for u in users:
        status = "disabled" if u.disabled else "active"
        has_pw = "yes" if u.password_hash else "no (SSO-only)"
        print(f"{u.email:<32} {u.display_name:<20} {status:<10} {has_pw}")


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
    p_add.set_defaults(func=cmd_add_user)

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
