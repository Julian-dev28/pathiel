"""Live silver breakout entry (services/strategies/next_trade.py), operator
2026-11-04: "turn on silver breakout, chase OK on silver". The live rule must
be the backtest's combo_best2 rule exactly (desk_tick_sim mode a5, measured
target, 24h lookback, 4h stop, leverage fit 20/15):
docs/strategy/SILVER_STRATEGY_TEST_2026-11-04.md."""
from __future__ import annotations

import dataclasses
import random

import pytest

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.backtest import desk_tick_sim as SIM
from services.strategies.evals.live_params import live_silver_params

SILVER = "xyz:SILVER"
H = SIM.HOUR_MS


def _rows(bars):
    return [{"h": b.h, "l": b.l, "c": b.c} for b in bars]


def _flat_then(close, n=24, lo=48.0, hi=50.0, last_h=None, last_l=None):
    bars = [Candle(t=i * H, o=49.0, h=hi, l=lo, c=49.0, v=1.0) for i in range(n)]
    bars.append(Candle(t=n * H, o=49.0, h=last_h if last_h is not None else max(49.0, close) + 0.05,
                       l=last_l if last_l is not None else min(49.0, close) - 0.05, c=close, v=1.0))
    return bars


# ── switches ─────────────────────────────────────────────────────────────

def test_breakout_and_leverage_fit_are_silver_only():
    assert NT.breakout_for(SILVER) and not NT.breakout_for("xyz:BRENTOIL") and not NT.breakout_for("xyz:CL")
    assert NT.leverage_fit_for(SILVER) == (20.0, 15.0) == NT.leverage_fit_for("xyz:BRENTOIL")
    assert (NT.BREAKOUT_LOOKBACK_H, NT.BREAKOUT_STOP_H) == (24, 4)


def test_live_constants_are_the_tested_combo():
    """The combo the silver test scored, with the operator's 15x floor."""
    p = live_silver_params(25.0, 0.4)
    assert p.pullback_entry is None          # the backtest baseline stays the old live rule
    assert SIM.PB_TRIGGERS == ("chase", "min_r")
    assert NT.LEVERAGE_FIT == (20.0, 15.0)


# ── breakout_levels ───────────────────────────────────────────────────────

def test_breakout_levels_reads_the_24_bars_before_the_last():
    bars = _flat_then(47.5, last_h=49.2, last_l=47.4)
    bo = NT.breakout_levels(_rows(bars))
    assert bo["prior_low"] == 48.0 and bo["prior_high"] == 50.0 and bo["last_close"] == 47.5
    assert bo["recent_high"] == 50.0 and bo["recent_low"] == 47.4   # last 4 bars, the last included
    assert NT.breakout_levels(_rows(bars[:24])) is None              # 24 bars: no "prior 24" yet


# ── breakout_trade ───────────────────────────────────────────────────────

def test_short_breakdown_sells_with_the_measured_target():
    bars = _flat_then(47.5, last_h=48.1, last_l=47.4)
    bo = NT.breakout_levels(_rows(bars))
    # stop beyond the last 4 bars' high (50.0 from the flat bars), measured target 48 - 2 = 46
    out = NT.breakout_trade("short", 47.5, bo, leverage=20, coin_max_leverage=25, stop_buffer=0.4)
    assert out["structure_tp_px"] == pytest.approx(46.0)
    assert out["tp_px"] == pytest.approx(46.0 * 1.005)
    assert out["stop_px"] == pytest.approx(50.4)
    assert not out["ok"] and "too wide for 20x" in out["reason"]     # a 2.9 stop on 47.5 needs 6x


def test_short_breakdown_trades_when_the_stop_is_tight():
    bo = NT.breakout_levels(_rows(_tight_breakdown()))
    out = NT.breakout_trade("short", 47.9, bo, leverage=20, coin_max_leverage=25, stop_buffer=0.38)
    assert out["ok"], out
    assert out["stop_px"] == pytest.approx(48.05 + 0.38)
    assert out["r_multiple"] >= 2.0 and out["leverage"] == 20


def test_no_close_through_the_level_no_trade():
    bo = NT.breakout_levels(_rows(_flat_then(48.2)))
    out = NT.breakout_trade("short", 48.2, bo, leverage=20, coin_max_leverage=25, stop_buffer=0.4)
    assert not out["ok"] and out["level"] == 48.0 and "no 1h close under" in out["reason"]
    assert "stop_px" not in out


def test_long_mirrors_short():
    bars = [Candle(t=i * H, o=49.0, h=50.0, l=48.0, c=49.0, v=1.0) for i in range(21)]
    bars += [Candle(t=(21 + i) * H, o=49.85, h=49.95, l=49.75, c=49.9, v=1.0) for i in range(3)]
    bars.append(Candle(t=24 * H, o=49.9, h=50.15, l=49.9, c=50.1, v=1.0))
    out = NT.breakout_trade("long", 50.1, NT.breakout_levels(_rows(bars)), leverage=20,
                            coin_max_leverage=25, stop_buffer=0.38)
    assert out["ok"], out
    assert out["structure_tp_px"] == pytest.approx(52.0)
    assert out["stop_px"] == pytest.approx(49.75 - 0.38)


def test_breakout_needs_the_20x_line():
    bo = {"prior_low": 48.0, "prior_high": 60.0, "last_close": 47.0, "recent_high": 52.0, "recent_low": 46.9,
          "lookback_h": 24, "stop_h": 4}
    out = NT.breakout_trade("short", 47.0, bo, leverage=20, coin_max_leverage=25, stop_buffer=0.4)
    assert not out["ok"] and "too wide for 20x" in out["reason"]


# ── next_trade: when the breakout is tried ─────────────────────────────────

def _tight_breakdown():
    """24 bars ranging 48-50 (the last 3 hugging 48.01-48.05), then a 1h
    close at 47.9 under the 48.0 low: stop 48.05 + buffer, about 1.1%
    (silver's 20x line sits near 1.47%)."""
    bars = [Candle(t=i * H, o=49.0, h=50.0, l=48.0, c=49.0, v=1.0) for i in range(21)]
    bars += [Candle(t=(21 + i) * H, o=48.03, h=48.05, l=48.01, c=48.02, v=1.0) for i in range(3)]
    bars.append(Candle(t=24 * H, o=48.02, h=48.03, l=47.85, c=47.9, v=1.0))
    return bars


def test_chase_refusal_on_silver_takes_the_breakout():
    bo = NT.breakout_levels(_rows(_tight_breakdown()))
    out = NT.next_trade("short", 47.9, 47.85, 50.0, thesis_entry=49.0, leverage=20, coin_max_leverage=25,
                        stop_buffer=0.38, leverage_fit=(20.0, 15.0), breakout=bo)
    assert out["ok"] and out["source"] == "breakout" and out["entry_px"] == 47.9
    assert out["rule_blockers"] == ["price is at the target already: nothing to sell into"]
    assert out["structure_tp_px"] == pytest.approx(46.0)
    assert "_pb_trigger" not in out and "_trigger_lev" not in out


def test_without_breakout_input_the_chase_still_refuses():
    out = NT.next_trade("short", 47.9, 47.85, 50.0, thesis_entry=49.0, leverage=20, coin_max_leverage=25,
                        stop_buffer=0.38, leverage_fit=(20.0, 15.0))
    assert not out["ok"] and "breakout" not in out and "_pb_trigger" not in out


def test_no_break_yet_says_where_it_would_sell():
    bo = NT.breakout_levels(_rows(_flat_then(48.6)))
    out = NT.next_trade("short", 48.6, 48.0, 50.0, thesis_entry=49.0, leverage=20, coin_max_leverage=25,
                        stop_buffer=0.4, leverage_fit=(20.0, 15.0), breakout=bo)
    assert not out["ok"] and out["breakout"]["ok"] is False
    assert out["verdict"].endswith("or sells a 1h close under 48")


def test_main_rule_trade_wins_over_the_breakout():
    bo = NT.breakout_levels(_rows(_tight_breakdown()))
    out = NT.next_trade("short", 49.9, 47.6, 50.0, thesis_entry=49.0, leverage=20, coin_max_leverage=25,
                        stop_buffer=0.2, leverage_fit=(20.0, 15.0), breakout=bo)
    assert out["source"] == "desk_rules"


# ── parity with the backtest's own code ───────────────────────────────────

def _bar(i, o, c, wick):
    return Candle(t=i * H, o=o, h=max(o, c) + wick, l=min(o, c) - wick, c=c, v=1.0)


def _walk(rng, n=40, px=50.0):
    """Half the time a plain random walk; half the time a wide range, a
    quiet drift to one edge and a last bar that may close through it (the
    breakout's own shape, so the parity test sees real breakouts)."""
    bars = []
    if rng.random() < 0.5:
        drift = rng.choice((-0.25, -0.1, 0.0, 0.1, 0.25))
        for i in range(n):
            o, px = px, max(5.0, px + drift + rng.gauss(0, 0.25))
            bars.append(_bar(i, o, px, abs(rng.gauss(0, 0.12))))
        return bars
    lo_, hi_ = px - rng.uniform(1.0, 3.0), px + rng.uniform(1.0, 3.0)
    for i in range(n - 5):
        o, px = px, min(hi_, max(lo_, px + rng.gauss(0, 0.5)))
        bars.append(_bar(i, o, px, abs(rng.gauss(0, 0.1))))
    down = rng.random() < 0.5
    edge = min(b.l for b in bars[-24:]) if down else max(b.h for b in bars[-24:])
    px = edge + (0.15 if down else -0.15)
    for i in range(n - 5, n - 1):
        o, px = px, edge + (1 if down else -1) * abs(rng.gauss(0.06, 0.04))
        bars.append(_bar(i, o, px, abs(rng.gauss(0, 0.02))))
    close = edge + (-1 if down else 1) * rng.gauss(0.05, 0.12)
    bars.append(_bar(n - 1, px, close, abs(rng.gauss(0, 0.02))))
    return bars


def _sim_decision(d, mid, lv, c1h, params):
    setup, lev = SIM._fit_leverage_setup(d, mid, lv, params)
    if setup is not None and setup.get("refused") in SIM.PB_TRIGGERS:
        pb = SIM.pullback_setup(d, c1h[-1].t + H, mid, lv, c1h, params, lev)
        if pb is not None and not pb.get("refused"):
            return pb, pb["leverage"]
    return setup, lev


def test_live_rule_is_the_backtested_combo_on_random_markets():
    """Thousands of random silver states: the live next_trade and the
    backtest's combo (`_fit_leverage_setup` then `pullback_setup` a5) agree on
    trade / no trade, which entry, the stop, the target and the leverage."""
    params = dataclasses.replace(live_silver_params(25.0, 0.4), pullback_entry="a5", pb_breakout_lookback_h=24,
                                 pb_breakout_stop_h=4, pb_target="measured", entry_fit_leverage=(20.0, 15.0))
    rng = random.Random(20261104)
    seen = {"breakout": 0, "desk_rules": 0, "none": 0}
    for _ in range(6000):
        c1h = _walk(rng)
        lv = SIM._levels(c1h, 24, 24)
        if not lv["atr14"] or lv["target_low"] is None:
            continue
        side = rng.choice(("long", "short"))
        mid = c1h[-1].c + rng.gauss(0, 0.1)
        entry = rng.choice((None, mid + rng.gauss(0, 0.8)))
        d = SIM.Direction(side=side, entry_ceiling=entry, regime_id="x")
        sim, sim_lev = _sim_decision(d, mid, lv, c1h, params)
        buf, _ = SIM._stop_buffer(params, lv, mid)
        live = NT.next_trade(side, mid, lv["target_low"], lv["target_high"], thesis_entry=entry, leverage=20.0,
                             coin_max_leverage=25.0, stop_buffer=buf, leverage_fit=(20.0, 15.0),
                             breakout=NT.breakout_levels(_rows(c1h)))
        sim_ok = sim is not None and not sim.get("refused")
        assert live["ok"] == sim_ok, (side, mid, entry, sim, live)
        if not sim_ok:
            seen["none"] += 1
            continue
        assert live["source"] == ("breakout" if sim.get("pb") == "a5" else "desk_rules")
        seen[live["source"]] += 1
        assert live["stop_px"] == pytest.approx(sim["stop_px"], abs=1e-5)
        assert live["tp_px"] == pytest.approx(sim["target_px"], abs=1e-5)
        assert live["leverage"] == sim_lev
    assert seen["breakout"] >= 30 and seen["desk_rules"] >= 30 and seen["none"] >= 300, seen


def _oil_levels():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "oil_levels.py")
    spec = importlib.util.spec_from_file_location("oil_levels_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _summary(bars, mid, atr14=0.2):
    """A summarize()-shaped answer; the ATR is pinned (the fixture's own wide
    flat bars would make the 1.3 x ATR buffer the whole range)."""
    from services.strategies.levels import price_range
    rows = [{"o": b.o, "h": b.h, "l": b.l, "c": b.c} for b in bars]
    return {"mid": mid, "range_24h": price_range(rows[-24:]), "atr14_1h": atr14,
            "breakout_24h": NT.breakout_levels(rows)}


def test_levels_script_prints_the_place_bracket_args_with_the_structural_target():
    ol = _oil_levels()
    out = ol.next_order(SILVER, "short", _summary(_tight_breakdown(), 47.9), 49.0, 20.0, 25.0)
    assert out["ok"] and out["source"] == "breakout"
    # place_bracket applies the 0.5% offset itself: it gets the measured level, not tp_px
    assert out["place_bracket"] == {"coin": SILVER, "stop_px": out["stop_px"], "tp_px": out["structure_tp_px"]}


def test_brent_never_takes_the_breakout():
    """Brent keeps never-chase: the same breakdown on Brent is refused."""
    ol = _oil_levels()
    out = ol.next_order("xyz:BRENTOIL", "short", _summary(_tight_breakdown(), 47.9), 49.0, 20.0, 20.0)
    assert not out["ok"] and "breakout" not in out and "place_bracket" not in out


# ── the eval's pass bar ───────────────────────────────────────────────────

def test_live_check_eval_variants_are_the_shipped_config():
    from services.strategies.evals import silver_breakout_live_check as E
    live = E.VARIANTS["LIVE_NEW"]
    assert live["entry_fit_leverage"] == NT.LEVERAGE_FIT
    assert (live["pb_breakout_lookback_h"], live["pb_breakout_stop_h"]) == (NT.BREAKOUT_LOOKBACK_H, NT.BREAKOUT_STOP_H)
    assert live["pullback_entry"] == "a5" and live["pb_target"] == "measured"
    rows = [{"dataset": "replay", "variant": "live", "net_usd": 1.0},
            {"dataset": "replay", "variant": "LIVE_NEW", "net_usd": 2.0}]
    assert E.passes(rows) and not E.passes([rows[0], dict(rows[1], net_usd=0.5)])
    ctrl = [{"dataset": "always_long", "variant": "live", "net_usd": -60.25},
            {"dataset": "always_long", "variant": "LIVE_NEW", "net_usd": -60.83}]
    assert E.passes(ctrl) and not E.passes([ctrl[0], dict(ctrl[1], net_usd=-62.0)])
