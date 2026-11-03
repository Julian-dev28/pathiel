"""Gate tests for the event-blackout entry filter (desk_tick_sim,
Params.event_blackout_windows / in_event_blackout; 2026-11-03,
OIL_EVENT_TIMING_TEST_2026-11-03.md). The windows themselves come from
oil_calendar (its own gate tests cover the timestamps); this file only
covers the simulator hook. Synthetic candles only, no network.
"""
from __future__ import annotations

import dataclasses

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, in_event_blackout, live_params, simulate)

HOUR_MS = 3_600_000
Q = 900_000


def _warmup(n: int = 24, lo: float = 100.0, hi: float = 104.0, mid: float = 102.0):
    return [Candle(t=i * HOUR_MS, o=mid, h=hi, l=lo, c=mid, v=1.0) for i in range(n)]


def _bars(rows, t0: int):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _params(**over) -> Params:
    base = dataclasses.replace(live_params(), stop_buffer=0.2, target_offset_frac=0.0, slippage=0.0,
                               starting_equity=64.0)
    return dataclasses.replace(base, **over)


def _long(t):
    return Direction(side="long")


ENTER_LONG = (100.3, 100.4, 100.2, 100.3)      # a market long fires here (24h low 100, stop 99.8 - 0.2)
QUIET = (100.3, 100.35, 100.25, 100.3)


def _run(rows, direction=_long, warm_hours: int = 24, **over):
    c1h = _warmup(warm_hours)
    return simulate(_bars(rows, warm_hours * HOUR_MS), c1h, direction, _params(**over))


# ── in_event_blackout, pure ─────────────────────────────────────────────────

def test_in_event_blackout_none_or_empty_is_never_blackout():
    assert in_event_blackout(1_000, None) is False
    assert in_event_blackout(1_000, ()) is False
    assert in_event_blackout(1_000, []) is False


def test_in_event_blackout_half_open_boundaries():
    windows = ((1000, 2000),)
    assert in_event_blackout(999, windows) is False
    assert in_event_blackout(1000, windows) is True    # inclusive start
    assert in_event_blackout(1999, windows) is True
    assert in_event_blackout(2000, windows) is False   # exclusive end


def test_in_event_blackout_multiple_windows():
    windows = ((0, 10), (100, 110))
    assert in_event_blackout(5, windows) is True
    assert in_event_blackout(50, windows) is False
    assert in_event_blackout(105, windows) is True


# ── Params default / golden ──────────────────────────────────────────────

def test_default_is_off():
    assert Params().event_blackout_windows is None
    assert live_params().event_blackout_windows is None


def test_off_is_byte_identical_to_explicit_none():
    off = _run([ENTER_LONG, QUIET, QUIET])
    explicit = _run([ENTER_LONG, QUIET, QUIET], event_blackout_windows=None)
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert off.equity_curve == explicit.equity_curve
    assert "event_blackout" not in off.refused


def test_a_window_outside_the_data_changes_nothing():
    t0 = 24 * HOUR_MS
    off = _run([ENTER_LONG, QUIET, QUIET])
    far = _run([ENTER_LONG, QUIET, QUIET], event_blackout_windows=((t0 - 10 * HOUR_MS, t0 - 9 * HOUR_MS),))
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in far.trades]
    assert "event_blackout" not in far.refused


# ── blocking behaviour ───────────────────────────────────────────────────

def test_a_window_covering_the_entry_tick_blocks_it():
    t0 = 24 * HOUR_MS   # the first simulated bar's timestamp
    off = _run([ENTER_LONG, QUIET])
    assert off.open_position_at_end is not None
    assert off.open_position_levels["opened_at"] == t0   # fires on bar 0, no window in the way

    blocked = _run([ENTER_LONG, QUIET], event_blackout_windows=((t0, t0 + Q),))
    assert blocked.refused["event_blackout"] == 1
    assert blocked.open_position_at_end is not None
    # the entry that fired at bar 0 in `off` now waits one tick, until the
    # window (which covers only bar 0) has passed.
    assert blocked.open_position_levels["opened_at"] == t0 + Q
    assert blocked.open_position_levels["opened_at"] > off.open_position_levels["opened_at"]


def test_window_narrower_than_a_tick_never_blocks():
    t0 = 24 * HOUR_MS
    off = _run([ENTER_LONG, QUIET, QUIET])
    # a window that starts and ends between two ticks (never equal to a
    # tick's own timestamp) matches no bar: half-open [start, end) with
    # start == end is empty by construction.
    nothing = _run([ENTER_LONG, QUIET, QUIET], event_blackout_windows=((t0 + 1, t0 + 1),))
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in nothing.trades]


def test_blackout_never_closes_an_open_position():
    # Open on bar 0 (no blackout yet), then a blackout window covers the
    # bar where the stop would otherwise trade through: the stop still
    # fires -- the filter only ever refuses a NEW entry.
    t0 = 24 * HOUR_MS
    STOP_LONG = (100.3, 100.35, 99.7, 99.75)
    rows = [ENTER_LONG, STOP_LONG]
    off = _run(rows)
    assert off.trades and off.trades[0].exit_reason == "stop"

    blocked = _run(rows, event_blackout_windows=((t0 + Q, t0 + 2 * Q),))
    assert blocked.trades and blocked.trades[0].exit_reason == "stop"
    assert blocked.trades[0].exit_time == off.trades[0].exit_time
