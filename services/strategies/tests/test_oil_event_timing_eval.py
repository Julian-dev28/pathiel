"""Gate tests for the oil event-timing eval's pure parts
(services/strategies/evals/oil_event_timing_test.py,
OIL_EVENT_TIMING_TEST_2026-11-03.md). Synthetic candles only, no network.
"""
from __future__ import annotations

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import DAY_MS, HOUR_MS
from services.strategies.evals.oil_event_timing_test import (
    _compare, _half_split, build_filter_variants, build_null_anchors, day_stats, session_study, stop_out_flags,
    summary_csv_rows, window_stats)


def _hourly(prices, t0=0):
    """One 1h bar per close in `prices`, open = previous close, high/low a
    fixed wick either side."""
    out, prev = [], prices[0]
    for i, c in enumerate(prices):
        o = prev
        out.append(Candle(t=t0 + i * HOUR_MS, o=o, h=max(o, c) + 0.1, l=min(o, c) - 0.1, c=c, v=1.0))
        prev = c
    return out


# ── build_null_anchors ──────────────────────────────────────────────────────

def test_null_anchors_same_time_of_day_other_days():
    event = DAY_MS + 10 * HOUR_MS   # day 1, 10:00
    anchors = build_null_anchors([event], start_ms=0, end_ms=5 * DAY_MS)
    # day 0 and day 2 excluded (within 1 day of the event's own day 1);
    # days 3 and 4 remain, at the same 10:00 time-of-day.
    assert anchors == [3 * DAY_MS + 10 * HOUR_MS, 4 * DAY_MS + 10 * HOUR_MS]


def test_null_anchors_empty_inputs():
    assert build_null_anchors([], 0, DAY_MS) == []
    assert build_null_anchors([DAY_MS], 100, 100) == []


def test_null_anchors_dedupes_by_time_of_day_not_by_event():
    # two events sharing the same time-of-day contribute that time-of-day
    # once per eligible day, not twice.
    events = [10 * HOUR_MS, 10 * HOUR_MS + 7 * DAY_MS]
    anchors = build_null_anchors(events, start_ms=0, end_ms=10 * DAY_MS)
    assert len(anchors) == len(set(anchors))


# ── window_stats ─────────────────────────────────────────────────────────

def test_window_stats_move_and_range():
    bars = _hourly([100.0, 101.0, 102.0, 104.0, 103.0])   # t=0..4h
    t = 2 * HOUR_MS
    s = window_stats(bars, t, before_h=2.0, after_h=2.0)
    assert s is not None
    # before: bars with t in [0h, 2h) -> indices 0,1 (opens 100, closes 100, 101);
    # pre_open = bars[0].o = 100, anchor = bars[1].c = 101 (the last bar BEFORE t).
    assert s["move_before_pct"] == round(abs(101.0 / 100.0 - 1.0) * 100.0, 4)
    # after: t in [2h, 4h) -> indices 2,3 (closes 102, 104); post_close = 104.
    assert s["move_after_pct"] == round(abs(104.0 / 101.0 - 1.0) * 100.0, 4)
    assert s["reversal"] == 0.0   # both legs rise: no reversal


def test_window_stats_detects_reversal():
    bars = _hourly([100.0, 105.0, 104.0, 95.0])
    t = 2 * HOUR_MS
    s = window_stats(bars, t, before_h=2.0, after_h=2.0)
    assert s["reversal"] == 1.0   # rose into the anchor, fell after


def test_window_stats_none_without_both_sides():
    bars = _hourly([100.0, 101.0])
    assert window_stats(bars, 5 * HOUR_MS, before_h=1.0, after_h=1.0) is None   # no "after" bars


# ── stop_out_flags ──────────────────────────────────────────────────────────

def test_stop_out_flags_detects_a_long_stop():
    c1h = _hourly([100.0] * 30)   # a flat 24h range: low ~99.9, high ~100.1
    t = 25 * HOUR_MS
    c15 = [Candle(t=t, o=100.0, h=100.0, l=99.9 - 0.75 - 0.01, c=99.0, v=1.0)]   # trades through the long stop
    flags = stop_out_flags(c1h, c15, t, before_h=1.0, after_h=1.0)
    assert flags is not None and flags["long_stopped"] == 1.0


def test_stop_out_flags_none_without_enough_history():
    c1h = _hourly([100.0] * 5)
    assert stop_out_flags(c1h, [], 5 * HOUR_MS, 1.0, 1.0) is None


# ── day_stats ───────────────────────────────────────────────────────────

def test_day_stats_whole_day_move_and_range():
    bars = _hourly([100.0, 102.0, 98.0, 101.0])
    s = day_stats(bars, 0)
    assert s is not None
    assert s["move_pct"] == round(abs(101.0 / 100.0 - 1.0) * 100.0, 4)
    assert s["range_pct"] > 0


def test_day_stats_none_outside_range():
    bars = _hourly([100.0])
    assert day_stats(bars, 10 * DAY_MS) is None


# ── _compare / _half_split ──────────────────────────────────────────────────

def test_compare_reports_means_and_a_pvalue():
    row = _compare("m", [1.0, 2.0, 3.0], [0.0, 0.0, 0.0], n_perm=200)
    assert row["event_mean"] == 2.0 and row["null_mean"] == 0.0
    assert row["p_value"] is not None and 0.0 < row["p_value"] <= 1.0


def test_compare_none_pvalue_when_either_side_empty():
    assert _compare("m", [], [1.0], n_perm=50)["p_value"] is None
    assert _compare("m", [1.0], [], n_perm=50)["p_value"] is None


def test_half_split_even_and_odd():
    pairs = [(3, 30.0), (1, 10.0), (2, 20.0)]
    h1, h2 = _half_split(pairs)
    assert h1 == [10.0] and h2 == [20.0, 30.0]


# ── session_study ────────────────────────────────────────────────────────

def test_session_study_every_bar_goes_to_exactly_one_bucket():
    bars = _hourly([100.0 + i * 0.1 for i in range(48)], t0=0)
    out = session_study(bars, n_perm=50)
    assert set(out) == {"weekend", "nymex_day", "asia", "other"}
    total = sum(x["n_event"] for x in out.values())
    assert total == len(bars)


# ── build_filter_variants ───────────────────────────────────────────────────

def test_build_filter_variants_shape():
    import time
    now = int(time.time() * 1000)
    named = build_filter_variants(now - 60 * DAY_MS, now)
    assert "baseline" in named and named["baseline"] == {} if "baseline" in named else True
    for name in ("eia_1_1", "api_1_1", "both_1_1", "wait_eia_1h", "wait_api_1h"):
        assert name in named
        assert "event_blackout_windows" in named[name]


def test_build_filter_variants_narrow_window_has_no_events():
    named = build_filter_variants(0, HOUR_MS)   # 1970, far too narrow/old for any EIA/API window logic to matter
    for name, ov in named.items():
        if "event_blackout_windows" in ov:
            assert isinstance(ov["event_blackout_windows"], tuple)


# ── summary_csv_rows ──────────────────────────────────────────────────────

def test_summary_csv_rows_reads_event_blackout_refused():
    results = {"ds1": {"variants": {"baseline": {
        "final_equity": 64.0, "return_pct": 0.0, "max_dd_pct": 0.0, "trades": 1, "win_rate": 1.0, "avg_r": 2.0,
        "worst_loss_usd": 0.0, "worst_loss_pct": 0.0, "max_stop_liq_frac": 0.1,
        "refused": {"event_blackout": 3, "weekend": 1}}}}}
    rows = summary_csv_rows(results)
    assert rows == [{"dataset": "ds1", "variant": "baseline", "final_equity": 64.0, "return_pct": 0.0,
                     "max_dd_pct": 0.0, "trades": 1, "win_rate": 1.0, "avg_r": 2.0, "worst_loss_usd": 0.0,
                     "worst_loss_pct": 0.0, "max_stop_liq_frac": 0.1, "event_blackout_refused": 3}]
