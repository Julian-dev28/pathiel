"""services/strategies/desk_leverage.effective_cap and GET /api/desks/active's
`leverage_cap` field (2026-11-20 bug report): the Desk page's "Next trade"
leverage picker and the call card's "the desk's rule" line showed oil's own
20x on every desk, HYPE included (whose venue cap is 10x) -- oil_desk.js hard-
coded `DEFAULT_LEVERAGE = 20` and `LEVERAGE_CHOICES = [5, 10, 20]` regardless
of which desk was on screen (see tests/test_desk_switcher_ui.py for the JS
side of that fix).

effective_cap is the resolved number each of those reads: the desk's own
override (desk_leverage.DESK_MAX_LEVERAGE -- sp500 50x, hype 10x) when it has
one, else the account's own saved risk profile cap
(services.strategies.desks.profile_leverage_cap) -- oil has no entry of its
own there, so it floats with whatever the operator's profile says (20x on the
live account's checked-in .agent-config.json).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import desk_leverage, desks

BRENT, SP500, HYPE = "xyz:BRENTOIL", "xyz:SP500", "HYPE"


# ── desk_leverage.effective_cap ─────────────────────────────────────────────

def test_sp500_and_hype_keep_their_own_cap_regardless_of_the_profile(monkeypatch):
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 20.0)
    assert desk_leverage.effective_cap(SP500, {}) == 50.0
    assert desk_leverage.effective_cap(HYPE, {}) == 10.0
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 3.0)   # a public/small-account cap
    assert desk_leverage.effective_cap(SP500, {}) == 50.0
    assert desk_leverage.effective_cap(HYPE, {}) == 10.0


def test_oil_floats_with_the_accounts_own_profile_cap(monkeypatch):
    """Oil has no entry of its own in DESK_MAX_LEVERAGE -- the exact bug
    report: the Desk page showed 20x on HYPE because oil's own number was
    hardcoded everywhere. effective_cap(oil) must track the profile, not a
    literal 20."""
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 20.0)
    assert desk_leverage.effective_cap(BRENT, {}) == 20.0
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 5.0)
    assert desk_leverage.effective_cap(BRENT, {}) == 5.0


def test_a_configured_desk_max_leverage_override_still_wins(monkeypatch):
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 20.0)
    assert desk_leverage.effective_cap(HYPE, {"desk_max_leverage": {"hype": 5}}) == 5.0
    assert desk_leverage.effective_cap(BRENT, {"desk_max_leverage": {"oil": 12}}) == 12.0


def test_a_broken_profile_read_degrades_to_the_public_cap_not_a_500(monkeypatch):
    from services.risk import presets

    def boom():
        raise RuntimeError("config file corrupt")

    monkeypatch.setattr("pathiel.agents.config_store.read_risk_profile", boom)
    assert desk_leverage.effective_cap(BRENT, {}) == presets.public_default_profile().max_leverage
    assert desk_leverage.effective_cap(SP500, {}) == 50.0   # a desk's own cap is unaffected either way


# ── GET /api/desks/active ────────────────────────────────────────────────────

def _client():
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_the_bug_report_scenario_oil_20x_sp500_50x_hype_10x(monkeypatch):
    """The exact numbers from the 2026-11-20 report, all three desks active
    at once with the live account's own 20x profile cap."""
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {
        "active_desks": ["oil", "sp500", "hype"],
        "risk_profile": {"preset": "custom", "max_leverage": 20},
        "desk_equity_share": {"oil": 0.80, "sp500": 0.10, "hype": 0.10},
    })
    r = _client().get("/api/desks/active")
    assert r.status_code == 200
    by_id = {d["id"]: d["leverage_cap"] for d in r.json()["desks"]}
    assert by_id == {"oil": 20.0, "sp500": 50.0, "hype": 10.0}


def test_hype_alone_never_inherits_oils_20x(monkeypatch):
    """The literal symptom: a lone HYPE desk, oil's 20x profile in force,
    must still answer 10x -- its own venue cap, not oil's number."""
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {
        "active_desks": ["hype"],
        "risk_profile": {"preset": "custom", "max_leverage": 20},
    })
    r = _client().get("/api/desks/active")
    desk = r.json()["desks"][0]
    assert desk["id"] == "hype" and desk["leverage_cap"] == 10.0


def test_operator_lowering_the_profile_cap_moves_oil_but_not_sp500_or_hype(monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {
        "active_desks": ["oil", "sp500", "hype"],
        "risk_profile": {"preset": "custom", "max_leverage": 8},
        "desk_equity_share": {"oil": 0.80, "sp500": 0.10, "hype": 0.10},
    })
    r = _client().get("/api/desks/active")
    by_id = {d["id"]: d["leverage_cap"] for d in r.json()["desks"]}
    assert by_id == {"oil": 8.0, "sp500": 50.0, "hype": 10.0}
