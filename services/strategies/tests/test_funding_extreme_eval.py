"""Gate tests for the funding-extreme eval's pure parts
(services/strategies/evals/funding_extreme_test.py,
FUNDING_EXTREME_TEST_2026-11-03.md): the event/forward-return math, the
matched null and its p-value, the EVIDENCE_DOCTRINE.md verdict gates, the
filter-override grid, and the standalone direction factory. Synthetic
data only, no network, under 2s.
"""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction
from services.strategies.evals import funding_extreme_test as T

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def _candles(n_days: int, start_px: float, drift_per_day: float) -> list:
    """`n_days` x 24 hourly candles, one flat price per calendar day
    (`start_px + drift_per_day * day_index`) -- a step function, so a
    forward return queried safely inside a day (not exactly on a day
    boundary) is exact arithmetic with no close-time rounding surprises."""
    out = []
    for i in range(n_days * 24):
        day = i // 24
        px = start_px + drift_per_day * day
        out.append(Candle(t=i * HOUR_MS, o=px, h=px, l=px, c=px, v=1.0))
    return out


# ── events ───────────────────────────────────────────────────────────────

def test_collapse_events_counts_one_per_spike_not_per_hour():
    z = {0: 0.5, HOUR_MS: 2.1, 2 * HOUR_MS: 2.4, 3 * HOUR_MS: 0.1, 4 * HOUR_MS: 2.2}
    assert T.collapse_events(z, 2.0, "pos") == [HOUR_MS, 4 * HOUR_MS]
    assert T.collapse_events(z, 2.0, "neg") == []


def test_collapse_events_negative_side():
    z = {0: -0.1, HOUR_MS: -2.5, 2 * HOUR_MS: -2.1, 3 * HOUR_MS: 0.0}
    assert T.collapse_events(z, 2.0, "neg") == [HOUR_MS]


# ── forward returns ────────────────────────────────────────────────────

def test_close_series_is_sorted_by_close_time():
    c1h = [Candle(t=HOUR_MS, o=1, h=1, l=1, c=20, v=1), Candle(t=0, o=1, h=1, l=1, c=10, v=1)]
    times, closes = T.close_series(c1h)
    assert times == [HOUR_MS, 2 * HOUR_MS] and closes == [10, 20]


def test_price_at_or_after_and_forward_return_exact():
    """Queried from the MIDDLE of a day (not its exact boundary, where the
    last hour's close still belongs to the day before it): day0 (100) to
    day5 (110) is +10%."""
    c1h = _candles(10, 100.0, drift_per_day=2.0)
    times, closes = T.close_series(c1h)
    mid_day = 12 * HOUR_MS
    r = T.forward_return(times, closes, mid_day, 5)
    assert r == pytest.approx(0.10, abs=1e-9)


def test_forward_return_none_past_the_data_end():
    c1h = _candles(3, 100.0, 1.0)
    times, closes = T.close_series(c1h)
    assert T.forward_return(times, closes, 0, 30) is None


def test_side_pnl_short_inverts_long_does_not():
    assert T.side_pnl(0.05, "short") == -0.05
    assert T.side_pnl(0.05, "long") == 0.05


def test_usable_events_drops_events_too_close_to_the_data_end():
    events = [0, 5 * DAY_MS, 9 * DAY_MS]
    out = T.usable_events(events, candle_start=0, candle_end=10 * DAY_MS, max_hold_days=5)
    assert out == [0, 5 * DAY_MS]       # 9d + 5d hold > 10d end: dropped


# ── event_study ────────────────────────────────────────────────────────

def test_event_study_known_values_and_halves():
    """Four SHORT events, each fired mid-day (so entry/exit never straddle
    a day boundary) one day apart on a +2/day price step: a rising price
    always loses a short before fees, in the exact amount `2/(100+2d)`."""
    c1h = _candles(40, 100.0, drift_per_day=2.0)
    times, closes = T.close_series(c1h)
    mid_day = 12 * HOUR_MS
    days = (0, 5, 10, 15)
    events = [mid_day + d * DAY_MS for d in days]
    st = T.event_study(events, times, closes, "short", hold_days=1, fee_frac=0.0025)
    assert st["n"] == 4 and st["dropped"] == 0
    expect_gross = [-2.0 / (100.0 + 2.0 * d) for d in days]
    assert st["mean_gross"] == pytest.approx(sum(expect_gross) / 4, abs=1e-6)
    assert st["mean_net"] == pytest.approx(sum(expect_gross) / 4 - 0.0025, abs=1e-6)
    assert st["win_rate"] == 0.0
    assert st["half1_n"] == 2 and st["half2_n"] == 2
    assert st["both_halves_positive"] is False


def test_event_study_empty_when_no_events_resolve():
    c1h = _candles(3, 100.0, 1.0)
    times, closes = T.close_series(c1h)
    st = T.event_study([100 * DAY_MS], times, closes, "short", hold_days=1)
    assert st == {"n": 0, "dropped": 1, "mean_gross": None, "mean_net": None, "win_rate": None,
                  "half1_n": 0, "half1_mean_net": None, "half2_n": 0, "half2_mean_net": None,
                  "both_halves_positive": False, "rows": []}


def test_event_study_odd_n_gives_the_extra_row_to_the_first_half():
    c1h = _candles(40, 100.0, 0.0)
    times, closes = T.close_series(c1h)
    events = [0, 1 * DAY_MS, 2 * DAY_MS]
    st = T.event_study(events, times, closes, "long", hold_days=1)
    assert st["half1_n"] == 2 and st["half2_n"] == 1


# ── matched null / p-value ────────────────────────────────────────────

def test_matched_null_is_deterministic_for_a_fixed_seed():
    c1h = _candles(60, 100.0, 0.0)
    times, closes = T.close_series(c1h)
    eligible = list(range(0, 50 * DAY_MS, DAY_MS))
    a = T.matched_null(eligible, times, closes, "short", hold_days=1, n=5, draws=50, seed=7)
    b = T.matched_null(eligible, times, closes, "short", hold_days=1, n=5, draws=50, seed=7)
    assert a == b and len(a) == 50


def test_matched_null_empty_when_pool_too_small():
    assert T.matched_null([1, 2], [], [], "short", 1, n=5) == []
    assert T.matched_null([1, 2, 3], [], [], "short", 1, n=0) == []


def test_null_p_value_extremes():
    assert T.null_p_value(0.10, [0.01] * 100) == pytest.approx(1 / 101)     # nothing in the null beats it
    assert T.null_p_value(0.0, [1.0] * 100) == pytest.approx(101 / 101)     # everything beats it
    assert T.null_p_value(0.5, []) is None


# ── Bonferroni / doctrine verdict ──────────────────────────────────────

def test_bonferroni_alpha_divides_by_cell_count():
    assert T.bonferroni_alpha(0.05, 10) == pytest.approx(0.005)
    assert T.bonferroni_alpha(0.05, 0) == pytest.approx(0.05)    # never divides by zero


def test_doctrine_verdict_requires_every_gate():
    ok = T.doctrine_verdict(n=20, mean_net=0.01, both_halves_positive=True, p=0.0001, alpha_corrected=0.001,
                            min_n=10)
    assert ok["pass"] is True
    too_few = T.doctrine_verdict(n=3, mean_net=0.05, both_halves_positive=True, p=0.0001, alpha_corrected=0.001,
                                 min_n=10)
    assert too_few["pass"] is False and too_few["enough_events"] is False
    negative = T.doctrine_verdict(n=20, mean_net=-0.01, both_halves_positive=True, p=0.0001, alpha_corrected=0.001)
    assert negative["pass"] is False and negative["positive_net"] is False
    one_half = T.doctrine_verdict(n=20, mean_net=0.01, both_halves_positive=False, p=0.0001, alpha_corrected=0.001)
    assert one_half["pass"] is False
    not_sig = T.doctrine_verdict(n=20, mean_net=0.01, both_halves_positive=True, p=0.02, alpha_corrected=0.001)
    assert not_sig["pass"] is False and not_sig["beats_null"] is False


# ── filter overrides / standalone direction ────────────────────────────

def test_filter_overrides_grid():
    ov = T.filter_overrides((1.5, 2.0))
    assert ov["live"] == {}
    assert ov["funding_z_1.5"] == {"funding_z_filter": 1.5}
    assert ov["funding_z_2"] == {"funding_z_filter": 2.0}


def test_crowded_short_direction_shorts_only_over_threshold():
    times = [0, HOUR_MS, 2 * HOUR_MS]
    zs = [1.0, 2.5, 0.5]
    d_at = T.crowded_short_direction(times, zs, 2.0)
    assert d_at(0) is None
    assert d_at(HOUR_MS) == Direction(side="short")
    assert d_at(2 * HOUR_MS) is None
    assert d_at(-1) is None       # before any known z


# ── funding facts / threshold hours ────────────────────────────────────

def test_funding_facts_long_cost_and_sign():
    rows = [(i * HOUR_MS, 0.0001) for i in range(48)]     # longs pay a flat 0.0001/hr
    f = T.funding_facts(rows, [])
    assert f["n_hours"] == 48
    assert f["long_cost_pct_per_day"] == pytest.approx(0.0001 * 24 * 100)
    assert f["frac_hours_long_pays"] == 1.0


def test_funding_facts_negative_rate_means_longs_are_paid():
    rows = [(i * HOUR_MS, -0.0002) for i in range(24)]
    f = T.funding_facts(rows, [])
    assert f["long_cost_pct_per_day"] < 0
    assert f["frac_hours_long_pays"] == 0.0


def test_funding_facts_empty():
    assert T.funding_facts([], []) == {"n": 0}


def test_threshold_hours_counts_and_events():
    z = {0: 2.1, HOUR_MS: 2.2, 2 * HOUR_MS: 0.0, 3 * HOUR_MS: -2.1}
    out = T.threshold_hours(z, (2.0,))
    row = out["2"]
    assert row["pos_hours"] == 2 and row["pos_events"] == 1
    assert row["neg_hours"] == 1 and row["neg_events"] == 1
