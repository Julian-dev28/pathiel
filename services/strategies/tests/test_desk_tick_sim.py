"""Offline gate tests for the pure desk-tick simulator
(services/strategies/backtest/desk_tick_sim.py). Every candle here is
synthetic and hand-constructed; nothing touches the network. Covers every
bullet in the plan's test row (docs/overhaul/BACKTEST_PLAN_2026-10-25.md
section 7): a pullback fills and stops, a target fills, the invalidation
close exits, the 1.5R gate refuses, the 20x rule refuses, funding and fees
are signed right, no lookahead — plus a liquidation-path test (the eval's
exit-1 check depends on `Run.liquidated` actually being reachable) and a
round-trip test for the backtest's disk cache (data.py).
"""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Direction, Params, simulate

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000


def _warmup_1h(start: int = 0, n: int = 24, o: float = 100.0, h: float = 101.0,
              l: float = 99.0, c: float = 100.0):
    return [Candle(t=start + i * HOUR_MS, o=o, h=h, l=l, c=c, v=1.0) for i in range(n)]


def _c15(t: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t=t, o=o, h=h, l=l, c=c, v=1.0)


def const_direction(side: str, invalidation_px=None, entry_ceiling=None):
    d = Direction(side=side, invalidation_px=invalidation_px, entry_ceiling=entry_ceiling)
    return lambda t: d


# ── 1: pullback fills, then stops ───────────────────────────────────────────

def test_pullback_fills_and_stops():
    c1h = _warmup_1h()                              # 8h/24h range [99,101], ATR14 = 2.0
    hour24 = 24 * HOUR_MS
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),   # mid near the 8h low (99) -> rest a limit there
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),   # low trades through 99 -> fills
        # low trades through the stop (98.75); open (98.3) is far enough from
        # the 8h edge (99) that the tick doesn't immediately rest a fresh
        # entry in the same bar it just stopped out of.
        _c15(hour24 + 2 * FIFTEEN_MS, 98.3, 98.9, 98.2, 98.3),
    ]
    # stop_buffer pinned: the default moved 0.25 -> 0.50 on 2026-10-25 and
    # this scenario's bars are built around a 98.75 stop.
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, stop_buffer=0.25)
    run = simulate(c15, c1h, const_direction("long"), params)

    assert run.rested == 1
    assert run.filled == 1
    assert len(run.trades) == 1
    t = run.trades[0]
    assert t.entry_px == pytest.approx(99.0)      # the limit price, not the bar's low
    assert t.stop_px == pytest.approx(98.75)       # edge (99) minus the stop buffer (0.25)
    assert t.exit_reason == "stop"
    assert t.exit_px == pytest.approx(98.75)       # no slippage in this run
    assert t.net_pnl_usd < 0
    assert not run.liquidated


# ── 2: a target fills ───────────────────────────────────────────────────────

def test_pullback_fills_and_hits_target():
    c1h = _warmup_1h()
    hour24 = 24 * HOUR_MS
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),   # fills at 99
        _c15(hour24 + 2 * FIFTEEN_MS, 99.5, 101.2, 99.4, 101.0),  # high trades through the target (101)
    ]
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    run = simulate(c15, c1h, const_direction("long"), params)

    assert run.filled == 1
    assert len(run.trades) == 1
    t = run.trades[0]
    assert t.target_px == pytest.approx(101.0)
    assert t.exit_reason == "target"
    assert t.exit_px == pytest.approx(101.0)       # the resting limit's own price, not the bar's high
    assert t.net_pnl_usd > 0


# ── 3: a 1h close through the invalidation exits ────────────────────────────

def test_invalidation_close_exits():
    c1h = list(_warmup_1h())
    hour24 = 24 * HOUR_MS
    hour25 = 25 * HOUR_MS
    # The hour24 1h candle is deliberately absent: this test isolates the
    # invalidation path, which reads the CLOSING 1h candle's own `c`, not
    # the 15m bars. hour25's candle is the one whose close triggers it.
    c1h.append(Candle(t=hour25, o=99.0, h=99.3, l=98.9, c=99.1, v=1.0))
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),   # fills at 99
        _c15(hour24 + 2 * FIFTEEN_MS, 99.0, 99.1, 98.9, 99.0),   # safe: no stop (98.75) or target (101)
        _c15(hour24 + 3 * FIFTEEN_MS, 99.0, 99.1, 98.95, 99.0),
        _c15(hour25 + 0 * FIFTEEN_MS, 99.0, 99.0, 99.0, 99.0),
        _c15(hour25 + 1 * FIFTEEN_MS, 99.0, 99.0, 99.0, 99.0),
        _c15(hour25 + 2 * FIFTEEN_MS, 99.0, 99.0, 99.0, 99.0),
        _c15(hour25 + 3 * FIFTEEN_MS, 99.0, 99.0, 99.0, 99.0),
        _c15(hour25 + HOUR_MS, 99.1, 99.1, 99.1, 99.1),         # tick coinciding with hour25's 1h close
    ]
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    run = simulate(c15, c1h, const_direction("long", invalidation_px=99.2), params)

    assert run.filled == 1
    assert len(run.trades) == 1
    t = run.trades[0]
    assert t.exit_reason == "invalidation"
    assert t.exit_px == pytest.approx(99.1)          # the closing 1h candle's close, not a 15m bar
    assert t.exit_time == hour25 + HOUR_MS


# ── 4/5: the gates refuse ───────────────────────────────────────────────────

def _flat_edge_1h():
    """Uniform 1h bars: 8h/24h low+high = 98.9/99.3, ATR14 = 0.4."""
    return _warmup_1h(o=99.1, h=99.3, l=98.9, c=99.1)


def test_min_r_gate_refuses():
    c1h = _flat_edge_1h()
    c15 = [_c15(24 * HOUR_MS, 98.9, 98.9, 98.9, 98.9)]   # mid sits right on the edge
    # stop_buffer=0.50 makes R = (99.3-98.9)/(98.9-98.4) = 0.8, under the 1.5 floor.
    params = Params(stop_buffer=0.50, leverage=20.0, max_leverage=20.0)
    run = simulate(c15, c1h, const_direction("long"), params)

    assert run.rested == 0
    assert run.filled == 0
    assert run.trades == []
    assert run.refused["min_r"] >= 1
    assert "leverage" not in run.refused


def test_leverage_gate_refuses():
    c1h = _flat_edge_1h()
    c15 = [_c15(24 * HOUR_MS, 98.9, 98.9, 98.9, 98.9)]
    # stop_buffer=5.0 at 20x: liquidation sits ~2.54 away from entry, so a
    # 5.0 stop uses ~197% of that distance -- the 20x rule's "at most half"
    # refuses it outright, before the R gate is even reached.
    params = Params(stop_buffer=5.0, leverage=20.0, max_leverage=20.0, min_r=0.01)
    run = simulate(c15, c1h, const_direction("long"), params)

    assert run.rested == 0
    assert run.trades == []
    assert run.refused["leverage"] >= 1
    assert "min_r" not in run.refused


# ── 6: fees are signed right (entry + exit, applied exactly once) ──────────

def test_fees_signed_right():
    c1h = _warmup_1h()
    hour24 = 24 * HOUR_MS
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),   # fills at 99 (maker)
        _c15(hour24 + 2 * FIFTEEN_MS, 98.8, 98.9, 98.5, 98.6),   # stops out (taker)
    ]
    params = Params(maker_fee=0.0002, taker_fee=0.0005, slippage=0.0, starting_equity=1000.0)
    run = simulate(c15, c1h, const_direction("long"), params)

    t = run.trades[0]
    expected_entry_fee = params.maker_fee * t.entry_px * t.size
    expected_exit_fee = params.taker_fee * t.exit_px * t.size
    assert t.fees_usd == pytest.approx(expected_entry_fee + expected_exit_fee)
    # Equity identity: starting + gross price move - total fees, exactly
    # once each -- the regression check for a fee (or funding) double-count.
    assert run.final_equity == pytest.approx(
        params.starting_equity + t.gross_pnl_usd - t.fees_usd, abs=1e-9)


# ── 7: funding is signed right, both sides ──────────────────────────────────

def test_funding_signed_long_pays_positive_rate():
    c1h = list(_warmup_1h())
    hour24 = 24 * HOUR_MS
    hour25 = 25 * HOUR_MS
    c1h.append(Candle(t=hour24, o=99.3, h=99.3, l=98.9, c=99.0, v=1.0))
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),    # fills at 99
        _c15(hour24 + 2 * FIFTEEN_MS, 99.0, 99.1, 98.9, 99.0),    # safe
        _c15(hour24 + 3 * FIFTEEN_MS, 99.0, 99.1, 98.95, 99.0),
        _c15(hour25 + 0 * FIFTEEN_MS, 99.0, 99.1, 98.9, 99.0),    # tick coincides with hour24's 1h close -> funding
        _c15(hour25 + 1 * FIFTEEN_MS, 99.5, 101.2, 99.4, 101.0),  # target fires here
    ]
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, starting_equity=1000.0)
    funding = [{"time": hour25, "fundingRate": 0.001}]
    run = simulate(c15, c1h, const_direction("long"), params, funding_history=funding)

    t = run.trades[0]
    expected_pay = 0.001 * 99.0 * t.size              # long pays positive funding
    assert t.funding_usd == pytest.approx(expected_pay)
    assert t.funding_usd > 0
    assert run.final_equity == pytest.approx(
        params.starting_equity + t.gross_pnl_usd - t.funding_usd, abs=1e-6)


def test_funding_signed_short_receives_positive_rate():
    c1h = list(_warmup_1h())                          # 8h/24h high=101, low=99
    hour24 = 24 * HOUR_MS
    hour25 = 25 * HOUR_MS
    c1h.append(Candle(t=hour24, o=100.7, h=101.1, l=100.6, c=100.8, v=1.0))
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 100.7, 100.7, 100.7, 100.7),  # mid near the 8h high (101)
        _c15(hour24 + 1 * FIFTEEN_MS, 100.8, 101.1, 100.6, 100.9),   # fills (short) at 101
        _c15(hour24 + 2 * FIFTEEN_MS, 100.8, 100.9, 100.7, 100.8),   # safe
        _c15(hour24 + 3 * FIFTEEN_MS, 100.8, 100.9, 100.7, 100.8),
        _c15(hour25 + 0 * FIFTEEN_MS, 100.8, 100.9, 100.7, 100.8),   # funding settles here
        _c15(hour25 + 1 * FIFTEEN_MS, 100.0, 100.1, 98.9, 99.0),     # target (99, the 24h low) fires
    ]
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, starting_equity=1000.0)
    funding = [{"time": hour25, "fundingRate": 0.001}]
    run = simulate(c15, c1h, const_direction("short"), params, funding_history=funding)

    t = run.trades[0]
    expected_pay = -0.001 * 100.8 * t.size            # short RECEIVES positive funding
    assert t.funding_usd == pytest.approx(expected_pay)
    assert t.funding_usd < 0
    assert run.final_equity == pytest.approx(
        params.starting_equity + t.gross_pnl_usd - t.funding_usd, abs=1e-6)


# ── 8: no lookahead ─────────────────────────────────────────────────────────

def test_no_lookahead():
    """The entry fill (decided at tick 1 of hour24) must not change when a
    bar strictly AFTER it (tick 2) is rewritten from a stop into a target —
    only the EXIT, which happens later, may differ."""
    c1h = _warmup_1h()
    hour24 = 24 * HOUR_MS
    base = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),
    ]
    stop_version = base + [_c15(hour24 + 2 * FIFTEEN_MS, 98.8, 98.9, 98.5, 98.6)]
    target_version = base + [_c15(hour24 + 2 * FIFTEEN_MS, 99.5, 101.2, 99.4, 101.0)]

    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    run_a = simulate(stop_version, c1h, const_direction("long"), params)
    run_b = simulate(target_version, c1h, const_direction("long"), params)

    assert run_a.trades[0].entry_time == run_b.trades[0].entry_time
    assert run_a.trades[0].entry_px == run_b.trades[0].entry_px
    assert run_a.trades[0].size == run_b.trades[0].size
    assert run_a.trades[0].exit_reason != run_b.trades[0].exit_reason   # the only thing allowed to differ


# ── hourly ticks (2026-10-25: "change the strategy to hourly") ──────────────

def _hourly_scenario():
    """Warm-up [99,101] then three 1h bars: rest at the 8h low, fill on the
    next bar, stop on the one after. The same shape as the 15m scenario
    above, one tick per hour."""
    c1h = _warmup_1h()
    hour24 = 24 * HOUR_MS
    c1h += [
        Candle(t=hour24 + 0 * HOUR_MS, o=99.3, h=99.3, l=99.3, c=99.3, v=1.0),   # mid near the 8h low -> rest at 99
        Candle(t=hour24 + 1 * HOUR_MS, o=99.2, h=99.3, l=98.9, c=99.0, v=1.0),   # low through 99 -> fills
        Candle(t=hour24 + 2 * HOUR_MS, o=98.3, h=98.9, l=98.2, c=98.3, v=1.0),   # low through the 98.75 stop
    ]
    return c1h


def test_hourly_ticks_run_on_the_1h_bars_and_ignore_the_15m_series():
    c1h = _hourly_scenario()
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, stop_buffer=0.25, tick_minutes=60)
    run = simulate([], c1h, const_direction("long"), params)          # no 15m bars at all
    assert len(run.equity_curve) == len(c1h)                          # one tick per 1h bar
    assert run.rested == 1 and run.filled == 1 and len(run.trades) == 1
    t = run.trades[0]
    assert t.entry_px == pytest.approx(99.0) and t.stop_px == pytest.approx(98.75)
    assert t.exit_reason == "stop"
    assert t.entry_time == 25 * HOUR_MS and t.exit_time == 26 * HOUR_MS   # fill and exit on later bars, never the rest bar
    # a 15m series, when given, changes nothing on the hourly clock
    hour24 = 24 * HOUR_MS
    noise15 = [_c15(hour24 + i * FIFTEEN_MS, 50.0, 50.0, 50.0, 50.0) for i in range(12)]
    run2 = simulate(noise15, c1h, const_direction("long"), params)
    assert [(x.entry_px, x.exit_px, x.exit_reason) for x in run2.trades] == \
        [(t.entry_px, t.exit_px, t.exit_reason)]


def test_hourly_ticks_keep_the_no_lookahead_property():
    c1h = _hourly_scenario()
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, stop_buffer=0.25, tick_minutes=60)
    stop_version = c1h
    target_version = c1h[:-1] + [Candle(t=26 * HOUR_MS, o=99.5, h=101.2, l=99.4, c=101.0, v=1.0)]
    run_a = simulate([], stop_version, const_direction("long"), params)
    run_b = simulate([], target_version, const_direction("long"), params)
    assert run_a.trades[0].entry_time == run_b.trades[0].entry_time
    assert run_a.trades[0].entry_px == run_b.trades[0].entry_px
    assert run_a.trades[0].exit_reason != run_b.trades[0].exit_reason


def test_default_clock_is_still_the_15m_tick():
    assert Params().tick_minutes == 15


# ── bonus: liquidation is reachable (the eval's exit-1 check depends on it) ─

def test_liquidation_ends_run_and_zeroes_equity():
    c1h = _warmup_1h()
    hour24 = 24 * HOUR_MS
    flip_tick = hour24 + 2 * FIFTEEN_MS
    c15 = [
        _c15(hour24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(hour24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),   # fills long at 99
        _c15(flip_tick, 50.0, 50.0, 50.0, 50.0),                 # a flash crash, same tick the call flips
    ]

    def d_at(t):
        return Direction(side="short") if t >= flip_tick else Direction(side="long")

    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0, starting_equity=1.0, leverage=20.0)
    run = simulate(c15, c1h, d_at, params)

    assert run.liquidated is True
    assert run.liquidation_time == flip_tick
    assert run.final_equity == 0.0


# ── data.py: the disk cache round-trips and never refetches on a hit ───────

def test_candle_cache_round_trips_and_is_not_refetched(tmp_path):
    cache_dir = str(tmp_path)
    original = [Candle(t=i * HOUR_MS, o=100.0 + i, h=101.0 + i, l=99.0 + i, c=100.5 + i, v=1.0)
               for i in range(5)]
    calls = {"n": 0}

    def fetch_once(coin, interval, count):
        calls["n"] += 1
        return original

    first = D.fetch_candles_cached("xyz:BRENTOIL", "1h", 5, cache_dir, fetch=fetch_once)
    assert first == original
    assert calls["n"] == 1

    def fetch_should_not_run(coin, interval, count):
        raise AssertionError("cache hit should never call the fetcher again")

    second = D.fetch_candles_cached("xyz:BRENTOIL", "1h", 5, cache_dir, fetch=fetch_should_not_run)
    assert second == original
    assert calls["n"] == 1


def test_parse_invalidation_px():
    assert D.parse_invalidation_px("1h close below 110.80 (under the 8h low 110.97).") == 110.80
    assert D.parse_invalidation_px("Daily close below 94.30 (the 10x stop level).") == 94.30
    assert D.parse_invalidation_px("Brent closes above $4,028.50 or we're wrong.") == 4028.50
    assert D.parse_invalidation_px("no numbers here") is None


# ── moving the stop (2026-10-28): breakeven and trailing variants ────────────
#
# One scenario for every variant: long rests at the 8h low 99, fills on bar 1
# (stop 98.75, so 1R = 0.25), bar 2 trades 0.30 in favour (> 1R, < 2R) and
# dips only to 99.00, bar 3 dips to 98.95. A fixed stop survives bar 3; a
# breakeven stop at 99.00 is hit on bar 3, never on bar 2 (the move reads the
# finished bar and applies from the next one).

def _stop_scenario(bar3_open: float = 99.0):
    c1h = _warmup_1h()                              # range [99,101], ATR14 = 2.0
    h24 = 24 * HOUR_MS
    c15 = [
        _c15(h24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),        # rest at 99
        _c15(h24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),        # fills at 99
        _c15(h24 + 2 * FIFTEEN_MS, 99.1, 99.30, 99.0, 99.2),       # +0.30 = 1.2R, low 99.00
        _c15(h24 + 3 * FIFTEEN_MS, bar3_open, 99.15, 98.95, 99.05),  # dips to 98.95
    ]
    return c15, c1h


def _p(**kw):
    base = dict(taker_fee=0.0, maker_fee=0.0, slippage=0.0, stop_buffer=0.25)
    return Params(**{**base, **kw})


def test_fixed_stop_never_moves():
    c15, c1h = _stop_scenario()
    run = simulate(c15, c1h, const_direction("long"), _p())
    assert run.trades == [] and run.open_position_at_end is not None


def test_breakeven_after_1r_stops_out_flat_on_the_next_bar_only():
    c15, c1h = _stop_scenario()
    run = simulate(c15, c1h, const_direction("long"), _p(stop_mode="breakeven", stop_trigger_r=1.0))
    assert len(run.trades) == 1
    t = run.trades[0]
    assert t.exit_reason == "stop" and t.exit_time == c15[3].t      # bar 3, not bar 2 (low 99.00)
    assert t.stop_moved and t.orig_stop_px == pytest.approx(98.75)
    assert t.stop_px == pytest.approx(99.0) and t.exit_px == pytest.approx(99.0)
    assert t.net_pnl_usd == pytest.approx(0.0, abs=1e-9)


def test_breakeven_at_2r_does_not_arm_on_a_1_2r_move():
    c15, c1h = _stop_scenario()
    run = simulate(c15, c1h, const_direction("long"), _p(stop_mode="breakeven", stop_trigger_r=2.0))
    assert run.trades == []


def test_breakeven_covers_the_round_trip_fee():
    c15, c1h = _stop_scenario(bar3_open=99.1)        # opens above the stop: fills at the stop
    run = simulate(c15, c1h, const_direction("long"),
                   _p(stop_mode="breakeven", maker_fee=0.00015, taker_fee=0.00045))
    t = run.trades[0]
    assert t.stop_px == pytest.approx(99.0 * 1.0006)
    assert abs(t.net_pnl_usd) < 0.001 * t.size        # nets about zero after both fees


def test_trail_tightens_and_a_gap_through_fills_at_the_open():
    c15, c1h = _stop_scenario()
    # best close 99.2 - 0.05 x ATR 2.0 = 99.10; bar 3 opens at 99.00, under it
    run = simulate(c15, c1h, const_direction("long"), _p(stop_mode="trail", trail_atr=0.05))
    t = run.trades[0]
    assert t.stop_px == pytest.approx(99.1) and t.stop_moved
    assert t.exit_px == pytest.approx(99.0)          # the open, not the 99.10 stop


def test_a_trail_wider_than_the_stop_never_widens_it():
    c15, c1h = _stop_scenario()
    # 99.2 - 2 x 2.0 = 95.2, far under 98.75: the stop stays where it was
    run = simulate(c15, c1h, const_direction("long"), _p(stop_mode="trail", trail_atr=2.0))
    assert run.trades == [] and run.open_position_at_end is not None


def test_breakeven_on_a_short_mirrors_the_long():
    c1h = _warmup_1h()
    h24 = 24 * HOUR_MS
    c15 = [
        _c15(h24 + 0 * FIFTEEN_MS, 100.7, 100.7, 100.7, 100.7),     # rest a short at the 8h high 101
        _c15(h24 + 1 * FIFTEEN_MS, 100.8, 101.1, 100.7, 101.0),     # fills at 101, stop 101.25
        _c15(h24 + 2 * FIFTEEN_MS, 100.9, 101.0, 100.70, 100.8),    # -0.30 in favour, high 101.00
        _c15(h24 + 3 * FIFTEEN_MS, 101.0, 101.05, 100.85, 100.95),  # rises to 101.05
    ]
    run = simulate(c15, c1h, const_direction("short"), _p(stop_mode="breakeven"))
    t = run.trades[0]
    assert t.side == "short" and t.exit_time == c15[3].t and t.exit_px == pytest.approx(101.0)


def test_an_unknown_stop_mode_is_an_error_not_a_silent_fixed_stop():
    c15, c1h = _stop_scenario()
    with pytest.raises(ValueError):
        simulate(c15, c1h, const_direction("long"), _p(stop_mode="moon"))


# ── target execution: touch (default) vs market vs limit (2026-10-28) ───────
# Operator: "actually test it first before u do that" about moving the live
# target from a Take Profit Market trigger to a resting limit. Same pullback
# as test_pullback_fills_and_hits_target: rest at 99, fill, target 101.

def _target_scenario(bar2_high: float, bar3=None):
    c1h = _warmup_1h()
    h24 = 24 * HOUR_MS
    c15 = [
        _c15(h24 + 0 * FIFTEEN_MS, 99.3, 99.3, 99.3, 99.3),
        _c15(h24 + 1 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0),        # fills at 99
        _c15(h24 + 2 * FIFTEEN_MS, 99.5, bar2_high, 99.4, 100.8),
    ]
    if bar3:
        c15.append(_c15(h24 + 3 * FIFTEEN_MS, *bar3))
    return c15, c1h


def test_a_limit_target_fills_on_a_trade_through_at_its_own_price_and_maker_fee():
    c15, c1h = _target_scenario(bar2_high=101.01)               # one tick through 101
    run = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit", maker_fee=0.00015))
    t = run.trades[0]
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(101.0)
    assert t.exit_fee_usd == pytest.approx(0.00015 * 101.0 * t.size)
    assert t.target_missed is False


def test_a_bare_touch_does_not_fill_a_limit_target_but_does_fill_the_default():
    c15, c1h = _target_scenario(bar2_high=101.0)                # touches 101, no trade-through
    limit = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit"))
    assert limit.trades == [] and limit.open_position_at_end is not None
    touch = simulate(c15, c1h, const_direction("long"), _p())    # the default is unchanged
    assert touch.trades[0].exit_reason == "target"


def test_a_touch_that_reverses_to_the_stop_takes_the_stop_and_counts_as_missed():
    c15, c1h = _target_scenario(bar2_high=101.0, bar3=(100.0, 100.1, 98.5, 98.6))  # back through 98.75
    run = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit"))
    t = run.trades[0]
    assert t.exit_reason == "stop" and t.target_missed is True


def test_a_market_target_pays_the_taker_fee_and_the_exit_slip():
    c15, c1h = _target_scenario(bar2_high=101.0)
    run = simulate(c15, c1h, const_direction("long"),
                   _p(target_mode="market", taker_fee=0.00045, slippage=0.0003))
    t = run.trades[0]
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(101.0 * (1 - 0.0003))
    assert t.exit_fee_usd == pytest.approx(0.00045 * t.exit_px * t.size)


def test_an_unknown_target_mode_is_an_error():
    c15, c1h = _target_scenario(bar2_high=101.01)
    with pytest.raises(ValueError, match="target_mode"):
        simulate(c15, c1h, const_direction("long"), _p(target_mode="moon"))


def test_the_exit_slip_curve_is_the_measured_book():
    from services.strategies.backtest.desk_tick_sim import exit_slip_bps, _market_exit_slip
    assert exit_slip_bps(0) == pytest.approx(0.9)
    assert exit_slip_bps(1_000_000) == pytest.approx(3.8)
    assert exit_slip_bps(750_000) == pytest.approx(3.3)            # linear between points
    assert exit_slip_bps(8_000_000) == pytest.approx(34.1 + 7.9)   # last slope (7.9 bps per $1M) extended
    # off: the flat slippage; on: the larger of it and the curve
    assert _market_exit_slip(Params(slippage=0.0003), 5_000_000) == pytest.approx(0.0003)
    assert _market_exit_slip(Params(slippage=0.0003, size_slippage=True), 100_000) == pytest.approx(0.0003)
    assert _market_exit_slip(Params(slippage=0.0003, size_slippage=True), 5_000_000) == pytest.approx(0.00217)


def test_size_slippage_makes_a_big_stop_fill_worse_and_a_limit_target_pays_none():
    c15, c1h = _target_scenario(bar2_high=100.5, bar3=(100.0, 100.1, 98.5, 98.6))  # stops out
    small = simulate(c15, c1h, const_direction("long"), _p(size_slippage=True, slippage=0.0))
    big = simulate(c15, c1h, const_direction("long"),
                   _p(size_slippage=True, slippage=0.0, starting_equity=500_000.0))
    assert big.trades[0].exit_px < small.trades[0].exit_px      # $10M notional slips far more
    c15w, c1hw = _target_scenario(bar2_high=101.01)
    lim = simulate(c15w, c1hw, const_direction("long"),
                   _p(target_mode="limit", size_slippage=True, starting_equity=500_000.0))
    assert lim.trades[0].exit_px == pytest.approx(101.0)


# ── closer targets and re-entry (2026-10-29) ───────────────────────────────
# Operator: "test .3% lower targets and then reentry". Same pullback: rest at
# 99, fill, structural target 101, stop 98.75.

def test_offset_target_moves_toward_the_entry_on_both_sides():
    from services.strategies.backtest.desk_tick_sim import offset_target
    assert offset_target(101.0, "long", 0.003) == pytest.approx(100.697)
    assert offset_target(99.0, "short", 0.003) == pytest.approx(99.297)
    assert offset_target(101.0, "long", 0.0) == 101.0


def test_a_closer_target_fills_a_move_that_stops_short_of_the_structural_one():
    c15, c1h = _target_scenario(bar2_high=100.8)                # never reaches 101
    base = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit"))
    assert base.trades == []                                     # still open at 101
    closer = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit", target_offset_frac=0.003))
    t = closer.trades[0]
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(100.697)
    assert t.orig_target_px == pytest.approx(101.0) and t.offset_capture is True


def test_a_short_target_offsets_upward():
    c1h = _warmup_1h()                              # 8h high 101: a short rests there
    h24 = 24 * HOUR_MS
    c15 = [
        _c15(h24 + 0 * FIFTEEN_MS, 100.7, 100.7, 100.7, 100.7),
        _c15(h24 + 1 * FIFTEEN_MS, 100.8, 101.1, 100.7, 101.0),     # fills at 101
        _c15(h24 + 2 * FIFTEEN_MS, 100.5, 100.6, 99.2, 99.4),        # low 99.2: short of 99, through 99.297
    ]
    run = simulate(c15, c1h, const_direction("short"), _p(target_mode="limit", target_offset_frac=0.003))
    t = run.trades[0]
    assert t.side == "short" and t.exit_reason == "target" and t.exit_px == pytest.approx(99.297)


def _reentry_scenario():
    c15, c1h = _target_scenario(bar2_high=101.2)                # target trades through on bar 2
    c15.append(_c15(24 * HOUR_MS + 3 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0))   # back to the 99 edge
    return c15, c1h


def test_the_default_re_enters_under_the_same_call_after_a_target():
    c15, c1h = _reentry_scenario()
    run = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit"))
    assert run.trades[0].exit_reason == "target"
    assert run.rested == 2 and run.filled == 2                  # a second entry under the same call


def test_no_re_entry_waits_for_the_next_call():
    c15, c1h = _reentry_scenario()
    run = simulate(c15, c1h, const_direction("long"), _p(target_mode="limit", reentry_after_target=False))
    assert run.rested == 1 and run.filled == 1 and run.open_position_at_end is None
    # a new call (a fresh regime_id) at bar 2 lifts the block
    def two_calls(t):
        return Direction(side="long", regime_id="a" if t < 24 * HOUR_MS + 2 * FIFTEEN_MS + 1 else "b")
    c15b = c15 + [_c15(24 * HOUR_MS + 4 * FIFTEEN_MS, 99.2, 99.3, 98.9, 99.0)]
    run2 = simulate(c15b, c1h, two_calls, _p(target_mode="limit", reentry_after_target=False))
    assert run2.rested == 2
