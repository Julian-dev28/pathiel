"""Gate tests for the 2026-11-02 sim options behind BRENT_STOP_BUFFER_TEST_2026-11-02.md
(desk_tick_sim): the floor under a fixed buffer (`fixed_buffer_floor_frac`),
the oil futures weekend window (`weekend_closed`, DST edges included),
weekend-off entries (`weekend_off` "hold" / "flatten"), the weekend-only
profit lock (`lock_weekend_only`), the target-share trigger and the
break-even-plus-fees lock.

Synthetic candles only, no network. Real 2026 timestamps, so the weekend
window means something: the warmup is 24 flat 1h bars (range 100 to 104,
ATR14 4) ending Friday 2026-11-06 00:00Z, and the 15m bars start at
Friday 20:00Z, one hour before the winter close (16:00 New York = 21:00Z).
A market long at 100.3 with a 0.2 buffer stops at 99.8 (risk 0.5) and
targets 104 (7.4R).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, live_params, lock_on, profit_lock_stop, simulate, weekend_closed)

HOUR_MS = 3_600_000
Q = 900_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


FRI = ms(2026, 11, 6)            # Friday, US winter time (EST since 2026-11-01)
T0 = FRI + 20 * HOUR_MS          # Friday 20:00Z: one hour before the close
LONG = lambda t: Direction(side="long")      # noqa: E731

ENTER_LONG = (100.3, 100.4, 100.2, 100.3)
FLAT = (100.3, 100.4, 100.25, 100.3)


def _warmup(end: int = FRI, n: int = 24):
    return [Candle(t=end - (n - i) * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(n)]


def _bars(rows, t0: int = T0):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _params(**over) -> Params:
    base = dataclasses.replace(live_params(), stop_buffer=0.2, target_offset_frac=0.0, slippage=0.0,
                               starting_equity=64.0)
    return dataclasses.replace(base, **over)


def _run(rows, t0: int = T0, direction=LONG, **over):
    return simulate(_bars(rows, t0), _warmup(), direction, _params(**over))


def _asdicts(run):
    return [dataclasses.asdict(t) for t in run.trades]


# ── the weekend window ─────────────────────────────────────────────────────

@pytest.mark.parametrize("when,closed", [
    # US winter (EST): Fri 21:00Z to Sun 23:00Z
    ((2026, 11, 6, 20, 59), False), ((2026, 11, 6, 21, 0), True), ((2026, 11, 7, 12, 0), True),
    ((2026, 11, 8, 22, 59), True), ((2026, 11, 8, 23, 0), False), ((2026, 11, 9, 0, 0), False),
    # US summer (EDT): Fri 20:00Z to Sun 22:00Z
    ((2026, 10, 23, 19, 59), False), ((2026, 10, 23, 20, 0), True),
    ((2026, 10, 25, 21, 59), True), ((2026, 10, 25, 22, 0), False),
    # the fall-back weekend: closes on summer time, reopens on winter time
    ((2026, 10, 30, 19, 59), False), ((2026, 10, 30, 20, 0), True), ((2026, 10, 31, 16, 18), True),
    ((2026, 11, 1, 22, 30), True), ((2026, 11, 1, 23, 0), False), ((2026, 11, 2, 0, 51), False),
    # the spring-forward weekend (2027-03-14): closes on winter time, reopens on summer time
    ((2027, 3, 12, 20, 30), False), ((2027, 3, 12, 21, 0), True),
    ((2027, 3, 14, 21, 59), True), ((2027, 3, 14, 22, 0), False),
    # US on summer time, UK still on winter time (2026-03-27): New York sets the clock
    ((2026, 3, 27, 19, 59), False), ((2026, 3, 27, 20, 0), True),
    # weekdays are always open
    ((2026, 11, 2, 12, 0), False), ((2026, 11, 5, 23, 59), False), ((2026, 11, 4, 3, 0), False),
])
def test_weekend_window(when, closed):
    assert weekend_closed(ms(*when)) is closed


def test_weekend_window_is_one_contiguous_block_a_week():
    """Walk a winter week in 15 minute steps: exactly one closed stretch,
    Fri 21:00Z to Sun 23:00Z, 50 hours."""
    start = ms(2026, 11, 2)
    flags = [weekend_closed(start + i * Q) for i in range(7 * 96)]
    edges = [i for i in range(1, len(flags)) if flags[i] != flags[i - 1]]
    assert len(edges) == 2
    assert start + edges[0] * Q == ms(2026, 11, 6, 21) and start + edges[1] * Q == ms(2026, 11, 8, 23)
    assert sum(flags) == 50 * 4


# ── the floor under a fixed buffer ─────────────────────────────────────────

def test_new_options_are_off_by_default():
    for p in (Params(), live_params()):
        assert p.fixed_buffer_floor_frac is None and p.weekend_off is None
        assert p.lock_weekend_only is False and p.lock_trigger_target_frac is None and p.lock_plus_fees is False
        assert not lock_on(p)


def test_defaults_spelled_out_are_byte_identical():
    rows = [ENTER_LONG, FLAT, (100.3, 100.9, 99.7, 99.9), ENTER_LONG, FLAT, FLAT]
    a = _run(rows)
    b = _run(rows, fixed_buffer_floor_frac=None, weekend_off=None, lock_weekend_only=False,
             lock_trigger_target_frac=None, lock_plus_fees=False)
    assert _asdicts(a) == _asdicts(b) and a.equity_curve == b.equity_curve and a.refused == b.refused


def test_fixed_floor_widens_the_fixed_buffer():
    live = _run([ENTER_LONG, FLAT])
    assert live.open_position_at_end is not None
    run = _run([ENTER_LONG, (100.3, 100.35, 99.4, 99.45)], fixed_buffer_floor_frac=0.005)
    assert run.trades[0].stop_px == pytest.approx(100.0 - 0.005 * 100.3)
    assert run.floor_widened == 1


def test_fixed_floor_never_narrows():
    rows = [ENTER_LONG, (100.3, 100.35, 99.7, 99.75)]
    a = _run(rows)
    b = _run(rows, fixed_buffer_floor_frac=0.001)          # 0.1 under the 0.2 buffer
    assert _asdicts(a) == _asdicts(b) and b.floor_widened == 0
    assert a.trades[0].stop_px == pytest.approx(99.8)


def test_fixed_floor_is_ignored_by_the_atr_rule():
    rows = [ENTER_LONG, (100.3, 100.35, 99.7, 99.75)]
    a = _run(rows, stop_buffer_atr=0.05)
    b = _run(rows, stop_buffer_atr=0.05, fixed_buffer_floor_frac=0.01)
    assert _asdicts(a) == _asdicts(b) and b.floor_widened == 0


def test_fixed_floor_goes_through_the_gates():
    # 10x: floor 1.6% = 1.6 under the 100 low, risk 1.9 vs 3.7 to target, 1.95R
    run = _run([ENTER_LONG], leverage=10.0, fixed_buffer_floor_frac=0.016)
    assert run.refused["min_r"] == 1 and run.floor_blocked == {"min_r": 1}
    # 20x: floor 1.1% = 1.1, risk 1.4, past half the ~1.29 liquidation room
    run = _run([ENTER_LONG], fixed_buffer_floor_frac=0.011)
    assert run.refused["leverage"] == 1 and run.floor_blocked == {"leverage": 1}


@pytest.mark.parametrize("over", [{"fixed_buffer_floor_frac": -0.01}, {"fixed_buffer_floor_frac": 1.0},
                                  {"weekend_off": "sleep"},
                                  {"lock_trigger_target_frac": 0.5, "lock_trigger_r": 1.0},
                                  {"lock_trigger_target_frac": 0.0}, {"lock_trigger_target_frac": 1.0}])
def test_bad_options_are_refused(over):
    with pytest.raises(ValueError):
        _run([ENTER_LONG], **over)


# ── weekend off ────────────────────────────────────────────────────────────

SAT = ms(2026, 11, 7, 12)


def test_hold_takes_no_entry_inside_the_window():
    live = _run([ENTER_LONG, FLAT], t0=SAT)
    assert live.open_position_at_end is not None
    run = _run([ENTER_LONG, FLAT], t0=SAT, weekend_off="hold")
    assert run.trades == [] and run.open_position_at_end is None and run.refused["weekend"] == 2


@pytest.mark.parametrize("mode", ["hold", "flatten"])
def test_entries_resume_at_the_reopen(mode):
    sun = ms(2026, 11, 8, 22, 45)               # one tick before the 23:00Z winter reopen
    run = _run([ENTER_LONG, ENTER_LONG, FLAT], t0=sun, weekend_off=mode)
    assert run.refused["weekend"] == 1 and run.open_position_at_end is not None
    assert run.open_position_at_end["entry_px"] == pytest.approx(100.3)


def test_hold_keeps_a_position_opened_before_the_window():
    # in at 20:00Z, still open through the 21:00Z close with its own stop
    rows = [ENTER_LONG, FLAT, FLAT, FLAT, (100.5, 100.6, 100.4, 100.5), FLAT, (100.3, 100.35, 99.7, 99.75)]
    live = _run(rows)
    run = _run(rows, weekend_off="hold")
    assert _asdicts(live) == _asdicts(run)
    assert [t.exit_reason for t in run.trades] == ["stop"]


def test_flatten_closes_at_the_window_open():
    rows = [ENTER_LONG, FLAT, FLAT, FLAT, (100.5, 100.6, 100.4, 100.5), ENTER_LONG]
    run = _run(rows, weekend_off="flatten")
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.exit_reason == "weekend" and tr.exit_time == ms(2026, 11, 6, 21) and tr.exit_px == pytest.approx(100.5)
    assert run.open_position_at_end is None and run.refused["weekend"] == 2


def test_flatten_takes_size_slippage_on_the_exit():
    rows = [ENTER_LONG, FLAT, FLAT, FLAT, (100.5, 100.6, 100.4, 100.5)]
    run = _run(rows, weekend_off="flatten", slippage=0.001)
    assert run.trades[0].exit_px == pytest.approx(100.5 * (1 - 0.001))


# ── weekend-only profit lock ───────────────────────────────────────────────

UP_WEEKDAY = (100.3, 101.0, 100.3, 100.9)         # +1.4R on Friday 20:15Z
UP_WEEKEND = (100.6, 100.9, 100.55, 100.8)        # +1.2R inside the window
BACK = (100.4, 100.45, 100.2, 100.25)             # back through the entry


def test_weekday_bars_never_arm_a_weekend_lock():
    # Friday: up 1.4R, then back down to the stop, all before 21:00Z
    rows = [ENTER_LONG, UP_WEEKDAY, BACK, (100.2, 100.25, 99.7, 99.75)]
    all_week = _run(rows, lock_trigger_r=1.0, lock_r=0.0)
    assert all_week.trades[0].exit_reason == "lock"
    wk = _run(rows, lock_trigger_r=1.0, lock_r=0.0, lock_weekend_only=True)
    assert _asdicts(wk) == _asdicts(_run(rows)) and wk.trades[0].exit_reason == "stop"


def test_weekend_bars_arm_the_lock():
    rows = [ENTER_LONG, FLAT, FLAT, FLAT, UP_WEEKEND, BACK]
    run = _run(rows, lock_trigger_r=1.0, lock_r=0.0, lock_weekend_only=True)
    tr = run.trades[0]
    assert tr.exit_reason == "lock" and tr.exit_px == pytest.approx(100.3) and tr.exit_time == T0 + 5 * Q


def test_a_weekday_peak_is_not_folded_into_the_weekend_best():
    # +1.4R on Friday 20:15Z, then only +0.6R inside the window: no lock
    rows = [ENTER_LONG, UP_WEEKDAY, FLAT, FLAT, (100.5, 100.6, 100.4, 100.5), BACK]
    run = _run(rows, lock_trigger_r=1.0, lock_r=0.0, lock_weekend_only=True)
    assert run.trades == [] and run.open_position_at_end is not None


def test_a_weekend_lock_stays_after_the_reopen():
    sun = ms(2026, 11, 8, 22, 30)                 # 22:30 and 22:45 in the window, 23:00 out
    rows = [ENTER_LONG, (100.6, 100.9, 100.55, 100.8), (100.8, 100.85, 100.7, 100.8), BACK]
    run = _run(rows, t0=sun, lock_trigger_r=1.0, lock_r=0.0, lock_weekend_only=True)
    assert run.trades[0].exit_reason == "lock" and run.trades[0].exit_time == ms(2026, 11, 8, 23, 15)


# ── target-share trigger and break-even plus fees ──────────────────────────

def test_target_share_trigger_arms_at_that_share_of_the_way():
    p = _params(lock_trigger_target_frac=0.5, lock_r=0.0)
    assert profit_lock_stop("long", 100.0, 0.5, 101.99, p, target=104.0) is None
    assert profit_lock_stop("long", 100.0, 0.5, 102.0, p, target=104.0) == pytest.approx(100.0)
    assert profit_lock_stop("short", 100.0, 0.5, 98.0, p, target=96.0) == pytest.approx(100.0)
    assert profit_lock_stop("long", 100.0, 0.5, 103.0, p, target=None) is None
    p5 = _params(lock_trigger_target_frac=0.5, lock_r=0.5)
    assert profit_lock_stop("long", 100.0, 0.5, 102.0, p5, target=104.0) == pytest.approx(100.25)


def test_plus_fees_lock_covers_the_taker_round_trip():
    p = _params(lock_trigger_r=1.0, lock_r=0.0, lock_plus_fees=True)
    fee = 2 * p.taker_fee * 100.0
    assert profit_lock_stop("long", 100.0, 0.5, 100.5, p) == pytest.approx(100.0 + fee)
    assert profit_lock_stop("short", 100.0, 0.5, 99.5, p) == pytest.approx(100.0 - fee)


def test_r_trigger_lock_unchanged():
    p = _params(lock_trigger_r=1.5, lock_r=0.5, lock_trail=True)
    assert profit_lock_stop("long", 100.0, 0.5, 100.74, p) is None
    assert profit_lock_stop("long", 100.0, 0.5, 101.0, p) == pytest.approx(101.0 - 0.5)


def test_target_share_trigger_in_the_sim():
    # target 104, entry 100.3: half way is 102.15
    rows = [ENTER_LONG, (100.3, 102.2, 100.3, 102.1), (102.1, 102.15, 100.2, 100.25)]
    run = _run(rows, lock_trigger_target_frac=0.5, lock_r=0.0)
    assert run.trades[0].exit_reason == "lock" and run.trades[0].exit_px == pytest.approx(100.3)
