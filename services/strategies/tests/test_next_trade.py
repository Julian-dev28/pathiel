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
    assert stop_buffer_for("xyz:SILVER", 10.0) == pytest.approx(13.0)
    assert stop_buffer_for("xyz:SILVER", None) is None


def test_the_response_carries_the_actual_stop_buffer_used():
    """The page must show the real buffer (0.75 on Brent, ~1.3xATR on any
    other coin), never a hardcoded "0.75" literal (2026-10-31: silver joins
    oil, and silver's buffer is nowhere near 0.75)."""
    n = _nt(113.135)
    assert n["stop_buffer"] == 0.75
    n2 = next_trade("long", 56.9, 55.0, 59.0, thesis_entry=None, leverage=20,
                    coin_max_leverage=25, stop_buffer=1.3)
    assert n2["stop_buffer"] == 1.3


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
    # the trigger (about 110.0, stop 108.26, 1.6% away) is too wide for 20x:
    # since the 2026-11-04 leverage fit the tick trades it at 15x
    assert body["r_now"] == 0.07 and body["leverage"] == 15
    assert body["stop_px"] != 111.29                          # the thesis's stop is not the tick's


def test_next_route_with_no_thesis_or_no_levels_says_so(client, monkeypatch, caplog):
    assert "no thesis yet" in client.get("/api/desks/oil/next").json()["reason"]
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=113.87, stop_px=111.29, targets=[118.87]))

    def boom(coin):
        raise RuntimeError("venue down")
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", boom)
    with caplog.at_level("WARNING"):
        body = client.get("/api/desks/oil/next").json()
    # The answer names the failure; its text (URLs, internals) stays in the log
    # (2026-11-15, CodeQL py/stack-trace-exposure).
    assert body["ok"] is False and body["reason"].endswith("(RuntimeError)")
    assert "venue down" not in body["reason"] and "venue down" in caplog.text


def test_the_page_reads_the_next_trade_not_the_thesis_line():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    js = (root / "pathiel" / "static" / "oil_desk.js").read_text()
    html = (root / "pathiel" / "templates" / "oil_desk.html").read_text()
    assert "/next?leverage=" in js and "function paintNextTrade" in js
    assert "the desk's rule" in js                            # leverage row when flat
    assert "Thesis plan pays" in html and 'id="call-last-tick"' in html
    assert "Next trade, by the tick's rules" in html



def test_the_silver_stop_buffer_never_drops_under_0_8pct_of_the_mid():
    """2026-11-02: the live per-tick 1.3 x ATR buffer was about 0.21 on silver
    at 50 and two shorts were stopped by noise; the floor is 0.8% of the mid
    (0.40 at 50). It never narrows a wider ATR buffer and never touches Brent."""
    from services.strategies.next_trade import STOP_BUFFER_FLOOR_FRAC
    assert STOP_BUFFER_FLOOR_FRAC == 0.008
    assert stop_buffer_for("xyz:SILVER", 0.16, 50.0) == pytest.approx(0.40)        # floor wins
    assert stop_buffer_for("xyz:SILVER", 0.50, 50.0) == pytest.approx(0.65)        # ATR wider: kept
    assert stop_buffer_for("xyz:SILVER", 0.16) == pytest.approx(0.208)             # no mid: old rule
    assert stop_buffer_for("xyz:SILVER", None, 50.0) is None                       # no ATR: no line
    assert stop_buffer_for("xyz:BRENTOIL", 0.10, 110.0) == 0.75                    # Brent unchanged


def test_the_floor_refuses_the_two_shorts_stopped_on_2026_11_02():
    """The 00:51Z short (mid 49.93, ATR buffer ~0.21 over the 24h high 49.892)
    paid 2.31R live; with the 0.40 floor the same setup no longer clears 2R.
    (no_break=False: the mid sat over the 24h high, which the 11-04 rule
    refuses on its own; this pins the floor alone.)"""
    old = next_trade("short", 49.93, 49.194, 49.892, thesis_entry=49.70, leverage=20,
                     coin_max_leverage=25, stop_buffer=0.209, no_break=False)
    new = next_trade("short", 49.93, 49.194, 49.892, thesis_entry=49.70, leverage=20,
                     coin_max_leverage=25, stop_buffer=stop_buffer_for("xyz:SILVER", 0.161, 49.93),
                     no_break=False)
    assert old["ok"] and not new["ok"]


def test_live_rule_line_names_the_price_the_tick_trades_at_not_the_thesis():
    """2026-11-02: thesis said short 109.30, the live rule only sells near
    111.62, and the operator was told the wrong price."""
    from services.strategies.next_trade import live_rule_line, next_trade
    nt = next_trade("short", 108.985, 108.16, 112.33, thesis_entry=109.30, leverage=20,
                    coin_max_leverage=20, stop_buffer=0.75)
    line = live_rule_line(nt)
    assert not nt["ok"]
    assert line.startswith("Live rule: waits: sell at ")
    assert "not at the thesis price 109.3" in line
    assert f"stop {nt['stop_px']:g}" in line


def test_live_rule_line_when_it_trades_now_or_has_no_side():
    from services.strategies.next_trade import live_rule_line, next_trade
    nt = next_trade("short", 111.9, 108.92, 112.33, thesis_entry=109.30, leverage=10,
                    coin_max_leverage=20, stop_buffer=0.75)
    assert nt["ok"] and live_rule_line(nt).startswith("Live rule: sells now")
    assert "thesis price" not in live_rule_line(nt)
    flat = next_trade("flat", 110.0, 108.0, 112.0, thesis_entry=None, leverage=20,
                      coin_max_leverage=20, stop_buffer=0.75)
    assert live_rule_line(flat).startswith("Live rule: no trade")


# ── leverage fit (operator 2026-11-04: "TURN ON LEVERAGE FIT") ──────────────

def test_fit_leverage_keeps_20x_when_the_stop_fits():
    from services.strategies.next_trade import LEVERAGE_FIT, fit_leverage
    lev, gate = fit_leverage(103.61, 102.99, 20.0, 20.0, LEVERAGE_FIT)   # the 2026-11-03 first long
    assert lev == 20.0 and gate["ok"]


def test_fit_leverage_drops_to_15x_never_lower():
    from pathiel.agents.bracket import check_leverage
    from services.strategies.next_trade import LEVERAGE_FIT, fit_leverage
    # a stop 1.8% away fails 20x but passes 15x
    assert not check_leverage(100.0, 98.2, 20.0, 20.0)["ok"]
    lev, gate = fit_leverage(100.0, 98.2, 20.0, 20.0, LEVERAGE_FIT)
    assert lev == 15.0 and gate["ok"]
    # 2.6% away fails 15x too: the rule refuses rather than going to 10x
    lev, gate = fit_leverage(100.0, 97.4, 20.0, 20.0, LEVERAGE_FIT)
    assert lev == 15.0 and not gate["ok"]
    assert LEVERAGE_FIT == (20.0, 15.0)


def test_fit_leverage_without_candidates_is_the_old_fixed_rule():
    from pathiel.agents.bracket import check_leverage
    from services.strategies.next_trade import fit_leverage
    lev, gate = fit_leverage(100.0, 97.4, 20.0, 20.0, ())
    assert lev == 20.0 and gate == check_leverage(100.0, 97.4, 20.0, 20.0)


def test_fit_leverage_never_exceeds_the_configured_or_venue_cap():
    from services.strategies.next_trade import fit_leverage
    assert fit_leverage(100.0, 99.5, 15.0, 20.0, (20.0, 15.0))[0] == 15.0
    assert fit_leverage(100.0, 99.5, 20.0, 15.0, (20.0, 15.0))[0] == 15.0


def test_brent_silver_and_gold_fit_their_leverage():
    # silver since 2026-11-04 with the breakout (SILVER_STRATEGY_TEST_2026-11-04.md, "Live config"),
    # gold since 2026-11-05 (GOLD_STRATEGY_TEST_2026-11-05.md)
    from services.strategies.next_trade import LEVERAGE_FIT, leverage_fit_for
    assert leverage_fit_for("xyz:BRENTOIL") == LEVERAGE_FIT == leverage_fit_for("xyz:SILVER")
    assert leverage_fit_for("xyz:GOLD") == LEVERAGE_FIT
    for coin in ("xyz:CL", "xyz:NATGAS", "BTC"):
        assert leverage_fit_for(coin) == ()


def test_next_trade_takes_a_wide_stop_long_at_15x_instead_of_waiting():
    from services.strategies.next_trade import LEVERAGE_FIT
    # 24h range 98.60-110: stop 97.85 is 1.7% under a 99.50 mid -> no 20x, 15x ok
    kw = dict(thesis_entry=None, leverage=20.0, coin_max_leverage=20.0, stop_buffer=0.75)
    fixed = next_trade("long", 99.5, 98.6, 110.0, **kw)
    fitted = next_trade("long", 99.5, 98.6, 110.0, leverage_fit=LEVERAGE_FIT, **kw)
    assert not fixed["ok"] and any("20x" in b for b in fixed["blockers"])
    assert fitted["ok"] and fitted["leverage"] == 15.0 and fitted["verdict"] == "buys now"
    assert fitted["stop_px"] == fixed["stop_px"]          # the stop never moves, only the leverage


def test_fit_leverage_without_a_venue_max_keeps_the_leverage_and_never_raises():
    from services.strategies.next_trade import LEVERAGE_FIT, fit_leverage
    for cands in ((), LEVERAGE_FIT):
        lev, gate = fit_leverage(100.0, 98.2, 20.0, None, cands)
        assert lev == 20.0 and gate["ok"] is False


def test_the_placeholder_page_escapes_what_it_echoes():
    """CodeQL py/reflective-xss (2026-11-15): the desk id reaches the page."""
    page = strategies_api._render_or_placeholder("no-such-template.html", "Desk: <script>x</script>",
                                                  "body <b>&</b>").body.decode()
    assert "<script>" not in page and "&lt;script&gt;" in page and "&amp;" in page


# ── rest at the trigger (operator, 2026-11-18: "yeah") ──────────────────────
# "Flat, slot free: a short waits 91.22" instead of waiting for a 15-minute
# tick to catch a touch at the level, REST that exact bracket as a limit
# order now. Same entry, stop, target, sizing and 2R gate; only the
# mechanism changes.

def test_distance_only_block_emits_a_rest_at_the_trigger():
    """The 10:08Z tick (test above): blocked on R alone at the current mid,
    everything else (side, no-break, never-chase) clear, and the trigger's
    own leverage line passes once Brent's leverage fit is on. `rest` is the
    exact bracket place_bracket takes to rest there."""
    from services.strategies.next_trade import LEVERAGE_FIT
    fitted = next_trade("long", 113.135, 109.01, 114.07, thesis_entry=113.87, leverage=20,
                        coin_max_leverage=20, stop_buffer=0.75, leverage_fit=LEVERAGE_FIT)
    assert not fitted["ok"] and fitted["trigger_leverage_ok"] is True
    assert fitted["rest"] == {"entry_px": pytest.approx(110.0066, abs=1e-3), "stop_px": 108.26,
                              "tp_px": 114.07}
    # tp_px is the STRUCTURAL target (114.07), not the offset 113.4997: place_bracket
    # applies TARGET_OFFSET_FRAC itself, exactly like the market-entry place_bracket object.
    assert fitted["rest"]["tp_px"] == fitted["structure_tp_px"]
    assert fitted["rest"]["entry_px"] == fitted["trigger_px"]
    assert fitted["rest"]["stop_px"] == fitted["stop_px"]


def test_no_rest_without_the_trigger_leverage_fit():
    """Without leverage_fit, 20x fails at the trigger (max 17x): the current
    mid's own 'stop too wide' blocker is irrelevant (it never checks the
    mid), but the trigger's OWN leverage line failing withholds the rest."""
    n = _nt(113.135)
    assert not n["ok"] and n["trigger_leverage_ok"] is False
    assert n["rest"] is None


def test_never_chase_never_rests():
    n = _nt(111.30, low=111.03, entry=111.20)
    assert not n["ok"] and n["rest"] is None


def test_no_entry_after_a_break_never_rests():
    n = next_trade("long", 108.0, 109.01, 114.07, thesis_entry=None, leverage=20,
                   coin_max_leverage=20, stop_buffer=0.75)
    assert not n["ok"] and any("no entry after a break" in b for b in n["blockers"]) and n["rest"] is None


def test_past_the_target_never_rests():
    """Nothing left to buy into: no trigger_px at all, so no rest either."""
    n = next_trade("long", 115.0, 109.01, 114.07, thesis_entry=None, leverage=20,
                   coin_max_leverage=20, stop_buffer=0.75)
    assert not n["ok"] and "nothing to buy into" in n["reason"] and n.get("rest") is None


def test_a_flat_or_missing_leverage_thesis_never_rests():
    assert next_trade("flat", 112.0, 109.0, 114.0, thesis_entry=None, leverage=20,
                      coin_max_leverage=20, stop_buffer=0.75).get("rest") is None
    assert next_trade("long", 112.0, 109.0, 114.0, thesis_entry=None, leverage=20,
                      coin_max_leverage=None, stop_buffer=0.75).get("rest") is None


def test_rest_mirrors_on_a_short():
    """Mid 110.0 sits low in the range: far from the target (109.01), so a
    short pays little R there and waits for a higher mid (the mirror of the
    long case, which waits for a lower one)."""
    from services.strategies.next_trade import LEVERAGE_FIT
    n = next_trade("short", 110.0, 109.01, 114.07, thesis_entry=109.5, leverage=20,
                   coin_max_leverage=20, stop_buffer=0.75, leverage_fit=LEVERAGE_FIT)
    assert not n["ok"] and n["rest"] is not None and n["r_now"] < 2.0
    assert n["rest"]["entry_px"] == n["trigger_px"] == pytest.approx(113.065017, abs=1e-5)
    assert n["rest"]["entry_px"] >= 109.5                          # never sells below the thesis entry
    assert n["rest"]["stop_px"] > n["rest"]["entry_px"]            # a short's stop sits above entry
    assert n["rest"]["tp_px"] == 109.01                            # structural, not the offset target


def test_already_tradeable_now_never_rests():
    """Once the live rule would already fire at market, there is nothing to
    rest: `ok` is True and `rest` stays None (place_bracket gets the market
    call, not a limit)."""
    n = _nt(111.30, low=111.03)
    assert n["ok"] is True and n["rest"] is None


def test_the_entry_delay_suppresses_a_rest_too():
    """2026-11-13's entry delay (off by default): while a thesis is too
    young to trade, it must not sit resting at the trigger either -- reaching
    the trigger price would still not make it tradable."""
    from services.strategies.next_trade import LEVERAGE_FIT
    kw = dict(thesis_entry=113.87, leverage=20, coin_max_leverage=20, stop_buffer=0.75,
              leverage_fit=LEVERAGE_FIT)
    without_delay = next_trade("long", 113.135, 109.01, 114.07, **kw)
    assert without_delay["rest"] is not None
    with_delay = next_trade("long", 113.135, 109.01, 114.07, thesis_age_min=5.0, entry_delay_min=120.0, **kw)
    assert with_delay["ok"] is False and with_delay["rest"] is None
    assert with_delay["entry_delay"]["blocked"] is True


def test_rest_price_is_stable_across_repeated_calls():
    """The same inputs must reproduce the exact same rest level every time
    (the keep/cancel rule below trusts this): no hidden clock or RNG."""
    from services.strategies.next_trade import LEVERAGE_FIT
    kw = dict(thesis_entry=113.87, leverage=20, coin_max_leverage=20, stop_buffer=0.75,
              leverage_fit=LEVERAGE_FIT)
    a = next_trade("long", 113.135, 109.01, 114.07, **kw)["rest"]
    b = next_trade("long", 113.135, 109.01, 114.07, **kw)["rest"]
    assert a == b


# ── keep / cancel / re-rest a desk's own resting entry (2026-11-18) ─────────

def test_keep_the_resting_entry_at_the_same_level_and_side():
    from services.strategies.next_trade import resting_entry_action
    rest = {"entry_px": 110.00655, "stop_px": 108.26, "tp_px": 114.07}
    out = resting_entry_action({"px": 110.00655, "side": "long"}, "long", rest)
    assert out == {"action": "keep", "reason": "still the live rule's trigger"}
    # within the float-rounding tolerance, not just an exact match
    close = resting_entry_action({"px": 110.00660, "side": "long"}, "long", rest)
    assert close["action"] == "keep"


def test_cancel_and_rerest_when_the_trigger_moved():
    from services.strategies.next_trade import resting_entry_action
    old = {"px": 110.00655, "side": "long"}
    new_rest = {"entry_px": 109.50, "stop_px": 108.26, "tp_px": 114.07}        # the 24h range shifted
    out = resting_entry_action(old, "long", new_rest)
    assert out["action"] == "cancel_and_rest" and out["rest"] == new_rest
    assert "trigger moved" in out["reason"]


def test_cancel_and_rerest_when_the_thesis_side_flips():
    from services.strategies.next_trade import resting_entry_action
    old = {"px": 110.00655, "side": "long"}
    short_rest = {"entry_px": 113.00, "stop_px": 114.82, "tp_px": 109.01}
    out = resting_entry_action(old, "short", short_rest)
    assert out["action"] == "cancel_and_rest" and out["rest"] == short_rest
    assert "re-rest" in out["reason"]


def test_cancel_with_no_rerest_when_the_rule_offers_nothing():
    from services.strategies.next_trade import resting_entry_action
    out = resting_entry_action({"px": 110.00655, "side": "long"}, "long", None)
    assert out == {"action": "cancel", "reason": "the live rule no longer offers a resting entry here"}


def test_the_tolerance_is_tight_enough_to_catch_a_real_range_move():
    """A one-cent move on Brent must still trigger cancel_and_rest: the
    tolerance is float-rounding only, never wide enough to paper over a
    genuine (if small) change in the 24h range or ATR."""
    from services.strategies.next_trade import RESTING_LEVEL_TOLERANCE, resting_entry_action
    assert RESTING_LEVEL_TOLERANCE < 0.01
    rest = {"entry_px": 110.01, "stop_px": 108.26, "tp_px": 114.07}
    out = resting_entry_action({"px": 110.00, "side": "long"}, "long", rest)
    assert out["action"] == "cancel_and_rest"
