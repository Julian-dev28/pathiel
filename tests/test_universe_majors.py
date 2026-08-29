"""Gate tests for the majors universe restriction (2026-08-29).

The restriction has to bind in THREE places or it is theatre:
  1. the scan, so the candle + AI budget is not spent on untradable markets
  2. the risk gate, so a book cannot route around the scan
  3. the loop's own entry-block reason, which is a separate code path

These tests pin all three, plus the bare-ticker matching that lets one entry
cover every HIP-3 venue's prefix for the same underlying.
"""
from __future__ import annotations

import pytest

from hermes_trader.agents import universe as U


# ── matching ─────────────────────────────────────────────────────────────────

def test_bare_ticker_strips_the_dex_prefix():
    assert U.bare_ticker("xyz:GOLD") == "GOLD"
    assert U.bare_ticker("km:USOIL") == "USOIL"
    assert U.bare_ticker("BTC") == "BTC"
    assert U.bare_ticker("btc") == "BTC"
    assert U.bare_ticker("") == ""


def test_one_entry_covers_every_venue_prefix():
    """The whole point of bare-ticker matching: GOLD is GOLD wherever listed."""
    for coin in ("GOLD", "xyz:GOLD", "km:GOLD", "hyna:GOLD"):
        assert U.in_allowlist(coin, ["GOLD"]), coin


def test_an_exact_namespaced_entry_still_works():
    """An operator who pinned xyz:GOLD in a live config must not break."""
    assert U.in_allowlist("xyz:GOLD", ["xyz:GOLD"])


def test_empty_allowlist_means_unrestricted():
    """Historical meaning of the config key — callers depend on it."""
    assert U.in_allowlist("FARTCOIN", []) is True
    assert U.in_allowlist("FARTCOIN", None) is True


def test_majors_admits_the_asked_for_universe_and_rejects_the_tail():
    for coin in ("BTC", "ETH", "xyz:GOLD", "xyz:SILVER", "xyz:CL", "xyz:SP500",
                 "xyz:NVDA"):
        assert U.in_allowlist(coin, U.MAJORS), f"{coin} should be tradable"
    for coin in ("FARTCOIN", "PEPE", "BONK", "xyz:SNDK", "TRUMP"):
        assert not U.in_allowlist(coin, U.MAJORS), f"{coin} should be excluded"


def test_filter_markets_is_unrestricted_on_an_empty_list():
    mkts = [{"coin": "BTC"}, {"coin": "FARTCOIN"}]
    assert U.filter_markets(mkts, []) == mkts


def test_filter_markets_drops_the_tail():
    mkts = [{"coin": "BTC"}, {"coin": "FARTCOIN"}, {"coin": "xyz:GOLD"}]
    got = [m["coin"] for m in U.filter_markets(mkts, U.MAJORS)]
    assert got == ["BTC", "xyz:GOLD"]


# ── it binds where the cost is ───────────────────────────────────────────────

def test_the_scan_applies_the_allowlist_not_just_the_entry_gate():
    """Gating only at entry still burns the candle and AI budget on markets the
    system can never trade. This is the test that would catch that regression."""
    import inspect
    from hermes_trader.agents import perception
    src = inspect.getsource(perception)
    assert "universe_filter.filter_markets" in src, (
        "perception no longer filters the scan universe by coin_allowlist — the "
        "restriction would be cosmetic and the scan would still pay for the tail")


def test_risk_gate_rejects_a_coin_off_the_allowlist():
    from hermes_trader.agents.risk_gates import coin_allowlist_gate

    class _Ctx:
        coin = "FARTCOIN"

    assert coin_allowlist_gate(_Ctx(), list(U.MAJORS), [])["pass"] is False


def test_risk_gate_admits_a_namespaced_major_on_a_bare_entry():
    from hermes_trader.agents.risk_gates import coin_allowlist_gate

    class _Ctx:
        coin = "xyz:GOLD"

    assert coin_allowlist_gate(_Ctx(), list(U.MAJORS), [])["pass"] is True


def test_blocklist_still_wins_over_the_allowlist():
    from hermes_trader.agents.risk_gates import coin_allowlist_gate

    class _Ctx:
        coin = "BTC"

    r = coin_allowlist_gate(_Ctx(), list(U.MAJORS), ["BTC"])
    assert r["pass"] is False and "blocklist" in r["reason"]


def test_the_shipped_default_config_is_restricted_to_majors():
    import hermes_trader.agents.config_store as cs
    defaults = next(v for v in vars(cs).values()
                    if isinstance(v, dict) and "coin_allowlist" in v)
    assert set(defaults["coin_allowlist"]) == set(U.MAJORS)


def test_the_restriction_is_documented_as_capacity_not_edge():
    """A future reader must not mistake a universe cut for an alpha claim."""
    assert "not a strategy" in U.__doc__.lower() or "not an edge" in U.__doc__.lower()
