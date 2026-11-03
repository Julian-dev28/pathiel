"""Gate tests for the funding-extreme filter (services/strategies/backtest/
funding_signal.py and desk_tick_sim.Params.funding_z_filter,
FUNDING_EXTREME_TEST_2026-11-03.md). Synthetic hourly funding rows and
flat candles only -- no network, under 2s.

Covers: warm-up (fewer than 30 days of history emits nothing), a known
spike scores a large z of the right sign, a literally-constant rate is
zero-variance and emits nothing (not a floating-point-noise explosion),
causality (extending the series never changes an already-emitted z),
`funding_z_at`'s lookup, `funding_filter_blocks`'s truth table, and the
sim-level hook: off by default (byte-identical golden check) and, when
set, it refuses the crowded side's new entry and only that side.
"""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction, FUNDING_FILTER_NAME, live_params, simulate
from services.strategies.backtest.funding_signal import (
    DEFAULT_DIST_DAYS, DEFAULT_LOOKBACK_H, funding_filter_blocks, funding_z_at, funding_zscore_series)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
Q = 900_000


def _hourly(n_hours: int, rate) -> list:
    """`n_hours` synthetic funding rows at `rate` (a float, or a callable of
    the hour index for a non-constant series), one per hour from t=0."""
    return [(i * HOUR_MS, rate(i) if callable(rate) else rate) for i in range(n_hours)]


def _spiked(n_days: int, base: float, spike: float, spike_hours: int = 24):
    """`n_days` of hourly funding at `base`, the LAST `spike_hours` at
    `spike` instead (the crowded-funding event near the series' end)."""
    rows = _hourly(n_days * 24, base)
    for i in range(len(rows) - spike_hours, len(rows)):
        rows[i] = (rows[i][0], spike)
    return rows


# ── funding_zscore_series (pure) ────────────────────────────────────────────

def test_warms_up_before_dist_days():
    """Under 30 days of history: not enough distribution to score, nothing
    comes out even once lookback_h rows exist."""
    rows = _hourly(10 * 24, 0.0001)
    assert funding_zscore_series(rows) == {}


def test_warms_up_right_at_dist_days():
    """Just over 30 days: the tail end (after both windows fill) scores."""
    rows = _hourly(DEFAULT_DIST_DAYS * 24 + DEFAULT_LOOKBACK_H, lambda i: 0.0001 + 0.0000001 * (i % 7))
    z = funding_zscore_series(rows)
    assert len(z) > 0
    assert all(t >= rows[DEFAULT_DIST_DAYS * 24][0] - HOUR_MS for t in z)   # no z before warm-up


def test_positive_spike_scores_a_large_positive_z():
    """A 24h funding spike 80x the 31-day baseline: the trailing-24h mean at
    the spike's end is far above its own 30-day distribution. z >= 2.0 (D4's
    own gate) comfortably."""
    rows = _spiked(31, base=0.00001, spike=0.0008)
    z = funding_zscore_series(rows)
    last_t = rows[-1][0]
    assert z[last_t] >= 2.0
    assert z[last_t] > 5.0   # it is not a close call


def test_negative_spike_scores_a_large_negative_z():
    rows = _spiked(31, base=0.00001, spike=-0.0008)
    z = funding_zscore_series(rows)
    last_t = rows[-1][0]
    assert z[last_t] <= -2.0


def test_constant_rate_is_zero_variance_and_emits_nothing():
    """A literally flat funding rate the whole window: no real distribution
    to score against. Must not explode into a huge z from floating-point
    noise around an exact-zero variance."""
    rows = _hourly(31 * 24, 0.00002)
    assert funding_zscore_series(rows) == {}


def test_causal_extending_the_series_never_changes_an_earlier_z():
    rows = _spiked(31, base=0.00001, spike=0.0003)
    z_before = funding_zscore_series(rows)
    extended = rows + _hourly(48, lambda i: 0.00001)
    extended = [(rows[-1][0] + (i + 1) * HOUR_MS, r) for i, (_, r) in enumerate(extended[len(rows):])]
    full = rows + extended
    z_after = funding_zscore_series(full)
    common = [t for t in z_before if t in z_after]
    assert len(common) == len(z_before)
    for t in common:
        assert z_after[t] == pytest.approx(z_before[t])


# ── funding_z_at (pure) ──────────────────────────────────────────────────────

def test_funding_z_at_lookup():
    times = [10, 20, 30]
    zs = [1.0, 2.0, 3.0]
    assert funding_z_at(times, zs, 5) is None
    assert funding_z_at(times, zs, 10) == 1.0
    assert funding_z_at(times, zs, 25) == 2.0
    assert funding_z_at(times, zs, 999) == 3.0
    assert funding_z_at([], [], 10) is None


# ── funding_filter_blocks (pure) ────────────────────────────────────────────

@pytest.mark.parametrize("z, side, threshold, expected", [
    (None, "long", 2.0, False),
    (2.0, "long", 2.0, True),
    (1.999, "long", 2.0, False),
    (3.5, "long", 2.0, True),
    (-2.0, "short", 2.0, True),
    (-1.999, "short", 2.0, False),
    (-5.0, "short", 2.0, True),
    (5.0, "short", 2.0, False),     # a crowded long never blocks a new short
    (-5.0, "long", 2.0, False),     # a crowded short never blocks a new long
    (5.0, "flat", 2.0, False),      # not long or short: never blocks
])
def test_funding_filter_blocks_table(z, side, threshold, expected):
    assert funding_filter_blocks(z, side, threshold) is expected


# ── the sim hook (desk_tick_sim.Params.funding_z_filter) ───────────────────

def _flat_bars(n15: int = 20):
    """24 flat 1h bars, then 15m bars that never reach a sane stop/target --
    isolates the entry decision from the exit mechanics."""
    t0 = 40 * DAY_MS   # plenty of room before t=0 for the funding history below
    c1h = [Candle(t=t0 - (24 - i) * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(24)]
    c15 = [Candle(t=t0 + i * Q, o=102.3, h=102.4, l=102.2, c=102.3, v=1.0) for i in range(n15)]
    return c15, c1h, t0


def _base(**over):
    return dataclasses.replace(live_params(), starting_equity=100.0, **over)


def test_golden_funding_filter_off_changes_nothing():
    """funding_z_filter=None (the default): a run with an extreme funding
    history attached is byte-identical to the same run with none at all --
    the hook never even looks at funding_history unless the field is set."""
    c15, c1h, t0 = _flat_bars()
    d = lambda t: Direction(side="long", regime_id=str(t0))   # noqa: E731
    spiked = _spiked(31, base=0.00001, spike=0.0008)
    funding = [{"time": t, "fundingRate": r, "premium": 0.0} for t, r in spiked]

    def key(run):
        return ([dataclasses.asdict(tr) for tr in run.trades], run.equity_curve, run.final_equity,
                dict(run.refused))

    no_funding = simulate(c15, c1h, d, _base(), None)
    with_funding_off = simulate(c15, c1h, d, _base(), funding)
    assert key(with_funding_off) == key(no_funding)
    explicit_none = simulate(c15, c1h, d, _base(funding_z_filter=None), funding)
    assert key(explicit_none) == key(no_funding)


def test_filter_refuses_a_new_long_during_a_crowded_long_spike():
    """31 days of baseline funding, a crowded-long spike landing exactly on
    the entry window: a long is refused (FUNDING_FILTER_NAME in run.refused)
    and no long opens while z stays over the threshold."""
    c15, c1h, t0 = _flat_bars()
    spiked = _spiked(31, base=0.00001, spike=0.0008)
    funding = [{"time": t, "fundingRate": r, "premium": 0.0} for t, r in spiked]
    d = lambda t: Direction(side="long", regime_id=str(t0))   # noqa: E731

    run = simulate(c15, c1h, d, _base(funding_z_filter=2.0), funding)
    assert run.refused[FUNDING_FILTER_NAME] > 0
    assert not any(tr.side == "long" for tr in run.trades)

    # Same bars, no spike (flat funding throughout): the filter has nothing
    # to block and the long opens as normal.
    flat_funding = [{"time": t, "fundingRate": 0.00001, "premium": 0.0} for t, _ in spiked]
    run_flat = simulate(c15, c1h, d, _base(funding_z_filter=2.0), flat_funding)
    assert run_flat.refused[FUNDING_FILTER_NAME] == 0


def test_filter_refuses_a_new_short_during_a_crowded_short_spike_only():
    """The negative-z spike blocks a SHORT, never a LONG at the same
    timestamp -- the filter is side-specific."""
    c15, c1h, t0 = _flat_bars()
    spiked = _spiked(31, base=0.00001, spike=-0.0008)
    funding = [{"time": t, "fundingRate": r, "premium": 0.0} for t, r in spiked]
    short_d = lambda t: Direction(side="short", regime_id=str(t0))   # noqa: E731
    long_d = lambda t: Direction(side="long", regime_id=str(t0))     # noqa: E731

    run_short = simulate(c15, c1h, short_d, _base(funding_z_filter=2.0), funding)
    assert run_short.refused[FUNDING_FILTER_NAME] > 0
    assert not any(tr.side == "short" for tr in run_short.trades)

    run_long = simulate(c15, c1h, long_d, _base(funding_z_filter=2.0), funding)
    assert run_long.refused[FUNDING_FILTER_NAME] == 0
