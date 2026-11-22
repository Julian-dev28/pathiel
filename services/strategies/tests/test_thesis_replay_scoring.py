"""Gate tests for services.strategies.evals.thesis_replay_scoring: pure,
fixed-candle-fixture, no network (conftest.py's socket block would fail
any test that reached for one)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import Call
from services.strategies.evals import thesis_replay_scoring as S
from services.strategies.tests.conftest import make_candles_ohlc

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _call(**kw) -> Call:
    base = dict(side="long", instrument="XYZ", one_liner="t", entry_px=None, stop_px=None,
               targets=[], leverage=5.0, confidence=0.5, invalidation="x")
    base.update(kw)
    return Call(**base)


def _flat_rows(closes, wiggle=0.0):
    rows = []
    prev = closes[0]
    for c in closes:
        hi = max(prev, c) * (1.0 + wiggle)
        lo = min(prev, c) * (1.0 - wiggle)
        rows.append({"o": prev, "h": hi, "l": lo, "c": c})
        prev = c
    return rows


# ── bars_from_candles ────────────────────────────────────────────────────

def test_bars_from_candles_clips_and_sorts():
    candles = make_candles_ohlc(_flat_rows([100, 101, 102, 103, 104]), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0 + HOUR, T0 + 3 * HOUR)
    assert [b.t for b in bars] == [T0 + HOUR, T0 + 2 * HOUR, T0 + 3 * HOUR]
    assert [b.close for b in bars] == [101, 102, 103]


# ── has_coverage ─────────────────────────────────────────────────────────

def test_has_coverage_true_when_first_bar_at_start():
    candles = make_candles_ohlc(_flat_rows([100, 101]), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + HOUR)
    assert S.has_coverage(bars, T0)


def test_has_coverage_false_when_window_starts_late():
    # fetcher only returned bars starting well after `created_at` -- the
    # 1m/5m "rolls from now" failure mode the brief calls out.
    late_start = T0 + timedelta(hours=10)
    candles = make_candles_ohlc(_flat_rows([100, 101]), start=late_start, step=HOUR)
    bars = S.bars_from_candles(candles, late_start, late_start + HOUR)
    assert not S.has_coverage(bars, T0)


def test_has_coverage_false_on_empty_bars():
    assert not S.has_coverage([], T0)


# ── direction_24h ────────────────────────────────────────────────────────

def test_direction_24h_hit_long():
    call = _call(side="long")
    # hour 0..23 at 100, hour 24 (the 24h cutoff) on through at 110.
    closes = [100.0] * 24 + [110.0] * 6
    candles = make_candles_ohlc(_flat_rows(closes), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "hit"
    assert score.ref_px == pytest.approx(100.0)
    assert score.px_after == pytest.approx(110.0)


def test_direction_24h_miss_long():
    call = _call(side="long")
    closes = [100.0] * 24 + [90.0] * 6
    candles = make_candles_ohlc(_flat_rows(closes), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "miss"


def test_direction_24h_hit_short_is_sign_flipped():
    call = _call(side="short")
    closes = [100.0] * 24 + [90.0] * 6   # price fell: short was right
    candles = make_candles_ohlc(_flat_rows(closes), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "hit"


def test_direction_24h_void_flat_side():
    call = _call(side="flat")
    candles = make_candles_ohlc(_flat_rows([100.0] * 30), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_NO_SIDE


def test_direction_24h_void_pair_side():
    call = _call(side="pair")
    candles = make_candles_ohlc(_flat_rows([100.0] * 30), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_PAIR_NOT_SCORED


def test_direction_24h_void_no_coverage():
    call = _call(side="long")
    late_start = T0 + timedelta(hours=10)
    candles = make_candles_ohlc(_flat_rows([100.0] * 30), start=late_start, step=HOUR)
    bars = S.bars_from_candles(candles, late_start, late_start + 30 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_NO_COVERAGE


def test_direction_24h_void_window_too_short():
    call = _call(side="long")
    # only 5 hours of bars after created_at -- nowhere near the 24h mark
    candles = make_candles_ohlc(_flat_rows([100.0] * 6), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 6 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_WINDOW_TOO_SHORT


def test_direction_24h_tie_is_miss():
    call = _call(side="long")
    closes = [100.0] * 26
    candles = make_candles_ohlc(_flat_rows(closes), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 26 * HOUR)
    score = S.direction_24h(call, T0, bars)
    assert score.outcome == "miss"   # ret == 0 is not a hit, matches grader.direction_hit


# ── target_before_stop ───────────────────────────────────────────────────

def test_target_before_stop_hit_long_market_entry():
    call = _call(side="long", entry_px=None, stop_px=90.0, targets=[110.0])
    rows = _flat_rows([100, 102, 105, 108, 111, 109])
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 6 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "hit"
    assert score.entry_used == pytest.approx(100.0)   # bars[0].close, no entry_px set


def test_target_before_stop_stop_first_long():
    call = _call(side="long", entry_px=100.0, stop_px=90.0, targets=[110.0])
    rows = [{"o": 100, "h": 100, "l": 100, "c": 100},
           {"o": 100, "h": 101, "l": 89, "c": 95}]        # bar 2 dips through the stop
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "stop"


def test_target_before_stop_same_bar_tie_is_stop():
    # one bar's range touches BOTH the stop (90) and the target (110):
    # grader's own "unprovable you avoided it" conservatism -> stop wins.
    call = _call(side="long", entry_px=100.0, stop_px=90.0, targets=[110.0])
    rows = [{"o": 100, "h": 100, "l": 100, "c": 100},
           {"o": 100, "h": 112, "l": 88, "c": 100}]
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "stop"


def test_target_before_stop_neither_within_horizon():
    call = _call(side="long", entry_px=100.0, stop_px=90.0, targets=[110.0])
    rows = _flat_rows([100.0] * 10)
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 9 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "neither"


def test_target_before_stop_entry_never_fills_falls_back_to_reference_price():
    # entry_px far below the market, never touched -- score from bars[0]
    # .close instead (the "reference price if no entry trigger fires").
    call = _call(side="long", entry_px=50.0, stop_px=90.0, targets=[110.0])
    rows = _flat_rows([100, 102, 105, 108, 111, 109])
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 6 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "hit"
    assert score.entry_used == pytest.approx(100.0)


def test_target_before_stop_short_side_sign_flip():
    call = _call(side="short", entry_px=None, stop_px=110.0, targets=[90.0])
    rows = _flat_rows([100, 97, 94, 91, 88])
    candles = make_candles_ohlc(rows, start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 4 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "hit"


def test_target_before_stop_void_flat():
    call = _call(side="flat", stop_px=90.0, targets=[110.0])
    candles = make_candles_ohlc(_flat_rows([100.0] * 5), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 4 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_NO_SIDE


def test_target_before_stop_void_missing_stop():
    call = _call(side="long", stop_px=None, targets=[110.0])
    candles = make_candles_ohlc(_flat_rows([100.0] * 5), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 4 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_MISSING_STOP_OR_TARGET


def test_target_before_stop_void_missing_target():
    call = _call(side="long", stop_px=90.0, targets=[])
    candles = make_candles_ohlc(_flat_rows([100.0] * 5), start=T0, step=HOUR)
    bars = S.bars_from_candles(candles, T0, T0 + 4 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_MISSING_STOP_OR_TARGET


def test_target_before_stop_void_no_coverage():
    call = _call(side="long", stop_px=90.0, targets=[110.0])
    late_start = T0 + timedelta(hours=10)
    candles = make_candles_ohlc(_flat_rows([100.0] * 5), start=late_start, step=HOUR)
    bars = S.bars_from_candles(candles, late_start, late_start + 4 * HOUR)
    score = S.target_before_stop(call, T0, bars)
    assert score.outcome == "void" and score.reason == S.VOID_NO_COVERAGE


# ── score_call (both scores together) ───────────────────────────────────

def test_score_call_combines_both_and_bounds_target_stop_to_horizon():
    call = _call(side="long", entry_px=None, stop_px=90.0, targets=[110.0])
    # target touched at hour 40 -- AFTER a 1-day (24h) expiry, so
    # target_before_stop must see "neither" (bounded by expires_at), while
    # direction_24h (fixed 24h window) sees the price still flat at hour 24.
    closes = [100.0] * 24 + [100.0] + [100.0] * 15 + [111.0]
    candles = make_candles_ohlc(_flat_rows(closes), start=T0, step=HOUR)
    expires_at = T0 + timedelta(hours=24)
    d, t = S.score_call(call, T0, expires_at, candles, now=T0 + timedelta(hours=100))
    assert d.outcome == "miss"          # flat at the 24h mark, tie -> miss
    assert t.outcome == "neither"       # target only touched after expiry


def test_score_call_void_for_flat_short_circuits_without_bars():
    call = _call(side="flat")
    d, t = S.score_call(call, T0, T0 + timedelta(days=7), candles=[], now=T0 + timedelta(days=8))
    assert d.outcome == "void" and t.outcome == "void"


# ── summarize_outcomes ───────────────────────────────────────────────────

def test_summarize_outcomes_basic():
    s = S.summarize_outcomes(["hit", "hit", "miss", "void"])
    assert s.n == 3 and s.k == 2 and s.void == 1
    assert s.rate == pytest.approx(2 / 3)
    assert s.ci_lo is not None and s.ci_lo <= s.rate <= s.ci_hi


def test_summarize_outcomes_all_void_reports_none():
    s = S.summarize_outcomes(["void", "void"])
    assert s.n == 0 and s.rate is None and s.ci_lo is None


def test_summarize_outcomes_empty():
    s = S.summarize_outcomes([])
    assert s.n == 0 and s.rate is None
