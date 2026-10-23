"""Gate tests for the majors universe restriction (2026-08-29).

2026-10-23 (wave 2): this used to pin the restriction in three places — the
scan (pathiel.agents.perception), the risk gate (pathiel.agents.risk_gates),
and the loop's own entry-block reason (scripts/trading_loop.py) — all three
retired with the rest of the autonomous engine. Claude is the only trader
now and names the coin directly on every place_bracket call, so there is no
scan/gate/loop entry path left for the restriction to bind at; what remains
here is the matching logic itself (pathiel.agents.universe, kept) and the
one place it still surfaces: the default config's coin_allowlist.
"""
from __future__ import annotations


from pathiel.agents import universe as U


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


def test_the_shipped_default_config_is_restricted_to_majors():
    import pathiel.agents.config_store as cs
    defaults = next(v for v in vars(cs).values()
                    if isinstance(v, dict) and "coin_allowlist" in v)
    assert set(defaults["coin_allowlist"]) == set(U.MAJORS)


def test_the_restriction_is_documented_as_capacity_not_edge():
    """A future reader must not mistake a universe cut for an alpha claim."""
    assert "not a strategy" in U.__doc__.lower() or "not an edge" in U.__doc__.lower()
