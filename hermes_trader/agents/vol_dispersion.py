"""Vol-dispersion / factor rebalancer — beta-neutral, within-β-tercile, pluggable score function.

Beta neutralization is achieved by construction via WITHIN-β-TERCILE ranking: the universe is split
into three beta terciles (low/mid/high vs BTC), then within each tercile the top-K coins by the
chosen score function are longed and the bottom-K are shorted. Because each tercile has a narrow
beta band, the long and short legs carry matched beta exposure → net portfolio beta ≈ 0 without
explicit leg-weighting.

Supported score functions (select via ``score_fn`` parameter to ``rank_universe``):
  - ``"idio_vol"`` (default) — idiosyncratic vol = stdev of BTC-residual daily returns over
    ``window`` days. Validated +5.56%/rebal (W1+V1, 30d/K8, OOS robust). LONG high-vol = long
    uncertain, underpriced coins.
  - ``"sortino"`` — mean(daily return) / downside-deviation over ``window`` days. Validated
    +3.66%/rebal (V2, within-β-tercile). More regime-stable than idio-vol (holds in down-regime,
    +2.24%; corr +0.07 to mom / +0.37 to idio-vol → partially orthogonal).
  - ``"amihud"`` — mean(|daily ret| / daily $volume) over ``window`` days. BORDERLINE (+2.33%
    validated but lumpy, 2/4 quarters negative). LONG illiquid coins. Requires candle.v > 0.
  - ``"kurtosis"`` — excess kurtosis of daily returns over ``window`` days (~60d validated).
    Validated V2: +1.71%/rebal (beta-neutral, within-β-tercile). HIGH kurtosis = LONG (fat-tailed
    return distribution; captures coins prone to large positive surprises). MODEST edge — bleeds in
    sustained down-regimes; use with small k_per_tercile (k=1) and shadow-validate before going live.

The existing public API (``idio_vol_score``, ``coin_beta``, ``rank_universe``, ``rebalance_plan``,
``is_empty_plan``, ``TargetBook``) is UNCHANGED — existing callers and tests continue to work.

PURE module (no network, no orders). Inputs: candles + bench candles → TargetBook.
All parameters are configurable via ``score_fn`` / ``window`` / ``k_per_tercile``.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes_trader.indicators.math import candle_val


@dataclass
class TargetBook:
    """Target book for the vol-dispersion rebalancer."""
    longs: List[str]
    shorts: List[str]
    scores: Dict[str, float] = field(default_factory=dict)   # coin -> idio_vol score
    tercile_assignments: Dict[str, int] = field(default_factory=dict)  # coin -> 0/1/2 tercile index


# ── Return helpers ────────────────────────────────────────────────────────────

def _daily_rets(bars: List[Any], window: int) -> List[float]:
    """Trailing `window` daily close-to-close returns. Lookahead-safe."""
    closes = [candle_val(b, "c") for b in bars[-(window + 1):]]
    return [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def _ols_beta(coin_rets: List[float], bench_rets: List[float]) -> float:
    """OLS beta of coin returns on benchmark (1.0 if degenerate / too short)."""
    n = min(len(coin_rets), len(bench_rets))
    if n < 8:
        return 1.0
    cr, br = coin_rets[-n:], bench_rets[-n:]
    mb = sum(br) / n
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = sum(cr) / n
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def idio_vol_score(bars: List[Any], bench_bars: List[Any], window: int) -> Optional[float]:
    """Idiosyncratic vol = stdev of (coin daily return − beta × bench daily return) over `window` days.

    Uses the same trailing window for both the beta estimate and the residual-stdev calculation —
    consistent with edge_beta_neutral_factor.py. Returns None when there is insufficient history.
    Lookahead-safe: uses only bars passed in (caller controls the bar slice).
    """
    if not bars or not bench_bars or len(bars) < window + 1 or len(bench_bars) < window + 1:
        return None
    cr = _daily_rets(bars, window)
    br = _daily_rets(bench_bars, window)
    if len(cr) < 10 or len(br) < 10:
        return None
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    residuals = [c - beta * b for c, b in zip(cr, br)]
    if len(residuals) < 4:
        return None
    try:
        return statistics.pstdev(residuals)
    except statistics.StatisticsError:
        return None


def sortino_score(bars: List[Any], bench_bars: List[Any], window: int) -> Optional[float]:
    """Sortino score = mean(daily return) / downside-deviation over `window` days.

    Downside deviation = sqrt(mean of squared negative returns). bench_bars is accepted for API
    symmetry with idio_vol_score but is NOT used — Sortino ranks on absolute (not residual)
    risk-adjusted return. The within-β-tercile construction provides the beta-neutrality.

    Validated +3.66%/rebal (V2, within-β-tercile). More regime-stable than idio-vol.
    Returns None when there is insufficient history. Lookahead-safe.
    """
    if not bars or len(bars) < window + 1:
        return None
    cr = _daily_rets(bars, window)
    if len(cr) < 10:
        return None
    mu = sum(cr) / len(cr)
    neg_sq = [r * r for r in cr if r < 0]
    if not neg_sq:
        # No negative returns → perfect non-negative streak. Return a large finite score.
        return mu if mu > 0 else 0.0
    downside_dev = math.sqrt(sum(neg_sq) / len(neg_sq))
    if downside_dev <= 0:
        return mu if mu > 0 else 0.0
    return mu / downside_dev


def kurtosis_score(bars: List[Any], bench_bars: List[Any], window: int) -> Optional[float]:
    """Excess kurtosis of trailing ``window`` daily returns.

    Excess kurtosis = (mean of (r - mu)^4) / sigma^4 − 3, where sigma = population std.
    A fat-tailed distribution has positive excess kurtosis. bench_bars accepted for API
    symmetry but not used — the within-β-tercile construction provides beta-neutrality.

    Validated V2: +1.71%/rebal (HIGH kurtosis = LONG direction, OOS robust). MODEST edge
    — bleeds in sustained down-regimes; use with k_per_tercile=1 and shadow first.
    Returns None when history is too short. Lookahead-safe.
    """
    if not bars or len(bars) < window + 1:
        return None
    cr = _daily_rets(bars, window)
    if len(cr) < 10:
        return None
    n = len(cr)
    mu = sum(cr) / n
    deviations = [r - mu for r in cr]
    var = sum(d * d for d in deviations) / n   # population variance
    if var <= 0:
        return 0.0
    sigma_sq = var
    m4 = sum(d ** 4 for d in deviations) / n
    return m4 / (sigma_sq * sigma_sq) - 3.0   # excess kurtosis (normal = 0)


def amihud_score(bars: List[Any], bench_bars: List[Any], window: int) -> Optional[float]:
    """Amihud illiquidity score = mean(|daily ret| / daily $volume) over `window` days.

    $volume = candle.v * candle.c (base-coin units × close price). bench_bars accepted for API
    symmetry but not used. The within-β-tercile construction provides beta-neutrality.

    BORDERLINE edge (+2.33%/rebal, lumpy). LONG illiquid (high Amihud) / SHORT liquid.
    Returns None when bars lack volume data or history is too short. Lookahead-safe.
    """
    if not bars or len(bars) < window + 1:
        return None
    recent = bars[-(window + 1):]
    ratios = []
    for i in range(1, len(recent)):
        b_prev = recent[i - 1]
        b_curr = recent[i]
        c_prev = candle_val(b_prev, "c")
        c_curr = candle_val(b_curr, "c")
        vol = candle_val(b_curr, "v")   # base-coin units
        if c_prev <= 0 or c_curr <= 0 or vol <= 0:
            continue
        ret = abs(c_curr / c_prev - 1)
        dollar_vol = vol * c_curr
        if dollar_vol > 0:
            ratios.append(ret / dollar_vol)
    if len(ratios) < window // 2:
        return None
    return sum(ratios) / len(ratios)


# ── Score-function registry ───────────────────────────────────────────────────

_SCORE_FNS: Dict[str, Callable[[List[Any], List[Any], int], Optional[float]]] = {
    "idio_vol": idio_vol_score,
    "sortino": sortino_score,
    "amihud": amihud_score,
    "kurtosis": kurtosis_score,
}


def coin_beta(bars: List[Any], bench_bars: List[Any], window: int) -> float:
    """Per-coin BTC beta for the tercile sort (reuses the same trailing window)."""
    if not bars or not bench_bars or len(bars) < window + 1 or len(bench_bars) < window + 1:
        return 1.0
    cr = _daily_rets(bars, window)
    br = _daily_rets(bench_bars, window)
    return _ols_beta(cr, br)


# ── Within-tercile book construction ─────────────────────────────────────────

def _split_into_terciles(scored: List[Tuple[str, float, float]]) -> List[List[Tuple[str, float, float]]]:
    """Split (coin, idio_vol, beta) list into three equal beta-sorted terciles.

    Returns [low_beta_tercile, mid_beta_tercile, high_beta_tercile]. The last tercile absorbs
    any remainder when len(scored) is not divisible by 3.
    """
    sorted_by_beta = sorted(scored, key=lambda x: x[2])
    n = len(sorted_by_beta)
    n_per = n // 3
    return [
        sorted_by_beta[:n_per],
        sorted_by_beta[n_per: 2 * n_per],
        sorted_by_beta[2 * n_per:],   # remainder goes here
    ]


def rank_universe(
    candles_by_coin: Dict[str, List[Any]],
    bench_bars: List[Any],
    idio_vol_window: int,
    k_per_tercile: int,
    score_fn: str = "idio_vol",
) -> TargetBook:
    """Build a beta-neutral TargetBook via within-β-tercile ranking.

    Algorithm:
    1. Score every coin with the selected score function (score_fn).
    2. Compute each coin's BTC beta (same window) for tercile assignment.
    3. Sort universe into 3 beta terciles.
    4. Within each tercile: top-k_per_tercile by score = LONG, bottom-k_per_tercile = SHORT.
    5. Collect longs + shorts across all three terciles.

    score_fn: one of "idio_vol" (default, original behaviour), "sortino", "amihud".
    The idio_vol_window parameter serves as the lookback window for all score functions.

    Returns an empty TargetBook if:
    - < 9 coins have enough history (need at least 3 terciles × 3 coins each)
    - fewer than 2 × k_per_tercile coins per tercile survive
    """
    if not candles_by_coin or not bench_bars:
        return TargetBook([], [], {}, {})

    _score_fn = _SCORE_FNS.get(score_fn, idio_vol_score)

    # Step 1+2: score + beta every coin
    scored: List[Tuple[str, float, float]] = []   # (coin, factor_score, beta)
    for coin, bars in candles_by_coin.items():
        iv = _score_fn(bars, bench_bars, idio_vol_window)
        if iv is None:
            continue
        b = coin_beta(bars, bench_bars, idio_vol_window)
        scored.append((coin, iv, b))

    min_coins = max(9, 3 * 2 * k_per_tercile)   # need at least k longs + k shorts per tercile
    if len(scored) < min_coins:
        return TargetBook([], [], {}, {})

    # Step 3: split into beta terciles
    terciles = _split_into_terciles(scored)

    longs: List[str] = []
    shorts: List[str] = []
    all_scores: Dict[str, float] = {}
    tercile_assignments: Dict[str, int] = {}

    # Step 4+5: within-tercile rank
    for ti, tercile_coins in enumerate(terciles):
        if len(tercile_coins) < 2 * k_per_tercile:
            continue   # tercile too thin — skip (edge guard)
        # Sort by idio_vol descending
        by_iv = sorted(tercile_coins, key=lambda x: x[1], reverse=True)
        for coin, iv, _ in by_iv:
            all_scores[coin] = iv
            tercile_assignments[coin] = ti
        long_coins = [c for c, _, _ in by_iv[:k_per_tercile]]
        short_coins = [c for c, _, _ in by_iv[-k_per_tercile:]]
        longs.extend(long_coins)
        shorts.extend(short_coins)

    if not longs or not shorts:
        return TargetBook([], [], {}, {})

    return TargetBook(longs=longs, shorts=shorts, scores=all_scores,
                      tercile_assignments=tercile_assignments)


def rebalance_plan(book: TargetBook, current_long: List[str], current_short: List[str]) -> Dict[str, List[str]]:
    """Diff target book against current holdings. Mirrors xs_momentum.rebalance_plan."""
    tgt_long, tgt_short = set(book.longs), set(book.shorts)
    cur_long, cur_short = set(current_long or []), set(current_short or [])
    return {
        "open_long": sorted(tgt_long - cur_long),
        "open_short": sorted(tgt_short - cur_short),
        "close_long": sorted(cur_long - tgt_long),
        "close_short": sorted(cur_short - tgt_short),
        "hold_long": sorted(tgt_long & cur_long),
        "hold_short": sorted(tgt_short & cur_short),
    }


def is_empty_plan(plan: Dict[str, List[str]]) -> bool:
    return not any(plan.get(k) for k in ("open_long", "open_short", "close_long", "close_short"))
