"""Gate tests for the 2026-11-19 breakout entry, mode a6
(`desk_tick_sim.Params.pullback_entry == "a6"`; see
docs/strategy/BREAKOUT_ENTRY_TEST_2026-11-19.md).

Operator, 2026-11-18/19: "ur like doing one trade every 2 weeks" ... "i need
to see some profitable trades soon" -- the live pullback rule only buys a
dip / sells a rally back to the thesis entry and never chases, so a thesis
that is RIGHT on direction but never gets its pullback (Brent falling
87.6-90.5 past a short thesis waiting to sell a rally; Brent running
88.6->90.34 past a long thesis waiting for a dip) takes no trade at all. a6
is a SECOND, additional entry on the thesis side only: buy/sell the break of
the prior 8h high/low itself, confirmed on a closed 15m or 1h bar, with a
stop back inside the broken level (k x ATR14, k swept) or at the entry
range's own midpoint, a target shared with every other pullback mode
(`pb_target`/`pb_target_r`), a size fraction (`pb_breakout_size_frac`) and a
one-fill-per-thesis cap (`pb_breakout_cap_one`) -- the pullback rule and the
live 2R gate, blackout calendar and one-slot-at-a-time rule are UNCHANGED
(see desk_tick_sim.py's own a6 Params block for the full contract).

Synthetic bars only, no network, no thesis journal. Covers: validation,
the long/short signal (8h break, both confirmation timeframes), no-break-
yet, every stop mode, every target variant plus the min_r override a fixed-R
target needs, the thesis-stop cap, that `pullback_entry=None` (every other
eval, and every default) is byte-identical to before a6 existed, the full
sim takes the entry when the live rule chase-refuses, the cap-one-per-
thesis rule blocking (or not) a second fill under the same call, and the
size fraction halving both the entry and its top-up ceiling.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    FIFTEEN_MIN_MS, HOUR_MS, Direction, _levels, live_params, pullback_setup, simulate)

T0 = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)


def bar(i, o, h, lo, c, start=T0, step=HOUR_MS):
    return Candle(t=start + i * step, o=o, h=h, l=lo, c=c, v=1.0)


def _base(**over):
    return dataclasses.replace(live_params(), **over)


# 20 wide hours (96-104, so the 24h/far target has room), 20 tight hours
# near 100 (99.9-100.1, the 8h breakout level and a small ATR14 at the
# break), then one break bar. Mirrors test_pullback_sim.py's a5 calm-tail
# fixture, scaled to an 8h lookback instead of a5's 24h.
WIDE_LO, WIDE_HI = 96.0, 104.0
TIGHT_LO, TIGHT_HI = 99.9, 100.1


def calm_then_break_long(n_wide=20, n_tight=20, break_o=100.0, break_h=101.2, break_l=99.95, break_c=101.0):
    out = [bar(i, 100.0, WIDE_HI, WIDE_LO, 100.0) for i in range(n_wide)]
    out += [bar(n_wide + j, 100.0, TIGHT_HI, TIGHT_LO, 100.0) for j in range(n_tight)]
    out.append(bar(n_wide + n_tight, break_o, break_h, break_l, break_c))
    return out


def calm_then_break_short(n_wide=20, n_tight=20, break_o=100.0, break_h=100.05, break_l=98.8, break_c=99.0):
    out = [bar(i, 100.0, WIDE_HI, WIDE_LO, 100.0) for i in range(n_wide)]
    out += [bar(n_wide + j, 100.0, TIGHT_HI, TIGHT_LO, 100.0) for j in range(n_tight)]
    out.append(bar(n_wide + n_tight, break_o, break_h, break_l, break_c))
    return out


LONG_CHASE = Direction(side="long", entry_ceiling=95.0, regime_id="x")
SHORT_CHASE = Direction(side="short", entry_ceiling=105.0, regime_id="x")


def lv_at(c1h):
    return _levels(c1h, 24, 24)


def _bar15(i, o, h, lo, c, start=T0):
    return Candle(t=start + i * FIFTEEN_MIN_MS, o=o, h=h, l=lo, c=c, v=1.0)


def flat_then_break_15m(n=32, break_o=100.0, break_h=101.2, break_l=99.95, break_c=101.0):
    """8h (32 x 15m) flat near 100, then one 15m bar whose close breaks it."""
    out = [_bar15(i, 100.0, TIGHT_HI, TIGHT_LO, 100.0) for i in range(n)]
    out.append(_bar15(n, break_o, break_h, break_l, break_c))
    return out


# ── validation ───────────────────────────────────────────────────────────

def test_a6_validation():
    c1h = calm_then_break_long()
    bad = ({"pullback_entry": "a6", "pb_breakout_entry_h": 0},
          {"pullback_entry": "a6", "pb_breakout_confirm": "30m"},
          {"pullback_entry": "a6", "pb_breakout_stop_mode": "atr"},
          {"pullback_entry": "a6", "pb_breakout_stop_k": -0.1},
          {"pullback_entry": "a6", "pb_breakout_size_frac": 0.0},
          {"pullback_entry": "a6", "pb_breakout_size_frac": 1.5})
    for over in bad:
        with pytest.raises(ValueError):
            simulate([], c1h, lambda t: LONG_CHASE, _base(**over))


# ── the signal itself ───────────────────────────────────────────────────

def test_a6_long_breaks_the_8h_high_on_a_closed_1h_bar():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    mid = 101.05
    p = _base(pullback_entry="a6")
    s = pullback_setup(LONG_CHASE, t, mid, lv, c1h, p)
    assert s["pb"] == "a6" and s["is_market"] and s["side"] == "long" and s["size_frac"] == 1.0
    assert s["stop_px"] < mid < s["target_px"]
    # level_atr (default), k=0.75: stop = the broken 8h high (100.1) - 0.75 x ATR14
    assert s["stop_px"] == pytest.approx(TIGHT_HI - 0.75 * lv["atr14"])
    risk = mid - s["stop_px"]
    r = (s["target_px"] - mid) / risk
    assert r >= 2.0        # clears the live 2R gate on the far (24h) target


def test_a6_short_mirrors_long():
    c1h = calm_then_break_short()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    mid = 98.95
    p = _base(pullback_entry="a6")
    s = pullback_setup(SHORT_CHASE, t, mid, lv, c1h, p)
    assert s["pb"] == "a6" and s["side"] == "short"
    assert s["stop_px"] == pytest.approx(TIGHT_LO + 0.75 * lv["atr14"])
    assert s["target_px"] < mid < s["stop_px"]


def test_a6_no_break_yet_returns_none():
    c1h = calm_then_break_long(break_o=100.0, break_h=100.05, break_l=99.95, break_c=100.0)  # closes back inside
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    p = _base(pullback_entry="a6")
    assert pullback_setup(LONG_CHASE, t, 100.0, lv, c1h, p) is None


def test_a6_needs_entry_h_plus_one_closed_bars():
    c1h = calm_then_break_long(n_wide=0, n_tight=4)   # only 5 bars total: fewer than 8h + 1
    lv = _levels(c1h, 8, 8)
    if lv is None:
        return   # ATR14 itself needs 15 bars; either way there is nothing to break yet
    p = _base(pullback_entry="a6")
    assert pullback_setup(LONG_CHASE, c1h[-1].t + HOUR_MS, 100.0, lv, c1h, p) is None


# ── confirmation timeframe ──────────────────────────────────────────────

def test_a6_15m_confirm_reads_the_15m_close_not_the_1h_one():
    c15 = flat_then_break_15m()
    t = c15[-1].t + FIFTEEN_MIN_MS
    lv = {"atr14": 0.3, "range_8h_low": TIGHT_LO, "range_8h_high": TIGHT_HI,
         "target_low": 90.0, "target_high": 110.0}
    p = _base(pullback_entry="a6", pb_breakout_confirm="15m")
    s = pullback_setup(LONG_CHASE, t, 101.05, lv, [], p, closed_15m=c15)
    assert s["pb"] == "a6" and s["side"] == "long"
    # 1h confirmation on the SAME 15m data (no closed 1h bars at all) finds nothing
    assert pullback_setup(LONG_CHASE, t, 101.05, lv, [], p := _base(pullback_entry="a6", pb_breakout_confirm="1h"),
                          closed_15m=c15) is None


def test_a6_confirm_1h_never_reads_closed_15m():
    """confirm="1h" (the default) ignores closed_15m entirely -- passing
    None must not raise, and must give the same answer as a real 15m
    series would, since the 1h path never looks at it."""
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    p = _base(pullback_entry="a6")
    s_none = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p, closed_15m=None)
    s_ignored = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p, closed_15m=flat_then_break_15m())
    assert s_none == s_ignored


# ── stop modes ───────────────────────────────────────────────────────────

def test_a6_level_atr_k_widens_the_stop_as_k_grows():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    stops = {}
    for k in (0.5, 0.75, 1.0):
        # pb_target_r/pb_breakout_min_r isolate the stop FORMULA from the R
        # gate (a wide enough k on this fixture's small ATR fails the live
        # 2R gate on its own, which is a separate, already-covered concern).
        p = _base(pullback_entry="a6", pb_breakout_stop_k=k, pb_target_r=5.0, pb_breakout_min_r=0.1)
        stops[k] = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p)["stop_px"]
    assert stops[0.5] > stops[0.75] > stops[1.0]        # more k = further below the broken level = wider
    for k, stop in stops.items():
        assert stop == pytest.approx(TIGHT_HI - k * lv["atr14"])


def test_a6_midpoint_stop_uses_the_8h_ranges_own_middle():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    p = _base(pullback_entry="a6", pb_breakout_stop_mode="midpoint")
    s = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p)
    assert s["stop_px"] == pytest.approx((lv["range_8h_low"] + lv["range_8h_high"]) / 2.0)


def test_a6_bad_stop_side_is_refused():
    """A pathological 8h range (midpoint sitting ABOVE the entry) refuses
    rather than return a stop on the wrong side of a long."""
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = dict(lv_at(c1h))
    lv["range_8h_low"], lv["range_8h_high"] = 200.0, 300.0    # absurdly far above the mid
    p = _base(pullback_entry="a6", pb_breakout_stop_mode="midpoint")
    assert pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p) == {"refused": "pb_breakout_bad_stop"}


# ── target variants (shared with every other pullback mode) ────────────

def test_a6_far_target_is_the_24h_extreme():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    p = _base(pullback_entry="a6")   # pb_target defaults to "far"
    s = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, p)
    assert s["orig_target_px"] == lv["target_high"] == WIDE_HI


def test_a6_thesis_target():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    d = dataclasses.replace(LONG_CHASE, target_px=110.0)
    p = _base(pullback_entry="a6", pb_target="thesis")
    s = pullback_setup(d, t, 101.05, lv, c1h, p)
    assert s["orig_target_px"] == 110.0


def test_a6_fixed_r_target_needs_its_own_min_r_override():
    """A 1.5R target under the live 2R gate is a contradiction (the actual
    R IS 1.5) and is refused unless `pb_breakout_min_r` relaxes the gate for
    this mode ONLY -- the main rule's own `min_r` is untouched."""
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    mid = 101.05
    gated_out = pullback_setup(LONG_CHASE, t, mid, lv, c1h, _base(pullback_entry="a6", pb_target_r=1.5))
    assert gated_out == {"refused": "pb_min_r"}
    p = _base(pullback_entry="a6", pb_target_r=1.5, pb_breakout_min_r=1.5)
    s = pullback_setup(LONG_CHASE, t, mid, lv, c1h, p)
    risk = mid - s["stop_px"]
    assert (s["target_px"] - mid) / risk == pytest.approx(1.5)
    assert p.min_r == 2.0    # the live rule's own gate is untouched by pb_breakout_min_r


def test_a6_fixed_2r_target_needs_no_override():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    mid = 101.05
    p = _base(pullback_entry="a6", pb_target_r=2.0)   # min_r stays the live 2.0 default: no conflict
    s = pullback_setup(LONG_CHASE, t, mid, lv, c1h, p)
    risk = mid - s["stop_px"]
    assert (s["target_px"] - mid) / risk == pytest.approx(2.0)


# ── thesis-stop cap (shared mechanism, Params.pb_cap_thesis_stop) ──────

def test_a6_cap_thesis_stop():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    mid = 101.05
    p = _base(pullback_entry="a6")
    tight = dataclasses.replace(LONG_CHASE, stop_px=100.05)   # narrower than a6's own stop
    assert pullback_setup(tight, t, mid, lv, c1h, p) == {"refused": "pb_wide"}
    assert pullback_setup(tight, t, mid, lv, c1h, dataclasses.replace(p, pb_cap_thesis_stop=False))["pb"] == "a6"


# ── off changes nothing ──────────────────────────────────────────────────

def _key(run):
    import dataclasses as dc
    return ([dc.asdict(t) for t in run.trades], run.equity_curve, run.final_equity, dict(run.refused),
           run.open_position_at_end, run.filled, run.rested)


def test_a6_fields_set_but_mode_off_changes_nothing():
    c1h = calm_then_break_long()
    d_at = lambda t: LONG_CHASE   # noqa: E731
    c15 = []
    ref = _key(simulate(c15, c1h, d_at, _base()))
    over = _base(pb_breakout_entry_h=3, pb_breakout_confirm="15m", pb_breakout_stop_mode="midpoint",
                pb_breakout_stop_k=0.1, pb_breakout_size_frac=0.3, pb_breakout_cap_one=True,
                pb_breakout_min_r=1.0)
    assert _key(simulate(c15, c1h, d_at, over)) == ref


# ── the full sim: a6 fires when the live rule chase-refuses ─────────────

def test_a6_in_the_sim_takes_the_entry_the_live_rule_refuses():
    # one extra bar after the break: the sim decides off CLOSED bars, so the
    # break itself needs a following tick before the confirmation is seen
    c1h = calm_then_break_long() + [bar(41, 101.05, 101.3, 100.9, 101.1)]
    c15 = []
    chase_thesis = lambda t: Direction(side="long", entry_ceiling=50.0, regime_id="x")   # noqa: E731
    live = simulate(c15, c1h, chase_thesis, _base(tick_minutes=60))
    assert not live.trades and live.open_position_at_end is None and live.refused["chase"] > 0
    run = simulate(c15, c1h, chase_thesis, _base(tick_minutes=60, pullback_entry="a6"))
    assert run.open_position_at_end is not None and run.open_position_at_end["side"] == "long"
    assert run.open_position_levels["entry_kind"] == "a6"


# ── cap one fill per thesis ──────────────────────────────────────────────

def _cap_timeline():
    """One a6 breakout that stops out fast, a fresh calm range, then a
    SECOND break under the SAME call (regime_id) -- the cap-one rule's own
    scenario. weekend_off is turned off for this fixture so the second
    breakout is never blocked by the (orthogonal) weekend-flatten rule."""
    out = [bar(i, 100.0, WIDE_HI, WIDE_LO, 100.0) for i in range(20)]
    out += [bar(20 + j, 100.0, TIGHT_HI, TIGHT_LO, 100.0) for j in range(20)]
    out.append(bar(40, 100.0, 101.2, 99.95, 101.0))           # break 1
    out.append(bar(41, 101.05, 101.3, 100.9, 101.1))          # entry tick 1
    out.append(bar(42, 101.0, 101.05, 95.0, 96.0))            # crash: stops it out
    out += [bar(43 + j, 96.0, 96.1, 95.9, 96.0) for j in range(8)]   # a fresh calm 8h range
    out.append(bar(51, 96.0, 97.5, 95.9, 97.2))               # break 2
    out.append(bar(52, 97.25, 97.5, 97.0, 97.3))              # entry tick 2
    return out


def test_a6_cap_one_blocks_a_second_fill_under_the_same_call():
    c1h = _cap_timeline()
    one_call = lambda t: Direction(side="long", entry_ceiling=50.0, regime_id="call1")   # noqa: E731
    base = _base(tick_minutes=60, pullback_entry="a6", weekend_off=None, pb_breakout_stop_mode="midpoint",
                pb_target_r=2.0, pb_breakout_min_r=2.0)
    capped = simulate([], c1h, one_call, dataclasses.replace(base, pb_breakout_cap_one=True))
    free = simulate([], c1h, one_call, dataclasses.replace(base, pb_breakout_cap_one=False))
    assert sum(1 for t in capped.trades if t.entry_kind == "a6") == 1
    assert capped.open_position_at_end is None                # the 2nd break is capped: stays flat
    assert capped.refused["pb_breakout_capped"] > 0
    assert free.open_position_at_end is not None and free.open_position_at_end["side"] == "long"   # 2nd fill, uncapped


def test_a6_cap_one_is_per_call_not_global():
    """A NEW call (a different regime_id) gets its own fresh shot even with
    the cap on -- the cap is "one per thesis," not "one ever.\""""
    c1h = _cap_timeline()

    def two_calls(t):
        # the crash (idx42) is a round call rewrite, matching a real desk's
        # thesis rewrite after an invalidating move
        return Direction(side="long", entry_ceiling=50.0, regime_id="call1" if t < c1h[42].t else "call2")

    base = _base(tick_minutes=60, pullback_entry="a6", weekend_off=None, pb_breakout_stop_mode="midpoint",
                pb_target_r=2.0, pb_breakout_min_r=2.0, pb_breakout_cap_one=True)
    run = simulate([], c1h, two_calls, base)
    assert sum(1 for t in run.trades if t.entry_kind == "a6") == 1
    assert run.open_position_at_end is not None   # call2's own break still fills: the cap didn't carry over
    assert run.refused.get("pb_breakout_capped", 0) == 0


# ── size fraction (full vs half) ──────────────────────────────────────────

def test_a6_half_size_halves_the_entry_and_the_top_up_ceiling():
    c1h = calm_then_break_long(n_wide=20, n_tight=20) + [bar(41, 101.05, 101.3, 100.9, 101.1)]
    thesis = lambda t: Direction(side="long", entry_ceiling=50.0, regime_id="call1")   # noqa: E731
    base = _base(tick_minutes=60, pullback_entry="a6", pb_breakout_stop_mode="midpoint", pb_target_r=2.0,
                pb_breakout_min_r=2.0)
    full = simulate([], c1h, thesis, base)
    half = simulate([], c1h, thesis, dataclasses.replace(base, pb_breakout_size_frac=0.5))
    assert full.open_position_at_end is not None and half.open_position_at_end is not None
    assert half.open_position_at_end["size"] == pytest.approx(full.open_position_at_end["size"] * 0.5, rel=1e-6)


def test_a6_size_frac_only_appears_on_a6_setups():
    c1h = calm_then_break_long()
    t = c1h[-1].t + HOUR_MS
    lv = lv_at(c1h)
    s = pullback_setup(LONG_CHASE, t, 101.05, lv, c1h, _base(pullback_entry="a6", pb_breakout_size_frac=0.4))
    assert s["size_frac"] == 0.4
    s1 = pullback_setup(dataclasses.replace(LONG_CHASE, stop_px=None), t, 101.05, lv_at(calm_then_break_long()),
                        calm_then_break_long(), _base(pullback_entry="a1", pb_struct_h=4))
    assert "size_frac" not in s1
