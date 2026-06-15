"""Tests for the risk-first sizing module (Phase-2: Turtle-N equal-risk + risk-of-ruin).

These prove the pure functions behave; they do NOT touch the live executor (the
module is not yet wired — wiring is gated/default-off and presented separately).
"""

import math

from hermes_trader.agents.sizing import (
    SizingResult,
    atr_equal_risk_notional,
    risk_of_ruin,
)


# ── Equal-dollar-risk: the core invariant ────────────────────────────────────

def test_equal_dollar_risk_invariant_across_instruments():
    """A high-ATR coin and a low-ATR coin sized by this fn risk the SAME $."""
    equity = 1000.0
    risk_pct = 0.01  # 1% = $10 at risk
    # Low-vol major: ATR 1% of price
    major = atr_equal_risk_notional(
        equity=equity, risk_per_trade_pct=risk_pct,
        atr_abs=1.0, entry_px=100.0, sl_atr_mult=1.5,
    )
    # High-vol memecoin: ATR 8% of price
    meme = atr_equal_risk_notional(
        equity=equity, risk_per_trade_pct=risk_pct,
        atr_abs=0.08, entry_px=1.0, sl_atr_mult=1.5,
    )
    assert math.isclose(major.risk_usd, 10.0, rel_tol=1e-9)
    assert math.isclose(meme.risk_usd, 10.0, rel_tol=1e-9)
    # The memecoin (wider stop) must get a SMALLER notional for the same risk.
    assert meme.notional_usd < major.notional_usd


def test_notional_solves_risk_equation():
    r = atr_equal_risk_notional(
        equity=2000.0, risk_per_trade_pct=0.02,
        atr_abs=2.0, entry_px=100.0, sl_atr_mult=1.5,
    )
    # stop_distance = 1.5*2/100 = 0.03 ; risk target = 0.02*2000 = $40
    # notional = 40 / 0.03 = 1333.33
    assert math.isclose(r.stop_distance_frac, 0.03, rel_tol=1e-9)
    assert math.isclose(r.notional_usd, 40.0 / 0.03, rel_tol=1e-9)
    assert math.isclose(r.risk_usd, 40.0, rel_tol=1e-9)


def test_notional_cap_clamps_and_reduces_realized_risk():
    r = atr_equal_risk_notional(
        equity=10000.0, risk_per_trade_pct=0.05,
        atr_abs=0.5, entry_px=100.0, sl_atr_mult=1.5,
        max_trade_notional_usd=600.0,
    )
    assert r.notional_usd == 600.0
    assert r.clamped_by == "notional_cap"
    # Realized risk reflects the clamped notional (< the $500 target).
    assert math.isclose(r.risk_usd, 600.0 * r.stop_distance_frac, rel_tol=1e-9)
    assert r.risk_usd < 0.05 * 10000.0


def test_max_leverage_cap_binds():
    # Tiny stop => huge notional => must be capped by leverage.
    r = atr_equal_risk_notional(
        equity=1000.0, risk_per_trade_pct=0.02,
        atr_abs=0.001, entry_px=100.0, sl_atr_mult=1.0,
        coin_max_leverage=10, config_max_leverage=15,
    )
    # tighter of (10,15) = 10x => notional capped at 10*1000 = 10000
    assert r.notional_usd == 10000.0
    assert r.implied_leverage == 10.0
    assert r.clamped_by == "max_leverage"


def test_notional_cap_takes_precedence_over_leverage_when_tighter():
    r = atr_equal_risk_notional(
        equity=1000.0, risk_per_trade_pct=0.02,
        atr_abs=0.001, entry_px=100.0, sl_atr_mult=1.0,
        coin_max_leverage=10, config_max_leverage=15,
        max_trade_notional_usd=600.0,
    )
    assert r.notional_usd == 600.0
    assert r.clamped_by == "notional_cap"


def test_degenerate_inputs_return_zero_not_default():
    for kwargs in (
        dict(equity=0.0, risk_per_trade_pct=0.01, atr_abs=1.0, entry_px=100.0, sl_atr_mult=1.5),
        dict(equity=1000.0, risk_per_trade_pct=0.01, atr_abs=0.0, entry_px=100.0, sl_atr_mult=1.5),
        dict(equity=1000.0, risk_per_trade_pct=0.01, atr_abs=1.0, entry_px=0.0, sl_atr_mult=1.5),
        dict(equity=1000.0, risk_per_trade_pct=0.0, atr_abs=1.0, entry_px=100.0, sl_atr_mult=1.5),
    ):
        r = atr_equal_risk_notional(**kwargs)
        assert r.notional_usd == 0.0
        assert r.clamped_by == "zero"


# ── Risk of ruin ─────────────────────────────────────────────────────────────

def test_ror_negative_edge_is_certain_ruin():
    # 40% win at 1:1 payoff = negative edge.
    assert risk_of_ruin(win_rate=0.40, payoff_ratio=1.0, risk_per_trade_pct=0.02) == 1.0


def test_ror_zero_or_negative_payoff_is_certain_ruin():
    assert risk_of_ruin(win_rate=0.9, payoff_ratio=0.0, risk_per_trade_pct=0.02) == 1.0


def test_ror_smaller_risk_fraction_lowers_ruin():
    # Same positive edge; betting smaller per trade must not increase ruin.
    big = risk_of_ruin(win_rate=0.5, payoff_ratio=2.0, risk_per_trade_pct=0.10)
    small = risk_of_ruin(win_rate=0.5, payoff_ratio=2.0, risk_per_trade_pct=0.01)
    assert small <= big
    assert 0.0 <= small <= 1.0 and 0.0 <= big <= 1.0


def test_ror_strong_edge_small_risk_is_near_zero():
    ror = risk_of_ruin(win_rate=0.6, payoff_ratio=2.5, risk_per_trade_pct=0.01)
    assert ror < 0.05


def test_ror_is_bounded():
    for wr in (0.0, 0.3, 0.5, 0.7, 1.0):
        for pr in (0.5, 1.0, 3.0):
            for rp in (0.005, 0.05, 0.25):
                v = risk_of_ruin(win_rate=wr, payoff_ratio=pr, risk_per_trade_pct=rp)
                assert 0.0 <= v <= 1.0
