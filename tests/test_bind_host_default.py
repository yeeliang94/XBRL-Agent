"""Pin the server's network-bind default to loopback.

The app ships with NO authentication / CORS / CSRF but exposes destructive
+ paid-LLM endpoints (revert-to-original, re-review). The safe default is
therefore 127.0.0.1 (this machine only); 0.0.0.0 must be an explicit,
warned-about opt-in. Guards against a silent regression back to all-interfaces.
"""
from __future__ import annotations

import server


def test_default_is_loopback_and_not_exposed():
    host, exposed = server.resolve_bind_host({})
    assert host == "127.0.0.1"
    assert exposed is False


def test_explicit_all_interfaces_is_flagged_exposed():
    host, exposed = server.resolve_bind_host({"HOST": "0.0.0.0"})
    assert host == "0.0.0.0"
    assert exposed is True


def test_explicit_loopback_is_not_exposed():
    for h in ("127.0.0.1", "localhost", "::1"):
        host, exposed = server.resolve_bind_host({"HOST": h})
        assert host == h
        assert exposed is False


def test_lan_ip_is_flagged_exposed():
    host, exposed = server.resolve_bind_host({"HOST": "192.168.1.50"})
    assert host == "192.168.1.50"
    assert exposed is True
