"""The rule on a hand-built swing: zigzag, level, stop, target, the gates,
the size and the ratchet. No network."""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.fib_sleeve import rule
from services.fib_sleeve.config import SleeveConfig
from services.fib_sleeve.tests import _scenario as S
from services.strategies.backtest.desk_tick_sim import risk_capped_notional
from services.strategies.evals.trend_gate_test import base_params

CFG = SleeveConfig()
LEVEL = S.EXTREME - 0.382 * S.SIZE          # 1018.658
TARGET = S.EXTREME + 0.618 * S.SIZE         # 1049.658
SATURDAY = S.T0 + 5 * 24 * S.HOUR + 12 * S.HOUR


def _plan(mirror=False, price=None, extra=(), cfg=CFG, max_lev=20.0, resting=False, now=S.NOW):
    hs = S.hours(mirror)
    bars = rule.closed(S.quarters(hs) + list(extra), S.Q, now)
    if price is None:
        price = 976.0 if mirror else 1024.0
    return rule.plan_entry(cfg, hs, bars, price, now, max_lev, resting=resting)


def test_zigzag_names_the_last_confirmed_leg():
    swing, confirmed_ms, atr = rule.current_swing(S.hours(), 2.0)
    assert (swing.side, swing.origin, swing.extreme) == (1, S.ORIGIN, S.EXTREME)
    assert swing.t_ext == S.T0 + 29 * S.HOUR                 # the bar that made the high
    assert confirmed_ms == S.T0 + 31 * S.HOUR                # known at the close of hour 30
    assert atr == pytest.approx(S.atr14(S.hours()))


def test_only_closed_bars_are_read():
    hs = S.hours()
    assert len(rule.closed(hs, S.HOUR, S.T0 + 30 * S.HOUR + 1)) == 30    # hour 30 still forming
    swing, _, _ = rule.current_swing(rule.closed(hs, S.HOUR, S.T0 + 30 * S.HOUR + 1), 2.0)
    assert swing is None or swing.extreme != S.EXTREME                    # the high is not confirmed yet


def test_long_plan_level_stop_target():
    p = _plan()
    atr = S.atr14(S.hours())
    assert p["ok"] and p["side"] == "long" and p["variant"] == "zz2_with_L382_limit_atr1_T618_be75"
    assert p["entry"] == pytest.approx(LEVEL)                 # the 38.2% retracement
    assert p["stop"] == pytest.approx(LEVEL - atr)            # the level less 1 ATR14 (1h)
    assert p["target"] == pytest.approx(TARGET)               # the -61.8% extension
    assert p["r"] == pytest.approx(S.SIZE / atr, abs=0.01) and p["leverage"] == 20


def test_short_plan_is_the_mirror():
    p = _plan(mirror=True)
    atr = S.atr14(S.hours(True))
    assert p["ok"] and p["side"] == "short"
    assert p["entry"] == pytest.approx(2000 - LEVEL)
    assert p["stop"] == pytest.approx(2000 - LEVEL + atr) and p["stop"] > p["entry"]
    assert p["target"] == pytest.approx(2000 - TARGET) and p["target"] < p["entry"]


def test_long_only_direction_skips_a_down_swing():
    p = _plan(mirror=True, cfg=dataclasses.replace(CFG, direction="long"))
    assert not p["ok"] and p["reason"] == "down_swing_long_only"


def test_level_traded_through_is_spent():
    t = S.T0 + 31 * S.HOUR
    through = Candle(t=t, o=1024, h=1024, l=LEVEL - 0.02, c=1020, v=1)
    p = _plan(extra=[through], now=t + S.Q + 1, price=1020.0)
    assert not p["ok"] and p["reason"] == "level_spent"
    touch = Candle(t=t, o=1024, h=1024, l=LEVEL, c=1020, v=1)     # a bare touch is not a trade-through
    assert _plan(extra=[touch], now=t + S.Q + 1, price=1020.0)["ok"]


def test_price_beyond_the_level_places_nothing_unless_the_order_already_rests():
    p = _plan(price=LEVEL - 1.0)
    assert not p["ok"] and p["reason"] == "price_beyond_level"
    assert _plan(price=LEVEL - 1.0, resting=True)["ok"]


def test_weekend_and_reopen_bar_place_nothing():
    assert _plan(now=SATURDAY)["reason"] == "weekend"
    sunday_reopen = S.T0 + 6 * 24 * S.HOUR + 23 * S.HOUR         # Sun 23:00Z = 18:00 New York (winter)
    assert rule.session_block(sunday_reopen - 1) == "weekend"
    assert rule.session_block(sunday_reopen + 60_000) == "reopen_bar"
    assert rule.session_block(sunday_reopen + S.Q + 1) is None


def test_gates_min_r_leverage_and_missing_history():
    assert _plan(cfg=dataclasses.replace(CFG, min_r=50.0))["reason"] == "min_r"
    assert _plan(max_lev=None)["reason"] == "leverage"
    hs = S.hours()
    late = [b for b in S.quarters(hs) if b.t > S.T0 + 30 * S.HOUR]   # history starts after the extreme
    p = rule.plan_entry(CFG, hs, late, 1024.0, S.NOW, 20.0)
    assert p["reason"] == "swing_older_than_15m_history"
    assert rule.plan_entry(CFG, hs[:10], [], 1000.0, S.NOW, 20.0)["reason"] == "no_swing"


# ── size ──────────────────────────────────────────────────────────────────

def test_size_comes_from_the_equity_given_at_six_percent_risk():
    p = _plan()
    one = rule.size_for(CFG, 20.06, p["leverage"], p["entry"], p["stop"], 2)
    two = rule.size_for(CFG, 40.12, p["leverage"], p["entry"], p["stop"], 2)
    assert one["size"] == 0.31 and one["capped_by"] == "risk"
    assert one["risk_usd"] <= 0.06 * 20.06 and one["risk_pct_of_equity"] <= 6.0
    assert two["size"] == pytest.approx(2 * one["size"], abs=0.011)
    assert rule.size_for(CFG, 0.0, 20, p["entry"], p["stop"], 2)["size"] == 0.0


def test_size_matches_the_backtests_formula_and_rounds_down():
    p = _plan()
    params = dataclasses.replace(base_params(), taker_fee=CFG.taker_fee, slippage=CFG.slippage,
                                 risk_cap_frac=CFG.risk_frac)
    want = risk_capped_notional(20.06, params, p["leverage"], p["entry"], p["stop"])
    got = rule.size_for(CFG, 20.06, p["leverage"], p["entry"], p["stop"], 2)
    assert got["notional"] <= want and want - got["notional"] < 0.01 * p["entry"]   # under one size step


def test_a_tight_stop_is_capped_by_leverage_not_by_risk():
    s = rule.size_for(CFG, 20.06, 20, 100.0, 99.95, 2)
    assert s["capped_by"] == "leverage" and s["notional"] <= 20 * 20.06


# ── ratchet ───────────────────────────────────────────────────────────────

def _bar(h, l, c, t=0):
    return Candle(t=t, o=c, h=h, l=l, c=c, v=1)


def test_ratchet_moves_the_stop_to_the_entry_at_75_percent():
    fill, target, stop = 100.0, 110.0, 97.0
    assert rule.ratchet_stop(CFG, True, fill, target, stop, [_bar(107.4, 101, 107)]) is None   # 74%
    assert rule.ratchet_stop(CFG, True, fill, target, stop, [_bar(107.5, 101, 107)]) == 100.0  # 75%
    assert rule.ratchet_stop(CFG, True, fill, target, stop, []) is None                        # no closed bar yet


def test_ratchet_never_moves_twice_never_widens_and_waits_for_room():
    fill, target = 100.0, 110.0
    reached = [_bar(108, 101, 107)]
    assert rule.ratchet_stop(CFG, True, fill, target, 100.0, reached) is None      # already at the entry
    assert rule.ratchet_stop(CFG, True, fill, target, 100.004, reached) is None    # a rounding remainder
    assert rule.ratchet_stop(CFG, True, fill, target, 101.0, reached) is None      # a tighter stop stays
    back = [_bar(108, 99.9, 100.005)]                                              # close within a tick of the entry
    assert rule.ratchet_stop(CFG, True, fill, target, 97.0, back) is None


def test_ratchet_short_and_off_and_be50():
    assert rule.ratchet_stop(CFG, False, 100.0, 90.0, 103.0, [_bar(99, 92.5, 93)]) == 100.0
    assert rule.ratchet_stop(CFG, False, 100.0, 90.0, 103.0, [_bar(99, 92.6, 93)]) is None
    off = dataclasses.replace(CFG, ratchet="off")
    assert rule.ratchet_stop(off, True, 100.0, 110.0, 97.0, [_bar(109, 101, 108)]) is None
    be50 = dataclasses.replace(CFG, ratchet="be50")
    assert rule.ratchet_stop(be50, True, 100.0, 110.0, 97.0, [_bar(105, 101, 104)]) == 100.0
