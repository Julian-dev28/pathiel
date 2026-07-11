"""Gate tests for hermes_trader.indicators.range_structure (research-only).

Deterministic, local, no network. Covers: aggregation, geometry, state
classification, deviation vs acceptance, lookahead-safety, fail-safe on short
history, and the no-live-import rule.
"""
from __future__ import annotations

import math
import sys

import pytest

from hermes_trader.indicators.range_structure import (
    ACCUMULATION_CANDIDATE,
    BREAKOUT_DOWN_CANDIDATE,
    BREAKOUT_UP_CANDIDATE,
    HOUR_MS,
    RANGE,
    RANGE_FAMILY,
    REDISTRIBUTION_CANDIDATE,
    TREND_DOWN,
    TREND_UP,
    UNKNOWN,
    RangeConfig,
    RangeStructure,
    aggregate_bars,
    adx_series,
    atr_series,
    ema_series,
    location_bucket,
)

T0 = 1_760_000_000_000 - (1_760_000_000_000 % (4 * HOUR_MS))  # 4h-aligned epoch


def mk_bar(t: int, o: float, h: float, l: float, c: float, v: float = 1.0) -> list:
    return [t, o, h, l, c, v]


def flat_bars(n: int, px: float = 100.0, amp: float = 3.0, step_ms: int = 4 * HOUR_MS) -> list:
    """Deterministic sideways range: sine wave (period 16 bars) of amplitude
    `amp` around px, small-bodied bars, so the envelope is several ATRs wide."""
    bars = []
    prev = px
    for i in range(n):
        c = px + amp * math.sin(2.0 * math.pi * i / 16.0)
        o = prev
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        bars.append(mk_bar(T0 + i * step_ms, o, h, l, c))
        prev = c
    return bars


def trend_bars(n: int, start: float = 100.0, slope: float = 0.8) -> list:
    bars = []
    px = start
    for i in range(n):
        o = px
        c = px + slope
        h = max(o, c) + 0.2
        l = min(o, c) - 0.2
        bars.append(mk_bar(T0 + i * 4 * HOUR_MS, o, h, l, c))
        px = c
    return bars


# ---------------------------------------------------------------------------
# helpers / math
# ---------------------------------------------------------------------------
class TestMathHelpers:
    def test_ema_matches_hand_calc(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = ema_series(vals, 3)
        assert math.isnan(out[0]) and math.isnan(out[1])
        assert out[2] == pytest.approx(2.0)
        assert out[3] == pytest.approx(2.0 + (4 - 2.0) * 0.5)

    def test_ema_short_input_all_nan(self):
        assert all(math.isnan(x) for x in ema_series([1.0, 2.0], 5))

    def test_atr_constant_true_range(self):
        bars = [mk_bar(T0 + i * HOUR_MS, 100, 101, 99, 100) for i in range(20)]
        out = atr_series(bars, 14)
        assert math.isnan(out[13])
        assert out[14] == pytest.approx(2.0)
        assert out[-1] == pytest.approx(2.0)

    def test_atr_short_history_nan(self):
        bars = [mk_bar(T0, 100, 101, 99, 100)] * 5
        assert all(math.isnan(x) for x in atr_series(bars, 14))

    def test_adx_high_in_trend_low_in_chop(self):
        adx_t = adx_series(trend_bars(80), 14)[-1]
        adx_r = adx_series(flat_bars(80), 14)[-1]
        assert adx_t > 25
        assert adx_r < adx_t

    def test_location_bucket_edges(self):
        assert location_bucket(0.0) == "0.00-0.20"
        assert location_bucket(0.199) == "0.00-0.20"
        assert location_bucket(0.20) == "0.20-0.35"
        assert location_bucket(0.5) == "0.35-0.65"
        assert location_bucket(0.65) == "0.65-0.80"
        assert location_bucket(0.80) == "0.80-1.00"
        assert location_bucket(1.0) == "0.80-1.00"
        assert location_bucket(1.7) == "0.80-1.00"  # clamped


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
class TestAggregation:
    def test_4h_aggregation_ohlcv(self):
        base = T0
        bars_1h = [
            mk_bar(base + i * HOUR_MS, 10 + i, 11 + i, 9 + i, 10.5 + i, 2.0)
            for i in range(8)
        ]
        out = aggregate_bars(bars_1h, 4)
        assert len(out) == 2
        b = out[0]
        assert b[0] == base
        assert b[1] == 10          # open of first
        assert b[2] == 11 + 3      # max high
        assert b[3] == 9           # min low
        assert b[4] == 10.5 + 3    # close of last
        assert b[5] == 8.0         # summed volume

    def test_incomplete_and_holed_groups_dropped(self):
        base = T0
        bars = [mk_bar(base + i * HOUR_MS, 1, 1, 1, 1) for i in range(4)]
        bars += [mk_bar(base + 5 * HOUR_MS, 1, 1, 1, 1)]  # hole at +4h
        bars += [mk_bar(base + i * HOUR_MS, 1, 1, 1, 1) for i in range(6, 8)]
        out = aggregate_bars(bars, 4)
        assert len(out) == 1  # only the first complete group survives

    def test_unaligned_leading_bars_skipped(self):
        base = T0 + HOUR_MS  # misaligned start
        bars = [mk_bar(base + i * HOUR_MS, 1, 1, 1, 1) for i in range(11)]
        out = aggregate_bars(bars, 4)
        assert len(out) == 2
        assert out[0][0] % (4 * HOUR_MS) == 0

    def test_bad_hours_raises(self):
        with pytest.raises(ValueError):
            aggregate_bars([], 0)


# ---------------------------------------------------------------------------
# snapshot geometry + states
# ---------------------------------------------------------------------------
class TestSnapshot:
    def test_short_history_is_unknown_and_nan_geometry(self):
        rs = RangeStructure(flat_bars(10))
        s = rs.snapshot(5)
        assert s.state == UNKNOWN
        assert math.isnan(s.range_high)

    def test_out_of_bounds_raises(self):
        rs = RangeStructure(flat_bars(10))
        with pytest.raises(IndexError):
            rs.snapshot(10)

    def test_range_state_and_geometry(self):
        bars = flat_bars(160)
        rs = RangeStructure(bars)
        s = rs.snapshot(len(bars) - 1)
        assert s.state in RANGE_FAMILY
        assert s.range_low < s.range_mid < s.range_high
        assert 0.0 <= s.price_location <= 1.0
        assert s.touches_high >= 2 and s.touches_low >= 2
        assert s.bars_in_range >= 1
        # mid-price sanity: close ~100 in a 100 +/- ~3.5 range
        assert s.range_low < 100 < s.range_high

    def test_trend_up_and_down(self):
        up = RangeStructure(trend_bars(120, slope=0.8))
        dn = RangeStructure(trend_bars(120, slope=-0.8))
        assert up.snapshot(119).state == TREND_UP
        assert dn.snapshot(119).state == TREND_DOWN

    def test_breakout_up_candidate(self):
        bars = flat_bars(150)
        # last bar closes decisively above the trailing envelope, ADX still low
        t = T0 + 150 * 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, 108.0, 100.0, 107.5))
        rs = RangeStructure(bars)
        assert rs.snapshot(len(bars) - 1).state == BREAKOUT_UP_CANDIDATE

    def test_breakout_down_candidate(self):
        bars = flat_bars(150)
        t = T0 + 150 * 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, 100.0, 92.0, 92.5))
        rs = RangeStructure(bars)
        assert rs.snapshot(len(bars) - 1).state == BREAKOUT_DOWN_CANDIDATE

    def test_redistribution_candidate_after_decline(self):
        # 70-bar decline of ~45%, then a range: long EMA still falling
        bars = trend_bars(70, start=140.0, slope=-0.9)
        last_close = bars[-1][4]
        tail_start = bars[-1][0] + 4 * HOUR_MS
        tail = flat_bars(70, px=last_close)
        for i, b in enumerate(tail):
            b[0] = tail_start + i * 4 * HOUR_MS
        bars += tail
        rs = RangeStructure(bars)
        states = {rs.snapshot(i).state for i in range(len(bars) - 12, len(bars))}
        assert states & {REDISTRIBUTION_CANDIDATE, ACCUMULATION_CANDIDATE, RANGE}
        assert REDISTRIBUTION_CANDIDATE in states or ACCUMULATION_CANDIDATE in states


# ---------------------------------------------------------------------------
# deviation vs acceptance
# ---------------------------------------------------------------------------
class TestDeviationAcceptance:
    def _base(self) -> list:
        return flat_bars(150)

    def test_upper_deviation_poke_and_close_back_inside(self):
        bars = self._base()
        rs0 = RangeStructure(bars)
        rh = rs0.snapshot(len(bars) - 1).range_high
        atr = rs0.snapshot(len(bars) - 1).atr
        t = bars[-1][0] + 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, rh + 0.6 * atr, 99.0, 100.0))  # poke, close inside
        rs = RangeStructure(bars)
        s = rs.snapshot(len(bars) - 1)
        assert s.upper_deviation[0.10] and s.upper_deviation[0.25] and s.upper_deviation[0.50]
        assert not any(s.lower_deviation.values())
        assert not s.upper_acceptance[1]

    def test_deviation_threshold_ordering(self):
        bars = self._base()
        rs0 = RangeStructure(bars)
        snap0 = rs0.snapshot(len(bars) - 1)
        rh, atr = snap0.range_high, snap0.atr
        t = bars[-1][0] + 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, rh + 0.15 * atr, 99.0, 100.0))  # small poke
        s = RangeStructure(bars).snapshot(len(bars) - 1)
        assert s.upper_deviation[0.10]
        assert not s.upper_deviation[0.25]
        assert not s.upper_deviation[0.50]

    def test_lower_deviation(self):
        bars = self._base()
        snap0 = RangeStructure(bars).snapshot(len(bars) - 1)
        rl, atr = snap0.range_low, snap0.atr
        t = bars[-1][0] + 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, 101.0, rl - 0.6 * atr, 100.0))
        s = RangeStructure(bars).snapshot(len(bars) - 1)
        assert all(s.lower_deviation.values())
        assert not any(s.upper_deviation.values())

    def test_acceptance_counts_consecutive_closes(self):
        bars = self._base()
        snap0 = RangeStructure(bars).snapshot(len(bars) - 1)
        rh = snap0.range_high
        t0 = bars[-1][0]
        # three consecutive closes above the boundary
        for j in range(1, 4):
            px = rh + 1.0 + 0.2 * j
            bars.append(mk_bar(t0 + j * 4 * HOUR_MS, px - 0.1, px + 0.3, px - 0.5, px))
        rs = RangeStructure(bars)
        s1 = rs.snapshot(len(bars) - 3)
        s3 = rs.snapshot(len(bars) - 1)
        assert s1.upper_acceptance[1] and not s1.upper_acceptance[3]
        assert s3.upper_acceptance[1] and s3.upper_acceptance[2] and s3.upper_acceptance[3]
        assert not any(s3.lower_acceptance.values())
        # a genuine acceptance is NOT a deviation
        assert not s3.upper_deviation[0.10]


# ---------------------------------------------------------------------------
# lookahead safety + hygiene
# ---------------------------------------------------------------------------
class TestLookaheadAndHygiene:
    def test_future_bars_do_not_change_past_snapshot(self):
        bars = flat_bars(160)
        i = 120
        a = RangeStructure(bars[: i + 1]).snapshot(i)
        # append a violent future move; snapshot at i must be identical
        t0 = bars[i][0]
        future = [mk_bar(t0 + j * 4 * HOUR_MS, 100, 300, 100, 290) for j in range(1, 30)]
        b = RangeStructure(bars[: i + 1] + future).snapshot(i)
        assert a.state == b.state
        assert a.range_high == b.range_high and a.range_low == b.range_low
        assert a.price_location == b.price_location
        assert a.upper_deviation == b.upper_deviation
        assert a.upper_acceptance == b.upper_acceptance
        assert a.bars_in_range == b.bars_in_range

    def test_boundary_excludes_current_bar(self):
        bars = flat_bars(150)
        snap0 = RangeStructure(bars).snapshot(len(bars) - 1)
        rh = snap0.range_high
        t = bars[-1][0] + 4 * HOUR_MS
        bars.append(mk_bar(t, 100.0, rh + 50.0, 99.0, 100.0))  # huge poke
        s = RangeStructure(bars).snapshot(len(bars) - 1)
        # if the boundary included bar i, range_high would jump by ~50
        assert s.range_high == pytest.approx(rh)

    def test_deterministic(self):
        bars = flat_bars(160)
        s1 = RangeStructure(bars).snapshot(150)
        s2 = RangeStructure(bars).snapshot(150)
        assert s1 == s2

    def test_not_imported_by_live_modules(self):
        """range_structure is research-only: importing the live agent modules
        must not pull it in."""
        banned = "hermes_trader.indicators.range_structure"
        sys.modules.pop(banned, None)
        import hermes_trader.indicators.triggers  # noqa: F401
        import hermes_trader.indicators.math  # noqa: F401
        assert banned not in sys.modules

    def test_config_frozen(self):
        cfg = RangeConfig()
        with pytest.raises(Exception):
            cfg.lookback = 99  # type: ignore[misc]
