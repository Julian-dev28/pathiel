"""Gate tests for the profit lock (desk_tick_sim, Params.lock_*; 2026-11-01).

Operator, 2026-11-01: a Brent long from 111.48 (stop 111.02, target 113.66)
reached +1.9R (112.36) and drifted back to close near flat. The lock: once
the best price is TRIGGER R in favour, the stop moves to entry + LOCK R.

Synthetic candles only, no network. The Brent path below rebuilds that
trade's levels exactly: a 24h range of 111.40 to 113.66, a market entry at
111.48, a 0.38 stop buffer under the 24h low (stop 111.02, R 0.46) and the
24h high as the target (no offset).
"""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, _Position, _profit_lock, live_params, profit_lock_stop, simulate)

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000
T0 = 24 * HOUR_MS                       # first tick: 24 closed 1h bars behind it

ENTRY, STOP, TARGET = 111.48, 111.02, 113.66
R = ENTRY - STOP                        # 0.46


def _warmup(lo: float = 111.40, hi: float = 113.66, mid: float = 112.5):
    return [Candle(t=i * HOUR_MS, o=mid, h=hi, l=lo, c=mid, v=1.0) for i in range(24)]


def _bars(rows):
    return [Candle(t=T0 + i * FIFTEEN_MS, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _params(**over) -> Params:
    """The live rules (market entry off the 24h range, 2R gate, limit
    target), tuned so the setup lands on the 2026-11-01 trade's levels;
    no slippage so exit prices are exact."""
    base = dataclasses.replace(live_params(), stop_buffer=0.38, target_offset_frac=0.0,
                               slippage=0.0, starting_equity=64.0)
    return dataclasses.replace(base, **over)


def _long(t):
    return Direction(side="long")


def _short(t):
    return Direction(side="short")


def _run(rows, side="long", c1h=None, **over):
    return simulate(_bars(rows), c1h or _warmup(), _long if side == "long" else _short, _params(**over))


# The 2026-11-01 path: up to 112.36 (+1.91R), back down through the lock,
# then on to the original stop.
BRENT_PATH = [
    (111.48, 111.70, 111.40, 111.65),   # entry bar: market fill at the open, 111.48
    (111.65, 112.00, 111.60, 111.95),
    (111.95, 112.36, 111.90, 112.30),   # the peak: 112.36, +1.91R; 1.5R (112.17) reached, close above the lock
    (112.30, 112.32, 112.00, 112.05),
    (112.05, 112.10, 111.80, 111.85),   # back through 111.94 (entry + 1R)
    (111.85, 111.90, 111.30, 111.40),
    (111.40, 111.45, 110.90, 110.95),   # through the original stop, 111.02
]


def _mirror(rows, m=2 * ENTRY):
    """The same path for a short: every price p -> m - p (highs and lows swap)."""
    return [(m - o, m - l, m - h, m - c) for o, h, l, c in rows]


# ── defaults / golden ──────────────────────────────────────────────────────

def test_lock_is_off_by_default():
    p = Params()
    assert p.lock_trigger_r is None and p.lock_r == 0.0 and not p.lock_trail and p.lock_confirm_close
    assert live_params().lock_trigger_r is None


def test_off_is_byte_identical_and_on_changes_the_run():
    off = _run(BRENT_PATH)
    explicit = _run(BRENT_PATH, lock_trigger_r=None, lock_r=0.5, lock_trail=True)
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert off.equity_curve == explicit.equity_curve
    on = _run(BRENT_PATH, lock_trigger_r=1.5, lock_r=1.0)
    assert on.trades[0].exit_px != off.trades[0].exit_px


def test_lock_r_must_sit_below_the_trigger():
    with pytest.raises(ValueError):
        _run(BRENT_PATH, lock_trigger_r=1.0, lock_r=1.0)


# ── the trigger and the level ──────────────────────────────────────────────

def test_triggers_at_exactly_trigger_r_long():
    p = Params(lock_trigger_r=1.5, lock_r=1.0)
    assert profit_lock_stop("long", 100.0, 1.0, 101.49, p) is None
    assert profit_lock_stop("long", 100.0, 1.0, 101.50, p) == pytest.approx(101.0)
    assert profit_lock_stop("long", 100.0, 1.0, 105.00, p) == pytest.approx(101.0)   # fixed lock: no trail
    be = Params(lock_trigger_r=1.0, lock_r=0.0)
    assert profit_lock_stop("long", 100.0, 1.0, 101.0, be) == pytest.approx(100.0)   # break-even


def test_short_side_is_mirrored():
    p = Params(lock_trigger_r=1.5, lock_r=1.0)
    assert profit_lock_stop("short", 100.0, 1.0, 98.51, p) is None
    assert profit_lock_stop("short", 100.0, 1.0, 98.50, p) == pytest.approx(99.0)
    t = Params(lock_trigger_r=1.5, lock_r=1.0, lock_trail=True)
    assert profit_lock_stop("short", 100.0, 1.0, 97.0, t) == pytest.approx(97.5)


def test_trail_keeps_its_distance_and_never_drops_below_the_lock():
    p = Params(lock_trigger_r=1.5, lock_r=0.5, lock_trail=True)
    assert profit_lock_stop("long", 100.0, 1.0, 101.5, p) == pytest.approx(100.5)   # at the trigger: the lock
    assert profit_lock_stop("long", 100.0, 1.0, 103.0, p) == pytest.approx(102.0)   # 1R behind the best


def test_off_returns_no_stop():
    assert profit_lock_stop("long", 100.0, 1.0, 200.0, Params()) is None


# ── only tightens ──────────────────────────────────────────────────────────

def _pos(stop: float) -> _Position:
    return _Position(side="long", size=1.0, avg_entry=100.0, stop_px=stop, target_px=110.0,
                     opened_at=0, risk_usd=1.0, orig_stop_px=99.0)


def test_lock_never_widens_a_stop_already_tighter():
    pos = _pos(stop=101.5)                          # already above the 101.0 lock
    bar = Candle(t=0, o=100.0, h=103.0, l=100.0, c=102.0, v=1.0)
    assert _profit_lock(pos, bar, Params(lock_trigger_r=1.5, lock_r=1.0)) is False
    assert pos.stop_px == 101.5 and not pos.locked


def test_trail_stop_holds_when_price_falls_back():
    p = Params(lock_trigger_r=1.5, lock_r=0.5, lock_trail=True)
    pos = _pos(stop=99.0)
    assert _profit_lock(pos, Candle(t=0, o=100, h=103.0, l=100, c=102.5, v=1), p)
    assert pos.stop_px == pytest.approx(102.0)
    assert not _profit_lock(pos, Candle(t=1, o=102.5, h=102.6, l=102.1, c=102.2, v=1), p)
    assert pos.stop_px == pytest.approx(102.0)      # a lower high does not pull the stop down


# ── the 2026-11-01 Brent path ──────────────────────────────────────────────

def test_brent_setup_matches_the_live_trade():
    run = _run(BRENT_PATH)
    t = run.trades[0]
    assert (t.entry_px, t.stop_px, t.target_px) == pytest.approx((ENTRY, STOP, TARGET))


def test_brent_baseline_round_trips_to_the_stop():
    t = _run(BRENT_PATH).trades[0]
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(STOP)
    assert t.net_pnl_usd < 0


def test_brent_lock_1_5_1_0_exits_at_111_94():
    t = _run(BRENT_PATH, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "lock"
    assert t.exit_px == pytest.approx(ENTRY + R)    # 111.94
    assert t.exit_px == pytest.approx(111.94)
    assert t.exit_time == T0 + 4 * FIFTEEN_MS       # the bar that came back through it
    assert t.orig_stop_px == pytest.approx(STOP) and t.stop_moved
    assert t.net_pnl_usd > 0
    assert 0.7 < t.r_multiple < 1.0                 # 1R less two taker fees (about 0.22R)


def test_brent_break_even_lock_exits_at_entry():
    t = _run(BRENT_PATH, lock_trigger_r=1.0, lock_r=0.0).trades[0]
    assert t.exit_reason == "lock" and t.exit_px == pytest.approx(ENTRY)
    assert t.exit_time == T0 + 5 * FIFTEEN_MS


def test_brent_trail_exits_half_r_under_the_peak():
    t = _run(BRENT_PATH, lock_trigger_r=1.5, lock_r=1.0, lock_trail=True).trades[0]
    assert t.exit_reason == "lock"
    assert t.exit_px == pytest.approx(112.36 - 0.5 * R)    # 112.13
    assert t.exit_time == T0 + 3 * FIFTEEN_MS


def test_brent_2r_trigger_never_arms():
    t = _run(BRENT_PATH, lock_trigger_r=2.0, lock_r=1.0).trades[0]
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(STOP)   # peak was 1.91R


def test_short_brent_path_mirrored():
    m = 2 * ENTRY
    c1h = _warmup(lo=m - 113.66, hi=m - 111.40, mid=m - 112.5)
    rows = _mirror(BRENT_PATH)
    base = _run(rows, side="short", c1h=c1h).trades[0]
    assert base.side == "short" and base.stop_px == pytest.approx(m - STOP)
    assert base.exit_reason == "stop"
    t = _run(rows, side="short", c1h=c1h, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "lock" and t.exit_px == pytest.approx(ENTRY - R)   # 111.02
    assert t.net_pnl_usd > 0


# ── conservative intrabar order ────────────────────────────────────────────

def test_original_stop_is_judged_before_the_lock_in_the_same_bar():
    rows = [BRENT_PATH[0], (111.65, 112.20, 111.00, 111.70)]   # 1.5R and the stop in one bar
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(STOP)


def test_a_dip_under_the_lock_in_the_trigger_bar_does_not_fill_it():
    # trigger and a low under 111.94 in one bar, but the close is above the
    # lock: the low may have come before the high, so no fill this bar
    rows = [BRENT_PATH[0], (111.65, 112.20, 111.80, 112.10), (112.10, 112.15, 111.90, 112.00)]
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "lock" and t.exit_px == pytest.approx(111.94)
    assert t.exit_time == T0 + 2 * FIFTEEN_MS       # the NEXT bar, not the trigger bar


def test_a_close_through_the_lock_fills_it_in_the_trigger_bar():
    rows = [BRENT_PATH[0], (111.65, 112.20, 111.60, 111.70), (111.70, 111.75, 111.50, 111.60)]
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "lock" and t.exit_px == pytest.approx(111.94)
    assert t.exit_time == T0 + 1 * FIFTEEN_MS


def test_without_close_confirm_the_lock_fills_next_bar_at_the_open():
    rows = [BRENT_PATH[0], (111.65, 112.20, 111.60, 111.70), (111.70, 111.75, 111.50, 111.60)]
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0, lock_confirm_close=False).trades[0]
    assert t.exit_reason == "lock"
    assert t.exit_px == pytest.approx(111.70)       # the next bar opened under the lock: fills at the open
    assert t.exit_time == T0 + 2 * FIFTEEN_MS


def test_a_market_entry_bar_can_arm_the_lock():
    rows = [(111.48, 112.20, 111.40, 112.10), (112.10, 112.12, 111.90, 112.00)]
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "lock" and t.exit_px == pytest.approx(111.94)
    assert t.exit_time == T0 + FIFTEEN_MS


def test_target_still_fills_with_the_lock_on():
    rows = [BRENT_PATH[0], (111.65, 112.40, 111.60, 112.30), (112.30, 113.80, 112.20, 113.70)]
    t = _run(rows, lock_trigger_r=1.5, lock_r=1.0).trades[0]
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(TARGET)
