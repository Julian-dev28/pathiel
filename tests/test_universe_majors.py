"""Gate tests for the majors universe restriction (2026-08-29).

The restriction has to bind in THREE places or it is theatre:
  1. the scan, so the candle + AI budget is not spent on untradable markets
  2. the risk gate, so a book cannot route around the scan
  3. the loop's own entry-block reason, which is a separate code path

These tests pin all three, plus the bare-ticker matching that lets one entry
cover every HIP-3 venue's prefix for the same underlying.
"""
from __future__ import annotations


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


# ── the degraded-feed entry gate, end to end through the loop's own path ─────

def test_the_book_execute_path_blocks_on_a_degraded_feed():
    """The gate has to sit where the money actually flows.

    It used to live only in _fresh_entry_preblock_reason — the main_engine entry
    preflight — so it guarded a path that (W-ME1) could not fire on the majors
    universe, while the four books that DO trade went through unguarded. When
    main_engine's entries were deleted the gate moved to _book_execute, the
    single choke point every book passes.

    Checked by source inspection rather than import, because importing
    trading_loop starts the live loop.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "trading_loop.py").read_text()
    start = src.index("def _book_execute")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert "_scan_is_trustworthy()" in body, (
        "the degraded-feed gate is not in the book execute path — an exchange "
        "outage would again read as a quiet market for the books that trade")
    assert "degraded_feed" in body


def test_the_dust_floor_guards_every_book():
    """The other half of what the deleted preflight used to cover. The floor
    lives in the executor, which every book reaches through _book_execute, so
    it survived the deletion — this pins that."""
    import inspect

    from hermes_trader.agents import executor
    src = inspect.getsource(executor.maybe_execute)
    assert "min_tradable_equity" in src


def test_main_engine_entries_are_gone():
    """W-ME1: refuted by backtest, and structurally unable to fire on the
    majors universe (0 signals in 17 days at the live gate of 54, peak 45.9)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "trading_loop.py").read_text()
    assert "MAIN_ENGINE_DELETED" in src
    assert "_fresh_entry_preblock_reason" not in src


def test_ai_closes_survived_the_deletion():
    """A standing hard operator requirement: "AI powered closes are a MUST
    HAVE". Only main_engine's ENTRIES were removed; held coins still get their
    throttled AI close-check."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "trading_loop.py").read_text()
    assert "held_research_ms" in src, "the held-coin AI close throttle is gone"
    assert "analysis = research(coin, perception)" in src, (
        "the research call the AI close path depends on was removed")
    assert "route_verdict" in src
