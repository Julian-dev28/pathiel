"""Offline gate tests for services/strategies/evals/cross_market_backtest.py:
the leverage matching, the ATR measure, the stop-buffer scaling and the
row builder. Synthetic candles only, no network."""
from __future__ import annotations

import math

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction, Params, simulate
from services.strategies.evals import cross_market_backtest as X

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000


def _series(scale: float = 1.0, n15: int = 6 * 24 * 4):
    """A deterministic swinging 15m path around 100 (times `scale`) and the
    1h bars built from it, so ranges, pullbacks, stops and targets occur."""
    px = [scale * (100 + 1.5 * math.sin(j / 7) + 0.4 * math.sin(j / 2.3)) for j in range(n15 + 1)]
    c15 = []
    for j in range(n15):
        o, c = px[j], px[j + 1]
        c15.append(Candle(t=j * FIFTEEN_MS, o=o, h=max(o, c) + 0.1 * scale, l=min(o, c) - 0.1 * scale, c=c, v=1.0))
    c1h = []
    for i in range(0, n15, 4):
        grp = c15[i:i + 4]
        c1h.append(Candle(t=grp[0].t, o=grp[0].o, h=max(b.h for b in grp), l=min(b.l for b in grp),
                          c=grp[-1].c, v=1.0))
    return c15, c1h


def test_matched_leverage_scales_by_atr_and_respects_the_venue():
    # Brent 0.56% at 20x; BTC 0.47% -> 24x; gold 0.24% -> 47x capped at 25; S&P 0.15% -> 75x capped at 50
    assert X.matched_leverage(0.0056, 0.0047, 40) == 24.0
    assert X.matched_leverage(0.0056, 0.0024, 25) == 25.0
    assert X.matched_leverage(0.0056, 0.0015, 50) == 50.0
    # a much wilder coin gets far less, never under 1x
    assert X.matched_leverage(0.0056, 0.0280, 10) == 4.0
    assert X.matched_leverage(0.0056, 0.5, 10) == 1.0
    assert X.matched_leverage(0.0056, 0.0056, 20) == 20.0        # oil itself is unchanged
    with pytest.raises(ValueError):
        X.matched_leverage(0.0056, 0.0, 20)


def test_flat_leverage_is_20x_capped_at_the_venue_max():
    assert X.flat_leverage(40) == 20.0 and X.flat_leverage(10) == 10.0


def test_mean_atr_matches_atr14_on_a_constant_range():
    c1h = [Candle(t=i * HOUR_MS, o=100, h=101, l=99, c=100, v=1) for i in range(40)]
    atr, pct = X.mean_atr(c1h)
    assert atr == pytest.approx(2.0) and pct == pytest.approx(0.02)
    assert X.mean_atr(c1h[:10]) == (None, None)                  # under ATR14's history


def test_stop_buffer_is_oils_half_dollar_in_atr_units():
    assert X.OIL_STOP_BUFFER == 0.50
    assert X.scaled_stop_buffer(coin_atr=40.0, oil_atr=0.5) == pytest.approx(40.0)   # 1 ATR on both
    assert X.scaled_stop_buffer(coin_atr=0.5, oil_atr=0.5) == pytest.approx(0.50)
    with pytest.raises(ValueError):
        X.scaled_stop_buffer(1.0, 0.0)


def test_the_same_chart_at_another_price_level_trades_identically():
    """The plumbing claim behind the cross-market table: with the buffer in
    ATR terms, a market is judged on its shape, not its price. The same path
    at 36x the price (gold-like) gives the same trades, R and dollars."""
    long = lambda t: Direction(side="long")
    base15, base1h = _series(1.0)
    big15, big1h = _series(36.0)
    a_atr, _ = X.mean_atr(base1h)
    b_atr, _ = X.mean_atr(big1h)
    pa = Params(stop_buffer=X.scaled_stop_buffer(a_atr, a_atr), leverage=10, max_leverage=20)
    pb = Params(stop_buffer=X.scaled_stop_buffer(b_atr, a_atr), leverage=10, max_leverage=20)
    ra, rb = simulate(base15, base1h, long, pa), simulate(big15, big1h, long, pb)
    assert ra.trades, "the synthetic path must produce trades for this test to mean anything"
    assert [t.exit_reason for t in ra.trades] == [t.exit_reason for t in rb.trades]
    assert [round(t.r_multiple, 6) for t in ra.trades] == [round(t.r_multiple, 6) for t in rb.trades]
    assert ra.final_equity == pytest.approx(rb.final_equity, rel=1e-6)


def test_row_carries_market_leverage_and_total_r():
    c15, c1h = _series()
    p = Params(leverage=24, max_leverage=40, stop_buffer=0.4)
    run = simulate(c15, c1h, lambda t: Direction(side="long"), p)
    r = X.row("BTC", "BTC", 30, "matched", "always_long", 0.0047, p, run)
    assert (r["market"], r["coin"], r["days"], r["mode"], r["series"]) == ("BTC", "BTC", 30, "matched", "always_long")
    assert r["leverage"] == 24 and r["venue_max"] == 40 and r["stop_buffer"] == 0.4
    assert r["total_r"] == pytest.approx(sum(t.r_multiple for t in run.trades if t.r_multiple is not None))
    assert r["n_trades"] == len(run.trades)


def test_the_markets_are_brent_gold_btc_and_the_sp500_perp():
    coins = [c for c, _ in X.MARKETS]
    assert coins == ["xyz:BRENTOIL", "xyz:GOLD", "BTC", "xyz:SP500"]
    assert X.BASELINE == coins[0] and X.WINDOWS_DAYS == (30, 50)
