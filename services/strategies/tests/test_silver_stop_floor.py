"""Gate tests for the stop buffer floor (desk_tick_sim, Params.stop_buffer_floor /
stop_buffer_floor_frac; 2026-11-02).

Operator, 2026-11-02 ("yes"): the live silver stop (1.3 x the tick's 1h
ATR14) ran about half the buffer the 30-day silver test validated. The floor:
buffer = max(stop_buffer_atr x ATR14, floor, floor_frac x entry), only with
stop_buffer_atr on.

Synthetic candles only, no network. The warmup is a flat 24h range of 100
to 104 with a true range of 4 on every bar, so ATR14 is 4 and
stop_buffer_atr=0.05 gives a 0.2 live buffer: a market long at 100.3 stops
at 99.8 (target 104, 7.4R); a market short at 103.7 stops at 104.2.
"""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, Run, floored_buffer, live_params, simulate)

HOUR_MS = 3_600_000
Q = 900_000
T0 = 24 * HOUR_MS


def _warmup(n: int = 24):
    return [Candle(t=i * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(n)]


def _bars(rows, t0: int = T0):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _params(**over) -> Params:
    """Live rules, no target offset, no slippage, the 0.2 ATR buffer on."""
    base = dataclasses.replace(live_params(), stop_buffer=0.2, target_offset_frac=0.0, slippage=0.0,
                               starting_equity=64.0, stop_buffer_atr=0.05)
    return dataclasses.replace(base, **over)


LONG = lambda t: Direction(side="long")      # noqa: E731
SHORT = lambda t: Direction(side="short")    # noqa: E731

ENTER_LONG = (100.3, 100.4, 100.2, 100.3)
ENTER_SHORT = (103.7, 103.8, 103.6, 103.7)
DIP_LONG = (100.3, 100.35, 99.7, 100.0)       # through the live 99.8 stop, not through 99.5
DEEP_LONG = (100.0, 100.05, 99.4, 99.45)      # through 99.5 too
POP_SHORT = (103.7, 104.3, 103.65, 104.0)     # through the live 104.2 stop, not through 104.5


def _run(rows, direction=LONG, **over) -> Run:
    return simulate(_bars(rows), _warmup(), direction, _params(**over))


# ── the pure helper ────────────────────────────────────────────────────────

def test_floored_buffer_is_the_max_and_never_narrows():
    assert floored_buffer(0.2, 50.0, None, None) == 0.2
    assert floored_buffer(0.2, 50.0, 0.41, None) == 0.41
    assert floored_buffer(0.6, 50.0, 0.41, None) == 0.6            # a wider ATR stop stays
    assert floored_buffer(0.2, 50.0, None, 0.008) == pytest.approx(0.4)
    assert floored_buffer(0.6, 50.0, None, 0.008) == 0.6
    assert floored_buffer(0.2, 50.0, 0.30, 0.008) == pytest.approx(0.4)   # the larger floor wins
    assert floored_buffer(0.2, 50.0, 0.45, 0.008) == 0.45
    assert floored_buffer(0.2, 50.0, 0.0, 0.0) == 0.2


# ── off by default, byte-identical when off ────────────────────────────────

def test_floor_is_off_by_default():
    for p in (Params(), live_params()):
        assert p.stop_buffer_floor is None and p.stop_buffer_floor_frac is None


def test_off_is_byte_identical():
    rows = [ENTER_LONG, DIP_LONG, ENTER_LONG, DEEP_LONG, ENTER_LONG]
    a = _run(rows)
    b = _run(rows, stop_buffer_floor=None, stop_buffer_floor_frac=None)
    assert [dataclasses.asdict(t) for t in a.trades] == [dataclasses.asdict(t) for t in b.trades]
    assert a.equity_curve == b.equity_curve and a.refused == b.refused
    assert a.floor_widened == 0 and not a.floor_blocked and a.floor_blocked_rows == []


def test_floor_never_touches_a_fixed_buffer():
    """Brent: stop_buffer_atr off. The live floor would sit after Brent's
    return in stop_buffer_for, so a fixed buffer must ignore it."""
    rows = [ENTER_LONG, DIP_LONG, ENTER_LONG, DEEP_LONG, ENTER_LONG]
    a = _run(rows, stop_buffer_atr=None)
    b = _run(rows, stop_buffer_atr=None, stop_buffer_floor=1.0, stop_buffer_floor_frac=0.009)
    assert [dataclasses.asdict(t) for t in a.trades] == [dataclasses.asdict(t) for t in b.trades]
    assert a.equity_curve == b.equity_curve and b.floor_widened == 0


@pytest.mark.parametrize("over", [{"stop_buffer_floor": -0.1}, {"stop_buffer_floor_frac": -0.01},
                                  {"stop_buffer_floor_frac": 1.0}])
def test_bad_floor_is_refused(over):
    with pytest.raises(ValueError):
        _run([ENTER_LONG], **over)


# ── the floor applies only under it ────────────────────────────────────────

def test_long_floor_widens_a_narrow_atr_stop():
    live = _run([ENTER_LONG, DEEP_LONG])
    assert live.trades[0].stop_px == pytest.approx(99.8)
    run = _run([ENTER_LONG, DEEP_LONG], stop_buffer_floor=0.5)
    assert run.trades[0].stop_px == pytest.approx(99.5)
    assert run.floor_widened == 1


def test_short_floor_widens_a_narrow_atr_stop():
    run = _run([ENTER_SHORT, (103.7, 104.6, 103.6, 104.55)], direction=SHORT, stop_buffer_floor=0.5)
    assert run.trades[0].side == "short" and run.trades[0].stop_px == pytest.approx(104.5)
    run = _run([ENTER_SHORT, (103.7, 104.6, 103.6, 104.55)], direction=SHORT, stop_buffer_floor_frac=0.005)
    assert run.trades[0].stop_px == pytest.approx(104.0 + 0.005 * 103.7)


def test_frac_floor_scales_with_the_entry_price():
    run = _run([ENTER_LONG, DEEP_LONG], stop_buffer_floor_frac=0.005)
    assert run.trades[0].stop_px == pytest.approx(100.0 - 0.005 * 100.3)


@pytest.mark.parametrize("side,rows,direction,live_stop", [
    ("long", [ENTER_LONG, DEEP_LONG], LONG, 99.8),
    ("short", [ENTER_SHORT, POP_SHORT], SHORT, 104.2)])
def test_floor_never_narrows_a_wider_atr_stop(side, rows, direction, live_stop):
    a = _run(rows, direction=direction)
    b = _run(rows, direction=direction, stop_buffer_floor=0.1, stop_buffer_floor_frac=0.001)
    assert a.trades[0].stop_px == pytest.approx(live_stop) == b.trades[0].stop_px
    assert [dataclasses.asdict(t) for t in a.trades] == [dataclasses.asdict(t) for t in b.trades]
    assert b.floor_widened == 0


@pytest.mark.parametrize("rows,direction", [([ENTER_LONG, DIP_LONG], LONG), ([ENTER_SHORT, POP_SHORT], SHORT)])
def test_floor_keeps_a_trade_the_live_stop_loses(rows, direction):
    live = _run(rows, direction=direction)
    assert [t.exit_reason for t in live.trades] == ["stop"]
    floored = _run(rows, direction=direction, stop_buffer_floor=0.5)
    assert floored.trades == [] and floored.open_position_at_end is not None


# ── the gates read the floored stop ────────────────────────────────────────

def test_two_r_gate_uses_the_floored_stop():
    # 10x so the 20x line cannot be the one that refuses. Floor 1.6: risk
    # 0.3 + 1.6 = 1.9 against 3.7 to the target is 1.95R, under 2R; the live
    # 0.2 buffer pays 7.4R.
    live = _run([ENTER_LONG], leverage=10.0)
    assert live.open_position_at_end is not None
    run = _run([ENTER_LONG], leverage=10.0, stop_buffer_floor=1.6)
    assert run.open_position_at_end is None and run.trades == []
    assert run.refused["min_r"] == 1 and run.floor_blocked == {"min_r": 1}
    row = run.floor_blocked_rows[0]
    assert row["gate"] == "min_r" and row["side"] == "long" and row["time"] == T0
    assert row["stop_px"] == pytest.approx(98.4) and row["raw_stop_px"] == pytest.approx(99.8)
    # 1.5: risk 1.8, 2.06R, passes and trades at the floored stop
    ok = _run([ENTER_LONG, (100.3, 100.35, 98.4, 98.45)], leverage=10.0, stop_buffer_floor=1.5)
    assert ok.trades[0].stop_px == pytest.approx(98.5) and not ok.floor_blocked


def test_twenty_x_line_still_applies_to_a_floored_stop():
    # 20x on a 20x-max coin: the stop may use half the ~2.56% liquidation
    # distance, about 1.29 on 100.3. Floor 1.1: risk 1.4 (2.64R) fails the
    # 20x line; the live 0.5 risk passes both gates.
    run = _run([ENTER_LONG], stop_buffer_floor=1.1)
    assert run.open_position_at_end is None
    assert run.refused["leverage"] == 1 and run.floor_blocked == {"leverage": 1}
    assert run.floor_blocked_rows[0]["gate"] == "leverage"


def test_a_gate_the_live_stop_fails_too_is_not_blamed_on_the_floor():
    # mid at 102: target 104 is 2.0 away, the live 2.2 risk already fails 2R
    rows = [(102.0, 102.1, 101.9, 102.0)]
    live = _run(rows, leverage=10.0)
    assert live.refused["min_r"] == 1
    run = _run(rows, leverage=10.0, stop_buffer_floor=0.5)
    assert run.refused["min_r"] == 1 and not run.floor_blocked and run.floor_blocked_rows == []


def test_blocked_ticks_repeat_each_tick_the_setup_stays_refused():
    run = _run([ENTER_LONG, ENTER_LONG, ENTER_LONG], leverage=10.0, stop_buffer_floor=1.6)
    assert run.floor_blocked == {"min_r": 3}
    assert [r["time"] for r in run.floor_blocked_rows] == [T0, T0 + Q, T0 + 2 * Q]
