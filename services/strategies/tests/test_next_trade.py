"""services/strategies/next_trade.py and GET /api/desks/{id}/next: the Desk
page's "next trade" box must say what the tick would actually do.

2026-10-30, operator: "is the call part of the fe still valid after the
thesis and desk tick" -> "fix it make the fe be in sync with the ticks". The
card showed the thesis's own bracket (113.87 / 111.29 / 118.87 at 5x, 1.9R,
stop using 88% of the way to liquidation) while every tick that hour logged
"market long pays 0.07R at 113.17 (stop 108.26, tp 114.07 offset 113.50)".
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import journal, pipeline
from services.strategies.backtest.desk_tick_sim import Direction, _setup, live_params
from services.strategies.next_trade import BRENT_STOP_BUFFER, next_trade, stop_buffer_for
from services.strategies.tests.conftest import make_thesis, minimal_analytics


def _nt(mid, low=109.01, high=114.07, entry=113.87, side="long", lev=20):
    return next_trade(side, mid, low, high, thesis_entry=entry, leverage=lev,
                      coin_max_leverage=20, stop_buffer=0.75)


def test_the_10_08z_tick_reads_the_same_on_the_page():
    """The 10:08Z tick: mid 113.135, 0.07R, stop 108.26, tp 113.50."""
    n = _nt(113.135)
    assert n["ok"] is False and n["r_now"] == 0.07
    assert n["stop_px"] == pytest.approx(108.26) and n["tp_px"] == pytest.approx(113.4997, abs=1e-3)
    assert "pays 0.07R at 113.135, needs 2R" in n["blockers"]
    # where it would buy, and why 20x is blocked there (max 17x, as projected 2026-10-30)
    assert n["trigger_px"] == pytest.approx(110.0066, abs=1e-3)
    assert n["trigger_leverage_ok"] is False and n["max_leverage_ok"] == 17
    assert n["verdict"] == "waits: buy at 110.01 or lower, but no 20x there (max 17x)"


def test_once_the_range_tightens_it_buys_at_the_mid():
    n = _nt(111.30, low=111.03)
    assert n["ok"] is True and n["blockers"] == [] and n["verdict"] == "buys now"
    assert n["entry_px"] == 111.30 and n["stop_px"] == pytest.approx(110.28)
    assert n["r_to_targets"][0]["r_multiple"] >= 2.0


def test_never_above_the_thesis_entry():
    n = _nt(111.30, low=111.03, entry=111.20)
    assert not n["ok"] and any("never chase" in b for b in n["blockers"])
    assert n["trigger_px"] == pytest.approx(111.20)          # the ceiling caps the buy price


def test_a_short_thesis_mirrors_it():
    n = _nt(113.70, low=109.01, high=114.07, entry=113.50, side="short")
    assert n["side"] == "short" and n["stop_px"] == pytest.approx(114.82)
    assert n["tp_px"] == pytest.approx(109.01 * 1.005)
    assert n["trigger_px"] >= 113.50                          # never sells below the thesis entry


def test_a_flat_thesis_has_no_next_trade():
    n = next_trade("flat", 112.0, 109.0, 114.0, thesis_entry=None, leverage=20,
                   coin_max_leverage=20, stop_buffer=0.75)
    assert n["ok"] is False and "no trade until it calls a side" in n["reason"]


@pytest.mark.parametrize("mid", [x / 100 for x in range(10920, 11400, 7)])
def test_the_page_and_the_backtest_agree_on_every_mid(mid):
    """The live rules as the backtest runs them (desk_tick_sim._setup with
    live_params()) and the page must reach the same yes/no at every price."""
    p = live_params()
    lv = {"entry_low": 109.01, "entry_high": 114.07, "target_low": 109.01, "target_high": 114.07, "atr14": 0.83}
    setup = _setup(Direction(side="long", entry_ceiling=113.87), mid, lv, p)
    sim_trades = setup is not None and "refused" not in setup
    assert _nt(mid)["ok"] is sim_trades


def test_the_stop_buffer_is_the_live_rule():
    assert BRENT_STOP_BUFFER == live_params().stop_buffer == 0.75
    assert stop_buffer_for("xyz:BRENTOIL", 0.83) == 0.75
    assert stop_buffer_for("xyz:GOLD", 10.0) == pytest.approx(13.0)
    assert stop_buffer_for("xyz:GOLD", None) is None


# ── GET /api/desks/{id}/next ────────────────────────────────────────────────

@pytest.fixture
def client(stub_live_analytics):
    analytics = minimal_analytics("oil", mid=113.135, coin="xyz:BRENTOIL")
    stub_live_analytics(analytics)
    pipeline.save_analytics("oil", analytics)
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_next_route_uses_the_live_range_and_the_thesis_side(client, monkeypatch):
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29,
                                    targets=[118.87, 122.20], leverage=5))
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": 113.135, "range_24h": {"low": 109.01, "high": 114.07}, "atr14_1h": 0.83})
    body = client.get("/api/desks/oil/next").json()
    assert body["source"] == "desk_rules" and body["coin"] == "xyz:BRENTOIL"
    assert body["r_now"] == 0.07 and body["leverage"] == 20
    assert body["stop_px"] != 111.29                          # the thesis's stop is not the tick's


def test_next_route_with_no_thesis_or_no_levels_says_so(client, monkeypatch):
    assert "no thesis yet" in client.get("/api/desks/oil/next").json()["reason"]
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29, targets=[118.87]))

    def boom(coin):
        raise RuntimeError("venue down")
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", boom)
    body = client.get("/api/desks/oil/next").json()
    assert body["ok"] is False and "venue down" in body["reason"]


def test_the_page_reads_the_next_trade_not_the_thesis_line():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    js = (root / "pathiel" / "static" / "oil_desk.js").read_text()
    html = (root / "pathiel" / "templates" / "oil_desk.html").read_text()
    assert "/next?leverage=" in js and "function paintNextTrade" in js
    assert "the desk's rule" in js                            # leverage row when flat
    assert "Thesis plan pays" in html and 'id="call-last-tick"' in html
    assert "Next trade, by the tick's rules" in html
