"""Admin provisioning CLI: add / set-password / disable / list (no hash leak).

Pins PLAN auth Phase 1.2 provisioning. getpass is monkeypatched so the tests
don't block on an interactive prompt.
"""
from __future__ import annotations

import sqlite3

import pytest

from auth import manage, passwords
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "auth.db"
    init_db(p)
    return p


def _fetch(db, email):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return repo.fetch_auth_user(conn, email)
    finally:
        conn.close()


def test_add_user_inserts_argon2id_row(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "You@Firm.com", "--name", "You"])
    user = _fetch(db, "you@firm.com")
    assert user is not None
    assert user.display_name == "You"
    assert user.password_hash.startswith("$argon2id$")
    assert user.disabled is False


def test_add_user_on_disabled_account_warns_and_stays_disabled(db, monkeypatch, capsys):
    """Re-provisioning a disabled account sets the new password but does NOT
    silently re-enable it — and the CLI says so, so the admin isn't surprised
    the password 'doesn't work'."""
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "you@firm.com", "--name", "You"])
    manage.main(["--db", str(db), "disable-user", "you@firm.com"])
    capsys.readouterr()  # drain
    manage.main(["--db", str(db), "add-user", "you@firm.com", "--name", "You"])
    out = capsys.readouterr().out
    assert "DISABLED" in out
    assert "enable-user" in out
    assert _fetch(db, "you@firm.com").disabled is True


def test_set_password_rotates_hash(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "you@firm.com"])
    first = _fetch(db, "you@firm.com").password_hash

    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "second-password")
    manage.main(["--db", str(db), "set-password", "you@firm.com"])
    second = _fetch(db, "you@firm.com").password_hash
    assert second != first
    assert passwords.verify_password(second, "second-password")


def test_set_password_unknown_user_errors(db, monkeypatch):
    with pytest.raises(SystemExit):
        manage.main(["--db", str(db), "set-password", "ghost@firm.com"])


def test_disable_blocks_then_enable_restores(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "you@firm.com"])
    manage.main(["--db", str(db), "disable-user", "you@firm.com"])
    assert _fetch(db, "you@firm.com").disabled is True
    manage.main(["--db", str(db), "enable-user", "you@firm.com"])
    assert _fetch(db, "you@firm.com").disabled is False


def test_password_mismatch_aborts(db, monkeypatch):
    # First prompt returns one value, the confirm returns another.
    answers = iter(["password-one", "password-two"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: next(answers))
    with pytest.raises(SystemExit):
        manage.main(["--db", str(db), "add-user", "you@firm.com"])


def test_list_users_never_prints_hash(db, monkeypatch, capsys):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "you@firm.com", "--name", "You"])
    manage.main(["--db", str(db), "list-users"])
    out = capsys.readouterr().out
    assert "you@firm.com" in out
    assert "$argon2id$" not in out


def test_add_user_with_admin_flag_mints_admin(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "boss@firm.com", "--name", "Boss", "--admin"])
    assert _fetch(db, "boss@firm.com").is_admin is True


def test_add_user_without_admin_does_not_demote_existing_admin(db, monkeypatch):
    """Re-running add-user without --admin must not silently demote an admin —
    same 'never clear a flag the command didn't name' rule as `disabled`."""
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "boss@firm.com", "--admin"])
    manage.main(["--db", str(db), "add-user", "boss@firm.com", "--name", "Boss"])
    assert _fetch(db, "boss@firm.com").is_admin is True


def test_make_and_revoke_admin_round_trip(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    # Two accounts so revoke isn't blocked by the last-admin guard.
    manage.main(["--db", str(db), "add-user", "a@firm.com", "--admin"])
    manage.main(["--db", str(db), "add-user", "b@firm.com"])
    manage.main(["--db", str(db), "make-admin", "b@firm.com"])
    assert _fetch(db, "b@firm.com").is_admin is True
    manage.main(["--db", str(db), "revoke-admin", "b@firm.com"])
    assert _fetch(db, "b@firm.com").is_admin is False


def test_revoke_last_admin_is_refused(db, monkeypatch):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "only@firm.com", "--admin"])
    with pytest.raises(SystemExit):
        manage.main(["--db", str(db), "revoke-admin", "only@firm.com"])
    # Still an admin after the refused demotion.
    assert _fetch(db, "only@firm.com").is_admin is True


def test_revoke_admin_on_disabled_admin_is_allowed(db, monkeypatch):
    # One ENABLED admin + one DISABLED admin. Demoting the disabled one can't
    # reduce the count of admins who can actually act, so it must be allowed —
    # the last-admin guard only protects the last ENABLED admin (Codex review
    # P3). Without the `not user.disabled` qualifier this would wrongly refuse.
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "enabled@firm.com", "--admin"])
    manage.main(["--db", str(db), "add-user", "stale@firm.com", "--admin"])
    manage.main(["--db", str(db), "disable-user", "stale@firm.com"])
    # Should NOT raise — the enabled admin still remains afterwards.
    manage.main(["--db", str(db), "revoke-admin", "stale@firm.com"])
    assert _fetch(db, "stale@firm.com").is_admin is False
    assert _fetch(db, "enabled@firm.com").is_admin is True


def test_make_admin_unknown_user_errors(db):
    with pytest.raises(SystemExit):
        manage.main(["--db", str(db), "make-admin", "ghost@firm.com"])


def test_list_users_shows_role_column(db, monkeypatch, capsys):
    monkeypatch.setattr(manage.getpass, "getpass", lambda *_: "first-password")
    manage.main(["--db", str(db), "add-user", "boss@firm.com", "--admin"])
    manage.main(["--db", str(db), "list-users"])
    out = capsys.readouterr().out
    assert "ROLE" in out
    assert "admin" in out
