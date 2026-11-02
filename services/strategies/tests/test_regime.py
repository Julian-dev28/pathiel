"""Gate tests for the causal regime labeller (services/strategies/backtest/
regime.py, OIL_REGIME_TEST_2026-11-02.md). Synthetic 1h bars, no network.

Covers: each label on a shape built to be it (steady rise, steady fall,
flat back-and-forth, a stalled rise, a straight-line break against the
20-day trend), warm-up, causality (the label for day D does not move when
bars after D's open change or are added), the fast series equal to the
point-in-time rescan at every close, the +-25% threshold shift, and the
filter rules the sim reads.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.regime import (
    CHOP, DAY_MS, HOUR_MS, TREND_DOWN, TREND_UP, RegimeConfig, RegimeFeatures, against_20d, classify,
    day_labels, features_at, label_series, regime_blocks)

T0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
CFG = RegimeConfig()


def bars_from(closes, start=T0, wick=0.05, first_open=None):
    """1h bars whose open is the previous close and whose wicks stick out
    `wick` past the body."""
    out, prev = [], (closes[0] if first_open is None else first_open)
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle(t=start + i * HOUR_MS, o=o, h=max(o, c) + wick, l=min(o, c) - wick, c=c, v=1.0))
        prev = c
    return out


def wavy(n, start_px, drift_per_h, amp=0.3, period=6):
    """A drift with a sine on top (so the efficiency ratio is not 1)."""
    return [start_px + drift_per_h * i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


def end_of(bars):
    return bars[-1].t + HOUR_MS


# ── the labels ─────────────────────────────────────────────────────────────

def test_steady_rise_is_trend_up_and_steady_fall_is_trend_down():
    """A slow two-day wave on a drift: the 48h run is not a straight line
    (er under fast_er), so the 20-day return and a full 24h range decide."""
    up = bars_from(wavy(30 * 24, 100.0, 0.02, amp=1.0, period=48))
    f = features_at(up, end_of(up))
    assert f.ret20 > CFG.trend_ret and f.er < CFG.fast_er and f.range_atr >= CFG.chop_range_atr
    assert f.label == TREND_UP
    down = bars_from(wavy(30 * 24, 100.0, -0.02, amp=1.0, period=48))
    assert features_at(down, end_of(down)).label == TREND_DOWN


def test_flat_back_and_forth_is_chop():
    flat = bars_from(wavy(30 * 24, 100.0, 0.0, amp=0.6, period=10))
    f = features_at(flat, end_of(flat))
    assert abs(f.ret20) < CFG.trend_ret and f.er < CFG.fast_er
    assert f.label == CHOP


def test_a_stalled_rise_is_chop():
    """20 days up, then a day of big bars going nowhere: the 24h range is
    under chop_range_atr x ATR14, so the 20-day trend no longer counts."""
    rise = wavy(28 * 24, 100.0, 0.02)
    top = rise[-1]
    stall = [top + (0.8 if i % 2 else -0.8) for i in range(30)]
    bars = bars_from(rise + stall, wick=0.4)
    f = features_at(bars, end_of(bars))
    assert f.ret20 > CFG.trend_ret
    assert f.range_atr < CFG.chop_range_atr and f.er < CFG.fast_er
    assert f.label == CHOP


def test_a_straight_line_break_against_the_20d_trend_is_the_new_trend():
    """The 11-02 shape: a 20-day rise, then two days of near straight-line
    fall. The 20-day return is still positive; the 48h efficiency says the
    trend broke."""
    rise = wavy(28 * 24, 100.0, 0.02)
    fall = [rise[-1] - 0.06 * (i + 1) + (0.02 if i % 2 else 0.0) for i in range(48)]
    bars = bars_from(rise + fall)
    f = features_at(bars, end_of(bars))
    assert f.ret20 > CFG.trend_ret and f.er >= CFG.fast_er and f.move < 0
    assert f.label == TREND_DOWN


def test_warm_up_has_no_label():
    short = bars_from(wavy(10 * 24, 100.0, 0.02))
    f = features_at(short, end_of(short))
    assert f.ret20 is None and f.label is None
    assert features_at([], T0).label is None


def test_classify_order_and_missing():
    assert classify(None, 0.5, 1.0, 5.0, CFG) is None
    assert classify(0.10, 0.50, -1.0, 9.0, CFG) == TREND_DOWN       # fast beats slow
    assert classify(0.10, 0.10, 1.0, 9.0, CFG) == TREND_UP
    assert classify(-0.10, 0.10, 1.0, 9.0, CFG) == TREND_DOWN
    assert classify(0.10, 0.10, 1.0, 3.0, CFG) == CHOP              # stalled
    assert classify(0.01, 0.10, 1.0, 9.0, CFG) == CHOP              # no 20-day trend


# ── causality and the fast series ─────────────────────────────────────────

def _random_walk(n, seed=7):
    rnd = random.Random(seed)
    px, out = 100.0, []
    for _ in range(n):
        px *= 1.0 + rnd.gauss(0.0003, 0.004)
        out.append(px)
    return out


def test_label_for_day_d_ignores_bars_after_its_open():
    bars = bars_from(_random_walk(40 * 24))
    series = label_series(bars)
    for day in range(bars[0].t // DAY_MS + 22, bars[-1].t // DAY_MS):
        d_open = day * DAY_MS
        ref = features_at(bars, d_open)
        assert ref.label is not None
        rnd = random.Random(day)
        altered = [b if b.t + HOUR_MS <= d_open else
                   Candle(t=b.t, o=b.o * 3, h=b.h * 5, l=b.l * 0.2, c=b.c * rnd.uniform(0.1, 9.0), v=b.v)
                   for b in bars]
        extra = altered + bars_from([1.0, 500.0, 2.0], start=bars[-1].t + HOUR_MS)
        assert features_at(altered, d_open) == ref
        assert features_at(extra, d_open) == ref
        assert label_series(altered).at(d_open) == ref
        assert label_series(extra).at(d_open) == ref
        assert series.at(d_open) == ref


def test_the_bar_closing_exactly_at_the_open_counts_and_the_next_does_not():
    bars = bars_from(_random_walk(30 * 24))
    d_open = (bars[0].t // DAY_MS + 25) * DAY_MS
    f = features_at(bars, d_open)
    assert f.asof_ms == d_open                       # the 23:00 bar closed at 00:00
    assert features_at(bars, d_open - 1).asof_ms == d_open - HOUR_MS


def test_series_equals_the_rescan_at_every_close():
    walk = _random_walk(30 * 24, seed=3)
    bars = bars_from(walk)
    bars = bars[:300] + bars[305:]                   # a 5-hour gap
    s = label_series(bars)
    for b in bars[::7] + bars[-3:]:
        t = b.t + HOUR_MS
        assert s.at(t) == features_at(bars, t)        # float for float


def test_day_labels_reads_each_open():
    bars = bars_from(wavy(30 * 24, 100.0, 0.02, amp=1.0, period=48))
    s = label_series(bars)
    days = [bars[0].t // DAY_MS + k for k in (1, 25, 29)]
    got = day_labels(s, days)
    assert got[days[0]] is None                      # warm-up
    assert got[days[1]] == TREND_UP and got[days[2]] == TREND_UP
    assert s.at(bars[0].t) is None                   # before any close


# ── thresholds and filters ────────────────────────────────────────────────

def test_shifted_moves_one_threshold():
    assert CFG.shifted("trend_ret", 1.25).trend_ret == pytest.approx(0.05)
    assert CFG.shifted("fast_er", 0.75).fast_er == pytest.approx(0.225)
    s = CFG.shifted("chop_range_atr", 1.25)
    assert s.chop_range_atr == pytest.approx(5.625) and s.trend_ret == CFG.trend_ret
    with pytest.raises(ValueError):
        CFG.shifted("trend_days", 1.25)


def _f(label=None, ret20=0.0, er=0.0, move=0.0):
    return RegimeFeatures(asof_ms=0, ret20=ret20, er=er, move=move, range_atr=5.0, label=label)


def test_regime_blocks_rules():
    up, down, chop = _f(TREND_UP), _f(TREND_DOWN), _f(CHOP)
    assert regime_blocks("long", chop, "no_chop", CFG) == "regime_chop"
    assert regime_blocks("long", up, "no_chop", CFG) is None
    assert regime_blocks("short", up, "with_trend", CFG) == "regime_against"
    assert regime_blocks("long", up, "with_trend", CFG) is None
    assert regime_blocks("short", chop, "with_trend", CFG) is None
    assert regime_blocks("long", down, "trend_only", CFG) == "regime_against"
    assert regime_blocks("short", chop, "trend_only", CFG) == "regime_chop"
    assert regime_blocks("short", down, "trend_only", CFG) is None
    assert regime_blocks("long", None, "trend_only", CFG) is None    # warm-up never blocks
    assert regime_blocks("long", _f(None), "no_chop", CFG) is None
    with pytest.raises(ValueError):
        regime_blocks("long", up, "nope", CFG)


def test_veto_20d_and_its_break_escape():
    rising = _f(CHOP, ret20=0.08, er=0.10, move=-0.5)
    assert against_20d("short", rising, CFG) and regime_blocks("short", rising, "veto_20d", CFG) == "regime_veto"
    assert not against_20d("long", rising, CFG)
    broke = _f(TREND_DOWN, ret20=0.08, er=0.45, move=-2.0)     # straight-line fall: the short may go
    assert not against_20d("short", broke, CFG)
    assert against_20d("long", _f(CHOP, ret20=-0.08, er=0.45, move=-2.0), CFG)
    assert not against_20d("short", _f(CHOP, ret20=0.02), CFG)  # no 20-day trend: no veto
    assert not against_20d("short", None, CFG)
