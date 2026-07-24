"""Backtest math: the no-lookahead price read, the fee/slippage model, the
calibration buckets, and the momentum-vs-fade study. Pure functions on synthetic
series — no network, no LLM, deterministic."""
from __future__ import annotations

import pytest

from services.polymarket_scout import backtest
from services.polymarket_scout.scout import FEE_PER_FILL

H = backtest.HOUR
DAY = backtest.DAY


def _series(prices, start=0, step=H):
    return [{"t": start + i * step, "p": p} for i, p in enumerate(prices)]


# ── no lookahead ─────────────────────────────────────────────────────────────
def test_price_at_never_reads_a_bar_from_the_future():
    hist = _series([0.10, 0.20, 0.90])
    assert backtest.price_at(hist, 0) == 0.10
    assert backtest.price_at(hist, H + 1) == 0.20       # the 0.90 bar is not visible
    assert backtest.price_at(hist, 5 * H) == 0.90


def test_price_at_before_the_first_bar_is_none():
    assert backtest.price_at(_series([0.5], start=1000), 999) is None


def test_price_at_skips_malformed_bars():
    hist = [{"t": 0, "p": 0.1}, {"nope": 1}, {"t": H, "p": 0.2}]
    assert backtest.price_at(hist, H) == 0.2


# ── fees ─────────────────────────────────────────────────────────────────────
def test_net_pnl_charges_slippage_on_entry_and_fees_both_ways():
    px = 0.50
    won = backtest.net_pnl(px, True, fee=0.01, slip=0.01)
    assert won == pytest.approx((1 - 0.51) - 0.02)
    lost = backtest.net_pnl(px, False, fee=0.01, slip=0.01)
    assert lost == pytest.approx(-0.51 - 0.01)


def test_net_pnl_is_strictly_worse_than_the_gross_edge():
    assert backtest.net_pnl(0.40, True) < (1 - 0.40)
    assert backtest.net_pnl(0.40, False) < -0.40 + 1e-9


def test_net_pnl_defaults_use_the_shared_fee_constant():
    assert backtest.net_pnl(0.5, True, slip=0.0) == pytest.approx(0.5 - 2 * FEE_PER_FILL)


# ── calibration ──────────────────────────────────────────────────────────────
def test_calibration_measures_lift_against_the_price():
    # everything priced ~0.20 but resolving YES 50% of the time = badly underpriced
    pairs = [(0.20, True), (0.21, False), (0.19, True), (0.20, False)]
    rows = {r["bucket"]: r for r in backtest.calibration(pairs)}
    r = rows["0.15-0.25"]
    assert r["n"] == 4
    assert r["realized"] == pytest.approx(0.5)
    assert r["lift"] > 0.29
    assert r["ev_buy_yes"] > 0            # underpriced YES pays
    assert r["ev_buy_no"] < 0


def test_calibration_of_a_fair_market_leaves_no_edge_after_fees():
    pairs = [(0.50, i % 2 == 0) for i in range(100)]
    r = backtest.calibration(pairs)[0]
    assert r["lift"] == pytest.approx(0.0, abs=0.01)
    assert r["ev_buy_yes"] < 0 and r["ev_buy_no"] < 0   # a fair coin loses the fee
    assert r["ev_buy_yes"] == pytest.approx(r["ev_buy_no"], abs=1e-9)


def test_bucket_edges_are_inclusive_low_exclusive_high():
    assert backtest.bucket_of(0.05) == "0.05-0.15"
    assert backtest.bucket_of(0.149) == "0.05-0.15"
    assert backtest.bucket_of(1.0) == "0.95-1.00"


# ── momentum vs fade ─────────────────────────────────────────────────────────
def _daily(prices, yes_won):
    return {"history": _series(prices, step=DAY), "yes_won": yes_won}


def test_move_study_detects_continuation():
    # every market: +20pp day, then another +20pp day, resolving YES
    series = [_daily([0.20, 0.40, 0.60, 0.80], True) for _ in range(5)]
    out = backtest.move_study(series, threshold=0.05, horizon_s=DAY)
    assert out["fwd_move_same_direction"]["n"] > 0
    assert out["fwd_move_same_direction"]["mean"] > 0        # the move continued
    assert out["momentum_to_resolution"]["mean"] > 0
    assert out["fade_to_resolution"]["mean"] < 0


def test_move_study_detects_reversal():
    # spikes up then gives it all back, resolving NO
    series = [_daily([0.30, 0.70, 0.30, 0.10], False) for _ in range(5)]
    out = backtest.move_study(series, threshold=0.05, horizon_s=DAY)
    assert out["fwd_move_same_direction"]["mean"] < 0
    assert out["fade_to_resolution"]["mean"] > out["momentum_to_resolution"]["mean"]


def test_move_study_ignores_quiet_days_but_still_counts_them_unconditionally():
    series = [_daily([0.50, 0.51, 0.52, 0.53], True) for _ in range(3)]
    out = backtest.move_study(series, threshold=0.05, horizon_s=DAY)
    assert out["momentum_to_resolution"]["n"] == 0           # no move cleared 5pp
    assert out["sample_points"] > 0
    assert out["unconditional_buy_yes"]["n"] > 0


def test_move_study_never_samples_the_settlement_bar():
    # the last bar is the 0/1 pin; sampling it would make every rule look perfect
    series = [_daily([0.50, 0.70, 1.00], True)]
    out = backtest.move_study(series, threshold=0.05, horizon_s=DAY)
    assert out["sample_points"] == 1                         # only t=1, never t=2


def test_move_study_null_is_reproducible():
    series = [_daily([0.20, 0.45, 0.70, 0.30], True) for _ in range(4)]
    a = backtest.move_study(series, seed=3)["matched_null_random_side"]
    b = backtest.move_study(series, seed=3)["matched_null_random_side"]
    assert a == b


# ── horizons ─────────────────────────────────────────────────────────────────
def test_horizon_study_drops_markets_younger_than_the_horizon():
    young = {"history": _series([0.4, 0.5], step=H), "yes_won": True}     # 1h of life
    out = backtest.horizon_study([young], horizons_h=(24,))
    assert out["T-24h"]["n"] == 0


def test_horizon_study_scores_the_market_brier():
    series = [{"history": _series([0.9] * 50, step=H), "yes_won": True}]
    out = backtest.horizon_study(series, horizons_h=(24,))
    assert out["T-24h"]["n"] == 1
    assert out["T-24h"]["brier_market"] == pytest.approx(0.01)


# ── sample hygiene ───────────────────────────────────────────────────────────
def test_resolved_outcome_drops_ambiguous_settlements():
    assert backtest.resolved_outcome({"outcomePrices": '["1", "0"]'}) is True
    assert backtest.resolved_outcome({"outcomePrices": '["0", "1"]'}) is False
    assert backtest.resolved_outcome({"outcomePrices": '["0.5", "0.5"]'}) is None
    assert backtest.resolved_outcome({}) is None


@pytest.mark.parametrize("q,expect", [
    ("Lakers vs. Celtics", True),
    ("Games Total: O/U 2.5", True),
    ("LoL: Team WE vs JD Gaming - Game 1 Winner", True),
    ("Will the Fed cut rates in September?", False),
    ("Will Trump meet with Netanyahu by July 31?", False),
])
def test_sports_like_split(q, expect):
    assert backtest.is_sports_like(q) is expect


def test_stats_reports_none_t_for_a_single_sample():
    s = backtest._stats([0.5])
    assert s["n"] == 1 and s["t"] is None and s["mean"] == 0.5


def test_stats_of_nothing_is_empty():
    assert backtest._stats([]) == {"n": 0}
