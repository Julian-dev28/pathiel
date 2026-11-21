"""Gate tests for the bidirectional fib sleeve study
(services/strategies/evals/fib_bidirectional_test.py, 2026-11-16): the
counter-trend extension math, the OCO fill/cancel logic (including parity
against `fib_retrace_test.walk`'s own with-swing math -- the live rule by
construction), the random-level baseline generator, the funding gate, the
regime split, and the holdout split. Synthetic bars only, no network, no
state read, no order, no config write.
"""
from __future__ import annotations

import dataclasses
import random
import time as _time
from collections import Counter
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.evals import fib_bidirectional_test as B
from services.strategies.evals import fib_retrace_test as F
from services.strategies.evals.fib_retrace_test import Swing

Q = F.FIFTEEN_MS
H = F.HOUR_MS


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


T0 = ms(2026, 11, 3, 8)
TAKER, MAKER = 0.00009, 0.00003
P = dataclasses.replace(F.base_params(1000.0, F.RISK_FULL), taker_fee=TAKER, maker_fee=MAKER)
UP = Swing(1, 100.0, 110.0, T0 - 2 * H, ("t", 1, T0 - 2 * H, 110.0))     # with 61.8%=103.82; counter ext .382=113.82
DOWN = Swing(-1, 110.0, 100.0, T0 - 2 * H, ("t", -1, T0 - 2 * H, 100.0))  # with 61.8%=106.18; counter ext .382=96.18
QUIET = (105.0, 105.2, 104.8, 105.0)        # away from every with-leg and counter-leg level used below


def ctx_of(rows, swing=DOWN, t0=T0, step=Q, atr=1.0, fund=None):
    t = [t0 + i * step for i in range(len(rows))]
    o, h, l, c = ([r[k] for r in rows] for k in range(4))
    sw = [swing] * len(rows)
    return F.Ctx(step=step, t=t, o=o, h=h, l=l, c=c, atr=[atr] * len(rows),
                weekend=[F.weekend_closed(x) for x in t], reopen=[F.SL.reopen_tick(x, step) for x in t],
                fund=fund or {}, swings={"range24": sw}, ret={"range24": F.retrace_series(t, h, l, sw)})


# ── counter-leg level math ──────────────────────────────────────────────────

def test_counter_entry_is_target_price_an_extension_beyond_the_extreme():
    # down-swing: extension BELOW the low; up-swing: extension ABOVE the high
    assert F.target_price(DOWN, 0.236) == pytest.approx(97.64)
    assert F.target_price(DOWN, 0.382) == pytest.approx(96.18)
    assert F.target_price(UP, 0.236) == pytest.approx(112.36)
    assert F.target_price(UP, 0.382) == pytest.approx(113.82)


def test_counter_target_is_fib_price_a_retracement_back():
    assert F.fib_price(DOWN.origin, DOWN.extreme, 0.382) == pytest.approx(103.82)
    assert F.fib_price(UP.origin, UP.extreme, 0.382) == pytest.approx(106.18)


def test_next_ext_level_walks_the_ladder():
    assert [B.next_ext_level(r) for r in B.EXT_LEVELS] == [0.382, 0.618, 1.0]
    assert B.next_ext_level(1.0) == 1.0


def test_counter_stop_price_atr1_pushes_further_in_the_swings_own_direction():
    # down-swing (side -1): stop further DOWN than the entry
    entry = F.target_price(DOWN, 0.382)
    assert B.counter_stop_price("atr1", DOWN, 0.382, 2.0) == pytest.approx(entry - 2.0)
    # up-swing (side +1): stop further UP than the entry
    entry_up = F.target_price(UP, 0.382)
    assert B.counter_stop_price("atr1", UP, 0.382, 2.0) == pytest.approx(entry_up + 2.0)


def test_counter_stop_price_next_is_beyond_the_next_deeper_extension():
    got = B.counter_stop_price("next", DOWN, 0.236, 1.0)
    want = F.target_price(DOWN, 0.382) - 1.0 * F.STOP_BUF_ATR     # 0.1 ATR further down than the 38.2% extension
    assert got == pytest.approx(want)
    with pytest.raises(ValueError):
        B.counter_stop_price("nope", DOWN, 0.382, 1.0)


def test_with_levels_and_counter_levels_trade_sides_are_opposite():
    spec = B.LegSpec(level=0.382, stop="atr1", target=0.272, ratchet="be50")
    cspec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    w_side, w_level, w_stop, w_target = B.with_levels(DOWN, spec, 1.0)
    c_side, c_level, c_stop, c_target = B.counter_levels(DOWN, cspec, 1.0)
    assert w_side == DOWN.side == -1 and c_side == -DOWN.side == 1
    assert w_level > DOWN.extreme > c_level           # with rests above the low; counter rests below it
    assert c_target > c_level and c_target < DOWN.origin   # the counter target is a partial retracement, not a round trip


# ── continuation_series: the mirror of retrace_series ───────────────────────

def test_continuation_series_tracks_the_furthest_point_with_the_swing():
    # one bar per hour, so the extreme's own bar (index 0) is the only bar
    # in its hour window: _extreme_index resolves to i=0 unambiguously.
    t = [T0 + i * H for i in range(6)]
    l = [88.0, 87.0, 86.0, 85.5, 86.5, 85.0]
    h = [92.0, 91.5, 91.0, 90.5, 90.8, 90.2]
    s = Swing(-1, 100.0, 90.0, T0, ("k",))          # extreme = 90, index 0's low (88) satisfies it
    got = B.continuation_series(t, h, l, [s] * 6)
    assert got == [None, None, 87.0, 86.0, 85.5, 85.5]


def test_continuation_series_is_none_without_a_swing():
    t = [T0, T0 + Q]
    assert B.continuation_series(t, [1.0, 1.0], [1.0, 1.0], [None, None]) == [None, None]


# ── the fill candidate (shared by with and counter) ─────────────────────────

def test_fill_candidate_matches_the_with_legs_own_fill_model():
    rows = [QUIET, (105.0, 105.1, 96.3, 97.0), (97.0, 97.1, 96.0, 96.5)]
    ctx = ctx_of(rows, swing=DOWN)
    armed = set()
    cont = B.continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
    level, stop, target = 96.18, 95.0, 103.82
    cand, dead = B._fill_candidate(ctx, 1, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand is None and not dead and DOWN.key in armed       # bar 1's low (96.3) never reaches 96.18
    cand, dead = B._fill_candidate(ctx, 2, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand is not None and cand["fill"] == pytest.approx(level) and cand["fee_in"] == MAKER


def test_fill_candidate_a_touch_is_not_a_fill_a_trade_through_is():
    rows = [QUIET, (105.0, 105.1, 96.18, 97.0), (97.0, 97.1, 96.17, 96.5)]
    ctx = ctx_of(rows, swing=DOWN)
    armed = set()
    cont = B.continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
    level, stop, target = 96.18, 95.0, 103.82
    cand, _ = B._fill_candidate(ctx, 1, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand is None and DOWN.key in armed                    # a bare touch at the level is not a fill
    cand, _ = B._fill_candidate(ctx, 2, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand is not None and cand["fill"] == pytest.approx(level) and cand["fee_in"] == MAKER


def test_fill_candidate_a_gap_fills_at_the_open_once_armed():
    rows = [QUIET, (95.0, 95.1, 94.5, 94.8)]
    ctx = ctx_of(rows, swing=DOWN)
    armed = set()
    cont = B.continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
    # a stop that stays valid (below the entry) whether the fill is the level or the lower gap price
    level, stop, target = 96.18, 94.9, 103.82
    cand0, _ = B._fill_candidate(ctx, 0, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand0 is None and DOWN.key in armed
    cand1, _ = B._fill_candidate(ctx, 1, 1, level, stop, target, cont, P, 0.0, armed, DOWN.key)
    assert cand1 is not None and cand1["fill"] == pytest.approx(95.0) and cand1["fee_in"] == MAKER


def test_fill_candidate_a_gap_before_arming_never_fills():
    rows = [(95.0, 95.1, 94.5, 94.8), (94.8, 94.9, 94.6, 94.7)]
    ctx = ctx_of(rows, swing=DOWN)
    armed = set()
    cont = B.continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
    cand, dead = B._fill_candidate(ctx, 0, 1, 96.18, 94.0, 103.82, cont, P, 0.0, armed, DOWN.key)
    assert cand is None and not dead and DOWN.key not in armed


def test_fill_candidate_level_already_spent_is_never_offered():
    rows = [QUIET, QUIET, QUIET, (97.0, 97.1, 96.5, 96.8)]
    ctx = ctx_of(rows, swing=DOWN)
    series = [None, None, None, 95.0]       # price already ran down to 95.0, past the 96.18 level, before this tick
    armed = set()
    cand, dead = B._fill_candidate(ctx, 3, 1, 96.18, 94.0, 103.82, series, P, 0.0, armed, DOWN.key)
    assert cand is None and not dead


def test_fill_candidate_gated_gap_refusal_is_permanently_dead():
    rows = [QUIET, (95.0, 95.1, 94.5, 94.8)]
    ctx = ctx_of(rows, swing=DOWN)
    armed = set()
    cont = B.continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
    B._fill_candidate(ctx, 0, 1, 96.18, 95.18, 103.82, cont, P, 0.0, armed, DOWN.key)   # arm it on a quiet bar
    assert DOWN.key in armed
    # a huge min_r refuses the gapped fill (R multiple far below it), which is the permanent-death path
    cand, dead = B._fill_candidate(ctx, 1, 1, 96.18, 95.18, 103.82, cont, P, 50.0, armed, DOWN.key)
    assert cand is None and dead            # a gated refusal on a gapped bar kills the leg for this swing


# ── manage_trade: exactly fib_retrace_test.walk's own exit loop ────────────

def test_manage_trade_stop_target_and_ratchet_match_fib_retrace_walk():
    rows = [(103.5, 103.9, 103.3, 103.8), (103.8, 108.0, 103.7, 107.5), (107.5, 107.6, 103.5, 103.6)]
    ctx = ctx_of(rows, swing=UP)
    be50 = F.RATCHET_RULES["be50"]
    res = B.manage_trade(ctx, P, 1, 0, 103.82, 100.0, 110.0, be50, UP, 2)
    assert res["reason"] == "ratchet" and res["moved"] and res["stop_final"] == pytest.approx(103.82)


def test_manage_trade_weekend_closes_at_market():
    fri = ms(2026, 11, 6, 20)                   # the oil weekend starts 21:00Z
    rows = [QUIET, QUIET, QUIET, QUIET, (90.6, 90.9, 90.2, 90.4), QUIET]
    ctx = ctx_of(rows, swing=DOWN, t0=fri)
    assert ctx.weekend[4] and not ctx.weekend[3]
    # target set well above QUIET's own range (105.2) so it is never spuriously touched before the weekend bar
    res = B.manage_trade(ctx, P, 1, 0, 91.0, 89.0, 110.0, None, DOWN, 5)
    assert res["reason"] == "weekend" and res["x"] == 4


# ── the OCO walker: parity with fib_retrace_test.walk, and real cancellation

def test_with_only_matches_fib_retrace_walk_exactly():
    """walk_sleeve with ONLY a with_spec must reproduce fib_retrace_test's
    own with-swing walk trade for trade -- the module's central reuse
    claim, checked, not assumed."""
    rows = [QUIET, (105, 105.1, 103.82, 104.5), (104.5, 104.6, 103.81, 104.2), (104.2, 110.5, 104.0, 110.2)]
    ctx = ctx_of(rows, swing=UP)
    spec = B.LegSpec(level=0.618, stop="next", target=0.0, ratchet="off")
    v = F.Variant("range24", "with", 0.618, "limit", "next", 0.0, "off")
    got_orig = F.walk(ctx, v, P, 0, len(rows) - 1)
    got_mine = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec=spec, counter_spec=None)
    assert len(got_orig["paths"]) == len(got_mine["paths"]) == 1
    a, b = got_orig["paths"][0], got_mine["paths"][0]
    for k in ("i", "x", "side", "fill", "stop", "target", "exit_px", "reason", "fee_in", "fee_out", "lev"):
        assert a[k] == pytest.approx(b[k]) if isinstance(a[k], float) else a[k] == b[k], k


def test_oco_with_leg_trades_are_unaffected_when_the_counter_leg_never_fires():
    """When the counter level is never reached, OCO must produce EXACTLY
    the with-leg's own trades (no interference)."""
    rows = [QUIET, (105, 105.1, 103.82, 104.5), (104.5, 104.6, 103.81, 104.2), (104.2, 110.5, 104.0, 110.2)]
    ctx = ctx_of(rows, swing=UP)
    with_spec = B.LegSpec(level=0.618, stop="next", target=0.0, ratchet="off")
    counter_spec = B.LegSpec(level=0.618, stop="atr1", target=0.236, ratchet="be50")   # UP ext .618 = 116.18: never reached
    only = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec=with_spec, counter_spec=None)
    both = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec=with_spec, counter_spec=counter_spec)
    assert [p["leg"] for p in both["paths"]] == ["with"] * len(both["paths"])
    assert len(only["paths"]) == len(both["paths"]) == 1
    for a, b in zip(only["paths"], both["paths"]):
        assert a["fill"] == pytest.approx(b["fill"]) and a["reason"] == b["reason"]


def test_counter_only_fires_without_a_with_spec():
    rows = [QUIET, (105.0, 105.1, 96.0, 96.5)]
    ctx = ctx_of(rows, swing=DOWN)
    counter_spec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    out = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec=None, counter_spec=counter_spec)
    assert len(out["paths"]) == 1
    assert out["paths"][0]["leg"] == "counter" and out["paths"][0]["side"] == 1


def test_oco_takes_whichever_leg_fills_and_cancels_the_other():
    """Price never dips to the with-leg's retrace level; it runs straight
    up through the counter-short's extension instead. Only the counter
    leg fires."""
    rows = [QUIET, (105.0, 110.0, 104.9, 109.5), (109.5, 114.0, 109.4, 113.9), (113.9, 114.5, 113.8, 114.2)]
    ctx = ctx_of(rows, swing=UP)
    with_spec = B.LegSpec(level=0.618, stop="next", target=0.0, ratchet="off")
    counter_spec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    out = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec, counter_spec)
    assert len(out["paths"]) == 1
    assert out["paths"][0]["leg"] == "counter" and out["paths"][0]["side"] == -1


def test_oco_tie_break_picks_the_level_closer_to_the_bars_open():
    """One wide bar trades through BOTH levels (with-long at 103.82, and
    the counter-short's extension at 113.82): the documented convention
    takes the one closer to the bar's OPEN as having triggered first."""
    rows = [QUIET, (108.0, 115.0, 103.5, 110.0)]
    ctx = ctx_of(rows, swing=UP)
    with_spec = B.LegSpec(level=0.618, stop="next", target=0.0, ratchet="off")
    counter_spec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    out = B.walk_sleeve(ctx, P, 0, len(rows) - 1, with_spec, counter_spec)
    assert len(out["paths"]) == 1 and out["paths"][0]["leg"] == "with"      # |108-103.82| < |108-113.82|


def test_overextension_filter_blocks_a_narrow_swing():
    # a swing only 2.0 wide (atr=1.0): k=3 needs size >= 3.0, never eligible; k=None always is
    narrow = Swing(-1, 102.0, 100.0, T0 - 2 * H, ("n", -1, T0 - 2 * H, 100.0))
    rows = [QUIET, (100.0, 100.1, 99.0, 99.5)]
    ctx = ctx_of(rows, swing=narrow)
    counter_spec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    out = B.walk_sleeve(ctx, P, 0, len(rows) - 1, counter_spec=counter_spec, k_filter=3.0)
    assert out["paths"] == []
    out2 = B.walk_sleeve(ctx, P, 0, len(rows) - 1, counter_spec=counter_spec, k_filter=None)
    assert len(out2["paths"]) == 1


def test_funding_gate_blocks_the_counter_leg_when_not_extreme():
    rows = [QUIET, (105.0, 105.1, 96.0, 96.5)]
    ctx = ctx_of(rows, swing=DOWN)
    counter_spec = B.LegSpec(level=0.382, stop="atr1", target=0.382, ratchet="be50")
    out_blocked = B.walk_sleeve(ctx, P, 0, len(rows) - 1, counter_spec=counter_spec, fund_gate=lambda t, s: False)
    assert out_blocked["paths"] == []
    out_open = B.walk_sleeve(ctx, P, 0, len(rows) - 1, counter_spec=counter_spec, fund_gate=lambda t, s: True)
    assert len(out_open["paths"]) == 1


# ── the funding gate's own direction ─────────────────────────────────────────

def test_fund_gate_factory_direction_is_against_the_move():
    times, zs = [T0, T0 + H], [2.5, -2.5]
    gate = B.fund_gate_factory(times, zs, 2.0)
    assert gate(T0, -1) is True             # down-swing, z=+2.5 (crowded long, against the move): eligible
    assert gate(T0, 1) is False             # up-swing, z=+2.5 is WITH a crowded long, not against: blocked
    assert gate(T0 + H, 1) is True          # up-swing, z=-2.5 (crowded short, against the move): eligible
    assert gate(T0 + H, -1) is False
    assert gate(T0 - H, 1) is False         # before any z-score is known


# ── the random-level baseline and the reality check ─────────────────────────

def test_random_level_variant_stays_in_the_declared_band():
    rng = random.Random(1)
    v = B.REFERENCE
    for _ in range(500):
        rv = B.random_level_variant(v, rng)
        assert B.RANDOM_LO <= rv.ext <= B.RANDOM_HI
        assert rv.stop == v.stop and rv.tgt == v.tgt and rv.ratchet == v.ratchet and rv.k_filter == v.k_filter


def test_random_level_variant_is_deterministic_for_a_given_seed():
    a = B.random_level_variant(B.REFERENCE, random.Random(5))
    b = B.random_level_variant(B.REFERENCE, random.Random(5))
    assert a.ext == b.ext


# ── bootstrap CI, cost stress, regime split, holdout ────────────────────────

def test_bootstrap_mean_r_ci_brackets_the_sample_mean():
    trades = [{"r": 1.0}, {"r": -1.0}, {"r": 2.0}, {"r": -0.5}, {"r": 0.5}, {"r": -1.5}, {"r": 1.0}, {"r": 0.0},
              {"r": -0.5}, {"r": 0.5}]
    got = B.bootstrap_mean_r_ci(trades, draws=500, seed=1)
    assert got["n"] == 10 and got["mean_r"] == pytest.approx(0.15, abs=1e-6)
    assert got["ci_low"] <= got["mean_r"] <= got["ci_high"]


def test_bootstrap_mean_r_ci_empty():
    assert B.bootstrap_mean_r_ci([])["n"] == 0 and B.bootstrap_mean_r_ci([])["mean_r"] is None


def test_cost_stressed_params_doubles_fees_and_slippage():
    got = B.cost_stressed_params(P)
    assert got.taker_fee == pytest.approx(P.taker_fee * 2.0)
    assert got.maker_fee == pytest.approx(P.maker_fee * 2.0)
    assert got.slippage == pytest.approx(P.slippage * 2.0)
    assert got.leverage == P.leverage and got.tick_size == P.tick_size


def test_daily_efficiency_ratio_trend_vs_chop():
    t0 = T0 - T0 % H
    trend = [Candle(t=t0 + i * H, o=100 + i, h=100 + i + 0.1, l=100 + i - 0.1, c=100 + i, v=1.0) for i in range(5)]
    chop = [Candle(t=t0 + (i + 24) * H, o=100 + (i % 2), h=101, l=99, c=100 + (i % 2), v=1.0) for i in range(5)]
    er = B.daily_efficiency_ratio(trend + chop)
    day1 = _time.strftime("%Y-%m-%d", _time.gmtime(t0 / 1000.0))
    day2 = _time.strftime("%Y-%m-%d", _time.gmtime((t0 + 24 * H) / 1000.0))
    assert er[day1] == pytest.approx(1.0, abs=1e-6)        # every hour moved the same direction: pure trend
    assert er[day2] == pytest.approx(0.0, abs=1e-6)        # back and forth, net zero: pure chop


def test_regime_split_buckets_by_the_entry_days_ratio():
    trades = [{"entry": "2026-11-01 00:00Z", "net_usd": 5.0, "r": 1.0},
              {"entry": "2026-11-02 00:00Z", "net_usd": -3.0, "r": -0.5},
              {"entry": "2026-11-05 00:00Z", "net_usd": 2.0, "r": 0.5}]
    er = {"2026-11-01": 0.9, "2026-11-02": 0.1}
    out = B.regime_split(trades, er)
    assert out["trending"]["n"] == 1 and out["trending"]["net_usd"] == 5.0
    assert out["ranging"]["n"] == 1 and out["ranging"]["net_usd"] == -3.0
    assert out["unknown"]["n"] == 1 and out["unknown"]["net_usd"] == 2.0


def test_trades_on_day_filters_by_entry_date():
    trades = [{"entry": "2026-11-16 04:00Z"}, {"entry": "2026-11-15 23:00Z"}]
    assert B.trades_on_day(trades, "2026-11-16") == [trades[0]]


def test_holdout_cut_is_a_fraction_of_the_way_rounded_to_a_bar():
    start, end = T0, T0 + 100 * H
    cut = B.holdout_cut(start, end, frac=0.7, step_ms=H)
    assert cut % H == 0 and start < cut < end
    assert abs(cut - (start + 70 * H)) <= H          # float rounding can land one step short
    cut2 = B.holdout_cut(start, end, frac=0.3, step_ms=H)
    assert cut2 < cut


# ── the grid ───────────────────────────────────────────────────────────────

def test_grid_is_the_declared_33_variants_under_100():
    vs = B.variants()
    assert len(vs) == 33 and len({v.name for v in vs}) == 33
    fams = Counter(v.family for v in vs)
    assert fams["H1"] == 6 + 2            # the grid's k_filter=None row (6) plus the 2 sensitivity checks
    assert fams["H1+H2"] == 18
    assert fams["H1+H3"] == 2 and fams["H1+H2+H3"] == 2
    assert fams["H4"] == 3
    assert B.REFERENCE in vs


def test_neighbours_step_the_grid_axes_only():
    nb = B.neighbours(B.REFERENCE)
    names = {n.name for n in nb}
    assert B.REFERENCE.name not in names
    assert all(n.kind == "counter" for n in nb)
    v = dataclasses.replace(B.REFERENCE, k_filter=4.0)
    nb2 = B.neighbours(v)
    assert any(n.k_filter == 3.0 for n in nb2) and any(n.k_filter == 5.0 for n in nb2)


def test_variant_name_and_family_are_distinct_by_kind():
    c = B.Variant("counter", ext=0.382, k_filter=3.0)
    o = B.Variant("oco", ext=0.382)
    assert c.name.startswith("counter_") and o.name.startswith("oco_")
    assert c.family == "H1+H2" and o.family == "H4"
    assert o.with_spec() is not None and c.with_spec() is None


# ── the CLI ──────────────────────────────────────────────────────────────

def test_cli_help_and_no_journal(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        B.main(["--help"])
    assert e.value.code == 0
    assert B.main(["--oil-theses-dir", str(tmp_path / "none"), "--out-dir", str(tmp_path / "o"),
                  "--cache-dir", str(tmp_path / "c")]) == 2
