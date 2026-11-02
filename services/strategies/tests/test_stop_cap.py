"""Gate tests for the daily stop-out cap (desk_tick_sim, Params.daily_stop_cap /
cap_per_side; 2026-11-02) and the per-tick ATR stop buffer (stop_buffer_atr).

Operator, 2026-11-02: the silver desk shorted three times into a rising
market; two stops, then a third short because a fresh thesis still said
short. The cap: after N stop exits in one UTC day, no new entries that day.

Synthetic candles only, no network. Levels come from a flat warmup: a 24h
range of 100 to 104, so a market long at 100.3 stops at 99.8 (0.2 under
the 24h low) with the target at 104, and a market short at 103.7 stops at
104.2 with the target at 100.
"""
from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    DAY_MS, Direction, Params, Trade, live_params, simulate, stop_cap_blocks, tally_stops)

HOUR_MS = 3_600_000
Q = 900_000


def _warmup(n: int = 24, lo: float = 100.0, hi: float = 104.0, mid: float = 102.0):
    return [Candle(t=i * HOUR_MS, o=mid, h=hi, l=lo, c=mid, v=1.0) for i in range(n)]


def _bars(rows, t0: int):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _params(**over) -> Params:
    """Live rules (market entry off the 24h range, 2R gate, limit target),
    no target offset and no slippage so prices are exact."""
    base = dataclasses.replace(live_params(), stop_buffer=0.2, target_offset_frac=0.0, slippage=0.0,
                               starting_equity=64.0)
    return dataclasses.replace(base, **over)


def _long(t):
    return Direction(side="long")


def _run(rows, direction=_long, warm_hours: int = 24, **over):
    c1h = _warmup(warm_hours)
    return simulate(_bars(rows, warm_hours * HOUR_MS), c1h, direction, _params(**over))


ENTER_LONG = (100.3, 100.4, 100.2, 100.3)      # flat tick: a market long at 100.3, stop 99.8
STOP_LONG = (100.3, 100.35, 99.7, 99.75)       # trades through 99.8
QUIET = (100.3, 100.35, 100.25, 100.3)         # a flat tick re-enters here
# three stops in a row, then a quiet bar to show what re-enters
THREE_STOPS = [ENTER_LONG, STOP_LONG, QUIET, STOP_LONG, QUIET, STOP_LONG, QUIET]


def _entries(run):
    return [t.entry_time for t in run.trades]


# ── defaults / golden ──────────────────────────────────────────────────────

def test_cap_is_off_by_default():
    p = Params()
    assert p.daily_stop_cap is None and not p.cap_per_side and p.stop_buffer_atr is None
    lp = live_params()
    assert lp.daily_stop_cap is None and lp.stop_buffer_atr is None


def test_off_is_byte_identical_and_on_changes_the_run():
    off = _run(THREE_STOPS)
    explicit = _run(THREE_STOPS, daily_stop_cap=None, cap_per_side=True)
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert off.equity_curve == explicit.equity_curve
    assert off.cap_blocked == [] and "stop_cap" not in off.refused
    on = _run(THREE_STOPS, daily_stop_cap=1)
    assert len(on.trades) < len(off.trades)


def test_cap_below_one_is_refused():
    with pytest.raises(ValueError):
        _run(THREE_STOPS, daily_stop_cap=0)


# ── blocks after N stops ───────────────────────────────────────────────────

def test_baseline_reenters_after_every_stop():
    run = _run(THREE_STOPS)
    assert [t.exit_reason for t in run.trades] == ["stop", "stop", "stop"]
    assert run.open_position_at_end is not None        # the 4th entry, on the last bar


@pytest.mark.parametrize("n", [1, 2, 3])
def test_cap_n_allows_exactly_n_stopped_entries(n):
    base = _run(THREE_STOPS)
    run = _run(THREE_STOPS, daily_stop_cap=n)
    assert [t.exit_reason for t in run.trades] == ["stop"] * n
    assert _entries(run) == _entries(base)[:n]          # the first n trades are untouched
    assert run.open_position_at_end is None             # nothing opened after the n-th stop
    blocked_times = [b["time"] for b in run.cap_blocked]
    assert blocked_times and all(t > run.trades[-1].exit_time for t in blocked_times)
    assert run.refused["stop_cap"] == len(run.cap_blocked)


def test_blocked_row_carries_the_setup_it_skipped():
    run = _run(THREE_STOPS, daily_stop_cap=1)
    b = run.cap_blocked[0]
    assert b["kind"] == "entry" and b["side"] == "long" and b["is_market"]
    assert (b["entry_px"], b["stop_px"], b["target_px"]) == pytest.approx((100.3, 99.8, 104.0))
    assert b["stops_today"] == 1
    assert b["time"] == 24 * HOUR_MS + 2 * Q            # the quiet bar after the first stop


def test_the_stop_bar_itself_never_reenters():
    # baseline and cap alike: a trade closed inside the bar, so no market open in it
    run = _run([ENTER_LONG, STOP_LONG], daily_stop_cap=1)
    assert run.cap_blocked == [] and len(run.trades) == 1


# ── UTC day reset ──────────────────────────────────────────────────────────

def test_count_resets_at_midnight_utc():
    # 47 warmup hours: the first tick is day 1, 23:00Z; bar 4 is 00:00Z day 2
    rows = [ENTER_LONG, STOP_LONG, QUIET, QUIET, QUIET, STOP_LONG, QUIET, QUIET]
    run = _run(rows, warm_hours=47, daily_stop_cap=1)
    t0 = 47 * HOUR_MS
    midnight = 2 * DAY_MS
    assert t0 + 4 * Q == midnight
    # 23:30 and 23:45 on day 1; then day 2's own stop (00:15) blocks 00:30 and 00:45
    assert [b["time"] for b in run.cap_blocked] == [t0 + 2 * Q, t0 + 3 * Q, t0 + 6 * Q, t0 + 7 * Q]
    assert _entries(run) == [t0, midnight]              # re-entered at 00:00Z
    assert run.trades[1].exit_reason == "stop" and run.trades[1].exit_time == t0 + 5 * Q
    assert [b["stops_today"] for b in run.cap_blocked] == [1, 1, 1, 1]
    assert run.open_position_at_end is None


def test_a_stop_after_midnight_counts_for_the_new_day_only():
    stops = Counter()
    trades = [Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=2,
                    exit_time=2 * DAY_MS - 1, exit_reason="stop"),
              Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=2,
                    exit_time=2 * DAY_MS, exit_reason="stop")]
    assert tally_stops(trades, 0, stops) == 2
    assert stops[(1, None)] == 1 and stops[(2, None)] == 1
    p = Params(daily_stop_cap=2)
    assert not stop_cap_blocks(stops, 2 * DAY_MS + 5, "long", p)
    assert stop_cap_blocks(stops, 2 * DAY_MS + 5, "long", Params(daily_stop_cap=1))


# ── what counts ────────────────────────────────────────────────────────────

def test_only_stop_exits_count():
    stops = Counter()
    trades = [Trade(side="short", entry_time=0, entry_px=1, size=1, stop_px=1.1, target_px=0.5,
                    exit_time=HOUR_MS, exit_reason=r)
              for r in ("target", "lock", "invalidation", "direction_flip", "time", "liquidation")]
    tally_stops(trades, 0, stops)
    assert sum(stops.values()) == 0
    assert not stop_cap_blocks(stops, HOUR_MS, "short", Params(daily_stop_cap=1))


def test_tally_counts_each_trade_once():
    stops = Counter()
    t = Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=2,
              exit_time=HOUR_MS, exit_reason="stop")
    start = tally_stops([t], 0, stops)
    start = tally_stops([t], start, stops)
    assert stops[(0, None)] == 1 and stops[(0, "long")] == 1


def test_a_target_exit_does_not_block_the_next_entry():
    target_bar = (100.3, 104.2, 100.3, 104.0)          # trades through the 104 target
    rows = [ENTER_LONG, target_bar, QUIET, QUIET]
    base = _run(rows)
    run = _run(rows, daily_stop_cap=1)
    assert run.trades[0].exit_reason == "target"
    assert _entries(run) == _entries(base) and run.cap_blocked == []
    assert run.open_position_at_end is not None


def test_cap_off_never_blocks():
    stops = Counter({(0, None): 99, (0, "long"): 99})
    assert not stop_cap_blocks(stops, 0, "long", Params())


# ── never touches an open position ─────────────────────────────────────────

def _short_then_long(t):
    return Direction(side="short") if t < 24 * HOUR_MS + 2 * Q else Direction(side="long")


ENTER_SHORT = (103.7, 103.8, 103.6, 103.7)         # market short at 103.7, stop 104.2, target 100
STOP_SHORT = (103.7, 104.3, 103.7, 104.25)
LONG_FLAT = QUIET                                  # market long at 100.3, stop 99.8, target 104
LONG_RUN = (100.3, 102.0, 100.25, 101.8)
LONG_TARGET = (101.8, 104.2, 101.8, 104.0)


def test_per_side_blocks_only_the_stopped_side():
    rows = [ENTER_SHORT, STOP_SHORT, LONG_FLAT, LONG_RUN, LONG_TARGET]
    base = _run(rows, _short_then_long)
    side = _run(rows, _short_then_long, daily_stop_cap=1, cap_per_side=True)
    desk = _run(rows, _short_then_long, daily_stop_cap=1)
    assert [(t.side, t.exit_reason) for t in base.trades] == [("short", "stop"), ("long", "target")]
    # per side: the short stop does not block the long, which runs to its target untouched
    assert [dataclasses.asdict(t) for t in side.trades] == [dataclasses.asdict(t) for t in base.trades][:2]
    assert side.cap_blocked == []
    # per desk: the long is blocked
    assert [t.side for t in desk.trades] == ["short"]
    assert desk.cap_blocked[0]["side"] == "long"


def test_per_side_still_blocks_the_stopped_side():
    def short_then_long_later(t):
        return Direction(side="short") if t < 24 * HOUR_MS + 3 * Q else Direction(side="long")
    rows = [ENTER_SHORT, STOP_SHORT, ENTER_SHORT, LONG_FLAT, LONG_FLAT]
    run = _run(rows, short_then_long_later, daily_stop_cap=1, cap_per_side=True)
    assert [b["side"] for b in run.cap_blocked] == ["short"]
    assert run.open_position_at_end is not None and run.open_position_at_end["side"] == "long"


def test_open_position_rides_through_the_cap_untouched():
    # side_1: the short stop blocks shorts; the long opened after it is held,
    # its stop and target unchanged, while later short setups stay blocked
    rows = [ENTER_SHORT, STOP_SHORT, LONG_FLAT, LONG_RUN, LONG_RUN, LONG_TARGET]
    base = _run(rows, _short_then_long)
    side = _run(rows, _short_then_long, daily_stop_cap=1, cap_per_side=True)
    lb, ls = base.trades[1], side.trades[1]
    assert dataclasses.asdict(lb) == dataclasses.asdict(ls)
    assert ls.stop_px == pytest.approx(99.8) and ls.target_px == pytest.approx(104.0) and not ls.stop_moved
    assert base.equity_curve == side.equity_curve


def test_flip_after_target_is_blocked_by_the_cap():
    rows = [ENTER_SHORT, STOP_SHORT, LONG_FLAT, LONG_TARGET, (104.0, 104.05, 103.95, 104.0), QUIET]
    base = _run(rows, _short_then_long, flip_after_target=True)
    side = _run(rows, _short_then_long, flip_after_target=True, daily_stop_cap=1, cap_per_side=True)
    assert [t.side for t in base.trades if t.is_flip] == ["short"]     # baseline flips short
    assert not any(t.is_flip for t in side.trades)
    flips = [b for b in side.cap_blocked if b["kind"] == "flip"]
    assert len(flips) == 1 and flips[0]["side"] == "short"
    assert flips[0]["target_px"] == pytest.approx(100.3)   # back to the long's entry
    assert side.open_position_at_end is not None and side.open_position_at_end["side"] == "long"


# ── per-tick ATR stop buffer ───────────────────────────────────────────────

def test_stop_buffer_atr_sets_the_stop_from_the_tick_atr():
    # the warmup's true range is 4 on every bar, so ATR14 is 4
    rows = [ENTER_LONG]
    run = simulate(_bars(rows, 24 * HOUR_MS), _warmup(), _long,
                   _params(stop_buffer_atr=0.05, min_r=1.5))
    assert run.open_position_at_end is not None
    run2 = simulate(_bars(rows + [STOP_LONG], 24 * HOUR_MS), _warmup(), _long,
                    _params(stop_buffer_atr=0.05, min_r=1.5))
    t = run2.trades[0]
    assert t.stop_px == pytest.approx(100.0 - 0.05 * 4.0)    # 99.8
    run3 = simulate(_bars(rows + [STOP_LONG], 24 * HOUR_MS), _warmup(), _long,
                    _params(stop_buffer_atr=0.02, min_r=1.5))
    assert run3.trades[0].stop_px == pytest.approx(99.92)    # 0.08 under the 24h low


def test_stop_buffer_atr_off_keeps_the_fixed_buffer():
    a = _run(THREE_STOPS)
    b = _run(THREE_STOPS, stop_buffer_atr=None)
    assert [dataclasses.asdict(t) for t in a.trades] == [dataclasses.asdict(t) for t in b.trades]
