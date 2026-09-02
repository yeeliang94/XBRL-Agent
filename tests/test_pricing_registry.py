"""Pricing lookup + the registry's rates.

Two failures this file exists to prevent, both found on 2026-08-03 after a
live GPT-5.6 Luna run reported a cost that was wrong by 25x:

1. A model id whose provider prefix `pricing._normalize` does not know falls
   through to $0. The prefix list is a hand-maintained copy of
   `server._PROVIDER_PREFIXES`; when `openai.global.` was added there and not
   here, every DIRECT-mode 5.6 run silently costed at zero.
2. A placeholder rate that nobody corrects. The registry carries
   `pricing_unconfirmed` for exactly this, but a guess that is never revisited
   reads as a real number.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import pricing
import server

_REGISTRY = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "models.json")
    .read_text(encoding="utf-8")
)


def _ids() -> list[str]:
    return [m["id"] for m in _REGISTRY]


# --------------------------------------------------------------------------
# 1. prefix stripping must agree with the router
# --------------------------------------------------------------------------

def test_normalize_knows_every_prefix_the_router_strips():
    """`pricing._normalize` is a copy of `server._PROVIDER_PREFIXES`. Direct
    mode constructs the model with the bare name, so a prefix the router
    strips but pricing does not is a silent $0 cost estimate."""
    missing = [
        p for p in server._PROVIDER_PREFIXES
        if pricing._normalize(p + "x-probe") != "x-probe"
    ]
    assert missing == [], (
        f"pricing._normalize does not strip {missing}; models under those "
        "prefixes will price at $0 in direct mode."
    )


@pytest.mark.parametrize("model_id", _ids())
def test_every_registry_id_prices_the_same_bare_as_prefixed(model_id):
    """Direct mode hands pricing the bare name, the proxy hands it the full
    id. The same model must cost the same either way."""
    bare = server._strip_provider_prefix(model_id)
    assert pricing.get_model_pricing(model_id) == pricing.get_model_pricing(bare)


@pytest.mark.parametrize("model_id", _ids())
def test_no_registry_model_prices_at_zero(model_id):
    """$0 is what a lookup MISS returns, so a real zero is indistinguishable
    from a broken lookup."""
    inp, out = pricing.get_model_pricing(model_id)
    assert inp > 0 and out > 0, f"{model_id} priced at ({inp}, {out})"


@pytest.mark.parametrize("model_id", _ids())
def test_the_unconfirmed_flag_survives_prefix_stripping(model_id):
    """Same resolution as the price itself — otherwise a direct-mode run
    shows an estimated cost with no estimate marker."""
    bare = server._strip_provider_prefix(model_id)
    assert (
        pricing.pricing_is_unconfirmed(model_id)
        == pricing.pricing_is_unconfirmed(bare)
    )


# --------------------------------------------------------------------------
# 2. the rates themselves
# --------------------------------------------------------------------------

# Vendor list prices. GPT-5.6 was checked 2026-09-02 against the OpenAI API
# model docs; the remaining entries retain their dates in config/models.json.
# Update BOTH this table and
# config/models.json in the same commit — the point of pinning them is that a
# rate cannot drift in one place only.
_EXPECTED_RATES = {
    "openai.global.gpt-5.6": (4.0, 20.0),         # alias of gpt-5.6-sol
    "openai.global.gpt-5.6-luna": (0.2, 1.2),
    "openai.global.gpt-5.6-terra": (2.0, 12.0),
    "openai.global.gpt-5.5-pro": (30.0, 180.0),
    "openai.gpt-5.5": (5.0, 30.0),
    "openai.gpt-5.4": (2.5, 15.0),
    "vertex_ai.gemini-3.6-flash": (1.5, 7.5),
    "vertex_ai.gemini-3.5-flash": (1.5, 9.0),
    "vertex_ai.gemini-3.5-flash-lite": (0.3, 2.5),
    "vertex_ai.gemini-3.1-pro-preview": (2.0, 12.0),
}


@pytest.mark.parametrize("model_id,expected", sorted(_EXPECTED_RATES.items()))
def test_registry_rate_matches_the_published_rate_card(model_id, expected):
    assert pricing.get_model_pricing(model_id) == expected


def test_luna_is_the_cheap_tier_not_the_flagship_rate():
    """The regression in one line. Luna was carrying GPT-5.5's 5/30, which
    made the cheapest 5.6 tier read as more expensive than GPT-5.4."""
    luna = pricing.get_model_pricing("openai.global.gpt-5.6-luna")
    sol = pricing.get_model_pricing("openai.global.gpt-5.6")
    gpt54 = pricing.get_model_pricing("openai.gpt-5.4")
    assert luna[0] < gpt54[0] and luna[1] < gpt54[1]
    assert luna[0] < sol[0] and luna[1] < sol[1]


def test_run_detail_marks_a_cost_derived_from_a_placeholder_rate(
    tmp_path, monkeypatch,
):
    """The flag existed in the registry from the day the 5.6 models landed,
    and NOTHING read it — so a placeholder-derived figure was displayed in
    the same shape as a real one. The Telemetry tab reads this field."""
    import importlib
    import sqlite3

    from fastapi.testclient import TestClient

    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    from db import repository as repo

    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "x.pdf", status="completed")
    # One agent on a placeholder-priced model, one on a published-rate model.
    guessed = repo.create_run_agent(
        conn, run_id, "SOFP", model="openai.global.gpt-5.6-luna",
    )
    known = repo.create_run_agent(conn, run_id, "SOPL", model="openai.gpt-5.4")
    repo.finish_run_agent(conn, guessed, "completed", total_cost=1.0)
    repo.finish_run_agent(conn, known, "completed", total_cost=1.0)
    conn.commit()
    conn.close()

    agents = TestClient(srv.app).get(f"/api/runs/{run_id}").json()["agents"]
    by_stmt = {a["statement_type"]: a for a in agents}
    assert by_stmt["SOFP"]["pricing_unconfirmed"] is True
    assert by_stmt["SOPL"]["pricing_unconfirmed"] is False


def test_proxy_openai_models_stay_flagged_until_pwc_rates_are_known():
    """The enterprise proxy may bill at its own rate card, so a vendor list
    price is not yet a confirmed cost for these. Drop the flag only when
    somebody has the PwC rates."""
    for model_id in _ids():
        if model_id.startswith("openai.global."):
            assert pricing.pricing_is_unconfirmed(model_id), model_id
