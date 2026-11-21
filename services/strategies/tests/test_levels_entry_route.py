"""GET /api/desks/{desk_id}/levels's `entry` field (services/strategies/
api.py::_levels_entry, services/strategies/entry_card.py). Operator,
2026-11-19, on the Desk page's Levels card: "should add entry to FE
right?" — this pins the HTTP contract the Levels card actually fetches:
the response carries `entry`, and its numbers are byte-for-byte what
calling `next_trade()` directly, with the exact inputs the route used,
produces. No mocking of next_trade/entry_card themselves anywhere here —
only the network-shaped edges (venue levels, venue max leverage, the
agent-config read entry_delay_for falls back to) are stubbed, same as
test_next_trade.py's own route test.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import journal, pipeline
from services.strategies.next_trade import (breakout_for, leverage_fit_for, next_trade,
                                            stop_buffer_for)
from services.strategies.tests.conftest import make_thesis, minimal_analytics

MID, LOW, HIGH, ATR = 113.135, 109.01, 114.07, 0.83
COIN = "xyz:BRENTOIL"


@pytest.fixture
def client(stub_live_analytics, monkeypatch):
    analytics = minimal_analytics("oil", mid=MID, coin=COIN)
    stub_live_analytics(analytics)
    pipeline.save_analytics("oil", analytics)
    # Deterministic regardless of this machine's own ~/.pathiel config: the
    # entry delay is off by default (next_trade.ENTRY_DELAY_MIN is None),
    # and this test fixes that explicitly rather than depending on a
    # config read nobody here controls.
    monkeypatch.setattr(strategies_api.next_trade_mod, "entry_delay_for", lambda coin: None)
    # event_blackout() reads the real clock. On 2026-11-21 (a Saturday, in the
    # oil weekend window) every status here came back "blackout" with no code
    # change. Pin a quiet weekday minute, Wed 2026-11-18 10:08Z (the tick this
    # fixture's numbers come from); the EIA-window test sets its own time.
    from services.strategies import event_blackout as _EB
    _quiet = int(datetime(2026, 11, 18, 10, 8, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(_EB, "now_ms", lambda: _quiet)
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": MID, "range_24h": {"low": LOW, "high": HIGH}, "atr14_1h": ATR})
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def _expected_next_trade(thesis_entry: float, leverage: float = 20.0):
    """The exact call desk_next_trade()/the route make for this fixture,
    run directly — the "never re-derived" proof."""
    buf = stop_buffer_for(COIN, ATR, MID)
    return next_trade("long", MID, LOW, HIGH, thesis_entry=thesis_entry, leverage=leverage,
                      coin_max_leverage=20, stop_buffer=buf, leverage_fit=leverage_fit_for(COIN),
                      breakout=None if not breakout_for(COIN) else {}, thesis_age_min=None,
                      entry_delay_min=None)


def test_payload_keeps_the_existing_levels_fields(client):
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29,
                                    targets=[118.87, 122.20], leverage=5))
    body = client.get("/api/desks/oil/levels").json()
    # Nothing about the Mid/8h/24h/48h/ATR14/mid-position fields changed.
    assert body["mid"] == MID and body["range_24h"] == {"low": LOW, "high": HIGH}
    assert body["atr14_1h"] == ATR
    assert "entry" in body


def test_waiting_status_matches_next_trade_byte_for_byte(client):
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29,
                                    targets=[118.87, 122.20], leverage=5))
    body = client.get("/api/desks/oil/levels").json()
    e = body["entry"]
    assert e["has_thesis"] is True and e["side"] == "long" and e["thesis_entry_px"] == 113.87

    expected = _expected_next_trade(113.87)
    assert expected["ok"] is False and expected["r_now"] == 0.07   # sanity: the known 10:08Z tick
    live = e["live"]
    assert live["status"] == "waiting" and live["ok"] is False
    assert live["entry_px"] == expected["trigger_px"]
    assert live["stop_px"] == expected["stop_px"]
    assert live["tp_px"] == expected["tp_px"]
    assert live["r_multiple"] == expected["r_to_targets"][0]["r_multiple"]
    assert live["rule_verdict"] == expected["verdict"]
    dist = abs(MID - expected["trigger_px"])
    assert e["distance"]["price"] == pytest.approx(dist, abs=1e-6)
    assert e["distance"]["pct"] == pytest.approx(dist / MID, abs=1e-6)
    assert e["distance"]["atr"] == pytest.approx(dist / ATR, abs=1e-3)


def test_ready_status_when_the_thesis_entry_is_already_past_the_mid(client, monkeypatch):
    # A thesis entry at or above the mid (long) lets the rule trade right
    # at the mid once the range is tight enough to pay 2R there — reuse
    # the exact range test_next_trade.py's own "ready" case uses.
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": 111.30, "range_24h": {"low": 111.03, "high": HIGH}, "atr14_1h": ATR})
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29,
                                    targets=[118.87, 122.20], leverage=5))
    body = client.get("/api/desks/oil/levels").json()
    e = body["entry"]
    live = e["live"]
    assert live["status"] == "ready" and live["ok"] is True
    assert e["distance"] == {"price": 0.0, "pct": 0.0, "atr": 0.0}


def test_no_thesis_status_with_no_call_on_the_desk(client):
    body = client.get("/api/desks/oil/levels").json()
    e = body["entry"]
    assert e["has_thesis"] is False and e["side"] is None
    assert e["live"]["status"] == "no_thesis" and e["live"]["status_text"] == "no thesis"
    assert e["distance"] == {"price": None, "pct": None, "atr": None}


def test_blackout_status_during_the_live_eia_window(client, monkeypatch):
    """The real event_blackout() call, at a timestamp inside the sourced
    EIA window (test_event_blackout.py's own fixture) — the route's
    now_ms(), not a second clock, so this patches
    event_blackout.now_ms exactly where the route imports it from."""
    from services.strategies import event_blackout as EB
    from datetime import datetime, timezone
    at_ms = int(datetime(2026, 11, 4, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(EB, "now_ms", lambda: at_ms)
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": 111.30, "range_24h": {"low": 111.03, "high": HIGH}, "atr14_1h": ATR})
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29,
                                    targets=[118.87, 122.20], leverage=5))
    body = client.get("/api/desks/oil/levels").json()
    live = body["entry"]["live"]
    assert live["status"] == "blackout" and live["ok"] is False
    assert live["status_text"] == "blackout until 2026-11-04 17:30Z"
