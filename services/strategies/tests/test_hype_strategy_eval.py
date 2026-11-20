"""Gate tests for the HYPE strategy eval's own new pieces
(services/strategies/evals/hype_strategy_test.py,
docs/strategy/HYPE_BACKTEST_2026-11-20.md). This module reuses the sp500
eval's engine wholesale (Costs, Tick/Order/Trade, build_ticks, simulate,
summarize, verdict, pick, Dataset, edge_rule -- all already gate-tested in
test_sp500_strategy_eval.py); these tests cover only what HYPE's own module
adds: the Fibonacci leg/tick builder and rule, the edge24/trend variant
split, and the venue's own 10x leverage line. Synthetic candles only: no
network, no snapshot, no account."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.evals import hype_strategy_test as H

T0 = 1_790_000_000_000 - (1_790_000_000_000 % H.HOUR_MS)     # an exact UTC hour
NO_COST = H.Costs(fee=0.0, slip=0.0)
NO_FUND = ([], [])


def bar(i, o, h, l, c, step=H.HOUR_MS):
    return Candle(t=T0 + i * step, o=o, h=h, l=l, c=c, v=1.0)


# ── the venue line (10x, not 50x) ─────────────────────────────────────────

def test_at_10x_liquidation_is_about_5_3_percent_away_and_the_stop_may_use_half():
    b = H.leverage_budget(10.0, H.VENUE_MAX_LEVERAGE)
    assert b["liq_dist_pct"] == pytest.approx(5.263, abs=0.01)
    assert b["max_stop_pct"] == pytest.approx(2.632, abs=0.01)
    assert H.VENUE_MAX_LEVERAGE == 10.0


def test_costs_are_hypes_own_main_dex_fee_not_borrowed_from_sp500():
    """4.5bps taker, not the xyz dex's 0.9bps growth-mode rate."""
    assert H.MEASURED.fee == pytest.approx(0.00045) and H.MEASURED.slip == pytest.approx(0.0001)
    assert H.STRESS.fee == pytest.approx(0.0006) and H.STRESS.slip == pytest.approx(0.0003)
    from services.strategies.evals.sp500_strategy_test import MEASURED as SP_MEASURED
    assert H.MEASURED.fee != SP_MEASURED.fee


# ── the fib leg ──────────────────────────────────────────────────────────

def _bars(highs_lows):
    return [bar(i, h - 1, h, l, h - 0.5) for i, (h, l) in enumerate(highs_lows)]


def test_leg_from_reads_the_later_extreme_as_the_legs_direction():
    # low at index 1, high at index 4: an up-leg (low -> high)
    up = _bars([(10, 9), (10, 5), (10, 6), (10, 7), (12, 8), (10, 7)])
    leg = H.leg_from(up)
    assert leg is not None and leg.up is True
    assert (leg.high, leg.low, leg.height) == (12.0, 5.0, 7.0)
    # high at index 1, low at index 4: a down-leg (high -> low)
    down = _bars([(10, 9), (12, 9), (10, 8), (10, 7), (10, 5), (10, 7)])
    leg = H.leg_from(down)
    assert leg is not None and leg.up is False
    assert (leg.high, leg.low) == (12.0, 5.0)


def test_leg_from_is_none_with_too_little_history_or_a_degenerate_leg():
    assert H.leg_from([]) is None
    assert H.leg_from(_bars([(10, 5)])) is None
    # the same bar carries both the high and the low: no leg
    one = [bar(0, 10, 10, 5, 7)]
    assert H.leg_from(one) is None


# ── the fib ticks (lookback_h is a grid axis, baked in at build time) ─────

def wave_bars(n):
    """Hourly bars walking 60 -> 70 -> 60 every 24 hours: a clean up-leg then
    down-leg, the same shape sp500_strategy_test.wave_bars uses."""
    out, prev = [], 60.0
    for i in range(n):
        k = i % 24
        c = 60.0 + 10.0 / 12.0 * (k if k <= 12 else 24 - k)
        out.append(bar(i, prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c))
        prev = c
    return out


def test_build_fib_ticks_reads_only_closed_bars_and_the_right_lookback():
    bars = wave_bars(72)
    ticks = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=24)
    assert ticks[23] is None and ticks[24] is not None          # 25 closed bars needed (the ATR floor)
    tk = ticks[48]
    assert tk.leg is not None
    assert tk.t == bars[48].t + H.HOUR_MS and tk.mid == bars[48].c
    assert tk.leg == H.leg_from(bars[48 - 24:48])               # exactly the window leg_from reads alone
    # a lookback shorter than one full wave cycle (24h) sees only part of
    # the move at a mid-descent index (70: k=70%24=22, still falling, with
    # enough history behind it for both a 6h and a 48h window), so it reads
    # a smaller height than the full-cycle window does
    short = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=6)[70]
    long_ = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=48)[70]
    assert short is not None and long_ is not None
    assert short.leg.height < long_.leg.height


# ── the fib rule ─────────────────────────────────────────────────────────

LB = 25   # the lookback_h used below, chosen to equal the bars list length (see _impulse_then_retrace_bars)


def _impulse_then_retrace_bars():
    """25 zero-wick bars (h = l = c, so the leg's high/low are exact), sized
    like a real HYPE move (a few percent of price, not a round trip -- the
    10x leverage line needs a stop inside 2.63% of price): a clean 60 -> 62
    impulse (indices 0-9, low=60 at index 0, high=62 at index 9: an
    up-leg), then a pullback that settles at 60.9 -- inside the 50% (61.0)
    / 61.8% (60.764) retrace band of that 2-point move -- and holds there
    through the rest of the window."""
    out = []
    for k in range(10):                                                     # the impulse: 60 -> 62
        c = 60.0 + k * (2.0 / 9.0)
        out.append(bar(k, c, c, c, c))
    for k in range(10):                                                     # the retrace: 62 -> 60.9, then hold
        c = max(60.9, 62.0 - k * 0.11)
        out.append(bar(10 + k, c, c, c, c))
    for k in range(5):
        out.append(bar(20 + k, 60.9, 60.9, 60.9, 60.9))
    assert len(out) == LB
    return out


def test_fib_rule_enters_in_the_50_to_61_8_retrace_band_in_the_legs_direction():
    bars = _impulse_then_retrace_bars()
    ticks = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=LB)
    rule = H.fib_rule(lookback_h=LB, k_atr=0.0, floor_frac=0.0, target_mode="extreme", leverage=10.0)
    i = len(bars) - 1                                     # the last bar: settled at 60.9, inside the band
    tk, leg = ticks[i], ticks[i].leg
    assert leg is not None and leg.up is True and (leg.high, leg.low) == (62.0, 60.0)
    o = rule(i, ticks, bars, {})
    assert o is not None, "60.9 should sit inside the 2-point move's 50/61.8% retrace band (61.0/60.764)"
    band_lo, band_hi = leg.high - H.FIB_RETRACE_FAR * leg.height, leg.high - H.FIB_RETRACE_NEAR * leg.height
    assert band_lo <= tk.mid <= band_hi and (band_lo, band_hi) == pytest.approx((60.764, 61.0))
    assert o.side == "long"
    assert o.stop == pytest.approx(leg.high - H.FIB_STOP_LEVEL * leg.height, abs=1e-6)   # the 78.6% level
    assert o.tp == leg.high                               # target_mode="extreme"


def test_fib_rule_ext127_target_is_27_2_pct_of_the_legs_height_past_the_extreme():
    bars = _impulse_then_retrace_bars()
    ticks = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=LB)
    extreme = H.fib_rule(lookback_h=LB, k_atr=0.0, floor_frac=0.0, target_mode="extreme", leverage=10.0)
    ext127 = H.fib_rule(lookback_h=LB, k_atr=0.0, floor_frac=0.0, target_mode="ext127", leverage=10.0)
    i = len(bars) - 1
    a, b = extreme(i, ticks, bars, {}), ext127(i, ticks, bars, {})
    assert a is not None and b is not None
    leg = ticks[i].leg
    assert b.tp == pytest.approx(leg.high + 0.272 * leg.height, abs=1e-6)
    assert b.side == a.side and b.stop == a.stop


def test_fib_rule_refuses_when_not_flat_outside_the_band_or_when_the_leverage_gate_fails():
    bars = _impulse_then_retrace_bars()
    ticks = H.build_fib_ticks(bars, H.HOUR_MS, bars, lookback_h=LB)
    i = len(bars) - 1
    rule = H.fib_rule(lookback_h=LB, k_atr=0.0, floor_frac=0.0, target_mode="extreme", leverage=10.0)
    # not flat: never answers, even where the plain rule would trade
    assert rule(i, ticks, bars, {}, False) is None
    # outside the band: a mid moved back up near the leg's own high, well
    # past the 50% retrace level (150) -- the same leg, a different mid
    out_of_band = ticks[:i] + [H.FibTick(t=ticks[i].t, mid=190.0, atr=ticks[i].atr, leg=ticks[i].leg,
                                         weekend=ticks[i].weekend)]
    assert rule(i, out_of_band, bars, {}) is None
    # a stop too wide for 10x (an absurdly high floor_frac forces it)
    wide = H.fib_rule(lookback_h=LB, k_atr=0.0, floor_frac=0.20, target_mode="extreme", leverage=10.0)
    assert wide(i, ticks, bars, {}) is None


# ── variants: the edge24/trend split and the fib grid ─────────────────────

def test_edge24_and_trend_are_disjoint_and_both_sit_on_the_shared_grid():
    edge = H.edge_variants("edge24", H.SIDES_EDGE)
    trend = H.edge_variants("trend", H.SIDES_TREND)
    assert set(edge) & set(trend) == set()
    assert len(edge) == len(H.K_ATRS) * len(H.FLOORS) * len(H.OFFSETS)          # one side mode
    assert len(trend) == len(H.K_ATRS) * len(H.FLOORS) * len(H.OFFSETS) * 2     # two side modes
    assert all(v["family"] == "edge24" for v in edge.values())
    assert all(v["family"] == "trend" for v in trend.values())
    assert all(v["kw"]["sides"] == "both" for v in edge.values())
    assert all(v["kw"]["sides"] in ("trend120", "trend24") for v in trend.values())


def test_fib_variants_grid_size_and_shape():
    fv = H.fib_variants()
    assert len(fv) == len(H.FIB_LOOKBACKS) * len(H.FIB_K_ATRS) * len(H.FIB_FLOORS) * len(H.FIB_TARGETS)
    assert all(v["family"] == "fib" and v["kind"] == "fib" for v in fv.values())
    one = fv[f"fib_lb24_k0_f0_extreme_{H.VENUE_MAX_LEVERAGE:g}x"]
    assert one["kw"] == {"lookback_h": 24, "k_atr": 0.0, "floor_frac": 0.0, "target_mode": "extreme",
                         "leverage": H.VENUE_MAX_LEVERAGE}


def test_fib_neighbours_steps_one_knob_at_a_time_same_target_mode_and_leverage():
    named = H.fib_variants()
    name = f"fib_lb24_k0.5_f0.25_extreme_{H.VENUE_MAX_LEVERAGE:g}x"
    nb = H.fib_neighbours(name, named)
    for n in nb:
        v = named[n]
        assert v["kw"]["target_mode"] == "extreme" and v["kw"]["leverage"] == H.VENUE_MAX_LEVERAGE
    # this cell sits in the MIDDLE of all three grid dimensions (lookback_h
    # 24 of 12/24/48/72, k_atr 0.5 of 0/0.5/1.0, floor 0.25 of 0/0.25/0.5),
    # so each gives two neighbours (one step either way): 6 total.
    assert len(nb) == 6
    assert f"fib_lb24_k0_f0.25_extreme_{H.VENUE_MAX_LEVERAGE:g}x" in nb       # one step down in k_atr
    assert f"fib_lb24_k1_f0.25_extreme_{H.VENUE_MAX_LEVERAGE:g}x" in nb       # one step up in k_atr
    assert f"fib_lb12_k0.5_f0.25_extreme_{H.VENUE_MAX_LEVERAGE:g}x" in nb     # one step down in lookback_h
    assert f"fib_lb48_k0.5_f0.25_extreme_{H.VENUE_MAX_LEVERAGE:g}x" in nb     # one step up in lookback_h
    # a different target_mode is never a neighbour
    assert all(named[n]["kw"]["target_mode"] == "extreme" for n in nb)


def test_make_rule_dispatches_edge_vs_fib_by_kind():
    edge_v = {"kind": "edge", "kw": {"k_atr": 1.0, "floor_frac": 0.0, "offset_frac": 0.0, "sides": "both",
                                     "entry_lookback_h": 24, "leverage": 10.0}}
    fib_v = {"kind": "fib", "kw": {"lookback_h": 24, "k_atr": 0.0, "floor_frac": 0.0, "target_mode": "extreme",
                                   "leverage": 10.0}}
    assert callable(H.make_rule(edge_v)) and callable(H.make_rule(fib_v))


def test_live_variant_reads_hype_rule_and_sits_on_the_trend_grid():
    live = H.live_variant()
    assert live["kw"] == {"k_atr": NT.HYPE_RULE["k_atr"], "floor_frac": NT.HYPE_RULE["floor_frac"],
                          "offset_frac": NT.target_offset_frac_for(H.COIN), "sides": "trend120",
                          "entry_lookback_h": 24, "leverage": NT.HYPE_LEVERAGE, "fit": NT.leverage_fit_for(H.COIN)}
    name = H.edge_name("edge24", live["kw"]["k_atr"], live["kw"]["floor_frac"], live["kw"]["offset_frac"],
                       live["kw"]["sides"], H.VENUE_MAX_LEVERAGE)
    assert name in H.edge_variants("trend", H.SIDES_TREND)
