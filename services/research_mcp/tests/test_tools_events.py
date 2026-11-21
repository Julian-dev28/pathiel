"""services/research_mcp/tools.py: events_tool / scheduled_calendar.

All offline: calendar_catalysts/scheduled_events read the repo's checked-in
services/intel/data/scheduled_events.json, no network. The use_overlay=False
plumbing itself is proven separately in services/intel/tests/
test_scheduled_events.py and test_sources_catalysts.py; these tests cover
this tool's own composition and coin handling.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.research_mcp import tools


def test_events_tool_default_covers_every_supported_coin():
    out = tools.events_tool(days=3)
    assert set(out["coins"]) == {"xyz:BRENTOIL", "HYPE", "BTC", "ETH", "xyz:SP500"}
    assert out["sources_failed"] == []


def test_oil_metals_sp500_hype_coins_get_the_curated_calendar():
    out = tools.events_tool(coins=["BRENTOIL"], days=30)
    row = out["coins"]["xyz:BRENTOIL"]
    assert "note" not in row
    assert isinstance(row["events"], list)
    # active_blackout is either None or a dict with the blackout shape; just
    # prove the key exists and the call didn't blow up building it.
    assert "active_blackout" in row


def test_btc_eth_have_no_curated_calendar_group_but_get_a_clear_note():
    out = tools.events_tool(coins=["BTC", "ETH"], days=7)
    for coin in ("BTC", "ETH"):
        row = out["coins"][coin]
        assert row["events"] == []
        assert row["active_blackout"] is None
        assert "note" in row and "oil/metals/sp500/hype" in row["note"]


def test_an_unsupported_coin_is_reported_not_raised():
    out = tools.events_tool(coins=["BTC", "DOGE"])
    assert "BTC" in out["coins"]
    assert "DOGE" not in out["coins"]
    assert len(out["sources_failed"]) == 1 and "DOGE" in out["sources_failed"][0]


def test_days_is_clamped_to_a_sane_range():
    assert tools.events_tool(coins=["BTC"], days=0)["window_days"] == 1.0
    assert tools.events_tool(coins=["BTC"], days=10_000)["window_days"] == 60.0
    assert tools.events_tool(coins=["BTC"], days=None)["window_days"] == 7.0


def test_coin_aliases_are_normalized_to_the_canonical_key():
    out = tools.events_tool(coins=["brent", "bitcoin"])
    assert set(out["coins"]) == {"xyz:BRENTOIL", "BTC"}


def test_scheduled_calendar_curated_coin_uses_use_overlay_false(monkeypatch):
    """Pins that this tool goes through the keyless-safe path: a direct
    sanity check that env.state_dir() is never reached for a curated coin."""
    from services.intel import env

    def _boom(*a, **kw):
        raise AssertionError("env.state_dir() must not be called")
    monkeypatch.setattr(env, "state_dir", _boom)
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    row = tools.scheduled_calendar("xyz:BRENTOIL", now, days=7)
    assert isinstance(row["events"], list)
