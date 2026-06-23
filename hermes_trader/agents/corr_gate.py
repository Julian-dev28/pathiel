"""Correlation-regime sizing gate (V3 — validated by edge_regime_timing.py).

Rolling average pairwise Pearson correlation across the universe, compared against its
trailing median. The gate produces a sizing SCALAR passed to the momentum and vol-dispersion
rebalancers:

  LOW correlation  → more cross-sectional dispersion → stronger momentum LS book.
                     Scale MOMENTUM exposure UP (scalar > 1.0).
  HIGH correlation → coins move together → less cross-sectional spread → weaker momentum.
                     Scale VOL-DISPERSION exposure UP (scalar > 1.0), scale momentum DOWN.

Validated result from V3 (edge_regime_timing.py):
  - Momentum  Sharpe:  base 4.95 → low-corr 8.36 (USEFUL gate)
  - Vol-disp  Sharpe:  base 9.06 → high-corr 13.27 (USEFUL gate)

PURE module — no network, no orders. Returns a CorrRegimeState (scalars for each book).
The live rebalancers call ``compute_corr_regime`` and apply the scalar to their execution.

Config: ``correlation_gate`` block in DEFAULT_CONFIG. enabled=False → all scalars return 1.0
(neutral, no change in behaviour).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CorrRegimeState:
    """Output of compute_corr_regime. All scalars are 1.0 when gate is disabled or data is thin."""
    avg_corr: float           # current average pairwise correlation
    corr_high: bool           # True if avg_corr > trailing median (high-correlation regime)
    momentum_scalar: float    # multiply momentum exposure by this (>1 in low-corr; <1 in high-corr)
    vol_disp_scalar: float    # multiply vol-dispersion exposure by this (>1 in high-corr)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pearson(xs: List[float], ys: List[float]) -> float:
    """Pearson correlation. Returns 0 if degenerate."""
    n = len(xs)
    if n < 4:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return 0.0
    return num / (dx * dy)


def _daily_rets(closes: List[float]) -> List[float]:
    return [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def _closes(bars: List[Any]) -> List[float]:
    out = []
    for b in bars:
        c = b.get("c") if isinstance(b, dict) else getattr(b, "c", None)
        if c and float(c) > 0:
            out.append(float(c))
    return out


# ── Core computation ──────────────────────────────────────────────────────────

def avg_pairwise_corr(candles_by_coin: Dict[str, List[Any]], window: int,
                      max_coins: int = 15) -> Optional[float]:
    """Average pairwise Pearson correlation of daily returns over last ``window`` days.

    Caps at ``max_coins`` coins (speed: n*(n-1)/2 pairs with n=15 → 105 pairs, fast).
    Returns None if fewer than 4 eligible coins.
    """
    # Collect trailing `window` daily returns per coin
    ret_series: List[List[float]] = []
    for coin, bars in list(candles_by_coin.items())[:max_coins]:
        cl = _closes(bars)
        if len(cl) < window + 2:
            continue
        rets = _daily_rets(cl[-(window + 1):])
        if len(rets) < window * 0.8:
            continue
        ret_series.append(rets[-window:])

    if len(ret_series) < 4:
        return None

    # Compute all pairwise correlations
    pair_corrs: List[float] = []
    for i in range(len(ret_series)):
        for j in range(i + 1, len(ret_series)):
            n = min(len(ret_series[i]), len(ret_series[j]))
            if n < 5:
                continue
            rho = _pearson(ret_series[i][-n:], ret_series[j][-n:])
            pair_corrs.append(rho)

    if not pair_corrs:
        return None
    return sum(pair_corrs) / len(pair_corrs)


def compute_corr_regime(
    candles_by_coin: Dict[str, List[Any]],
    history: List[float],     # trailing corr history for rolling median (in-place updated by caller)
    window: int = 14,
    cap: float = 1.5,         # maximum scalar (never more than 1.5x exposure from this gate)
    low_scalar: float = 1.2,  # momentum scalar when correlation is LOW (favour momentum)
    high_scalar: float = 1.2, # vol-dispersion scalar when correlation is HIGH
) -> CorrRegimeState:
    """Compute the current correlation regime and return sizing scalars.

    history is a list of past average-pairwise-corr values (passed in by the caller, who
    maintains it across calls so the rolling median is restart-safe when persisted). The caller
    should append the returned avg_corr to history after calling this function.

    cap, low_scalar, high_scalar are from config and control how aggressively the gate scales.

    Returns CorrRegimeState with neutral scalars (1.0) when data is thin or gate is disabled.
    """
    avg_c = avg_pairwise_corr(candles_by_coin, window)
    if avg_c is None or len(history) < 5:
        # Insufficient data → neutral scalars
        return CorrRegimeState(
            avg_corr=avg_c or 0.0, corr_high=False,
            momentum_scalar=1.0, vol_disp_scalar=1.0,
        )

    median_corr = statistics.median(history) if history else avg_c
    corr_high = avg_c > median_corr

    # Scale momentum UP in low-corr (cross-sectional dispersion is high → momentum pays more)
    # Scale vol-dispersion UP in high-corr (beta-neutralised, can still find dispersion)
    mom_scalar = min(cap, low_scalar) if not corr_high else max(0.7, 1.0 / low_scalar)
    vd_scalar = min(cap, high_scalar) if corr_high else max(0.7, 1.0 / high_scalar)

    return CorrRegimeState(
        avg_corr=avg_c,
        corr_high=corr_high,
        momentum_scalar=mom_scalar,
        vol_disp_scalar=vd_scalar,
    )
