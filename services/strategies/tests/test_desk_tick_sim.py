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
    params = Params(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
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
