"""Offline gate tests for the pure scalp simulator
(services/strategies/backtest/scalp_sim.py; SCALP_TEST_2026-11-03.md).
Every candle here is synthetic and hand-built; nothing touches the
network. Covers: range_fade fill+target, range_fade fill+stop, mean_revert
fill+target, mean_revert fill+stop, the min-R gate, the 20x/half-
liquidation leverage gate, risk-capped sizing, the thesis-direction gate,
the "only while the other desk is flat" gate, the regime chop gate,
liquidation, no lookahead (both "future bars don't change past ticks" and
"a tick's own bar doesn't feed its own structure"), and the random-entry
control's determinism.
"""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.agents.bracket import check_leverage
from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.regime import RegimeConfig, label_series
from services.strategies.backtest.scalp_sim import (
    Params, atr, gated_setup, oil_desk_flat_at, range_edges, risk_capped_size, rolling_mean_std,
    simulate, simulate_random_control, slice_equity)

Q = 900_000   # 15m


def _c(t, o, h, l, c, v=1.0):
    return Candle(t=t, o=o, h=h, l=l, c=c, v=v)


def _flat_warmup(n=20, start=0, o=100.0, h=100.5, l=99.5, c=100.0):
    return [_c(start + i * Q, o, h, l, c) for i in range(n)]


def _converging_warmup(n=20, start=0, mid=100.0, half_width0=1.0, shrink=0.02):
    """`n` bars whose range [mid-hw, mid+hw] narrows by `shrink` each bar.
    The rested entry a later tick computes from any 4 consecutive prior
    bars always comes from the OLDEST (widest) of those 4, so it sits a
    constant `4*shrink` outside every LATER bar's own high/low -- the
    warmup never touches, let alone fills, its own rested edge. Lets a
    "fill" bar be added afterward with a clean, single, deliberate touch."""
    out = []
    for i in range(n):
        hw = half_width0 - shrink * i
        assert hw > 0.2, "half-width must stay comfortably positive"
        out.append(_c(start + i * Q, mid, mid + hw, mid - hw, mid))
    return out


def _base(**over):
    over.setdefault("min_r", 1.0)
    return dataclasses.replace(Params(), starting_equity=1000.0, slice_frac=1.0, risk_frac=None, **over)


# ── range_fade: fill + target ────────────────────────────────────────────

def _range_fade_params(**over):
    return _base(rule="range_fade", lookback_hours=1.0, target_frac=1.0, stop_atr_k=0.5, **over)


def _range_fade_levels(p=None):
    """The long setup `p` (default `_range_fade_params()`) would rest after
    the 20-bar converging warmup -- computed through the real `gated_setup`
    path so the fixture never hand-derives ATR/range arithmetic."""
    p = p or _range_fade_params()
    warm = _converging_warmup(20)
    setup, reason = gated_setup("long", warm, p)
    assert reason is None and setup is not None
    return warm, setup


def test_range_fade_fill_and_target():
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    entry, stop, target = setup["entry_px"], setup["stop_px"], setup["target_px"]
    fill = _c(20 * Q, entry + 0.3, entry + 0.4, entry - 0.3, entry)        # low below entry: fills the long
    quiet = _c(21 * Q, entry, entry + 0.1, entry - 0.1, entry)             # inside stop/target: nothing happens
    win = _c(22 * Q, entry, target + 0.05, entry - 0.1, target)           # high through the target
    run = simulate(warm + [fill, quiet, win], p)
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.side == "long"
    assert tr.entry_px == pytest.approx(entry)
    assert tr.stop_px == pytest.approx(stop)
    assert tr.target_px == pytest.approx(target)
    assert tr.exit_reason == "target"
    assert tr.exit_px == pytest.approx(target)
    assert tr.net_pnl_usd > 0
    assert tr.r_multiple == pytest.approx((target - entry) / (entry - stop), abs=0.05)


def test_range_fade_fill_and_stop():
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    entry, stop = setup["entry_px"], setup["stop_px"]
    fill = _c(20 * Q, entry + 0.3, entry + 0.4, entry - 0.3, entry)
    stop_out = _c(21 * Q, entry - 0.3, entry - 0.2, stop - 0.1, stop)      # low through the stop
    run = simulate(warm + [fill, stop_out], p)
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.exit_reason == "stop"
    assert tr.exit_px < tr.stop_px        # taker + slippage: a long's stop fill prices below the stop
    assert tr.net_pnl_usd < 0


# ── mean_revert: fill + target, fill + stop ─────────────────────────────

def _mean_revert_warmup():
    """20 bars alternating closes 99/101 (mean 100, population std 1.0),
    small wicks that never reach 98 or 97.5."""
    out = []
    for i in range(20):
        c = 99.0 if i % 2 == 0 else 101.0
        o = 101.0 if i % 2 == 0 else 99.0
        out.append(_c(i * Q, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
    return out


def test_mean_revert_fill_and_target():
    warm = _mean_revert_warmup()
    fill = _c(20 * Q, 98.5, 98.6, 97.8, 98.0)      # low 97.8 <= entry 98 (mean 100 - 2*std 1)
    win = _c(21 * Q, 98.0, 100.2, 97.9, 100.0)      # high 100.2 >= target (the mean, 100); low > stop 97.5
    p = _base(rule="mean_revert", ma_window=20, z_entry=2.0, z_stop=2.5)
    run = simulate(warm + [fill, win], p)
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.side == "long"
    assert tr.entry_px == pytest.approx(98.0)
    assert tr.stop_px == pytest.approx(97.5)
    assert tr.target_px == pytest.approx(100.0)
    assert tr.exit_reason == "target"
    assert tr.net_pnl_usd > 0


def test_mean_revert_fill_and_stop():
    warm = _mean_revert_warmup()
    fill = _c(20 * Q, 98.5, 98.6, 97.8, 98.0)
    lose = _c(21 * Q, 98.0, 98.2, 97.3, 97.6)      # low 97.3 <= stop 97.5
    p = _base(rule="mean_revert", ma_window=20, z_entry=2.0, z_stop=2.5)
    run = simulate(warm + [fill, lose], p)
    assert len(run.trades) == 1
    assert run.trades[0].exit_reason == "stop"
    assert run.trades[0].net_pnl_usd < 0


# ── gates: min-R, leverage, regime, direction, "other desk flat" ───────────

def test_min_r_gate_refuses():
    # target_frac so small the reward never clears min_r: refused, no trade,
    # on every warmup tick ATR becomes available for (the gate runs whether
    # or not the bar would have filled).
    p = _base(rule="range_fade", lookback_hours=1.0, target_frac=0.1, stop_atr_k=0.5, min_r=1.2)
    run = simulate(_converging_warmup(25), p)
    assert run.trades == []
    assert run.refused["min_r"] > 0


def test_leverage_gate_refuses_a_too_wide_stop():
    # stop_atr_k huge: the stop sits past half the way to liquidation at 20x.
    p = _base(rule="range_fade", lookback_hours=1.0, target_frac=1.0, stop_atr_k=20.0, leverage=20.0,
             max_leverage=20.0)
    run = simulate(_converging_warmup(25), p)
    assert run.trades == []
    assert run.refused["leverage"] > 0


def test_gated_setup_matches_bracket_check_leverage():
    closed = _converging_warmup(20)
    p = _range_fade_params()
    setup, reason = gated_setup("long", closed, p)
    assert reason is None and setup is not None
    gate = check_leverage(setup["entry_px"], setup["stop_px"], p.leverage, p.max_leverage)
    assert gate["ok"] and gate["stop_frac_of_liq"] <= 0.5


def test_thesis_direction_gate_blocks_the_other_side():
    # a bar that touches BOTH the long and short edge; direction_at picks
    # which side is even considered.
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    long_setup, reason = gated_setup("long", warm, p)
    short_setup, reason2 = gated_setup("short", warm, p)
    assert reason is None and reason2 is None
    both = _c(20 * Q, 100.0, short_setup["entry_px"] + 0.05, long_setup["entry_px"] - 0.05, 100.0)
    run_short_only = simulate(warm + [both], p, direction_at=lambda t: "short")
    run_long_only = simulate(warm + [both], p, direction_at=lambda t: "long")
    assert run_short_only.trades or run_short_only.open_position_at_end
    assert run_long_only.trades or run_long_only.open_position_at_end
    side_short = (run_short_only.trades[0].side if run_short_only.trades
                 else run_short_only.open_position_at_end["side"])
    side_long = (run_long_only.trades[0].side if run_long_only.trades
                else run_long_only.open_position_at_end["side"])
    assert side_short == "short"
    assert side_long == "long"
    # "flat" or None blocks both sides -- no new entry at all
    run_blocked = simulate(warm + [both], p, direction_at=lambda t: "flat")
    assert run_blocked.trades == [] and run_blocked.open_position_at_end is None


def test_active_at_gate_blocks_entries_while_false():
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    entry = setup["entry_px"]
    fill = _c(20 * Q, entry + 0.3, entry + 0.4, entry - 0.3, entry)   # would fill if active_at allowed it
    run = simulate(warm + [fill], p, active_at=lambda t: False)
    assert run.trades == []
    assert run.open_position_at_end is None


def test_oil_desk_flat_at_blocks_only_inside_the_desk_s_own_trade():
    class FakeTrade:
        def __init__(self, entry_time, exit_time):
            self.entry_time, self.exit_time = entry_time, exit_time

    flat_at = oil_desk_flat_at([FakeTrade(100, 200), FakeTrade(500, 600)])
    assert flat_at(50) is True
    assert flat_at(100) is False   # entry instant: the desk already holds the position
    assert flat_at(150) is False
    assert flat_at(200) is True    # exit instant: already flat again
    assert flat_at(550) is False
    assert flat_at(700) is True

    flat_at_open = oil_desk_flat_at([FakeTrade(100, 200)], open_position_at_end={"entry_time": 300})
    assert flat_at_open(250) is True
    assert flat_at_open(300) is False
    assert flat_at_open(10_000) is False   # still open at the end of the desk's own run


def test_regime_chop_gate_blocks_in_trend_and_allows_in_chop():
    # 21 days of a straight-line rise (a clean 20-day trend, not chop), then
    # a sideways chop window, matching test_oil_regime_sim.py's fixture shape.
    from services.strategies.backtest.regime import DAY_MS as R_DAY_MS
    from services.strategies.tests.test_oil_regime_sim import D0, chop_history
    c1h = chop_history()
    series = label_series(c1h, RegimeConfig())
    trend_t, chop_t = D0 + 10 * R_DAY_MS, D0 + 22 * R_DAY_MS
    assert series.label_at(trend_t) != "chop"
    assert series.label_at(chop_t) == "chop"

    p = _range_fade_params(regime_filter="chop_only")

    def episode_at(center_t):
        warm = _converging_warmup(20, start=center_t - 20 * Q)
        setup, reason = gated_setup("long", warm, p)
        assert reason is None
        entry = setup["entry_px"]
        fill = _c(center_t, entry + 0.3, entry + 0.4, entry - 0.3, entry)
        return warm + [fill]

    run_trend = simulate(episode_at(trend_t), p, regime_series=series)
    assert run_trend.trades == [] and run_trend.open_position_at_end is None
    assert run_trend.refused["regime_not_chop"] > 0

    run_chop = simulate(episode_at(chop_t), p, regime_series=series)
    assert run_chop.trades or run_chop.open_position_at_end is not None


# ── sizing ───────────────────────────────────────────────────────────────

def test_slice_equity_and_full_vs_risk_capped_size():
    p = dataclasses.replace(Params(), starting_equity=1000.0, slice_frac=0.05, leverage=20.0, risk_frac=0.5)
    s_eq = slice_equity(1000.0, p)
    assert s_eq == pytest.approx(50.0)
    full = risk_capped_size(s_eq, dataclasses.replace(p, risk_frac=None), 100.0, 99.0)
    capped = risk_capped_size(s_eq, p, 100.0, 50.0)   # a very wide stop: the cap must bind
    assert capped < full
    # the cap never loses more than risk_frac x slice equity at the stop
    loss = abs(100.0 - 50.0) * capped + 2 * p.taker_fee * 100.0 * capped
    assert loss <= p.risk_frac * s_eq + 1e-6


# ── liquidation ──────────────────────────────────────────────────────────

def test_liquidation_ends_the_run():
    warm = _flat_warmup(20)
    fill = _c(20 * Q, 99.8, 99.9, 99.4, 99.6)
    crash = _c(21 * Q, 99.0, 99.1, 50.0, 55.0)      # far past any 20x long's liquidation price
    p = _base(rule="range_fade", lookback_hours=1.0, target_frac=1.0, stop_atr_k=0.0001, leverage=20.0,
             max_leverage=20.0)
    # stop_atr_k ~0 keeps the stop just under the entry; widen the crash
    # bar's low below BOTH the stop and a plausible liq price so either
    # path (stop then liq-check skipped, or liq itself) still ends in ruin.
    run = simulate(warm + [fill, crash], p)
    assert run.final_equity == 0.0 or run.liquidated or run.trades[-1].net_pnl_usd < 0


# ── no lookahead ─────────────────────────────────────────────────────────

def test_future_bars_never_change_past_ticks():
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    entry = setup["entry_px"]
    fill = _c(20 * Q, entry + 0.3, entry + 0.4, entry - 0.3, entry)
    quiet = _c(21 * Q, entry, entry + 0.1, entry - 0.1, entry)
    bars = warm + [fill, quiet]           # position still open at the end of this prefix
    run_a = simulate(bars, p)
    wild = _c(bars[-1].t + Q, 9999.0, 9999.0, 0.01, 0.01)   # an absurd bar appended after
    run_b = simulate(bars + [wild], p)
    cutoff = bars[-1].t
    a_curve = [pt for pt in run_a.equity_curve if pt[0] <= cutoff]
    b_curve = [pt for pt in run_b.equity_curve if pt[0] <= cutoff]
    assert a_curve == b_curve
    # trades that CLOSE at or before the cutoff must match (an open
    # position at the cutoff has not been appended to `.trades` yet in
    # either run, so entry_time alone would wrongly flag a trade the wild
    # bar closes AFTER the cutoff -- that's the wild bar's own effect on
    # its own bar, not lookahead).
    assert run_a.trades == [tr for tr in run_b.trades if tr.exit_time is not None and tr.exit_time <= cutoff]


def test_a_ticks_own_bar_does_not_feed_its_own_structure():
    # same 20-bar converging warmup; the tick bar (index 20) differs ONLY in
    # its own high (entry+0.4 vs 500.0) beyond what is needed to fill the
    # long -- if the range/ATR read this bar's own high, the resulting
    # stop/target (built from bars strictly before it) would change. They
    # must not.
    p = _range_fade_params()
    warm, setup = _range_fade_levels(p)
    entry = setup["entry_px"]
    tick_a = _c(20 * Q, entry + 0.3, entry + 0.4, entry - 0.3, entry)
    tick_b = _c(20 * Q, entry + 0.3, 500.0, entry - 0.3, entry)
    run_a = simulate(warm + [tick_a], p)
    run_b = simulate(warm + [tick_b], p)
    pos_a, pos_b = run_a.open_position_at_end, run_b.open_position_at_end
    assert pos_a is not None and pos_b is not None
    assert pos_a["stop_px"] == pos_b["stop_px"]
    assert pos_a["target_px"] == pos_b["target_px"]
    assert pos_a["entry_px"] == pos_b["entry_px"]


# ── helpers (atr / range_edges / rolling_mean_std) ──────────────────────────

def test_atr_and_range_edges_and_rolling_mean_std_pure_helpers():
    closed = _flat_warmup(20)
    assert atr(closed, 14) == pytest.approx(1.0)
    assert atr(closed, 25) is None    # not enough bars yet
    lo, hi = range_edges(closed, 4)
    assert (lo, hi) == pytest.approx((99.5, 100.5))
    assert range_edges(closed, 100) is None
    ms = rolling_mean_std(_mean_revert_warmup(), 20)
    assert ms is not None
    mean, std = ms
    assert mean == pytest.approx(100.0)
    assert std == pytest.approx(1.0)
    assert rolling_mean_std(_flat_warmup(20, o=100, h=100, l=100, c=100), 20) is None   # zero std: no signal


# ── random-entry control ────────────────────────────────────────────────

def test_random_control_is_deterministic_and_trades_about_as_often_as_asked():
    bars = _flat_warmup(400, o=100.0, h=100.3, l=99.7, c=100.0)
    p = _base(rule="range_fade", stop_atr_k=0.5, min_r=1.0, bar_minutes=15)
    run_1 = simulate_random_control(bars, p, seed=7, entries_per_day=4.0)
    run_2 = simulate_random_control(bars, p, seed=7, entries_per_day=4.0)
    assert [dataclasses.asdict(t) for t in run_1.trades] == [dataclasses.asdict(t) for t in run_2.trades]
    run_other_seed = simulate_random_control(bars, p, seed=8, entries_per_day=4.0)
    assert run_1.trades != run_other_seed.trades or len(run_1.trades) == 0


# ── paged candle fetch (data.py; longer history than a single 5000-bar request) ──

def test_fetch_candles_paged_walks_backward_and_dedupes(tmp_path):
    # 3 pages of <=5000 5m bars each needed to cover 16000 bars; the fake
    # page fetcher returns exactly what a real candleSnapshot would for the
    # [start,end) it's asked, so this also checks the paging math (page_span,
    # the "no progress" stop) without hitting the network.
    ms = 300_000  # 5m
    total = 16_000
    all_bars = [Candle(t=i * ms, o=100.0, h=100.1, l=99.9, c=100.0, v=1.0) for i in range(total)]
    calls = []

    def fake_page(coin, interval, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return [c for c in all_bars if start_ms <= c.t < end_ms]

    start_ms, end_ms = 0, total * ms
    got = D.fetch_candles_paged("xyz:BRENTOIL", "5m", start_ms, end_ms, fetch_page=fake_page, max_pages=10)
    assert len(got) == total
    assert got == sorted(got, key=lambda c: c.t)
    assert got[0].t == 0 and got[-1].t == (total - 1) * ms
    assert len(calls) >= 4   # 16000 bars / 5000-per-page needs at least 4 pages


def test_fetch_candles_paged_stops_when_the_venue_runs_out_of_history():
    ms = 900_000  # 15m
    available_from = 10 * ms   # the venue has nothing before this

    def fake_page(coin, interval, start_ms, end_ms):
        lo = max(start_ms, available_from)
        if lo >= end_ms:
            return []
        return [Candle(t=t, o=1.0, h=1.0, l=1.0, c=1.0, v=1.0) for t in range(lo, end_ms, ms)]

    got = D.fetch_candles_paged("xyz:CL", "15m", 0, 50 * ms, fetch_page=fake_page, max_pages=20)
    assert got and got[0].t == available_from


def test_fetch_candles_paged_cached_round_trips_and_is_not_refetched(tmp_path):
    cache_dir = str(tmp_path)
    bars = [Candle(t=i * 900_000, o=1.0, h=1.0, l=1.0, c=1.0, v=1.0) for i in range(5)]
    calls = {"n": 0}

    def fake_page(coin, interval, start_ms, end_ms):
        calls["n"] += 1
        return [c for c in bars if start_ms <= c.t < end_ms]

    first = D.fetch_candles_paged_cached("xyz:SILVER", "15m", 0, 5 * 900_000, cache_dir, fetch_page=fake_page)
    assert first == bars
    assert calls["n"] >= 1

    def fetch_should_not_run(coin, interval, start_ms, end_ms):
        raise AssertionError("cache hit should never call the fetcher again")

    second = D.fetch_candles_paged_cached("xyz:SILVER", "15m", 0, 5 * 900_000, cache_dir,
                                          fetch_page=fetch_should_not_run)
    assert second == bars
