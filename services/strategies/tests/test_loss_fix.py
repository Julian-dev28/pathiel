"""Gate tests for the 2026-11-02 loss-audit sim options (desk_tick_sim, see
docs/strategy/LOSS_AUDIT_2026-11-02.md): risk-capped size
(`risk_cap_frac`, `risk_capped_notional`), the entry filters
(`entry_delay_min`, `side_age_h`, `confirm_1h`, `no_break_entry`), the
golden check that they change nothing when off, and the regression for
the 2026-11-02 Brent long on its real bars.

No network. The 11-02 bars are the real xyz:BRENTOIL candles (public
candleSnapshot): 1h from 2026-11-01 00:00Z to 2026-11-02 08:00Z, 15m from
03:45Z to 09:30Z, typed in. The theses are the live oil journal's three
calls around it (long 111.7 from 11-01 18:19Z, long 110.0 from 11-02
03:48Z, short 110.2 from 08:40Z), reduced to what the sim reads.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, _full_size_notional, _setup, call_start, entry_filter, filters_on, live_params,
    risk_capped_notional, simulate)

HOUR_MS = 3_600_000
Q = 900_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _base(**over) -> Params:
    return dataclasses.replace(live_params(), starting_equity=64.0, **over)


# ── risk-capped size ───────────────────────────────────────────────────────

def test_risk_cap_off_is_the_full_port():
    p = _base()
    assert risk_capped_notional(100.0, p, 20.0, 110.0, 109.25) == _full_size_notional(100.0, p, 20.0)


def test_risk_cap_sizes_the_stop_out_to_the_cap():
    """$100 at a 0.75 stop on 110: the full port (about $1,982) would lose
    about $15.9 at the stop; a 10% cap sizes it so the stop, both taker
    fees and the 0.03% slip cost $10."""
    p = _base(risk_cap_frac=0.10)
    n = risk_capped_notional(100.0, p, 20.0, 110.0, 109.25)
    per = 0.75 / 110.0 + 2 * p.taker_fee + p.slippage
    assert n == pytest.approx(10.0 / per)
    assert n * per == pytest.approx(10.0)
    assert n < _full_size_notional(100.0, p, 20.0)


def test_risk_cap_never_sizes_above_the_full_port():
    """A tight stop: the cap would allow more than 20x, the full port binds."""
    p = _base(risk_cap_frac=0.20)
    assert risk_capped_notional(100.0, p, 20.0, 110.0, 109.9) == _full_size_notional(100.0, p, 20.0)


# ── entry filters (pure) ───────────────────────────────────────────────────

def test_call_start_reads_a_thesis_time_else_the_side_start():
    assert call_start(Direction(side="long", regime_id="1793000000000"), 5) == 1793000000000
    assert call_start(Direction(side="long"), 5) == 5
    assert call_start(Direction(side="long", regime_id="call-a"), 7) == 7


def test_entry_delay():
    d = Direction(side="long", regime_id=str(ms(2026, 11, 2, 3, 48)))
    p = _base(entry_delay_min=15.0)
    assert entry_filter(d, ms(2026, 11, 2, 3, 50), None, None, p) == "delay"
    assert entry_filter(d, ms(2026, 11, 2, 4, 3), None, None, p) is None
    assert entry_filter(d, ms(2026, 11, 2, 3, 50), None, None, _base()) is None


def test_side_age():
    p = _base(side_age_h=4.0)
    d = Direction(side="short")
    since = ms(2026, 11, 2, 8, 40)
    assert entry_filter(d, since + 3 * HOUR_MS, since, None, p) == "side_age"
    assert entry_filter(d, since + 4 * HOUR_MS, since, None, p) is None


def test_confirm_1h():
    p = _base(confirm_1h=True)
    red = Candle(t=0, o=111.13, h=111.13, l=110.14, c=110.17, v=1.0)     # 11-02 02:00Z
    green = Candle(t=0, o=109.68, h=110.09, l=109.6, c=109.92, v=1.0)    # 11-02 04:00Z
    flat = Candle(t=0, o=110.0, h=110.1, l=109.9, c=110.0, v=1.0)
    assert entry_filter(Direction(side="long"), 0, None, red, p) == "confirm_1h"
    assert entry_filter(Direction(side="long"), 0, None, green, p) is None
    assert entry_filter(Direction(side="short"), 0, None, red, p) is None
    assert entry_filter(Direction(side="long"), 0, None, flat, p) == "confirm_1h"
    assert entry_filter(Direction(side="long"), 0, None, None, p) == "confirm_1h"


def test_filters_on():
    assert not filters_on(_base())
    assert not filters_on(_base(no_break_entry=True, risk_cap_frac=0.1))    # neither is a tick filter
    assert filters_on(_base(entry_delay_min=0.0)) and filters_on(_base(side_age_h=1.0))
    assert filters_on(_base(confirm_1h=True))


def test_no_break_refuses_a_long_under_the_entry_range():
    """The 11-02 long: mid 110.00 under the 24h low 110.14."""
    lv = {"atr14": 0.5, "entry_low": 110.14, "entry_high": 112.39, "target_low": 110.14, "target_high": 112.39}
    d = Direction(side="long", entry_ceiling=111.7)
    assert _setup(d, 110.00, lv, _base(no_break_entry=True)) == {"refused": "break"}
    live = _setup(d, 110.00, lv, _base())
    assert live["stop_px"] == pytest.approx(109.25) and not live.get("refused")
    inside = _setup(d, 110.30, lv, _base(no_break_entry=True, min_r=1.5))     # inside the range: allowed
    assert inside["stop_px"] == pytest.approx(110.14 - 0.75)
    short = Direction(side="short")
    assert _setup(short, 112.50, lv, _base(no_break_entry=True)) == {"refused": "break"}


# ── the sim ────────────────────────────────────────────────────────────────

def _flat_run(direction, n15: int = 40, **over):
    """24 flat 1h bars (range 100 to 104), then 15m bars that open at 100.3
    and never reach the 99.55 stop or the 103.48 target."""
    t0 = ms(2026, 10, 27)
    c1h = [Candle(t=t0 - (24 - i) * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(24)]
    c15 = [Candle(t=t0 + i * Q, o=100.3, h=100.4, l=100.2, c=100.3, v=1.0) for i in range(n15)]
    return simulate(c15, c1h, direction, _base(**over))


def _key(run):
    return ([dataclasses.asdict(t) for t in run.trades], run.equity_curve, run.final_equity, dict(run.refused))


def test_golden_options_off_or_neutral_change_nothing():
    """Off, or set where they cannot bind (a 1000% risk cap, a 0-minute
    delay, a 0-hour side age), every new option leaves the run as it was."""
    d = lambda t: Direction(side="long", regime_id=str(ms(2026, 10, 26)))   # noqa: E731
    ref = _key(_flat_run(d))
    for over in ({"risk_cap_frac": 10.0}, {"entry_delay_min": 0.0}, {"side_age_h": 0.0},
                 {"risk_cap_frac": None, "confirm_1h": False, "no_break_entry": False}):
        assert _key(_flat_run(d, **over)) == ref, over


def test_side_age_blocks_after_a_flip_and_not_after_a_same_side_rewrite():
    t0 = ms(2026, 10, 27)
    flip = t0 + 2 * HOUR_MS

    def d(t):
        if t < flip:
            return Direction(side="short", regime_id=str(t0 - 12 * HOUR_MS))
        return Direction(side="long", regime_id=str(flip))

    run = _flat_run(d, n15=40, side_age_h=3.0)
    longs = [tr for tr in run.trades if tr.side == "long"]
    assert run.refused["side_age"] > 0
    first_long = min((tr.entry_time for tr in longs), default=None)
    if first_long is None:
        assert run.open_position_at_end and run.open_position_at_end["side"] == "long"
    else:
        assert first_long >= flip + 3 * HOUR_MS

    def same_side(t):     # a long held 12h, rewritten every hour: the side never changes
        return Direction(side="long", regime_id=str(t0 + (t - t0) // HOUR_MS * HOUR_MS - (12 * HOUR_MS if t < t0 + Q
                                                                                           else 0)))

    assert _flat_run(same_side, side_age_h=3.0).refused["side_age"] == 0


def test_entry_delay_waits_after_each_rewrite():
    t0 = ms(2026, 10, 27)
    hourly = lambda t: Direction(side="long", regime_id=str(t - (t - t0) % HOUR_MS))   # noqa: E731
    run = _flat_run(hourly, entry_delay_min=30.0)
    assert run.refused["delay"] == 2                        # 00:00 and 00:15, then the 00:30 tick buys
    assert run.open_position_at_end and _flat_run(hourly).refused["delay"] == 0


# ── 2026-11-02: the Brent long on its real bars ────────────────────────────

# xyz:BRENTOIL 1h, 2026-11-01 00:00Z to 2026-11-02 08:00Z (o, h, l, c)
NOV2_1H = [
    (112.33, 112.39, 112.26, 112.28), (112.28, 112.28, 112.18, 112.21), (112.2, 112.23, 112.05, 112.05),
    (112.05, 112.38, 112.04, 112.34), (112.33, 112.39, 112.26, 112.27), (112.27, 112.37, 112.21, 112.28),
    (112.28, 112.31, 112.12, 112.15), (112.16, 112.24, 112.09, 112.13), (112.13, 112.2, 112.02, 112.16),
    (112.15, 112.26, 111.97, 112.09), (112.1, 112.11, 111.95, 111.99), (111.99, 112.0, 111.87, 111.91),
    (111.91, 112.26, 111.91, 112.11), (112.12, 112.12, 111.77, 111.97), (111.97, 112.06, 111.92, 112.05),
    (112.05, 112.08, 111.89, 111.91), (111.91, 111.95, 111.88, 111.94), (111.94, 112.06, 111.87, 111.97),
    (111.97, 111.98, 111.57, 111.88), (111.88, 111.99, 111.82, 111.92), (111.92, 111.97, 111.82, 111.84),
    (111.84, 112.0, 111.82, 111.91), (111.93, 112.33, 111.6, 111.82), (111.81, 112.23, 111.42, 111.54),
    (111.54, 111.88, 111.38, 111.41), (111.37, 111.45, 110.81, 111.14), (111.13, 111.13, 110.14, 110.17),
    (110.16, 110.25, 109.64, 109.7), (109.68, 110.09, 109.6, 109.92), (109.91, 109.91, 109.4, 109.78),
    (109.77, 110.32, 109.76, 110.18), (110.16, 110.5, 109.49, 110.26), (110.24, 110.36, 108.92, 109.3),
]
# xyz:BRENTOIL 15m, 2026-11-02 03:45Z to 09:30Z
NOV2_15M = [
    (109.99, 110.03, 109.64, 109.7), (109.68, 109.74, 109.63, 109.67), (109.65, 109.92, 109.6, 109.86),
    (109.85, 110.09, 109.83, 110.01), (110.01, 110.05, 109.91, 109.92), (109.91, 109.91, 109.74, 109.75),
    (109.75, 109.83, 109.61, 109.75), (109.72, 109.74, 109.4, 109.54), (109.53, 109.86, 109.53, 109.78),
    (109.77, 110.11, 109.76, 110.01), (110.0, 110.14, 109.97, 110.01), (109.98, 110.23, 109.91, 110.23),
    (110.22, 110.32, 110.11, 110.18), (110.16, 110.24, 109.98, 110.03), (110.03, 110.15, 109.49, 109.94),
    (109.92, 110.5, 109.89, 110.44), (110.43, 110.5, 110.06, 110.26), (110.24, 110.36, 109.62, 109.65),
    (109.64, 109.93, 109.47, 109.49), (109.53, 109.61, 108.92, 109.35), (109.33, 109.36, 108.97, 109.3),
    (109.29, 109.3, 108.82, 108.87), (108.87, 108.89, 108.43, 108.68), (108.66, 108.83, 108.62, 108.62),
]
# the oil journal around it: (created, side, entry, invalidation)
NOV2_THESES = [((2026, 11, 1, 18, 19), "long", 111.7, 110.83), ((2026, 11, 2, 3, 48), "long", 110.0, 109.55),
               ((2026, 11, 2, 8, 40), "short", 110.2, 112.83)]


def nov2_bars():
    h0, q0 = ms(2026, 11, 1), ms(2026, 11, 2, 3, 45)
    c1h = [Candle(t=h0 + i * HOUR_MS, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(NOV2_1H)]
    c15 = [Candle(t=q0 + i * Q, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(NOV2_15M)]
    return c15, c1h


def nov2_direction(t: int):
    cur = None
    for parts, side, entry, inv in NOV2_THESES:
        if ms(*parts) <= t:
            cur = Direction(side=side, entry_ceiling=entry, invalidation_px=inv, regime_id=str(ms(*parts)))
    return cur


def _nov2(**over):
    c15, c1h = nov2_bars()
    return simulate(c15, c1h, nov2_direction, _base(size_slippage=True, **over))


def test_nov2_live_buys_the_break_and_is_stopped_on_the_0830_bar():
    run = _nov2()
    tr = run.trades[0]
    assert tr.side == "long" and tr.entry_time == ms(2026, 11, 2, 3, 45)
    assert tr.orig_stop_px == pytest.approx(109.99 - 0.75)              # under the mid: 110.14 low already broken
    assert tr.exit_reason == "stop" and tr.exit_time == ms(2026, 11, 2, 8, 30)
    assert tr.net_pnl_usd / 64.0 == pytest.approx(-0.165, abs=0.005)     # about a sixth of the desk


def test_nov2_risk_caps_cut_the_same_stop_to_the_cap():
    for cap in (0.05, 0.10, 0.15):
        tr = _nov2(risk_cap_frac=cap).trades[0]
        assert tr.exit_reason == "stop" and tr.exit_time == ms(2026, 11, 2, 8, 30)
        assert -tr.net_pnl_usd / 64.0 == pytest.approx(cap, abs=0.006), cap
    assert _nov2(risk_cap_frac=0.20).trades[0].net_pnl_usd == pytest.approx(_nov2().trades[0].net_pnl_usd)


def test_nov2_lower_leverage_scales_the_loss():
    live = _nov2().trades[0].net_pnl_usd
    for lev in (5.0, 10.0, 15.0):
        tr = _nov2(leverage=lev).trades[0]
        assert tr.net_pnl_usd == pytest.approx(live * lev / 20.0, rel=0.03), lev


def test_nov2_no_break_and_confirm_only_delay_the_long_and_it_still_loses():
    """Neither filter dodges the wrong call: no_break refuses the 03:45 break
    and buys 04:00 inside the new range; confirm_1h waits for a green hour
    (05:15). Both are closed by the 08:40 short at the 08:45 open, for less
    than the live stop because they bought lower with a wider stop."""
    live = _nov2().trades[0]
    nb = _nov2(no_break_entry=True)
    assert nb.refused["break"] >= 1
    assert nb.trades[0].entry_time == ms(2026, 11, 2, 4, 0)
    cf = _nov2(confirm_1h=True)
    assert cf.refused["confirm_1h"] >= 1
    assert cf.trades[0].entry_time == ms(2026, 11, 2, 5, 15)
    for tr in (nb.trades[0], cf.trades[0]):
        assert tr.exit_reason == "direction_flip" and tr.exit_time == ms(2026, 11, 2, 8, 45)
        assert live.net_pnl_usd < tr.net_pnl_usd < 0


def test_nov2_delay_and_side_age_do_not_touch_the_sim_entry():
    """The sim's 03:45 tick still sees the 11-01 long (the 03:48 rewrite is
    later and the same side), so a delay or a side age cannot see the
    two-minute gap the live entry had; see loss_fix_test.filter_hits for
    the real-fill view."""
    live = _nov2().trades[0]
    for over in ({"entry_delay_min": 60.0}, {"side_age_h": 8.0}):
        tr = _nov2(**over).trades[0]
        assert (tr.entry_time, tr.exit_reason, tr.net_pnl_usd) == (live.entry_time, live.exit_reason,
                                                                   live.net_pnl_usd), over
