"""Gate tests for the strategy variants added to the desk-tick simulator on
2026-10-30 (operator: "expand on various High ROI strategies ... think the
most profit PER DAY, THEN PER WEEK"): fixed-R targets, a scale-out with an
optional breakeven, a time stop, market entries, the session filter and the
structure lookbacks. Synthetic candles only; no network, no fees unless a
test says so.

Warm-up: 24 flat 1h bars, range [99, 101], ATR14 2.0. A long rests at the
8h low (99), stop 0.50 under it (98.50, risk 0.50 a unit), target the 24h
high (101, 4R).
"""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, _in_session, simulate)

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000
T0 = 24 * HOUR_MS


def _1h(n=24, l=99.0, h=101.0):
    return [Candle(t=i * HOUR_MS, o=100.0, h=h, l=l, c=100.0, v=1.0) for i in range(n)]


def _b(i, o, h, l, c):
    return Candle(t=T0 + i * FIFTEEN_MS, o=o, h=h, l=l, c=c, v=1.0)


def _long(**kw):
    d = Direction(side="long", **kw)
    return lambda t: d


def _p(**kw):
    base = dict(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    base.update(kw)
    return Params(**base)


REST_AND_FILL = [_b(0, 99.3, 99.3, 99.3, 99.3),      # mid 0.3 from the 99 edge: rest a limit
                 _b(1, 99.2, 99.3, 98.9, 99.0)]      # trades through 99: filled
SIZE = 64.0 * 20 / 99.0                              # full-port size at 20x, no fees


def test_the_new_defaults_change_nothing():
    p = Params()
    assert (p.target_r, p.partial_frac, p.time_stop_hours, p.entry_mode,
            p.entry_lookback_h, p.target_lookback_h, p.session_utc) == (None, 0.0, None, "edge", 8, 24, None)


def test_a_fixed_r_target_replaces_the_far_edge():
    bars = REST_AND_FILL + [_b(2, 99.2, 100.05, 99.1, 100.0)]   # reaches 2R (100.0), not the 101 edge
    run = simulate(bars, _1h(), _long(), _p(target_r=2.0))
    t = run.trades[0]
    assert t.target_px == pytest.approx(100.0) and t.exit_reason == "target"
    assert t.r_multiple == pytest.approx(2.0)
    # the default keeps the far edge and is still open
    assert simulate(bars, _1h(), _long(), _p()).trades == []


def test_a_scale_out_banks_half_at_1r_and_the_rest_rides_to_the_target():
    bars = REST_AND_FILL + [_b(2, 99.2, 99.6, 99.1, 99.4),       # through 99.50 (1R): half off
                            _b(3, 99.5, 101.2, 99.4, 101.0)]     # target 101 on the rest
    run = simulate(bars, _1h(), _long(), _p(partial_frac=0.5, partial_r=1.0))
    t = run.trades[0]
    assert t.exit_reason == "target"
    assert t.size == pytest.approx(SIZE / 2)                      # the top-up never refilled the half
    # half at +0.5 and half at +2.0 on a 0.5 risk: 0.5 + 2.0 = 2.5R
    assert t.r_multiple == pytest.approx(2.5)
    assert t.net_pnl_usd == pytest.approx(SIZE * 0.5 * 0.5 + SIZE * 0.5 * 2.0)
    assert run.final_equity == pytest.approx(64.0 + t.net_pnl_usd)
    assert run.gross_pnl_usd == pytest.approx(t.net_pnl_usd)     # no fees here, so gross == net


def test_breakeven_after_the_scale_out_turns_a_stop_into_a_small_win():
    bars = REST_AND_FILL + [_b(2, 99.2, 99.6, 99.1, 99.4),
                            _b(3, 99.3, 99.4, 98.9, 99.0)]       # back to 98.90: the 98.50 stop would not fire
    plain = simulate(bars, _1h(), _long(), _p(partial_frac=0.5))
    assert plain.trades == []                                     # still open under the original stop
    run = simulate(bars, _1h(), _long(), _p(partial_frac=0.5, partial_breakeven=True))
    t = run.trades[0]
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(99.0) and t.stop_moved
    assert t.r_multiple == pytest.approx(0.5)                     # only the banked half


def test_the_stop_wins_a_bar_that_also_reaches_the_scale_out():
    bars = REST_AND_FILL + [_b(2, 99.0, 99.6, 98.4, 98.6)]
    t = simulate(bars, _1h(), _long(), _p(partial_frac=0.5)).trades[0]
    assert t.exit_reason == "stop" and t.size == pytest.approx(SIZE)
    assert t.r_multiple == pytest.approx(-1.0)


def test_a_scale_out_past_the_target_is_skipped_and_the_target_takes_it_all():
    bars = REST_AND_FILL + [_b(2, 99.5, 101.2, 99.4, 101.0)]
    t = simulate(bars, _1h(), _long(), _p(partial_frac=0.5, partial_r=5.0)).trades[0]
    assert t.exit_reason == "target" and t.size == pytest.approx(SIZE)


def test_a_time_stop_closes_at_the_open_once_the_time_is_up():
    bars = REST_AND_FILL + [_b(2, 99.2, 99.4, 99.1, 99.3),        # 15 min in
                            _b(3, 99.35, 99.4, 99.1, 99.3)]       # 30 min in: out at 99.35
    t = simulate(bars, _1h(), _long(), _p(time_stop_hours=0.5)).trades[0]
    assert t.exit_reason == "time" and t.exit_px == pytest.approx(99.35)
    assert t.exit_time == T0 + 3 * FIFTEEN_MS


def test_a_market_entry_takes_the_mid_with_the_stop_beyond_the_range():
    bars = [_b(0, 99.5, 99.6, 99.4, 99.5),
            _b(1, 99.6, 101.1, 99.5, 101.0)]
    p = _p(entry_mode="market", target_r=2.0, stop_buffer=0.25)
    run = simulate(bars, _1h(), _long(), p)
    t = run.trades[0]
    assert t.entry_px == pytest.approx(99.5) and t.stop_px == pytest.approx(98.75)
    assert t.target_px == pytest.approx(101.0) and t.exit_reason == "target"
    # one fill: the bar that hit the target cannot re-enter at its own open
    assert run.filled == 1 and run.rested == 0 and run.open_position_at_end is None


def test_a_market_entry_never_chases_above_the_thesis_entry():
    bars = [_b(0, 99.5, 99.6, 99.4, 99.5)]
    run = simulate(bars, _1h(), _long(entry_ceiling=99.3), _p(entry_mode="market", target_r=2.0, stop_buffer=0.25))
    assert run.filled == 0 and run.refused["chase"] == 1


def test_a_market_entry_can_be_stopped_out_on_its_own_bar():
    bars = [_b(0, 99.5, 99.6, 98.6, 98.7)]                        # opens 99.50, trades to 98.60 < 98.75
    t = simulate(bars, _1h(), _long(), _p(entry_mode="market", target_r=2.0, stop_buffer=0.25)).trades[0]
    assert t.exit_reason == "stop" and t.r_multiple == pytest.approx(-1.0)


def test_an_unknown_entry_mode_is_an_error():
    with pytest.raises(ValueError):
        simulate([], [], _long(), Params(entry_mode="breakout"))


def test_the_session_filter_blocks_new_entries_outside_its_hours():
    assert _in_session(0, (0, 24)) and not _in_session(0, (1, 2))
    assert _in_session(23 * HOUR_MS, (22, 6)) and _in_session(2 * HOUR_MS, (22, 6))
    assert not _in_session(12 * HOUR_MS, (22, 6))
    # the bars sit at UTC hour 0
    assert simulate(REST_AND_FILL, _1h(), _long(), _p(session_utc=(7, 21))).rested == 0
    assert simulate(REST_AND_FILL, _1h(), _long(), _p(session_utc=(22, 6))).rested == 1


def test_a_shorter_entry_lookback_rests_on_the_nearer_low():
    c1h = _1h(20) + [Candle(t=(20 + i) * HOUR_MS, o=100.0, h=100.4, l=99.6, c=100.0, v=1.0) for i in range(4)]
    bars = [_b(0, 99.8, 99.8, 99.8, 99.8), _b(1, 99.7, 99.7, 99.5, 99.6)]
    assert simulate(bars, c1h, _long(), _p()).rested == 0                 # 8h low 99.0 is too far
    run = simulate(bars, c1h, _long(), _p(entry_lookback_h=4))
    assert run.rested == 1 and run.filled == 1                            # the 4h low, 99.6


def test_a_longer_target_lookback_waits_for_the_history():
    assert simulate(REST_AND_FILL, _1h(), _long(), _p(target_lookback_h=48)).rested == 0
    run = simulate([Candle(t=48 * HOUR_MS + b.t - T0, o=b.o, h=b.h, l=b.l, c=b.c, v=1.0) for b in REST_AND_FILL],
                   _1h(48), _long(), _p(target_lookback_h=48))
    assert run.rested == 1
